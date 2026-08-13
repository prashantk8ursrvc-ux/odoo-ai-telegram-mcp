"""
pdf_generator.py
────────────────
Premium Branded PDF Report Generator using ReportLab.
Produces Claude-quality, pixel-drawn rich PDFs with:
• Full-width deep navy header banner with company wordmark & timestamp
• Accent blue left-border executive overview block
• KPI metric cards with navy-bordered tiles
• Section titles with Brand Gold underline rule
• Tables with dark navy column headers, alternating shading, colored status dots
• Deep navy footer rule with full brand signature, contact, and page numbering
"""

from __future__ import annotations

import os
import datetime
import re
from typing import List, Dict, Any, Optional, Tuple

from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer
)
from reportlab.pdfgen import canvas

import brand_config as bc

# ─── Colors ───────────────────────────────────────────────────────────────────

NAVY_DARK = HexColor("#0F172A")      # Deep Slate Navy Header
NAVY_HEADER = HexColor("#1E293B")    # Section / Table Header BG
BLUE_ACCENT = HexColor("#2563EB")    # Electric Blue Accent
LIGHT_BG = HexColor("#F8FAFC")       # Card Background
ALT_ROW_BG = HexColor("#F1F5F9")     # Table Alternating Row
TEXT_DARK = HexColor("#0F172A")      # Primary Body Text
TEXT_MUTED = HexColor("#64748B")     # Secondary Text
LINE_COLOR = HexColor("#E2E8F0")     # Dividers / Borders

BADGE_GREEN_FG = HexColor("#166534")
BADGE_AMBER_FG = HexColor("#92400E")
BADGE_RED_FG = HexColor("#991B1B")

BADGE_GREEN_BG = HexColor("#DCFCE7")
BADGE_AMBER_BG = HexColor("#FEF3C7")
BADGE_RED_BG   = HexColor("#FEE2E2")

# ─── Dynamic Column Width Calculator ─────────────────────────────────────────

def calculate_dynamic_pdf_col_widths(
    headers: List[str],
    rows: List[List[Any]],
    printable_width: float = 540.0
) -> List[float]:
    """
    100% dynamic PDF column width calculator.
    Measures exact character string length of headers and data cells.
    Allocates printable_width (pt) dynamically with zero hardcoding.
    """
    if not headers:
        return []

    num_cols = len(headers)
    required_widths = []

    for c_idx, h in enumerate(headers):
        max_chars = len(str(h))
        for row in rows:
            if c_idx < len(row):
                max_chars = max(max_chars, len(str(row[c_idx])))
        req_pt = max(28.0, (max_chars * 6.0) + 16.0)
        required_widths.append(req_pt)

    total_required = sum(required_widths)

    if total_required <= printable_width:
        extra = printable_width - total_required
        weights = [w / (total_required or 1.0) for w in required_widths]
        final_widths = [required_widths[i] + (weights[i] * extra) for i in range(num_cols)]
    else:
        scale = printable_width / total_required
        final_widths = [max(25.0, w * scale) for w in required_widths]

    diff = printable_width - sum(final_widths)
    if final_widths and abs(diff) > 0.01:
        max_i = final_widths.index(max(final_widths))
        final_widths[max_i] += diff

    return final_widths


# ─── Premium Canvas Renderer ──────────────────────────────────────────────────

class NumberedCanvas(canvas.Canvas):
    """Full-bleed page decorator: header banner + footer rule drawn via canvas."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []
        self._pending_header_data: dict = {}

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_page_chrome(num_pages)
            super().showPage()
        super().save()

    def _draw_page_chrome(self, page_count: int):
        self.saveState()
        W, H = 612, 792   # letter size

        # ── 1. Deep Navy Full-Width Header Banner ──────────────────────────
        banner_h = 70
        banner_top = H - banner_h
        self.setFillColor(HexColor("#0F172A"))
        self.rect(0, banner_top, W, banner_h, fill=1, stroke=0)

        # Left: Company Wordmark
        self.setFont("Helvetica-Bold", 17)
        self.setFillColor(HexColor("#FFFFFF"))
        self.drawString(36, H - 30, bc.COMPANY_NAME)

        # Company subtitle in accent blue next to wordmark
        self.setFont("Helvetica", 9)
        self.setFillColor(HexColor("#93C5FD"))   # light blue
        wm_bbox_w = len(bc.COMPANY_NAME) * 10.5
        self.drawString(36 + wm_bbox_w + 6, H - 29, bc.COMPANY_SUBTITLE)

        # Right: timestamp (top right)
        ts_str = self._pending_header_data.get("timestamp", "")
        if ts_str:
            self.setFont("Helvetica", 8.5)
            self.setFillColor(HexColor("#94A3B8"))
            self.drawRightString(W - 36, H - 28, ts_str)

        # Report title in header (line 2)
        report_title = self._pending_header_data.get("title", "")
        if report_title:
            self.setFont("Helvetica-Bold", 11)
            self.setFillColor(HexColor("#E2E8F0"))
            self.drawString(36, H - 50, report_title)

        # 4px Accent Blue stripe at bottom of banner
        self.setFillColor(HexColor("#2563EB"))
        self.rect(0, banner_top - 4, W, 4, fill=1, stroke=0)

        # ── 2. Premium Footer ──────────────────────────────────────────────
        footer_y = 46
        # 2px navy rule
        self.setStrokeColor(HexColor("#1B2A4E"))
        self.setLineWidth(2)
        self.line(36, footer_y, W - 36, footer_y)

        # Left: company signature
        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(HexColor("#1B2A4E"))
        self.drawString(36, footer_y - 10, f"{bc.COMPANY_NAME} / {bc.COMPANY_TAGLINE}")

        # Left sub-line: contact info
        self.setFont("Helvetica", 7)
        self.setFillColor(HexColor("#64748B"))
        contact = f"{bc.SIGNATURE_ADDRESS}  •  {bc.SIGNATURE_EMAIL}  •  {bc.SIGNATURE_PHONE}"
        self.drawString(36, footer_y - 20, contact)

        # Right: Page X of Y
        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(HexColor("#2563EB"))
        self.drawRightString(W - 36, footer_y - 10, f"Page {self._pageNumber} of {page_count}")

        self.restoreState()


# ─── Premium PDF Generator ────────────────────────────────────────────────────

def generate_claude_style_pdf(
    title: str,
    subtitle: str = "",
    description: str = "",
    kpi_cards: Optional[List[Dict[str, str]]] = None,
    table_headers: Optional[List[str]] = None,
    table_rows: Optional[List[List[Any]]] = None,
    col_widths: Optional[List[float]] = None,
    output_path: str = "report.pdf",
    timestamp: str = "",
) -> str:
    """Generate a premium branded PDF with Claude-quality design."""
    from reportlab.platypus import HRFlowable, KeepTogether

    if not timestamp:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # We draw the header/footer via canvas, so topMargin must clear the banner (70px + 4px stripe + 12px gap = 88pt)
    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=92,       # clears the 70pt banner + 4pt stripe + gap
        bottomMargin=68,    # clears footer
    )

    printable_width = 612 - 72   # 540 pt

    styles = getSampleStyleSheet()

    # Typography
    s_title    = ParagraphStyle("PDFTitle",  parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=14,  leading=18, textColor=HexColor("#0F172A"), spaceAfter=2)
    s_sub      = ParagraphStyle("PDFSub",    parent=styles["Normal"], fontName="Helvetica",      fontSize=9.5, leading=13, textColor=HexColor("#475569"), spaceAfter=8)
    s_exec_h   = ParagraphStyle("ExecH",     parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=9,   leading=12, textColor=HexColor("#1E3A5F"))
    s_exec_b   = ParagraphStyle("ExecB",     parent=styles["Normal"], fontName="Helvetica",      fontSize=8.5, leading=12, textColor=HexColor("#334155"))
    s_sec      = ParagraphStyle("PDFSec",    parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=11,  leading=14, textColor=HexColor("#0F172A"), spaceBefore=6)
    s_kpi_val  = ParagraphStyle("KPIVal",    parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=14,  leading=17, textColor=HexColor("#0F172A"), alignment=1)
    s_th       = ParagraphStyle("TH",        parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=8.5, leading=11, textColor=HexColor("#FFFFFF"))
    s_td       = ParagraphStyle("TD",        parent=styles["Normal"], fontName="Helvetica",      fontSize=8.5, leading=11, textColor=HexColor("#1E293B"))
    s_td_b     = ParagraphStyle("TDB",       parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=8.5, leading=11, textColor=HexColor("#0F172A"))
    s_td_green = ParagraphStyle("TDG",       parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=8,   leading=11, textColor=HexColor("#166534"))
    s_td_amber = ParagraphStyle("TDA",       parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=8,   leading=11, textColor=HexColor("#92400E"))
    s_td_red   = ParagraphStyle("TDR",       parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=8,   leading=11, textColor=HexColor("#991B1B"))

    # Pass header metadata into canvas decorator via a shared dict
    # We inject it before build
    _canvas_meta = {"title": title, "timestamp": timestamp}

    story = []

    # ── 1. Report Title + Subtitle (below the drawn banner) ────────────────
    if title:
        story.append(Paragraph(f"<b>{title}</b>", s_title))
    if subtitle:
        story.append(Paragraph(subtitle, s_sub))

    # ── 2. Executive Summary Block ──────────────────────────────────────────
    if description:
        exec_rows = [
            [Paragraph("<b>Executive Summary</b>", s_exec_h)],
            [Paragraph(description, s_exec_b)],
        ]
        exec_tbl = Table(exec_rows, colWidths=[printable_width])
        exec_tbl.setStyle(TableStyle([
            ("BACKGROUND",  (0, 0), (-1, -1), HexColor("#EFF6FF")),
            ("LINELEFT",    (0, 0), (-1, -1), 4, HexColor("#2563EB")),
            ("BOX",         (0, 0), (-1, -1), 0.5, HexColor("#BFDBFE")),
            ("TOPPADDING",  (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING",(0,0), (-1, -1), 7),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING",(0, 0), (-1, -1), 8),
        ]))
        story.append(exec_tbl)
        story.append(Spacer(1, 10))

    # ── 3. KPI Metric Cards ─────────────────────────────────────────────────
    if kpi_cards:
        n = len(kpi_cards)
        cw = printable_width / n
        cells = []
        for card in kpi_cards:
            val = card.get("value", "")
            lbl = card.get("label", "")
            cells.append(
                Paragraph(
                    f'<b><font size=14>{val}</font></b><br/>'
                    f'<font size=7.5 color="#64748B">{lbl.upper()}</font>',
                    s_kpi_val
                )
            )
        kpi_tbl = Table([cells], colWidths=[cw] * n)
        kpi_tbl.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, -1), HexColor("#F0F9FF")),
            ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
            ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",   (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 10),
            ("BOX",          (0, 0), (-1, -1), 1.5, HexColor("#1B2A4E")),
            ("INNERGRID",    (0, 0), (-1, -1), 0.75, HexColor("#CBD5E1")),
        ]))
        story.append(kpi_tbl)
        story.append(Spacer(1, 14))

    # ── 4. Section Header + Data Table ─────────────────────────────────────
    if table_headers and table_rows:
        story.append(Paragraph("<b>Data Breakdown</b>", s_sec))
        story.append(HRFlowable(
            width="100%", thickness=1.5,
            color=HexColor("#C9A24B"),      # brand gold
            spaceBefore=3, spaceAfter=8
        ))

        if not col_widths:
            col_widths = calculate_dynamic_pdf_col_widths(table_headers, table_rows, printable_width)

        # Header row
        hdr_cells = [Paragraph(f"<b>{h}</b>", s_th) for h in table_headers]
        table_data = [hdr_cells]

        # Data rows
        for r_idx, row in enumerate(table_rows):
            formatted = []
            for c_idx, cell in enumerate(row):
                txt = str(cell)
                cu  = txt.upper()

                if any(w in cu for w in ("PAID", "POSTED", "ONLINE", "ACTIVE", "CONFIRMED", "SUCCESS", "DONE", "COMPLETED")):
                    p = Paragraph(f"<b>● {txt}</b>", s_td_green)
                elif any(w in cu for w in ("PENDING", "DRAFT", "WARNING", "IN PROGRESS", "SYNCING", "PARTIAL")):
                    p = Paragraph(f"<b>● {txt}</b>", s_td_amber)
                elif any(w in cu for w in ("OVERDUE", "CANCELLED", "FAILED", "OFFLINE", "ALERT", "UNPAID", "NOT PAID")):
                    p = Paragraph(f"<b>● {txt}</b>", s_td_red)
                elif c_idx == 0:
                    p = Paragraph(f"<b>{txt}</b>", s_td_b)
                else:
                    p = Paragraph(txt, s_td)
                formatted.append(p)
            table_data.append(formatted)

        tbl = Table(table_data, colWidths=col_widths, repeatRows=1)

        ts = [
            # Header: dark navy bg, white text
            ("BACKGROUND",    (0, 0), (-1, 0), HexColor("#1B2A4E")),
            ("ROWBACKGROUNDS",(0, 1), (-1, -1), [HexColor("#FFFFFF"), HexColor("#F8FAFC")]),
            ("ALIGN",         (0, 0), (-1, -1), "LEFT"),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",    (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING",   (0, 0), (-1, -1), 7),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 7),
            # Header bottom border
            ("LINEBELOW",     (0, 0), (-1, 0), 2, HexColor("#2563EB")),
            # All borders
            ("GRID",          (0, 0), (-1, -1), 0.4, HexColor("#E2E8F0")),
            # Outer border
            ("BOX",           (0, 0), (-1, -1), 1, HexColor("#CBD5E1")),
        ]
        tbl.setStyle(TableStyle(ts))
        story.append(tbl)

    # ── Build with custom canvas that draws header/footer ───────────────────
    class _MetaCanvas(NumberedCanvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pending_header_data = _canvas_meta

    doc.build(story, canvasmaker=_MetaCanvas)
    return output_path


# ─── Sample Pending Invoices Generator ───────────────────────────────────────

def render_sample_pending_invoices_pdf(output_path: str = "sample_pending_invoices_claude.pdf") -> str:
    """Generate a sample Pending Invoices & Revenue Audit PDF."""
    kpi_cards = [
        {"label": "Total Outstanding", "value": "$142,850.00"},
        {"label": "Pending Invoices", "value": "24 Invoices"},
        {"label": "Overdue (>30 Days)", "value": "6 Invoices"},
        {"label": "Active Customers", "value": "18 Accounts"},
    ]

    table_headers = ["Invoice #", "Customer / Partner", "Invoice Date", "Due Date", "Amount ($)", "Status"]
    col_widths = [90, 170, 80, 80, 60, 60]

    table_rows = [
        ["INV/2026/00102", "Acme Corporation", "2026-06-12", "2026-07-12", "14,500.00", "OVERDUE"],
        ["INV/2026/00105", "Global Tech LLC", "2026-06-15", "2026-07-15", "8,200.00", "OVERDUE"],
        ["INV/2026/00108", "Apex Logistics Ltd", "2026-06-20", "2026-07-20", "22,100.00", "OVERDUE"],
        ["INV/2026/00112", "Horizon Energy Corp", "2026-07-01", "2026-07-31", "19,450.00", "PENDING"],
        ["INV/2026/00115", "Vanguard Financial", "2026-07-05", "2026-08-05", "12,600.00", "PENDING"],
        ["INV/2026/00118", "Starlight Systems", "2026-07-08", "2026-08-08", "5,300.00", "PENDING"],
        ["INV/2026/00122", "Quantum Dynamics", "2026-07-10", "2026-08-10", "11,800.00", "PENDING"],
        ["INV/2026/00125", "Example Customer Ltd.", "2026-07-12", "2026-08-12", "9,750.00", "PENDING"],
        ["INV/2026/00128", "Nexus Innovations", "2026-07-15", "2026-08-15", "6,400.00", "PENDING"],
        ["INV/2026/00130", "Summit Media Group", "2026-07-18", "2026-08-18", "15,250.00", "PENDING"],
        ["INV/2026/00133", "Atlas Retail Ventures", "2026-07-20", "2026-08-20", "17,500.00", "PENDING"],
    ]

    return generate_claude_style_pdf(
        title="Odoo ERP — Pending Invoices & Outstanding Balances",
        subtitle="Comprehensive financial breakdown of unpaid customer invoices and aging analysis.",
        kpi_cards=kpi_cards,
        table_headers=table_headers,
        table_rows=table_rows,
        col_widths=col_widths,
        output_path=output_path,
    )


def parse_table_data(text: str) -> Tuple[List[str], List[List[str]]]:
    """
    Extracts headers and rows from either Markdown tables or HTML tables.
    Returns (headers, rows).
    """
    if not text:
        return [], []

    # 1. Try HTML Table Parsing
    if "<tr" in text.lower():
        try:
            tr_matches = re.findall(r'<tr[^>]*>(.*?)</tr>', text, flags=re.DOTALL | re.IGNORECASE)
            headers = []
            rows = []
            for tr in tr_matches:
                ths = re.findall(r'<th[^>]*>(.*?)</th>', tr, flags=re.DOTALL | re.IGNORECASE)
                tds = re.findall(r'<td[^>]*>(.*?)</td>', tr, flags=re.DOTALL | re.IGNORECASE)
                
                if ths:
                    clean_ths = [re.sub(r'<[^>]+>', '', cell).strip() for cell in ths]
                    if clean_ths and not headers:
                        headers = clean_ths
                elif tds:
                    clean_tds = [re.sub(r'<[^>]+>', '', cell).strip() for cell in tds]
                    if clean_tds:
                        if not headers:
                            headers = clean_tds
                        else:
                            rows.append(clean_tds)
            
            if headers and rows:
                return headers, rows
        except Exception:
            pass

    # 2. Try Markdown Table Parsing
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    table_lines = [l for l in lines if l.startswith("|") and l.endswith("|")]
    if len(table_lines) >= 3:
        try:
            headers = [c.strip() for c in table_lines[0].split("|")[1:-1]]
            rows = []
            for tl in table_lines[2:]:
                cells = [c.strip() for c in tl.split("|")[1:-1]]
                if cells:
                    rows.append(cells)
            if headers and rows:
                return headers, rows
        except Exception:
            pass

    return [], []


def _generate_filename_with_llm(text: str) -> Optional[str]:
    """Uses a fast LLM call to derive a concise Title_Case filename e.g. Customer_Payments_2025.pdf."""
    try:
        import httpx
        api_key = os.getenv("OPENROUTER_API_KEY")
        if api_key:
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {
                "model": os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a precise file naming assistant for an ERP. Generate a concise, human-friendly Title_Case filename ending in .pdf (e.g., Customer_Payments_2025.pdf, Unpaid_Invoices_May_2025.pdf, Project_Tasks_2026.pdf). CRITICAL: If summary describes paid invoices or revenue, DO NOT call it Unpaid; name it Customer_Payments_YYYY.pdf or Revenue_Summary_YYYY.pdf. Output ONLY the raw filename string."
                    },
                    {"role": "user", "content": f"Summary text: {text[:400]}"}
                ],
                "temperature": 0.1,
                "max_tokens": 20
            }
            res = httpx.post(url, headers=headers, json=payload, timeout=3.0)
            if res.status_code == 200:
                data = res.json()
                fname = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip().strip('"`\'')
                if fname.endswith(".pdf") and " " not in fname and len(fname) > 5:
                    return fname
    except Exception:
        pass
    return None


def _derive_report_metadata(text: str, default_title: str) -> Tuple[str, str, str]:
    """
    Derives natural report title, clean Title_Cased filename, and executive summary text from AI response.
    First tries a fast LLM call for dynamic filenames like Customer_Payments_2025.pdf,
    falling back to smart regex rules.
    """
    # 1. Try simple LLM filename generation first
    llm_fname = _generate_filename_with_llm(text)
    if llm_fname:
        clean_name = llm_fname.replace(".pdf", "")
        report_title = f"Odoo ERP — {clean_name.replace('_', ' ')}"
        filename = llm_fname
        
        intro_lines = [l.strip() for l in text.split("\n") if l.strip() and not l.strip().startswith("|") and not l.strip().startswith("<")]
        raw_summary = " ".join(intro_lines[:3]) if intro_lines else "Automated system audit report generated for detailed record breakdown."
        exec_summary = re.sub(r'<[^>]+>', '', raw_summary).strip()
        return report_title, filename, exec_summary

    t_lower = text.lower()
    
    # Extract year or month if present in text
    year_match = re.search(r'\b(202\d)\b', text)
    year_str = year_match.group(1) if year_match else ""
    
    month_match = re.search(r'\b(january|february|march|april|may|june|july|august|september|october|november|december)\b', t_lower)
    month_str = month_match.group(1).capitalize() if month_match else ""

    period = f"{month_str}_{year_str}".strip("_") if (month_str or year_str) else year_str

    # 1. Topic Identification based on strong domain keywords (ORDERED BY SPECIFICITY)
    if re.search(r'\b(timesheet|timesheets|hours|hours logged|time entry)\b', t_lower):
        base_name = f"Timesheet_Summary_{period}".strip("_") if period else "Timesheet_Summary"
        report_title = f"Odoo ERP — {base_name.replace('_', ' ')}"

    elif re.search(r'\b(unpaid|outstanding|not paid|overdue)\b', t_lower) and re.search(r'\b(invoice|invoices|bill|bills)\b', t_lower):
        base_name = f"Unpaid_Invoices_{period}".strip("_") if period else "Unpaid_Invoices_Summary"
        report_title = f"Odoo ERP — {base_name.replace('_', ' ')}"

    elif re.search(r'\b(payment|payments|received|paid|revenue|inbound)\b', t_lower):
        base_name = f"Customer_Payments_{period}".strip("_") if period else "Customer_Payments_Summary"
        report_title = f"Odoo ERP — {base_name.replace('_', ' ')}"

    elif re.search(r'\b(invoice|invoices|billing|bill|bills)\b', t_lower):
        base_name = f"Invoices_Summary_{period}".strip("_") if period else "Invoices_Summary"
        report_title = f"Odoo ERP — {base_name.replace('_', ' ')}"

    elif re.search(r'\b(task|tasks|project task|todo)\b', t_lower):
        base_name = f"Project_Tasks_{period}".strip("_") if period else "Project_Tasks_Summary"
        report_title = f"Odoo ERP — {base_name.replace('_', ' ')}"

    elif re.search(r'\b(active user|system user|user list|registered user|users count|users)\b', t_lower) and not re.search(r'\b(payment|invoice|task|timesheet)\b', t_lower):
        base_name = "Active_System_Users"
        report_title = "Odoo ERP — Active System Users Audit"

    elif re.search(r'\b(ticket|tickets|helpdesk|support ticket)\b', t_lower):
        base_name = f"Helpdesk_Tickets_{period}".strip("_") if period else "Helpdesk_Support_Tickets"
        report_title = f"Odoo ERP — {base_name.replace('_', ' ')}"

    elif re.search(r'\b(sale|sales|order|orders|quotation|quotations)\b', t_lower):
        base_name = f"Sales_Orders_{period}".strip("_") if period else "Sales_Orders_Summary"
        report_title = f"Odoo ERP — {base_name.replace('_', ' ')}"

    elif re.search(r'\b(product|products|inventory|stock)\b', t_lower):
        base_name = f"Inventory_Products_{period}".strip("_") if period else "Inventory_Products_Summary"
        report_title = f"Odoo ERP — {base_name.replace('_', ' ')}"

    else:
        first_heading = ""
        for line in text.split("\n"):
            line_str = line.strip().lstrip("#*📄 ").strip()
            if line_str and len(line_str) > 5 and not line_str.startswith("<"):
                first_heading = line_str
                break
        
        if first_heading:
            clean_words = [w.capitalize() for w in re.sub(r'[^a-zA-Z0-9\s]+', '', first_heading).split() if w.lower() not in ("i", "have", "retrieved", "compiled", "below", "is", "a", "the", "list", "of", "summary")]
            base_name = "_".join(clean_words[:4]) if clean_words else "System_Audit_Report"
        else:
            base_name = "System_Audit_Report"

        report_title = f"Odoo ERP — {base_name.replace('_', ' ')}"

    filename = f"{base_name}.pdf"

    intro_lines = []
    if "<table" in text.lower():
        intro_part = re.split(r'<table[^>]*>', text, flags=re.IGNORECASE)[0]
        intro_lines = [l.strip() for l in intro_part.split("\n") if l.strip() and not l.strip().startswith("<")]
    else:
        for line in text.split("\n"):
            line_str = line.strip()
            if line_str.startswith("|") and line_str.endswith("|"):
                break
            if line_str:
                intro_lines.append(line_str)

    # Clean HTML tags out of exec summary
    raw_summary = " ".join(intro_lines[:3]) if intro_lines else "Automated system audit report generated for detailed record breakdown."
    exec_summary = re.sub(r'<[^>]+>', '', raw_summary).strip()

    return report_title, filename, exec_summary


def _calculate_dynamic_kpis(headers: List[str], rows: List[List[Any]]) -> List[Dict[str, str]]:
    """
    Analyzes table headers and data rows to dynamically compute relevant financial sums,
    total hours, overdue counts, and record metrics for the PDF KPI summary cards.
    """
    kpis = []
    num_records = len(rows)
    kpis.append({"label": "Total Records", "value": f"{num_records} Items"})

    if not headers or not rows:
        return kpis

    amount_col_idx = -1
    hours_col_idx = -1
    status_col_idx = -1

    for c_idx, h in enumerate(headers):
        h_lower = str(h).lower()
        if any(k in h_lower for k in ("total", "amount", "balance", "price", "subtotal")):
            amount_col_idx = c_idx
        elif any(k in h_lower for k in ("hours", "duration", "time", "qty", "quantity")):
            hours_col_idx = c_idx
        elif "status" in h_lower or "state" in h_lower:
            status_col_idx = c_idx

    # Compute financial sums
    if amount_col_idx != -1:
        total_amount = 0.0
        currency_symbol = "$"
        has_amount = False
        for r in rows:
            if amount_col_idx < len(r):
                val_str = str(r[amount_col_idx]).strip()
                symbol_match = re.search(r'([$€£₹])', val_str)
                if symbol_match:
                    currency_symbol = symbol_match.group(1)
                
                num_str = re.sub(r'[^0-9.]', '', val_str)
                if num_str:
                    try:
                        total_amount += float(num_str)
                        has_amount = True
                    except ValueError:
                        pass
        if has_amount:
            kpis.append({"label": "Total Amount", "value": f"{currency_symbol}{total_amount:,.2f}"})

    # Compute total hours / quantities
    if hours_col_idx != -1:
        total_hours = 0.0
        has_hours = False
        for r in rows:
            if hours_col_idx < len(r):
                val_str = str(r[hours_col_idx]).strip()
                num_str = re.sub(r'[^0-9.]', '', val_str)
                if num_str:
                    try:
                        total_hours += float(num_str)
                        has_hours = True
                    except ValueError:
                        pass
        if has_hours:
            kpis.append({"label": "Total Hours", "value": f"{total_hours:,.2f} hrs"})

    # Compute status count (e.g. Overdue or Pending)
    if status_col_idx != -1:
        overdue_count = 0
        unpaid_count = 0
        for r in rows:
            if status_col_idx < len(r):
                st = str(r[status_col_idx]).upper()
                if "OVERDUE" in st or "FAILED" in st or "ALERT" in st:
                    overdue_count += 1
                elif "NOT PAID" in st or "UNPAID" in st or "PENDING" in st:
                    unpaid_count += 1

        if overdue_count > 0:
            kpis.append({"label": "Overdue Count", "value": f"{overdue_count} Records"})
        elif unpaid_count > 0:
            kpis.append({"label": "Outstanding", "value": f"{unpaid_count} Pending"})

    if len(kpis) < 3:
        kpis.append({"label": "Data Source", "value": "Odoo Enterprise"})
    if len(kpis) < 4:
        kpis.append({"label": "Audit Type", "value": "Executive Report"})

    return kpis[:4]


def auto_generate_pdf_if_needed(text: str, title: str = "Odoo ERP — Executive Report") -> Tuple[bool, Optional[str], str]:
    """
    Checks if data is large (>=8 table rows).
    Generates a Claude-style dark navy/blue PDF document with Executive Summary and returns (has_pdf, pdf_path, summary).
    """
    if not text:
        return False, None, text

    headers, rows = parse_table_data(text)

    # Trigger PDF generation for 8+ rows
    if len(rows) < 8 or not headers:
        return False, None, text

    try:
        report_title, filename, description = _derive_report_metadata(text, title)
        
        os.makedirs("temp_reports", exist_ok=True)
        out_path = os.path.join("temp_reports", filename)
        
        kpi_cards = _calculate_dynamic_kpis(headers, rows)
        
        generate_claude_style_pdf(
            title=report_title,
            subtitle="Comprehensive Odoo ERP system audit and record breakdown.",
            description=description,
            kpi_cards=kpi_cards,
            table_headers=headers,
            table_rows=rows,
            output_path=out_path
        )
        
        # Clean raw table tags out of text while preserving the conversational intro/outro notes
        clean_text = text
        has_mention = any(phrase in clean_text.lower() for phrase in ("pdf report", "pdf document", "report attached", "attached as a pdf", "attached below", "report containing"))

        if "<table" in clean_text.lower():
            replacement_note = f"\n\n📄 <i>(Detailed breakdown of {len(rows)} records is attached below as a PDF report)</i>\n\n" if not has_mention else "\n\n"
            clean_text = re.sub(r'<table[^>]*>.*?</table>', replacement_note, clean_text, flags=re.DOTALL | re.IGNORECASE)
        else:
            lines = clean_text.split("\n")
            non_table = [l for l in lines if not (l.strip().startswith("|") and l.strip().endswith("|"))]
            clean_text = "\n".join(non_table).strip()
            if not has_mention:
                clean_text += f"\n\n📄 <i>(Detailed breakdown of {len(rows)} records is attached below as a PDF report)</i>"

        if not clean_text.strip() or len(clean_text.strip()) < 15:
            clean_text = f"📄 <b>{report_title}</b>\n<i>I have compiled the requested {len(rows)} records into the attached PDF report.</i>"

        return True, out_path, clean_text
    except Exception:
        pass

    return False, None, text


