"""Tamper-evident audit trail."""
import hashlib
import json

from app.extensions import db
from app.utils.timeutils import now


class AuditLog(db.Model):
    """
    An append-only record of every consequential action in the portal.

    Tamper evidence comes from hash chaining: each row stores the SHA-256 of
    its own content concatenated with the previous row's hash, so altering or
    deleting any historical entry breaks every hash that follows it. The
    integrity check on the audit report walks the chain and reports the first
    row where the recomputed digest stops matching.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        db.Index("ix_audit_action_time", "action", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("staff.id"), index=True)
    actor_name = db.Column(db.String(120))  # Retained if the account is removed
    actor_role = db.Column(db.String(16))

    action = db.Column(db.String(40), nullable=False, index=True)
    entity_type = db.Column(db.String(40))
    entity_id = db.Column(db.Integer)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"), index=True)

    summary = db.Column(db.String(255), nullable=False)
    old_value = db.Column(db.Text)  # JSON
    new_value = db.Column(db.Text)  # JSON
    ip_address = db.Column(db.String(45))

    created_at = db.Column(db.DateTime, default=now, nullable=False, index=True)

    previous_hash = db.Column(db.String(64))
    entry_hash = db.Column(db.String(64), index=True)

    actor = db.relationship("Staff", foreign_keys=[actor_id])
    department = db.relationship("Department", foreign_keys=[department_id])

    def payload(self):
        """The canonical content hashed for this entry."""
        return json.dumps(
            {
                "actor_id": self.actor_id,
                "actor_name": self.actor_name,
                "actor_role": self.actor_role,
                "action": self.action,
                "entity_type": self.entity_type,
                "entity_id": self.entity_id,
                "department_id": self.department_id,
                "summary": self.summary,
                "old_value": self.old_value,
                "new_value": self.new_value,
                "created_at": self.created_at.isoformat() if self.created_at else None,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def compute_hash(self):
        material = f"{self.previous_hash or ''}|{self.payload()}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @property
    def old_data(self):
        return json.loads(self.old_value) if self.old_value else None

    @property
    def new_data(self):
        return json.loads(self.new_value) if self.new_value else None

    def __repr__(self):
        return f"<AuditLog {self.action} by={self.actor_id} at={self.created_at}>"
