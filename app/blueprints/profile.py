"""Personal profile and the staff directory."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.constants import AssignmentStatus, AuditAction, LeaveStatus, RosterStatus
from app.extensions import db
from app.models import Assignment, AttendanceRecord, Department, LeaveRequest, Roster, Staff
from app.services import audit
from app.utils.timeutils import today, week_start

profile_bp = Blueprint("profile", __name__)


@profile_bp.route("/")
@login_required
def me():
    return _profile_for(current_user, editable=True)


@profile_bp.route("/<int:staff_id>")
@login_required
def view(staff_id):
    staff = db.session.get(Staff, staff_id)
    if staff is None:
        abort(404)

    same_department = staff.department_id == current_user.department_id
    if not (
        current_user.has_oversight
        or current_user.is_manager
        or same_department
        or staff.id == current_user.id
    ):
        abort(403)

    return _profile_for(staff, editable=(staff.id == current_user.id))


def _profile_for(staff, editable):
    upcoming = (
        Assignment.query.join(Roster)
        .filter(
            Assignment.staff_id == staff.id,
            Assignment.work_date >= today(),
            Assignment.status.in_(
                [AssignmentStatus.SCHEDULED, AssignmentStatus.REPLACEMENT]
            ),
            Roster.status == RosterStatus.PUBLISHED,
        )
        .order_by(Assignment.work_date)
        .limit(8)
        .all()
    )

    recent_attendance = (
        AttendanceRecord.query.filter_by(staff_id=staff.id)
        .order_by(AttendanceRecord.work_date.desc())
        .limit(8)
        .all()
    )

    leave_rows = (
        LeaveRequest.query.filter_by(staff_id=staff.id)
        .order_by(LeaveRequest.start_date.desc())
        .limit(6)
        .all()
    )

    period_start = week_start()
    period_assignments = Assignment.query.filter(
        Assignment.staff_id == staff.id,
        Assignment.work_date >= period_start,
        Assignment.status.in_(
            [AssignmentStatus.SCHEDULED, AssignmentStatus.REPLACEMENT]
        ),
    ).all()

    return render_template(
        "profile/profile.html",
        staff=staff,
        editable=editable,
        upcoming=upcoming,
        recent_attendance=recent_attendance,
        leave_rows=leave_rows,
        stats={
            "week_shifts": len(period_assignments),
            "week_hours": round(
                sum(a.shift.duration_hours for a in period_assignments), 1
            ),
            "week_nights": len([a for a in period_assignments if a.is_night]),
            "leave_days": sum(
                r.days for r in leave_rows if r.status == LeaveStatus.APPROVED
            ),
        },
    )


@profile_bp.route("/update", methods=["POST"])
@login_required
def update():
    """Staff maintain their own contact details and shift preference."""
    before = {
        "phone": current_user.phone,
        "preferred_shift": current_user.preferred_shift,
    }

    phone = (request.form.get("phone") or "").strip()
    preferred = request.form.get("preferred_shift") or None

    current_user.phone = phone or None
    current_user.preferred_shift = preferred if preferred != "NONE" else None

    audit.record(
        AuditAction.STAFF_UPDATED,
        f"{current_user.full_name} updated their contact details",
        actor=current_user,
        entity_type="Staff",
        entity_id=current_user.id,
        department_id=current_user.department_id,
        old=before,
        new={
            "phone": current_user.phone,
            "preferred_shift": current_user.preferred_shift,
        },
    )
    db.session.commit()
    flash("Your details have been updated.", "success")
    return redirect(url_for("profile.me"))


@profile_bp.route("/directory")
@login_required
def directory():
    """Everyone in the hospital, grouped by department."""
    department_id = request.args.get("department_id", type=int)
    query = Staff.query.filter_by(is_active=True)
    if department_id:
        query = query.filter_by(department_id=department_id)

    staff_list = query.order_by(Staff.department_id, Staff.first_name).all()

    grouped = {}
    for staff in staff_list:
        grouped.setdefault(staff.department, []).append(staff)

    return render_template(
        "profile/directory.html",
        grouped=dict(sorted(grouped.items(), key=lambda kv: kv[0].name)),
        departments=Department.query.order_by(Department.name).all(),
        selected=department_id,
        total=len(staff_list),
    )
