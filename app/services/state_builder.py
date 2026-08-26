"""
Builds a live ScheduleState from the database.

The generator constructs its own state from scratch for a whole week. The
re-optimiser and the manual override screen need the opposite: a snapshot of the
roster exactly as it currently stands, so that any change they propose is judged
against the real world rather than a fresh simulation.
"""
from datetime import timedelta

from flask import current_app

from app.constants import AssignmentStatus, LeaveStatus
from app.models import Assignment, LeaveRequest
from app.services.constraints import (
    HardConstraintChecker,
    SchedulingPolicy,
    ScheduleState,
)
from app.services.fairness import WorkloadTally
from app.utils.timeutils import date_range, is_weekend

# Wide enough to see a complete consecutive-days run either side of the window,
# so a replacement search cannot create a run the generator would have refused.
CONTEXT_DAYS = 8


def build_state(department, start, end, ignore_assignment_ids=(), context_days=CONTEXT_DAYS):
    """
    Snapshot the department's current roster over a window.

    Returns (state, checker, tally, staff_by_id). Assignments listed in
    ignore_assignment_ids are left out, which is how a caller asks "what would
    be legal if this shift were free?".
    """
    policy = SchedulingPolicy.from_config(current_app.config)
    state = ScheduleState(policy=policy)

    staff = [s for s in department.members if s.is_active]
    staff_by_id = {s.id: s for s in staff}
    if not staff_by_id:
        return state, HardConstraintChecker(state, department), WorkloadTally(), {}

    window_start = start - timedelta(days=context_days)
    window_end = end + timedelta(days=context_days)
    ignore = set(ignore_assignment_ids)

    assignments = Assignment.query.filter(
        Assignment.staff_id.in_(staff_by_id),
        Assignment.work_date >= window_start,
        Assignment.work_date <= window_end,
        Assignment.status.in_(
            [AssignmentStatus.SCHEDULED, AssignmentStatus.REPLACEMENT]
        ),
    ).all()

    tally = WorkloadTally.for_staff(staff)
    for assignment in assignments:
        if assignment.id in ignore:
            continue
        state.assign(assignment.staff_id, assignment.work_date, assignment.shift)
        holder = staff_by_id.get(assignment.staff_id)
        if holder is not None:
            tally.add(holder, assignment.shift, assignment.work_date)

    state.leave_dates = _leave_lookup(list(staff_by_id), window_start, window_end)

    return state, HardConstraintChecker(state, department), tally, staff_by_id


def _leave_lookup(staff_ids, start, end):
    requests = LeaveRequest.query.filter(
        LeaveRequest.staff_id.in_(staff_ids),
        LeaveRequest.status == LeaveStatus.APPROVED,
        LeaveRequest.start_date <= end,
        LeaveRequest.end_date >= start,
    ).all()

    lookup = {}
    for request in requests:
        lookup.setdefault(request.staff_id, set()).update(
            date_range(request.start_date, request.end_date)
        )
    return lookup


def rank_candidates(checker, tally, staff_pool, shift, work_date, objective, exclude=()):
    """Feasible staff for a slot, best (fairest) first."""
    feasible = [
        s
        for s in staff_pool
        if s.id not in exclude and checker.is_feasible(s, shift, work_date)
    ]
    feasible.sort(key=lambda s: objective.candidate_rank(s, shift, work_date, tally))
    return feasible


def weekend_flag(work_date):
    return is_weekend(work_date)
