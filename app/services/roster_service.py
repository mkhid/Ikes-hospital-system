"""
Roster lifecycle: publish, override and withdraw.

Generation lives in scheduler.py; this module covers what a manager does with
the result. Publication is the moment a draft becomes binding, so it is the
point at which notifications go out and an immutable audit entry is written.

Manual overrides run through the same hard-constraint checker as the generator.
A manager can move anyone they like within the rules, but cannot hand-edit the
roster into a state the engine would have refused to produce.
"""
from app.constants import AssignmentStatus, AuditAction, RosterStatus
from app.extensions import db
from app.models import Assignment
from app.services import audit, notifications
from app.services.fairness import FairnessObjective, WorkloadTally
from app.services.state_builder import build_state, rank_candidates
from app.utils.timeutils import format_d, now


class RosterError(Exception):
    """Raised when an operation is not valid for the roster's current state."""


def publish(roster, actor):
    """
    Make a draft roster binding and notify every member of the department.

    Returns the notification rows created, so the caller can report the delivery
    count back to the manager.
    """
    if roster.status == RosterStatus.PUBLISHED:
        raise RosterError("This roster has already been published.")

    roster.status = RosterStatus.PUBLISHED
    roster.published_by_id = actor.id
    roster.published_at = now()
    db.session.flush()

    recipients = [s for s in roster.department.members if s.is_active]
    sent = notifications.notify_roster_published(roster, recipients)

    audit.record(
        AuditAction.ROSTER_PUBLISHED,
        f"{roster.department.name} roster for {roster.period_label} published to "
        f"{len(recipients)} staff",
        actor=actor,
        entity_type="Roster",
        entity_id=roster.id,
        department_id=roster.department_id,
        old={"status": RosterStatus.DRAFT.value},
        new={
            "status": RosterStatus.PUBLISHED.value,
            "recipients": len(recipients),
            "notifications": len(sent),
            "shifts": len(roster.live_assignments()),
            "coverage_gaps": roster.gap_count,
        },
    )

    audit.record(
        AuditAction.NOTIFICATION_DISPATCHED,
        f"{len(sent)} notification(s) dispatched for the "
        f"{roster.department.name} roster {roster.period_label}",
        actor=actor,
        entity_type="Roster",
        entity_id=roster.id,
        department_id=roster.department_id,
    )

    db.session.commit()
    return sent


def withdraw(roster, actor):
    """Return a published roster to draft so it can be regenerated."""
    if roster.status != RosterStatus.PUBLISHED:
        raise RosterError("Only a published roster can be withdrawn.")

    roster.status = RosterStatus.DRAFT
    roster.published_at = None
    roster.published_by_id = None

    audit.record(
        AuditAction.ROSTER_DELETED,
        f"{roster.department.name} roster for {roster.period_label} withdrawn to draft",
        actor=actor,
        entity_type="Roster",
        entity_id=roster.id,
        department_id=roster.department_id,
        old={"status": RosterStatus.PUBLISHED.value},
        new={"status": RosterStatus.DRAFT.value},
    )
    db.session.commit()
    return roster


def eligible_replacements(assignment, limit=None):
    """
    Staff who could legally take this shift, fairest first.

    Powers the override screen: the dropdown only ever offers people the
    constraint checker has already cleared, so an invalid override is not
    something the manager can pick by mistake.
    """
    department = assignment.roster.department
    objective = FairnessObjective()
    state, checker, tally, staff_by_id = build_state(
        department,
        assignment.work_date,
        assignment.work_date,
        ignore_assignment_ids=[assignment.id],
        allow_overtime=True,  # an override writes a REPLACEMENT: it is cover
    )

    holders = {
        a.staff_id
        for a in Assignment.query.filter_by(
            roster_id=assignment.roster_id,
            work_date=assignment.work_date,
            shift_id=assignment.shift_id,
        ).all()
        if a.staff_id
        and a.id != assignment.id
        and a.status in (AssignmentStatus.SCHEDULED, AssignmentStatus.REPLACEMENT)
    }

    ranked = rank_candidates(
        checker,
        tally,
        list(staff_by_id.values()),
        assignment.shift,
        assignment.work_date,
        objective,
        exclude=holders | ({assignment.staff_id} if assignment.staff_id else set()),
    )
    return ranked[:limit] if limit else ranked


def blocked_candidates(assignment):
    """Staff who cannot take this shift, with the reason, for the override screen."""
    department = assignment.roster.department
    state, checker, _tally, staff_by_id = build_state(
        department,
        assignment.work_date,
        assignment.work_date,
        ignore_assignment_ids=[assignment.id],
        allow_overtime=True,  # an override writes a REPLACEMENT: it is cover
    )

    blocked = []
    for staff in staff_by_id.values():
        if staff.id == assignment.staff_id:
            continue
        reasons = checker.explain(staff, assignment.shift, assignment.work_date)
        if reasons:
            blocked.append({"staff": staff, "reasons": reasons})
    blocked.sort(key=lambda b: b["staff"].full_name)
    return blocked


def override_assignment(assignment, new_staff, actor, reason=None, notify=True):
    """
    Reassign a shift by hand, subject to the same hard constraints.

    Raises RosterError if the proposed assignment would break a rule, quoting
    the rules it breaks.
    """
    department = assignment.roster.department
    state, checker, _tally, _staff = build_state(
        department,
        assignment.work_date,
        assignment.work_date,
        ignore_assignment_ids=[assignment.id],
        allow_overtime=True,  # an override writes a REPLACEMENT: it is cover
    )

    problems = checker.explain(new_staff, assignment.shift, assignment.work_date)
    if problems:
        raise RosterError(
            f"{new_staff.full_name} cannot take this shift: " + "; ".join(problems)
        )

    previous = assignment.staff
    assignment.original_staff_id = previous.id if previous else None
    assignment.staff_id = new_staff.id
    assignment.status = AssignmentStatus.REPLACEMENT
    assignment.is_override = True
    assignment.change_reason = reason or "Manual override by manager"
    assignment.updated_at = now()
    db.session.flush()

    audit.record(
        AuditAction.ASSIGNMENT_OVERRIDE,
        f"{assignment.shift.short_name} shift on {format_d(assignment.work_date)} "
        f"reassigned from {previous.full_name if previous else 'unfilled'} to "
        f"{new_staff.full_name}",
        actor=actor,
        entity_type="Assignment",
        entity_id=assignment.id,
        department_id=department.id,
        old={"staff": previous.full_name if previous else None},
        new={"staff": new_staff.full_name, "reason": assignment.change_reason},
    )

    if notify and assignment.roster.status == RosterStatus.PUBLISHED:
        notifications.notify_replacement(
            assignment, new_staff, assignment.change_reason
        )
        if previous is not None:
            notifications.dispatch(
                previous,
                f"Shift removed: {format_d(assignment.work_date, '%a %d/%m')}",
                (
                    f"Dear {previous.first_name},\n\n"
                    f"You have been removed from the "
                    f"{assignment.shift.short_name.lower()} shift "
                    f"({assignment.shift.window_label}) on "
                    f"{format_d(assignment.work_date, '%A %d/%m/%Y')}.\n\n"
                    f"Reason: {assignment.change_reason}\n\n"
                    f"{new_staff.full_name} will cover this shift."
                ),
                category="SHIFT_CHANGE",
                link=None,
            )

    db.session.commit()
    return assignment


def fairness_summary(roster):
    """
    Per-staff workload for the roster page: the fairness counts, each person's
    mix of shifts and stated preference, and their hours against their contract.

    Hours are compared with each person's contracted hours for the week rather
    than with each other. A colleague on three days' leave is contracted for 16
    hours, not 40, so a raw hours spread would report her correct week as a
    24-hour imbalance. contract_spread is the spread of hours minus contract,
    which is zero when everyone is rostered exactly to their contract.
    """
    from datetime import timedelta

    from flask import current_app

    from app.constants import LeaveStatus
    from app.models import LeaveRequest, Shift
    from app.services.constraints import SchedulingPolicy

    department = roster.department
    staff_list = [s for s in department.members if s.is_active]
    tally = WorkloadTally.for_staff(staff_list)
    by_id = {s.id: s for s in staff_list}
    mix = {s.id: {} for s in staff_list}

    for assignment in roster.live_assignments():
        staff = by_id.get(assignment.staff_id)
        if staff is not None:
            tally.add(staff, assignment.shift, assignment.work_date)
            code = assignment.shift.code
            mix[staff.id][code] = mix[staff.id].get(code, 0) + 1

    report = FairnessObjective().report(tally, staff_list)

    # Contracted hours, the same rule the engine rosters to: one shift less for
    # each day of approved leave in the week, and never above a personal cap.
    policy = SchedulingPolicy.from_config(current_app.config)
    week = {roster.week_start + timedelta(days=i) for i in range(7)}
    leave_days = {s.id: set() for s in staff_list}
    for request in LeaveRequest.query.filter(
        LeaveRequest.staff_id.in_(list(by_id)),
        LeaveRequest.status == LeaveStatus.APPROVED,
        LeaveRequest.start_date <= roster.week_end,
        LeaveRequest.end_date >= roster.week_start,
    ).all():
        day = request.start_date
        while day <= request.end_date:
            if day in week:
                leave_days[request.staff_id].add(day)
            day += timedelta(days=1)

    shift_names = {s.code: s.short_name for s in Shift.query.all()}
    for row in report["rows"]:
        staff = row["staff"]
        counts = mix[staff.id]
        row["morning"] = counts.get("MORNING", 0)
        row["afternoon"] = counts.get("AFTERNOON", 0)
        row["shifts"] = sum(counts.values())
        row["leave_days"] = len(leave_days[staff.id])
        contract = policy.contract_hours(row["leave_days"])
        if staff.max_weekly_hours:
            contract = min(contract, staff.max_weekly_hours)
        row["contract_hours"] = round(contract, 1)
        row["off_contract"] = round(row["hours"] - contract, 1)
        row["preferred_shift"] = staff.preferred_shift
        row["preferred_label"] = shift_names.get(staff.preferred_shift)

    gaps = [row["off_contract"] for row in report["rows"]] or [0]
    report["contract_spread"] = round(max(gaps) - min(gaps), 1)
    report["off_contract_count"] = len([g for g in gaps if g])
    return report
