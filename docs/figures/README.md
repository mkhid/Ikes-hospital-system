# Code figures for the thesis

Six syntax-highlighted PNGs, one per implementation module described in
Chapter 4.4, ready to insert into the document.

Each image already carries its own caption at the foot, so you can drop it in
as-is. The captions are repeated below in plain text if you would rather set
them in Word as figure captions instead.

Line numbers in the figures are the **real line numbers** in the source files,
so an examiner can find the code in the repository.

---

## The six figures

| Figure | File | Illustrates | Source |
|---|---|---|---|
| 4.6 | `fig_4_6_rest_constraint.png` | Automated Scheduling Engine — hard constraints | `app/services/constraints.py` L220–255 |
| 4.7 | `fig_4_7_optimiser.png` | Automated Scheduling Engine — Phase 2 optimisation | `app/services/scheduler.py` L330–361 |
| 4.8 | `fig_4_8_reoptimiser.png` | Leave and Absence Management — automatic re-optimisation | `app/services/reoptimizer.py` L97–140 |
| 4.9 | `fig_4_9_attendance.png` | Attendance Tracking — reconciliation | `app/services/attendance.py` L249–274 |
| 4.10 | `fig_4_10_availability.png` | Real-time availability / dashboard | `app/services/availability.py` L104–139 |
| 4.11 | `fig_4_11_audit.png` | Audit Trail and Security | `app/services/audit.py` L82–118 |

Figures 4.8 and 4.10 are excerpts of longer functions. The gap is marked with a
dotted rule and the line numbers continue correctly across it, so nothing is
misrepresented as contiguous.

---

## Inserting into Word

1. **Insert → Pictures → This Device**, choose the PNG.
2. Right-click → **Size and Position** → set width to about **15 cm** (fits A4
   with normal margins). The images are 200 DPI, so they stay sharp in print.
3. If you prefer Word's own captions, crop off the caption strip at the foot of
   the image and use the text below instead.

Figure 4.8 is the widest at 1472 px. If it looks small at 15 cm, set it to
**16 cm** and reduce the left and right margins slightly for that page, or
place it landscape.

---

## Caption text

### Figure 4.6 — Hard-constraint verification: the rest-period rule

Enforces the minimum rest period between shifts, and with it the requirement
that a night worker must not take the following morning or afternoon shift. The
proposed shift is compared against the two days either side. Overlap is rejected
outright; otherwise the gap to the neighbouring shift is measured and rejected
if it falls below the eleven-hour minimum. The night rule is not coded as a
special case: a night shift ends at 07:00, so a 07:00 morning start leaves zero
hours of rest and a 14:00 afternoon start leaves seven, both below the minimum.
A following night shift begins at 22:00, leaving fifteen hours, which is what
still permits night rotations.

### Figure 4.7 — Phase 2: heuristic optimisation of the feasible roster

Improves a feasible roster by hill climbing over three neighbourhood moves. Each
iteration selects a move at random: fill a recorded coverage gap, reassign one
slot to a different staff member, or swap two staff between two slots. Every
candidate move is re-validated by the same hard-constraint checker used in Phase
1, so feasibility is an invariant of the entire search rather than something
verified at the end. Only strictly improving moves are kept. Measured across the
six departments, this phase reduced the fairness cost by between 26 and 62 per
cent.

### Figure 4.8 — Automatic re-optimisation after approved leave

Fills each shift released by approved leave or a reported absence, without human
intervention. For every vacated shift the engine excludes staff already holding
that slot, then ranks the remaining colleagues by the same fairness objective
used during generation and assigns the best candidate. The original assignment
is preserved as VACATED and a new REPLACEMENT row is written alongside it, so
the roster records both who was scheduled and who actually covers. The in-memory
state is updated as it goes, so a second gap in the same run sees the cover just
assigned. Each reassignment is written to the audit trail and the replacement is
notified.

### Figure 4.9 — Attendance reconciliation against the published roster

Converts raw sign-in and sign-out stamps into the figures used for payroll and
management reporting. Time logged as away from the facility, recorded whenever a
staff member steps out for a break or official duty, is deducted so that hours
worked reflect time actually on site rather than the span between the first and
last stamp. The result is then compared against the rostered shift to derive
early departure and overtime, where overtime counts only beyond a configurable
threshold. A sign-in with no rostered shift is recorded without these
comparisons.

### Figure 4.10 — Deriving real-time staff availability

Determines each staff member's live status for the dashboard. Presence is
derived on request from three existing records rather than stored as a field, so
the dashboard cannot drift out of step with the underlying data. Attendance
takes priority: someone signed in is available, or temporarily out if they have
logged an exit. Otherwise approved leave applies, and failing that a staff
member rostered for the shift in progress is marked absent once a grace period
has elapsed. Everyone else is off duty.

### Figure 4.11 — Audit trail integrity verification

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
`generate_figures.py`. A whole function is rendered by naming it; an excerpt is
selected with `keep=[(start, end), ...]`, and each gap between ranges is marked
in the output.
