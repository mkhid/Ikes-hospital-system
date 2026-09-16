"""
Phase 1 of the hybrid scheduling algorithm: hard-constraint verification.

Every rule in this module is inviolable. The generator only ever proposes an
assignment that passes all of them, so a produced roster is feasible by
construction rather than repaired afterwards. The same checker is reused by the
re-optimiser when it looks for a replacement, and by the manual override screen,
which means a manager cannot hand-edit a roster into an illegal state either.
"""
from dataclasses import dataclass, field
from datetime import timedelta

from app.utils.timeutils import week_start


@dataclass
class SchedulingPolicy:
    """Hospital policy limits, loaded from configuration."""

    min_rest_hours: int = 11
    # The contracted week: this many shifts, totalling max_weekly_hours.
    max_weekly_hours: int = 40
    contract_shifts_per_week: int = 5
    # Reachable only when covering a colleague's leave or absence.
    max_cover_weekly_hours: int = 48
    max_consecutive_days: int = 6
    max_consecutive_nights: int = 3
    min_days_off_per_week: int = 1

    @classmethod
    def from_config(cls, config):
        return cls(
            min_rest_hours=config["MIN_REST_HOURS"],
            max_weekly_hours=config["MAX_WEEKLY_HOURS"],
            contract_shifts_per_week=config.get("CONTRACT_SHIFTS_PER_WEEK", 5),
            max_cover_weekly_hours=config.get("MAX_COVER_WEEKLY_HOURS", 48),
            max_consecutive_days=config["MAX_CONSECUTIVE_DAYS"],
            max_consecutive_nights=config["MAX_CONSECUTIVE_NIGHTS"],
            min_days_off_per_week=config["MIN_DAYS_OFF_PER_WEEK"],
        )

    @property
    def max_working_days_per_week(self):
        return 7 - self.min_days_off_per_week

    @property
    def hours_per_contract_shift(self):
        return self.max_weekly_hours / self.contract_shifts_per_week

    @property
    def cover_allowance_hours(self):
        """How far cover may take someone past their contracted hours."""
        return max(0, self.max_cover_weekly_hours - self.max_weekly_hours)

    def contract_hours(self, leave_days_in_week):
        """
        Contracted hours for one week, reduced by one shift per day of leave.

        Someone on leave for two days of a week is contracted for three shifts,
        not five; someone on leave all week is contracted for none.
        """
        shifts = max(0, self.contract_shifts_per_week - leave_days_in_week)
        return shifts * self.hours_per_contract_shift


# Human-readable text for each rule, used in tooltips and the audit report.
RULE_TEXT = {
    "INACTIVE": "Staff account is not active",
    "WRONG_DEPARTMENT": "Staff belongs to a different department",
    "ALREADY_ASSIGNED": "Already assigned to a shift on this day",
    "ON_LEAVE": "On approved leave for this date",
    "NO_NIGHTS": "Staff is not cleared for night duty",
    "OVERLAP": "Overlaps an existing shift",
    "MIN_REST": "Breaks the minimum rest period between shifts",
    "MAX_CONSECUTIVE_DAYS": "Exceeds the maximum consecutive working days",
    "MAX_CONSECUTIVE_NIGHTS": "Exceeds the maximum consecutive night shifts",
    "MAX_WEEKLY_HOURS": "Exceeds contracted weekly hours",
    "MAX_COVER_HOURS": "Exceeds the weekly overtime limit for covering shifts",
    "MAX_WORKING_DAYS": "Leaves too few rest days in the week",
}


@dataclass
class ScheduleState:
    """
    A mutable working copy of who is on what shift.

    The generator and optimiser mutate this in memory, so a candidate roster can
    be scored and revised thousands of times without touching the database.
    Assignments from adjacent weeks are pre-loaded as context, which is what
    stops a Sunday night shift from colliding with the next Monday morning.
    """

    policy: SchedulingPolicy
    # (staff_id, work_date) maps to the Shift occupying that day
    slots: dict = field(default_factory=dict)
    # staff_id maps to the set of dates covered by approved leave
    leave_dates: dict = field(default_factory=dict)
    # staff_id -> {work_date: shift}. An index over slots, so the per-person
    # counters below read one person's handful of shifts rather than scanning
    # the whole department on every one of the thousands of checks a run makes.
    by_staff: dict = field(default_factory=dict)
    # shift id -> hours, cached: Shift.duration_hours is computed on every read.
    hours_of: dict = field(default_factory=dict)

    # --- Mutation ---------------------------------------------------------
    def assign(self, staff_id, work_date, shift):
        self.slots[(staff_id, work_date)] = shift
        self.by_staff.setdefault(staff_id, {})[work_date] = shift
        if shift.id not in self.hours_of:
            self.hours_of[shift.id] = shift.duration_hours

    def release(self, staff_id, work_date):
        self.slots.pop((staff_id, work_date), None)
        self.by_staff.get(staff_id, {}).pop(work_date, None)

    def shift_on(self, staff_id, work_date):
        return self.slots.get((staff_id, work_date))

    def dates_for(self, staff_id):
        return sorted(self.by_staff.get(staff_id, {}))

    def shifts_for(self, staff_id):
        return list(self.by_staff.get(staff_id, {}).items())

    # --- Derived counters -------------------------------------------------
    def weekly_hours(self, staff_id, reference_date):
        start = week_start(reference_date)
        end = start + timedelta(days=6)
        return sum(
            self.hours_of[shift.id]
            for d, shift in self.by_staff.get(staff_id, {}).items()
            if start <= d <= end
        )

    def working_days_in_week(self, staff_id, reference_date):
        start = week_start(reference_date)
        end = start + timedelta(days=6)
        return len([d for d in self.by_staff.get(staff_id, {}) if start <= d <= end])

    def consecutive_days_around(self, staff_id, work_date):
        """Length of the unbroken run of working days that includes work_date."""
        run = 1
        cursor = work_date - timedelta(days=1)
        while self.shift_on(staff_id, cursor):
            run += 1
            cursor -= timedelta(days=1)
        cursor = work_date + timedelta(days=1)
        while self.shift_on(staff_id, cursor):
            run += 1
            cursor += timedelta(days=1)
        return run

    def consecutive_nights_around(self, staff_id, work_date):
        run = 1
        cursor = work_date - timedelta(days=1)
        while True:
            shift = self.shift_on(staff_id, cursor)
            if not shift or not shift.crosses_midnight:
                break
            run += 1
            cursor -= timedelta(days=1)
        cursor = work_date + timedelta(days=1)
        while True:
            shift = self.shift_on(staff_id, cursor)
            if not shift or not shift.crosses_midnight:
                break
            run += 1
            cursor += timedelta(days=1)
        return run

    def is_on_leave(self, staff_id, work_date):
        return work_date in self.leave_dates.get(staff_id, set())

    def leave_days_in_week(self, staff_id, reference_date):
        start = week_start(reference_date)
        end = start + timedelta(days=6)
        return len([d for d in self.leave_dates.get(staff_id, ()) if start <= d <= end])

    def contract_hours(self, staff, reference_date):
        """This person's contracted hours for the week, after leave and any personal cap."""
        hours = self.policy.contract_hours(self.leave_days_in_week(staff.id, reference_date))
        if staff.max_weekly_hours:
            hours = min(hours, staff.max_weekly_hours)
        return hours


class HardConstraintChecker:
    """
    Answers whether one staff member may take one shift on one date.

    By default no one may be rostered beyond their contracted hours for the week.
    A checker built with allow_overtime=True, used only when covering a
    colleague's leave or absence, lets a shift take someone up to one cover
    allowance past their contract, 48 hours in a full week.
    """

    def __init__(self, state, department=None, allow_overtime=False):
        self.state = state
        self.policy = state.policy
        self.department = department
        self.allow_overtime = allow_overtime

    def violations(self, staff, shift, work_date):
        """Every rule this proposed assignment would break. Empty means legal."""
        broken = []
        state = self.state
        policy = self.policy

        if not staff.is_active:
            broken.append("INACTIVE")

        if self.department is not None and staff.department_id != self.department.id:
            broken.append("WRONG_DEPARTMENT")

        already_busy = state.shift_on(staff.id, work_date) is not None
        if already_busy:
            broken.append("ALREADY_ASSIGNED")

        # A night shift is anchored to the day it starts, so leave on the start
        # date blocks it; the spill into the next morning is not double counted.
        if state.is_on_leave(staff.id, work_date):
            broken.append("ON_LEAVE")

        if shift.crosses_midnight and not staff.can_work_nights:
            broken.append("NO_NIGHTS")

        rest_issue = self._rest_violation(staff, shift, work_date)
        if rest_issue:
            broken.append(rest_issue)

        # Simulate the assignment so the run-length and total rules can be
        # evaluated against the roster as it would actually stand.
        if not already_busy:
            state.assign(staff.id, work_date, shift)
        try:
            if (
                state.consecutive_days_around(staff.id, work_date)
                > policy.max_consecutive_days
            ):
                broken.append("MAX_CONSECUTIVE_DAYS")

            if shift.crosses_midnight:
                nights = state.consecutive_nights_around(staff.id, work_date)
                if nights > policy.max_consecutive_nights:
                    broken.append("MAX_CONSECUTIVE_NIGHTS")

            contract = state.contract_hours(staff, work_date)
            hours = state.weekly_hours(staff.id, work_date)
            if not self.allow_overtime:
                if hours > contract:
                    broken.append("MAX_WEEKLY_HOURS")
            elif hours > min(contract + policy.cover_allowance_hours, policy.max_cover_weekly_hours):
                broken.append("MAX_COVER_HOURS")

            if (
                state.working_days_in_week(staff.id, work_date)
                > policy.max_working_days_per_week
            ):
                broken.append("MAX_WORKING_DAYS")
        finally:
            if not already_busy:
                state.release(staff.id, work_date)

        return broken

    def is_feasible(self, staff, shift, work_date):
        return not self.violations(staff, shift, work_date)

    def explain(self, staff, shift, work_date):
        """Violations rendered as sentences for the override screen."""
        return [
            RULE_TEXT.get(code, code)
            for code in self.violations(staff, shift, work_date)
        ]

    # --- Rest period ------------------------------------------------------
    def _rest_violation(self, staff, shift, work_date):
        """
        Compare the proposed shift against the two days either side.

        This single rule enforces the brief's requirement that a night worker
        does not take a morning or afternoon shift: the night shift ends at
        06:00, so a 06:00 morning start leaves zero rest and a 14:00 afternoon
        start leaves eight hours, both short of the eleven-hour minimum. A
        following night shift starting at 22:00 leaves sixteen hours and is
        therefore allowed, which is what makes night rotations possible.
        """
        proposed_start = shift.start_datetime(work_date)
        proposed_end = shift.end_datetime(work_date)
        minimum = timedelta(hours=self.policy.min_rest_hours)

        for offset in (-2, -1, 1, 2):
            neighbour_date = work_date + timedelta(days=offset)
            neighbour = self.state.shift_on(staff.id, neighbour_date)
            if neighbour is None:
                continue

            other_start = neighbour.start_datetime(neighbour_date)
            other_end = neighbour.end_datetime(neighbour_date)

            if proposed_start < other_end and other_start < proposed_end:
                return "OVERLAP"

            if other_end <= proposed_start:
                gap = proposed_start - other_end
            else:
                gap = other_start - proposed_end

            if gap < minimum:
                return "MIN_REST"

        return None
