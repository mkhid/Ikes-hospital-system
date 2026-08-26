"""Outbound notification log across email, SMS and in-app channels."""
from app.constants import Channel, DeliveryStatus
from app.extensions import db
from app.utils.timeutils import now


class Notification(db.Model):
    """
    One message to one recipient on one channel.

    Every dispatch is recorded here whether it went out over a live gateway or
    was composed by the console adapter, which gives the delivery-rate figures
    shown on the dashboard and in the audit report.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        db.Index("ix_notification_recipient_status", "recipient_id", "status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    recipient_id = db.Column(
        db.Integer, db.ForeignKey("staff.id"), nullable=False, index=True
    )
    channel = db.Column(db.String(16), default=Channel.IN_APP, nullable=False)
    category = db.Column(db.String(40), default="GENERAL", nullable=False, index=True)

    subject = db.Column(db.String(160), nullable=False)
    body = db.Column(db.Text, nullable=False)
    # Address actually used: email address, phone number, or the portal itself.
    destination = db.Column(db.String(160))
    link = db.Column(db.String(255))

    status = db.Column(
        db.String(16), default=DeliveryStatus.QUEUED, nullable=False, index=True
    )
    error = db.Column(db.String(255))

    created_at = db.Column(db.DateTime, default=now, nullable=False, index=True)
    sent_at = db.Column(db.DateTime)
    read_at = db.Column(db.DateTime)

    recipient = db.relationship("Staff", back_populates="notifications")

    @property
    def is_unread(self):
        return self.read_at is None

    @property
    def delivered(self):
        return self.status in (DeliveryStatus.SENT, DeliveryStatus.SIMULATED)

    @property
    def channel_icon(self):
        return {
            Channel.EMAIL: "mail",
            Channel.SMS: "phone",
            Channel.IN_APP: "bell",
        }.get(Channel(self.channel), "bell")

    def __repr__(self):
        return f"<Notification {self.channel} to={self.recipient_id} {self.status}>"
