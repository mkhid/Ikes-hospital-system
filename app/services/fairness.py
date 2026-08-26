"""
Phase 2 objective: how good is a feasible roster?

Feasibility says a roster is legal; it says nothing about whether one nurse took
every night this week while another took none. This module turns fairness into a
number the optimiser can minimise. Lower is better, and zero means every staff
member carries an identical share of nights, weekends and hours.

Fairness is measured cumulatively, not weekly. The counts a staff member carried
in recent weeks are seeded into the tally before this week is scored, so someone
who worked three nights last week is pushed to the back of the queue this week.
That is what turns fair rostering into a rolling property rather than a
coincidence of one lucky week.
"""
from dataclasses import dataclass, field

from app.utils.timeutils import is_weekend

DEFAULT_WEIGHTS = {
    "unfilled_slot": 1000.0,
    "night_spread": 6.0,
    "weekend_spread": 4.0,
    "hours_spread": 2.0,
    "preference_violation": 1.5,
}


def _variance(values):
    """Population variance. Zero when every staff member carries the same load."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


@dataclass
class WorkloadTally:
    """Per-staff counters the cost function reads."""

    nights: dict = field(default_factory=dict)
    weekends: dict = field(default_factory=dict)
    hours: dict = field(default_factory=dict)
    preference_misses: dict = field(default_factory=dict)

    @classmethod
    def for_staff(cls, staff_list, history=None):
        """Start every counter at zero, then fold in recent-week history."""
        history = history or {}
        tally = cls(
            nights={s.id: 0.0 for s in staff_list},
            weekends={s.id: 0.0 for s in staff_list},
            hours={s.id: 0.0 for s in staff_list},
            preference_misses={s.id: 0.0 for s in staff_list},
        )
        for staff_id, past in history.items():
            if staff_id not in tally.nights:
                continue
            tally.nights[staff_id] += past.get("nights", 0)
            tally.weekends[staff_id] += past.get("weekends", 0)
            tally.hours[staff_id] += past.get("hours", 0.0)
        return tally

    def copy(self):
        return WorkloadTally(
            nights=dict(self.nights),
            weekends=dict(self.weekends),
            hours=dict(self.hours),
            preference_misses=dict(self.preference_misses),
        )

    def apply(self, staff, shift, work_date, sign=1):
        """Add (sign=1) or remove (sign=-1) one assignment from the counters."""
        if staff.id not in self.hours:
            return
        self.hours[staff.id] += sign * shift.duration_hours
        if shift.crosses_midnight:
            self.nights[staff.id] += sign
        if is_weekend(work_date):
            self.weekends[staff.id] += sign
        if staff.preferred_shift and staff.preferred_shift != shift.code:
            self.preference_misses[staff.id] += sign

    def add(self, staff, shift, work_date):
        self.apply(staff, shift, work_date, sign=1)

    def remove(self, staff, shift, work_date):
        self.apply(staff, shift, work_date, sign=-1)


class FairnessObjective:
    """The weighted cost function minimised by the heuristic optimiser."""

    def __init__(self, weights=None):
        self.weights = {**DEFAULT_WEIGHTS, **(weights or {})}

    def cost(self, tally, unfilled_slots=0):
        w = self.weights
        return (
            w["unfilled_slot"] * unfilled_slots
            + w["night_spread"] * _variance(list(tally.nights.values()))
            + w["weekend_spread"] * _variance(list(tally.weekends.values()))
            + w["hours_spread"] * _variance(list(tally.hours.values()))
            + w["preference_violation"] * sum(tally.preference_misses.values())
        )

    def candidate_rank(self, staff, shift, work_date, tally):
        """
        Ordering key for the greedy construction pass.

        Whoever is currently carrying the lightest share of the burden this
        shift represents is offered it first: fewest nights for a night shift,
        fewest weekends on a weekend, then fewest hours overall.
        """
        night_load = tally.nights.get(staff.id, 0) if shift.crosses_midnight else 0
        weekend_load = tally.weekends.get(staff.id, 0) if is_weekend(work_date) else 0
        hours_load = tally.hours.get(staff.id, 0.0)
        preference_penalty = (
            1 if staff.preferred_shift and staff.preferred_shift != shift.code else 0
        )
        return (night_load, weekend_load, hours_load, preference_penalty)

    def report(self, tally, staff_list):
        """Human-readable fairness breakdown for the roster and analytics pages."""
        rows = []
        for staff in staff_list:
            rows.append(
                {
                    "staff": staff,
                    "nights": int(tally.nights.get(staff.id, 0)),
                    "weekends": int(tally.weekends.get(staff.id, 0)),
                    "hours": round(tally.hours.get(staff.id, 0.0), 1),
                    "preference_misses": int(tally.preference_misses.get(staff.id, 0)),
                }
            )
        rows.sort(key=lambda r: r["staff"].full_name)

        nights = [r["nights"] for r in rows] or [0]
        hours = [r["hours"] for r in rows] or [0]
        return {
            "rows": rows,
            "night_spread": max(nights) - min(nights),
            "hours_spread": round(max(hours) - min(hours), 1),
            "night_variance": round(_variance(nights), 3),
            "hours_variance": round(_variance(hours), 3),
        }
