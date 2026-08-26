"""Render every GET route as each role and report status codes."""
import pathlib
import sys

# Resolve the project root relative to this file, so the script runs
# from any machine and any working directory.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import create_app
from app.models import Assignment, Department, LeaveRequest, Notification, Roster, Staff

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False

ROLES = {
    "ADMIN   ": "TH-ADM-001",
    "HR      ": "TH-HRM-001",
    "MANAGER ": "TH-NUR-001",
    "STAFF   ": "TH-DOC-002",
}

with app.app_context():
    dept = Department.query.filter_by(code="NUR").first()
    roster = Roster.query.filter_by(department_id=dept.id).first()
    assignment = Assignment.query.filter(Assignment.staff_id.isnot(None)).first()
    note = Notification.query.filter_by(channel="IN_APP").first()
    week = roster.week_start.isoformat()

    routes = [
        ("/", "dashboard"),
        ("/schedule/me", "my schedule"),
        (f"/schedule/me?week={week}", "my schedule (week)"),
        (f"/schedule/department/{dept.id}", "department roster"),
        (f"/schedule/department/{dept.id}?week={week}", "department roster (week)"),
        (f"/schedule/roster/{roster.id}", "roster redirect"),
        (f"/schedule/roster/{roster.id}/export.csv", "roster CSV"),
        ("/schedule/generate", "generate form"),
        (f"/schedule/assignment/{assignment.id}/override", "override form"),
        ("/leave/", "my leave"),
        ("/leave/pending", "leave approvals"),
        ("/leave/absence", "report absence"),
        ("/attendance/", "attendance terminal"),
        ("/attendance/register", "attendance register"),
        ("/attendance/register/export.csv", "attendance CSV"),
        ("/attendance/register/export.pdf", "attendance PDF"),
        ("/profile/", "my profile"),
        ("/profile/2", "other profile"),
        ("/profile/directory", "directory"),
        ("/reports/", "analytics"),
        ("/reports/audit-report.pdf", "audit report PDF"),
        ("/reports/audit-report.csv", "audit report CSV"),
        ("/reports/audit-trail", "audit trail"),
        ("/reports/audit-trail/export.pdf", "audit trail PDF"),
        ("/reports/audit-trail/export.csv", "audit trail CSV"),
        ("/notifications/", "inbox"),
        ("/notifications/outbox", "outbox"),
        ("/admin/", "admin home"),
        ("/admin/staff", "staff accounts"),
        ("/admin/staff/new", "new staff form"),
        ("/admin/staff/3/edit", "edit staff form"),
        (f"/admin/departments/{dept.id}", "department settings"),
        ("/dashboard/api/presence", "presence API"),
        ("/change-password", "change password"),
    ]

failures = []
print(f"{'ROUTE':<48} " + "  ".join(ROLES.keys()))
print("=" * 106)

for path, label in routes:
    row = []
    for role, staff_no in ROLES.items():
        client = app.test_client()
        client.post("/login", data={"identifier": staff_no, "password": "Password123"})
        try:
            response = client.get(path)
            code = response.status_code
            size = len(response.data)
        except Exception as exc:  # noqa: BLE001
            failures.append((path, role, repr(exc)[:180]))
            row.append("ERR")
            continue

        if code == 200:
            row.append(f"200")
        elif code in (302, 403, 404):
            row.append(str(code))
        else:
            failures.append((path, role, f"HTTP {code}"))
            row.append(f"*{code}*")

        if code == 200 and size < 400 and not path.endswith((".csv", ".pdf")):
            failures.append((path, role, f"suspiciously small body: {size} bytes"))

    print(f"{path:<48} " + "  ".join(f"{v:<8}" for v in row))

print()
if failures:
    print(f"{len(failures)} PROBLEM(S):")
    for path, role, detail in failures:
        print(f"  {role} {path}\n      {detail}")
else:
    print("All routes responded as expected (200 = ok, 403 = correctly denied by RBAC).")
sys.exit(1 if failures else 0)
