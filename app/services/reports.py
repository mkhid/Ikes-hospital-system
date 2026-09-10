"""
Report generation: PDF for circulation, CSV for further analysis.

The audit report is the deliverable hospital management acts on, so it pulls
together three things that are normally read separately: what each staff member
was scheduled and actually worked, how the unpopular shifts were distributed,
and the tamper-evidence status of the trail those figures rest on.
"""
import csv
import io

from flask import current_app
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.services import analytics, audit
from app.utils.timeutils import format_d, format_dt, humanise_duration, now

INK = colors.HexColor("#1f2933")
MUTED = colors.HexColor("#6b7280")
ACCENT = colors.HexColor("#1a5fb4")
BAND = colors.HexColor("#f1f5f9")
LINE = colors.HexColor("#d5dbe3")
WARN = colors.HexColor("#b45309")
BAD = colors.HexColor("#b91c1c")


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontSize=17, textColor=INK, spaceAfter=2
        ),
        "subtitle": ParagraphStyle(
            "subtitle",
            parent=base["Normal"],
            fontSize=9.5,
            textColor=MUTED,
            alignment=TA_CENTER,
            spaceAfter=10,
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base["Heading2"],
            fontSize=12,
            textColor=ACCENT,
            spaceBefore=12,
            spaceAfter=5,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontSize=9, textColor=INK, leading=13
        ),
        "small": ParagraphStyle(
            "small", parent=base["Normal"], fontSize=7.6, textColor=MUTED, leading=10
        ),
        "cell": ParagraphStyle(
            "cell", parent=base["Normal"], fontSize=7.8, textColor=INK, leading=10
        ),
    }


def _table(data, widths, align_right=(), highlight_rows=()):
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BAND]),
    ]
    for column in align_right:
        style.append(("ALIGN", (column, 1), (column, -1), "RIGHT"))
    for row in highlight_rows:
        style.append(("TEXTCOLOR", (0, row), (-1, row), BAD))
    table.setStyle(TableStyle(style))
    return table


def _kpi_band(pairs, styles, columns=4):
    """A row of headline figures rendered as a borderless grid."""
    cells = []
    for label, value in pairs:
        cells.append(
            Paragraph(
                f'<font size="13" color="#1a5fb4"><b>{value}</b></font><br/>'
                f'<font size="7" color="#6b7280">{label.upper()}</font>',
                styles["body"],
            )
        )
    rows = [cells[i : i + columns] for i in range(0, len(cells), columns)]
    for row in rows:
        while len(row) < columns:
            row.append("")

    width = (landscape(A4)[0] - 24 * mm) / columns
    table = Table(rows, colWidths=[width] * columns, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), BAND),
                ("BOX", (0, 0), (-1, -1), 0.4, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.white),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def _header_footer(canvas, doc):
    canvas.saveState()
    width, height = landscape(A4)
    hospital = current_app.config.get("HOSPITAL_NAME", "Hospital")
    system_name = current_app.config.get(
        "SYSTEM_NAME", "Workforce Scheduling System"
    )

    canvas.setFillColor(ACCENT)
    canvas.rect(0, height - 8 * mm, width, 8 * mm, stroke=0, fill=1)

    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(12 * mm, 8 * mm, f"{hospital}  |  {system_name}")
    canvas.drawRightString(
        width - 12 * mm, 8 * mm, f"Page {doc.page}  |  Generated {format_dt(now())}"
    )
    canvas.setStrokeColor(LINE)
    canvas.line(12 * mm, 11 * mm, width - 12 * mm, 11 * mm)
    canvas.restoreState()


def _document(buffer, title):
    return SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=14 * mm,
        bottomMargin=16 * mm,
        title=title,
        author=current_app.config.get("HOSPITAL_NAME", "Hospital"),
        subject=current_app.config.get("SYSTEM_NAME", "Workforce Scheduling System"),
    )


# ----------------------------------------------------------------------
# Management audit report
# ----------------------------------------------------------------------
def staff_audit_report_pdf(start_date, end_date, department=None, actor=None):
    """The full decision-support report on every staff member in the period."""
    report = analytics.workforce_report(start_date, end_date, department)
    integrity = audit.verify_chain()
    styles = _styles()
    buffer = io.BytesIO()
    doc = _document(buffer, "Workforce Audit Report")

    scope = department.name if department else "All departments"
    story = [
        Paragraph("Workforce Audit Report", styles["title"]),
        Paragraph(
            f"{scope} &nbsp;|&nbsp; {format_d(start_date)} to {format_d(end_date)} "
            f"&nbsp;|&nbsp; {report['headcount']} active staff",
            styles["subtitle"],
        ),
    ]

    scheduling = report["scheduling"]
    attendance = report["attendance"]
    leave = report["leave"]
    notify = report["notifications"]

    story.append(Paragraph("Headline indicators", styles["h2"]))
    story.append(
        _kpi_band(
            [
                ("Shifts scheduled", scheduling["shifts_scheduled"]),
                ("Shift fill rate", f"{scheduling['fill_rate']}%"),
                ("Coverage gaps", scheduling["coverage_gaps"]),
                ("Auto replacements", scheduling["replacements"]),
                ("Hours scheduled", scheduling["scheduled_hours"]),
                ("Hours worked", attendance["worked_hours"]),
                ("Utilisation", f"{attendance['utilisation']}%"),
                ("Overtime hours", attendance["overtime_hours"]),
                ("Absenteeism", f"{attendance['absenteeism_rate']}%"),
                ("Punctuality", f"{attendance['punctuality_rate']}%"),
                ("Leave days approved", leave["days_approved"]),
                ("Notification delivery", f"{notify['delivery_rate']}%"),
            ],
            styles,
            columns=4,
        )
    )

    story.append(Paragraph("Staff performance and workload", styles["h2"]))
    header = [
        "Staff",
        "No.",
        "Department",
        "Shifts",
        "Nights",
        "W/ends",
        "Sched hrs",
        "Worked hrs",
        "Util %",
        "Overtime",
        "Late",
        "Absent",
        "Leave days",
    ]
    rows = [header]
    flagged = []
    for index, row in enumerate(report["per_staff"], start=1):
        staff = row["staff"]
        rows.append(
            [
                Paragraph(staff.full_name, styles["cell"]),
                staff.staff_no,
                staff.department.code if staff.department else "-",
                row["shifts"],
                row["nights"],
                row["weekends"],
                row["scheduled_hours"],
                row["worked_hours"],
                f"{row['utilisation']}%",
                row["overtime_hours"],
                row["late_count"],
                row["absences"],
                row["leave_days"],
            ]
        )
        if row["absences"] > 0:
            flagged.append(index)

    widths = [34 * mm, 22 * mm, 20 * mm] + [16 * mm] * 10
    story.append(
        _table(rows, widths, align_right=tuple(range(3, 13)), highlight_rows=flagged)
    )
    story.append(Spacer(1, 4))
    story.append(
        Paragraph(
            "Rows in red carry at least one recorded absence. Utilisation is hours "
            "actually worked on site as a percentage of hours rostered.",
            styles["small"],
        )
    )

    if report["by_department"]:
        story.append(PageBreak())
        story.append(Paragraph("Departmental comparison", styles["h2"]))
        dept_rows = [
            [
                "Department",
                "Staff",
                "Shifts",
                "Sched hrs",
                "Worked hrs",
                "Overtime",
                "Gaps",
                "Fill %",
                "Absentee %",
                "Util %",
            ]
        ]
        for row in report["by_department"]:
            dept_rows.append(
                [
                    row["department"].name,
                    row["headcount"],
                    row["shifts"],
                    row["scheduled_hours"],
                    row["worked_hours"],
                    row["overtime_hours"],
                    row["gaps"],
                    f"{row['fill_rate']}%",
                    f"{row['absenteeism_rate']}%",
                    f"{row['utilisation']}%",
                ]
            )
        story.append(
            _table(
                dept_rows,
                [44 * mm] + [22 * mm] * 9,
                align_right=tuple(range(1, 10)),
            )
        )

    story.append(Paragraph("Leave and communication", styles["h2"]))
    story.append(
        _kpi_band(
            [
                ("Requests received", leave["total"]),
                ("Approved", leave["approved"]),
                ("Pending", leave["pending"]),
                ("Approval rate", f"{leave['approval_rate']}%"),
                ("Avg turnaround", f"{leave['avg_turnaround_hours']} hrs"),
                ("Shifts released", leave["released"]),
                ("Auto refilled", leave["auto_refilled"]),
                ("Messages sent", notify["sent"]),
            ],
            styles,
            columns=4,
        )
    )

    story.append(Paragraph("Audit trail integrity", styles["h2"]))
    verdict = (
        f"<b>Verified.</b> All {integrity['checked']} audit entries were "
        f"recomputed and every hash matched its recorded value. No entry has "
        f"been altered or removed since it was written."
        if integrity["intact"]
        else (
            f"<b><font color='#b91c1c'>Integrity check failed.</font></b> "
            f"{integrity['reason']} at entry #{integrity['broken_at']} of "
            f"{integrity['checked']} checked."
        )
    )
    story.append(Paragraph(verdict, styles["body"]))
    story.append(Spacer(1, 5))
    story.append(
        Paragraph(
            "Each audit entry stores the SHA-256 digest of its own content chained "
            "to the digest of the entry before it, so any edit to the history "
            "invalidates every digest that follows.",
            styles["small"],
        )
    )

    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    buffer.seek(0)
    return buffer


# ----------------------------------------------------------------------
# Attendance reconciliation
# ----------------------------------------------------------------------
def attendance_report_pdf(records, start_date, end_date, department=None):
    styles = _styles()
    buffer = io.BytesIO()
    doc = _document(buffer, "Attendance Reconciliation")

    scope = department.name if department else "All departments"
    story = [
        Paragraph("Attendance Reconciliation Report", styles["title"]),
        Paragraph(
            f"{scope} &nbsp;|&nbsp; {format_d(start_date)} to {format_d(end_date)} "
            f"&nbsp;|&nbsp; {len(records)} record(s)",
            styles["subtitle"],
        ),
    ]

    header = [
        "Date",
        "Staff",
        "Dept",
        "Shift",
        "Signed in",
        "Signed out",
        "Rostered",
        "Worked",
        "Away",
        "Late",
        "Early",
        "Overtime",
        "Status",
    ]
    rows = [header]
    flagged = []
    for index, record in enumerate(records, start=1):
        rows.append(
            [
                format_d(record.work_date, "%d %b"),
                Paragraph(record.staff.full_name, styles["cell"]),
                record.staff.department.code if record.staff.department else "-",
                record.shift.short_name if record.shift else "-",
                format_dt(record.sign_in_at, "%H:%M"),
                format_dt(record.sign_out_at, "%H:%M"),
                humanise_duration(record.scheduled_minutes),
                humanise_duration(record.worked_minutes),
                humanise_duration(record.break_minutes),
                humanise_duration(record.late_minutes) if record.late_minutes else "-",
                humanise_duration(record.early_departure_minutes)
                if record.early_departure_minutes
                else "-",
                humanise_duration(record.overtime_minutes)
                if record.overtime_minutes
                else "-",
                record.status.replace("_", " ").title(),
            ]
        )
        if record.status == "ABSENT" or (record.late_minutes or 0) > 0:
            flagged.append(index)

    widths = [17 * mm, 34 * mm, 14 * mm, 19 * mm, 19 * mm, 19 * mm] + [18 * mm] * 6 + [22 * mm]
    story.append(_table(rows, widths, align_right=(6, 7, 8, 9, 10, 11), highlight_rows=flagged))
    story.append(Spacer(1, 5))
    story.append(
        Paragraph(
            "Worked time excludes any period logged as away from the facility. "
            "Rows in red were late or absent.",
            styles["small"],
        )
    )

    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    buffer.seek(0)
    return buffer


# ----------------------------------------------------------------------
# Audit trail
# ----------------------------------------------------------------------
def audit_trail_pdf(entries, start_date, end_date):
    styles = _styles()
    integrity = audit.verify_chain()
    buffer = io.BytesIO()
    doc = _document(buffer, "Audit Trail")

    story = [
        Paragraph("System Audit Trail", styles["title"]),
        Paragraph(
            f"{format_d(start_date)} to {format_d(end_date)} &nbsp;|&nbsp; "
            f"{len(entries)} entries listed",
            styles["subtitle"],
        ),
    ]

    status_text = (
        f"Chain verified across {integrity['checked']} entries."
        if integrity["intact"]
        else f"INTEGRITY FAILURE: {integrity['reason']} at entry #{integrity['broken_at']}."
    )
    story.append(Paragraph(f"<b>{status_text}</b>", styles["body"]))
    story.append(Spacer(1, 6))

    rows = [["#", "Timestamp", "User", "Role", "Action", "Detail", "Digest"]]
    for entry in entries:
        rows.append(
            [
                entry.id,
                format_dt(entry.created_at, "%d %b %H:%M:%S"),
                Paragraph(entry.actor_name or "System", styles["cell"]),
                entry.actor_role or "-",
                entry.action.replace("_", " ").title(),
                Paragraph(entry.summary, styles["cell"]),
                (entry.entry_hash or "")[:12],
            ]
        )

    story.append(
        _table(
            rows,
            [12 * mm, 27 * mm, 32 * mm, 18 * mm, 38 * mm, 105 * mm, 22 * mm],
        )
    )
    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    buffer.seek(0)
    return buffer


# ----------------------------------------------------------------------
# CSV exports
# ----------------------------------------------------------------------
def _csv(header, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return io.BytesIO(buffer.getvalue().encode("utf-8-sig"))


def staff_audit_csv(start_date, end_date, department=None):
    report = analytics.workforce_report(start_date, end_date, department)
    header = [
        "Staff number",
        "Name",
        "Department",
        "Role",
        "Shifts",
        "Night shifts",
        "Weekend shifts",
        "Scheduled hours",
        "Worked hours",
        "Utilisation %",
        "Overtime hours",
        "Late arrivals",
        "Late minutes",
        "Absences",
        "Leave days",
        "Replacement shifts taken",
    ]
    rows = []
    for row in report["per_staff"]:
        staff = row["staff"]
        rows.append(
            [
                staff.staff_no,
                staff.full_name,
                staff.department.name if staff.department else "",
                staff.role_label,
                row["shifts"],
                row["nights"],
                row["weekends"],
                row["scheduled_hours"],
                row["worked_hours"],
                row["utilisation"],
                row["overtime_hours"],
                row["late_count"],
                row["late_minutes"],
                row["absences"],
                row["leave_days"],
                row["replacements_taken"],
            ]
        )
    return _csv(header, rows)


def attendance_csv(records):
    header = [
        "Date",
        "Staff number",
        "Name",
        "Department",
        "Shift",
        "Sign in",
        "Sign out",
        "Scheduled minutes",
        "Worked minutes",
        "Away minutes",
        "Late minutes",
        "Early departure minutes",
        "Overtime minutes",
        "Status",
    ]
    rows = []
    for record in records:
        rows.append(
            [
                record.work_date,
                record.staff.staff_no,
                record.staff.full_name,
                record.staff.department.name if record.staff.department else "",
                record.shift.name if record.shift else "",
                format_dt(record.sign_in_at, "%Y-%m-%d %H:%M"),
                format_dt(record.sign_out_at, "%Y-%m-%d %H:%M"),
                record.scheduled_minutes or 0,
                record.worked_minutes or 0,
                record.break_minutes or 0,
                record.late_minutes or 0,
                record.early_departure_minutes or 0,
                record.overtime_minutes or 0,
                record.status,
            ]
        )
    return _csv(header, rows)


def roster_csv(roster):
    header = ["Date", "Day", "Shift", "Window", "Staff number", "Staff", "Status", "Note"]
    rows = []
    for assignment in sorted(
        roster.assignments, key=lambda a: (a.work_date, a.shift.sort_order)
    ):
        rows.append(
            [
                assignment.work_date,
                assignment.work_date.strftime("%A"),
                assignment.shift.short_name,
                assignment.shift.window_label,
                assignment.staff.staff_no if assignment.staff else "",
                assignment.staff.full_name if assignment.staff else "UNFILLED",
                assignment.status,
                assignment.change_reason or "",
            ]
        )
    return _csv(header, rows)


def audit_csv(entries):
    header = [
        "ID",
        "Timestamp",
        "User",
        "Role",
        "Action",
        "Entity",
        "Entity ID",
        "Summary",
        "Previous values",
        "New values",
        "IP address",
        "Entry digest",
    ]
    rows = []
    for entry in entries:
        rows.append(
            [
                entry.id,
                format_dt(entry.created_at, "%Y-%m-%d %H:%M:%S"),
                entry.actor_name,
                entry.actor_role,
                entry.action,
                entry.entity_type or "",
                entry.entity_id or "",
                entry.summary,
                entry.old_value or "",
                entry.new_value or "",
                entry.ip_address or "",
                entry.entry_hash or "",
            ]
        )
    return _csv(header, rows)
