"""
Attendance capture and reconciliation.

Four actions make up the working day: sign in at the start of the shift, step
out whenever leaving the facility for a break, official duty or a personal
reason, sign back in on return, and sign out at the end. Time spent outside the
facility is deducted from hours worked, so the reconciliation figures reflect
time actually on site rather than the gap between the first and last stamp.

Every record is matched against the roster where one exists, which is what turns
raw stamps into lateness, early departure and overtime figures.
"""
from datetime import timedelta

from flask import current_app

from app.constants import AssignmentStatus, AttendanceStatus, AuditAction
from app.extensions import db
from app.models import Assignment, AttendanceEvent, AttendanceRecord
from app.services import audit
from app.utils.timeutils import now

# How early a staff member may sign in before their shift starts.
EARLY_SIGN_IN_MINUTES = 60
# How long after a shift ends the record still counts as that shift.
LATE_SIGN_OUT_HOURS = 6


class AttendanceError(Exception):
    """Raised when an action does not make sense for the current state."""


def open_record(staff, moment=None):
    """The staff member's record that is signed in and not signed out."""
    moment = moment or now()
    return (
        AttendanceRecord.query.filter(
            AttendanceRecord.staff_id == staff.id,
            AttendanceRecord.work_date >= moment.date() - timedelta(days=1),
            AttendanceRecord.sign_in_at.isnot(None),
            AttendanceRecord.sign_out_at.is_(None),
        )
        .order_by(AttendanceRecord.sign_in_at.desc())
        .first()
    )


def expected_assignment(staff, moment=None, exclude_attended=True):
    """
    The shift this sign-in most plausibly belongs to.

    Looks for one already in progress, then one starting within the early
    window, then one that ended recently, so a late sign-in still attaches to
    the right shift instead of creating an unrostered record.

    Assignments that already carry an attendance record are skipped by default.
    One record per rostered shift is a database constraint, so returning an
    already-attended shift here would make a second sign-in fail at the insert
    rather than being handled. This matters most after a night shift: it ends at
    07:00 but stays inside the late window until early afternoon, so someone who
    worked it, signed out, and then signed in again would otherwise collide with
    their own completed record.
    """
    moment = moment or now()
    candidates = (
        Assignment.query.filter(
            Assignment.staff_id == staff.id,
            Assignment.work_date >= moment.date() - timedelta(days=1),
            Assignment.work_date <= moment.date() + timedelta(days=1),
            Assignment.status.in_(
                [AssignmentStatus.SCHEDULED, AssignmentStatus.REPLACEMENT]
            ),
        )
        .order_by(Assignment.work_date)
        .all()
    )

    if exclude_attended:
        candidates = [a for a in candidates if a.attendance is None]

    for assignment in candidates:
        if assignment.covers(moment):
            return assignment

    early = timedelta(minutes=EARLY_SIGN_IN_MINUTES)
    upcoming = [a for a in candidates if 0 <= (a.start_at - moment).total_seconds() <= early.total_seconds()]
    if upcoming:
        return min(upcoming, key=lambda a: a.start_at)

    recent = [
        a
        for a in candidates
        if 0 <= (moment - a.end_at).total_seconds() <= LATE_SIGN_OUT_HOURS * 3600
    ]
    if recent:
        return max(recent, key=lambda a: a.end_at)

    return None


def sign_in(staff, moment=None, actor=None):
    """Record arrival at the facility."""
    moment = moment or now()
    if open_record(staff, moment) is not None:
        raise AttendanceError("You are already signed in. Sign out before signing in again.")

    assignment = expected_assignment(staff, moment)
    shift = assignment.shift if assignment else None

    late_minutes = 0
    scheduled_minutes = 0
    if assignment is not None:
        scheduled_minutes = shift.duration_minutes
        overdue = (moment - assignment.start_at).total_seconds() / 60
        late_minutes = max(0, int(overdue))
        note = None
    elif expected_assignment(staff, moment, exclude_attended=False) is not None:
        # A rostered shift was in range, but its attendance is already recorded.
        note = "Additional sign-in; the rostered shift is already reconciled"
    else:
        note = "Signed in without a rostered shift"

    record = AttendanceRecord(
        staff_id=staff.id,
        work_date=assignment.work_date if assignment else moment.date(),
        assignment_id=assignment.id if assignment else None,
        shift_id=shift.id if shift else None,
        sign_in_at=moment,
        status=AttendanceStatus.ON_DUTY,
        scheduled_minutes=scheduled_minutes,
        late_minutes=late_minutes,
        notes=note,
    )
    db.session.add(record)
    db.session.flush()

    audit.record(
        AuditAction.ATTENDANCE_SIGN_IN,
        f"{staff.full_name} signed in at {moment.strftime('%H:%M')}"
        + (f" ({late_minutes} min late)" if late_minutes else ""),
        actor=actor or staff,
        entity_type="AttendanceRecord",
        entity_id=record.id,
        department_id=staff.department_id,
        new={"sign_in_at": moment.isoformat(), "late_minutes": late_minutes},
    )
    db.session.commit()
    return record


def step_out(staff, reason=None, moment=None, actor=None):
    """Log a temporary exit from the facility."""
    moment = moment or now()
    record = open_record(staff, moment)
    if record is None:
        raise AttendanceError("You must be signed in before logging an exit.")
    if record.status == AttendanceStatus.TEMPORARILY_OUT:
        raise AttendanceError("You are already logged out of the facility.")

    event = AttendanceEvent(
        attendance_id=record.id,
        event_type="STEP_OUT",
        reason=(reason or "Not stated")[:120],
        occurred_at=moment,
    )
    record.status = AttendanceStatus.TEMPORARILY_OUT
    db.session.add(event)
    db.session.flush()

    audit.record(
        AuditAction.ATTENDANCE_STEP_OUT,
        f"{staff.full_name} left the facility at {moment.strftime('%H:%M')} "
        f"({event.reason})",
        actor=actor or staff,
        entity_type="AttendanceEvent",
        entity_id=event.id,
        department_id=staff.department_id,
        new={"occurred_at": moment.isoformat(), "reason": event.reason},
    )
    db.session.commit()
    return event


def return_to_facility(staff, moment=None, actor=None):
    """Close the open temporary exit."""
    moment = moment or now()
    record = open_record(staff, moment)
    if record is None:
        raise AttendanceError("You are not currently signed in.")

    event = record.open_exit
    if event is None:
        raise AttendanceError("There is no open exit to close.")

    event.returned_at = moment
    record.status = AttendanceStatus.ON_DUTY
    record.break_minutes = record.completed_break_minutes(moment)
    db.session.flush()

    audit.record(
        AuditAction.ATTENDANCE_RETURN,
        f"{staff.full_name} returned to the facility at {moment.strftime('%H:%M')} "
        f"after {event.duration_minutes} min",
        actor=actor or staff,
        entity_type="AttendanceEvent",
        entity_id=event.id,
        department_id=staff.department_id,
        new={"returned_at": moment.isoformat(), "minutes": event.duration_minutes},
    )
    db.session.commit()
    return event


def sign_out(staff, moment=None, actor=None):
    """Record departure and reconcile the record against the roster."""
    moment = moment or now()
    record = open_record(staff, moment)
    if record is None:
        raise AttendanceError("You are not currently signed in.")

    # An unclosed exit is closed automatically at sign-out.
    open_exit = record.open_exit
    if open_exit is not None:
        open_exit.returned_at = moment

    record.sign_out_at = moment
    record.status = AttendanceStatus.COMPLETED
    reconcile(record)
    db.session.flush()

    audit.record(
        AuditAction.ATTENDANCE_SIGN_OUT,
        f"{staff.full_name} signed out at {moment.strftime('%H:%M')} "
        f"after {record.worked_minutes} min on site",
        actor=actor or staff,
        entity_type="AttendanceRecord",
        entity_id=record.id,
        department_id=staff.department_id,
        new={
            "sign_out_at": moment.isoformat(),
            "worked_minutes": record.worked_minutes,
            "overtime_minutes": record.overtime_minutes,
        },
    )
    db.session.commit()
    return record


def reconcile(record):
    """Recompute worked, break, overtime and early-departure figures."""
    if not record.sign_in_at or not record.sign_out_at:
        return record

    threshold = current_app.config.get("OVERTIME_THRESHOLD_MINUTES", 30)

    gross = int((record.sign_out_at - record.sign_in_at).total_seconds() // 60)
    record.break_minutes = record.completed_break_minutes(record.sign_out_at)
    record.worked_minutes = max(0, gross - record.break_minutes)

    assignment = record.assignment
    if assignment is None:
        record.scheduled_minutes = 0
        record.overtime_minutes = 0
        record.early_departure_minutes = 0
        return record

    record.scheduled_minutes = assignment.shift.duration_minutes

    early = int((assignment.end_at - record.sign_out_at).total_seconds() // 60)
    record.early_departure_minutes = max(0, early)

    beyond = int((record.sign_out_at - assignment.end_at).total_seconds() // 60)
    record.overtime_minutes = beyond if beyond >= threshold else 0
    return record


def mark_absentees(work_date=None, actor=None):
    """
    Flag rostered staff who never signed in.

    Run for a completed day; a shift still in progress is left alone so someone
    arriving late is not permanently recorded as absent.
    """
    work_date = work_date or (now().date() - timedelta(days=1))
    moment = now()
    flagged = []

    assignments = Assignment.query.filter(
        Assignment.work_date == work_date,
        Assignment.status.in_(
            [AssignmentStatus.SCHEDULED, AssignmentStatus.REPLACEMENT]
        ),
        Assignment.staff_id.isnot(None),
    ).all()

    for assignment in assignments:
        if assignment.end_at > moment:
            continue  # Shift has not finished yet.
        if assignment.attendance is not None:
            continue

        record = AttendanceRecord(
            staff_id=assignment.staff_id,
            work_date=work_date,
            assignment_id=assignment.id,
            shift_id=assignment.shift_id,
            status=AttendanceStatus.ABSENT,
            scheduled_minutes=assignment.shift.duration_minutes,
            worked_minutes=0,
            notes="No sign-in recorded for a rostered shift",
        )
        db.session.add(record)
        flagged.append(assignment)

    if flagged:
        db.session.flush()
        audit.record(
            AuditAction.ATTENDANCE_SIGN_IN,
            f"{len(flagged)} staff flagged absent for {work_date}",
            actor=actor,
            entity_type="AttendanceRecord",
            new={"work_date": str(work_date), "count": len(flagged)},
        )
        db.session.commit()
    return flagged


def status_for(staff, moment=None):
    """Small dict driving the attendance widget on the staff dashboard."""
    moment = moment or now()
    record = open_record(staff, moment)
    assignment = expected_assignment(staff, moment)

    if record is None:
        return {
            "signed_in": False,
            "outside": False,
            "record": None,
            "assignment": assignment,
            "can_sign_in": True,
            "elapsed": 0,
        }

    return {
        "signed_in": True,
        "outside": record.status == AttendanceStatus.TEMPORARILY_OUT,
        "record": record,
        "assignment": record.assignment or assignment,
        "can_sign_in": False,
        "elapsed": record.elapsed_minutes(moment),
        "open_exit": record.open_exit,
    }
