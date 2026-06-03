"""Pembuat laporan PDF hasil pemetaan TTP (MITRE ATT&CK).

Menghasilkan dokumen PDF ringkas berisi metadata laporan, daftar taktik &
teknik yang terpetakan, ringkasan STIX, dan cuplikan teks laporan.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from html import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ACCENT = colors.HexColor("#ff7a2f")
ACCENT_2 = colors.HexColor("#2bb3a3")
INK = colors.HexColor("#1f2a2e")
MUTED = colors.HexColor("#516067")
LIGHT = colors.HexColor("#f1f1ee")


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="ReportTitle", parent=styles["Title"],
        fontSize=20, textColor=INK, spaceAfter=2,
    ))
    styles.add(ParagraphStyle(
        name="Sub", parent=styles["Normal"],
        fontSize=10, textColor=MUTED, spaceAfter=10,
    ))
    styles.add(ParagraphStyle(
        name="H2", parent=styles["Heading2"],
        fontSize=13, textColor=INK, spaceBefore=12, spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name="Body", parent=styles["Normal"],
        fontSize=9.5, textColor=INK, leading=14, alignment=TA_LEFT,
    ))
    styles.add(ParagraphStyle(
        name="Cell", parent=styles["Normal"],
        fontSize=9, textColor=INK, leading=12,
    ))
    return styles


def _table(header: list[str], rows: list[list[str]], col_widths: list[float], styles) -> Table:
    data = [[Paragraph(f"<b>{escape(h)}</b>", styles["Cell"]) for h in header]]
    for row in rows:
        data.append([Paragraph(escape(str(c)), styles["Cell"]) for c in row])

    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d6d6d2")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def build_pdf_report(
    report_id: str,
    report_text: str,
    tactics: list[dict],
    techniques: list[dict],
    stix_bundle: dict | None = None,
    attck_techniques: dict | None = None,
    tactic_evidence: dict | None = None,
    technique_evidence: dict | None = None,
) -> bytes:
    """Bangun laporan PDF dan kembalikan sebagai bytes.

    tactics            : [{"id": "TA0001", "name": "Initial Access"}, ...]
    techniques         : [{"id": "T1566", "name": "Phishing"}, ...]
    tactic_evidence    : {"TA0001": "kalimat rujukan ...", ...}
    technique_evidence : {"T1566": "kalimat rujukan ...", ...}
    """
    tactic_evidence = tactic_evidence or {}
    technique_evidence = technique_evidence or {}
    styles = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"Laporan Pemetaan TTP - {report_id}",
        author="TTP Mapping System",
    )

    attck_techniques = attck_techniques or {}
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    stix_objs = len((stix_bundle or {}).get("objects", []))

    story = []
    story.append(Paragraph("Laporan Pemetaan TTP", styles["ReportTitle"]))
    story.append(Paragraph(
        "Pemetaan laporan CTI ke MITRE ATT&amp;CK (Tactics &amp; Techniques)",
        styles["Sub"],
    ))

    # Metadata ringkas
    meta_rows = [
        ["Report ID", report_id or "-"],
        ["Dibuat", generated],
        ["Jumlah Taktik", str(len(tactics))],
        ["Jumlah Teknik", str(len(techniques))],
        ["Objek STIX", str(stix_objs)],
    ]
    story.append(_table(["Atribut", "Nilai"], meta_rows, [45 * mm, 129 * mm], styles))
    story.append(Spacer(1, 6))

    # Taktik + kalimat rujukan
    story.append(Paragraph("Tactics", styles["H2"]))
    if tactics:
        rows = []
        for t in tactics:
            tid = t.get("id", "")
            rows.append([tid, t.get("name", ""), tactic_evidence.get(tid, "-")])
        story.append(_table(
            ["ID", "Nama Taktik", "Kalimat Rujukan"],
            rows, [22 * mm, 40 * mm, 112 * mm], styles,
        ))
    else:
        story.append(Paragraph("Tidak ada taktik terpetakan.", styles["Body"]))

    # Teknik + kalimat rujukan
    story.append(Paragraph("Techniques", styles["H2"]))
    if techniques:
        rows = []
        for t in techniques:
            tid = t.get("id", "")
            name = t.get("name", "") or attck_techniques.get(tid, {}).get("name", "")
            rows.append([tid, name, technique_evidence.get(tid, "-")])
        story.append(_table(
            ["ID", "Nama Teknik", "Kalimat Rujukan"],
            rows, [24 * mm, 45 * mm, 105 * mm], styles,
        ))
    else:
        story.append(Paragraph("Tidak ada teknik terpetakan.", styles["Body"]))

    story.append(Spacer(1, 14))
    story.append(Paragraph(
        "Dihasilkan otomatis oleh TTP Mapping System. Hasil pemetaan berbasis LLM "
        "bersifat bantuan analis dan perlu diverifikasi oleh analis CTI.",
        styles["Sub"],
    ))

    doc.build(story)
    return buffer.getvalue()
