# Odoo AI Telegram Assistant

A Telegram bot that drives a full Odoo instance through natural language, backed by a custom MCP server exposing 104 Odoo tools.

I built this because the Odoo web client is a lot of clicking for operations that are conceptually one sentence. Creating a lead, converting it, quoting it, confirming, delivering and invoicing is six screens and a dozen clicks. It should be one message.

```
> Create a lead for Northwind Traders, convert it to an opportunity,
  quote 10 units of Product A, confirm it and invoice it.
```

The orchestrator resolves that into the tool sequence:

```
crm_create_lead → crm_convert_to_opportunity → crm_create_sale_order
→ sale_confirm_order → stock_validate_delivery → account_create_invoice
```

It reports back with the record IDs, and renders a styled PNG or PDF if the result is tabular.

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
   │                       odoo_crm_mcp.py    ── XML-RPC ──▶  Odoo
   │                       (104 MCP tools)
   │
   └─ brand_renderer.py / pdf_generator.py    PNG and PDF output
```

The MCP server is the interesting part. Rather than giving the model a generic "call Odoo" function and hoping it constructs valid domains, each operation is a typed tool with its own schema. `crm_create_lead` knows what a lead needs. `account_invoice_reconcile` knows what reconciliation requires. The model picks tools and fills arguments; it never writes raw ORM calls.

Tools cover CRM (leads, opportunities, stages, activities, chatter), Sales (quotations, confirmation, order lines), Inventory (deliveries, validation, stock moves), Accounting (invoices, credit notes, payment terms, reconciliation, payment registration) and Helpdesk.

## Authentication

Users log in with their own Odoo credentials. Every tool call executes as that user, so Odoo's own access rights and record rules apply — the bot cannot do anything the user could not do in the web client. This matters more than it sounds: it means you don't have to reimplement permissions in the bot, and a compromised bot session is bounded by one user's access.

Privileged operations go through a separate admin bot with an approval step, so destructive actions need a human to sign off rather than being one sentence away in normal chat.

## LLM providers

Configurable across OpenRouter, Groq, Gemini, Anthropic and Ollama. Switching is one environment variable; the tool-calling layer is provider-agnostic.

The Ollama path exists because ERP data is usually the last thing a business wants leaving its network. It works, but tool-calling reliability on small local models is noticeably worse than on the hosted ones — expect more retries and occasional malformed arguments. Use a hosted provider unless you have a reason not to.

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

All configuration is environment variables. See `.env.example`.

- `BOT_TOKEN`, `ADMIN_BOT_TOKEN` — the two Telegram bots
- `LLM_PROVIDER` plus the matching provider key
- `ODOO_URL`, `ODOO_DB`, `ODOO_VERSION`
- `BRAND_*` — report branding, all optional

Report output is branded through `brand_config.py`. Company name, colours, typography and the signature block are read from environment variables with neutral defaults, so you can rebrand without touching code.

## Known limitations

- Credentials are stored in a local JSON file. This is fine for a personal deployment and not fine for anything else — encrypt them at rest before you put this in front of real users.
- No test suite. It was built iteratively against a live Odoo instance.
- Tool count is large enough that some models struggle to select correctly on ambiguous prompts. Narrowing the exposed tool set per user role would help.
- Only tested against Odoo 18.

## Status

Personal project, published as a reference for anyone building MCP integrations against an ERP. No warranty. Read the code before deploying it anywhere that matters.
