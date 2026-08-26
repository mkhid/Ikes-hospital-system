"""
Seed the portal with a working hospital.

Creates the six departments, their staff and login accounts, the three shift
definitions and each department's staffing requirements, then builds several
weeks of realistic operating history: generated and published rosters,
attendance with the usual lateness and absence, leave requests including one
approved mid-cycle so the re-optimiser visibly fills the gap it opens.

Run with:  flask --app run.py seed --reset
"""
import random
from datetime import date, datetime, time, timedelta

from app import create_app
from app.constants import (
    AttendanceStatus,
    AuditAction,
    LeaveStatus,
    Role,
    SHIFT_DEFINITIONS,
    ShiftCode,
)
from app.extensions import db
from app.models import (
    Assignment,
    AttendanceEvent,
    AttendanceRecord,
    Department,
    LeaveRequest,
    Roster,
    Shift,
    ShiftRequirement,
    Staff,
)
from app.services import audit, leave_service, roster_service
from app.services.scheduler import generate_roster
from app.utils.timeutils import parse_time, week_start

RNG = random.Random(20250921)

# ----------------------------------------------------------------------
# Reference data
# ----------------------------------------------------------------------
DEPARTMENTS = [
    {
        "code": "NUR",
        "name": "Nursing",
        "location": "Wards A-D, Emergency Unit",
        "description": "Ward and emergency nursing cover around the clock.",
        "operating_model": "ROUND_THE_CLOCK",
        # shift code -> (weekday minimum, weekend minimum)
        "requirements": {
            ShiftCode.MORNING: (2, 1),
            ShiftCode.AFTERNOON: (1, 1),
            ShiftCode.NIGHT: (1, 1),
        },
    },
    {
        "code": "DOC",
        "name": "Medical (Doctors)",
        "location": "Consulting Rooms, Emergency Unit",
        "description": "Medical officers covering outpatient, ward and emergency duty.",
        "operating_model": "ROUND_THE_CLOCK",
        "requirements": {
            ShiftCode.MORNING: (2, 1),
            ShiftCode.AFTERNOON: (1, 1),
            ShiftCode.NIGHT: (1, 1),
        },
    },
    {
        "code": "PHA",
        "name": "Pharmacy",
        "location": "Main Dispensary",
        "description": "Dispensing and clinical pharmacy during extended hours.",
        "operating_model": "EXTENDED_HOURS",
        "requirements": {
            ShiftCode.MORNING: (2, 1),
            ShiftCode.AFTERNOON: (1, 1),
            ShiftCode.NIGHT: (0, 0),
        },
    },
    {
        "code": "LAB",
        "name": "Laboratory",
        "location": "Diagnostics Block",
        "description": "Diagnostic testing and phlebotomy around the clock.",
        "operating_model": "ROUND_THE_CLOCK",
        "requirements": {
            ShiftCode.MORNING: (1, 1),
            ShiftCode.AFTERNOON: (1, 1),
            ShiftCode.NIGHT: (1, 1),
        },
    },
    {
        "code": "HRM",
        "name": "Human Resources",
        "location": "Administration Block, First Floor",
        "description": "Staff records, leave policy and compliance.",
        "operating_model": "OFFICE_HOURS",
        "requirements": {
            ShiftCode.MORNING: (1, 0),
            ShiftCode.AFTERNOON: (0, 0),
            ShiftCode.NIGHT: (0, 0),
        },
    },
    {
        "code": "ADM",
        "name": "Administration",
        "location": "Administration Block, Ground Floor",
        "description": "Hospital administration and portal system management.",
        "operating_model": "OFFICE_HOURS",
        "requirements": {
            ShiftCode.MORNING: (1, 0),
            ShiftCode.AFTERNOON: (0, 0),
            ShiftCode.NIGHT: (0, 0),
        },
    },
]

# (staff_no, first, last, role, job title, qualification, phone,
#  is_department_manager, can_work_nights, preferred_shift)
STAFF = {
    "NUR": [
        ("TH-NUR-001", "Akosua", "Mensah", Role.MANAGER, "Nurse Manager", "RGN, BSc Nursing", "0244101001", True, True, None),
        ("TH-NUR-002", "Kwabena", "Owusu", Role.STAFF, "Senior Staff Nurse", "RGN", "0244101002", False, True, ShiftCode.NIGHT),
        ("TH-NUR-003", "Efua", "Boateng", Role.STAFF, "Staff Nurse", "RGN", "0244101003", False, True, ShiftCode.MORNING),
        ("TH-NUR-004", "Yaw", "Adjei", Role.STAFF, "Staff Nurse", "RGN", "0244101004", False, True, None),
        ("TH-NUR-005", "Abena", "Darko", Role.STAFF, "Staff Nurse", "RGN", "0244101005", False, False, ShiftCode.MORNING),
        ("TH-NUR-006", "Kofi", "Asante", Role.STAFF, "Enrolled Nurse", "Enrolled Nurse Cert.", "0244101006", False, True, None),
    ],
    "DOC": [
        ("TH-DOC-001", "Nana", "Amponsah", Role.MANAGER, "Head of Medical Services", "MBChB, MGCPS", "0202202001", True, True, None),
        ("TH-DOC-002", "Kwame", "Sarpong", Role.STAFF, "Senior Medical Officer", "MBChB", "0202202002", False, True, None),
        ("TH-DOC-003", "Ama", "Ofori", Role.STAFF, "Medical Officer", "MBChB", "0202202003", False, True, ShiftCode.MORNING),
        ("TH-DOC-004", "Selorm", "Agbeko", Role.STAFF, "Medical Officer", "MBChB", "0202202004", False, True, ShiftCode.NIGHT),
        ("TH-DOC-005", "Adwoa", "Nyarko", Role.STAFF, "Medical Officer", "MBChB", "0202202005", False, True, None),
        ("TH-DOC-006", "Kojo", "Baffour", Role.STAFF, "House Officer", "MBChB", "0202202006", False, True, None),
    ],
    "PHA": [
        ("TH-PHA-001", "Gifty", "Ansah", Role.MANAGER, "Chief Pharmacist", "BPharm, MPSGH", "0554303001", True, False, None),
        ("TH-PHA-002", "Emmanuel", "Tetteh", Role.STAFF, "Pharmacist", "BPharm", "0554303002", False, False, ShiftCode.MORNING),
        ("TH-PHA-003", "Naa Adjeley", "Quaye", Role.STAFF, "Pharmacist", "PharmD", "0554303003", False, False, None),
        ("TH-PHA-004", "Isaac", "Nkrumah", Role.STAFF, "Pharmacy Technician", "Dip. Pharmacy", "0554303004", False, False, ShiftCode.AFTERNOON),
        ("TH-PHA-005", "Comfort", "Aidoo", Role.STAFF, "Pharmacy Technician", "Dip. Pharmacy", "0554303005", False, False, None),
        ("TH-PHA-006", "Daniel", "Appiah", Role.STAFF, "Dispensing Assistant", "Cert. Dispensing", "0554303006", False, False, None),
    ],
    "LAB": [
        ("TH-LAB-001", "Peter", "Danquah", Role.MANAGER, "Laboratory Manager", "MSc Med. Lab Science", "0244404001", True, True, None),
        ("TH-LAB-002", "Rita", "Amoah", Role.STAFF, "Senior Biomedical Scientist", "BSc Med. Lab Science", "0244404002", False, True, None),
        ("TH-LAB-003", "Samuel", "Osei", Role.STAFF, "Biomedical Scientist", "BSc Med. Lab Science", "0244404003", False, True, ShiftCode.NIGHT),
        ("TH-LAB-004", "Joyce", "Bediako", Role.STAFF, "Laboratory Technician", "Dip. Med. Lab Tech", "0244404004", False, True, None),
        ("TH-LAB-005", "Michael", "Antwi", Role.STAFF, "Laboratory Technician", "Dip. Med. Lab Tech", "0244404005", False, True, None),
        ("TH-LAB-006", "Vida", "Frimpong", Role.STAFF, "Phlebotomist", "Cert. Phlebotomy", "0244404006", False, False, ShiftCode.MORNING),
    ],
    "HRM": [
        ("TH-HRM-001", "Grace", "Otoo", Role.HR, "Human Resources Manager", "MSc HRM", "0204505001", True, False, ShiftCode.MORNING),
        ("TH-HRM-002", "Bernard", "Kyei", Role.HR, "Human Resources Officer", "BA HRM", "0204505002", False, False, ShiftCode.MORNING),
    ],
    "ADM": [
        ("TH-ADM-001", "Ibrahim", "Musah", Role.ADMIN, "System Administrator", "BSc Information Technology", "0554606001", True, False, ShiftCode.MORNING),
        ("TH-ADM-002", "Linda", "Acquah", Role.ADMIN, "Administrative Officer", "BSc Administration", "0554606002", False, False, ShiftCode.MORNING),
    ],
}

EXIT_REASONS = [
    "Break",
    "Official duty outside the facility",
    "Referral or patient transfer",
    "Personal reason",
    "Meeting off-site",
]


# ----------------------------------------------------------------------
# Builders
# ----------------------------------------------------------------------
def create_shifts():
    shifts = {}
    for definition in SHIFT_DEFINITIONS:
        shift = Shift(
            code=definition["code"],
            name=definition["name"],
            start_time=parse_time(definition["start_time"]),
            end_time=parse_time(definition["end_time"]),
            crosses_midnight=definition["crosses_midnight"],
            sort_order=definition["sort_order"],
        )
        db.session.add(shift)
        shifts[definition["code"]] = shift
    db.session.flush()
    print(f"  {len(shifts)} shift definitions")
    return shifts


def create_departments(shifts):
    departments = {}
    for spec in DEPARTMENTS:
        department = Department(
            code=spec["code"],
            name=spec["name"],
            description=spec["description"],
            location=spec["location"],
            operating_model=spec["operating_model"],
        )
        db.session.add(department)
        db.session.flush()

        for code, (weekday, weekend) in spec["requirements"].items():
            db.session.add(
                ShiftRequirement(
                    department_id=department.id,
                    shift_id=shifts[code].id,
                    min_staff=weekday,
                    weekend_min_staff=weekend,
                )
            )
        departments[spec["code"]] = department

    db.session.flush()
    print(f"  {len(departments)} departments with staffing requirements")
    return departments


def create_staff(departments, password):
    joined = date.today() - timedelta(days=RNG.randint(400, 1200))
    people = []

    for code, roster in STAFF.items():
        department = departments[code]
        for row in roster:
            (
                staff_no,
                first,
                last,
                role,
                job_title,
                qualification,
                phone,
                is_manager,
                nights,
                preferred,
            ) = row

            person = Staff(
                staff_no=staff_no,
                first_name=first,
                last_name=last,
                email=f"{first.split()[0].lower()}.{last.lower()}@taifahospital.gh",
                phone=phone,
                role=role,
                job_title=job_title,
                qualification=qualification,
                department_id=department.id,
                can_work_nights=nights,
                preferred_shift=preferred,
                date_joined=joined + timedelta(days=RNG.randint(0, 300)),
                is_active=True,
                must_change_password=False,
            )
            person.set_password(password)
            db.session.add(person)
            db.session.flush()

            if is_manager:
                department.manager_id = person.id
            people.append(person)

    db.session.flush()
    print(f"  {len(people)} staff accounts across {len(departments)} departments")
    return people


# ----------------------------------------------------------------------
# Operating history
# ----------------------------------------------------------------------
def build_rosters(departments, weeks_back):
    """Generate and publish rosters from the past through to next week."""
    current = week_start()
    built = []

    for offset in range(-weeks_back, 2):
        target = current + timedelta(weeks=offset)
        for department in departments.values():
            result = generate_roster(department, target, actor=department.manager)
            roster = result.roster

            # Everything up to and including this week is published; next week
            # is left as a draft so the manager has something to publish live.
            if offset <= 0:
                roster_service.publish(roster, department.manager)

            built.append((roster, result))

    published = len([r for r, _ in built if r.is_published])
    gaps = sum(result.gap_count for _, result in built)
    print(
        f"  {len(built)} rosters generated ({published} published), "
        f"{gaps} coverage gap(s)"
    )
    return built


def simulate_attendance(weeks_back):
    """
    Create attendance for every completed shift.

    Roughly four in five people arrive on time, one in six is a little late,
    a few slip out during the shift and about one in twenty-five never turns up.
    """
    horizon = date.today()
    start = week_start() - timedelta(weeks=weeks_back)

    assignments = (
        Assignment.query.filter(
            Assignment.work_date >= start,
            Assignment.work_date < horizon,
            Assignment.staff_id.isnot(None),
            Assignment.status.in_(["SCHEDULED", "REPLACEMENT"]),
        )
        .order_by(Assignment.work_date)
        .all()
    )

    created = absences = late_arrivals = exits = 0

    for assignment in assignments:
        if assignment.attendance is not None:
            continue

        roll = RNG.random()

        if roll < 0.04:
            db.session.add(
                AttendanceRecord(
                    staff_id=assignment.staff_id,
                    work_date=assignment.work_date,
                    assignment_id=assignment.id,
                    shift_id=assignment.shift_id,
                    status=AttendanceStatus.ABSENT,
                    scheduled_minutes=assignment.shift.duration_minutes,
                    worked_minutes=0,
                    notes="No sign-in recorded for a rostered shift",
                )
            )
            absences += 1
            created += 1
            continue

        start_at = assignment.start_at
        end_at = assignment.end_at

        if roll < 0.20:
            offset = RNG.randint(6, 38)      # late
            late_arrivals += 1
        elif roll < 0.30:
            offset = -RNG.randint(4, 22)     # early
        else:
            offset = RNG.randint(-3, 4)      # on time

        sign_in = start_at + timedelta(minutes=offset)
        late_minutes = max(0, int((sign_in - start_at).total_seconds() // 60))

        departure = RNG.random()
        if departure < 0.12:
            out_offset = -RNG.randint(8, 40)          # left early
        elif departure < 0.32:
            out_offset = RNG.randint(35, 95)          # overtime
        else:
            out_offset = RNG.randint(-4, 12)
        sign_out = end_at + timedelta(minutes=out_offset)

        record = AttendanceRecord(
            staff_id=assignment.staff_id,
            work_date=assignment.work_date,
            assignment_id=assignment.id,
            shift_id=assignment.shift_id,
            sign_in_at=sign_in,
            sign_out_at=sign_out,
            status=AttendanceStatus.COMPLETED,
            scheduled_minutes=assignment.shift.duration_minutes,
            late_minutes=late_minutes,
        )
        db.session.add(record)
        db.session.flush()

        # Temporary exits from the facility during the shift.
        if RNG.random() < 0.35:
            for _ in range(RNG.choice([1, 1, 1, 2])):
                out_at = sign_in + timedelta(minutes=RNG.randint(60, 300))
                if out_at >= sign_out:
                    continue
                back_at = out_at + timedelta(minutes=RNG.randint(12, 55))
                if back_at >= sign_out:
                    back_at = sign_out - timedelta(minutes=5)
                if back_at <= out_at:
                    continue
                db.session.add(
                    AttendanceEvent(
                        attendance_id=record.id,
                        event_type="STEP_OUT",
                        reason=RNG.choice(EXIT_REASONS),
                        occurred_at=out_at,
                        returned_at=back_at,
                    )
                )
                exits += 1
            db.session.flush()

        _reconcile_offline(record, assignment)
        created += 1

    db.session.commit()
    print(
        f"  {created} attendance records ({late_arrivals} late, {absences} absent, "
        f"{exits} temporary exits)"
    )


def _reconcile_offline(record, assignment):
    """Reconciliation without an app context dependency on config."""
    gross = int((record.sign_out_at - record.sign_in_at).total_seconds() // 60)
    record.break_minutes = record.completed_break_minutes(record.sign_out_at)
    record.worked_minutes = max(0, gross - record.break_minutes)

    early = int((assignment.end_at - record.sign_out_at).total_seconds() // 60)
    record.early_departure_minutes = max(0, early)

    beyond = int((record.sign_out_at - assignment.end_at).total_seconds() // 60)
    record.overtime_minutes = beyond if beyond >= 30 else 0


def build_leave(departments):
    """
    Create leave in three states.

    One approved request lands inside the current published week, so the
    re-optimiser runs for real and the roster shows replacement cover.
    """
    today = date.today()
    hr_manager = Staff.query.filter_by(staff_no="TH-HRM-001").first()

    # Historic, already taken.
    historic = Staff.query.filter_by(staff_no="TH-LAB-004").first()
    _approved_in_past(historic, today - timedelta(days=18), 3, "ANNUAL", hr_manager)

    # Pending, awaiting a decision from a manager or HR.
    pending = [
        ("TH-NUR-004", "ANNUAL", 9, 3, "Family commitment out of town."),
        ("TH-DOC-003", "STUDY", 14, 2, "Attending the paediatrics update course."),
        ("TH-PHA-005", "ANNUAL", 6, 2, "Personal travel."),
    ]
    for staff_no, leave_type, days_ahead, span, reason in pending:
        staff = Staff.query.filter_by(staff_no=staff_no).first()
        start = today + timedelta(days=days_ahead)
        db.session.add(
            LeaveRequest(
                staff_id=staff.id,
                leave_type=leave_type,
                start_date=start,
                end_date=start + timedelta(days=span - 1),
                reason=reason,
                status=LeaveStatus.PENDING,
                created_at=datetime.now() - timedelta(days=RNG.randint(1, 4)),
            )
        )
    db.session.commit()
    print(f"  {len(pending)} pending leave request(s)")

    # Live approval, so the engine re-optimises a published roster.
    subject = Staff.query.filter_by(staff_no="TH-NUR-003").first()
    start = max(today, week_start())
    request = leave_service.submit(
        subject,
        "SICK",
        start,
        start + timedelta(days=2),
        reason="Medical rest advised after a clinic visit.",
    )
    outcome = leave_service.approve(
        request, hr_manager, comment="Approved. Get well soon."
    )
    print(
        f"  Live approval for {subject.full_name}: {outcome.released_count} shift(s) "
        f"released, {outcome.refilled_count} refilled, {outcome.gap_count} gap(s)"
    )


def _approved_in_past(staff, start, span, leave_type, approver):
    """
    Approve a past-dated request through the real workflow.

    The request is written as pending with historic dates, which the submission
    form would reject, then put through leave_service.approve so the genuine
    re-optimiser releases and refills the affected shifts. Seeding the approved
    row directly would leave the person rostered during their own leave.
    """
    request = LeaveRequest(
        staff_id=staff.id,
        leave_type=leave_type,
        start_date=start,
        end_date=start + timedelta(days=span - 1),
        reason="Annual leave taken earlier in the cycle.",
        status=LeaveStatus.PENDING,
        created_at=datetime.combine(start - timedelta(days=9), time(8, 15)),
    )
    db.session.add(request)
    db.session.commit()

    outcome = leave_service.approve(
        request, approver, comment="Approved in line with the annual leave plan."
    )
    request.reviewed_at = datetime.combine(start - timedelta(days=5), time(10, 30))
    db.session.commit()
    return outcome


def open_a_shift():
    """
    Leave one staff member signed in right now.

    Gives the real-time dashboard something to show the moment the portal is
    opened, rather than an empty facility.
    """
    from app.services import attendance as attendance_service
    from app.utils.timeutils import now

    moment = now()
    for staff in Staff.query.filter_by(is_active=True).all():
        assignment = attendance_service.expected_assignment(staff, moment)
        # Only sign in people whose shift is genuinely running right now.
        # expected_assignment also matches shifts about to start or recently
        # finished, which would leave the dashboard showing staff on duty for
        # a shift that is already over.
        if assignment is None or not assignment.covers(moment):
            continue
        if attendance_service.open_record(staff, moment):
            continue
        try:
            record = attendance_service.sign_in(staff, moment=moment)
        except Exception:  # noqa: BLE001 - a seeded convenience, never fatal
            continue
        # One of them steps out, to exercise the temporary-exit state.
        if RNG.random() < 0.25:
            try:
                attendance_service.step_out(staff, RNG.choice(EXIT_REASONS), moment=moment)
            except Exception:  # noqa: BLE001
                pass
    print("  Current shift signed in for the live dashboard")


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------
def run_seed(reset=False, history_weeks=2):
    import logging

    from flask import current_app

    password = current_app.config["DEFAULT_PASSWORD"]

    # Seeding dispatches hundreds of notifications, and the console adapters log
    # every one at INFO. That is useful when debugging a single send, but here
    # it buries the progress messages under a wall of text. Quieten the logger
    # for the duration and restore it afterwards.
    logger = current_app.logger
    previous_level = logger.level
    logger.setLevel(logging.WARNING)
    try:
        _seed(reset, history_weeks, password)
    finally:
        logger.setLevel(previous_level)


def _seed(reset, history_weeks, password):

    if reset:
        print("Dropping and recreating all tables...")
        db.drop_all()
        db.create_all()
    else:
        db.create_all()
        if Staff.query.first() is not None:
            print("Database already contains staff. Use --reset to rebuild.")
            return

    print("Seeding reference data...")
    shifts = create_shifts()
    departments = create_departments(shifts)
    people = create_staff(departments, password)
    db.session.commit()

    admin = Staff.query.filter_by(staff_no="TH-ADM-001").first()
    audit.record(
        AuditAction.POLICY_UPDATED,
        f"Portal initialised with {len(departments)} departments and "
        f"{len(people)} staff accounts",
        actor=admin,
        entity_type="System",
        commit=True,
    )

    print(f"Building {history_weeks} week(s) of history plus the current and next week...")
    build_rosters(departments, history_weeks)

    # Leave is processed before attendance so the records that follow reflect
    # the roster as it actually stood once the re-optimiser had run, rather
    # than crediting attendance to shifts that were later reassigned.
    print("Creating leave activity...")
    build_leave(departments)

    print("Simulating attendance...")
    simulate_attendance(history_weeks)

    print("Opening the current shift...")
    open_a_shift()

    print("\nDone. Sign in with any of these:")
    print(f"  System Administrator   TH-ADM-001   {password}")
    print(f"  HR Officer             TH-HRM-001   {password}")
    print(f"  Nursing Manager        TH-NUR-001   {password}")
    print(f"  Doctor (staff)         TH-DOC-002   {password}")
    print(f"\n  Every seeded account uses the password: {password}")


if __name__ == "__main__":
    application = create_app()
    with application.app_context():
        run_seed(reset=True)
