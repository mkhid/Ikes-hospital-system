"""Analytics dashboard, audit trail and the management audit report."""
from datetime import date, timedelta

from flask import (
    Blueprint,
    abort,
    render_template,
    request,
    send_file,
)
from flask_login import current_user, login_required

from app.constants import AuditAction
from app.extensions import db
from app.models import AuditLog, Department, Staff
from app.services import analytics, audit
from app.services import reports as report_service
from app.utils.decorators import oversight_required, scheduling_rights_required
from app.utils.timeutils import today

reports_bp = Blueprint("reports", __name__)


def _parse_date(value, fallback):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return fallback


def _scope():
    """Resolve the reporting period and department from the query string."""
    end = _parse_date(request.args.get("end"), today())
    start = _parse_date(request.args.get("start"), end - timedelta(days=27))

    department = None
    if current_user.has_oversight:
        department_id = request.args.get("department_id", type=int)
        if department_id:
            department = db.session.get(Department, department_id)
    else:
        department = current_user.department

    return start, end, department


@reports_bp.route("/")
@login_required
@scheduling_rights_required
def analytics_dashboard():
    start, end, department = _scope()
    report = analytics.workforce_report(start, end, department)

    return render_template(
        "reports/analytics.html",
        report=report,
        start=start,
        end=end,
        department=department,
        departments=Department.query.order_by(Department.name).all()
        if current_user.has_oversight
        else [],
    )


@reports_bp.route("/audit-report.<fmt>")
@login_required
@scheduling_rights_required
def audit_report(fmt):
    """
    The management decision-support report on every staff member.

    This is the deliverable the brief asks for: scheduling, attendance, leave and
    communication figures per staff member, with the integrity status of the
    audit trail those figures were derived from.
    """
    if fmt not in ("pdf", "csv"):
        abort(404)

    start, end, department = _scope()

    audit.record(
        AuditAction.REPORT_EXPORTED,
        f"{current_user.full_name} exported the workforce audit report "
        f"({start} to {end}) as {fmt.upper()}",
        actor=current_user,
        entity_type="Report",
        department_id=department.id if department else None,
        commit=True,
    )

    stem = f"workforce_audit_{start.isoformat()}_{end.isoformat()}"
    if fmt == "csv":
        return send_file(
            report_service.staff_audit_csv(start, end, department),
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"{stem}.csv",
        )
    return send_file(
        report_service.staff_audit_report_pdf(start, end, department, current_user),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{stem}.pdf",
    )


# ----------------------------------------------------------------------
# Audit trail
# ----------------------------------------------------------------------
@reports_bp.route("/audit-trail")
@login_required
@oversight_required
def audit_trail():
    end = _parse_date(request.args.get("end"), today())
    start = _parse_date(request.args.get("start"), end - timedelta(days=13))

    query = AuditLog.query.filter(
        AuditLog.created_at >= _start_of(start), AuditLog.created_at <= _end_of(end)
    )

    action = request.args.get("action")
    if action:
        query = query.filter_by(action=action)

    actor_id = request.args.get("actor_id", type=int)
    if actor_id:
        query = query.filter_by(actor_id=actor_id)

    department_id = request.args.get("department_id", type=int)
    if department_id:
        query = query.filter_by(department_id=department_id)

    entries = query.order_by(AuditLog.created_at.desc()).limit(400).all()

    return render_template(
        "reports/audit_trail.html",
        entries=entries,
        start=start,
        end=end,
        integrity=audit.verify_chain(),
        actions=sorted({a.value for a in AuditAction}),
        selected_action=action,
        selected_actor=actor_id,
        selected_department=department_id,
        actors=Staff.query.order_by(Staff.first_name).all(),
        departments=Department.query.order_by(Department.name).all(),
    )


@reports_bp.route("/audit-trail/export.<fmt>")
@login_required
@oversight_required
def export_audit_trail(fmt):
    if fmt not in ("pdf", "csv"):
        abort(404)

    end = _parse_date(request.args.get("end"), today())
    start = _parse_date(request.args.get("start"), end - timedelta(days=13))

    entries = (
        AuditLog.query.filter(
            AuditLog.created_at >= _start_of(start),
            AuditLog.created_at <= _end_of(end),
        )
        .order_by(AuditLog.created_at.asc())
        .all()
    )

    audit.record(
        AuditAction.REPORT_EXPORTED,
        f"{current_user.full_name} exported the audit trail ({start} to {end})",
        actor=current_user,
        entity_type="AuditLog",
        commit=True,
    )

    stem = f"audit_trail_{start.isoformat()}_{end.isoformat()}"
    if fmt == "csv":
        return send_file(
            report_service.audit_csv(entries),
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"{stem}.csv",
        )
    return send_file(
        report_service.audit_trail_pdf(entries, start, end),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{stem}.pdf",
    )


def _start_of(value):
    from datetime import datetime, time

    return datetime.combine(value, time.min)


def _end_of(value):
    from datetime import datetime, time

    return datetime.combine(value, time.max)
