# Handover guide

Everything the next person needs to run and present the **Intelligent Hospital
Workforce Scheduling System**. No programming knowledge required.

---

## The short version

1. Install **Python 3.11 or newer** from <https://www.python.org/downloads/>.
   During installation, tick **"Add python.exe to PATH"**. This matters.
2. Double-click **`start.bat`** (Windows) or run `bash start.sh` (Mac/Linux).
3. Wait about a minute the first time. A browser opens at
   <http://127.0.0.1:5000>.
4. Sign in as **`TH-ADM-001`** with password **`Password123`**.

That is the whole procedure. `start.bat` creates its own environment, installs
what it needs, builds the demonstration hospital and starts the portal. On
every run after the first it starts in a couple of seconds.

To stop it, press **Ctrl+C** in the black window, or just close the window.

---

## What the recipient's machine needs

| Requirement | Detail |
|---|---|
| **Python 3.11+** | The only thing that must be installed by hand. About 25 MB. No administrator rights needed. |
| **Internet, once** | Only for the first run, to download the libraries. See the offline note below. |
| **A web browser** | Any modern one. |
| **Disk space** | About 250 MB once set up. |

It does **not** need: a database server, a web server, Node.js, Docker,
administrator rights, or an internet connection after setup.

Windows, macOS and Linux all work. It has been tested on Windows.

---

## If the presentation venue has no internet

The first run normally downloads about 30 libraries. If you cannot rely on
having internet on the day, there are two options.

**Option A, strongly recommended.** Run `start.bat` once on the presenting
machine *before* the day, while you still have internet. Everything is cached
in the `.venv` folder from then on and no further connection is needed.

**Option B, the `wheels/` folder.** The project ships with a pre-downloaded
copy of every library in `wheels/`. `start.bat` tries this first and only falls
back to the internet if it does not fit. Be aware of its limits:

> The `wheels/` bundle was built for **64-bit Windows running Python 3.14**.
> On a different Python version or operating system it will not match, and
> `start.bat` will fall back to downloading from the internet. It is insurance,
> not a guarantee.

If you know the presenting machine's Python version in advance, a matching
bundle can be rebuilt with:

```bash
python -m pip download -r requirements.txt -d wheels
```

You can safely delete `wheels/` (14 MB) if you do not need it.

---

## What to copy

Copy the **whole project folder**, with two exceptions:

| Folder | Copy it? | Why |
|---|---|---|
| `.venv/` | **No — delete it** | It contains absolute paths to this machine. It will not work elsewhere and `start.bat` rebuilds it. |
| `__pycache__/` | No | Regenerated automatically. |
| `instance/hospital.db` | **Your choice** | See below. |
| Everything else | Yes | |

**About `instance/hospital.db`:** this single file *is* the database.

- **Include it** and the presenter gets exactly the data you rehearsed with —
  same staff, same rosters, same numbers on screen. Safest for a presentation.
- **Leave it out** and `start.bat` builds a fresh hospital on first run. The
  data will be similar but not identical, because rosters are generated
  relative to the current date.

For a presentation, include it.

To make the copy from a command prompt in the project folder:

```bash
rmdir /s /q .venv
```

Then zip the folder.

---

## Resetting the demonstration data

If the data gets messy during practice, rebuild it from scratch:

```bash
.venv\Scripts\python.exe seed.py
```

This wipes the database and creates a fresh hospital in about 15 seconds. It is
safe to run as often as you like.

---

## The five-minute demonstration

A route through the system that shows every major feature.

1. **Sign in as `TH-ADM-001`.** The operations dashboard shows live presence:
   who is on duty, doctors on duty, departmental coverage. It refreshes itself
   every 30 seconds.
2. **Scheduling → Generate roster.** Choose Nursing and a future week. The
   roster is built in well under a second, with the fairness breakdown and the
   engine's own timing shown beneath it.
3. **Publish it.** A message confirms how many notifications went out. Open
   **Message outbox** to read the actual emails and SMS texts that were composed.
4. **Sign in as `TH-NUR-003`** (open a private browsing window to stay signed
   in as both). This is the staff view: own shifts, own attendance terminal.
5. **Request leave** covering one of those shifts.
6. **Back as `TH-HRM-001` → Leave approvals.** Approve it. The message reports
   how many shifts were released and automatically refilled. Open the Nursing
   roster: the replacement is marked in amber.
7. **Attendance.** Sign in, log an exit, log back in, sign out. Watch the
   dashboard status change.
8. **Reports → Analytics → Audit report (PDF).** The management deliverable.
9. **Reports → Audit trail.** Every action above, hash-verified.

---

## Accounts

Every account uses the password `Password123`.

| Role | Staff number | Sees |
|---|---|---|
| System Administrator | `TH-ADM-001` | Everything, plus accounts and policy |
| HR Officer | `TH-HRM-001` | All departments, leave approval, audit trail |
| Nursing Manager | `TH-NUR-001` | Generates and publishes the Nursing roster |
| Doctor (staff) | `TH-DOC-002` | Own profile, schedule, leave, attendance |

All 28 staff numbers work: `TH-NUR-001`–`006`, `TH-DOC-001`–`006`,
`TH-PHA-001`–`006`, `TH-LAB-001`–`006`, `TH-HRM-001`–`002`, `TH-ADM-001`–`002`.

---

## If something goes wrong

| Symptom | Cause and fix |
|---|---|
| `'python' is not recognized` | Python is not on the PATH. Reinstall it and tick **"Add python.exe to PATH"**. |
| Setup stops at "Checking dependencies" | No internet on the first run. Connect and run `start.bat` again. |
| `Address already in use` / port 5000 busy | The portal is already running in another window, or another program uses port 5000. Close the other window, or open Task Manager and end any `python.exe` processes. |
| Browser shows "can't connect" | Give it a few seconds after the black window says "Starting the portal", then refresh. |
| Data looks wrong or empty | Run `.venv\Scripts\python.exe seed.py` to rebuild the demonstration data. |
| Everything is broken | Delete `.venv` and `instance`, then run `start.bat` again. It rebuilds both. |

The black console window shows what the system is doing. If you need help, the
last twenty lines of it will usually explain the problem.

---

## For a technical audience

Full architecture, the scheduling algorithm, the constraint model and the
configuration reference are in [README.md](README.md).
