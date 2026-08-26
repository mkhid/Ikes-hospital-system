"""Leave requests and unplanned absence reports."""
from app.constants import LeaveStatus, LeaveType
from app.extensions import db
from app.utils.timeutils import now


class LeaveRequest(db.Model):
    """
    A request for time off.

    Approving a request removes the staff member from every affected shift and
    triggers the re-optimiser, which fills the resulting gaps automatically.
    """

    __tablename__ = "leave_requests"
    __table_args__ = (
        db.Index("ix_leave_staff_range", "staff_id", "start_date", "end_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    staff_id = db.Column(
        db.Integer, db.ForeignKey("staff.id"), nullable=False, index=True
    )
    leave_type = db.Column(db.String(24), default=LeaveType.ANNUAL, nullable=False)
    start_date = db.Column(db.Date, nullable=False, index=True)
    end_date = db.Column(db.Date, nullable=False, index=True)
    reason = db.Column(db.Text)
    status = db.Column(
        db.String(16), default=LeaveStatus.PENDING, nullable=False, index=True
    )

    # An unplanned absence reported on the day rather than requested in advance.
    is_unplanned = db.Column(db.Boolean, default=False, nullable=False)

    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("staff.id"))
    reviewed_at = db.Column(db.DateTime)
    review_comment = db.Column(db.Text)

    # Set once the re-optimiser has processed this approval.
    reoptimised_at = db.Column(db.DateTime)
    shifts_released = db.Column(db.Integer, default=0)
    shifts_refilled = db.Column(db.Integer, default=0)

    created_at = db.Column(db.DateTime, default=now, nullable=False)

    staff = db.relationship(
        "Staff", back_populates="leave_requests", foreign_keys=[staff_id]
    )
    reviewed_by = db.relationship("Staff", foreign_keys=[reviewed_by_id])

    @property
    def days(self):
        return (self.end_date - self.start_date).days + 1

    @property
    def is_pending(self):
        return self.status == LeaveStatus.PENDING

    @property
    def is_approved(self):
        return self.status == LeaveStatus.APPROVED

    @property
    def type_label(self):
        return LeaveType(self.leave_type).value.replace("_", " ").title()

    @property
    def period_label(self):
        if self.start_date == self.end_date:
            return self.start_date.strftime("%d %b %Y")
        return (
            f"{self.start_date.strftime('%d %b')} - "
            f"{self.end_date.strftime('%d %b %Y')}"
        )

    def covers(self, work_date):
        return self.start_date <= work_date <= self.end_date

    def overlaps(self, start, end):
        return self.start_date <= end and start <= self.end_date

    def __repr__(self):
        return f"<LeaveRequest staff={self.staff_id} {self.start_date}..{self.end_date}>"
