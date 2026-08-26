"""Staff accounts: identity, RBAC role and scheduling attributes."""
from flask_login import UserMixin

from app.constants import Role
from app.extensions import bcrypt, db, login_manager
from app.utils.timeutils import now


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(Staff, int(user_id))


class Staff(UserMixin, db.Model):
    """A hospital employee with a portal login."""

    __tablename__ = "staff"

    id = db.Column(db.Integer, primary_key=True)
    staff_no = db.Column(db.String(24), unique=True, nullable=False, index=True)
    first_name = db.Column(db.String(60), nullable=False)
    last_name = db.Column(db.String(60), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(24))
    password_hash = db.Column(db.String(255), nullable=False)

    role = db.Column(db.String(16), default=Role.STAFF, nullable=False, index=True)
    job_title = db.Column(db.String(80))
    qualification = db.Column(db.String(120))
    department_id = db.Column(
        db.Integer, db.ForeignKey("departments.id"), nullable=False, index=True
    )

    employment_type = db.Column(db.String(24), default="FULL_TIME")
    date_joined = db.Column(db.Date)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    must_change_password = db.Column(db.Boolean, default=False, nullable=False)

    # Scheduling attributes ------------------------------------------------
    # A personal cap overriding the hospital-wide MAX_WEEKLY_HOURS, and an
    # optional shift preference treated as a soft constraint by the optimiser.
    max_weekly_hours = db.Column(db.Integer)
    preferred_shift = db.Column(db.String(16))
    can_work_nights = db.Column(db.Boolean, default=True, nullable=False)

    last_login_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=now, nullable=False)

    department = db.relationship(
        "Department", back_populates="members", foreign_keys=[department_id]
    )
    assignments = db.relationship(
        "Assignment",
        back_populates="staff",
        foreign_keys="Assignment.staff_id",
        cascade="all, delete-orphan",
    )
    leave_requests = db.relationship(
        "LeaveRequest",
        back_populates="staff",
        foreign_keys="LeaveRequest.staff_id",
        cascade="all, delete-orphan",
    )
    attendance_records = db.relationship(
        "AttendanceRecord",
        back_populates="staff",
        cascade="all, delete-orphan",
    )
    notifications = db.relationship(
        "Notification",
        back_populates="recipient",
        cascade="all, delete-orphan",
        order_by="Notification.created_at.desc()",
    )

    # --- Identity ---------------------------------------------------------
    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def initials(self):
        return f"{self.first_name[:1]}{self.last_name[:1]}".upper()

    @property
    def role_enum(self):
        return Role(self.role)

    @property
    def role_label(self):
        return self.role_enum.label

    # --- Authentication ---------------------------------------------------
    def set_password(self, raw_password):
        self.password_hash = bcrypt.generate_password_hash(raw_password).decode()

    def check_password(self, raw_password):
        return bcrypt.check_password_hash(self.password_hash, raw_password)

    # --- Authorisation ----------------------------------------------------
    @property
    def is_admin(self):
        return self.role == Role.ADMIN

    @property
    def is_hr(self):
        return self.role == Role.HR

    @property
    def is_manager(self):
        """True when this person is the designated manager of a department."""
        return self.department is not None and self.department.manager_id == self.id

    @property
    def has_oversight(self):
        """HR and Admin see every department; managers see only their own."""
        return self.role in (Role.ADMIN, Role.HR)

    def can_manage_department(self, department):
        """May this user generate, publish or override that department's roster?"""
        if department is None:
            return False
        if self.has_oversight:
            return True
        return department.manager_id == self.id

    def can_approve_leave_for(self, other):
        """HR and Admin approve anyone; a manager approves their own members."""
        if other is None or other.id == self.id:
            return False
        if self.has_oversight:
            return True
        return other.department_id == self.department_id and self.is_manager

    def managed_departments(self):
        """Departments whose rosters this user may act on."""
        from app.models.department import Department

        if self.has_oversight:
            return Department.query.filter_by(is_active=True).order_by(
                Department.name
            ).all()
        if self.is_manager:
            return [self.department]
        return []

    def __repr__(self):
        return f"<Staff {self.staff_no} {self.full_name}>"
