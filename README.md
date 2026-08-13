# Odoo AI Telegram Assistant (MCP)

Run a full Odoo ERP from a Telegram chat, in natural language.

This is a Telegram bot backed by a custom **MCP (Model Context Protocol) server exposing 104 Odoo tools**, with an orchestrator that plans and executes multi-step business workflows. Ask it to create a lead, quote it, deliver it and invoice it — it works out the sequence of tool calls and runs them.

```
"Create a lead for Northwind Traders, convert it to an opportunity,
 raise a quotation for 10 units of Product A, confirm it, and invoice it."
```

→ `crm_create_lead` → `crm_convert_to_opportunity` → `crm_create_sale_order` →
  `sale_confirm_order` → `stock_validate_delivery` → `account_create_invoice` → `account_register_payment`

---

## What's in it

**104 Odoo MCP tools** spanning CRM, Sales, Inventory, Accounting and Helpdesk — leads, opportunities, activities, chatter, quotations, deliveries, invoices, credit notes, reconciliation and payment terms.

**Multi-provider LLM support** — OpenRouter, Groq, Google Gemini, Anthropic Claude, or a fully local model via Ollama. Switch with one environment variable; the tool-calling orchestration is provider-agnostic.

**Odoo authentication with role-based access** — users authenticate with their own Odoo credentials, so every action runs under that user's real permissions and record rules. The bot cannot do what the user cannot do.

**Approval-gated admin bot** — a separate admin bot handles restricted operations, so privileged actions require explicit approval rather than being available in normal chat.

**Branded report generation** — renders results as styled PNG images (PIL) and PDF documents (ReportLab), with the full brand identity — name, colours, typography, signature block — driven by environment variables. See `brand_config.py`.

**Multilingual output** — reports and responses can be generated in the user's language.

**Conversation memory** — per-user history so follow-up questions resolve against earlier context.

---

## Architecture

```
Telegram  ──►  bot.py            Telegram transport, commands, media
               ai_processor.py   Orchestrator: planning + multi-step tool execution
               openrouter.py     Provider abstraction (OpenRouter/Groq/Gemini/Claude/Ollama)
               mcp_client.py     MCP transport — tool discovery and invocation
                    │
                    ▼
               odoo_crm_mcp.py   MCP server — 104 Odoo tools over XML-RPC
                    │
                    ▼
                  Odoo
```

Supporting modules: `auth_manager.py` (Odoo login), `admin_manager.py` (privileged actions + approvals), `conversation_store.py` (history), `brand_config.py` / `brand_renderer.py` / `pdf_generator.py` (report output), `odoo_knowledge_base.py` (domain context).

---

## Setup

```bash
git clone <this-repo>
cd odoo-ai-telegram-mcp
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env      # then fill in your own values
python bot.py
```

Requires Python 3.10+ and an Odoo instance reachable over XML-RPC.

---

## Configuration

All configuration is via environment variables — see [`.env.example`](.env.example) for the full list.

| Group | Purpose |
|---|---|
| `BOT_TOKEN`, `ADMIN_BOT_TOKEN` | Telegram bots (main + admin) |
| `LLM_PROVIDER` + provider keys | Which model backend to use |
| `ODOO_URL`, `ODOO_DB`, `ODOO_VERSION` | Target Odoo instance |
| `BRAND_*` | Report branding — neutral defaults, override for your own identity |

---

## Security notes

- `.env`, `user_credentials.json` and `admin_users.json` are gitignored and must never be committed.
- Odoo credentials are supplied per user and all actions execute under that user's own Odoo permissions.
- Privileged operations route through the admin bot's approval flow.
- If you deploy this, review how user credentials are persisted and encrypt them at rest.

---

## Status

Personal project, shared as a working reference for MCP-based ERP automation. No warranty; adapt before production use.
