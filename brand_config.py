"""
brand_config.py
───────────────
Centralized brand guidelines and configuration for generated reports.

Every value below is read from an environment variable with a neutral
placeholder default, so the report renderer can be branded for any
organisation without touching the code. Set the BRAND_* variables in your
.env file (see .env.example) to apply your own identity.
"""

from __future__ import annotations
import os
from typing import Dict, Any

# ─── 1. Company Information (Easily Configurable) ───────────────────────────

COMPANY_NAME = os.getenv("BRAND_COMPANY_NAME", "ACME")
COMPANY_SUBTITLE = os.getenv("BRAND_COMPANY_SUBTITLE", "Ltd.")
COMPANY_TAGLINE = os.getenv("BRAND_COMPANY_TAGLINE", "Business Automation")

# Signature details — placeholders only; override via environment variables.
SIGNATURE_NAME = os.getenv("BRAND_SIGNATURE_NAME", "Jane Doe")
SIGNATURE_COMPANY = os.getenv("BRAND_SIGNATURE_COMPANY", "ACME Ltd.")
SIGNATURE_ADDRESS = os.getenv("BRAND_SIGNATURE_ADDRESS", "123 Example Street, Example City")
SIGNATURE_EMAIL = os.getenv("BRAND_SIGNATURE_EMAIL", "contact@example.com")
SIGNATURE_PHONE = os.getenv("BRAND_SIGNATURE_PHONE", "+1 (555) 010-0000")

# ─── 2. Brand Color Palette ──────────────────────────────────────────────────

COLOR_PRIMARY_NAVY = os.getenv("BRAND_COLOR_PRIMARY_NAVY", "#1B2A4E")  # Headers, key text
COLOR_ACCENT_BLUE   = os.getenv("BRAND_COLOR_ACCENT_BLUE", "#2E5AAC")   # Subheadings, links
COLOR_BRAND_GOLD    = os.getenv("BRAND_COLOR_BRAND_GOLD", "#C9A24B")    # Dividers, rules (reports only)
COLOR_ALERT_RED     = os.getenv("BRAND_COLOR_ALERT_RED", "#C0392B")     # Warning border, alerts
COLOR_ALERT_RED_BG  = os.getenv("BRAND_COLOR_ALERT_RED_BG", "#FDECEA")  # Warning box background
COLOR_SUCCESS_GREEN = os.getenv("BRAND_COLOR_SUCCESS_GREEN", "#1E8449") # OK / Completed status
COLOR_BACKGROUND    = os.getenv("BRAND_COLOR_BACKGROUND", "#FFFFFF")    # Canvas background (always white)
COLOR_TEXT_BODY     = os.getenv("BRAND_COLOR_TEXT_BODY", "#333333")     # Body text
COLOR_TEXT_MUTED    = os.getenv("BRAND_COLOR_TEXT_MUTED", "#666666")    # Small print / timestamps
COLOR_LINE_BORDER   = os.getenv("BRAND_COLOR_LINE_BORDER", "#DDDDDD")   # Table borders, separators
COLOR_TABLE_HEADER  = os.getenv("BRAND_COLOR_TABLE_HEADER", "#F5F7FA")  # Table header background

# ─── 3. Typography Configuration ─────────────────────────────────────────────

FONT_FAMILY_PRIMARY = "Verdana"
FONT_FAMILY_FALLBACK = "DejaVu Sans, Geneva, sans-serif"

# Font size specs (pixels for image rendering)
FONT_SIZE_HEADER_TITLE = 28
FONT_SIZE_SECTION_TITLE = 20
FONT_SIZE_BODY = 16
FONT_SIZE_META = 13
FONT_SIZE_SMALL = 12

# ─── 4. System Prompt Brand Rules Snippet ────────────────────────────────────

def get_system_prompt_branding() -> str:
    """Return the brand guidelines instruction block for the LLM system prompt."""
    return f"""
<brand_guidelines>
You create reports, emails and client-facing responses representing {COMPANY_NAME} {COMPANY_SUBTITLE}.
Always apply the following brand rules:

1. VOICE AND TONE:
   - Write in FIRST PERSON SINGULAR ("I configured", "I verified") — never "we".
   - Plain, factual language — no marketing filler.
   - Never claim an issue "resolved itself"; state what action was actually taken.
   - Address technical readers directly; explain jargon only when recipient is non-technical.

2. COLOR & STYLING RULES:
   - Primary Navy: {COLOR_PRIMARY_NAVY} (headers, key text)
   - Accent Blue: {COLOR_ACCENT_BLUE} (subheadings, links, key terms)
   - Brand Gold: {COLOR_BRAND_GOLD} (reports and document rules only; NEVER use gold in emails)
   - Header Rule: {COMPANY_NAME} wordmark MUST be Navy text on Light/White background. NEVER white text on navy fill.
   - Warning/Critical Boxes: Red border ({COLOR_ALERT_RED}) on light red background ({COLOR_ALERT_RED_BG}).

3. SIGNATURE BLOCK:
   - The formal signature block is rendered automatically on formal PDF/PNG document footers.
   - Do NOT append raw text signature blocks at the end of regular instant chat responses.
</brand_guidelines>
"""
