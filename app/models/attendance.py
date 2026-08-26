"""Attendance records and the temporary-exit events attached to them."""
from app.constants import AttendanceStatus
from app.extensions import db
from app.utils.timeutils import now


class AttendanceRecord(db.Model):
    """
    One staff member's presence for one working day.

    Sign-in and sign-out bracket the shift. Between them the staff member may
    step out of the facility any number of times (break, official duty,
    personal reason); each exit and return is an AttendanceEvent, and the time
    spent outside is deducted from hours worked.
    """

    __tablename__ = "attendance_records"
    __table_args__ = (
        db.Index("ix_attendance_staff_date", "staff_id", "work_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    staff_id = db.Column(
        db.Integer, db.ForeignKey("staff.id"), nullable=False, index=True
    )
    work_date = db.Column(db.Date, nullable=False, index=True)

    # Null when someone signs in on a day they were not rostered.
    assignment_id = db.Column(
        db.Integer, db.ForeignKey("assignments.id"), unique=True, index=True
    )
    shift_id = db.Column(db.Integer, db.ForeignKey("shifts.id"))

    sign_in_at = db.Column(db.DateTime)
    sign_out_at = db.Column(db.DateTime)
    status = db.Column(
        db.String(24), default=AttendanceStatus.ON_DUTY, nullable=False, index=True
    )

    # Reconciliation figures, recalculated on sign-out.
    scheduled_minutes = db.Column(db.Integer, default=0)
    worked_minutes = db.Column(db.Integer, default=0)
    break_minutes = db.Column(db.Integer, default=0)
    late_minutes = db.Column(db.Integer, default=0)
    early_departure_minutes = db.Column(db.Integer, default=0)
    overtime_minutes = db.Column(db.Integer, default=0)

    notes = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=now, nullable=False)

    staff = db.relationship("Staff", back_populates="attendance_records")
    assignment = db.relationship("Assignment", back_populates="attendance")
    shift = db.relationship("Shift")
    events = db.relationship(
        "AttendanceEvent",
        back_populates="record",
        cascade="all, delete-orphan",
        order_by="AttendanceEvent.occurred_at",
    )

    @property
    def is_open(self):
        """Signed in and not yet signed out for the day."""
        return self.sign_in_at is not None and self.sign_out_at is None

    @property
    def is_outside_facility(self):
        return self.status == AttendanceStatus.TEMPORARILY_OUT

    @property
    def open_exit(self):
        """The step-out event that has not been closed by a return, if any."""
        for event in reversed(self.events):
            if event.event_type == "STEP_OUT" and event.returned_at is None:
                return event
        return None

    def elapsed_minutes(self, moment=None):
        """Minutes since sign-in, excluding completed time outside the facility."""
        if not self.sign_in_at:
            return 0
        end = self.sign_out_at or moment or now()
        gross = int((end - self.sign_in_at).total_seconds() // 60)
        return max(0, gross - self.completed_break_minutes(moment))

    def completed_break_minutes(self, moment=None):
        total = 0
        for event in self.events:
            if event.event_type != "STEP_OUT":
                continue
            end = event.returned_at or moment or now()
            total += max(0, int((end - event.occurred_at).total_seconds() // 60))
        return total

    def __repr__(self):
        return f"<AttendanceRecord staff={self.staff_id} {self.work_date}>"


class AttendanceEvent(db.Model):
    """
    A temporary exit from the facility during a shift.

    The brief requires staff to log out whenever they leave for official duties,
    breaks or personal reasons, and to log back in on return. Each pair is
    stored as a single row: occurred_at is the exit, returned_at the return.
    """

    __tablename__ = "attendance_events"

    id = db.Column(db.Integer, primary_key=True)
    attendance_id = db.Column(
        db.Integer,
        db.ForeignKey("attendance_records.id"),
        nullable=False,
        index=True,
    )
    event_type = db.Column(db.String(16), default="STEP_OUT", nullable=False)
    reason = db.Column(db.String(120))
    occurred_at = db.Column(db.DateTime, default=now, nullable=False)
    returned_at = db.Column(db.DateTime)

    record = db.relationship("AttendanceRecord", back_populates="events")

    @property
    def duration_minutes(self):
        if not self.returned_at:
            return None
        return max(0, int((self.returned_at - self.occurred_at).total_seconds() // 60))

    @property
    def is_open(self):
        return self.returned_at is None

    def __repr__(self):
        return f"<AttendanceEvent {self.event_type} {self.occurred_at}>"
