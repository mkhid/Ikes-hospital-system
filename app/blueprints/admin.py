"""System administration: staff accounts, departments and scheduling policy."""
from datetime import date

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.constants import AuditAction, Role, ShiftCode
from app.extensions import db
from app.models import Department, Shift, ShiftRequirement, Staff
from app.services import audit
from app.utils.decorators import oversight_required, roles_required

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/")
@login_required
@oversight_required
def index():
    departments = Department.query.order_by(Department.name).all()
    return render_template(
        "admin/index.html",
        departments=departments,
        staff_count=Staff.query.filter_by(is_active=True).count(),
        inactive_count=Staff.query.filter_by(is_active=False).count(),
        shifts=Shift.query.order_by(Shift.sort_order).all(),
        policy={
            "min_rest_hours": current_app.config["MIN_REST_HOURS"],
            "max_weekly_hours": current_app.config["MAX_WEEKLY_HOURS"],
            "max_consecutive_days": current_app.config["MAX_CONSECUTIVE_DAYS"],
            "max_consecutive_nights": current_app.config["MAX_CONSECUTIVE_NIGHTS"],
            "min_days_off_per_week": current_app.config["MIN_DAYS_OFF_PER_WEEK"],
            "optimiser_iterations": current_app.config["OPTIMISER_ITERATIONS"],
            "grace_minutes": current_app.config["ATTENDANCE_GRACE_MINUTES"],
            "overtime_threshold": current_app.config["OVERTIME_THRESHOLD_MINUTES"],
            "email_backend": current_app.config["EMAIL_BACKEND"],
            "sms_backend": current_app.config["SMS_BACKEND"],
        },
    )


# ----------------------------------------------------------------------
# Staff accounts
# ----------------------------------------------------------------------
@admin_bp.route("/staff")
@login_required
@oversight_required
def staff_list():
    department_id = request.args.get("department_id", type=int)
    query = Staff.query
    if department_id:
        query = query.filter_by(department_id=department_id)

    return render_template(
        "admin/staff_list.html",
        staff_list=query.order_by(Staff.department_id, Staff.first_name).all(),
        departments=Department.query.order_by(Department.name).all(),
        selected=department_id,
        roles=list(Role),
    )


@admin_bp.route("/staff/new", methods=["GET", "POST"])
@login_required
@roles_required(Role.ADMIN, Role.HR)
def create_staff():
    departments = Department.query.order_by(Department.name).all()

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        staff_no = (request.form.get("staff_no") or "").strip().upper()

        if Staff.query.filter_by(email=email).first():
            flash("That email address is already registered.", "danger")
            return redirect(url_for("admin.create_staff"))
        if Staff.query.filter_by(staff_no=staff_no).first():
            flash("That staff number is already in use.", "danger")
            return redirect(url_for("admin.create_staff"))

        password = request.form.get("password") or current_app.config["DEFAULT_PASSWORD"]
        staff = Staff(
            staff_no=staff_no,
            first_name=(request.form.get("first_name") or "").strip(),
            last_name=(request.form.get("last_name") or "").strip(),
            email=email,
            phone=(request.form.get("phone") or "").strip() or None,
            role=request.form.get("role") or Role.STAFF,
            job_title=(request.form.get("job_title") or "").strip() or None,
            qualification=(request.form.get("qualification") or "").strip() or None,
            department_id=int(request.form.get("department_id", 0)),
            can_work_nights=bool(request.form.get("can_work_nights")),
            date_joined=date.today(),
            must_change_password=True,
        )
        staff.set_password(password)
        db.session.add(staff)
        db.session.flush()

        audit.record(
            AuditAction.STAFF_CREATED,
            f"{current_user.full_name} created the account for {staff.full_name} "
            f"({staff.staff_no})",
            actor=current_user,
            entity_type="Staff",
            entity_id=staff.id,
            department_id=staff.department_id,
            new={
                "staff_no": staff.staff_no,
                "email": staff.email,
                "role": staff.role,
                "department": staff.department.name if staff.department else None,
            },
        )
        db.session.commit()
        flash(
            f"Account created for {staff.full_name}. They must change their password "
            f"on first sign-in.",
            "success",
        )
        return redirect(url_for("admin.staff_list"))

    return render_template(
        "admin/staff_form.html",
        departments=departments,
        roles=list(Role),
        shifts=list(ShiftCode),
        staff=None,
        default_password=current_app.config["DEFAULT_PASSWORD"],
    )


@admin_bp.route("/staff/<int:staff_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required(Role.ADMIN, Role.HR)
def edit_staff(staff_id):
    staff = db.session.get(Staff, staff_id)
    if staff is None:
        abort(404)

    if request.method == "POST":
        before = {
            "role": staff.role,
            "department": staff.department.name if staff.department else None,
            "job_title": staff.job_title,
            "can_work_nights": staff.can_work_nights,
            "max_weekly_hours": staff.max_weekly_hours,
        }

        staff.first_name = (request.form.get("first_name") or staff.first_name).strip()
        staff.last_name = (request.form.get("last_name") or staff.last_name).strip()
        staff.phone = (request.form.get("phone") or "").strip() or None
        staff.job_title = (request.form.get("job_title") or "").strip() or None
        staff.qualification = (request.form.get("qualification") or "").strip() or None
        staff.role = request.form.get("role") or staff.role
        staff.department_id = int(request.form.get("department_id", staff.department_id))
        staff.can_work_nights = bool(request.form.get("can_work_nights"))
        staff.preferred_shift = request.form.get("preferred_shift") or None
        if staff.preferred_shift == "NONE":
            staff.preferred_shift = None

        max_hours = request.form.get("max_weekly_hours", type=int)
        staff.max_weekly_hours = max_hours or None

        if request.form.get("reset_password"):
            staff.set_password(current_app.config["DEFAULT_PASSWORD"])
            staff.must_change_password = True

        audit.record(
            AuditAction.STAFF_UPDATED,
            f"{current_user.full_name} updated the record for {staff.full_name}",
            actor=current_user,
            entity_type="Staff",
            entity_id=staff.id,
            department_id=staff.department_id,
            old=before,
            new={
                "role": staff.role,
                "department": staff.department.name if staff.department else None,
                "job_title": staff.job_title,
                "can_work_nights": staff.can_work_nights,
                "max_weekly_hours": staff.max_weekly_hours,
            },
        )
        db.session.commit()
        flash(f"{staff.full_name}'s record has been updated.", "success")
        return redirect(url_for("admin.staff_list"))

    return render_template(
        "admin/staff_form.html",
        staff=staff,
        departments=Department.query.order_by(Department.name).all(),
        roles=list(Role),
        shifts=list(ShiftCode),
        default_password=current_app.config["DEFAULT_PASSWORD"],
    )


@admin_bp.route("/staff/<int:staff_id>/toggle", methods=["POST"])
@login_required
@roles_required(Role.ADMIN, Role.HR)
def toggle_staff(staff_id):
    """
    Activate or deactivate an account.

    Deactivating releases every future shift the person held and re-optimises,
    so removing someone from the rota cannot silently leave shifts uncovered.
    """
    staff = db.session.get(Staff, staff_id)
    if staff is None:
        abort(404)
    if staff.id == current_user.id:
        flash("You cannot deactivate your own account.", "warning")
        return redirect(url_for("admin.staff_list"))

    staff.is_active = not staff.is_active
    db.session.flush()

    released = 0
    if not staff.is_active:
        from datetime import timedelta

        from app.services import reoptimizer

        outcome = reoptimizer.release_and_refill(
            staff,
            date.today(),
            date.today() + timedelta(days=90),
            f"{staff.full_name} deactivated",
            actor=current_user,
        )
        released = outcome.released_count

    audit.record(
        AuditAction.STAFF_DEACTIVATED if not staff.is_active else AuditAction.STAFF_UPDATED,
        f"{current_user.full_name} "
        f"{'deactivated' if not staff.is_active else 'reactivated'} "
        f"{staff.full_name}"
        + (f", {released} future shift(s) released" if released else ""),
        actor=current_user,
        entity_type="Staff",
        entity_id=staff.id,
        department_id=staff.department_id,
        new={"is_active": staff.is_active, "shifts_released": released},
    )
    db.session.commit()

    flash(
        f"{staff.full_name} has been "
        f"{'deactivated' if not staff.is_active else 'reactivated'}."
        + (f" {released} shift(s) were released and refilled." if released else ""),
        "info",
    )
    return redirect(url_for("admin.staff_list"))


# ----------------------------------------------------------------------
# Departments and staffing levels
# ----------------------------------------------------------------------
@admin_bp.route("/departments/<int:department_id>", methods=["GET", "POST"])
@login_required
@oversight_required
def department_settings(department_id):
    department = db.session.get(Department, department_id)
    if department is None:
        abort(404)

    shifts = Shift.query.order_by(Shift.sort_order).all()

    if request.method == "POST":
        before = {
            f"{r.shift.code}": {"min": r.min_staff, "weekend": r.weekend_min_staff}
            for r in department.requirements
        }

        manager_id = request.form.get("manager_id", type=int)
        if manager_id:
            department.manager_id = manager_id

        for shift in shifts:
            minimum = request.form.get(f"min_{shift.id}", type=int) or 0
            weekend = request.form.get(f"weekend_{shift.id}", type=int)
            weekend = minimum if weekend is None else weekend

            requirement = next(
                (r for r in department.requirements if r.shift_id == shift.id), None
            )
            if requirement is None:
                requirement = ShiftRequirement(
                    department_id=department.id, shift_id=shift.id
                )
                db.session.add(requirement)
            requirement.min_staff = max(0, minimum)
            requirement.weekend_min_staff = max(0, weekend)

        db.session.flush()
        audit.record(
            AuditAction.POLICY_UPDATED,
            f"{current_user.full_name} updated staffing requirements for "
            f"{department.name}",
            actor=current_user,
            entity_type="Department",
            entity_id=department.id,
            department_id=department.id,
            old=before,
            new={
                f"{r.shift.code}": {"min": r.min_staff, "weekend": r.weekend_min_staff}
                for r in department.requirements
            },
        )
        db.session.commit()
        flash(
            f"Staffing requirements for {department.name} saved. Regenerate the "
            f"roster for them to take effect.",
            "success",
        )
        return redirect(url_for("admin.department_settings", department_id=department.id))

    requirements = {r.shift_id: r for r in department.requirements}
    return render_template(
        "admin/department.html",
        department=department,
        shifts=shifts,
        requirements=requirements,
        members=sorted(department.active_members(), key=lambda s: s.full_name),
        weekly_demand=sum(
            (requirements[s.id].min_staff * 5 + requirements[s.id].weekend_min_staff * 2)
            if s.id in requirements
            else 0
            for s in shifts
        ),
    )
