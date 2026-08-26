"""Roster generation, publication, viewing and manual override."""
from datetime import date, timedelta

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required

from app.constants import AssignmentStatus, AuditAction, RosterStatus
from app.extensions import db
from app.models import Assignment, Department, Roster, Shift, Staff
from app.services import audit, reoptimizer, reports, roster_service
from app.services.roster_service import RosterError
from app.services.scheduler import generate_roster
from app.utils.decorators import scheduling_rights_required
from app.utils.timeutils import date_range, today, week_start

schedule_bp = Blueprint("schedule", __name__)


def _parse_week(value):
    if not value:
        return week_start()
    try:
        return week_start(date.fromisoformat(value))
    except ValueError:
        return week_start()


def _require_department(department_id):
    department = db.session.get(Department, department_id)
    if department is None:
        abort(404)
    if not current_user.can_manage_department(department):
        abort(403)
    return department


# ----------------------------------------------------------------------
# Staff-facing
# ----------------------------------------------------------------------
@schedule_bp.route("/me")
@login_required
def my_schedule():
    """The signed-in staff member's own published shifts."""
    start = _parse_week(request.args.get("week"))
    end = start + timedelta(days=6)

    assignments = (
        Assignment.query.join(Roster)
        .filter(
            Assignment.staff_id == current_user.id,
            Assignment.work_date >= start,
            Assignment.work_date <= end,
            Assignment.status.in_(
                [AssignmentStatus.SCHEDULED, AssignmentStatus.REPLACEMENT]
            ),
            Roster.status == RosterStatus.PUBLISHED,
        )
        .order_by(Assignment.work_date)
        .all()
    )

    roster = Roster.query.filter_by(
        department_id=current_user.department_id, week_start=start
    ).first()

    return render_template(
        "schedule/my_schedule.html",
        start=start,
        end=end,
        dates=date_range(start, end),
        assignments=assignments,
        by_date={a.work_date: a for a in assignments},
        roster=roster if roster and roster.is_published else None,
        total_hours=round(sum(a.shift.duration_hours for a in assignments), 1),
        prev_week=(start - timedelta(days=7)).isoformat(),
        next_week=(start + timedelta(days=7)).isoformat(),
    )


@schedule_bp.route("/department/<int:department_id>")
@login_required
def department_roster(department_id):
    """
    The published roster for a department.

    Any staff member may view their own department's published roster; managers,
    HR and Admin may view any department and also see drafts.
    """
    department = db.session.get(Department, department_id)
    if department is None:
        abort(404)

    may_manage = current_user.can_manage_department(department)
    if not may_manage and current_user.department_id != department.id:
        abort(403)

    start = _parse_week(request.args.get("week"))
    roster = Roster.query.filter_by(
        department_id=department.id, week_start=start
    ).first()

    if roster and not roster.is_published and not may_manage:
        roster = None

    shifts = Shift.query.order_by(Shift.sort_order).all()
    shifts = [s for s in shifts if department.runs_shift(s)] or shifts

    return render_template(
        "schedule/roster.html",
        department=department,
        roster=roster,
        start=start,
        dates=date_range(start, start + timedelta(days=6)),
        shifts=shifts,
        grid=roster.grid(date_range(start, start + timedelta(days=6)), shifts)
        if roster
        else None,
        fairness=roster_service.fairness_summary(roster) if roster else None,
        may_manage=may_manage,
        prev_week=(start - timedelta(days=7)).isoformat(),
        next_week=(start + timedelta(days=7)).isoformat(),
        departments=current_user.managed_departments(),
    )


@schedule_bp.route("/roster/<int:roster_id>")
@login_required
def view_roster(roster_id):
    roster = db.session.get(Roster, roster_id)
    if roster is None:
        abort(404)
    return redirect(
        url_for(
            "schedule.department_roster",
            department_id=roster.department_id,
            week=roster.week_start.isoformat(),
        )
    )


# ----------------------------------------------------------------------
# Manager actions
# ----------------------------------------------------------------------
@schedule_bp.route("/generate", methods=["GET", "POST"])
@login_required
@scheduling_rights_required
def generate():
    departments = current_user.managed_departments()
    if not departments:
        flash("You do not manage any department roster.", "warning")
        return redirect(url_for("dashboard.index"))

    default_week = week_start(today() + timedelta(days=7))

    if request.method == "POST":
        department = _require_department(int(request.form.get("department_id", 0)))
        target_week = _parse_week(request.form.get("week"))

        existing = Roster.query.filter_by(
            department_id=department.id, week_start=target_week
        ).first()
        if existing and existing.is_published and not request.form.get("confirm_replace"):
            flash(
                "A published roster already exists for that week. Withdraw it first "
                "if you want to regenerate.",
                "warning",
            )
            return redirect(
                url_for(
                    "schedule.department_roster",
                    department_id=department.id,
                    week=target_week.isoformat(),
                )
            )

        result = generate_roster(department, target_week, actor=current_user)

        audit.record(
            AuditAction.ROSTER_GENERATED,
            f"{department.name} roster generated for {result.roster.period_label}. "
            f"{result.summary}",
            actor=current_user,
            entity_type="Roster",
            entity_id=result.roster.id,
            department_id=department.id,
            new={
                "slots_required": result.slots_required,
                "slots_filled": result.slots_filled,
                "coverage_gaps": result.gap_count,
                "fairness_cost": round(result.cost_after_optimisation, 3),
                "improvement_percent": result.improvement_percent,
                "seconds": round(result.seconds, 3),
            },
            commit=True,
        )

        if result.gap_count:
            flash(
                f"Roster generated with {result.gap_count} coverage gap(s). "
                f"{result.slots_filled} of {result.slots_required} shifts filled in "
                f"{result.seconds:.2f}s.",
                "warning",
            )
        else:
            flash(
                f"Roster generated: all {result.slots_filled} shifts filled in "
                f"{result.seconds:.2f}s, fairness improved by "
                f"{result.improvement_percent}% during optimisation.",
                "success",
            )

        return redirect(
            url_for(
                "schedule.department_roster",
                department_id=department.id,
                week=target_week.isoformat(),
            )
        )

    return render_template(
        "schedule/generate.html",
        departments=departments,
        default_week=default_week,
        shifts=Shift.query.order_by(Shift.sort_order).all(),
    )


@schedule_bp.route("/roster/<int:roster_id>/publish", methods=["POST"])
@login_required
@scheduling_rights_required
def publish(roster_id):
    roster = db.session.get(Roster, roster_id)
    if roster is None:
        abort(404)
    if not current_user.can_manage_department(roster.department):
        abort(403)

    try:
        sent = roster_service.publish(roster, current_user)
    except RosterError as exc:
        flash(str(exc), "warning")
    else:
        recipients = len([s for s in roster.department.members if s.is_active])
        flash(
            f"Roster published to {recipients} staff. {len(sent)} notification(s) "
            f"dispatched across in-app, email and SMS.",
            "success",
        )

    return redirect(
        url_for(
            "schedule.department_roster",
            department_id=roster.department_id,
            week=roster.week_start.isoformat(),
        )
    )


@schedule_bp.route("/roster/<int:roster_id>/withdraw", methods=["POST"])
@login_required
@scheduling_rights_required
def withdraw(roster_id):
    roster = db.session.get(Roster, roster_id)
    if roster is None:
        abort(404)
    if not current_user.can_manage_department(roster.department):
        abort(403)

    try:
        roster_service.withdraw(roster, current_user)
        flash("Roster withdrawn to draft. It is no longer visible to staff.", "info")
    except RosterError as exc:
        flash(str(exc), "warning")

    return redirect(
        url_for(
            "schedule.department_roster",
            department_id=roster.department_id,
            week=roster.week_start.isoformat(),
        )
    )


@schedule_bp.route("/assignment/<int:assignment_id>/override", methods=["GET", "POST"])
@login_required
@scheduling_rights_required
def override(assignment_id):
    """Reassign one shift by hand, within the hard constraints."""
    assignment = db.session.get(Assignment, assignment_id)
    if assignment is None:
        abort(404)
    department = assignment.roster.department
    if not current_user.can_manage_department(department):
        abort(403)

    if request.method == "POST":
        staff_id = int(request.form.get("staff_id", 0))
        new_staff = db.session.get(Staff, staff_id)
        if new_staff is None:
            flash("Select a staff member to assign.", "danger")
            return redirect(url_for("schedule.override", assignment_id=assignment.id))

        try:
            roster_service.override_assignment(
                assignment,
                new_staff,
                current_user,
                reason=request.form.get("reason") or None,
            )
        except RosterError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("schedule.override", assignment_id=assignment.id))

        flash(f"Shift reassigned to {new_staff.full_name}.", "success")
        return redirect(
            url_for(
                "schedule.department_roster",
                department_id=department.id,
                week=assignment.roster.week_start.isoformat(),
            )
        )

    return render_template(
        "schedule/override.html",
        assignment=assignment,
        department=department,
        eligible=roster_service.eligible_replacements(assignment),
        blocked=roster_service.blocked_candidates(assignment),
    )


@schedule_bp.route("/assignment/<int:assignment_id>/fill", methods=["POST"])
@login_required
@scheduling_rights_required
def fill_gap(assignment_id):
    """Retry an unfilled slot."""
    assignment = db.session.get(Assignment, assignment_id)
    if assignment is None:
        abort(404)
    if not current_user.can_manage_department(assignment.roster.department):
        abort(403)
    if assignment.status != AssignmentStatus.UNFILLED:
        flash("That shift is not a coverage gap.", "warning")
        return redirect(url_for("schedule.view_roster", roster_id=assignment.roster_id))

    chosen = reoptimizer.fill_gap(assignment, actor=current_user)
    if chosen is None:
        flash(
            "No colleague can take that shift without breaching a hard constraint. "
            "The gap remains open.",
            "warning",
        )
    else:
        flash(f"Coverage gap filled by {chosen.full_name}.", "success")

    return redirect(
        url_for(
            "schedule.department_roster",
            department_id=assignment.roster.department_id,
            week=assignment.roster.week_start.isoformat(),
        )
    )


@schedule_bp.route("/roster/<int:roster_id>/export.csv")
@login_required
def export_roster(roster_id):
    roster = db.session.get(Roster, roster_id)
    if roster is None:
        abort(404)
    if (
        not current_user.can_manage_department(roster.department)
        and current_user.department_id != roster.department_id
    ):
        abort(403)

    audit.record(
        AuditAction.REPORT_EXPORTED,
        f"{current_user.full_name} exported the {roster.department.name} roster "
        f"for {roster.period_label}",
        actor=current_user,
        entity_type="Roster",
        entity_id=roster.id,
        department_id=roster.department_id,
        commit=True,
    )

    return send_file(
        reports.roster_csv(roster),
        mimetype="text/csv",
        as_attachment=True,
        download_name=(
            f"roster_{roster.department.code}_{roster.week_start.isoformat()}.csv"
        ),
    )
