"""
Dashboards.

Two views of the same data. Staff see their own week, attendance state and
notifications. Managers, HR and Admin get the operational picture: who is on
duty right now, which departments are short, and what needs a decision.
"""
from datetime import timedelta

from flask import Blueprint, jsonify, render_template
from flask_login import current_user, login_required

from app.constants import AssignmentStatus, LeaveStatus, RosterStatus
from app.models import Assignment, AuditLog, Department, LeaveRequest, Notification, Roster
from app.services import analytics, attendance as attendance_service, availability
from app.services import leave_service
from app.utils.timeutils import now, today, week_end, week_start

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@login_required
def index():
    if current_user.has_oversight or current_user.is_manager:
        return _operational_dashboard()
    return _staff_dashboard()


# ----------------------------------------------------------------------
# Staff view
# ----------------------------------------------------------------------
def _staff_dashboard():
    start = week_start()
    end = week_end()

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

    upcoming = (
        Assignment.query.join(Roster)
        .filter(
            Assignment.staff_id == current_user.id,
            Assignment.work_date >= today(),
            Assignment.status.in_(
                [AssignmentStatus.SCHEDULED, AssignmentStatus.REPLACEMENT]
            ),
            Roster.status == RosterStatus.PUBLISHED,
        )
        .order_by(Assignment.work_date)
        .limit(5)
        .all()
    )

    leave_rows = (
        LeaveRequest.query.filter_by(staff_id=current_user.id)
        .order_by(LeaveRequest.created_at.desc())
        .limit(5)
        .all()
    )

    unread = (
        Notification.query.filter_by(
            recipient_id=current_user.id, channel="IN_APP", read_at=None
        )
        .order_by(Notification.created_at.desc())
        .limit(6)
        .all()
    )

    return render_template(
        "dashboard/staff.html",
        week_label=f"{start.strftime('%d/%m/%Y')} - {end.strftime('%d/%m/%Y')}",
        week_dates=[start + timedelta(days=i) for i in range(7)],
        assignments=assignments,
        assignments_by_date={a.work_date: a for a in assignments},
        upcoming=upcoming,
        leave_rows=leave_rows,
        notifications=unread,
        attendance=attendance_service.status_for(current_user),
        weekly_hours=round(sum(a.shift.duration_hours for a in assignments), 1),
        night_count=len([a for a in assignments if a.is_night]),
    )


# ----------------------------------------------------------------------
# Manager / HR / Admin view
# ----------------------------------------------------------------------
def _operational_dashboard():
    department = None if current_user.has_oversight else current_user.department
    snapshot = availability.dashboard_snapshot(department=department)

    start = week_start()
    end = week_end()
    report = analytics.workforce_report(start, end, department)

    pending_leave = leave_service.pending_for(current_user)
    gaps = availability.departments_with_gaps()
    if department is not None:
        gaps = {d: rows for d, rows in gaps.items() if d.id == department.id}

    departments = current_user.managed_departments()
    roster_status = []
    for dept in departments:
        roster = Roster.query.filter_by(
            department_id=dept.id, week_start=start
        ).first()
        next_roster = Roster.query.filter_by(
            department_id=dept.id, week_start=start + timedelta(days=7)
        ).first()
        roster_status.append(
            {
                "department": dept,
                "current": roster,
                "next": next_roster,
                "headcount": dept.headcount,
            }
        )

    recent_activity = (
        AuditLog.query.order_by(AuditLog.created_at.desc()).limit(10).all()
        if current_user.has_oversight
        else AuditLog.query.filter_by(department_id=current_user.department_id)
        .order_by(AuditLog.created_at.desc())
        .limit(10)
        .all()
    )

    return render_template(
        "dashboard/operations.html",
        snapshot=snapshot,
        report=report,
        week_label=f"{start.strftime('%d/%m/%Y')} - {end.strftime('%d/%m/%Y')}",
        pending_leave=pending_leave,
        gaps=gaps,
        roster_status=roster_status,
        recent_activity=recent_activity,
        scope=department.name if department else "Hospital-wide",
    )


# ----------------------------------------------------------------------
# Live refresh endpoint
# ----------------------------------------------------------------------
@dashboard_bp.route("/api/presence")
@login_required
def presence_api():
    """
    Current presence counts as JSON.

    The dashboard polls this so the figures stay live without a full page
    reload; the payload is deliberately small enough to poll every 30 seconds.
    """
    department = None if current_user.has_oversight else current_user.department
    snapshot = availability.dashboard_snapshot(department=department)

    return jsonify(
        {
            "as_of": snapshot["moment"].strftime("%H:%M:%S"),
            "current_shift": (
                snapshot["current_shift"].short_name
                if snapshot["current_shift"]
                else "Between shifts"
            ),
            "on_duty": snapshot["on_duty"],
            "temporarily_out": snapshot["temporarily_out"],
            "on_leave": snapshot["on_leave"],
            "absent": snapshot["absent"],
            "off_duty": snapshot["off_duty"],
            "rostered_now": snapshot["rostered_now"],
            "doctors_on_duty": snapshot["doctors_on_duty"],
            "nurses_on_duty": snapshot["nurses_on_duty"],
            "coverage_rate": snapshot["coverage_rate"],
            "staff": [
                {
                    "id": row["staff"].id,
                    "name": row["staff"].full_name,
                    "department": (
                        row["staff"].department.name if row["staff"].department else ""
                    ),
                    "state": row["state"].value,
                    "label": row["state"].label,
                    "colour": row["state"].colour,
                }
                for row in snapshot["rows"]
            ],
        }
    )
