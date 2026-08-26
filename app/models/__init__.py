"""Model package. Import from here so SQLAlchemy sees every mapper."""
from app.models.attendance import AttendanceEvent, AttendanceRecord
from app.models.audit import AuditLog
from app.models.department import Department, ShiftRequirement
from app.models.leave import LeaveRequest
from app.models.notification import Notification
from app.models.roster import Assignment, Roster
from app.models.shift import Shift
from app.models.staff import Staff

__all__ = [
    "Assignment",
    "AttendanceEvent",
    "AttendanceRecord",
    "AuditLog",
    "Department",
    "LeaveRequest",
    "Notification",
    "Roster",
    "Shift",
    "ShiftRequirement",
    "Staff",
]
