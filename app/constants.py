"""Enumerations and fixed domain vocabulary for the portal."""
try:  # Python 3.11 and later
    from enum import StrEnum
except ImportError:  # pragma: no cover - Python 3.9 and 3.10
    # StrEnum arrived in 3.11. This shim reproduces the behaviour the portal
    # relies on, so the system runs on any Python that Flask itself supports
    # (3.9+) rather than requiring the newest interpreter on a presenter's
    # machine. Overriding __str__ is the part that matters: audit actions and
    # status columns are written with str(), which on a plain (str, Enum)
    # would yield "Role.STAFF" instead of "STAFF".
    from enum import Enum

    class StrEnum(str, Enum):
        def __str__(self):
            return str(self.value)


class Role(StrEnum):
    """Portal-wide RBAC role. Distinct from a job title such as Staff Nurse."""

    ADMIN = "ADMIN"      # System administration, user accounts, policy
    HR = "HR"            # Staff records, leave approval, audit access
    MANAGER = "MANAGER"  # Generates and publishes the department roster
    STAFF = "STAFF"      # Views own profile, roster, leave and attendance

    @property
    def label(self):
        return {
            Role.ADMIN: "System Administrator",
            Role.HR: "HR Officer",
            Role.MANAGER: "Department Manager",
            Role.STAFF: "Staff",
        }[self]


class ShiftCode(StrEnum):
    MORNING = "MORNING"
    AFTERNOON = "AFTERNOON"
    NIGHT = "NIGHT"


class RosterStatus(StrEnum):
    DRAFT = "DRAFT"          # Generated, visible to the manager only
    PUBLISHED = "PUBLISHED"  # Visible to all staff, notifications dispatched
    ARCHIVED = "ARCHIVED"


class AssignmentStatus(StrEnum):
    SCHEDULED = "SCHEDULED"      # Normal assignment from the generator
    REPLACEMENT = "REPLACEMENT"  # Filled by the re-optimiser after a gap opened
    VACATED = "VACATED"          # Original holder withdrew (leave or absence)
    UNFILLED = "UNFILLED"        # Coverage gap the engine could not fill
    CANCELLED = "CANCELLED"


class LeaveType(StrEnum):
    ANNUAL = "ANNUAL"
    SICK = "SICK"
    EMERGENCY = "EMERGENCY"
    STUDY = "STUDY"
    MATERNITY = "MATERNITY"
    COMPASSIONATE = "COMPASSIONATE"


class LeaveStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class AttendanceStatus(StrEnum):
    ON_DUTY = "ON_DUTY"                  # Signed in and inside the facility
    TEMPORARILY_OUT = "TEMPORARILY_OUT"  # Signed out for a break or errand
    COMPLETED = "COMPLETED"              # Signed out at end of shift
    ABSENT = "ABSENT"                    # Rostered but never signed in


class PresenceState(StrEnum):
    """Derived real-time availability shown on the dashboard."""

    AVAILABLE = "AVAILABLE"              # On duty, inside the facility
    TEMPORARILY_OUT = "TEMPORARILY_OUT"  # On duty but stepped out
    ON_LEAVE = "ON_LEAVE"                # Approved leave covers today
    ABSENT = "ABSENT"                    # Rostered now, not signed in
    OFF_DUTY = "OFF_DUTY"                # Not rostered for the current shift

    @property
    def label(self):
        return {
            PresenceState.AVAILABLE: "Available",
            PresenceState.TEMPORARILY_OUT: "Temporarily out",
            PresenceState.ON_LEAVE: "On leave",
            PresenceState.ABSENT: "Absent",
            PresenceState.OFF_DUTY: "Off duty",
        }[self]

    @property
    def colour(self):
        """CSS modifier used by the dashboard status indicators."""
        return {
            PresenceState.AVAILABLE: "green",
            PresenceState.TEMPORARILY_OUT: "amber",
            PresenceState.ON_LEAVE: "blue",
            PresenceState.ABSENT: "red",
            PresenceState.OFF_DUTY: "grey",
        }[self]


class Channel(StrEnum):
    EMAIL = "EMAIL"
    SMS = "SMS"
    IN_APP = "IN_APP"


class DeliveryStatus(StrEnum):
    QUEUED = "QUEUED"
    SENT = "SENT"
    FAILED = "FAILED"
    SIMULATED = "SIMULATED"  # Composed and logged, no live gateway configured


class AuditAction(StrEnum):
    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    LOGIN_FAILED = "LOGIN_FAILED"
    PASSWORD_CHANGED = "PASSWORD_CHANGED"
    ROSTER_GENERATED = "ROSTER_GENERATED"
    ROSTER_PUBLISHED = "ROSTER_PUBLISHED"
    ROSTER_DELETED = "ROSTER_DELETED"
    ASSIGNMENT_OVERRIDE = "ASSIGNMENT_OVERRIDE"
    SCHEDULE_REOPTIMISED = "SCHEDULE_REOPTIMISED"
    COVERAGE_GAP = "COVERAGE_GAP"
    LEAVE_REQUESTED = "LEAVE_REQUESTED"
    LEAVE_APPROVED = "LEAVE_APPROVED"
    LEAVE_REJECTED = "LEAVE_REJECTED"
    LEAVE_CANCELLED = "LEAVE_CANCELLED"
    ATTENDANCE_SIGN_IN = "ATTENDANCE_SIGN_IN"
    ATTENDANCE_SIGN_OUT = "ATTENDANCE_SIGN_OUT"
    ATTENDANCE_STEP_OUT = "ATTENDANCE_STEP_OUT"
    ATTENDANCE_RETURN = "ATTENDANCE_RETURN"
    STAFF_CREATED = "STAFF_CREATED"
    STAFF_UPDATED = "STAFF_UPDATED"
    STAFF_DEACTIVATED = "STAFF_DEACTIVATED"
    NOTIFICATION_DISPATCHED = "NOTIFICATION_DISPATCHED"
    REPORT_EXPORTED = "REPORT_EXPORTED"
    POLICY_UPDATED = "POLICY_UPDATED"


# Shift definitions seeded into the database. Times follow the project brief:
# Morning 07:00-14:00, Afternoon 14:00-22:00, Night 22:00-07:00 (next day).
SHIFT_DEFINITIONS = [
    {
        "code": ShiftCode.MORNING,
        "name": "Morning Shift",
        "start_time": "07:00",
        "end_time": "14:00",
        "crosses_midnight": False,
        "sort_order": 1,
    },
    {
        "code": ShiftCode.AFTERNOON,
        "name": "Afternoon Shift",
        "start_time": "14:00",
        "end_time": "22:00",
        "crosses_midnight": False,
        "sort_order": 2,
    },
    {
        "code": ShiftCode.NIGHT,
        "name": "Night Shift",
        "start_time": "22:00",
        "end_time": "07:00",
        "crosses_midnight": True,
        "sort_order": 3,
    },
]
