# Code figures for the thesis

Ten syntax-highlighted PNGs ready to insert into the document.

Each image already carries its own caption at the foot, so it can be dropped in
as-is. The captions are repeated below in plain text if you would rather set
them in Word as figure captions instead.

Line numbers in the figures are the **real line numbers** in the source files,
so an examiner can find the code in the repository.

---

## The ten figures

They are ordered as a narrative: the scheduling engine first, from its
top-level structure down into each phase and the objective it minimises, then
one figure per remaining implementation module of Chapter 4.4.

| Figure | File | Illustrates | Source |
|---|---|---|---|
| 4.6 | `fig_4_06_engine_overview.png` | The two-phase algorithm, top level | `scheduler.py` L123–153 |
| 4.7 | `fig_4_07_phase1_construct.png` | Phase 1 — feasible construction | `scheduler.py` L280–296 |
| 4.8 | `fig_4_08_rest_constraint.png` | Hard constraints — the rest-period rule | `constraints.py` L220–255 |
| 4.9 | `fig_4_09_fairness_objective.png` | The fairness objective function | `fairness.py` L28–120 |
| 4.10 | `fig_4_10_phase2_optimise.png` | Phase 2 — heuristic optimisation | `scheduler.py` L330–361 |
| 4.11 | `fig_4_11_reoptimiser.png` | Leave and absence — auto re-optimisation | `reoptimizer.py` L97–140 |
| 4.12 | `fig_4_12_attendance.png` | Attendance tracking — reconciliation | `attendance.py` L249–274 |
| 4.13 | `fig_4_13_availability.png` | Real-time availability / dashboard | `availability.py` L104–139 |
| 4.14 | `fig_4_14_notifications.png` | Notification service — email, SMS, in-app | `notifications.py` L152–214 |
| 4.15 | `fig_4_15_audit.png` | Audit trail and security | `audit.py` L82–118 |

Five figures cover the scheduling engine because it is the thesis's own
contribution; the remaining five cover one module each.

**Figures 4.9, 4.11, 4.13 and 4.14 are excerpts** of longer functions or span
more than one function in a file. Every gap is marked with a dotted rule and the
line numbers continue correctly across it, so nothing is presented as
contiguous code when it is not.

---

## Inserting into Word

1. **Insert → Pictures → This Device**, choose the PNG.
2. Right-click → **Size and Position** → set width to about **15 cm**, which
   fits A4 with normal margins. The images are 200 DPI, so they stay sharp in
   print.
3. If you prefer Word's own caption numbering, crop off the caption strip at the
   foot of the image and use the text below instead.

Figure 4.11 is the widest at 1472 px. If it looks small at 15 cm, give it 16 cm
and reduce that page's side margins slightly. Figure 4.7 is the shortest at 17
lines and will sit comfortably beside body text.

---

## Caption text

### Figure 4.6 — The two-phase scheduling algorithm

The entry point that produces one department's roster for one week, and the
clearest statement of the hybrid design. Demand is assembled first, then Phase 1
constructs a feasible roster and Phase 2 improves it. The fairness cost is
measured after each phase, which is what produces the improvement figure
reported to the manager and stored on the roster. Timing is recorded around the
whole operation: measured across the six departments, a complete hospital roster
is generated in approximately 1.5 seconds.

### Figure 4.7 — Phase 1: constructing a feasible roster

Fills every required slot using only staff who satisfy all hard constraints. The
week is walked in calendar order with night shifts resolved first, because each
decision constrains the next: whether a nurse may take Tuesday morning depends
on what they worked on Monday. For each slot the best candidate is selected and
placed. Where no legal candidate exists the slot is recorded as a coverage gap
and escalated, rather than filled by breaking a rule. A roster is therefore
feasible by construction and is never repaired after the fact.

### Figure 4.8 — Hard-constraint verification: the rest-period rule

Enforces the minimum rest period between shifts, and with it the requirement
that a night worker must not take the following morning or afternoon shift. The
proposed shift is compared against the two days either side. Overlap is rejected
outright; otherwise the gap to the neighbouring shift is measured and rejected
if it falls below the eleven-hour minimum. The night rule is not coded as a
special case: a night shift ends at 07:00, so a 07:00 morning start leaves zero
hours of rest and a 14:00 afternoon start leaves seven, both below the minimum.
A following night shift begins at 22:00, leaving fifteen hours, which is what
still permits night rotations.

### Figure 4.9 — Quantifying fairness: the objective function

Turns fairness from an intention into a number the optimiser can minimise. The
cost is a weighted sum of the population variance of night shifts, weekend
shifts and total hours across the department, so a cost of zero means every
staff member carries an identical share. Coverage gaps are weighted a thousand
times higher than any fairness term, which guarantees the engine never trades
away cover to make a roster look more even. The same objective orders candidates
during construction, so whoever currently carries the lightest share of that
kind of duty is offered the shift first.

### Figure 4.10 — Phase 2: heuristic optimisation of the feasible roster

Improves a feasible roster by hill climbing over three neighbourhood moves. Each
iteration selects a move at random: fill a recorded coverage gap, reassign one
slot to a different staff member, or swap two staff between two slots. Every
candidate move is re-validated by the same hard-constraint checker used in Phase
1, so feasibility is an invariant of the entire search rather than something
verified at the end. Only strictly improving moves are kept. Measured across the
six departments, this phase reduced the fairness cost by between 26 and 62 per
cent.

### Figure 4.11 — Automatic re-optimisation after approved leave

Fills each shift released by approved leave or a reported absence, without human
intervention. For every vacated shift the engine excludes staff already holding
that slot, then ranks the remaining colleagues by the same fairness objective
used during generation and assigns the best candidate. The original assignment
is preserved as VACATED and a new REPLACEMENT row is written alongside it, so
the roster records both who was scheduled and who actually covers. The in-memory
state is updated as it goes, so a second gap in the same run sees the cover just
assigned. Each reassignment is written to the audit trail and the replacement is
notified.

### Figure 4.12 — Attendance reconciliation against the published roster

Converts raw sign-in and sign-out stamps into the figures used for payroll and
management reporting. Time logged as away from the facility, recorded whenever a
staff member steps out for a break or official duty, is deducted so that hours
worked reflect time actually on site rather than the span between the first and
last stamp. The result is then compared against the rostered shift to derive
early departure and overtime, where overtime counts only beyond a configurable
threshold. A sign-in with no rostered shift is recorded without these
comparisons.

### Figure 4.13 — Deriving real-time staff availability

Determines each staff member's live status for the dashboard. Presence is
derived on request from three existing records rather than stored as a field, so
the dashboard cannot drift out of step with the underlying data. Attendance
takes priority: someone signed in is available, or temporarily out if they have
logged an exit. Otherwise approved leave applies, and failing that a staff
member rostered for the shift in progress is marked absent once a grace period
has elapsed. Everyone else is off duty.

### Figure 4.14 — Multi-channel notification dispatch

Sends one message to one recipient across in-app, email and SMS, recording every
attempt. The body is truncated for SMS so a long roster announcement does not
become several charged messages. Each channel resolves its own adapter from
configuration, so the console adapters used for demonstration and the live SMTP
or HTTP gateways are interchangeable without any change to this function. Every
dispatch is written to the notification log with its own delivery status, which
is what produces the delivery-rate figures on the analytics dashboard and lets a
partial failure stay visible rather than being lost.

### Figure 4.15 — Audit trail integrity verification

Detects any alteration to the audit trail. Every entry stores the SHA-256 digest
of its own content combined with the digest of the entry before it. Verification
walks the trail in order, confirming that each row links to its predecessor and
that recomputing its digest still reproduces the recorded value. Editing or
deleting any historical entry therefore invalidates every digest that follows
it, and the check reports the first entry at which the chain breaks. This was
confirmed by deliberately altering an entry and observing the failure.

---

## Regenerating

The figures are generated from the live source, so they must be rebuilt if the
code changes or the line numbers will no longer match.

```bash
python docs/generate_figures.py
```

- `docs/generate_figures.py` — which functions to render, and their captions
- `docs/_render_code.py` — the renderer

Highlighting uses Python's built-in `tokenize` and layout uses Pillow, which the
project already depends on through ReportLab, so no extra package is needed.

To change which functions appear, edit the `FIGURES` list in
`generate_figures.py`. A whole function is rendered by naming it; an excerpt or
a span across several functions is selected with `keep=[(start, end), ...]`, and
each gap between ranges is marked in the output.
