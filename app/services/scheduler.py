"""
The scheduling engine.

A hybrid, two-phase design:

  Phase 1 - Feasible construction. Walk the week chronologically, hardest shift
  first, and fill each slot from the pool of staff who pass every hard
  constraint. Candidates are offered the slot in fairness order, so the greedy
  pass already produces a reasonably balanced roster rather than a legal but
  lopsided one. A slot with no legal candidate becomes a recorded coverage gap
  instead of an illegal assignment.

  Phase 2 - Heuristic optimisation. Hill-climb over the feasible roster with
  three neighbourhood moves (fill a gap, reassign a slot, swap two slots),
  keeping any move that lowers the fairness cost and never accepting one that
  breaks a hard constraint. Feasibility is therefore an invariant of the whole
  search, not something checked at the end.

Chronological order matters in phase 1: whether someone may take Tuesday's
morning shift depends on what they worked on Monday, so days are resolved in
sequence and each decision constrains the next.
"""
import random
import time
from dataclasses import dataclass, field
from datetime import timedelta

from flask import current_app

from app.constants import AssignmentStatus, RosterStatus
from app.extensions import db
from app.models import Assignment, LeaveRequest, Roster, Shift, Staff
from app.services.constraints import (
    HardConstraintChecker,
    SchedulingPolicy,
    ScheduleState,
)
from app.services.fairness import FairnessObjective, WorkloadTally
from app.utils.timeutils import date_range, is_weekend, now, week_start

# How many prior weeks feed the rolling fairness memory.
HISTORY_WEEKS = 4
# Days either side of the target week loaded as constraint context.
#
# This must exceed the longest run of consecutive days the policy allows,
# otherwise the run-length check cannot see far enough back across a week
# boundary: someone finishing five days at the end of one week and starting
# three at the start of the next would read as a run of six, not eight.
CONTEXT_DAYS = 8


@dataclass
class GenerationResult:
    """What the generator produced, for the flash message and the audit trail."""

    roster: Roster = None
    slots_required: int = 0
    slots_filled: int = 0
    gaps: list = field(default_factory=list)
    cost_after_construction: float = 0.0
    cost_after_optimisation: float = 0.0
    iterations: int = 0
    improving_moves: int = 0
    seconds: float = 0.0

    @property
    def gap_count(self):
        return len(self.gaps)

    @property
    def improvement_percent(self):
        if not self.cost_after_construction:
            return 0.0
        delta = self.cost_after_construction - self.cost_after_optimisation
        return round(delta / self.cost_after_construction * 100, 1)

    @property
    def summary(self):
        text = (
            f"{self.slots_filled} of {self.slots_required} shifts filled "
            f"in {self.seconds:.2f}s"
        )
        if self.gap_count:
            text += f", {self.gap_count} coverage gap(s)"
        return text


class SchedulingEngine:
    """Generates one department's roster for one Monday-to-Sunday week."""

    def __init__(self, department, week_starting, actor=None, seed=None):
        self.department = department
        self.week_start = week_start(week_starting)
        self.week_end = self.week_start + timedelta(days=6)
        self.dates = date_range(self.week_start, self.week_end)
        self.actor = actor

        config = current_app.config
        self.policy = SchedulingPolicy.from_config(config)
        # Look back far enough to see a complete consecutive-days run.
        self.context_days = max(CONTEXT_DAYS, self.policy.max_consecutive_days + 2)
        self.objective = FairnessObjective(config.get("FAIRNESS_WEIGHTS"))
        self.iterations = config.get("OPTIMISER_ITERATIONS", 4000)

        seed = seed if seed is not None else config.get("OPTIMISER_SEED")
        self.random = random.Random(int(seed)) if seed not in (None, "") else random.Random()

        self.shifts = (
            Shift.query.order_by(Shift.sort_order).all()
        )
        self.staff = [s for s in department.members if s.is_active]
        self.staff_by_id = {s.id: s for s in self.staff}

        self.state = None
        self.tally = None
        self.checker = None
        # (work_date, shift) -> list of staff_id currently holding the slot
        self.placements = {}

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def generate(self, replace_existing=True):
        started = time.perf_counter()
        result = GenerationResult()

        existing = Roster.query.filter_by(
            department_id=self.department.id, week_start=self.week_start
        ).first()
        if existing and not replace_existing:
            raise ValueError("A roster already exists for this department and week.")

        self._prepare_state(ignore_roster_id=existing.id if existing else None)

        demand = self._build_demand()
        result.slots_required = sum(count for _, _, count in demand)

        self._construct(demand, result)
        result.cost_after_construction = self.objective.cost(
            self.tally, len(result.gaps)
        )

        self._optimise(result)
        result.cost_after_optimisation = self.objective.cost(
            self.tally, len(result.gaps)
        )

        result.slots_filled = sum(len(v) for v in self.placements.values())
        result.seconds = time.perf_counter() - started
        result.iterations = self.iterations

        result.roster = self._persist(existing, result)
        return result

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def _prepare_state(self, ignore_roster_id=None):
        """Load leave, neighbouring assignments and fairness history."""
        self.state = ScheduleState(policy=self.policy)
        self.state.leave_dates = self._load_leave()
        self._load_context_assignments(ignore_roster_id)
        self.tally = WorkloadTally.for_staff(self.staff, history=self._load_history())
        self.checker = HardConstraintChecker(self.state, self.department)
        self.placements = {}

    def _load_leave(self):
        """Approved leave for these staff anywhere near the target week."""
        from app.constants import LeaveStatus

        staff_ids = list(self.staff_by_id)
        if not staff_ids:
            return {}

        window_start = self.week_start - timedelta(days=self.context_days)
        window_end = self.week_end + timedelta(days=self.context_days)

        requests = LeaveRequest.query.filter(
            LeaveRequest.staff_id.in_(staff_ids),
            LeaveRequest.status == LeaveStatus.APPROVED,
            LeaveRequest.start_date <= window_end,
            LeaveRequest.end_date >= window_start,
        ).all()

        lookup = {}
        for request in requests:
            covered = lookup.setdefault(request.staff_id, set())
            covered.update(date_range(request.start_date, request.end_date))
        return lookup

    def _load_context_assignments(self, ignore_roster_id):
        """
        Seed the state with shifts these staff already hold in adjacent weeks.

        Without this the engine would happily put someone on Monday morning
        straight after last Sunday's night shift, because the collision lives in
        a different roster.
        """
        staff_ids = list(self.staff_by_id)
        if not staff_ids:
            return

        query = Assignment.query.filter(
            Assignment.staff_id.in_(staff_ids),
            Assignment.work_date >= self.week_start - timedelta(days=self.context_days),
            Assignment.work_date <= self.week_end + timedelta(days=self.context_days),
            Assignment.status.in_(
                [AssignmentStatus.SCHEDULED, AssignmentStatus.REPLACEMENT]
            ),
        )
        if ignore_roster_id:
            query = query.filter(Assignment.roster_id != ignore_roster_id)

        for assignment in query.all():
            # Only shifts outside the week being rebuilt act as fixed context.
            if self.week_start <= assignment.work_date <= self.week_end:
                continue
            self.state.assign(
                assignment.staff_id, assignment.work_date, assignment.shift
            )

    def _load_history(self):
        """Night, weekend and hour counts over the preceding weeks."""
        staff_ids = list(self.staff_by_id)
        if not staff_ids:
            return {}

        history_start = self.week_start - timedelta(weeks=HISTORY_WEEKS)
        rows = Assignment.query.filter(
            Assignment.staff_id.in_(staff_ids),
            Assignment.work_date >= history_start,
            Assignment.work_date < self.week_start,
            Assignment.status.in_(
                [AssignmentStatus.SCHEDULED, AssignmentStatus.REPLACEMENT]
            ),
        ).all()

        history = {}
        for row in rows:
            bucket = history.setdefault(
                row.staff_id, {"nights": 0, "weekends": 0, "hours": 0.0}
            )
            bucket["hours"] += row.shift.duration_hours
            if row.shift.crosses_midnight:
                bucket["nights"] += 1
            if is_weekend(row.work_date):
                bucket["weekends"] += 1

        # Decay history so older weeks weigh less than the most recent one.
        for bucket in history.values():
            bucket["nights"] /= HISTORY_WEEKS
            bucket["weekends"] /= HISTORY_WEEKS
            bucket["hours"] /= HISTORY_WEEKS
        return history

    def _build_demand(self):
        """
        The slots to fill, hardest first.

        Days run in calendar order because each decision constrains the next.
        Within a day the night shift is resolved first: it has the smallest
        eligible pool and the longest shadow over the following day.
        """
        order = {True: 0}  # night shifts sort ahead of day shifts
        demand = []
        for work_date in self.dates:
            day_shifts = sorted(
                self.shifts,
                key=lambda s: (order.get(s.crosses_midnight, 1), s.sort_order),
            )
            for shift in day_shifts:
                required = self.department.requirement_for(shift, work_date)
                if required > 0:
                    demand.append((work_date, shift, required))
        return demand

    # ------------------------------------------------------------------
    # Phase 1: feasible construction
    # ------------------------------------------------------------------
    def _construct(self, demand, result):
        for work_date, shift, required in demand:
            key = (work_date, shift.id)
            self.placements.setdefault(key, [])

            for _ in range(required):
                chosen = self._best_candidate(shift, work_date)
                if chosen is None:
                    result.gaps.append(
                        {
                            "work_date": work_date,
                            "shift": shift,
                            "reason": "No staff member satisfied all hard constraints",
                        }
                    )
                    continue
                self._place(chosen, shift, work_date)

    def _best_candidate(self, shift, work_date, exclude=()):
        """The feasible staff member with the lightest relevant workload."""
        candidates = [
            s
            for s in self.staff
            if s.id not in exclude and self.checker.is_feasible(s, shift, work_date)
        ]
        if not candidates:
            return None

        # Shuffle first so equal-ranked staff do not always resolve alphabetically.
        self.random.shuffle(candidates)
        candidates.sort(
            key=lambda s: self.objective.candidate_rank(s, shift, work_date, self.tally)
        )
        return candidates[0]

    def _place(self, staff, shift, work_date):
        self.state.assign(staff.id, work_date, shift)
        self.tally.add(staff, shift, work_date)
        self.placements.setdefault((work_date, shift.id), []).append(staff.id)

    def _lift(self, staff, shift, work_date):
        self.state.release(staff.id, work_date)
        self.tally.remove(staff, shift, work_date)
        holders = self.placements.get((work_date, shift.id), [])
        if staff.id in holders:
            holders.remove(staff.id)

    # ------------------------------------------------------------------
    # Phase 2: heuristic optimisation
    # ------------------------------------------------------------------
    def _optimise(self, result):
        """
        Hill-climb across three neighbourhood moves.

        Every candidate move is validated by the same hard-constraint checker
        used in phase 1, so the roster is feasible at every step of the search.
        Only strictly improving moves are kept, which makes the run
        deterministic in outcome quality and safe to stop at any point.
        """
        if not self.staff or not any(self.placements.values()):
            return

        shift_by_id = {s.id: s for s in self.shifts}
        current = self.objective.cost(self.tally, len(result.gaps))

        for _ in range(self.iterations):
            move = self.random.random()
            if result.gaps and move < 0.25:
                improved = self._try_fill_gap(result, shift_by_id)
                if improved:
                    result.improving_moves += 1
                    current = self.objective.cost(self.tally, len(result.gaps))
                continue

            if move < 0.65:
                delta = self._try_reassign(shift_by_id, current, len(result.gaps))
            else:
                delta = self._try_swap(shift_by_id, current, len(result.gaps))

            if delta is not None:
                current = delta
                result.improving_moves += 1

    def _try_fill_gap(self, result, shift_by_id):
        """Attempt to close a recorded coverage gap with any feasible staff member."""
        index = self.random.randrange(len(result.gaps))
        gap = result.gaps[index]
        shift = gap["shift"]
        work_date = gap["work_date"]

        chosen = self._best_candidate(shift, work_date)
        if chosen is None:
            return False

        self._place(chosen, shift, work_date)
        result.gaps.pop(index)
        return True

    def _random_placement(self):
        keys = [k for k, v in self.placements.items() if v]
        if not keys:
            return None
        key = keys[self.random.randrange(len(keys))]
        holders = self.placements[key]
        staff_id = holders[self.random.randrange(len(holders))]
        return key[0], key[1], staff_id

    def _try_reassign(self, shift_by_id, current_cost, gap_count):
        """Hand one slot to a different staff member if that improves fairness."""
        picked = self._random_placement()
        if picked is None:
            return None
        work_date, shift_id, staff_id = picked
        shift = shift_by_id[shift_id]
        holder = self.staff_by_id.get(staff_id)
        if holder is None:
            return None

        self._lift(holder, shift, work_date)

        already_on_slot = set(self.placements.get((work_date, shift_id), []))
        replacement = self._best_candidate(
            shift, work_date, exclude=already_on_slot | {staff_id}
        )

        if replacement is None:
            self._place(holder, shift, work_date)
            return None

        self._place(replacement, shift, work_date)
        new_cost = self.objective.cost(self.tally, gap_count)
        if new_cost < current_cost:
            return new_cost

        self._lift(replacement, shift, work_date)
        self._place(holder, shift, work_date)
        return None

    def _try_swap(self, shift_by_id, current_cost, gap_count):
        """Exchange two staff between two slots when neither can simply move."""
        first = self._random_placement()
        second = self._random_placement()
        if first is None or second is None:
            return None

        date_a, shift_id_a, staff_id_a = first
        date_b, shift_id_b, staff_id_b = second
        if staff_id_a == staff_id_b or (date_a, shift_id_a) == (date_b, shift_id_b):
            return None

        shift_a = shift_by_id[shift_id_a]
        shift_b = shift_by_id[shift_id_b]
        staff_a = self.staff_by_id.get(staff_id_a)
        staff_b = self.staff_by_id.get(staff_id_b)
        if staff_a is None or staff_b is None:
            return None

        self._lift(staff_a, shift_a, date_a)
        self._lift(staff_b, shift_b, date_b)

        legal = self.checker.is_feasible(staff_b, shift_a, date_a) and self.checker.is_feasible(
            staff_a, shift_b, date_b
        )
        if not legal:
            self._place(staff_a, shift_a, date_a)
            self._place(staff_b, shift_b, date_b)
            return None

        self._place(staff_b, shift_a, date_a)
        self._place(staff_a, shift_b, date_b)

        new_cost = self.objective.cost(self.tally, gap_count)
        if new_cost < current_cost:
            return new_cost

        self._lift(staff_b, shift_a, date_a)
        self._lift(staff_a, shift_b, date_b)
        self._place(staff_a, shift_a, date_a)
        self._place(staff_b, shift_b, date_b)
        return None

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _persist(self, existing, result):
        """Write the roster back as a draft, replacing any previous draft."""
        if existing:
            Assignment.query.filter_by(roster_id=existing.id).delete(
                synchronize_session=False
            )
            roster = existing
            roster.status = RosterStatus.DRAFT
            roster.published_at = None
            roster.published_by_id = None
        else:
            roster = Roster(
                department_id=self.department.id,
                week_start=self.week_start,
                week_end=self.week_end,
            )
            db.session.add(roster)

        roster.status = RosterStatus.DRAFT
        roster.generated_by_id = self.actor.id if self.actor else None
        roster.generated_at = now()
        roster.fairness_score = round(result.cost_after_optimisation, 3)
        roster.generation_seconds = round(result.seconds, 3)
        roster.optimiser_iterations = self.iterations
        roster.notes = result.summary
        db.session.flush()

        shift_by_id = {s.id: s for s in self.shifts}
        for (work_date, shift_id), holders in sorted(self.placements.items()):
            for staff_id in holders:
                db.session.add(
                    Assignment(
                        roster_id=roster.id,
                        staff_id=staff_id,
                        shift_id=shift_id,
                        work_date=work_date,
                        status=AssignmentStatus.SCHEDULED,
                    )
                )

        for gap in result.gaps:
            db.session.add(
                Assignment(
                    roster_id=roster.id,
                    staff_id=None,
                    shift_id=gap["shift"].id,
                    work_date=gap["work_date"],
                    status=AssignmentStatus.UNFILLED,
                    change_reason=gap["reason"],
                )
            )

        db.session.commit()
        return roster


def generate_roster(department, week_starting, actor=None, seed=None):
    """Convenience wrapper used by the blueprints."""
    engine = SchedulingEngine(department, week_starting, actor=actor, seed=seed)
    return engine.generate()
