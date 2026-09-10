"""
Generate the six code figures for the thesis.

One figure per implementation module described in Chapter 4.4, so the figures
line up with the section of the document they illustrate.
"""
import ast
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _render_code import PROJECT, render


def extract(rel_path, dotted, keep=None):
    """
    Return [(line_number, text), ...] for a function, or for selected ranges
    within it. A (None, "") row marks a gap, so the figure keeps the file's
    real line numbers either side of an excerpt rather than renumbering.
    """
    path = PROJECT / rel_path
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    parts = dotted.split(".")

    node = None
    if len(parts) == 1:
        for n in tree.body:
            if isinstance(n, ast.FunctionDef) and n.name == parts[0]:
                node = n
    else:
        for n in tree.body:
            if isinstance(n, ast.ClassDef) and n.name == parts[0]:
                for m in n.body:
                    if isinstance(m, ast.FunctionDef) and m.name == parts[1]:
                        node = m
    if node is None:
        raise SystemExit(f"not found: {dotted} in {rel_path}")

    all_lines = text.splitlines()
    blocks = keep or [(node.lineno, node.end_lineno)]

    rows = []
    for i, (a, b) in enumerate(blocks):
        for n in range(a, b + 1):
            rows.append((n, all_lines[n - 1]))
        if i < len(blocks) - 1:
            rows.append((None, ""))
    return rows


def dedent(rows):
    """Strip the common leading indent so the figure is not mostly whitespace."""
    pads = [len(t) - len(t.lstrip()) for n, t in rows if n is not None and t.strip()]
    cut = min(pads) if pads else 0
    return [(n, (t[cut:] if t.strip() else "")) for n, t in rows]


FIGURES = [
    dict(
        no="Figure 4.6",
        title="Hard-constraint verification: the rest-period rule",
        rel="app/services/constraints.py",
        dotted="HardConstraintChecker._rest_violation",
        file_label="app/services/constraints.py  ·  HardConstraintChecker._rest_violation()",
        out="fig_4_6_rest_constraint.png",
        caption=(
            "Enforces the minimum rest period between shifts, and with it the requirement that a "
            "night worker must not take the following morning or afternoon shift.\n"
            "The proposed shift is compared against the two days either side. Overlap is rejected "
            "outright; otherwise the gap to the neighbouring shift is measured and rejected if it "
            "falls below the eleven-hour minimum. The night rule is not coded as a special case: a "
            "night shift ends at 07:00, so a 07:00 morning start leaves zero hours of rest and a "
            "14:00 afternoon start leaves seven, both below the minimum. A following night shift "
            "begins at 22:00, leaving fifteen hours, which is what still permits night rotations."
        ),
    ),
    dict(
        no="Figure 4.7",
        title="Phase 2: heuristic optimisation of the feasible roster",
        rel="app/services/scheduler.py",
        dotted="SchedulingEngine._optimise",
        file_label="app/services/scheduler.py  ·  SchedulingEngine._optimise()",
        out="fig_4_7_optimiser.png",
        caption=(
            "Improves a feasible roster by hill climbing over three neighbourhood moves.\n"
            "Each iteration selects a move at random: fill a recorded coverage gap, reassign one "
            "slot to a different staff member, or swap two staff between two slots. Every candidate "
            "move is re-validated by the same hard-constraint checker used in Phase 1, so "
            "feasibility is an invariant of the entire search rather than something verified at the "
            "end. Only strictly improving moves are kept. Measured across the six departments, this "
            "phase reduced the fairness cost by between 26 and 62 per cent."
        ),
    ),
    dict(
        no="Figure 4.8",
        title="Automatic re-optimisation after approved leave",
        rel="app/services/reoptimizer.py",
        dotted="release_and_refill",
        file_label="app/services/reoptimizer.py  ·  release_and_refill()  ·  core replacement loop",
        out="fig_4_8_reoptimiser.png",
        keep=[(97, 99), (101, 140)],
        caption=(
            "Fills each shift released by approved leave or a reported absence, without human "
            "intervention.\n"
            "For every vacated shift the engine excludes staff already holding that slot, then ranks "
            "the remaining colleagues by the same fairness objective used during generation and "
            "assigns the best candidate. The original assignment is preserved as VACATED and a new "
            "REPLACEMENT row is written alongside it, so the roster records both who was scheduled "
            "and who actually covers. The in-memory state is updated as it goes, so a second gap in "
            "the same run sees the cover just assigned. Each reassignment is written to the audit "
            "trail and the replacement is notified."
        ),
    ),
    dict(
        no="Figure 4.9",
        title="Attendance reconciliation against the published roster",
        rel="app/services/attendance.py",
        dotted="reconcile",
        file_label="app/services/attendance.py  ·  reconcile()",
        out="fig_4_9_attendance.png",
        caption=(
            "Converts raw sign-in and sign-out stamps into the figures used for payroll and "
            "management reporting.\n"
            "Time logged as away from the facility, recorded whenever a staff member steps out for "
            "a break or official duty, is deducted so that hours worked reflect time actually on "
            "site rather than the span between the first and last stamp. The result is then compared "
            "against the rostered shift to derive early departure and overtime, where overtime "
            "counts only beyond a configurable threshold. A sign-in with no rostered shift is "
            "recorded without these comparisons."
        ),
    ),
    dict(
        no="Figure 4.10",
        title="Deriving real-time staff availability",
        rel="app/services/availability.py",
        dotted="presence_snapshot",
        file_label="app/services/availability.py  ·  presence_snapshot()  ·  derivation loop",
        out="fig_4_10_availability.png",
        keep=[(104, 139)],
        caption=(
            "Determines each staff member's live status for the dashboard.\n"
            "Presence is derived on request from three existing records rather than stored as a "
            "field, so the dashboard cannot drift out of step with the underlying data. Attendance "
            "takes priority: someone signed in is available, or temporarily out if they have logged "
            "an exit. Otherwise approved leave applies, and failing that a staff member rostered for "
            "the shift in progress is marked absent once a grace period has elapsed. Everyone else "
            "is off duty."
        ),
    ),
    dict(
        no="Figure 4.11",
        title="Audit trail integrity verification",
        rel="app/services/audit.py",
        dotted="verify_chain",
        file_label="app/services/audit.py  ·  verify_chain()",
        out="fig_4_11_audit.png",
        caption=(
            "Detects any alteration to the audit trail.\n"
            "Every entry stores the SHA-256 digest of its own content combined with the digest of "
            "the entry before it. Verification walks the trail in order, confirming that each row "
            "links to its predecessor and that recomputing its digest still reproduces the recorded "
            "value. Editing or deleting any historical entry therefore invalidates every digest that "
            "follows it, and the check reports the first entry at which the chain breaks. This was "
            "confirmed by deliberately altering an entry and observing the failure."
        ),
    ),
]


def main():
    print(f"{'FIGURE':<12} {'FILE':<34} {'SIZE':>11} {'ROWS':>5}  SOURCE LINES")
    print("-" * 82)
    for spec in FIGURES:
        rows = dedent(extract(spec["rel"], spec["dotted"], keep=spec.get("keep")))
        _path, w, h = render(
            spec["no"], spec["title"], spec["file_label"],
            rows, spec["caption"], spec["out"],
        )
        nums = [n for n, _ in rows if n is not None]
        print(f"{spec['no']:<12} {spec['out']:<34} {w}x{h:<5} {len(rows):>5}  "
              f"L{min(nums)}-{max(nums)}")


if __name__ == "__main__":
    main()
