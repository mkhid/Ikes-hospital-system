"""Weekly rosters and the individual shift assignments they contain."""
from app.constants import AssignmentStatus, RosterStatus
from app.extensions import db
from app.utils.timeutils import is_weekend, now


class Roster(db.Model):
    """A department's duty roster for one Monday-to-Sunday week."""

    __tablename__ = "rosters"
    __table_args__ = (
        db.UniqueConstraint("department_id", "week_start", name="uq_dept_week"),
    )

    id = db.Column(db.Integer, primary_key=True)
    department_id = db.Column(
        db.Integer, db.ForeignKey("departments.id"), nullable=False, index=True
    )
    week_start = db.Column(db.Date, nullable=False, index=True)
    week_end = db.Column(db.Date, nullable=False)
    status = db.Column(
        db.String(16), default=RosterStatus.DRAFT, nullable=False, index=True
    )

    generated_by_id = db.Column(db.Integer, db.ForeignKey("staff.id"))
    generated_at = db.Column(db.DateTime, default=now)
    published_by_id = db.Column(db.Integer, db.ForeignKey("staff.id"))
    published_at = db.Column(db.DateTime)

    # Engine telemetry, surfaced on the roster page and in the audit report.
    fairness_score = db.Column(db.Float)
    generation_seconds = db.Column(db.Float)
    optimiser_iterations = db.Column(db.Integer)
    notes = db.Column(db.Text)

    department = db.relationship("Department", back_populates="rosters")
    generated_by = db.relationship("Staff", foreign_keys=[generated_by_id])
    published_by = db.relationship("Staff", foreign_keys=[published_by_id])
    assignments = db.relationship(
        "Assignment",
        back_populates="roster",
        cascade="all, delete-orphan",
        order_by="Assignment.work_date",
    )

    # --- State ------------------------------------------------------------
    @property
    def is_published(self):
        return self.status == RosterStatus.PUBLISHED

    @property
    def is_draft(self):
        return self.status == RosterStatus.DRAFT

    @property
    def period_label(self):
        return (
            f"{self.week_start.strftime('%d %b')} - "
            f"{self.week_end.strftime('%d %b %Y')}"
        )

    # --- Aggregates -------------------------------------------------------
    def live_assignments(self):
        """Assignments that still represent real cover."""
        return [
            a
            for a in self.assignments
            if a.status in (AssignmentStatus.SCHEDULED, AssignmentStatus.REPLACEMENT)
        ]

    @property
    def coverage_gaps(self):
        return [a for a in self.assignments if a.status == AssignmentStatus.UNFILLED]

    @property
    def gap_count(self):
        return len(self.coverage_gaps)

    @property
    def replacement_count(self):
        return len(
            [a for a in self.assignments if a.status == AssignmentStatus.REPLACEMENT]
        )

    @property
    def total_hours(self):
        return round(
            sum(a.shift.duration_hours for a in self.live_assignments()), 1
        )

    @property
    def coverage_rate(self):
        """Percentage of required slots that are actually filled."""
        required = len(self.live_assignments()) + self.gap_count
        if not required:
            return 100.0
        return round(len(self.live_assignments()) / required * 100, 1)

    def assignments_for(self, work_date, shift_id=None):
        rows = [a for a in self.assignments if a.work_date == work_date]
        if shift_id is not None:
            rows = [a for a in rows if a.shift_id == shift_id]
        return rows

    def grid(self, dates, shifts):
        """Nested dict {date: {shift_id: [assignments]}} for template rendering."""
        table = {d: {s.id: [] for s in shifts} for d in dates}
        for assignment in self.assignments:
            if assignment.status == AssignmentStatus.CANCELLED:
                continue
            bucket = table.get(assignment.work_date)
            if bucket is not None and assignment.shift_id in bucket:
                bucket[assignment.shift_id].append(assignment)
        return table

    def __repr__(self):
        return f"<Roster dept={self.department_id} week={self.week_start}>"


class Assignment(db.Model):
    """One staff member on one shift on one day."""

    __tablename__ = "assignments"
    __table_args__ = (
        db.Index("ix_assignment_staff_date", "staff_id", "work_date"),
        db.Index("ix_assignment_date_shift", "work_date", "shift_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    roster_id = db.Column(
        db.Integer, db.ForeignKey("rosters.id"), nullable=False, index=True
    )
    # Null only for an UNFILLED coverage gap that the engine could not close.
    staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"), index=True)
    shift_id = db.Column(db.Integer, db.ForeignKey("shifts.id"), nullable=False)
    work_date = db.Column(db.Date, nullable=False, index=True)
    status = db.Column(
        db.String(16), default=AssignmentStatus.SCHEDULED, nullable=False, index=True
    )

    # Audit context for re-optimisation and manual overrides.
    original_staff_id = db.Column(db.Integer, db.ForeignKey("staff.id"))
    change_reason = db.Column(db.String(255))
    is_override = db.Column(db.Boolean, default=False, nullable=False)

    created_at = db.Column(db.DateTime, default=now, nullable=False)
    updated_at = db.Column(db.DateTime, default=now, onupdate=now, nullable=False)

    roster = db.relationship("Roster", back_populates="assignments")
    staff = db.relationship(
        "Staff", back_populates="assignments", foreign_keys=[staff_id]
    )
    original_staff = db.relationship("Staff", foreign_keys=[original_staff_id])
    shift = db.relationship("Shift")
    attendance = db.relationship(
        "AttendanceRecord", back_populates="assignment", uselist=False
    )

    @property
    def start_at(self):
        return self.shift.start_datetime(self.work_date)

    @property
    def end_at(self):
        return self.shift.end_datetime(self.work_date)

    @property
    def is_live(self):
        return self.status in (AssignmentStatus.SCHEDULED, AssignmentStatus.REPLACEMENT)

    @property
    def falls_on_weekend(self):
        return is_weekend(self.work_date)

    @property
    def is_night(self):
        return bool(self.shift and self.shift.crosses_midnight)

    def covers(self, moment):
        """Is this shift in progress at the given datetime?"""
        return self.start_at <= moment < self.end_at

    def __repr__(self):
        return f"<Assignment {self.work_date} shift={self.shift_id} staff={self.staff_id}>"
