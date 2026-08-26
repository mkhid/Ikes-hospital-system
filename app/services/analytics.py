"""
Workforce analytics.

Turns the operational tables into the indicators the brief asks management to
decide on: utilisation, overtime, absenteeism, coverage, fairness of the
unpopular shifts, leave turnaround and notification reliability. Every figure is
computed from primary records rather than maintained as a running counter, so a
correction to an attendance record immediately corrects the reports.
"""
from collections import defaultdict

from app.constants import (
    AssignmentStatus,
    AttendanceStatus,
    DeliveryStatus,
    LeaveStatus,
)
from app.models import (
    Assignment,
    AttendanceRecord,
    Department,
    LeaveRequest,
    Notification,
    Staff,
)
from app.utils.timeutils import is_weekend


def _pct(part, whole, digits=1):
    return round(part / whole * 100, digits) if whole else 0.0


def workforce_report(start_date, end_date, department=None):
    """The full analytics payload for a date range."""
    staff_query = Staff.query.filter_by(is_active=True)
    if department is not None:
        staff_query = staff_query.filter_by(department_id=department.id)
    staff_list = staff_query.order_by(Staff.first_name).all()
    staff_ids = [s.id for s in staff_list]

    assignments = _assignments(start_date, end_date, staff_ids, department)
    attendance = _attendance(start_date, end_date, staff_ids)
    leave = _leave(start_date, end_date, staff_ids)

    return {
        "period": {"start": start_date, "end": end_date},
        "department": department,
        "headcount": len(staff_list),
        "scheduling": _scheduling_kpis(assignments),
        "attendance": _attendance_kpis(assignments, attendance),
        "leave": _leave_kpis(leave),
        "notifications": _notification_kpis(start_date, end_date, staff_ids),
        "per_staff": _per_staff(staff_list, assignments, attendance, leave),
        "by_department": (
            _by_department(start_date, end_date) if department is None else []
        ),
        "shift_mix": _shift_mix(assignments),
        "daily_coverage": _daily_coverage(assignments),
    }


# ----------------------------------------------------------------------
# Source queries
# ----------------------------------------------------------------------
def _assignments(start_date, end_date, staff_ids, department):
    query = Assignment.query.filter(
        Assignment.work_date >= start_date, Assignment.work_date <= end_date
    )
    rows = query.all()
    if department is not None:
        rows = [r for r in rows if r.roster and r.roster.department_id == department.id]
    return rows


def _attendance(start_date, end_date, staff_ids):
    if not staff_ids:
        return []
    return AttendanceRecord.query.filter(
        AttendanceRecord.staff_id.in_(staff_ids),
        AttendanceRecord.work_date >= start_date,
        AttendanceRecord.work_date <= end_date,
    ).all()


def _leave(start_date, end_date, staff_ids):
    if not staff_ids:
        return []
    return LeaveRequest.query.filter(
        LeaveRequest.staff_id.in_(staff_ids),
        LeaveRequest.start_date <= end_date,
        LeaveRequest.end_date >= start_date,
    ).all()


# ----------------------------------------------------------------------
# Indicator groups
# ----------------------------------------------------------------------
def _scheduling_kpis(assignments):
    live = [a for a in assignments if a.is_live]
    gaps = [a for a in assignments if a.status == AssignmentStatus.UNFILLED]
    replacements = [a for a in assignments if a.status == AssignmentStatus.REPLACEMENT]
    vacated = [a for a in assignments if a.status == AssignmentStatus.VACATED]

    scheduled_hours = sum(a.shift.duration_hours for a in live)
    required = len(live) + len(gaps)

    return {
        "shifts_scheduled": len(live),
        "scheduled_hours": round(scheduled_hours, 1),
        "coverage_gaps": len(gaps),
        "replacements": len(replacements),
        "vacated": len(vacated),
        "fill_rate": _pct(len(live), required),
        "night_shifts": len([a for a in live if a.is_night]),
        "weekend_shifts": len([a for a in live if a.falls_on_weekend]),
    }


def _attendance_kpis(assignments, attendance):
    live = [a for a in assignments if a.is_live]
    completed = [r for r in attendance if r.sign_out_at is not None]
    absent = [r for r in attendance if r.status == AttendanceStatus.ABSENT]
    late = [r for r in attendance if (r.late_minutes or 0) > 0]

    worked_minutes = sum(r.worked_minutes or 0 for r in attendance)
    scheduled_minutes = sum(a.shift.duration_minutes for a in live)
    overtime_minutes = sum(r.overtime_minutes or 0 for r in attendance)

    expected = len(live)
    attended = len([r for r in attendance if r.sign_in_at is not None])

    return {
        "records": len(attendance),
        "completed": len(completed),
        "absent": len(absent),
        "late": len(late),
        "worked_hours": round(worked_minutes / 60, 1),
        "scheduled_hours": round(scheduled_minutes / 60, 1),
        "overtime_hours": round(overtime_minutes / 60, 1),
        "utilisation": _pct(worked_minutes, scheduled_minutes),
        "absenteeism_rate": _pct(len(absent), expected),
        "punctuality_rate": _pct(attended - len(late), attended),
        "attendance_rate": _pct(attended, expected),
        "break_hours": round(sum(r.break_minutes or 0 for r in attendance) / 60, 1),
    }


def _leave_kpis(leave):
    approved = [r for r in leave if r.status == LeaveStatus.APPROVED]
    pending = [r for r in leave if r.status == LeaveStatus.PENDING]
    rejected = [r for r in leave if r.status == LeaveStatus.REJECTED]

    reviewed = [r for r in leave if r.reviewed_at and r.created_at]
    turnaround = [
        (r.reviewed_at - r.created_at).total_seconds() / 3600 for r in reviewed
    ]

    by_type = defaultdict(int)
    for request in approved:
        by_type[request.leave_type] += request.days

    return {
        "total": len(leave),
        "approved": len(approved),
        "pending": len(pending),
        "rejected": len(rejected),
        "days_approved": sum(r.days for r in approved),
        "avg_turnaround_hours": (
            round(sum(turnaround) / len(turnaround), 1) if turnaround else 0.0
        ),
        "approval_rate": _pct(len(approved), len(approved) + len(rejected)),
        "auto_refilled": sum(r.shifts_refilled or 0 for r in approved),
        "released": sum(r.shifts_released or 0 for r in approved),
        "by_type": dict(by_type),
    }


def _notification_kpis(start_date, end_date, staff_ids):
    if not staff_ids:
        return {"sent": 0, "delivered": 0, "failed": 0, "delivery_rate": 0.0, "by_channel": {}}

    rows = Notification.query.filter(
        Notification.recipient_id.in_(staff_ids),
        Notification.created_at >= _as_datetime(start_date),
        Notification.created_at <= _as_datetime(end_date, end=True),
    ).all()

    delivered = [
        r
        for r in rows
        if r.status in (DeliveryStatus.SENT, DeliveryStatus.SIMULATED)
    ]
    failed = [r for r in rows if r.status == DeliveryStatus.FAILED]

    by_channel = defaultdict(lambda: {"sent": 0, "delivered": 0})
    for row in rows:
        by_channel[row.channel]["sent"] += 1
        if row.delivered:
            by_channel[row.channel]["delivered"] += 1

    return {
        "sent": len(rows),
        "delivered": len(delivered),
        "failed": len(failed),
        "delivery_rate": _pct(len(delivered), len(rows)),
        "by_channel": {
            channel: {
                **values,
                "rate": _pct(values["delivered"], values["sent"]),
            }
            for channel, values in by_channel.items()
        },
    }


def _per_staff(staff_list, assignments, attendance, leave):
    """One row per staff member: the table behind the audit report."""
    by_staff = {s.id: s for s in staff_list}
    rows = {
        s.id: {
            "staff": s,
            "shifts": 0,
            "nights": 0,
            "weekends": 0,
            "scheduled_hours": 0.0,
            "worked_hours": 0.0,
            "overtime_hours": 0.0,
            "late_count": 0,
            "late_minutes": 0,
            "absences": 0,
            "leave_days": 0,
            "replacements_taken": 0,
        }
        for s in staff_list
    }

    for assignment in assignments:
        row = rows.get(assignment.staff_id)
        if row is None or not assignment.is_live:
            continue
        row["shifts"] += 1
        row["scheduled_hours"] += assignment.shift.duration_hours
        if assignment.is_night:
            row["nights"] += 1
        if assignment.falls_on_weekend:
            row["weekends"] += 1
        if assignment.status == AssignmentStatus.REPLACEMENT:
            row["replacements_taken"] += 1

    for record in attendance:
        row = rows.get(record.staff_id)
        if row is None:
            continue
        row["worked_hours"] += (record.worked_minutes or 0) / 60
        row["overtime_hours"] += (record.overtime_minutes or 0) / 60
        if (record.late_minutes or 0) > 0:
            row["late_count"] += 1
            row["late_minutes"] += record.late_minutes or 0
        if record.status == AttendanceStatus.ABSENT:
            row["absences"] += 1

    for request in leave:
        row = rows.get(request.staff_id)
        if row is None or request.status != LeaveStatus.APPROVED:
            continue
        row["leave_days"] += request.days

    output = []
    for row in rows.values():
        row["scheduled_hours"] = round(row["scheduled_hours"], 1)
        row["worked_hours"] = round(row["worked_hours"], 1)
        row["overtime_hours"] = round(row["overtime_hours"], 1)
        row["utilisation"] = _pct(row["worked_hours"], row["scheduled_hours"])
        output.append(row)

    output.sort(key=lambda r: (-r["shifts"], r["staff"].full_name))
    return output


def _by_department(start_date, end_date):
    """Hospital-wide comparison across departments."""
    summary = []
    for department in Department.query.filter_by(is_active=True).order_by(Department.name):
        staff_ids = [s.id for s in department.active_members()]
        assignments = _assignments(start_date, end_date, staff_ids, department)
        attendance = _attendance(start_date, end_date, staff_ids)
        scheduling = _scheduling_kpis(assignments)
        attend = _attendance_kpis(assignments, attendance)
        summary.append(
            {
                "department": department,
                "headcount": len(staff_ids),
                "shifts": scheduling["shifts_scheduled"],
                "scheduled_hours": scheduling["scheduled_hours"],
                "worked_hours": attend["worked_hours"],
                "overtime_hours": attend["overtime_hours"],
                "gaps": scheduling["coverage_gaps"],
                "fill_rate": scheduling["fill_rate"],
                "absenteeism_rate": attend["absenteeism_rate"],
                "utilisation": attend["utilisation"],
            }
        )
    return summary


def _shift_mix(assignments):
    """Count of live assignments by shift type, for the dashboard bars."""
    mix = defaultdict(int)
    for assignment in assignments:
        if assignment.is_live and assignment.shift:
            mix[assignment.shift.short_name] += 1
    return dict(mix)


def _daily_coverage(assignments):
    """Filled versus required slots per day across the period."""
    days = defaultdict(lambda: {"filled": 0, "gaps": 0, "weekend": False})
    for assignment in assignments:
        bucket = days[assignment.work_date]
        bucket["weekend"] = is_weekend(assignment.work_date)
        if assignment.is_live:
            bucket["filled"] += 1
        elif assignment.status == AssignmentStatus.UNFILLED:
            bucket["gaps"] += 1

    rows = []
    for work_date in sorted(days):
        bucket = days[work_date]
        required = bucket["filled"] + bucket["gaps"]
        rows.append(
            {
                "date": work_date,
                "filled": bucket["filled"],
                "gaps": bucket["gaps"],
                "required": required,
                "weekend": bucket["weekend"],
                "rate": _pct(bucket["filled"], required),
            }
        )
    return rows


def _as_datetime(value, end=False):
    from datetime import datetime, time

    return datetime.combine(value, time.max if end else time.min)
