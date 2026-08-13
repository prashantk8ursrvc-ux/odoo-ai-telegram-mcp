"""
brand_renderer.py
─────────────────
Pure Python PIL Image Rendering Engine implementing the configurable Brand Specification.

Creates pixel-perfect, 1080px wide PNG images with:
• White background (never transparent)
• Header wordmark & 3px navy rule
• Custom Typography (Verdana / DejaVu)
• Styled Data Tables with alternating rows and headers
• Status Pills (Green, Amber, Red)
• Alert Callout Boxes (Red left border, light red background)
• Brand Footer and Signature
"""

from __future__ import annotations

import os
import datetime
from typing import List, Dict, Any, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont

import brand_config as bc

# ─── Font Helper ─────────────────────────────────────────────────────────────

def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_paths = []
    if bold:
        font_paths = [
            "C:/Windows/Fonts/verdanab.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/Library/Fonts/Verdana Bold.ttf",
        ]
    else:
        font_paths = [
            "C:/Windows/Fonts/verdana.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/Library/Fonts/Verdana.ttf",
        ]

    for path in font_paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


# ─── Helper Utilities ────────────────────────────────────────────────────────

def calculate_smart_col_widths(
    headers: List[str],
    rows: List[List[Any]],
    total_width: float = 984.0,
    draw: Optional[ImageDraw.ImageDraw] = None,
    font_header: Any = None,
    font_body: Any = None,
    font_pill: Any = None
) -> List[int]:
    """
    100% Dynamic column width calculator.
    Measures the exact font pixel width of every header, cell, and status pill using PIL.
    No hardcoded column names or static rules!
    """
    if not headers:
        return []

    num_cols = len(headers)
    required_widths = []

    # Fallback dummy draw context if not provided
    if draw is None:
        dummy_img = Image.new("RGB", (1, 1))
        draw = ImageDraw.Draw(dummy_img)
    if font_header is None:
        font_header = get_font(15, bold=True)
    if font_body is None:
        font_body = get_font(15, bold=False)
    if font_pill is None:
        font_pill = get_font(12, bold=True)

    cell_padding = 24  # 12px left + 12px right padding

    for c_idx, h in enumerate(headers):
        # 1. Measure header text pixel width
        h_bbox = draw.textbbox((0, 0), str(h), font=font_header)
        max_px = h_bbox[2] - h_bbox[0]

        # 2. Measure every row cell in this column
        for row in rows:
            if c_idx < len(row):
                cell_val = row[c_idx]
                if isinstance(cell_val, dict) and cell_val.get("type") == "pill":
                    p_text = str(cell_val.get("text", ""))
                    p_bbox = draw.textbbox((0, 0), p_text, font=font_pill)
                    cell_px = (p_bbox[2] - p_bbox[0]) + 24  # pill text + internal pill padding
                else:
                    cell_str = str(cell_val)
                    c_bbox = draw.textbbox((0, 0), cell_str, font=font_body)
                    cell_px = c_bbox[2] - c_bbox[0]
                max_px = max(max_px, cell_px)

        required_widths.append(max_px + cell_padding)

    total_required = sum(required_widths)

    if total_required <= total_width:
        extra = total_width - total_required
        weights = [w / (total_required or 1.0) for w in required_widths]
        final_widths = [int(required_widths[i] + (weights[i] * extra)) for i in range(num_cols)]
    else:
        scale = total_width / total_required
        final_widths = [max(35, int(w * scale)) for w in required_widths]

    diff = int(total_width) - sum(final_widths)
    if final_widths and diff != 0:
        max_i = final_widths.index(max(final_widths))
        final_widths[max_i] += diff

    return final_widths


def wrap_text_cell(draw: ImageDraw.ImageDraw, text: str, font: Any, max_w: float, max_lines: int = 3) -> List[str]:
    """Wraps text to fit within column pixel width, splitting long single words if needed."""
    if not text:
        return [""]
    
    # Clean text
    clean = str(text).strip()
    words = clean.split()
    lines = []
    curr = ""

    for w in words:
        # Check if single word itself is wider than max_w
        bbox_w = draw.textbbox((0, 0), w, font=font)
        w_px = bbox_w[2] - bbox_w[0]
        
        if w_px > max_w:
            # Word is wider than max_w -> split character by character
            if curr:
                lines.append(curr)
                curr = ""
            sub_curr = ""
            for char in w:
                t_sub = sub_curr + char
                b_sub = draw.textbbox((0, 0), t_sub, font=font)
                if b_sub[2] - b_sub[0] <= max_w:
                    sub_curr = t_sub
                else:
                    lines.append(sub_curr)
                    sub_curr = char
                    if len(lines) >= max_lines:
                        break
            curr = sub_curr
        else:
            test = f"{curr} {w}".strip()
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] <= max_w:
                curr = test
            else:
                if curr:
                    lines.append(curr)
                curr = w
                if len(lines) >= max_lines - 1:
                    break

    if curr and len(lines) < max_lines:
        lines.append(curr)

    # Hard truncation safety check
    for idx in range(len(lines)):
        b = draw.textbbox((0, 0), lines[idx], font=font)
        if b[2] - b[0] > max_w:
            lines[idx] = lines[idx][:max(1, len(lines[idx]) - 3)] + "..."

    return lines or [clean]


# ─── Render Engine ────────────────────────────────────────────────────────────

class BrandImageRenderer:
    def __init__(self, width: int = 1080, padding: int = 48, is_report: bool = True):
        self.width = width
        self.padding = padding
        self.usable_width = width - (padding * 2)
        self.is_report = is_report
        
        # Colors from brand_config
        self.c_navy = bc.COLOR_PRIMARY_NAVY
        self.c_blue = bc.COLOR_ACCENT_BLUE
        self.c_gold = bc.COLOR_BRAND_GOLD
        self.c_red = bc.COLOR_ALERT_RED
        self.c_red_bg = bc.COLOR_ALERT_RED_BG
        self.c_green = bc.COLOR_SUCCESS_GREEN
        self.c_bg = bc.COLOR_BACKGROUND
        self.c_text = bc.COLOR_TEXT_BODY
        self.c_muted = bc.COLOR_TEXT_MUTED
        self.c_line = bc.COLOR_LINE_BORDER
        self.c_header_bg = bc.COLOR_TABLE_HEADER
        
        # Fonts
        self.f_title = get_font(28, bold=True)
        self.f_section = get_font(20, bold=True)
        self.f_body_bold = get_font(15, bold=True)
        self.f_body = get_font(15, bold=False)
        self.f_meta = get_font(13, bold=False)
        self.f_small = get_font(12, bold=False)
        self.f_pill = get_font(12, bold=True)

    def draw_status_pill(self, draw: ImageDraw.ImageDraw, text: str, x: int, y: int, variant: str = "green") -> Tuple[int, int]:
        """Draw a status pill with rounded corners and white text."""
        pill_colors = {
            "green": self.c_green,
            "success": self.c_green,
            "ok": self.c_green,
            "done": self.c_green,
            "online": self.c_green,
            "gold": self.c_gold,
            "amber": self.c_gold,
            "warning": self.c_gold,
            "red": self.c_red,
            "error": self.c_red,
            "alert": self.c_red,
            "failed": self.c_red,
        }
        fill_color = pill_colors.get(variant.lower(), self.c_green)
        
        bbox = draw.textbbox((0, 0), text, font=self.f_pill)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        
        px = 12
        py = 4
        pw = tw + (px * 2)
        ph = th + (py * 2) + 2
        
        draw.rounded_rectangle([x, y, x + pw, y + ph], radius=6, fill=fill_color)
        draw.text((x + px, y + py), text, font=self.f_pill, fill="#FFFFFF")
        return pw, ph

    def _draw_gradient_rect(self, img: Image.Image, x1: int, y1: int, x2: int, y2: int, color_top: str, color_bottom: str):
        """Draw a vertical gradient rectangle by blending two hex colors row by row."""
        def hex_to_rgb(h: str):
            h = h.lstrip("#")
            return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

        r1, g1, b1 = hex_to_rgb(color_top)
        r2, g2, b2 = hex_to_rgb(color_bottom)
        height = y2 - y1
        if height <= 0:
            return
        draw = ImageDraw.Draw(img)
        for row in range(height):
            t = row / max(height - 1, 1)
            r = int(r1 + (r2 - r1) * t)
            g = int(g1 + (g2 - g1) * t)
            b = int(b1 + (b2 - b1) * t)
            draw.line([(x1, y1 + row), (x2, y1 + row)], fill=(r, g, b))

    def draw_kpi_cards(self, draw: ImageDraw.ImageDraw, img: Image.Image, cards: List[Dict[str, str]], y: int) -> int:
        """Draw a row of KPI metric summary cards. Returns new y offset."""
        if not cards:
            return y
        n = len(cards)
        total_w = 984
        gap = 12
        card_w = (total_w - (gap * (n - 1))) // n
        card_h = 70
        card_x = 48

        f_val = get_font(22, bold=True)
        f_lbl = get_font(11, bold=False)

        for i, card in enumerate(cards):
            val = card.get("value", "")
            lbl = card.get("label", "").upper()
            cx = card_x + i * (card_w + gap)

            # Card background with light blue tint
            draw.rounded_rectangle([cx, y, cx + card_w, y + card_h], radius=8, fill="#F0F9FF", outline="#1B2A4E", width=2)
            # 4px navy left accent stripe
            draw.rounded_rectangle([cx, y, cx + 4, y + card_h], radius=4, fill="#1B2A4E")

            # Value (large, navy)
            val_bbox = draw.textbbox((0, 0), val, font=f_val)
            val_w = val_bbox[2] - val_bbox[0]
            draw.text((cx + 18, y + 10), val, font=f_val, fill="#0F172A")

            # Label (small, muted)
            lbl_bbox = draw.textbbox((0, 0), lbl, font=f_lbl)
            draw.text((cx + 18, y + card_h - 22), lbl, font=f_lbl, fill="#64748B")

        return y + card_h + 20

    def create_report(
        self,
        report_title: str,
        subtitle: str = "",
        timestamp: str = "",
        sections: Optional[List[Dict[str, Any]]] = None,
        alert_box: Optional[Dict[str, str]] = None,
        tables: Optional[List[Dict[str, Any]]] = None,
        kpi_cards: Optional[List[Dict[str, str]]] = None,
    ) -> Image.Image:
        """
        Build premium branded image canvas with full-bleed navy header banner,
        dark navy table header row, KPI metric cards, accent executive block,
        alternating data rows, and rich brand footer.
        """
        if not timestamp:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

        sections = sections or []
        tables = tables or []

        # ── Pre-calculate estimated height ────────────────────────────────────
        banner_h = 88           # full-bleed header banner height
        stripe_h = 6            # accent blue bottom stripe
        gap_below_banner = 24   # space between banner bottom and first content

        estimated_height = banner_h + stripe_h + gap_below_banner
        estimated_height += 60  # report title + subtitle
        if kpi_cards:
            estimated_height += 90
        if alert_box:
            estimated_height += 100
        for s in sections:
            estimated_height += 60 + len(s.get("items", [])) * 40
        for t in tables:
            estimated_height += 80 + (len(t.get("rows", [])) + 1) * 48
        estimated_height += 100  # footer
        estimated_height = max(estimated_height, 600)

        img = Image.new("RGB", (self.width, estimated_height), "#FFFFFF")
        draw = ImageDraw.Draw(img)

        # ── 1. Full-Bleed Navy Gradient Header Banner ─────────────────────────
        self._draw_gradient_rect(img, 0, 0, self.width, banner_h, "#0F172A", "#1B2A4E")
        draw = ImageDraw.Draw(img)   # re-bind after pixel-level ops

        # Company wordmark: brand name in bold white
        f_brand = get_font(24, bold=True)
        f_subtitle_hdr = get_font(13, bold=False)
        f_ts = get_font(12, bold=False)

        draw.text((48, 22), bc.COMPANY_NAME, font=f_brand, fill="#FFFFFF")
        wm_bbox = draw.textbbox((0, 0), bc.COMPANY_NAME, font=f_brand)
        wm_w = wm_bbox[2] - wm_bbox[0]

        # Company subtitle in accent sky-blue
        draw.text((48 + wm_w + 10, 29), bc.COMPANY_SUBTITLE, font=f_subtitle_hdr, fill="#93C5FD")

        # Report title inside banner (line 2), light slate text
        if report_title:
            f_title_hdr = get_font(15, bold=True)
            draw.text((48, 57), report_title, font=f_title_hdr, fill="#E2E8F0")

        # Timestamp right-aligned
        if timestamp:
            ts_bbox = draw.textbbox((0, 0), timestamp, font=f_ts)
            ts_w = ts_bbox[2] - ts_bbox[0]
            draw.text((self.width - 48 - ts_w, 28), timestamp, font=f_ts, fill="#94A3B8")

        # 6px Electric Blue accent stripe below banner
        draw.rectangle([0, banner_h, self.width, banner_h + stripe_h], fill="#2563EB")

        cur_y = banner_h + stripe_h + gap_below_banner

        # ── 2. Report Title & Subtitle (below banner, in content area) ─────────
        if report_title:
            draw.text((48, cur_y), report_title, font=self.f_section, fill="#0F172A")
            cur_y += 30
        if subtitle:
            draw.text((48, cur_y), subtitle, font=self.f_body, fill="#475569")
            cur_y += 26

        cur_y += 8

        # ── 3. KPI Summary Cards (if provided) ────────────────────────────────
        if kpi_cards:
            cur_y = self.draw_kpi_cards(draw, img, kpi_cards, cur_y)
            draw = ImageDraw.Draw(img)

        # ── 4. Optional Executive Alert Box ───────────────────────────────────
        if alert_box:
            alert_title = alert_box.get("title", "Important")
            alert_body = alert_box.get("body", "")

            box_x = 48
            box_w = 984

            # Measure alert_body height (allow wrapping)
            body_lines = wrap_text_cell(draw, alert_body, self.f_body, float(box_w - 60), max_lines=4)
            box_h = 28 + len(body_lines) * 22 + 16

            # Sky-blue background (executive tone)
            draw.rounded_rectangle([box_x, cur_y, box_x + box_w, cur_y + box_h], radius=6, fill="#EFF6FF", outline="#BFDBFE", width=1)
            # 5px blue left accent
            draw.rounded_rectangle([box_x, cur_y, box_x + 5, cur_y + box_h], radius=4, fill="#2563EB")

            draw.text((box_x + 16, cur_y + 10), alert_title + ":", font=self.f_body_bold, fill="#1E3A5F")
            for li, ln in enumerate(body_lines):
                draw.text((box_x + 16, cur_y + 30 + li * 22), ln, font=self.f_body, fill="#334155")

            cur_y += box_h + 20

        # ── 5. Render Sections (key-value pairs) ─────────────────────────────
        for s in sections:
            s_title = s.get("title", "")
            s_rule = s.get("rule_color", self.c_gold if self.is_report else self.c_blue)
            if s_title:
                draw.text((48, cur_y), s_title, font=self.f_section, fill="#0F172A")
                cur_y += 30
                # Brand Gold rule (1.5px)
                draw.rectangle([48, cur_y, 1032, cur_y + 2], fill=s_rule)
                cur_y += 14

            items = s.get("items", [])
            for idx, item in enumerate(items):
                label = item.get("label", "")
                val = item.get("value", "")
                status_variant = item.get("status_variant")

                # Alternating row background
                row_bg = "#F8FAFC" if idx % 2 == 0 else "#FFFFFF"
                draw.rectangle([48, cur_y - 4, 1032, cur_y + 28], fill=row_bg)

                draw.text((56, cur_y), f"• {label}:", font=self.f_body_bold, fill="#0F172A")
                lb_bbox = draw.textbbox((0, 0), f"• {label}: ", font=self.f_body_bold)
                lb_w = lb_bbox[2] - lb_bbox[0]

                if status_variant:
                    self.draw_status_pill(draw, str(val), 56 + lb_w, cur_y - 1, variant=status_variant)
                else:
                    draw.text((56 + lb_w, cur_y), str(val), font=self.f_body, fill="#334155")
                cur_y += 34
            cur_y += 14

        # ── 6. Render Premium Data Tables ─────────────────────────────────────
        for table in tables:
            t_title = table.get("title", "")
            if t_title:
                draw.text((48, cur_y), t_title, font=self.f_section, fill="#0F172A")
                cur_y += 30
                # Brand Gold underline rule
                draw.rectangle([48, cur_y, 1032, cur_y + 2], fill=self.c_gold if self.is_report else self.c_blue)
                cur_y += 14

            headers = table.get("headers", [])
            rows    = table.get("rows", [])
            col_widths = table.get("col_widths", [])

            if not col_widths and headers:
                col_widths = calculate_smart_col_widths(
                    headers, rows, 984.0,
                    draw=draw, font_header=self.f_body_bold, font_body=self.f_body, font_pill=self.f_pill
                )

            table_x = 48
            header_h = 46

            # Pre-wrap cell text to compute row heights
            row_wrapped_data = []
            row_heights = []
            for row in rows:
                wrapped_row = []
                max_lines = 1
                for c_idx, cell in enumerate(row):
                    cw = (col_widths[c_idx] if c_idx < len(col_widths) else 100) - 20
                    if isinstance(cell, dict) and cell.get("type") == "pill":
                        wrapped_row.append([cell])
                    else:
                        lines = wrap_text_cell(draw, str(cell), self.f_body, float(cw), max_lines=3)
                        wrapped_row.append(lines)
                        max_lines = max(max_lines, len(lines))
                row_wrapped_data.append(wrapped_row)
                row_heights.append(max(42, 12 + max_lines * 20))

            table_total_h = header_h + sum(row_heights)

            # Table outer border
            draw.rounded_rectangle(
                [table_x, cur_y, table_x + 984, cur_y + table_total_h],
                radius=6, outline="#CBD5E1", width=1, fill="#FFFFFF"
            )

            # ── Dark Navy Table Header Row ──────────────────────────────────
            self._draw_gradient_rect(img, table_x, cur_y, table_x + 984, cur_y + header_h, "#1B2A4E", "#243351")
            draw = ImageDraw.Draw(img)

            # 2px blue stripe below header
            draw.rectangle([table_x, cur_y + header_h - 2, table_x + 984, cur_y + header_h], fill="#2563EB")

            # Rounded top corners mask (fill corners white to fake rounded rect on header)
            # Header column texts (white)
            cx = table_x
            for i, h in enumerate(headers):
                w = col_widths[i] if i < len(col_widths) else 100
                draw.text((cx + 12, cur_y + 14), str(h), font=self.f_body_bold, fill="#FFFFFF")
                cx += w
                if i < len(headers) - 1:
                    draw.line([cx, cur_y + 4, cx, cur_y + header_h - 4], fill="#2D4A6E", width=1)

            cur_y += header_h

            # ── Data Rows ──────────────────────────────────────────────────
            for r_idx, wrapped_row in enumerate(row_wrapped_data):
                rh = row_heights[r_idx]

                # Alternating row background
                row_bg = "#FFFFFF" if r_idx % 2 == 0 else "#F8FAFC"
                draw.rectangle([table_x, cur_y, table_x + 984, cur_y + rh], fill=row_bg)

                # Row top border
                draw.line([table_x, cur_y, table_x + 984, cur_y], fill="#E2E8F0", width=1)

                cx = table_x
                for c_idx, cell_data in enumerate(wrapped_row):
                    w = col_widths[c_idx] if c_idx < len(col_widths) else 100

                    if isinstance(cell_data[0], dict) and cell_data[0].get("type") == "pill":
                        pv = cell_data[0].get("variant", "green")
                        self.draw_status_pill(draw, cell_data[0].get("text", ""), cx + 10, cur_y + int((rh - 26) / 2), variant=pv)
                    else:
                        for l_idx, line_str in enumerate(cell_data):
                            txt_color = "#0F172A" if c_idx == 0 else "#334155"
                            f = self.f_body_bold if c_idx == 0 else self.f_body
                            draw.text((cx + 12, cur_y + 10 + l_idx * 20), line_str, font=f, fill=txt_color)

                    # Column separator
                    cx += w
                    if c_idx < len(wrapped_row) - 1:
                        draw.line([cx, cur_y, cx, cur_y + rh], fill="#E2E8F0", width=1)

                cur_y += rh

            # Outer border redraw on top
            draw.rounded_rectangle(
                [table_x, cur_y - sum(row_heights) - header_h,
                 table_x + 984, cur_y],
                radius=6, outline="#CBD5E1", width=1
            )

            cur_y += 28

        # ── 7. Premium Footer ─────────────────────────────────────────────────
        cur_y += 10
        # 2px navy footer rule
        draw.rectangle([48, cur_y, 1032, cur_y + 2], fill="#1B2A4E")
        cur_y += 14

        # Company name + tagline
        draw.text((48, cur_y), f"{bc.COMPANY_NAME} / {bc.COMPANY_TAGLINE}", font=self.f_body_bold, fill="#1B2A4E")
        cur_y += 22

        # Contact info
        footer_contact = f"{bc.SIGNATURE_ADDRESS}  •  {bc.SIGNATURE_EMAIL}  •  {bc.SIGNATURE_PHONE}"
        draw.text((48, cur_y), footer_contact, font=self.f_small, fill="#64748B")
        cur_y += 32

        final_height = cur_y + 20
        final_img = img.crop((0, 0, self.width, final_height))
        return final_img


# ─── Sample Generators (Deliverables) ────────────────────────────────────────

def render_sample_infrastructure_status(output_path: str = "sample_infrastructure_status.png") -> str:
    """Sample 1: Infrastructure Status Report with Table + Status Pills."""
    renderer = BrandImageRenderer(is_report=True)
    
    table_data = {
        "title": "Infrastructure Status & Storage Pools",
        "headers": ["Server ID", "IP Address", "Storage Pools", "Last Snapshot", "Status"],
        "col_widths": [180, 240, 220, 200, 144],
        "rows": [
            ["srv01", "192.168.180.5", "4 ONLINE, 0 errors", "2026-07-25 00:00", {"type": "pill", "text": "ONLINE", "variant": "green"}],
            ["srv02", "192.168.180.6", "2 ONLINE, 0 errors", "2026-07-25 01:00", {"type": "pill", "text": "ONLINE", "variant": "green"}],
            ["db-cluster-01", "192.168.180.12", "Syncing replication", "2026-07-25 04:30", {"type": "pill", "text": "WARNING", "variant": "gold"}],
            ["backup-node", "192.168.180.99", "Offline maintenance", "2026-07-24 18:00", {"type": "pill", "text": "FAILED", "variant": "red"}],
        ]
    }
    
    img = renderer.create_report(
        report_title="Big Sky — Infrastructure Status Report",
        subtitle="Automated daily system health breakdown for the monitored cloud infrastructure.",
        tables=[table_data]
    )
    
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path, format="PNG")
    return output_path


def render_sample_task_summary(output_path: str = "sample_task_summary.png") -> str:
    """Sample 2: Task Summary with Sections + Alert Block."""
    renderer = BrandImageRenderer(is_report=False)
    
    sections = [
        {
            "title": "Odoo CRM & Integration Audit Tasks",
            "items": [
                {"label": "MCP Server Synchronization", "value": "COMPLETED", "status_variant": "green"},
                {"label": "Odoo Invoice PDF Generation", "value": "VERIFIED", "status_variant": "green"},
                {"label": "WhatsApp Template Delivery", "value": "PENDING REVIEW", "status_variant": "amber"},
            ]
        }
    ]
    
    alert_box = {
        "title": "Important",
        "body": "Database maintenance scheduled for Sunday 02:00 UTC. Backup required prior to execution."
    }
    
    img = renderer.create_report(
        report_title="Odoo Assistant — Operational Task Summary",
        subtitle="Summary of recent system changes and active integration tasks.",
        sections=sections,
        alert_box=alert_box
    )
    
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path, format="PNG")
    return output_path


def auto_render_response(text: str, title: str = "Odoo Assistant — System Report") -> Tuple[bool, Optional[str], str]:
    """
    Analyzes response text. If it contains tables or structured status lists,
    automatically renders a Branded PNG image and returns (has_image, image_path, clean_summary).
    Otherwise returns (False, None, original_text).
    """
    if not text:
        return False, None, text

    from pdf_generator import parse_table_data
    raw_headers, raw_rows = parse_table_data(text)

    lines = [l.strip() for l in text.split("\n") if l.strip()]
    status_keywords = ["status:", "state:", "online", "offline", "completed", "failed", "warning", "done", "pending"]
    matched_status_lines = [l for l in lines if any(k in l.lower() for k in status_keywords)]
    has_status_items = len(matched_status_lines) >= 3 and len(lines) >= 4

    if not (raw_headers or has_status_items):
        return False, None, text

    parsed_tables = []
    if raw_headers and raw_rows:
        try:
            formatted_rows = []
            for r in raw_rows:
                row_cells = []
                for c in r:
                    cu = str(c).upper().strip()
                    if cu in ("ONLINE", "DONE", "OK", "COMPLETED", "SUCCESS", "VERIFIED", "PAID"):
                        row_cells.append({"type": "pill", "text": str(c), "variant": "green"})
                    elif cu in ("WARNING", "PENDING", "SYNCING", "IN PROGRESS", "PARTIAL"):
                        row_cells.append({"type": "pill", "text": str(c), "variant": "gold"})
                    elif cu in ("FAILED", "ERROR", "OFFLINE", "ALERT", "CANCELLED", "OVERDUE", "NOT PAID", "UNPAID"):
                        row_cells.append({"type": "pill", "text": str(c), "variant": "red"})
                    else:
                        row_cells.append(str(c))
                formatted_rows.append(row_cells)

            parsed_tables.append({
                "title": "Data Breakdown",
                "headers": raw_headers,
                "rows": formatted_rows
            })
        except Exception:
            pass

    parsed_sections = []
    if has_status_items and not parsed_tables:
        section_items = []
        for l in matched_status_lines:
            if ":" in l:
                parts = l.split(":", 1)
                label = parts[0].strip().lstrip("•*- ").strip()
                val = parts[1].strip()
                val_u = val.upper()
                variant = None
                if any(w in val_u for w in ("ONLINE", "DONE", "OK", "COMPLETED", "SUCCESS", "VERIFIED", "PAID")):
                    variant = "green"
                elif any(w in val_u for w in ("WARNING", "PENDING", "SYNCING", "AMBER", "PARTIAL")):
                    variant = "gold"
                elif any(w in val_u for w in ("FAILED", "ERROR", "OFFLINE", "ALERT", "RED", "UNPAID", "NOT PAID")):
                    variant = "red"
                
                item = {"label": label, "value": val}
                if variant:
                    item["status_variant"] = variant
                section_items.append(item)

        if section_items:
            parsed_sections.append({
                "title": "Operational Status",
                "items": section_items
            })

    if parsed_tables or parsed_sections:
        try:
            # Derive natural title and subtitle based on text content
            t_lower = text.lower()
            if "timesheet" in t_lower or "hours" in t_lower:
                report_title = "Odoo ERP — Timesheet Activity Breakdown"
                subtitle = "Summary of recorded time entries"
            elif "invoice" in t_lower or "unpaid" in t_lower:
                report_title = "Odoo ERP — Unpaid Customer Invoices"
                subtitle = "Outstanding balances and payment status"
            elif "user" in t_lower or "partner" in t_lower:
                report_title = "Odoo ERP — Active System Users Audit"
                subtitle = "User accounts and access roles"
            elif "ticket" in t_lower or "helpdesk" in t_lower:
                report_title = "Odoo ERP — Helpdesk Tickets Summary"
                subtitle = "Support ticket status and assignments"
            else:
                report_title = title if title != "Odoo Assistant — System Report" else "Odoo ERP — Executive Audit Report"
                subtitle = "System activity and record breakdown"

            renderer = BrandImageRenderer(is_report=True)
            img = renderer.create_report(
                report_title=report_title,
                subtitle=subtitle,
                tables=parsed_tables,
                sections=parsed_sections
            )
            os.makedirs("temp_reports", exist_ok=True)
            timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = os.path.join("temp_reports", f"brand_report_{timestamp_str}.png")
            img.save(out_path, format="PNG")
            
            import re
            clean_text = text
            if "<table" in clean_text.lower():
                clean_text = re.sub(r'<table[^>]*>.*?</table>', f'\n\n📊 <i>(Visual summary report attached below)</i>\n\n', clean_text, flags=re.DOTALL | re.IGNORECASE)
            elif "|" in clean_text:
                lines = clean_text.split("\n")
                non_table = [l for l in lines if not (l.strip().startswith("|") and l.strip().endswith("|"))]
                clean_text = "\n".join(non_table).strip()
                clean_text += "\n\n📊 <i>(Visual summary report attached below)</i>"

            if not clean_text.strip() or len(clean_text.strip()) < 15:
                clean_text = f"📊 <b>{title}</b>\n<i>I have generated and attached the visual status report below.</i>"

            return True, out_path, clean_text
        except Exception:
            pass

    return False, None, text


