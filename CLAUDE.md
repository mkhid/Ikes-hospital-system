# Project context

Written for whoever picks this up next, human or agent. It covers what the
system is, what has been built and proven, the decisions that were made and
why, and what is still open.

Claude Code loads this file automatically.

---

## 1. What this is

The **Intelligent Hospital Workforce Scheduling System** — a web portal that
automates duty rostering for doctors, nurses and support staff, with integrated
leave, attendance, real-time availability, multi-channel notifications and a
tamper-evident audit trail.

It is the practical component of a BSc Information Technology thesis
(Advanced School of Systems and Data Studies, supervised by Mr. Edward S.
Younge, September 2025). Case study: **Taifa Hospital**, Ghana. The thesis
documents are in the project folder but excluded from version control.

Two source documents defined the scope:

- `FINAL PROJECT WORK.docx` — an 8-point feature brief. **This is authoritative.**
- `Ikes_Eyram_Kunakey_ADS23A00031Y.docx` — the full five-chapter thesis,
  providing architecture, algorithm design and justification.

Where they disagree, the brief wins (see §7, shift hours).

---

## 2. Scope, as specified by the user

Verbatim requirements, all implemented:

1. Web portal for a hospital.
2. Six departments: HR, Nurse, Doctor, Pharmacy, Lab, Admin.
3. Six staff per department **except** HR (2) and Admin (2) — 28 total.
4. Every staff member has a login, a personal profile, and can see schedules
   published by their manager or HR.
5. **The primary feature is automated roster generation.** One staff member per
   department is the manager; they generate and publish, and all members see it.
6. Three shifts: morning 07:00–14:00, afternoon 14:00–22:00, night 22:00–07:00.
7. Hard constraints enforced. Specifically: **someone who works a night shift
   must not then work the morning or afternoon shift.**
8. When a staff member is off or on leave, the system **automatically
   re-optimises** to fill the gap and updates the affected staff.
9. Notify all staff by **email and SMS** when a schedule is published.
10. Attendance tracking: sign in and sign out of the facility.
11. Real-time dashboard: staff on duty and available right now, doctors on duty.
12. Audit report on all staff, for management decision-making.

The brief also requires temporary-exit logging: staff log out when leaving the
facility for breaks or official duties, and log back in on return.

---

## 3. Stack

| Layer | Choice |
|---|---|
| Language | Python 3.9+ (3.11+ recommended; a `StrEnum` shim keeps 3.9/3.10 working) |
| Web | Flask 3.1.3, Jinja2 |
| Data | SQLAlchemy 2.0.48, Flask-SQLAlchemy, SQLite |
| Migrations | Flask-Migrate / Alembic (wired up, **never initialised** — see §8) |
| Auth | Flask-Login, Flask-Bcrypt, Flask-WTF (CSRF only) |
| Front end | Bootstrap 5.3.3 + Chart.js 4.4.4, **vendored** into `app/static/vendor/` |
| PDF | ReportLab |
| Production | Waitress (Windows + Linux), Gunicorn optional on Linux |
| Container | Docker, `python:3.12-slim` |

**No build step.** No npm, no bundler, no `node_modules`. `python run.py` is the
whole dev procedure. Bootstrap and Chart.js are committed files, not CDN links,
so the portal works with **no internet connection** — a deliberate choice given
the thesis's focus on resource-constrained settings.

**No optimisation library.** No OR-Tools, PuLP or SciPy. The scheduling engine
is written from scratch, which matters for a thesis: the algorithm is the
author's contribution and is fully explainable.

---

## 4. Architecture

Three-tier, matching Chapter 3 of the thesis.

```
app/
├── models/       Data tier      - 10 SQLAlchemy models
├── services/     Application    - ALL business logic lives here
├── blueprints/   Presentation   - thin HTTP controllers, no business rules
├── templates/    Presentation   - 25 Jinja2 views on Bootstrap
└── static/       Presentation   - theme.css, app.js, vendor/
```

Blueprints parse the request, call a service, render. **Never put a business
rule in a route** — that convention is held throughout and should be kept.

### Key services

| File | Responsibility |
|---|---|
| `constraints.py` | Phase 1 hard-constraint checker. The safety kernel. |
| `fairness.py` | Phase 2 objective function (variance of nights/weekends/hours) |
| `scheduler.py` | The generator: greedy construction + hill climbing |
| `reoptimizer.py` | Releases shifts on leave/absence and refills them |
| `roster_service.py` | Publish, withdraw, manual override |
| `leave_service.py` | Leave workflow; approval triggers re-optimisation |
| `attendance.py` | Sign-in, temporary exits, reconciliation |
| `availability.py` | Derives real-time presence (never stored) |
| `analytics.py` | KPI computation |
| `notifications.py` | Pluggable email/SMS/in-app adapters |
| `audit.py` | Hash-chained audit trail + integrity verification |
| `reports.py` | PDF and CSV generation |
| `state_builder.py` | Snapshots current roster state for the checker |

### Data model (10 tables)

`Department`, `ShiftRequirement`, `Staff`, `Shift`, `Roster`, `Assignment`,
`LeaveRequest`, `AttendanceRecord`, `AttendanceEvent`, `Notification`,
`AuditLog`.

> The thesis ERD (Figure 4.3) shows only six entities. `ShiftRequirement`,
> `Notification`, `AuditLog` and `AttendanceEvent` were needed for features the
> thesis *text* describes but the diagram omits. **The diagram needs updating.**

---

## 5. The scheduling engine

A hybrid two-phase algorithm, as specified in thesis Chapter 4.

### Phase 1 — feasible construction (`constraints.py`, `scheduler.py`)

The week is walked in **calendar order, night shifts first**, because each
decision constrains the next. A staff member is only ever offered a slot if
they pass **every** hard constraint. A roster is therefore legal *by
construction*, never repaired afterwards.

Hard constraints, all configurable via `.env`:

| Rule | Default |
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

**The night-then-day rule is not special-cased.** It falls out of the rest
period: the night shift ends 07:00, so a 07:00 morning start gives zero rest
and a 14:00 afternoon start gives seven — both under eleven. A following night
starts 22:00 (fifteen hours), which is what still permits night rotations.
Understand this before touching `_rest_violation`.

### Phase 2 — heuristic optimisation (`fairness.py`, `scheduler.py`)

Hill climbing over three neighbourhood moves: **fill a gap**, **reassign a
slot**, **swap two slots**. Every candidate move is re-checked by the same
constraint checker, so feasibility is an invariant of the whole search.

The objective minimises variance of night shifts, weekend shifts and total
hours, plus a small preference penalty. **Fairness is cumulative, not weekly**:
counts from the previous four weeks are folded in before the current week is
scored (`HISTORY_WEEKS = 4`).

Where no legal assignment exists, the slot becomes a recorded **coverage gap**
and is escalated — never filled by breaking a rule.

### Measured performance

| Metric | Result |
|---|---|
| One department (6 staff, 26 shifts) | ~0.25 s |
| Whole hospital (28 staff, 102 shifts) | ~1.5 s |
| Projected at 100 staff | ~5 s (thesis target: under 5 minutes) |
| Phase 2 fairness cost reduction | 26–62 % |

---

## 6. Verification status

Three verification scripts live in `tests/`. They are **not yet pytest** —
converting them with proper fixtures is the top pending task (§8).

| Suite | What it does | Last result |
|---|---|---|
| `tests/verify_constraints.py` | Independently re-audits every assignment against all hard rules, without using the engine's own code | **0 violations** across 409 assignments |
| `tests/verify_workflows.py` | 43 checks: login, generate, publish, leave→re-optimise, attendance lifecycle, override rejection, audit tampering | **43/43** |
| `tests/verify_routes.py` | Renders every GET route as all four roles | All pass; RBAC denies correctly |

See `tests/README.md` for how to run them.

> `workflows.py` **mutates the database** and is not idempotent — it creates and
> publishes a roster. Re-run it against a dirty DB and five checks fail
> spuriously. **Always `python seed.py` first.** Give it proper fixtures when
> moving to pytest.

Docker deployment was built and run: image 299 MB, container `healthy`, login
verified over the LAN address, PDF generated, data persisted across restart,
`SECRET_KEY` guard confirmed.

---

## 7. Decisions and their rationale

Do not undo these without understanding why they were made.

**Shift hours follow the brief, not the thesis.** Morning 7h, afternoon 8h,
night 9h. Thesis Appendix C.1 says 8/8/8. The engine enforces weekly hours and
rest gaps rather than a flat 8h rule, so either works — but **the document and
the software currently disagree** and an examiner comparing them will notice.
*User decision pending.*

**Naive local datetimes, no timezone handling.** Ghana is GMT year-round with no
DST, so local time and UTC coincide. This keeps rostered times, attendance
stamps and audit entries directly comparable. **A server in another timezone
shifts everything** — the Docker image pins `TZ=Africa/Accra` for this reason.

**Notifications are simulated by default.** Console adapters compose every
message in full and log it to the Outbox page. Set `EMAIL_BACKEND=smtp` or
`SMS_BACKEND=http` with credentials to go live — no code changes.

**Audit trail is hash-chained.** Each row stores SHA-256 of its content plus the
previous row's digest. Editing any historical row invalidates every digest
after it. Verified by deliberately tampering with an entry.

**Presence is derived, never stored.** Computed from leave + roster + attendance
at request time, so the dashboard cannot drift from the records.

**Vacated assignments are kept, not deleted.** When leave releases a shift, the
original row is marked `VACATED` and a new `REPLACEMENT` row is written
alongside, so the roster shows both who was meant to work and who covers.

**RBAC is custom**, not a library — decorators in `app/utils/decorators.py` plus
permission methods on `Staff`. Four roles: `ADMIN`, `HR`, `MANAGER`, `STAFF`.

---

## 8. Pending work

Ordered by value.

1. **Convert `tests/` to pytest.** Highest priority. The scripts are in the
   repository and runnable, but they are procedural, share one database and are
   not idempotent. Thesis Chapter 3.5 describes unit/integration/system/UAT
   testing; proper fixtures would let you point an examiner at real evidence.
   Also fix the wrong `/dashboard/api/presence` path in `verify_routes.py`
   (the real endpoint is `/api/presence`).
2. **Initialise `migrations/`.** Flask-Migrate and Alembic are configured and
   the README implies migrations work, but `flask db init` was never run —
   schema currently comes from `db.create_all()` in `seed.py`. Either
   initialise it or drop the claim.
3. **Resolve the shift-hours contradiction** (§7). User decision.
4. **Live email/SMS credentials** if real delivery is wanted for the defence.
5. **`docs/` is an empty folder.** Use or remove.
6. **Update the thesis ERD** to the ten implemented tables (§4).

### Known limitation, documented not broken

**The re-optimiser does not chain moves.** When leave frees a shift, it picks
the fairest *legally eligible* colleague — but on any given day most of a
six-person department is already assigned, so often only one person qualifies.
In one seeded week a nurse reached 44h against a 48h cap. Legal and correct, but
a chained move (shuffling a third person to free a fairer candidate) would
improve it. Good "future work" material for Chapter 5.5.

---

## 9. Gotchas

Things that cost time to discover.

- **`localhost` is a secure context.** Testing login on the server itself passes
  even with `SESSION_COOKIE_SECURE=true` over plain HTTP; every real user gets a
  **400**. Always test from another machine using the address users will use.
- **Debug mode on a reachable host is remote code execution.** `run.py` now
  refuses to start in debug on a non-localhost address. Do not undo that.
- **`.venv` is not portable** — absolute paths are baked in. Delete before
  copying the project elsewhere.
- **The `wheels/` offline bundle is platform-specific.** Built for Windows x64 +
  Python 3.14. On anything else `start.bat` falls back to downloading.
- **Attendance has a UNIQUE constraint on `assignment_id`.** One record per
  rostered shift. `expected_assignment()` skips already-attended shifts to
  prevent a 500 — this was a real bug, found when a night shift stayed inside
  the six-hour late-sign-in window after being completed.
- **Seed order matters.** Rosters → leave → attendance. Leave must be processed
  before attendance so records reflect the post-re-optimisation roster.
- **`CONTEXT_DAYS` must exceed `MAX_CONSECUTIVE_DAYS`.** It was 3, too narrow to
  see a run spanning a week boundary, which produced runs of 7–8 days. Now 8.

---

## 10. Running it

```bash
pip install -r requirements.txt
python seed.py          # builds 28 staff, 24 rosters, ~2 weeks of history
python run.py           # http://127.0.0.1:5000
```

Sign in as `TH-ADM-001` / `Password123`. All 28 accounts share that password.

```bash
python seed.py                        # reset demo data any time
flask --app run.py verify-audit       # check the audit hash chain
flask --app run.py mark-absentees     # flag no-shows for a completed day
```

| Audience | Document |
|---|---|
| Developers | `README.md` |
| Someone running it to present | `HANDOVER.md` |
| Hosting it on a server | `DEPLOYMENT.md` |
