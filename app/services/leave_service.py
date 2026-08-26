"""
Leave and absence workflow.

The point of integration the thesis identifies as missing from most systems is
here: approving leave does not merely record a decision, it immediately releases
the shifts that decision invalidates and calls the re-optimiser to fill them.
The approver sees the coverage consequences in the same action that grants the
leave, rather than discovering them on the morning of the shift.
"""
from app.constants import AuditAction, LeaveStatus, Role
from app.extensions import db
from app.models import LeaveRequest, Staff
from app.services import audit, notifications, reoptimizer
from app.utils.timeutils import now, today


class LeaveError(Exception):
    """Raised when a request or decision is not valid."""


def approvers_for(staff):
    """Who may act on this person's request: their manager plus HR."""
    targets = []
    department = staff.department
    if department is not None and department.manager is not None:
        if department.manager.id != staff.id:
            targets.append(department.manager)

    for officer in Staff.query.filter_by(role=Role.HR, is_active=True).all():
        if officer.id != staff.id and officer.id not in {t.id for t in targets}:
            targets.append(officer)

    if not targets:
        # Last resort so a request can never become unactionable.
        targets = [
            a
            for a in Staff.query.filter_by(role=Role.ADMIN, is_active=True).all()
            if a.id != staff.id
        ]
    return targets


def submit(staff, leave_type, start_date, end_date, reason=None, unplanned=False):
    """Create a pending request and alert the approvers."""
    if end_date < start_date:
        raise LeaveError("The end date cannot fall before the start date.")
    if not unplanned and start_date < today():
        raise LeaveError(
            "Leave cannot be requested for a past date. Report it as an absence instead."
        )

    clash = LeaveRequest.query.filter(
        LeaveRequest.staff_id == staff.id,
        LeaveRequest.status.in_([LeaveStatus.PENDING, LeaveStatus.APPROVED]),
        LeaveRequest.start_date <= end_date,
        LeaveRequest.end_date >= start_date,
    ).first()
    if clash is not None:
        raise LeaveError(
            f"This overlaps an existing {clash.status.lower()} request for "
            f"{clash.period_label}."
        )

    request = LeaveRequest(
        staff_id=staff.id,
        leave_type=leave_type,
        start_date=start_date,
        end_date=end_date,
        reason=reason,
        is_unplanned=unplanned,
        status=LeaveStatus.PENDING,
    )
    db.session.add(request)
    db.session.flush()

    audit.record(
        AuditAction.LEAVE_REQUESTED,
        f"{staff.full_name} requested {request.type_label.lower()} leave for "
        f"{request.period_label} ({request.days} day(s))",
        actor=staff,
        entity_type="LeaveRequest",
        entity_id=request.id,
        department_id=staff.department_id,
        new={
            "type": leave_type,
            "start": str(start_date),
            "end": str(end_date),
            "unplanned": unplanned,
        },
    )

    notifications.notify_leave_submitted(request, approvers_for(staff))
    db.session.commit()
    return request


def approve(request, approver, comment=None):
    """
    Approve leave, release the affected shifts and refill them automatically.

    Returns the ReoptimisationResult so the caller can report how many shifts
    moved and whether anything could not be covered.
    """
    if not request.is_pending:
        raise LeaveError("Only a pending request can be approved.")
    if not approver.can_approve_leave_for(request.staff):
        raise LeaveError("You are not authorised to approve this request.")

    request.status = LeaveStatus.APPROVED
    request.reviewed_by_id = approver.id
    request.reviewed_at = now()
    request.review_comment = comment
    db.session.flush()

    reason = (
        f"{request.staff.full_name} on approved "
        f"{request.type_label.lower()} leave"
    )
    outcome = reoptimizer.release_and_refill(
        request.staff,
        request.start_date,
        request.end_date,
        reason,
        actor=approver,
    )

    request.reoptimised_at = now()
    request.shifts_released = outcome.released_count
    request.shifts_refilled = outcome.refilled_count

    audit.record(
        AuditAction.LEAVE_APPROVED,
        f"{approver.full_name} approved {request.staff.full_name}'s leave for "
        f"{request.period_label}. {outcome.summary}",
        actor=approver,
        entity_type="LeaveRequest",
        entity_id=request.id,
        department_id=request.staff.department_id,
        old={"status": LeaveStatus.PENDING.value},
        new={
            "status": LeaveStatus.APPROVED.value,
            "released": outcome.released_count,
            "refilled": outcome.refilled_count,
            "gaps": outcome.gap_count,
        },
    )

    notifications.notify_leave_decision(request, approver)
    db.session.commit()
    return outcome


def reject(request, approver, comment=None):
    if not request.is_pending:
        raise LeaveError("Only a pending request can be rejected.")
    if not approver.can_approve_leave_for(request.staff):
        raise LeaveError("You are not authorised to reject this request.")

    request.status = LeaveStatus.REJECTED
    request.reviewed_by_id = approver.id
    request.reviewed_at = now()
    request.review_comment = comment

    audit.record(
        AuditAction.LEAVE_REJECTED,
        f"{approver.full_name} rejected {request.staff.full_name}'s leave for "
        f"{request.period_label}",
        actor=approver,
        entity_type="LeaveRequest",
        entity_id=request.id,
        department_id=request.staff.department_id,
        old={"status": LeaveStatus.PENDING.value},
        new={"status": LeaveStatus.REJECTED.value, "comment": comment},
    )

    notifications.notify_leave_decision(request, approver)
    db.session.commit()
    return request


def cancel(request, actor):
    """
    Withdraw a request.

    Cancelling approved leave that has not started yet hands back any shift that
    is still uncovered; a colleague already assigned as cover keeps the shift, so
    nobody is stood down after being told to come in.
    """
    if request.status not in (LeaveStatus.PENDING, LeaveStatus.APPROVED):
        raise LeaveError("This request can no longer be cancelled.")
    if actor.id != request.staff_id and not actor.has_oversight:
        raise LeaveError("You are not authorised to cancel this request.")

    was_approved = request.is_approved
    request.status = LeaveStatus.CANCELLED
    request.reviewed_by_id = actor.id
    request.reviewed_at = now()
    db.session.flush()

    restored = 0
    if was_approved and request.end_date >= today():
        restored = reoptimizer.restore_availability(
            request.staff, max(request.start_date, today()), request.end_date, actor=actor
        )

    audit.record(
        AuditAction.LEAVE_CANCELLED,
        f"{actor.full_name} cancelled {request.staff.full_name}'s leave for "
        f"{request.period_label}"
        + (f", {restored} shift(s) restored" if restored else ""),
        actor=actor,
        entity_type="LeaveRequest",
        entity_id=request.id,
        department_id=request.staff.department_id,
        new={"status": LeaveStatus.CANCELLED.value, "restored": restored},
    )
    db.session.commit()
    return restored


def report_absence(staff, absence_date, reason, actor=None):
    """
    Record an unplanned absence and re-optimise the same day.

    An absence reported by a manager is treated as approved immediately, because
    the staff member is already not coming in; the coverage problem is real
    whether or not anyone signs it off.
    """
    actor = actor or staff
    request = LeaveRequest(
        staff_id=staff.id,
        leave_type="SICK",
        start_date=absence_date,
        end_date=absence_date,
        reason=reason,
        is_unplanned=True,
        status=LeaveStatus.APPROVED,
        reviewed_by_id=actor.id,
        reviewed_at=now(),
        review_comment="Recorded as an unplanned absence",
    )
    db.session.add(request)
    db.session.flush()

    outcome = reoptimizer.release_and_refill(
        staff,
        absence_date,
        absence_date,
        f"{staff.full_name} reported absent: {reason}",
        actor=actor,
    )
    request.reoptimised_at = now()
    request.shifts_released = outcome.released_count
    request.shifts_refilled = outcome.refilled_count

    audit.record(
        AuditAction.LEAVE_APPROVED,
        f"Unplanned absence recorded for {staff.full_name} on "
        f"{absence_date}. {outcome.summary}",
        actor=actor,
        entity_type="LeaveRequest",
        entity_id=request.id,
        department_id=staff.department_id,
        new={
            "reason": reason,
            "released": outcome.released_count,
            "refilled": outcome.refilled_count,
        },
    )
    db.session.commit()
    return request, outcome


def pending_for(user):
    """Requests this user may act on."""
    query = LeaveRequest.query.filter_by(status=LeaveStatus.PENDING)
    if not user.has_oversight:
        if not user.is_manager:
            return []
        member_ids = [s.id for s in user.department.members if s.id != user.id]
        if not member_ids:
            return []
        query = query.filter(LeaveRequest.staff_id.in_(member_ids))
    else:
        query = query.filter(LeaveRequest.staff_id != user.id)
    return query.order_by(LeaveRequest.start_date).all()
