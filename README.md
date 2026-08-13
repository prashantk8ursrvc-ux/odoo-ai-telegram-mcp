# Odoo AI Telegram Assistant

Drive a full Odoo instance from Telegram, in plain language. Backed by a custom MCP server exposing **104 Odoo tools** across CRM, Sales, Inventory, Accounting and Helpdesk.

![Python](https://img.shields.io/badge/python-3.10+-blue?style=flat-square)
![Odoo](https://img.shields.io/badge/odoo-18-714B67?style=flat-square)
![MCP](https://img.shields.io/badge/MCP-104%20tools-lightgrey?style=flat-square)

---

I built this because the Odoo web client is a lot of clicking for operations that are conceptually one sentence. Creating a lead, converting it, quoting it, confirming, delivering and invoicing is six screens and a dozen clicks. It should be one message.

```
> Create a lead for Northwind Traders, convert it to an opportunity,
  quote 10 units of Product A, confirm it and invoice it.
```

The orchestrator resolves that into a tool sequence, runs it, and reports back with the record IDs:

```
crm_create_lead → crm_convert_to_opportunity → crm_create_sale_order
→ sale_confirm_order → stock_validate_delivery → account_create_invoice
```

If the result is tabular, it renders a styled PNG or PDF instead of dumping text into the chat.

## How it works

```
Telegram
   │
   ├─ bot.py               transport, commands, media
   │
   ├─ ai_processor.py      planning and multi-step execution
   │     ├─ openrouter.py  provider abstraction
   │     └─ mcp_client.py  tool discovery and invocation
   │                             │
   │                             ▼
   │                       odoo_crm_mcp.py  ── XML-RPC ──▶  Odoo
   │                       (104 MCP tools)
   │
   └─ brand_renderer.py / pdf_generator.py    PNG and PDF output
```

The MCP server is the part worth looking at. Rather than giving the model one generic "call Odoo" function and hoping it constructs valid domains, every operation is a typed tool with its own schema. `crm_create_lead` knows what a lead needs. `account_invoice_reconcile` knows what reconciliation requires. The model selects tools and fills arguments; it never writes raw ORM calls.

| Area | Coverage |
|---|---|
| **CRM** | Leads, opportunities, stages, activities, chatter, won/lost, lead→SO conversion |
| **Sales** | Quotations, order confirmation, order lines, pricing |
| **Inventory** | Deliveries, validation, stock moves |
| **Accounting** | Invoices, credit notes, payment terms, reconciliation, payment registration |
| **Helpdesk** | Tickets and ticket workflow |

## Authentication

Users log in with their own Odoo credentials, and every tool call executes as that user. Odoo's access rights and record rules apply unchanged, so the bot cannot do anything the user could not do in the web client.

That matters more than it sounds. It means permissions never get reimplemented in the bot, and a compromised session is bounded by one user's access rather than the whole database.

Privileged operations route through a separate admin bot with an approval step, so destructive actions need a human to sign off instead of being one sentence away in normal chat.

## LLM providers

| Provider | Notes |
|---|---|
| OpenRouter, Groq, Gemini, Anthropic | Hosted. Reliable tool calling. |
| Ollama | Fully local. No data leaves the network. |

Switching is one environment variable — the tool-calling layer is provider-agnostic.

The Ollama path exists because ERP data is usually the last thing a business wants leaving its network. It works, but tool-calling reliability on small local models is noticeably worse than on hosted ones. Expect more retries and occasional malformed arguments. Use a hosted provider unless you have a reason not to.

## Setup

```bash
git clone https://github.com/prashantk8ursrvc-ux/odoo-ai-telegram-mcp.git
cd odoo-ai-telegram-mcp

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # fill in your values
python bot.py
```

Python 3.10+, and an Odoo instance reachable over XML-RPC.

## Configuration

Everything is environment variables — see `.env.example`.

| Variable | Purpose |
|---|---|
| `BOT_TOKEN`, `ADMIN_BOT_TOKEN` | The two Telegram bots |
| `LLM_PROVIDER` + provider key | Model backend |
| `ODOO_URL`, `ODOO_DB`, `ODOO_VERSION` | Target instance |
| `BRAND_*` | Report branding (optional) |

Report output is branded through `brand_config.py`. Company name, colours, typography and the signature block all read from environment variables with neutral defaults, so rebranding needs no code changes.

## Known limitations

- Credentials are stored in a local JSON file. Fine for a personal deployment, not fine for anything else. Encrypt them at rest before putting this in front of real users.
- No test suite. Built iteratively against a live Odoo instance.
- 104 tools is enough that some models pick wrong on ambiguous prompts. Narrowing the exposed set per user role would help.
- Only tested against Odoo 18.

## Status

Personal project, published as a reference for building MCP integrations against an ERP. No warranty — read the code before deploying it anywhere that matters.
