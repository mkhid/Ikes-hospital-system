# Intelligent Hospital Workforce Scheduling System

A web portal that automates duty rostering for doctors, nurses and support
staff, with integrated leave, attendance, real-time availability, multi-channel
notifications and a tamper-evident audit trail.

Built for the BSc Information Technology thesis
*Intelligent Hospital Workforce Scheduling System*, Advanced School of Systems
and Data Studies, with Taifa Hospital as the case study.

> **All data in this repository is fabricated.** The 28 staff members, their
> names, contact details, rosters, attendance records, lateness and absences are
> invented by [`seed.py`](seed.py) to demonstrate the system. They do not
> describe any real person, and no real hospital records were used. Every
> account shares a published demonstration password, so a deployment of this
> repository must never hold real staff data without changing them first.

---

## Getting started

**Handing this to someone else to run or present?** See
[HANDOVER.md](HANDOVER.md) — it needs no programming knowledge and comes down
to installing Python and double-clicking `start.bat`.

**Hosting it on a server so people reach it by URL?** See
[DEPLOYMENT.md](DEPLOYMENT.md) — including three failure modes that are easy
to hit and hard to diagnose.

For development:

```bash
# 1. Install the dependencies
pip install -r requirements.txt

# 2. Create your configuration (optional; sensible defaults apply)
cp .env.example .env

# 3. Build the database and fill it with a working hospital
python seed.py

# 4. Run the portal
python run.py
```

Open <http://127.0.0.1:5000>.

Requires **Python 3.9 or newer** (3.11+ recommended). Everything else installs
from `requirements.txt`; Bootstrap and Chart.js are already vendored, so the
portal makes no external requests at runtime.

### Demonstration accounts

Every seeded account uses the password `Password123`.

| Role | Staff number | What they can do |
|---|---|---|
| System Administrator | `TH-ADM-001` | Everything, plus accounts and policy |
| HR Officer | `TH-HRM-001` | All departments, leave approval, audit trail |
| Nursing Manager | `TH-NUR-001` | Generate and publish the Nursing roster |
| Doctor (staff) | `TH-DOC-002` | Own profile, schedule, leave and attendance |

Any of the 28 seeded staff numbers work: `TH-NUR-001`…`006`,
`TH-DOC-001`…`006`, `TH-PHA-001`…`006`, `TH-LAB-001`…`006`,
`TH-HRM-001`…`002`, `TH-ADM-001`…`002`.

---

## The hospital as seeded

Six departments, 28 staff, one manager each.

| Department | Code | Staff | Runs | Minimum per shift (weekday / weekend) |
|---|---|---|---|---|
| Nursing | NUR | 6 | 24/7 | Morning 2/1, Afternoon 1/1, Night 1/1 |
| Medical (Doctors) | DOC | 6 | 24/7 | Morning 2/1, Afternoon 1/1, Night 1/1 |
| Pharmacy | PHA | 6 | Extended hours | Morning 2/1, Afternoon 1/1 |
| Laboratory | LAB | 6 | 24/7 | Morning 1/1, Afternoon 1/1, Night 1/1 |
| Human Resources | HRM | 2 | Office hours | Morning 1/0 |
| Administration | ADM | 2 | Office hours | Morning 1/0 |

Shift windows follow the project brief:

| Shift | Window | Duration |
|---|---|---|
| Morning | 07:00 – 14:00 | 7 hours |
| Afternoon | 14:00 – 22:00 | 8 hours |
| Night | 22:00 – 07:00 (next day) | 9 hours |

---

## How the scheduling engine works

A hybrid two-phase algorithm, matching the design in Chapter 4 of the thesis.

### Phase 1 — feasible construction

The week is walked in calendar order, night shifts first, because each decision
constrains the next. A staff member is only ever offered a slot if they pass
**every** hard constraint, so a produced roster is legal by construction rather
than repaired afterwards.

| Hard constraint | Default |
|---|---|
| One shift per person per day | always |
| Minimum rest between shifts | 11 hours |
| Maximum weekly hours | 48 |
| Maximum consecutive working days | 6 |
| Maximum consecutive night shifts | 3 |
| Minimum days off per week | 1 |
| Approved leave | never rostered |
| Night-duty clearance | per staff member |
| Departmental minimum staffing | per shift, per department |

The brief's rule that *a night worker must not take the following morning or
afternoon shift* falls out of the rest period rather than being special-cased.
The night shift ends at 07:00, so a 07:00 morning start leaves zero hours' rest
and a 14:00 afternoon start leaves seven — both below the eleven-hour minimum. A
following night shift starts at 22:00, leaving fifteen hours, which is what makes
night rotations possible at all.

### Phase 2 — heuristic optimisation

The feasible roster is then improved by hill climbing over three neighbourhood
moves: **fill a gap**, **reassign a slot**, and **swap two slots**. Every
candidate move is re-checked by the same constraint checker, so feasibility is
an invariant of the entire search, never a post-hoc validation.

The objective minimises the variance of night shifts, weekend shifts and total
hours across the department, plus a small penalty for unmet shift preferences.
Fairness is **cumulative, not weekly**: the counts a staff member carried over
the previous four weeks are folded into the tally before the current week is
scored, so someone who worked three nights last week starts this week at the
back of the queue.

Where no legal assignment exists, the slot is recorded as a **coverage gap** and
escalated to the manager and HR, rather than filled by breaking a rule. A gap is
a decision for a human, not a failure of the engine.

### Measured performance

| Metric | Result |
|---|---|
| One department (6 staff, 26 shifts) | ~0.25 s |
| Whole hospital (28 staff, 102 shifts) | ~1.5 s |
| Projected for 100 staff | ~5 s |
| Thesis requirement | 100 staff in under 5 minutes |
| Fairness cost reduction in phase 2 | 26–62 % |

---

## Feature map

Each item of the project brief, and where it lives.

| Requirement | Implementation |
|---|---|
| Six departments, 6 staff each (HR and Admin 2) | [seed.py](seed.py) |
| Login account and personal profile for every staff member | [auth.py](app/blueprints/auth.py), [profile.py](app/blueprints/profile.py) |
| One manager per department generates and publishes | [schedule.py](app/blueprints/schedule.py), `Department.manager_id` |
| Automated roster on three-shift logic | [scheduler.py](app/services/scheduler.py) |
| Hard constraints, night-then-day rule | [constraints.py](app/services/constraints.py) |
| Auto re-optimisation when someone is off or on leave | [reoptimizer.py](app/services/reoptimizer.py) |
| Email and SMS on publication | [notifications.py](app/services/notifications.py) |
| Attendance sign-in and sign-out | [attendance.py](app/services/attendance.py) |
| Temporary exit and return logging | `AttendanceEvent` in [attendance.py](app/models/attendance.py) |
| Real-time dashboard, staff on duty, doctors on duty | [availability.py](app/services/availability.py) |
| Audit report for management decision-making | [reports.py](app/services/reports.py) |
| Tamper-evident audit trail | [audit.py](app/services/audit.py) |
| Workforce analytics | [analytics.py](app/services/analytics.py) |

---

## Project structure

```
Ikes_Project/
├── run.py                  Entry point
├── config.py               Configuration and policy limits
├── seed.py                 Builds the demonstration hospital
├── app/
│   ├── __init__.py         Application factory, CLI commands
│   ├── constants.py        Enumerations and shift definitions
│   ├── extensions.py       Extension singletons
│   ├── models/             Presentation-independent data layer
│   │   ├── department.py   Department, ShiftRequirement
│   │   ├── staff.py        Staff accounts and RBAC helpers
│   │   ├── shift.py        Shift windows
│   │   ├── roster.py       Roster, Assignment
│   │   ├── leave.py        LeaveRequest
│   │   ├── attendance.py   AttendanceRecord, AttendanceEvent
│   │   ├── notification.py Notification delivery log
│   │   └── audit.py        Hash-chained AuditLog
│   ├── services/           Application layer, all business logic
│   │   ├── constraints.py  Phase 1 hard-constraint checker
│   │   ├── fairness.py     Phase 2 objective function
│   │   ├── scheduler.py    The generator
│   │   ├── reoptimizer.py  Gap release and automatic refill
│   │   ├── roster_service.py  Publish, withdraw, override
│   │   ├── leave_service.py   Leave workflow
│   │   ├── attendance.py   Sign-in, exits, reconciliation
│   │   ├── availability.py Real-time presence derivation
│   │   ├── analytics.py    KPI computation
│   │   ├── notifications.py  Pluggable email/SMS/in-app adapters
│   │   ├── audit.py        Audit trail and integrity verification
│   │   └── reports.py      PDF and CSV generation
│   ├── blueprints/         Presentation layer, HTTP routes
│   ├── templates/          Jinja2 views, built on Bootstrap 5
│   └── static/
│       ├── vendor/         Bootstrap 5.3.3 and Chart.js 4.4.4, served locally
│       ├── css/theme.css   Hospital palette and the domain components
│       └── js/app.js       Chart setup, live polling, form helpers
└── instance/hospital.db    SQLite database (created by seed.py)
```

This is the three-tier architecture described in Chapter 3: `templates` and
`static` are the presentation tier, `services` the application tier, and
`models` the data tier. Blueprints are deliberately thin — they parse the
request, call a service, and render. No business rule lives in a route.

---

## Front end

**Bootstrap 5.3.3** for layout and components, **Chart.js 4.4.4** for the
analytics charts, and a single `theme.css` on top that does three things only:
retunes Bootstrap's colour tokens to the hospital palette, styles the sidebar
shell (Bootstrap has no sidebar component), and adds the domain pieces Bootstrap
cannot express — the weekly roster grid, the presence board, the KPI tiles and
the activity timeline.

Both libraries are **vendored into `app/static/vendor/`**, not loaded from a
CDN. Two consequences that matter for a hospital deployment:

- The portal works with **no internet connection**. Nothing on any page reaches
  outside the server.
- There is **no build step**. No npm, no bundler, no `node_modules`. The run
  procedure is still `python run.py`.

Charts are declared, not scripted. Each canvas carries its configuration in a
`data-chart` attribute and `app.js` renders whatever it finds, so no chart data
is inlined as JavaScript:

```html
<canvas id="coverageChart" data-chart='{"kind":"coverage","labels":[...],"filled":[...],"gaps":[...]}'></canvas>
```

Six chart types are defined in [app.js](app/static/js/app.js): `coverage`,
`shiftMix`, `workload`, `hours`, `presence` and `delivery`. The presence
doughnut on the operations dashboard is updated in place by the 30-second poll
rather than being re-created.

---

## Notifications

Delivery is handled by pluggable adapters selected at runtime. By default both
email and SMS run through **console adapters**: every message is composed in
full and written to the notification log, so the **Message outbox** page
(HR and Admin) shows exactly what would have gone out, with its destination and
delivery status, without incurring gateway charges.

To go live, set credentials in `.env` and switch the backend. No application
code changes.

```ini
# Real email
EMAIL_BACKEND=smtp
MAIL_USERNAME=you@gmail.com
MAIL_PASSWORD=your-16-character-app-password

# Real SMS (Hubtel, Arkesel, or any JSON gateway)
SMS_BACKEND=http
SMS_API_URL=https://sms.arkesel.com/api/v2/sms/send
SMS_API_KEY=your-key
SMS_SENDER_ID=TAIFA
```

Messages are sent on roster publication, replacement assignment, leave
submission, leave decision, shift removal and coverage-gap escalation.

---

## Audit trail

Every consequential action is appended to `audit_logs`: sign-ins, roster
generation and publication, manual overrides, re-optimisation, leave decisions,
attendance events, account changes and report exports.

Tamper evidence comes from **hash chaining**. Each row stores the SHA-256 digest
of its own content combined with the digest of the row before it, so editing or
deleting any historical entry invalidates every digest that follows. The
integrity check walks the chain and reports the first row whose digest no longer
matches.

```bash
flask --app run.py verify-audit
```

The check also runs automatically at the top of the audit trail page and in the
management audit report.

---

## Command line

```bash
flask --app run.py seed --reset          # Rebuild the demonstration data
flask --app run.py seed --weeks 4        # More history for richer analytics
flask --app run.py mark-absentees        # Flag no-shows for yesterday
flask --app run.py verify-audit          # Check the audit hash chain
```

---

## Suggested demonstration order

1. **Sign in as `TH-ADM-001`** — the operations dashboard shows live presence,
   who is on duty, doctors on duty, and departmental coverage. It refreshes
   itself every 30 seconds.
2. **Schedule → Generate roster** — pick Nursing and a future week. The roster
   appears as a draft in well under a second, with the fairness breakdown and
   engine telemetry beneath it.
3. **Publish it** — a flash message confirms how many notifications went out.
   Check **Message outbox** to read the actual emails and SMS texts.
4. **Sign in as `TH-NUR-003`** — the staff view: own week, own shifts, own
   attendance terminal.
5. **Request leave** covering a published shift.
6. **Back as `TH-HRM-001` → Leave approvals** — approve it. The flash message
   reports how many shifts were released and refilled automatically. Open the
   Nursing roster: the replacement is marked in amber.
7. **Attendance** — sign in, log an exit, log back in, sign out. Watch the
   dashboard status change and the reconciled hours appear on the register.
8. **Reports → Analytics → Audit report (PDF)** — the management deliverable.
9. **Reports → Audit trail** — every one of the above actions, hash-verified.

---

## Configuration

All policy lives in `.env` so it is version-controlled and applied
consistently. Defaults are in [config.py](config.py).

Two names are kept deliberately separate. `SYSTEM_NAME` is the software itself
and stays constant wherever it is deployed; `HOSPITAL_NAME` is the institution
running this particular instance. Both appear in the interface: the sidebar
shows the hospital above the system name, and every PDF report carries both in
its footer.

| Setting | Default | Effect |
|---|---|---|
| `SYSTEM_NAME` | Intelligent Hospital Workforce Scheduling System | The software's name: browser title, sidebar, sign-in page, PDF footers |
| `SYSTEM_ABBR` | IHWSS | Short form for tight spaces |
| `HOSPITAL_NAME` | Taifa Hospital | The institution running this instance |
| `MIN_REST_HOURS` | 11 | Rest between shifts; enforces the night-then-day rule |
| `MAX_WEEKLY_HOURS` | 48 | Weekly cap per staff member |
| `MAX_CONSECUTIVE_DAYS` | 6 | Longest run of working days |
| `MAX_CONSECUTIVE_NIGHTS` | 3 | Longest run of night shifts |
| `MIN_DAYS_OFF_PER_WEEK` | 1 | Guaranteed rest days |
| `OPTIMISER_ITERATIONS` | 4000 | Phase 2 search effort |
| `OPTIMISER_SEED` | unset | Set an integer for reproducible demonstrations |
| `ATTENDANCE_GRACE_MINUTES` | 15 | Before a no-show reads as absent |
| `OVERTIME_THRESHOLD_MINUTES` | 30 | Before extra time counts as overtime |

Changing a limit affects rosters generated afterwards; existing rosters are
untouched until regenerated.

### Moving to MySQL

One line in `.env`:

```ini
DATABASE_URL=mysql+pymysql://root:@localhost/hospital_db
```

Create the empty schema first, then run `python seed.py`.
