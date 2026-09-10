"""Attendance capture and the reconciliation register."""
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

from app.constants import AuditAction
from app.extensions import db
from app.models import AttendanceRecord, Department, Staff
from app.services import attendance as attendance_service
from app.services import audit, reports
from app.services.attendance import AttendanceError
from app.utils.decorators import scheduling_rights_required
from app.utils.timeutils import today, week_start

attendance_bp = Blueprint("attendance", __name__)

# Reasons offered when logging a temporary exit.
EXIT_REASONS = [
    "Break",
    "Official duty outside the facility",
    "Referral or patient transfer",
    "Personal reason",
    "Meeting off-site",
    "Other",
]


def _parse_date(value, fallback=None):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return fallback


@attendance_bp.route("/")
@login_required
def index():
    """The staff member's own attendance terminal and history."""
    status = attendance_service.status_for(current_user)
    history = (
        AttendanceRecord.query.filter_by(staff_id=current_user.id)
        .order_by(AttendanceRecord.work_date.desc(), AttendanceRecord.sign_in_at.desc())
        .limit(30)
        .all()
    )

    this_week = [r for r in history if r.work_date >= week_start()]
    return render_template(
        "attendance/index.html",
        status=status,
        history=history,
        exit_reasons=EXIT_REASONS,
        week_minutes=sum(r.worked_minutes or 0 for r in this_week),
        week_overtime=sum(r.overtime_minutes or 0 for r in this_week),
    )


@attendance_bp.route("/sign-in", methods=["POST"])
@login_required
def sign_in():
    try:
        record = attendance_service.sign_in(current_user)
    except AttendanceError as exc:
        flash(str(exc), "warning")
    else:
        if record.assignment is None:
            flash(
                "Signed in. Note that you have no rostered shift right now, so this "
                "record is marked as unrostered.",
                "warning",
            )
        elif record.late_minutes:
            flash(
                f"Signed in at {record.sign_in_at.strftime('%H:%M')}, "
                f"{record.late_minutes} minute(s) after your shift started.",
                "warning",
            )
        else:
            flash(
                f"Signed in at {record.sign_in_at.strftime('%H:%M')}. "
                f"You are now shown as available.",
                "success",
            )
    return redirect(url_for("attendance.index"))


@attendance_bp.route("/step-out", methods=["POST"])
@login_required
def step_out():
    reason = request.form.get("reason") or "Not stated"
    try:
        event = attendance_service.step_out(current_user, reason)
    except AttendanceError as exc:
        flash(str(exc), "warning")
    else:
        flash(
            f"Logged out of the facility at {event.occurred_at.strftime('%H:%M')} "
            f"({event.reason}). Remember to log back in on your return.",
            "info",
        )
    return redirect(url_for("attendance.index"))


@attendance_bp.route("/return", methods=["POST"])
@login_required
def return_to_facility():
    try:
        event = attendance_service.return_to_facility(current_user)
    except AttendanceError as exc:
        flash(str(exc), "warning")
    else:
        flash(
            f"Welcome back. You were away for {event.duration_minutes} minute(s), "
            f"which has been deducted from your hours on site.",
            "success",
        )
    return redirect(url_for("attendance.index"))


@attendance_bp.route("/sign-out", methods=["POST"])
@login_required
def sign_out():
    try:
        record = attendance_service.sign_out(current_user)
    except AttendanceError as exc:
        flash(str(exc), "warning")
    else:
        message = (
            f"Signed out at {record.sign_out_at.strftime('%H:%M')}. "
            f"{record.worked_minutes} minute(s) recorded on site"
        )
        if record.break_minutes:
            message += f", {record.break_minutes} minute(s) away"
        if record.overtime_minutes:
            message += f", {record.overtime_minutes} minute(s) overtime"
        flash(message + ".", "success")
    return redirect(url_for("attendance.index"))


# ----------------------------------------------------------------------
# Management register
# ----------------------------------------------------------------------
@attendance_bp.route("/register")
@login_required
@scheduling_rights_required
def register():
    """Reconciliation view across a date range."""
    end = _parse_date(request.args.get("end"), today())
    start = _parse_date(request.args.get("start"), end - timedelta(days=6))

    department = None
    department_id = request.args.get("department_id", type=int)
    if current_user.has_oversight:
        if department_id:
            department = db.session.get(Department, department_id)
    else:
        department = current_user.department

    query = AttendanceRecord.query.filter(
        AttendanceRecord.work_date >= start, AttendanceRecord.work_date <= end
    )
    if department is not None:
        staff_ids = [s.id for s in department.members]
        query = query.filter(AttendanceRecord.staff_id.in_(staff_ids or [0]))

    records = query.order_by(
        AttendanceRecord.work_date.desc(), AttendanceRecord.sign_in_at.desc()
    ).all()

    totals = {
        "records": len(records),
        "worked": sum(r.worked_minutes or 0 for r in records),
        "scheduled": sum(r.scheduled_minutes or 0 for r in records),
        "overtime": sum(r.overtime_minutes or 0 for r in records),
        "away": sum(r.break_minutes or 0 for r in records),
        "late": len([r for r in records if (r.late_minutes or 0) > 0]),
        "absent": len([r for r in records if r.status == "ABSENT"]),
    }

    return render_template(
        "attendance/register.html",
        records=records,
        start=start,
        end=end,
        department=department,
        departments=Department.query.order_by(Department.name).all()
        if current_user.has_oversight
        else [],
        totals=totals,
    )


@attendance_bp.route("/register/export.<fmt>")
@login_required
@scheduling_rights_required
def export_register(fmt):
    if fmt not in ("csv", "pdf"):
        abort(404)

    end = _parse_date(request.args.get("end"), today())
    start = _parse_date(request.args.get("start"), end - timedelta(days=6))

    department = None
    department_id = request.args.get("department_id", type=int)
    if current_user.has_oversight:
        if department_id:
            department = db.session.get(Department, department_id)
    else:
        department = current_user.department

    query = AttendanceRecord.query.filter(
        AttendanceRecord.work_date >= start, AttendanceRecord.work_date <= end
    )
    if department is not None:
        staff_ids = [s.id for s in department.members]
        query = query.filter(AttendanceRecord.staff_id.in_(staff_ids or [0]))
    records = query.order_by(AttendanceRecord.work_date, AttendanceRecord.staff_id).all()

    audit.record(
        AuditAction.REPORT_EXPORTED,
        f"{current_user.full_name} exported the attendance register "
        f"({start} to {end}) as {fmt.upper()}",
        actor=current_user,
        entity_type="AttendanceRecord",
        department_id=department.id if department else None,
        commit=True,
    )

    stem = f"attendance_{start.isoformat()}_{end.isoformat()}"
    if fmt == "csv":
        return send_file(
            reports.attendance_csv(records),
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"{stem}.csv",
        )
    return send_file(
        reports.attendance_report_pdf(records, start, end, department),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{stem}.pdf",
    )


@attendance_bp.route("/mark-absentees", methods=["POST"])
@login_required
@scheduling_rights_required
def mark_absentees():
    """Flag rostered staff who never signed in for a completed day."""
    work_date = _parse_date(request.form.get("date"), today() - timedelta(days=1))
    flagged = attendance_service.mark_absentees(work_date, actor=current_user)
    if flagged:
        flash(
            f"{len(flagged)} staff flagged absent for {work_date.strftime('%d/%m/%Y')}.",
            "warning",
        )
    else:
        flash(
            f"No unexplained absences found for {work_date.strftime('%d/%m/%Y')}.",
            "success",
        )
    return redirect(url_for("attendance.register", start=work_date, end=work_date))
