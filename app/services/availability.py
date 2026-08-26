"""
Real-time staff availability.

Presence is derived, never stored. A staff member's state at any instant is a
function of three facts already in the database: whether approved leave covers
today, whether they hold a shift that is in progress right now, and what their
attendance record says. Deriving it means the dashboard can never drift out of
step with the underlying records.

The night shift makes this less trivial than it looks. A shift starting at 22:00
on Monday is still in progress at 03:00 on Tuesday, so "who is on duty now" has
to look at yesterday's roster row as well as today's.
"""
from datetime import timedelta

from flask import current_app

from app.constants import (
    AssignmentStatus,
    AttendanceStatus,
    LeaveStatus,
    PresenceState,
)
from app.models import Assignment, AttendanceRecord, Department, LeaveRequest, Shift, Staff
from app.utils.timeutils import now


def current_shift(moment=None):
    """The shift window containing this instant, if any."""
    moment = moment or now()
    for shift in Shift.query.order_by(Shift.sort_order).all():
        start = shift.start_datetime(moment.date())
        end = shift.end_datetime(moment.date())
        if start <= moment < end:
            return shift
        # A night shift that began yesterday may still be running.
        if shift.crosses_midnight:
            start = shift.start_datetime(moment.date() - timedelta(days=1))
            end = shift.end_datetime(moment.date() - timedelta(days=1))
            if start <= moment < end:
                return shift
    return None


def _live_assignments_covering(moment):
    """Every in-progress assignment, keyed by staff id."""
    window_start = moment.date() - timedelta(days=1)
    window_end = moment.date() + timedelta(days=1)

    rows = Assignment.query.filter(
        Assignment.work_date >= window_start,
        Assignment.work_date <= window_end,
        Assignment.status.in_(
            [AssignmentStatus.SCHEDULED, AssignmentStatus.REPLACEMENT]
        ),
        Assignment.staff_id.isnot(None),
    ).all()

    covering = {}
    for row in rows:
        if row.covers(moment):
            covering[row.staff_id] = row
    return covering


def _open_attendance(moment):
    """Attendance records that are signed in and not yet signed out."""
    window_start = moment.date() - timedelta(days=1)
    rows = AttendanceRecord.query.filter(
        AttendanceRecord.work_date >= window_start,
        AttendanceRecord.sign_in_at.isnot(None),
        AttendanceRecord.sign_out_at.is_(None),
    ).all()
    return {row.staff_id: row for row in rows}


def _leave_today(reference_date):
    rows = LeaveRequest.query.filter(
        LeaveRequest.status == LeaveStatus.APPROVED,
        LeaveRequest.start_date <= reference_date,
        LeaveRequest.end_date >= reference_date,
    ).all()
    return {row.staff_id: row for row in rows}


def presence_snapshot(moment=None, department=None):
    """
    Derive the state of every active staff member at one instant.

    Returns a list of dicts, one per staff member, ready for the dashboard.
    """
    moment = moment or now()
    grace = timedelta(minutes=current_app.config.get("ATTENDANCE_GRACE_MINUTES", 15))

    query = Staff.query.filter_by(is_active=True)
    if department is not None:
        query = query.filter_by(department_id=department.id)
    staff_list = query.order_by(Staff.first_name).all()

    on_shift = _live_assignments_covering(moment)
    signed_in = _open_attendance(moment)
    on_leave = _leave_today(moment.date())

    rows = []
    for staff in staff_list:
        assignment = on_shift.get(staff.id)
        record = signed_in.get(staff.id)
        leave = on_leave.get(staff.id)

        if record is not None:
            # Physically accounted for, so attendance wins over the roster.
            if record.status == AttendanceStatus.TEMPORARILY_OUT:
                state = PresenceState.TEMPORARILY_OUT
            else:
                state = PresenceState.AVAILABLE
        elif leave is not None:
            state = PresenceState.ON_LEAVE
        elif assignment is not None:
            due_since = assignment.start_at + grace
            state = PresenceState.ABSENT if moment > due_since else PresenceState.OFF_DUTY
        else:
            state = PresenceState.OFF_DUTY

        rows.append(
            {
                "staff": staff,
                "state": state,
                "assignment": assignment,
                "record": record,
                "leave": leave,
                "since": record.sign_in_at if record else None,
                "out_since": (
                    record.open_exit.occurred_at
                    if record and record.open_exit
                    else None
                ),
            }
        )
    return rows


def summarise(rows):
    """Count each presence state across a snapshot."""
    counts = {state: 0 for state in PresenceState}
    for row in rows:
        counts[row["state"]] += 1
    return counts


def dashboard_snapshot(moment=None, department=None):
    """
    Everything the real-time dashboard renders in one pass.

    Includes hospital-wide counts, a per-department breakdown and the specific
    figures the brief calls for: how many staff are on duty and available right
    now, and how many of those are doctors.
    """
    moment = moment or now()
    rows = presence_snapshot(moment, department)
    counts = summarise(rows)
    shift = current_shift(moment)

    doctors_on_duty = [
        r
        for r in rows
        if r["state"] in (PresenceState.AVAILABLE, PresenceState.TEMPORARILY_OUT)
        and r["staff"].department
        and r["staff"].department.code == "DOC"
    ]
    nurses_on_duty = [
        r
        for r in rows
        if r["state"] in (PresenceState.AVAILABLE, PresenceState.TEMPORARILY_OUT)
        and r["staff"].department
        and r["staff"].department.code == "NUR"
    ]

    by_department = {}
    for row in rows:
        dept = row["staff"].department
        if dept is None:
            continue
        bucket = by_department.setdefault(
            dept.id,
            {
                "department": dept,
                "total": 0,
                "available": 0,
                "temporarily_out": 0,
                "on_leave": 0,
                "absent": 0,
                "off_duty": 0,
                "rostered_now": 0,
                "covered_now": 0,
            },
        )
        bucket["total"] += 1
        bucket[row["state"].lower()] += 1
        if row["assignment"] is not None:
            bucket["rostered_now"] += 1
            if row["state"] in (
                PresenceState.AVAILABLE,
                PresenceState.TEMPORARILY_OUT,
            ):
                bucket["covered_now"] += 1

    # Coverage compares the people rostered for the shift in progress against
    # how many of them are actually accounted for. Someone signed in without a
    # current shift is present and available, but they are not cover for a shift
    # they were not rostered on, so they are excluded from the ratio; counting
    # them would let coverage read above 100 per cent.
    on_shift_rows = [r for r in rows if r["assignment"] is not None]
    covered = [
        r
        for r in on_shift_rows
        if r["state"] in (PresenceState.AVAILABLE, PresenceState.TEMPORARILY_OUT)
    ]
    rostered_now = len(on_shift_rows)
    present = counts[PresenceState.AVAILABLE] + counts[PresenceState.TEMPORARILY_OUT]

    return {
        "moment": moment,
        "current_shift": shift,
        "rows": rows,
        "counts": counts,
        "on_duty": counts[PresenceState.AVAILABLE],
        "temporarily_out": counts[PresenceState.TEMPORARILY_OUT],
        "on_leave": counts[PresenceState.ON_LEAVE],
        "absent": counts[PresenceState.ABSENT],
        "off_duty": counts[PresenceState.OFF_DUTY],
        "total_staff": len(rows),
        "rostered_now": rostered_now,
        "present": present,
        "covered_now": len(covered),
        "doctors_on_duty": len(doctors_on_duty),
        "nurses_on_duty": len(nurses_on_duty),
        "doctor_rows": doctors_on_duty,
        "coverage_rate": (
            round(len(covered) / rostered_now * 100, 1) if rostered_now else 100.0
        ),
        "by_department": sorted(
            by_department.values(), key=lambda b: b["department"].name
        ),
    }


def departments_with_gaps(reference_date=None):
    """Departments carrying unfilled shifts from today onwards."""
    reference_date = reference_date or now().date()
    rows = (
        Assignment.query.filter(
            Assignment.status == AssignmentStatus.UNFILLED,
            Assignment.work_date >= reference_date,
        )
        .order_by(Assignment.work_date)
        .all()
    )
    grouped = {}
    for row in rows:
        dept = row.roster.department
        grouped.setdefault(dept, []).append(row)
    return grouped
