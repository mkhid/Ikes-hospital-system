"""
Automatic schedule re-optimisation.

When a staff member becomes unavailable mid-cycle, approved leave, a reported
absence or a deactivated account, this service releases every shift they held in
the affected window and closes the resulting holes without human intervention.

Two properties matter. First, the original assignment is never deleted: it is
marked VACATED and a new REPLACEMENT row is written alongside it, so the roster
shows both who was meant to work and who actually covers. Second, replacements
are chosen through the same hard-constraint checker used at generation time and
ranked by the same fairness objective, so filling a gap can neither breach a rest
period nor quietly dump every emergency shift on the same willing colleague.

Where no legal replacement exists the slot is recorded as a coverage gap and
escalated to the manager rather than being filled illegally or silently dropped.

Staff are rostered to their contracted week, so most shifts carry more people
than their minimum. A released shift is only refilled if losing that person took
it below minimum staffing; otherwise it stands as it is. Cover may take a
colleague into overtime, up to the cover limit, but a colleague who can take the
shift within their contracted hours is always preferred.
"""
from dataclasses import dataclass, field

from flask import current_app

from app.constants import AssignmentStatus, AuditAction, RosterStatus
from app.extensions import db
from app.models import Assignment, Staff
from app.services import audit, notifications
from app.services.fairness import FairnessObjective
from app.services.state_builder import build_state, rank_candidates
from app.utils.timeutils import format_d, now


@dataclass
class ReoptimisationResult:
    released: list = field(default_factory=list)
    refilled: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    # Released shifts still at or above minimum staffing, so left as they are.
    still_covered: list = field(default_factory=list)
    # Replacements that took the colleague past their contracted hours.
    overtime: list = field(default_factory=list)
    # Colleagues moved off a shift above minimum staffing to take the cover.
    moved: list = field(default_factory=list)

    @property
    def released_count(self):
        return len(self.released)

    @property
    def refilled_count(self):
        return len(self.refilled)

    @property
    def gap_count(self):
        return len(self.gaps)

    @property
    def summary(self):
        if not self.released:
            return "No published shifts were affected."
        text = (
            f"{self.released_count} shift(s) released, "
            f"{self.refilled_count} automatically reassigned"
        )
        if self.still_covered:
            text += f", {len(self.still_covered)} still at minimum staffing without cover"
        if self.overtime:
            text += f" ({len(self.overtime)} as overtime)"
        if self.moved:
            text += f", {len(self.moved)} by moving a colleague off a shift above minimum"
        if self.gaps:
            text += f", {self.gap_count} could not be covered"
        return text


def release_and_refill(staff, start_date, end_date, reason, actor=None, notify=True):
    """
    Vacate a staff member's shifts across a date window and refill them.

    Only live assignments on published or draft rosters are touched. Returns a
    ReoptimisationResult describing what moved.
    """
    result = ReoptimisationResult()
    department = staff.department
    if department is None:
        return result

    affected = (
        Assignment.query.filter(
            Assignment.staff_id == staff.id,
            Assignment.work_date >= start_date,
            Assignment.work_date <= end_date,
            Assignment.status.in_(
                [AssignmentStatus.SCHEDULED, AssignmentStatus.REPLACEMENT]
            ),
        )
        .order_by(Assignment.work_date, Assignment.shift_id)
        .all()
    )
    if not affected:
        return result

    # Vacate first, so the replacement search sees the freed capacity.
    for assignment in affected:
        assignment.status = AssignmentStatus.VACATED
        assignment.change_reason = reason
        assignment.updated_at = now()
        result.released.append(assignment)
    db.session.flush()

    objective = FairnessObjective(current_app.config.get("FAIRNESS_WEIGHTS"))
    state, checker, tally, staff_by_id = build_state(
        department, start_date, end_date, allow_overtime=True
    )
    staff_pool = [s for s in staff_by_id.values() if s.id != staff.id]

    for vacated in result.released:
        shift = vacated.shift
        work_date = vacated.work_date

        # Whoever already holds this exact slot cannot also cover it.
        current_holders = {
            a.staff_id
            for a in Assignment.query.filter_by(
                roster_id=vacated.roster_id, work_date=work_date, shift_id=shift.id
            ).all()
            if a.staff_id and a.status in (AssignmentStatus.SCHEDULED, AssignmentStatus.REPLACEMENT)
        }

        # Still at minimum staffing without this person: nothing to cover.
        if len(current_holders) >= department.requirement_for(shift, work_date):
            result.still_covered.append(vacated)
            continue

        candidates = rank_candidates(
            checker,
            tally,
            staff_pool,
            shift,
            work_date,
            objective,
            exclude=current_holders | {staff.id},
        )
        # Within contracted hours first; overtime only when no one else can.
        candidates.sort(key=lambda s: _needs_overtime(state, s, shift, work_date))

        chosen = candidates[0] if candidates else None
        moved_from = None
        if chosen is None:
            chosen, moved_from = _free_a_colleague(
                state, checker, tally, objective, department, staff_pool, shift, work_date,
                exclude=current_holders | {staff.id},
            )

        if chosen:
            if moved_from is not None:
                moved_from.status = AssignmentStatus.VACATED
                moved_from.change_reason = (
                    f"Moved to cover the {shift.short_name.lower()} shift on "
                    f"{format_d(work_date)} for {staff.full_name}"
                )
                moved_from.updated_at = now()
                result.moved.append(moved_from)
            elif _needs_overtime(state, chosen, shift, work_date):
                result.overtime.append(chosen)
            replacement = Assignment(
                roster_id=vacated.roster_id,
                staff_id=chosen.id,
                shift_id=shift.id,
                work_date=work_date,
                status=AssignmentStatus.REPLACEMENT,
                original_staff_id=staff.id,
                change_reason=reason,
            )
            db.session.add(replacement)
            db.session.flush()

            # Keep the in-memory state in step so the next gap sees this cover.
            state.assign(chosen.id, work_date, shift)
            tally.add(chosen, shift, work_date)
            result.refilled.append(replacement)

            audit.record(
                AuditAction.SCHEDULE_REOPTIMISED,
                f"{chosen.full_name} assigned to cover {shift.short_name.lower()} "
                f"shift on {format_d(work_date)} for {staff.full_name}",
                actor=actor,
                entity_type="Assignment",
                entity_id=replacement.id,
                department_id=department.id,
                old={"staff": staff.full_name, "status": "SCHEDULED"},
                new={"staff": chosen.full_name, "status": "REPLACEMENT", "reason": reason},
            )

            if notify and vacated.roster and vacated.roster.status == RosterStatus.PUBLISHED:
                notifications.notify_replacement(replacement, chosen, reason)
                if moved_from is not None:
                    _notify_moved(chosen, moved_from)
        else:
            gap = Assignment(
                roster_id=vacated.roster_id,
                staff_id=None,
                shift_id=shift.id,
                work_date=work_date,
                status=AssignmentStatus.UNFILLED,
                original_staff_id=staff.id,
                change_reason=(
                    "No colleague could take this shift without breaching a hard "
                    "constraint"
                ),
            )
            db.session.add(gap)
            db.session.flush()
            result.gaps.append(
                {"work_date": work_date, "shift": shift, "assignment": gap}
            )

            audit.record(
                AuditAction.COVERAGE_GAP,
                f"Coverage gap: {shift.short_name.lower()} shift on "
                f"{format_d(work_date)} in {department.name} could not be filled",
                actor=actor,
                entity_type="Assignment",
                entity_id=gap.id,
                department_id=department.id,
                new={"reason": reason, "vacated_by": staff.full_name},
            )

    if notify and result.gaps:
        notifications.notify_coverage_gap(
            department, result.gaps, _escalation_targets(department)
        )

    db.session.commit()
    return result


def restore_availability(staff, start_date, end_date, actor=None):
    """
    Undo a release when leave is cancelled before it starts.

    Replacement cover already dispatched is left in place; only slots that are
    still open, or still show the original holder as vacated with no cover, are
    handed back.
    """
    restored = 0
    vacated = Assignment.query.filter(
        Assignment.staff_id == staff.id,
        Assignment.work_date >= start_date,
        Assignment.work_date <= end_date,
        Assignment.status == AssignmentStatus.VACATED,
    ).all()

    for assignment in vacated:
        replacement = Assignment.query.filter(
            Assignment.roster_id == assignment.roster_id,
            Assignment.work_date == assignment.work_date,
            Assignment.shift_id == assignment.shift_id,
            Assignment.original_staff_id == staff.id,
            Assignment.status.in_(
                [AssignmentStatus.REPLACEMENT, AssignmentStatus.UNFILLED]
            ),
        ).first()

        if replacement is None:
            continue
        if replacement.status == AssignmentStatus.REPLACEMENT:
            # A colleague is already covering; leave their assignment alone.
            continue

        db.session.delete(replacement)
        assignment.status = AssignmentStatus.SCHEDULED
        assignment.change_reason = "Leave cancelled, original assignment restored"
        restored += 1

    if restored:
        audit.record(
            AuditAction.SCHEDULE_REOPTIMISED,
            f"{restored} shift(s) restored to {staff.full_name} after leave cancellation",
            actor=actor,
            entity_type="Staff",
            entity_id=staff.id,
            department_id=staff.department_id,
        )
    db.session.commit()
    return restored


def fill_gap(assignment, actor=None, notify=True):
    """
    Retry one recorded coverage gap on demand.

    Used by the manager's roster page after conditions change, for example once
    another leave request is rejected or a colleague returns from leave.
    """
    department = assignment.roster.department
    objective = FairnessObjective(current_app.config.get("FAIRNESS_WEIGHTS"))
    state, checker, tally, staff_by_id = build_state(
        department, assignment.work_date, assignment.work_date, allow_overtime=True
    )

    current_holders = {
        a.staff_id
        for a in Assignment.query.filter_by(
            roster_id=assignment.roster_id,
            work_date=assignment.work_date,
            shift_id=assignment.shift_id,
        ).all()
        if a.staff_id and a.status in (AssignmentStatus.SCHEDULED, AssignmentStatus.REPLACEMENT)
    }

    candidates = rank_candidates(
        checker,
        tally,
        list(staff_by_id.values()),
        assignment.shift,
        assignment.work_date,
        objective,
        exclude=current_holders,
    )
    if not candidates:
        return None

    chosen = candidates[0]
    assignment.staff_id = chosen.id
    assignment.status = AssignmentStatus.REPLACEMENT
    assignment.change_reason = "Coverage gap filled on retry"
    assignment.updated_at = now()

    audit.record(
        AuditAction.SCHEDULE_REOPTIMISED,
        f"Coverage gap on {format_d(assignment.work_date)} filled by {chosen.full_name}",
        actor=actor,
        entity_type="Assignment",
        entity_id=assignment.id,
        department_id=department.id,
        new={"staff": chosen.full_name},
    )

    if notify and assignment.roster.status == RosterStatus.PUBLISHED:
        notifications.notify_replacement(
            assignment, chosen, "Coverage gap filled on retry"
        )

    db.session.commit()
    return chosen


def _free_a_colleague(state, checker, tally, objective, department, staff_pool, shift, work_date,
                      exclude=()):
    """
    Chained cover, for when no colleague can take the shift as things stand.

    With everyone rostered to contract, the usual obstacle is a colleague's own
    shift: another shift the same day, or one the day before or after that the
    rest period rules out. If that
    shift has more people on it than its minimum, the colleague can be moved off
    it: the shift they leave stays covered, their hours do not change, and the
    gap is filled. Returns (colleague, assignment_to_vacate) or (None, None).
    The caller writes the change; state and tally are updated here.
    """
    from datetime import timedelta

    live = [AssignmentStatus.SCHEDULED, AssignmentStatus.REPLACEMENT]
    # The same day first: a colleague already on another shift that day is the
    # commonest obstacle, and moving them within the day disturbs least.
    nearby = [work_date + timedelta(days=offset) for offset in (0, -1, 1, -2, 2)]
    pool = sorted(
        (s for s in staff_pool if s.id not in exclude),
        key=lambda s: objective.candidate_rank(s, shift, work_date, tally),
    )

    for colleague in pool:
        own = Assignment.query.filter(
            Assignment.staff_id == colleague.id,
            Assignment.work_date.in_(nearby),
            Assignment.status.in_(live),
        ).all()
        for assignment in own:
            on_that_shift = Assignment.query.filter(
                Assignment.roster_id == assignment.roster_id,
                Assignment.work_date == assignment.work_date,
                Assignment.shift_id == assignment.shift_id,
                Assignment.status.in_(live),
            ).count()
            if on_that_shift <= department.requirement_for(assignment.shift, assignment.work_date):
                continue

            state.release(colleague.id, assignment.work_date)
            if checker.is_feasible(colleague, shift, work_date):
                tally.remove(colleague, assignment.shift, assignment.work_date)
                return colleague, assignment
            state.assign(colleague.id, assignment.work_date, assignment.shift)

    return None, None


def _notify_moved(colleague, assignment):
    """Tell a colleague the shift they were moved off is no longer theirs."""
    notifications.dispatch(
        colleague,
        f"Shift changed: {format_d(assignment.work_date, '%a %d/%m')}",
        (
            f"Dear {colleague.first_name},\n\n"
            f"You no longer work the {assignment.shift.short_name.lower()} shift "
            f"({assignment.shift.window_label}) on "
            f"{format_d(assignment.work_date, '%A %d/%m/%Y')}. That shift remains "
            f"at its minimum staffing without you.\n\n"
            f"Reason: {assignment.change_reason}"
        ),
        category="SHIFT_CHANGE",
        link=None,
    )


def _needs_overtime(state, staff, shift, work_date):
    """
    Whether taking this shift would put the person past the 40-hour week.

    Measured against the full contracted week, not the leave-reduced one, so it
    agrees with the overtime shown on reports: someone whose leave fell on their
    rest days still works an ordinary 40-hour week.
    """
    after = state.weekly_hours(staff.id, work_date) + shift.duration_hours
    return after > state.policy.max_weekly_hours


def _escalation_targets(department):
    """The manager of the department plus every HR officer."""
    from app.constants import Role

    targets = []
    if department.manager is not None:
        targets.append(department.manager)
    hr_officers = Staff.query.filter_by(role=Role.HR, is_active=True).all()
    for officer in hr_officers:
        if officer.id not in {t.id for t in targets}:
            targets.append(officer)
    return targets
