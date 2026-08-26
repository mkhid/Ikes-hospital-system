"""Independent audit of every assignment in the database against the hard rules."""
import pathlib
import sys
from collections import defaultdict
from datetime import timedelta

# Resolve the project root relative to this file, so the script runs
# from any machine and any working directory.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from app.models import Assignment, Department, LeaveRequest, Staff

app = create_app()

MIN_REST_HOURS = 11
MAX_WEEKLY_HOURS = 48
MAX_CONSEC_DAYS = 6
MAX_CONSEC_NIGHTS = 3

with app.app_context():
    rows = (
        Assignment.query.filter(
            Assignment.staff_id.isnot(None),
            Assignment.status.in_(["SCHEDULED", "REPLACEMENT"]),
        )
        .order_by(Assignment.staff_id, Assignment.work_date)
        .all()
    )
    print(f"Auditing {len(rows)} live assignments\n")

    by_staff = defaultdict(list)
    for row in rows:
        by_staff[row.staff_id].append(row)

    violations = []

    # 1. One shift per person per day
    for staff_id, items in by_staff.items():
        seen = defaultdict(list)
        for item in items:
            seen[item.work_date].append(item)
        for work_date, same_day in seen.items():
            if len(same_day) > 1:
                violations.append(
                    f"DOUBLE-BOOKED staff={staff_id} {work_date}: "
                    f"{[a.shift.code for a in same_day]}"
                )

    # 2. Minimum rest between consecutive shifts, and no overlap
    night_then_day = 0
    for staff_id, items in by_staff.items():
        ordered = sorted(items, key=lambda a: a.start_at)
        for first, second in zip(ordered, ordered[1:]):
            if second.start_at < first.end_at:
                violations.append(
                    f"OVERLAP staff={staff_id} {first.work_date} {first.shift.code} "
                    f"-> {second.work_date} {second.shift.code}"
                )
                continue
            gap = (second.start_at - first.end_at).total_seconds() / 3600
            if gap < MIN_REST_HOURS:
                violations.append(
                    f"REST staff={staff_id} only {gap:.1f}h between "
                    f"{first.work_date} {first.shift.code} and "
                    f"{second.work_date} {second.shift.code}"
                )
            if first.shift.crosses_midnight and not second.shift.crosses_midnight:
                if (second.work_date - first.work_date).days == 1:
                    night_then_day += 1
                    violations.append(
                        f"NIGHT->DAY staff={staff_id} night {first.work_date} then "
                        f"{second.shift.code} {second.work_date}"
                    )

    # 3. Weekly hours
    for staff_id, items in by_staff.items():
        weeks = defaultdict(float)
        for item in items:
            monday = item.work_date - timedelta(days=item.work_date.weekday())
            weeks[monday] += item.shift.duration_hours
        for monday, hours in weeks.items():
            if hours > MAX_WEEKLY_HOURS:
                violations.append(
                    f"HOURS staff={staff_id} week {monday}: {hours}h > {MAX_WEEKLY_HOURS}h"
                )

    # 4. Consecutive days and nights
    for staff_id, items in by_staff.items():
        dates = sorted({a.work_date for a in items})
        run = 1
        for prev, curr in zip(dates, dates[1:]):
            run = run + 1 if (curr - prev).days == 1 else 1
            if run > MAX_CONSEC_DAYS:
                violations.append(f"CONSEC-DAYS staff={staff_id} run of {run} ending {curr}")

        nights = sorted({a.work_date for a in items if a.shift.crosses_midnight})
        run = 1
        for prev, curr in zip(nights, nights[1:]):
            run = run + 1 if (curr - prev).days == 1 else 1
            if run > MAX_CONSEC_NIGHTS:
                violations.append(f"CONSEC-NIGHTS staff={staff_id} run of {run} ending {curr}")

    # 5. Night duty clearance
    for staff_id, items in by_staff.items():
        staff = db.session.get(Staff, staff_id)
        if staff.can_work_nights:
            continue
        for item in items:
            if item.shift.crosses_midnight:
                violations.append(
                    f"NIGHT-CLEARANCE staff={staff.full_name} on night {item.work_date}"
                )

    # 6. Approved leave never overlaps an assignment
    for leave in LeaveRequest.query.filter_by(status="APPROVED").all():
        for item in by_staff.get(leave.staff_id, []):
            if leave.start_date <= item.work_date <= leave.end_date:
                violations.append(
                    f"ON-LEAVE staff={leave.staff_id} rostered {item.work_date} "
                    f"during leave {leave.start_date}..{leave.end_date}"
                )

    # 7. Department minimums met
    shortfalls = 0
    for department in Department.query.all():
        for roster in department.rosters:
            for work_date in [roster.week_start + timedelta(days=i) for i in range(7)]:
                for requirement in department.requirements:
                    needed = (
                        requirement.weekend_min_staff
                        if work_date.weekday() >= 5
                        else requirement.min_staff
                    )
                    if needed == 0:
                        continue
                    have = len(
                        [
                            a
                            for a in roster.assignments
                            if a.work_date == work_date
                            and a.shift_id == requirement.shift_id
                            and a.is_live
                        ]
                    )
                    if have < needed:
                        shortfalls += 1

    # 8. Everyone is only rostered in their own department
    for staff_id, items in by_staff.items():
        staff = db.session.get(Staff, staff_id)
        for item in items:
            if item.roster.department_id != staff.department_id:
                violations.append(f"CROSS-DEPT staff={staff.full_name} {item.work_date}")

    print("HARD CONSTRAINT AUDIT")
    print("=" * 62)
    if violations:
        print(f"  {len(violations)} VIOLATION(S) FOUND:\n")
        for v in violations[:40]:
            print("   -", v)
    else:
        print("  No violations. Every assignment satisfies every hard rule.")
    print(f"\n  Under-staffed shift slots (coverage gaps): {shortfalls}")
    print(f"  Night-followed-by-day-shift cases: {night_then_day}")

    # Fairness
    print("\nFAIRNESS: night shifts per staff member, current week")
    print("=" * 62)
    from app.utils.timeutils import week_start

    start = week_start()
    end = start + timedelta(days=6)
    for department in Department.query.order_by(Department.name).all():
        counts = {}
        for staff in department.active_members():
            items = [
                a
                for a in by_staff.get(staff.id, [])
                if start <= a.work_date <= end
            ]
            counts[staff.last_name] = (
                len([a for a in items if a.shift.crosses_midnight]),
                round(sum(a.shift.duration_hours for a in items), 1),
            )
        nights = [v[0] for v in counts.values()]
        hours = [v[1] for v in counts.values()]
        if not nights:
            continue
        spread = max(nights) - min(nights)
        h_spread = round(max(hours) - min(hours), 1)
        print(
            f"  {department.name:<22} nights {dict((k, v[0]) for k, v in counts.items())} "
            f"spread={spread}  hours spread={h_spread}h"
        )

    sys.exit(1 if violations else 0)
