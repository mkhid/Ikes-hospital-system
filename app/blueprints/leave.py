"""Leave requests, approvals and absence reporting."""
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.constants import LeaveStatus, LeaveType
from app.extensions import db
from app.models import LeaveRequest, Staff
from app.services import leave_service
from app.services.leave_service import LeaveError
from app.utils.decorators import scheduling_rights_required
from app.utils.timeutils import today

leave_bp = Blueprint("leave", __name__)


def _parse_date(value, fallback=None):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return fallback


@leave_bp.route("/")
@login_required
def my_leave():
    requests = (
        LeaveRequest.query.filter_by(staff_id=current_user.id)
        .order_by(LeaveRequest.start_date.desc())
        .all()
    )
    approved_days = sum(r.days for r in requests if r.is_approved)
    return render_template(
        "leave/my_leave.html",
        requests=requests,
        leave_types=list(LeaveType),
        approved_days=approved_days,
        pending_count=len([r for r in requests if r.is_pending]),
        today=today(),
    )


@leave_bp.route("/request", methods=["POST"])
@login_required
def submit():
    start = _parse_date(request.form.get("start_date"))
    end = _parse_date(request.form.get("end_date"), start)
    leave_type = request.form.get("leave_type") or LeaveType.ANNUAL

    if start is None:
        flash("Choose a start date for your leave.", "danger")
        return redirect(url_for("leave.my_leave"))

    try:
        created = leave_service.submit(
            current_user,
            leave_type,
            start,
            end or start,
            reason=request.form.get("reason") or None,
        )
    except LeaveError as exc:
        flash(str(exc), "danger")
    else:
        flash(
            f"Leave request submitted for {created.period_label}. "
            f"Your manager and HR have been notified.",
            "success",
        )
    return redirect(url_for("leave.my_leave"))


@leave_bp.route("/pending")
@login_required
@scheduling_rights_required
def pending():
    requests = leave_service.pending_for(current_user)

    decided = LeaveRequest.query.filter(
        LeaveRequest.status.in_(
            [LeaveStatus.APPROVED, LeaveStatus.REJECTED, LeaveStatus.CANCELLED]
        )
    )
    if not current_user.has_oversight:
        member_ids = [s.id for s in current_user.department.members]
        decided = decided.filter(LeaveRequest.staff_id.in_(member_ids))
    decided = decided.order_by(LeaveRequest.reviewed_at.desc()).limit(20).all()

    return render_template(
        "leave/pending.html",
        requests=requests,
        decided=decided,
    )


@leave_bp.route("/<int:request_id>/approve", methods=["POST"])
@login_required
@scheduling_rights_required
def approve(request_id):
    leave_request = db.session.get(LeaveRequest, request_id)
    if leave_request is None:
        abort(404)

    try:
        outcome = leave_service.approve(
            leave_request, current_user, comment=request.form.get("comment") or None
        )
    except LeaveError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("leave.pending"))

    if outcome.gap_count:
        flash(
            f"Leave approved. {outcome.summary}. The uncovered shift(s) are flagged "
            f"on the roster and the manager has been alerted.",
            "warning",
        )
    elif outcome.released_count:
        flash(
            f"Leave approved and the roster re-optimised automatically: "
            f"{outcome.summary}.",
            "success",
        )
    else:
        flash("Leave approved. No rostered shifts were affected.", "success")

    return redirect(url_for("leave.pending"))


@leave_bp.route("/<int:request_id>/reject", methods=["POST"])
@login_required
@scheduling_rights_required
def reject(request_id):
    leave_request = db.session.get(LeaveRequest, request_id)
    if leave_request is None:
        abort(404)

    try:
        leave_service.reject(
            leave_request, current_user, comment=request.form.get("comment") or None
        )
    except LeaveError as exc:
        flash(str(exc), "danger")
    else:
        flash("Leave request rejected and the staff member notified.", "info")
    return redirect(url_for("leave.pending"))


@leave_bp.route("/<int:request_id>/cancel", methods=["POST"])
@login_required
def cancel(request_id):
    leave_request = db.session.get(LeaveRequest, request_id)
    if leave_request is None:
        abort(404)

    try:
        restored = leave_service.cancel(leave_request, current_user)
    except LeaveError as exc:
        flash(str(exc), "danger")
    else:
        message = "Leave request cancelled."
        if restored:
            message += f" {restored} shift(s) restored to the roster."
        flash(message, "info")

    if current_user.id == leave_request.staff_id:
        return redirect(url_for("leave.my_leave"))
    return redirect(url_for("leave.pending"))


@leave_bp.route("/absence", methods=["GET", "POST"])
@login_required
@scheduling_rights_required
def report_absence():
    """
    Record an unplanned absence on behalf of a staff member.

    This is the emergency path: it approves immediately and re-optimises the
    same day, because the shift starts whether or not paperwork is complete.
    """
    if current_user.has_oversight:
        staff_pool = Staff.query.filter_by(is_active=True).order_by(Staff.first_name).all()
    else:
        staff_pool = [s for s in current_user.department.members if s.is_active]

    if request.method == "POST":
        staff = db.session.get(Staff, int(request.form.get("staff_id", 0)))
        absence_date = _parse_date(request.form.get("absence_date"), today())
        reason = request.form.get("reason") or "No reason given"

        if staff is None or staff.id not in {s.id for s in staff_pool}:
            flash("Select a staff member from your department.", "danger")
            return redirect(url_for("leave.report_absence"))

        _request, outcome = leave_service.report_absence(
            staff, absence_date, reason, actor=current_user
        )

        if outcome.gap_count:
            flash(
                f"Absence recorded for {staff.full_name}. {outcome.summary}. "
                f"Unfilled shifts are flagged on the roster.",
                "warning",
            )
        else:
            flash(
                f"Absence recorded for {staff.full_name}. {outcome.summary}.",
                "success",
            )
        return redirect(url_for("dashboard.index"))

    return render_template(
        "leave/absence.html",
        staff_pool=staff_pool,
        today=today(),
    )
