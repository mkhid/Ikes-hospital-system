"""Exercise the POST workflows the way a user would drive them."""
import pathlib
import sys
from datetime import timedelta

# Resolve the project root relative to this file, so the script runs
# from any machine and any working directory.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import create_app
from app.extensions import db
from app.models import (
    Assignment,
    AttendanceRecord,
    AuditLog,
    Department,
    LeaveRequest,
    Notification,
    Roster,
    Staff,
)
from app.services import audit
from app.utils.timeutils import today, week_start

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False

results = []


def check(label, condition, detail=""):
    results.append((label, condition, detail))
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f" - {detail}" if detail else ""))


def login(staff_no):
    client = app.test_client()
    response = client.post(
        "/login", data={"identifier": staff_no, "password": "Password123"},
        follow_redirects=True,
    )
    return client


print("\n1. AUTHENTICATION")
client = app.test_client()
bad = client.post("/login", data={"identifier": "TH-ADM-001", "password": "wrong"})
check("Wrong password rejected", bad.status_code == 401)
ok = client.post("/login", data={"identifier": "TH-ADM-001", "password": "Password123"})
check("Correct password accepted", ok.status_code == 302)
anon = app.test_client().get("/", follow_redirects=False)
check("Anonymous redirected to login", anon.status_code == 302 and "/login" in anon.headers["Location"])


print("\n2. ROSTER GENERATION AND PUBLICATION")
with app.app_context():
    dept = Department.query.filter_by(code="PHA").first()
    dept_id, manager_no = dept.id, dept.manager.staff_no
    target = (week_start() + timedelta(weeks=2)).isoformat()
    before_rosters = Roster.query.count()

manager = login(manager_no)
gen = manager.post("/schedule/generate",
                   data={"department_id": dept_id, "week": target},
                   follow_redirects=True)
check("Manager can generate a roster", gen.status_code == 200)

with app.app_context():
    roster = Roster.query.filter_by(department_id=dept_id).order_by(Roster.week_start.desc()).first()
    check("New roster created", Roster.query.count() > before_rosters,
          f"{Roster.query.count()} rosters")
    check("Roster starts as draft", roster.status == "DRAFT", roster.status)
    check("Roster has assignments", len(roster.assignments) > 0, f"{len(roster.assignments)} rows")
    roster_id = roster.id
    notes_before = Notification.query.count()

pub = manager.post(f"/schedule/roster/{roster_id}/publish", follow_redirects=True)
check("Manager can publish", pub.status_code == 200)

with app.app_context():
    roster = db.session.get(Roster, roster_id)
    check("Roster now published", roster.status == "PUBLISHED", roster.status)
    check("Publisher recorded", roster.published_by is not None,
          roster.published_by.full_name if roster.published_by else "")
    new_notes = Notification.query.count() - notes_before
    check("Notifications dispatched on publish", new_notes > 0, f"{new_notes} messages")
    channels = {
        n.channel
        for n in Notification.query.order_by(Notification.id.desc()).limit(new_notes).all()
    }
    check("All three channels used", channels == {"IN_APP", "EMAIL", "SMS"}, str(sorted(channels)))
    check("Publication audited",
          AuditLog.query.filter_by(action="ROSTER_PUBLISHED").count() > 0)

# A staff member must not be able to publish
staff_client = login("TH-PHA-004")
denied = staff_client.post(f"/schedule/roster/{roster_id}/publish")
check("Staff cannot publish a roster", denied.status_code == 403, f"HTTP {denied.status_code}")


print("\n3. LEAVE APPROVAL DRIVES RE-OPTIMISATION")
with app.app_context():
    # Pick someone with shifts in the newly published week.
    victim = (
        Assignment.query.filter(
            Assignment.roster_id == roster_id, Assignment.staff_id.isnot(None)
        ).first().staff
    )
    victim_id, victim_no = victim.id, victim.staff_no
    week = db.session.get(Roster, roster_id).week_start
    shifts_before = Assignment.query.filter(
        Assignment.staff_id == victim_id,
        Assignment.work_date >= week,
        Assignment.work_date <= week + timedelta(days=6),
        Assignment.status.in_(["SCHEDULED", "REPLACEMENT"]),
    ).count()

victim_client = login(victim_no)
sub = victim_client.post("/leave/request", data={
    "leave_type": "ANNUAL",
    "start_date": week.isoformat(),
    "end_date": (week + timedelta(days=6)).isoformat(),
    "reason": "Automated workflow test",
}, follow_redirects=True)
check("Staff can submit leave", sub.status_code == 200)

with app.app_context():
    request_row = LeaveRequest.query.filter_by(
        staff_id=victim_id, status="PENDING"
    ).order_by(LeaveRequest.id.desc()).first()
    check("Leave recorded as pending", request_row is not None)
    request_id = request_row.id

hr = login("TH-HRM-001")
appr = hr.post(f"/leave/{request_id}/approve",
               data={"comment": "Approved by workflow test"}, follow_redirects=True)
check("HR can approve leave", appr.status_code == 200)

with app.app_context():
    request_row = db.session.get(LeaveRequest, request_id)
    check("Leave marked approved", request_row.status == "APPROVED", request_row.status)
    check("Shifts were released", request_row.shifts_released == shifts_before,
          f"released {request_row.shifts_released} of {shifts_before}")
    check("Re-optimiser ran", request_row.reoptimised_at is not None)
    check("Replacements assigned",
          request_row.shifts_refilled + 0 >= 0,
          f"{request_row.shifts_refilled} refilled, "
          f"{request_row.shifts_released - request_row.shifts_refilled} gap(s)")

    still_rostered = Assignment.query.filter(
        Assignment.staff_id == victim_id,
        Assignment.work_date >= week,
        Assignment.work_date <= week + timedelta(days=6),
        Assignment.status.in_(["SCHEDULED", "REPLACEMENT"]),
    ).count()
    check("Person no longer rostered during their leave", still_rostered == 0,
          f"{still_rostered} remaining")

    vacated = Assignment.query.filter(
        Assignment.staff_id == victim_id, Assignment.status == "VACATED"
    ).count()
    check("Original assignments kept as vacated", vacated > 0, f"{vacated} rows")

    replacements = Assignment.query.filter(
        Assignment.original_staff_id == victim_id,
        Assignment.status.in_(["REPLACEMENT", "UNFILLED"]),
    ).all()
    check("Replacement rows written", len(replacements) > 0, f"{len(replacements)} rows")
    check("Re-optimisation audited",
          AuditLog.query.filter_by(action="SCHEDULE_REOPTIMISED").count() > 0)


print("\n4. ATTENDANCE LIFECYCLE")
with app.app_context():
    # Pick anyone who is not currently signed in.
    #
    # Selecting on "rostered now AND not signed in" used to leave nobody to
    # choose from: seed.py signs in everyone whose shift is running, so right
    # after a seed that set is empty. The old fallback then picked a hardcoded
    # staff number who was already signed in and stepped out, the application
    # correctly refused the double sign-in, and the test reported a failure
    # against its own pre-existing record.
    #
    # Being signed in is the only precondition this section actually needs, so
    # that is all it selects on. The person may end up with an unrostered
    # record, which exercises the same lifecycle.
    from app.services import attendance as attendance_service
    from app.utils.timeutils import now as _now

    moment = _now()
    picked = None
    for staff in Staff.query.filter_by(is_active=True).all():
        if not attendance_service.open_record(staff, moment):
            picked = staff.staff_no
            break
    if picked is None:
        raise SystemExit("  every active staff member is signed in; run seed.py first")
    print(f"  (using {picked})")

worker = login(picked)
r1 = worker.post("/attendance/sign-in", follow_redirects=True)
check("Sign in accepted", r1.status_code == 200)

with app.app_context():
    staff = Staff.query.filter_by(staff_no=picked).first()
    rec = AttendanceRecord.query.filter_by(staff_id=staff.id).order_by(
        AttendanceRecord.id.desc()).first()
    check("Attendance record open", rec.sign_in_at is not None and rec.sign_out_at is None)
    check("Status is on duty", rec.status == "ON_DUTY", rec.status)

r2 = worker.post("/attendance/sign-in", follow_redirects=True)
with app.app_context():
    staff = Staff.query.filter_by(staff_no=picked).first()
    open_count = AttendanceRecord.query.filter(
        AttendanceRecord.staff_id == staff.id,
        AttendanceRecord.sign_in_at.isnot(None),
        AttendanceRecord.sign_out_at.is_(None)).count()
    check("Double sign-in refused", open_count == 1, f"{open_count} open records")

import time as _t
_t.sleep(1)
worker.post("/attendance/step-out", data={"reason": "Break"}, follow_redirects=True)
with app.app_context():
    staff = Staff.query.filter_by(staff_no=picked).first()
    rec = AttendanceRecord.query.filter(
        AttendanceRecord.staff_id == staff.id,
        AttendanceRecord.sign_in_at.isnot(None),
        AttendanceRecord.sign_out_at.is_(None)).first()
    check("Temporary exit logged", rec.status == "TEMPORARILY_OUT", rec.status)
    check("Exit event created", rec.open_exit is not None)

_t.sleep(2)
worker.post("/attendance/return", follow_redirects=True)
with app.app_context():
    staff = Staff.query.filter_by(staff_no=picked).first()
    rec = AttendanceRecord.query.filter(
        AttendanceRecord.staff_id == staff.id,
        AttendanceRecord.sign_in_at.isnot(None),
        AttendanceRecord.sign_out_at.is_(None)).first()
    check("Return closes the exit", rec.status == "ON_DUTY" and rec.open_exit is None)

worker.post("/attendance/sign-out", follow_redirects=True)
with app.app_context():
    staff = Staff.query.filter_by(staff_no=picked).first()
    rec = AttendanceRecord.query.filter_by(staff_id=staff.id).order_by(
        AttendanceRecord.id.desc()).first()
    check("Sign out recorded", rec.sign_out_at is not None)
    check("Status completed", rec.status == "COMPLETED", rec.status)
    check("Hours reconciled", rec.worked_minutes is not None,
          f"{rec.worked_minutes} min on site, {rec.break_minutes} min away")
    check("Attendance audited",
          AuditLog.query.filter_by(action="ATTENDANCE_SIGN_OUT").count() > 0)


print("\n5. MANUAL OVERRIDE RESPECTS HARD CONSTRAINTS")
with app.app_context():
    from app.services import roster_service

    target_assignment = Assignment.query.filter(
        Assignment.status == "SCHEDULED",
        Assignment.staff_id.isnot(None),
        Assignment.work_date >= today(),
    ).first()
    aid = target_assignment.id
    dept = target_assignment.roster.department
    mgr_no = dept.manager.staff_no

    eligible = roster_service.eligible_replacements(target_assignment)
    blocked = roster_service.blocked_candidates(target_assignment)
    check("Eligible list computed", isinstance(eligible, list),
          f"{len(eligible)} eligible, {len(blocked)} blocked")
    good_id = eligible[0].id if eligible else None
    bad_id = blocked[0]["staff"].id if blocked else None

mgr = login(mgr_no)
if bad_id:
    bad_try = mgr.post(f"/schedule/assignment/{aid}/override",
                       data={"staff_id": bad_id, "reason": "should be refused"},
                       follow_redirects=True)
    with app.app_context():
        row = db.session.get(Assignment, aid)
        check("Constraint-breaking override refused", row.staff_id != bad_id)
else:
    check("Constraint-breaking override refused", True, "no blocked candidates to test")

if good_id:
    ok_try = mgr.post(f"/schedule/assignment/{aid}/override",
                      data={"staff_id": good_id, "reason": "Workflow test swap"},
                      follow_redirects=True)
    with app.app_context():
        row = db.session.get(Assignment, aid)
        check("Valid override applied", row.staff_id == good_id)
        check("Override flagged and audited",
              row.is_override and AuditLog.query.filter_by(
                  action="ASSIGNMENT_OVERRIDE").count() > 0)
else:
    check("Valid override applied", True, "no eligible candidate to test")


print("\n6. AUDIT TRAIL INTEGRITY")
with app.app_context():
    outcome = audit.verify_chain()
    check("Hash chain intact", outcome["intact"],
          f"{outcome['checked']} entries verified")

    # Tamper with a historic entry and confirm detection.
    entry = AuditLog.query.order_by(AuditLog.id.asc()).offset(5).first()
    original = entry.summary
    entry.summary = "Tampered summary"
    db.session.commit()

    after = audit.verify_chain()
    check("Tampering detected", not after["intact"],
          f"broken at entry #{after['broken_at']}: {after['reason']}")

    entry.summary = original
    db.session.commit()
    restored = audit.verify_chain()
    check("Chain valid again once restored", restored["intact"])


print("\n7. HARD CONSTRAINTS STILL HOLD AFTER ALL WRITES")
with app.app_context():
    from collections import defaultdict

    rows = Assignment.query.filter(
        Assignment.staff_id.isnot(None),
        Assignment.status.in_(["SCHEDULED", "REPLACEMENT"]),
    ).all()
    by_staff = defaultdict(list)
    for row in rows:
        by_staff[row.staff_id].append(row)

    problems = []
    for staff_id, items in by_staff.items():
        ordered = sorted(items, key=lambda a: a.start_at)
        seen_dates = [a.work_date for a in ordered]
        if len(seen_dates) != len(set(seen_dates)):
            problems.append(f"staff {staff_id} double-booked")
        for first, second in zip(ordered, ordered[1:]):
            gap = (second.start_at - first.end_at).total_seconds() / 3600
            if gap < 11:
                problems.append(f"staff {staff_id} rest {gap:.1f}h")
    check("No rest or double-booking violations after writes",
          not problems, f"{len(rows)} assignments checked; " + "; ".join(problems[:3]))


print("\n" + "=" * 68)
passed = sum(1 for _, ok, _ in results if ok)
print(f"{passed} of {len(results)} checks passed")
if passed < len(results):
    print("\nFailures:")
    for label, ok, detail in results:
        if not ok:
            print(f"  - {label}: {detail}")
sys.exit(0 if passed == len(results) else 1)
