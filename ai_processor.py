"""
ai_processor.py
───────────────
Core agentic loop with robust error handling for weak/free models:

Failure cases handled:
  1. Model skips tool call → detects data-fetching keywords, nudges with tool_choice="required"
  2. Model returns empty content → retry with a prompt nudge
  3. Tool call arguments are malformed JSON → falls back to empty args gracefully
  4. Tool execution crashes → returns error message to model so it can explain
  5. Model loops forever → MAX_TOOL_ROUNDS hard cap
  6. OpenRouter rate limit / 5xx → retried with exponential backoff
  7. Model stops mid-thought (finish_reason=length) → warns user to rephrase
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import contextvars
from datetime import datetime, timedelta

# Dynamic session elevation key for access-approved queries
admin_override_var = contextvars.ContextVar("admin_override", default=None)

# Active escalated sessions mapped by user_id
_active_elevations: Dict[int, str] = {}

from typing import Any, Dict, List, Optional

from openrouter import chat
from mcp_client import get_tools, call_tool
from conversation_store import get_history, append_messages
import admin_manager

logger = logging.getLogger(__name__)

# Safety cap — prevents infinite loops if the model keeps calling tools.
MAX_TOOL_ROUNDS = 20

# Keywords that strongly imply the user wants live Odoo data.
# If the model skips tools on these, we retry with tool_choice="required".
DATA_KEYWORDS = re.compile(
    r"\b(show|list|get|find|fetch|search|create|update|add|delete|remove|unlink|write|change|modify|confirm|validate|post|render|print|download|pdf|report)\b|"
    r"\b(leads?|opportunities|pipeline|customers?|partners?|activities|activity|"
    r"quotations?|orders?|sales?|crm|contacts?|accounts?|users?|members?|"
    r"invoices?|bills?|refunds?|credit_notes?|payments?|journal_entries?|journal_items?|"
    r"projects?|tasks?|timesheets?|purchases?|vendors?|suppliers?|"
    r"stock|inventory|products?|warehouse|deliveries|shipments|receipts|"
    r"documents?|files?|folders?|tickets?|helpdesks?|planning|shifts?|slots?|whatsapp|signs?|social|posts?|channels?|budgets?|assets?|fsm)\b",
    re.IGNORECASE,
)


def resolve_date_string(date_str: Any) -> str:
    """Resolve relative date strings (e.g. 'yesterday', 'today', 'tomorrow') to exact ISO date YYYY-MM-DD."""
    if not date_str:
        return datetime.now().strftime("%Y-%m-%d")
    
    d_clean = str(date_str).lower().strip()
    now = datetime.now()

    if "yesterday" in d_clean:
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    elif "today" in d_clean:
        return now.strftime("%Y-%m-%d")
    elif "tomorrow" in d_clean:
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")
    
    return str(date_str)


def _check_is_user_confirmed(user_prompt: str) -> bool:
    """
    Strictly verify if the user has explicitly confirmed a pending mutation (e.g. timesheet creation).
    Avoids false positives from partial word matches like 'yes' inside 'yesterday' or 'confirm' inside long prompt text.
    """
    p_clean = user_prompt.lower().strip()
    if "confirm_timesheets" in p_clean:
        return True
    exact_triggers = {
        "confirm", "yes", "proceed", "go ahead", "do it", "approve", "approved",
        "confirm timesheets", "confirm_timesheets", "confirm_action", "confirm entries",
        "log entries", "yes log them", "yes, confirm", "yes log entries", "confirm creation",
        "confirm project", "confirm order", "confirm product", "confirm invoice"
    }
    if p_clean in exact_triggers:
        return True
    if re.search(r'^\s*(?:user selected option:\s*)?confirm_(?:timesheets|action)\s*$', p_clean):
        return True
    if re.search(r'^\s*(?:confirm|approve|proceed|yes)\s+(?:the\s+)?(?:timesheet|timesheets|entries|log|hours|action|creation|project|order|invoice|product|task|contact)\s*$', p_clean):
        return True
    return False


READ_ONLY_TOOLS = {
    "odoo_search_read", "odoo_get_model_fields", "odoo_list_models",
    "project_get_projects", "project_get_tasks", "sale_get_orders",
    "sale_get_order_details", "sale_get_pricelists", "invoice_get_invoices",
    "invoice_get_details", "search_knowledge_base", "search_partners",
    "search_users", "get_leads", "get_lead_details", "get_activities",
    "get_chatter", "get_stages", "get_lost_reasons", "get_tags",
    "get_teams", "get_pipeline_stats", "get_activity_types", "report_get_pdf",
    "whatsapp_get_templates", "whatsapp_get_messages", "documents_get_folders",
    "documents_get_files", "purchase_get_orders", "stock_get_pickings",
    "stock_get_quants", "planning_get_slots"
}


def _is_mutation_tool_call(tc: Dict[str, Any]) -> bool:
    fname = tc.get("function", {}).get("name", "")
    if fname in READ_ONLY_TOOLS:
        return False
        
    raw_args = tc.get("function", {}).get("arguments", "{}")
    parsed_args = _try_extract_valid_json(raw_args) if isinstance(raw_args, str) else (raw_args or {})
    raw_args_str = json.dumps(parsed_args).lower()

    if fname in ("odoo_create", "odoo_write", "odoo_unlink"):
        return True

    if fname == "odoo_call_method":
        method_name = str(parsed_args.get("method_name") or "").lower()
        read_methods = {"read", "name_get", "search", "search_count", "search_read", "fields_get", "get_metadata", "check_access_rights"}
        if method_name in read_methods or not method_name:
            return False
        return True

    if "timesheet" in fname or "timesheet" in raw_args_str or any(fname.startswith(prefix) for prefix in (
        "create_", "update_", "confirm_", "post_", "add_", "delete_",
        "unlink_", "log_", "schedule_", "register_", "project_create_",
        "project_update_", "project_log_", "sale_create_", "sale_confirm_", "invoice_create_",
        "invoice_post_", "purchase_create_", "planning_create_", "documents_upload_"
    )):
        return True

    return False


def _extract_mutation_summary(mutation_tool_calls: List[Dict[str, Any]]) -> tuple:
    details = []
    action_type = "Action"
    total_hours = 0.0

    for tc in mutation_tool_calls:
        fname = tc.get("function", {}).get("name", "")
        raw_args = tc.get("function", {}).get("arguments", "{}")
        args = _try_extract_valid_json(raw_args) if isinstance(raw_args, str) else (raw_args or {})
        args = args or {}

        model_name = str(args.get("model_name") or args.get("model") or args.get("res_model") or "").lower()
        vals = args.get("values") if isinstance(args.get("values"), dict) else args

        if "timesheet" in fname or model_name in ("account.analytic.line", "account_analytic_line"):
            action_type = "Timesheet Logging"
            date_val = args.get("date") or datetime.now().strftime("%Y-%m-%d")
            unit_amt = float(args.get("unit_amount") or args.get("hours") or 0.0)
            desc = args.get("name") or args.get("description") or "Work logged"
            total_hours += unit_amt
            short_desc = (desc[:80] + "...") if len(desc) > 80 else desc
            details.append(f"• <b>{date_val}</b>: {unit_amt:.1f} h — <i>{short_desc}</i>")

        elif "project" in fname or model_name == "project.project":
            action_type = "Project Creation"
            p_name = vals.get("name") or "New Project"
            partner = vals.get("partner_id")
            partner_str = f" (Client Partner ID: {partner})" if partner else ""
            details.append(f"• <b>Project Name:</b> {p_name}{partner_str}")

        elif "task" in fname or model_name == "project.task":
            action_type = "Task Action"
            t_name = vals.get("name") or "New Task"
            proj = vals.get("project_id")
            proj_str = f" (Project ID: {proj})" if proj else ""
            details.append(f"• <b>Task Title:</b> {t_name}{proj_str}")

        elif "sale" in fname or model_name in ("sale.order", "sale.order.line"):
            action_type = "Sales Order Action"
            partner = vals.get("partner_id")
            order_id = args.get("order_id")
            prod = vals.get("product_id") or vals.get("name")
            if partner:
                details.append(f"• <b>Customer/Partner ID:</b> {partner}")
            if order_id:
                details.append(f"• <b>Sales Order ID:</b> {order_id}")
            if prod:
                qty = vals.get("product_uom_qty", 1.0)
                price = vals.get("price_unit")
                price_str = f" @ ${price:.2f}" if price is not None else ""
                details.append(f"• <b>Item:</b> {prod} (Qty: {qty}{price_str})")

        elif "invoice" in fname or model_name in ("account.move", "account.move.line"):
            action_type = "Invoice Action"
            partner = vals.get("partner_id")
            move_type = vals.get("move_type", "out_invoice")
            inv_type = "Customer Invoice" if move_type == "out_invoice" else "Vendor Bill"
            partner_str = f" for Partner ID {partner}" if partner else ""
            details.append(f"• <b>Type:</b> {inv_type}{partner_str}")

        elif model_name in ("product.product", "product.template"):
            action_type = "Product Creation"
            name = vals.get("name") or "New Product"
            price = vals.get("list_price") or vals.get("price")
            price_str = f" (Price: ${float(price):.2f})" if price is not None else ""
            details.append(f"• <b>Product Name:</b> {name}{price_str}")

        elif model_name == "res.partner":
            action_type = "Contact Action"
            name = vals.get("name") or "New Contact"
            email = vals.get("email")
            email_str = f" ({email})" if email else ""
            details.append(f"• <b>Contact:</b> {name}{email_str}")

        else:
            action_type = "Odoo Action"
            fn_desc = fname.replace("_", " ").title()
            details.append(f"• <b>Operation:</b> {fn_desc} on {model_name or 'Odoo Record'}")

    summary_html = "\n".join(details) if details else "• Action details prepared."
    
    if action_type == "Timesheet Logging":
        confirm_text = (
            f"<b>📋 {action_type} Confirmation Required</b>\n\n"
            f"I have prepared the following timesheet entries for Odoo:\n\n"
            f"{summary_html}\n\n"
            f"<b>Total Hours:</b> {total_hours:.1f} h\n"
            f"<b>Status:</b> No records have been logged in Odoo yet.\n\n"
            f"Please confirm to proceed with logging these entries."
        )
        cb_data = "confirm_timesheets"
    else:
        confirm_text = (
            f"<b>📋 {action_type} Confirmation Required</b>\n\n"
            f"I have prepared the following details for Odoo:\n\n"
            f"{summary_html}\n\n"
            f"<b>Status:</b> No records have been created or modified in Odoo yet.\n\n"
            f"Please confirm to proceed with executing this action."
        )
        cb_data = "confirm_action"

    buttons = [
        [
            {"text": "✅ Confirm & Execute", "callback_data": cb_data},
            {"text": "❌ Cancel", "callback_data": "cancel_action"}
        ]
    ]

    return confirm_text, buttons


_pending_mutations: Dict[int, List[Dict[str, Any]]] = {}


def _consolidate_timesheet_tool_calls(tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Consolidate multiple timesheet tool calls sharing the same date
    into one single consolidated tool call per date.
    """
    date_buckets = {}
    non_timesheet_calls = []

    for tc in tool_calls:
        fname = tc.get("function", {}).get("name", "")
        raw_args = tc.get("function", {}).get("arguments", "{}")
        args = _try_extract_valid_json(raw_args) if isinstance(raw_args, str) else (raw_args or {})
        args = args or {}
        model_name = str(args.get("model_name") or args.get("model") or args.get("res_model") or "").lower()

        is_ts = "timesheet" in fname or model_name in ("account.analytic.line", "account_analytic_line")
        if not is_ts:
            non_timesheet_calls.append(tc)
            continue

        vals = args.get("values") if isinstance(args.get("values"), dict) else args
        date_val = resolve_date_string(vals.get("date") or args.get("date"))
        unit_amt = float(vals.get("unit_amount") or args.get("unit_amount") or args.get("hours") or 0.0)
        desc = vals.get("name") or args.get("name") or args.get("description") or "Work performed"
        task_id = vals.get("task_id") or args.get("task_id")
        project_id = vals.get("project_id") or args.get("project_id")

        if date_val not in date_buckets:
            date_buckets[date_val] = {
                "task_id": task_id,
                "project_id": project_id,
                "total_hours": 0.0,
                "descriptions": [],
                "original_tc": tc
            }

        date_buckets[date_val]["total_hours"] += unit_amt
        if desc and desc not in date_buckets[date_val]["descriptions"]:
            date_buckets[date_val]["descriptions"].append(desc)

    consolidated_calls = []
    for date_val, bdata in date_buckets.items():
        combined_desc = "\n".join(f"• {d}" for d in bdata["descriptions"]) if bdata["descriptions"] else "Work performed"
        new_args = {
            "task_id": bdata["task_id"],
            "date": date_val,
            "unit_amount": round(bdata["total_hours"], 2),
            "name": combined_desc
        }
        if bdata["project_id"]:
            new_args["project_id"] = bdata["project_id"]

        consolidated_tc = {
            "id": bdata["original_tc"].get("id", f"ts_{date_val}"),
            "type": "function",
            "function": {
                "name": bdata["original_tc"].get("function", {}).get("name", "project_log_timesheet"),
                "arguments": json.dumps(new_args)
            }
        }
        consolidated_calls.append(consolidated_tc)

    return non_timesheet_calls + consolidated_calls


async def _chat_with_retry(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    tool_choice: str = "auto",
    max_retries: int = 5,
) -> Dict[str, Any]:
    """
    Call the LLM with exponential backoff on transient errors (429, 5xx).
    Also handles the case where tool_choice forces a tool call.
    """
    delay = 2.0
    last_exc: Optional[Exception] = None

    for attempt in range(max_retries):
        try:
            return await chat(
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
            )
        except Exception as exc:
            last_exc = exc
            msg = str(exc).lower()

            # Extract detailed error body if available
            response_text = ""
            if hasattr(exc, 'response') and getattr(exc, 'response') is not None:
                try:
                    response_text = exc.response.text
                    msg += " " + response_text.lower()
                except Exception:
                    pass

            # Rate limit, server error, or SSL socket glitch → backoff and retry
            if any(code in msg for code in ("429", "500", "502", "503", "timeout", "ssl", "bad record mac", "connection")):
                # Check for dynamic retry delay specified by the provider (e.g. Gemini free tier)
                match = re.search(r'please retry in (\d+(?:\.\d+)?)s', msg)
                if match:
                    current_delay = float(match.group(1)) + 1.5  # Add a small buffer to be safe
                    logger.warning(
                        f"Rate limit hit. Dynamic retry requested by provider. "
                        f"Waiting {current_delay:.1f}s before attempt {attempt + 2}..."
                    )
                else:
                    current_delay = delay
                    logger.warning(
                        f"Transient error on attempt {attempt + 1}: {exc} "
                        f"— retrying in {current_delay:.0f}s"
                    )
                    delay *= 2  # Exponential backoff for next fallback attempts
                
                await asyncio.sleep(current_delay)
            else:
                raise  # non-retriable error, fail fast

    raise last_exc  # type: ignore[misc]


def _try_extract_valid_json(s: str) -> Optional[Dict[str, Any]]:
    """Try to extract a valid JSON object from a possibly malformed string."""
    if not isinstance(s, str):
        return None
    s = s.strip()
    
    # Try direct parse first
    try:
        val = json.loads(s)
        if isinstance(val, dict):
            return val
    except Exception:
        pass

    # Find balanced braces
    open_indices = [i for i, c in enumerate(s) if c == '{']
    close_indices = [i for i, c in enumerate(s) if c == '}']
    
    # Try matching pairs, starting with the longest/most complete match
    for start in open_indices:
        for end in reversed(close_indices):
            if end > start:
                substring = s[start:end+1]
                try:
                    val = json.loads(substring)
                    if isinstance(val, dict):
                        return val
                except Exception:
                    pass
    return None


def _extract_composite_payload(text: str) -> Optional[Dict[str, Any]]:
    """
    Search for and extract embedded JSON objects containing buttons or composite responses
    from anywhere within the text string. Supports is_composite_response, inline_keyboard, or buttons keys.
    """
    if not text:
        return None

    # Find candidate JSON indicators
    indicators = ["is_composite_response", "inline_keyboard", "buttons"]
    found_indicator = False
    for ind in indicators:
        if ind in text:
            found_indicator = True
            break
            
    if not found_indicator:
        # Check for fallback markdown text buttons like '[ ✅ Confirm & Execute ] [ ❌ Cancel ]'
        has_confirm = bool(re.search(r'\[\s*✅?\s*Confirm.*?\s*\]', text, re.IGNORECASE))
        has_cancel = bool(re.search(r'\[\s*❌?\s*Cancel.*?\s*\]', text, re.IGNORECASE))

        if has_confirm or has_cancel:
            clean_text = re.sub(r'\[\s*✅?\s*Confirm.*?\s*\]\s*\[\s*❌?\s*Cancel.*?\s*\]', '', text, flags=re.IGNORECASE).strip()
            clean_text = re.sub(r'\[\s*✅?\s*Confirm.*?\s*\]', '', clean_text, flags=re.IGNORECASE).strip()
            clean_text = re.sub(r'\[\s*❌?\s*Cancel.*?\s*\]', '', clean_text, flags=re.IGNORECASE).strip()

            buttons = [
                [
                    {"text": "✅ Confirm & Execute", "callback_data": "confirm_action"},
                    {"text": "❌ Cancel", "callback_data": "cancel_action"}
                ]
            ]
            return {
                "parsed": {"converted_text_buttons": True},
                "text": clean_text,
                "buttons": buttons,
                "raw_json_str": ""
            }

        return None

    # Search for JSON blocks {...}
    for match in re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL):
        json_str = match.group(0)
        try:
            val = json.loads(json_str)
            if isinstance(val, dict):
                is_comp = val.get("is_composite_response")
                has_kb = "inline_keyboard" in val or "buttons" in val
                
                if is_comp or has_kb:
                    start_brace = match.start()
                    end_brace = match.end()
                    prefix = text[:start_brace].strip()
                    suffix = text[end_brace:].strip()
                    
                    # Clean markdown code blocks from prefix/suffix
                    prefix = re.sub(r'```json\s*$', '', prefix, flags=re.IGNORECASE).strip()
                    suffix = re.sub(r'^\s*```', '', suffix).strip()
                    
                    clean_surrounding = (prefix + "\n\n" + suffix).strip() if (prefix and suffix) else (prefix or suffix)
                    json_text = val.get("text", "").strip()
                    
                    buttons = val.get("buttons") or val.get("inline_keyboard") or []
                    
                    combined_text = json_text or clean_surrounding or "Please select an option:"

                    if not buttons:
                        bullet_lines = re.findall(r'^[📋•\-*]\s*(.+)$', combined_text, re.MULTILINE)
                        if bullet_lines:
                            extracted_btns = []
                            row = []
                            for b in bullet_lines:
                                label = b.strip()
                                clean_lbl = re.sub(r'^[📋❌•\-*]\s*', '', label).strip()
                                row.append({"text": clean_lbl if clean_lbl else label, "callback_data": clean_lbl if clean_lbl else label})
                                if len(row) == 2:
                                    extracted_btns.append(row)
                                    row = []
                            if row:
                                extracted_btns.append(row)
                            if extracted_btns:
                                buttons = extracted_btns
                                combined_text = re.sub(r'^[📋•\-*]\s*.+$\n?', '', combined_text, flags=re.MULTILINE).strip()

                    return {
                        "parsed": val,
                        "text": combined_text,
                        "buttons": buttons,
                        "raw_json_str": json_str
                    }
        except Exception:
            pass

    return None





async def _execute_tool_call(
    user_id: int,
    tool_call: Dict[str, Any],
    accumulated_pdfs: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Execute a single tool call and return a 'tool' role message.

    Handles:
    • Malformed JSON arguments → falls back to {} and cleans message history to prevent 400 Bad Request
    • Tool execution errors → passes error string back to model
    """
    tool_name = tool_call["function"]["name"]
    raw_args = tool_call["function"].get("arguments", "{}")

    # Sanitize tool name to handle weak model token leakage (e.g., trailing <|channel|>commentary)
    match = re.match(r'^[a-zA-Z0-9_]+', tool_name)
    if match:
        tool_name = match.group(0)
        tool_call["function"]["name"] = tool_name

    # ── 1. Parse arguments with robust recovery ──────────────────────────────
    arguments = None
    if isinstance(raw_args, dict):
        arguments = raw_args
    else:
        arguments = _try_extract_valid_json(raw_args)

    if arguments is None:
        logger.warning(f"Bad args for {tool_name} (failed to parse JSON): {raw_args!r}")
        arguments = {}
        # Update the tool call arguments to prevent 400 Bad Request API crashes
        tool_call["function"]["arguments"] = "{}"
    else:
        # Auto-recover stringified array/object parameters (domain, fields, values, etc.)
        for key in ["domain", "fields", "values", "ids", "record_ids", "messages", "values_list"]:
            if key in arguments and isinstance(arguments[key], str):
                val = arguments[key].strip()
                if (val.startswith("[") and val.endswith("]")) or (val.startswith("{") and val.endswith("}")):
                    try:
                        parsed_val = json.loads(val)
                        if isinstance(parsed_val, (list, dict)):
                            arguments[key] = parsed_val
                            logger.info(f"Auto-recovered stringified parameter '{key}' for tool '{tool_name}'")
                    except Exception:
                        pass
        # Update the raw tool call arguments to be valid, standardized JSON
        tool_call["function"]["arguments"] = json.dumps(arguments)

    logger.info(f"→ Tool: {tool_name} | args: {json.dumps(arguments)[:200]}")

    # Automatically resolve relative date strings (e.g. "yesterday", "today") to exact ISO dates YYYY-MM-DD
    if isinstance(arguments, dict):
        model_name = str(arguments.get("model_name") or arguments.get("model") or arguments.get("res_model") or "").lower()
        if "timesheet" in tool_name or model_name in ("account.analytic.line", "account_analytic_line") or "date" in arguments:
            if "date" in arguments and arguments["date"]:
                arguments["date"] = resolve_date_string(arguments["date"])
            if "values" in arguments and isinstance(arguments["values"], dict) and "date" in arguments["values"] and arguments["values"]["date"]:
                arguments["values"]["date"] = resolve_date_string(arguments["values"]["date"])

    # ── 2. Execute ───────────────────────────────────────────────────────────
    access_denied = False
    result = None
    
    session_user_id = _active_elevations.get(user_id) or user_id
    if session_user_id != user_id:
        logger.info(f"Using escalated session {session_user_id} for tool execution")

    try:
        result = await call_tool(session_user_id, tool_name, arguments)
        content = admin_manager.extract_text_from_result(result)
        logger.info(f"← Tool result ({len(content)} chars): {content[:200]}")
        if admin_manager.is_access_denied_error(content):
            access_denied = True
    except Exception as exc:
        if admin_manager.is_access_denied_error(exc):
            access_denied = True
        else:
            content = (
                f"Tool '{tool_name}' failed with error: {exc}. "
                "Please inform the user and suggest alternatives."
            )
            logger.error(f"Tool {tool_name} error: {exc}")

    if access_denied:
        user_name = admin_manager.get_telegram_user_name(user_id)
        
        # Notify user on normal bot
        if admin_manager.user_bot:
            try:
                await admin_manager.user_bot.send_message(
                    chat_id=user_id,
                    text="🔑 *Access Denied.* Requesting permission from administrators. Please wait...",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to send warning to user: {e}")
                
        # Create request and notify admin
        req = await admin_manager.create_access_request(user_id, user_name, tool_name, arguments)
        await admin_manager.notify_admins(req)
        
        # Wait for approval
        try:
            await asyncio.wait_for(req["event"].wait(), timeout=60.0)
        except asyncio.TimeoutError:
            req["status"] = "timeout"
            
        if req["status"] == "approved":
            admin_user_id = req["approved_by"]
            admin_key = f"admin_{admin_user_id}"
            # Elevate all subsequent tool calls in the current query execution loop
            _active_elevations[user_id] = admin_key
            logger.info(f"Re-running tool {tool_name} for user {user_id} using admin {admin_key}")
            if admin_manager.user_bot:
                try:
                    await admin_manager.user_bot.send_message(
                        chat_id=user_id,
                        text="✅ *Access Approved.* Retrieving data...",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
            try:
                result = await call_tool(admin_key, tool_name, arguments)
                content = admin_manager.extract_text_from_result(result)
            except Exception as e:
                content = f"Failed to fetch data even with admin privileges: {e}"
        elif req["status"] == "denied":
            content = "❌ *Access Denied.* The administrator rejected your request to access this record."
        else: # timeout
            content = "⚠️ *Access Request Timeout.* The administrator did not respond in time."

    # Check if this content is a JSON payload containing base64 PDF reports
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            # Check for is_composite_response format
            if parsed.get("is_composite_response"):
                reports = parsed.get("pdf_reports", [])
                for r in reports:
                    if r.get("pdf_base64"):
                        # Accumulate original PDF with base64 for Telegram delivery
                        accumulated_pdfs.append(r.copy())
                        # Strip base64 payload from content passed to LLM to save tokens
                        r["pdf_base64"] = "[BASE64_DATA_STRIPPED]"
                content = json.dumps(parsed)
            # Check for direct is_pdf_report format
            elif parsed.get("is_pdf_report"):
                if parsed.get("pdf_base64"):
                    accumulated_pdfs.append(parsed.copy())
                    parsed["pdf_base64"] = "[BASE64_DATA_STRIPPED]"
                content = json.dumps(parsed)
    except Exception:
        pass

    return {
        "role": "tool",
        "tool_call_id": tool_call["id"],
        "content": content,
    }


def _clean_response(content: str) -> str:
    """Strip XML thought/planning blocks and raw text signature blocks for clean chat output."""
    if not content:
        return ""
    # Remove <thought>...</thought> blocks (including multiline)
    content = re.sub(r'<thought>.*?</thought>', '', content, flags=re.DOTALL)
    # Remove <planning>...</planning> blocks (including multiline)
    content = re.sub(r'<planning>.*?</planning>', '', content, flags=re.DOTALL)
    # Strip any leftover open/close tags
    content = re.sub(r'</?(thought|planning)>', '', content, flags=re.IGNORECASE)
    
    # Strip formal letter signature blocks from instant chat text.
    # The signature identity is read from brand_config so this works for any
    # configured brand rather than a hardcoded name.
    from brand_config import SIGNATURE_NAME, SIGNATURE_EMAIL, SIGNATURE_PHONE

    _sig_start = re.escape(SIGNATURE_NAME)
    _sig_end = "|".join(
        re.escape(v) for v in (SIGNATURE_PHONE, SIGNATURE_EMAIL) if v
    )
    if _sig_start and _sig_end:
        content = re.sub(
            rf'(?:\r?\n|\s)*(?:\*\*\*|---)?\s*{_sig_start}[\s\S]*?(?:{_sig_end}).*?(?:\r?\n|$)',
            '',
            content,
            flags=re.IGNORECASE,
        )
    return content.strip()


async def process_query(user_id: int, prompt: str) -> str:
    """Wrapper to clean up active session elevation at the end of the query execution."""
    _active_elevations.pop(user_id, None)
    try:
        return await _process_query_impl(user_id, prompt)
    finally:
        _active_elevations.pop(user_id, None)


async def _process_query_impl(user_id: int, prompt: str) -> str:
    """
    Process a user message with full conversation memory and multi-tool support.
    """
    # 0a. Check for explicit cancellation (button callback or short single-word message)
    p_clean = prompt.lower().strip()
    words = p_clean.split()
    is_explicit_cancel = (p_clean == "cancel_action") or (
        len(words) <= 3 and any(w in ("cancel", "abort", "stop", "nevermind", "cancel action") for w in (p_clean, words[0]))
    )
    if is_explicit_cancel:
        _pending_mutations.pop(user_id, None)
        return "Operation cancelled. No changes were made to Odoo."

    pdf_reports = []

    # 0b. Check if user is confirming a pending mutation stored in memory
    if _check_is_user_confirmed(prompt) and user_id in _pending_mutations:
        pending_calls = _pending_mutations.pop(user_id)
        logger.info(f"User confirmed pending action. Executing {len(pending_calls)} pending mutation tool call(s) directly...")
        
        details = []
        has_timesheets = False
        total_hours = 0.0
        created_ids: Dict[str, int] = {}

        for tc in pending_calls:
            # Dynamically substitute 0 placeholders with freshly created IDs
            if created_ids:
                raw_args_str = json.dumps(tc.get("function", {}).get("arguments", {}))
                for id_key, real_id in created_ids.items():
                    raw_args_str = raw_args_str.replace(f'"{id_key}": 0', f'"{id_key}": {real_id}')
                tc["function"]["arguments"] = json.loads(raw_args_str)

            res_msg = await _execute_tool_call(user_id, tc, pdf_reports)
            
            # Extract returned ID if a record was created
            try:
                res_content = res_msg.get("content", "")
                parsed_res = json.loads(res_content) if isinstance(res_content, str) else res_content
                if isinstance(parsed_res, dict):
                    created_id = parsed_res.get("result") or parsed_res.get("id") or parsed_res.get("created_id")
                    if isinstance(created_id, int):
                        raw_args_check = tc.get("function", {}).get("arguments", {})
                        m_name = str(raw_args_check.get("model_name") or raw_args_check.get("model") or "").lower()
                        if m_name == "res.partner": created_ids["partner_id"] = created_id
                        elif m_name in ("product.product", "product.template"): created_ids["product_id"] = created_id
                        elif m_name == "project.project": created_ids["project_id"] = created_id
            except Exception: pass

            raw_args = tc.get("function", {}).get("arguments", "{}")
            args = _try_extract_valid_json(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            args = args or {}
            
            fname = tc.get("function", {}).get("name", "")
            model_name = str(args.get("model_name") or args.get("model") or args.get("res_model") or "").lower()
            vals = args.get("values") if isinstance(args.get("values"), dict) else args

            is_ts = "timesheet" in fname or model_name in ("account.analytic.line", "account_analytic_line")

            if is_ts:
                has_timesheets = True
                date_val = args.get("date") or "Today"
                unit_amt = float(args.get("unit_amount") or 0.0)
                desc = args.get("name") or "Work logged"
                total_hours += unit_amt
                short_desc = desc.replace("\n", "; ")
                if len(short_desc) > 90: short_desc = short_desc[:90] + "..."
                details.append(f"• <b>{date_val}:</b> {unit_amt:.1f} h — <i>{short_desc}</i>")

            elif model_name == "res.partner" or "partner" in fname or "contact" in fname:
                name = vals.get("name") or args.get("name") or "New Contact"
                details.append(f"• <b>Contact Created:</b> {name}")

            elif model_name in ("project.project",) or "project" in fname:
                name = vals.get("name") or args.get("name") or "New Project"
                details.append(f"• <b>Project Created:</b> {name}")

            elif model_name in ("project.task",) or "task" in fname:
                name = vals.get("name") or args.get("name") or "New Task"
                details.append(f"• <b>Task Created:</b> {name}")

            elif model_name in ("product.product", "product.template") or "product" in fname:
                name = vals.get("name") or args.get("name") or "New Product"
                price = vals.get("list_price") or vals.get("price")
                price_str = f" (${float(price):.2f})" if price else ""
                details.append(f"• <b>Product Created:</b> {name}{price_str}")

            elif model_name in ("sale.order", "sale.order.line") or "sale" in fname:
                lines = vals.get("order_line", [])
                total_amt = 0.0
                qty_count = 0
                for l in lines:
                    if isinstance(l, (list, tuple)) and len(l) >= 3 and isinstance(l[2], dict):
                        l_dict = l[2]
                        q = float(l_dict.get("product_uom_qty") or l_dict.get("qty") or 1.0)
                        p = float(l_dict.get("price_unit") or l_dict.get("price") or 0.0)
                        total_amt += q * p
                        qty_count += int(q)
                amt_str = f" (${total_amt:.2f})" if total_amt > 0 else ""
                qty_str = f" ({qty_count} units)" if qty_count > 0 else ""
                details.append(f"• <b>Sales Order Created</b>{qty_str}{amt_str}")

            else:
                fn_desc = fname.replace("_", " ").title()
                details.append(f"• <b>Action Executed:</b> {fn_desc}")

        summary_html = "\n".join(details)
        if has_timesheets and total_hours > 0:
            header = f"Logged the following timesheet entries in Odoo (Total: {total_hours:.1f} h):"
        else:
            header = "Executed the following changes in Odoo:"

        has_transaction = any(
            "sale" in str(tc.get("function", {}).get("name", "")).lower() or
            "sale.order" in str(tc.get("function", {}).get("arguments", "")).lower() or
            "project.project" in str(tc.get("function", {}).get("arguments", "")).lower() or
            "project.task" in str(tc.get("function", {}).get("arguments", "")).lower()
            for tc in pending_calls
        )

        if not has_transaction and created_ids:
            created_desc = ", ".join([f"{k}={v}" for k, v in created_ids.items()])
            logger.info(f"Dependency creations complete ({created_desc}). Continuing agent loop to complete user request...")
            prompt = (
                f"User confirmed pending creations ({summary_html.replace('<b>', '').replace('</b>', '')}). "
                f"Freshly created Odoo IDs: {created_desc}. "
                f"Now continue and complete the remaining requested transaction (e.g. create the sales order, project, or task)."
            )
        else:
            return (
                f"<b>✅ Action Executed Successfully!</b>\n\n"
                f"{header}\n\n"
                f"{summary_html}"
            )

    tools = await get_tools()

    # 1. Load history and append the new user message
    history = get_history(user_id)
    new_user_msg = {"role": "user", "content": prompt}
    messages = history + [new_user_msg]

    # Track everything new to persist after this turn
    new_messages_to_save: List[Dict[str, Any]] = [new_user_msg]

    # Allow model to decide naturally whether it needs to invoke tools or converse with user
    tool_choice = "auto"

    # 2. Agentic loop
    for round_num in range(MAX_TOOL_ROUNDS):
        logger.info(
            f"LLM round {round_num + 1} | tool_choice={tool_choice} "
            f"| messages in context: {len(messages)}"
        )

        response = await _chat_with_retry(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
        )

        choice = response["choices"][0]
        assistant_msg = choice["message"]
        finish_reason = choice.get("finish_reason", "")

        # Normalise
        assistant_msg.setdefault("role", "assistant")
        messages.append(assistant_msg)

        tool_calls = assistant_msg.get("tool_calls") or []

        # ── Case A: No tool calls → final answer ────────────────────────────
        if not tool_calls:
            final_content: str = assistant_msg.get("content") or ""

            # Sub-case: model was forced to use a tool but gave text instead
            # (some weak models ignore tool_choice="required").
            # On round 0 only, retry once with a nudge in the system context.
            if (
                round_num == 0
                and not final_content.strip()
                or (
                    round_num == 0
                    and tool_choice == "required"
                    and not tool_calls
                    and final_content
                )
            ):
                logger.warning(
                    "Model ignored tool_choice=required — "
                    "injecting a nudge and retrying"
                )
                messages.pop()               # remove the non-tool assistant msg
                nudge = {
                    "role": "user",
                    "content": (
                        "System Note: You MUST use one of the available tools to answer this question. "
                        "Do NOT answer from memory."
                    ),
                }
                messages.append(nudge)       # inject as a user message at the end
                tool_choice = "required"
                continue  # retry

            # Sub-case: finish_reason == "length" means response was cut off
            if finish_reason == "length":
                suffix = (
                    "\n\n⚠️ _Response was cut off due to length. "
                    "Try asking for fewer items at once._"
                )
                final_content = (final_content or "Response cut off.") + suffix

            if not final_content.strip():
                final_content = (
                    "I couldn't generate a response. "
                    "Please rephrase your question or use /clear to reset."
                )

            new_messages_to_save.append(assistant_msg)
            append_messages(user_id, *new_messages_to_save)
            logger.info(f"✓ Done in {round_num + 1} round(s)")

            final_content = _clean_response(final_content)
            
            # Check if the LLM output a composite JSON response
            buttons = []
            comp_payload = _extract_composite_payload(final_content)
            if comp_payload:
                final_content = comp_payload["text"]
                buttons = comp_payload["buttons"]

            # 1. Auto-detect large data (10+ rows) to generate Claude-style Dark Navy PDF Document
            try:
                from pdf_generator import auto_generate_pdf_if_needed
                has_pdf, pdf_path, clean_pdf_text = auto_generate_pdf_if_needed(final_content)
                if has_pdf and pdf_path:
                    import base64
                    with open(pdf_path, "rb") as pf:
                        pdf_base64 = base64.b64encode(pf.read()).decode("utf-8")
                        pdf_reports.append({
                            "filename": os.path.basename(pdf_path),
                            "pdf_base64": pdf_base64
                        })
                    final_content = clean_pdf_text
            except Exception as pdf_err:
                logger.error(f"Auto-generate PDF failed: {pdf_err}")

            # 2. Auto-detect small/medium tables to render Branded PNG image
            brand_images = []
            if not pdf_reports:
                try:
                    from brand_renderer import auto_render_response
                    has_img, img_path, clean_text = auto_render_response(final_content)
                    if has_img and img_path:
                        brand_images.append({"path": img_path, "caption": "📊 Branded Report"})
                        final_content = clean_text
                except Exception as render_err:
                    logger.error(f"Auto-render image failed: {render_err}")

            if pdf_reports or buttons or brand_images:
                if not buttons:
                    buttons = [[
                        {"text": "🧹 Clear History", "callback_data": "/clear"},
                        {"text": "❓ Help & Examples", "callback_data": "/help"}
                    ]]
                return json.dumps({
                    "is_composite_response": True,
                    "text": final_content,
                    "pdf_reports": pdf_reports,
                    "brand_images": brand_images,
                    "buttons": buttons
                })
            return final_content

        # ── Case B: Execute tool calls sequentially ──────────────────────────
        is_user_confirmed = _check_is_user_confirmed(prompt)
        mutation_tool_calls = [tc for tc in tool_calls if _is_mutation_tool_call(tc)]

        # Check if mutations contain timesheet logging
        has_timesheets = False
        for tc in mutation_tool_calls:
            fname = tc.get("function", {}).get("name", "")
            raw_args = tc.get("function", {}).get("arguments", {})
            model_name = str(raw_args.get("model_name") or raw_args.get("model") or "").lower() if isinstance(raw_args, dict) else ""
            if "timesheet" in fname or model_name in ("account.analytic.line", "account_analytic_line"):
                has_timesheets = True
                break

        # User Directive:
        # 1. Remove confirm/cancel cards for all non-timesheet operations (Contacts, Products, Sales Orders, Projects execute directly).
        # 2. Timesheet confirm cards only appear if user gave data across multiple interactions (len(history) > 2).
        #    If all timesheet data was provided in 1 prompt (len(history) <= 2), execute directly without buttons!
        should_intercept = has_timesheets and (len(history) > 2) and not is_user_confirmed

        if should_intercept:
            consolidated_mutations = _consolidate_timesheet_tool_calls(mutation_tool_calls)
            _pending_mutations[user_id] = consolidated_mutations
            logger.info(f"Intercepted multi-interaction timesheet mutation call(s) for user confirmation")
            
            confirm_text, buttons = _extract_mutation_summary(consolidated_mutations)
            return json.dumps({
                "is_composite_response": True,
                "text": confirm_text,
                "buttons": buttons
            })

        logger.info(f"Executing {len(tool_calls)} tool call(s) sequentially …")
        tool_result_msgs = []
        for tc in tool_calls:
            res_msg = await _execute_tool_call(user_id, tc, pdf_reports)
            tool_result_msgs.append(res_msg)

        messages.extend(tool_result_msgs)
        new_messages_to_save.append(assistant_msg)
        new_messages_to_save.extend(tool_result_msgs)

        # After the first tool call, let the model decide freely
        tool_choice = "auto"

    # ── Safety: hit MAX_TOOL_ROUNDS ──────────────────────────────────────────
    logger.warning("Hit MAX_TOOL_ROUNDS — stopping loop")
    # Try to get the last assistant text content
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and msg.get("content"):
            last_content = msg["content"]
            break
    else:
        last_content = (
            "I got stuck in a processing loop. "
            "Please rephrase your question or use /clear."
        )

    append_messages(user_id, *new_messages_to_save)
    last_content = _clean_response(last_content)

    buttons = []
    comp_payload = _extract_composite_payload(last_content)
    if comp_payload:
        last_content = comp_payload["text"]
        buttons = comp_payload["buttons"]

    brand_images = []
    try:
        from brand_renderer import auto_render_response
        has_img, img_path, clean_text = auto_render_response(last_content)
        if has_img and img_path:
            brand_images.append({"path": img_path, "caption": "📊 Branded Report"})
            last_content = clean_text
    except Exception:
        pass

    if pdf_reports or buttons or brand_images:
        return json.dumps({
            "is_composite_response": True,
            "text": last_content,
            "pdf_reports": pdf_reports,
            "brand_images": brand_images,
            "buttons": buttons
        })
    return last_content