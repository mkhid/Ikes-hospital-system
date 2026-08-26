"""Department and its per-shift staffing requirements."""
from app.constants import ShiftCode
from app.extensions import db


class Department(db.Model):
    """A hospital department that owns its own roster."""

    __tablename__ = "departments"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(16), unique=True, nullable=False, index=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.String(255))
    location = db.Column(db.String(80))

    # The staff member who generates and publishes this department's roster.
    # use_alter breaks the circular dependency with the staff table.
    manager_id = db.Column(
        db.Integer,
        db.ForeignKey("staff.id", use_alter=True, name="fk_department_manager"),
        nullable=True,
    )

    # Departments that never run a night shift (HR, Administration) are marked
    # office-hours; the generator simply skips shifts with no requirement row.
    operating_model = db.Column(db.String(24), default="ROUND_THE_CLOCK")
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    members = db.relationship(
        "Staff",
        back_populates="department",
        foreign_keys="Staff.department_id",
        cascade="all, delete-orphan",
    )
    manager = db.relationship("Staff", foreign_keys=[manager_id], post_update=True)
    requirements = db.relationship(
        "ShiftRequirement",
        back_populates="department",
        cascade="all, delete-orphan",
        order_by="ShiftRequirement.id",
    )
    rosters = db.relationship(
        "Roster", back_populates="department", cascade="all, delete-orphan"
    )

    def active_members(self):
        return [m for m in self.members if m.is_active]

    def requirement_for(self, shift, on_date=None):
        """Minimum staff this department needs on a shift, weekends included."""
        from app.utils.timeutils import is_weekend

        for requirement in self.requirements:
            if requirement.shift_id == shift.id:
                if on_date is not None and is_weekend(on_date):
                    return requirement.weekend_min_staff
                return requirement.min_staff
        return 0

    def runs_shift(self, shift):
        return any(r.shift_id == shift.id and r.min_staff > 0 for r in self.requirements)

    @property
    def headcount(self):
        return len(self.active_members())

    def __repr__(self):
        return f"<Department {self.code}>"


class ShiftRequirement(db.Model):
    """Minimum staffing level for one department on one shift type."""

    __tablename__ = "shift_requirements"
    __table_args__ = (
        db.UniqueConstraint("department_id", "shift_id", name="uq_dept_shift"),
    )

    id = db.Column(db.Integer, primary_key=True)
    department_id = db.Column(
        db.Integer, db.ForeignKey("departments.id"), nullable=False, index=True
    )
    shift_id = db.Column(db.Integer, db.ForeignKey("shifts.id"), nullable=False)
    min_staff = db.Column(db.Integer, default=1, nullable=False)
    weekend_min_staff = db.Column(db.Integer, default=1, nullable=False)

    department = db.relationship("Department", back_populates="requirements")
    shift = db.relationship("Shift")

    def __repr__(self):
        return f"<ShiftRequirement dept={self.department_id} shift={self.shift_id}>"
