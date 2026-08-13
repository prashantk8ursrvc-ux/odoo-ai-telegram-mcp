"""
bot.py
──────
Telegram bot entry point.

Features
────────
• /start  — welcome message
• /clear  — wipe conversation memory for this user
• /help   — show available commands
• All text messages → ai_processor.process_query (with memory)
• Long responses split into ≤4000-char chunks automatically
• Typing indicator while processing
• Friendly error messages
"""

from __future__ import annotations

import os
import asyncio
import logging
import traceback

from dotenv import load_dotenv

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from ai_processor import process_query, _extract_composite_payload
from conversation_store import clear_history
from mcp_client import get_tools, authenticate_user  # pre-warm on startup
import auth_manager
import admin_manager

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
ADMIN_BOT_TOKEN: str = os.getenv("ADMIN_BOT_TOKEN", "")
MAX_MESSAGE_LENGTH = 4000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ─── Helpers ─────────────────────────────────────────────────────────────────


import re

def _sanitize_message_text(text: str) -> str:
    """
    Final cleanup pass for Telegram message text.
    Removes orphaned --- dividers, empty sections, excess blank lines,
    and produces clean readable output.
    """
    if not text:
        return text

    lines = text.split("\n")
    out = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Skip bare markdown/Telegram horizontal rules that are orphaned
        if stripped in ("---", "──────────────", "————————————", "***", "___"):
            # Only keep it if the previous non-blank line AND next non-blank line are real content
            prev_content = next((l.strip() for l in reversed(out) if l.strip()), "")
            next_content = next((l.strip() for l in lines[i+1:] if l.strip()), "")
            # Skip if: nothing before, nothing after, or both sides are also dividers/empty
            if not prev_content or not next_content:
                continue
            if next_content in ("---", "──────────────", "————————————", "***", "___"):
                continue
            # Skip if prev was a heading/section title and next is an emoji heading (i.e. double divider)
            if prev_content.startswith(("🏢", "🌐", "📊", "📄", "✅", "❌", "⚠️", "#")):
                continue
            out.append(line)
            continue

        out.append(line)

    text = "\n".join(out)

    # Collapse 3+ consecutive blank lines into max 2
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Remove blank lines between a heading emoji line and its following --- divider
    text = re.sub(r'((?:^|\n)[^\n]*(?:🏢|🌐|📊|📄|🛒|👥|💼)[^\n]*)\n\n---', r'\1', text)

    # Remove lone --- that has blank lines on BOTH sides
    text = re.sub(r'\n\n---\n\n', '\n\n', text)

    # Remove --- that appear right at the start of the message
    text = re.sub(r'^---\s*\n+', '', text)

    # Remove --- that appear right at the end
    text = re.sub(r'\n+---\s*$', '', text)

    # Collapse again after removals
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def clean_html_for_telegram(text: str) -> str:
    """Sanitize and convert Markdown/HTML payload into Telegram's supported HTML subset."""
    if not text:
        return ""

    # Convert Markdown headers (### Header) to bold text <b>Header</b>
    text = re.sub(r'^(?:#+)\s*(.*?)$', r'<b>\1</b>', text, flags=re.MULTILINE)

    # Convert Markdown bold (**text** or __text__) to <b>text</b>
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.*?)__', r'<b>\1</b>', text)

    # Convert Markdown italic (*text*) to <i>text</i>
    text = re.sub(r'(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)', r'<i>\1</i>', text)

    # Pre-clean list items and breaks into readable standard layouts
    text = re.sub(r'<li>\s*', '• ', text, flags=re.IGNORECASE)
    text = re.sub(r'</li>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</?(ul|ol)>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</?p>', '\n', text, flags=re.IGNORECASE)
    
    # Pre-clean HTML table structures into readable text lines if present as fallback
    if "<tr" in text.lower():
        text = re.sub(r'</tr>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'</t[dh]>', '  |  ', text, flags=re.IGNORECASE)
        text = re.sub(r'<t[dh][^>]*>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'</?table[^>]*>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'</?t(?:head|body|foot)[^>]*>', '', text, flags=re.IGNORECASE)
    
    # Strip any HTML tags except those strictly supported by Telegram
    tag_regex = re.compile(r'<(/?[a-zA-Z0-9]+)(?:\s+[^>]*)?>')
    allowed = {'a', 'b', 'strong', 'i', 'em', 'u', 'ins', 's', 'strike', 'del', 'code', 'pre', 'span'}
    
    def repl(match):
        full_tag = match.group(0)
        tag_name = match.group(1).lower()
        if tag_name.startswith('/'):
            tag_name = tag_name[1:]
        if tag_name in allowed:
            return full_tag
        return ""
        
    cleaned = tag_regex.sub(repl, text)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)

    # Final smart cleanup: remove orphaned dividers, empty sections
    cleaned = _sanitize_message_text(cleaned)

    return cleaned.strip()


async def send_long_message(update: Update, placeholder_msg, text: str, reply_markup=None) -> None:
    """
    Replace the placeholder 'Thinking…' bubble with the response.
    If the response is longer than MAX_MESSAGE_LENGTH, split into chunks.
    Automatically handles HTML cleaning and falls back to plain text on parsing errors.
    """
    text = str(text).strip()
    cleaned_text = clean_html_for_telegram(text)
    target_msg = update.effective_message

    try:
        if len(cleaned_text) <= MAX_MESSAGE_LENGTH:
            await placeholder_msg.edit_text(cleaned_text, parse_mode="HTML", reply_markup=reply_markup)
            return

        # First chunk replaces the placeholder
        await placeholder_msg.edit_text(cleaned_text[:MAX_MESSAGE_LENGTH], parse_mode="HTML")

        # Remaining chunks sent as new messages
        offset = MAX_MESSAGE_LENGTH
        while offset < len(cleaned_text):
            chunk = cleaned_text[offset : offset + MAX_MESSAGE_LENGTH]
            is_last = (offset + MAX_MESSAGE_LENGTH >= len(cleaned_text))
            await target_msg.reply_text(
                chunk, 
                parse_mode="HTML", 
                reply_markup=reply_markup if is_last else None
            )
            offset += MAX_MESSAGE_LENGTH
    except Exception as e:
        logger.warning(f"Failed to send HTML formatted message, falling back to plain text: {e}")
        # Strip all HTML tags completely for plain text fallback
        plain_text = re.sub(r'<[^>]+>', '', cleaned_text)
        
        if len(plain_text) <= MAX_MESSAGE_LENGTH:
            await placeholder_msg.edit_text(plain_text, reply_markup=reply_markup)
            return

        await placeholder_msg.edit_text(plain_text[:MAX_MESSAGE_LENGTH])
        offset = MAX_MESSAGE_LENGTH
        while offset < len(plain_text):
            chunk = plain_text[offset : offset + MAX_MESSAGE_LENGTH]
            is_last = (offset + MAX_MESSAGE_LENGTH >= len(plain_text))
            await target_msg.reply_text(
                chunk, 
                reply_markup=reply_markup if is_last else None
            )
            offset += MAX_MESSAGE_LENGTH


# ─── Command Handlers ─────────────────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    name = user.first_name if user else "there"

    await update.message.reply_text(
        f"👋 Hello, {name}!\n\n"
        "I'm your *Odoo Enterprise ERP Assistant*. I can help you with:\n"
        "• 💼 *CRM & Sales*: Leads, Opportunities, Quotations, Invoicing (`sale.order`, `account.move`)\n"
        "• 👥 *Contacts*: Manage partners, customers, and vendors (`res.partner`)\n"
        "• 🗂️ *Projects & FSM*: Track tasks, project milestones, Field Service worksheets\n"
        "• 📄 *Enterprise Documents*: Manage files, workspaces, tags (`documents.document`)\n"
        "• 🎫 *Helpdesk Tickets*: Track and update support tickets (`helpdesk.ticket`)\n"
        "• 🗓️ *Planning*: Schedule shifts and view scheduled slots (`planning.slot`)\n"
        "• 💬 *WhatsApp & Social*: Manage templates, track social media posts\n\n"
        "Just ask me anything in plain English! I can read, write, create, validate invoices, and render PDF reports across all modules.\n\n"
        "_Commands:_\n"
        "/clear – Reset conversation memory\n"
        "/help  – Show list of capabilities & examples",
        parse_mode="Markdown",
    )


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    clear_history(user_id)
    await update.message.reply_text(
        "🗑️ Conversation memory cleared. Let's start fresh!"
    )


async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    auth_manager.remove_credentials(user_id)
    await update.message.reply_text("You have been logged out. Send any message to log in again.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🤖 *Odoo Enterprise ERP Bot — Help*\n\n"
        "_Commands:_\n"
        "/clear  – Wipe conversation history\n"
        "/logout – Log out of your Odoo account\n"
        "/help   – Show this help\n\n"
        "*Example queries:*\n"
        "• _CRM/Sales:_ Create invoice for quotation 12 or confirm sales order 15.\n"
        "• _Accounting:_ Post/validate invoice 102 or print invoice PDF.\n"
        "• _Documents:_ List all files in folder/workspace 'Finance'.\n"
        "• _Helpdesk:_ Show me new helpdesk tickets assigned to me.\n"
        "• _Planning:_ List my planning shifts or schedule a shift for tomorrow.\n"
        "• _WhatsApp:_ List active templates or track messages.",
        parse_mode="Markdown",
    )


# ─── Message Handler ──────────────────────────────────────────────────────────


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    user = update.effective_user
    if user:
        name = user.first_name or f"User {user.id}"
        admin_manager.save_telegram_user_name(user.id, name)

    user_id = update.effective_user.id
    user_text = update.message.text.strip()

    if user_text.lower() == "logout":
        await cmd_logout(update, context)
        return
    
    state = auth_manager.get_flow_state(user_id)
    if state != auth_manager.AuthState.AUTHENTICATED:
        target_msg = update.effective_message
        if state == auth_manager.AuthState.UNAUTHENTICATED:
            await target_msg.reply_text(
                "👋 Welcome to the *Odoo Enterprise ERP Assistant*!\n\n"
                "To get started, please securely log in to your account.\n\n"
                "📧 Please enter your **Odoo username (email)**:", 
                parse_mode="Markdown"
            )
            auth_manager.set_flow_state(user_id, auth_manager.AuthState.WAITING_USERNAME)
        elif state == auth_manager.AuthState.WAITING_USERNAME:
            auth_manager.set_flow_state(user_id, auth_manager.AuthState.WAITING_PASSWORD, username=user_text)
            await target_msg.reply_text(
                "Excellent! Now, please enter your **password**.\n\n"
                "_(For your security, you may want to delete your message containing the password after sending it.)_", 
                parse_mode="Markdown"
            )
        elif state == auth_manager.AuthState.WAITING_PASSWORD:
            username = auth_manager.get_flow_username(user_id)
            password = user_text
            await target_msg.reply_text("⏳ Verifying your credentials with Odoo. Please wait...")
            await update.effective_chat.send_action(ChatAction.TYPING)
            auth_manager.save_user_credentials(user_id, username, password)
            success = await authenticate_user(user_id)
            if success:
                auth_manager.set_flow_state(user_id, auth_manager.AuthState.AUTHENTICATED)
                first_name = update.effective_user.first_name if update.effective_user else "there"
                await target_msg.reply_text(
                    f"✅ Login successful! Welcome, *{first_name}*.\n\n"
                    "You are securely connected to Odoo. How can I assist you today?",
                    parse_mode="Markdown"
                )
            else:
                auth_manager.remove_credentials(user_id)
                await target_msg.reply_text(
                    "❌ Authentication failed. Please check your credentials and try again.\n\n"
                    "📧 Please enter your **Odoo username (email)**:", 
                    parse_mode="Markdown"
                )
                auth_manager.set_flow_state(user_id, auth_manager.AuthState.WAITING_USERNAME)
        return

    await _process_user_input(update, user_id, user_text)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer(text="Processing...")
    
    user_id = update.effective_user.id
    state = auth_manager.get_flow_state(user_id)
    if state != auth_manager.AuthState.AUTHENTICATED:
        await query.message.reply_text("Please log in first. Send any text message to start.")
        return
    
    # Remove buttons from the original message to prevent double-clicks
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
        
    user_id = update.effective_user.id
    cb_data = str(query.data).strip().lower()
    if any(c in cb_data for c in ("cancel", "abort", "stop")):
        user_text = "Cancel operation. Abort this action."
    else:
        user_text = f"User selected option: {query.data}"
        
    await _process_user_input(update, user_id, user_text)


def _build_inline_keyboard(buttons_data: Any) -> Optional[InlineKeyboardMarkup]:
    if not buttons_data or not isinstance(buttons_data, list):
        return None
    
    keyboard = []
    for row in buttons_data:
        if isinstance(row, dict):
            row = [row]
        elif isinstance(row, str):
            row = [{"text": row, "callback_data": row}]
        
        if not isinstance(row, list):
            continue

        keyboard_row = []
        for btn in row:
            if isinstance(btn, dict):
                text = str(btn.get("text") or btn.get("label") or "Option")
                cb = str(btn.get("callback_data") or btn.get("data") or text)
                keyboard_row.append(InlineKeyboardButton(text=text, callback_data=cb))
            elif isinstance(btn, str):
                keyboard_row.append(InlineKeyboardButton(text=btn, callback_data=btn))
        
        if keyboard_row:
            keyboard.append(keyboard_row)

    return InlineKeyboardMarkup(keyboard) if keyboard else None


def _clean_pdf_filename(raw_filename: str) -> str:
    fn = (raw_filename or "report.pdf").strip()
    fn_lower = fn.lower()

    match = re.search(r'(\d+)', fn)
    rec_id = f"_{match.group(1)}" if match else ""

    if "sale" in fn_lower:
        return f"Sales_Order{rec_id}.pdf"
    elif "invoice" in fn_lower or "account" in fn_lower:
        return f"Invoice{rec_id}.pdf"
    elif "purchase" in fn_lower:
        return f"Purchase_Order{rec_id}.pdf"
    elif "timesheet" in fn_lower or "analytic" in fn_lower:
        return f"Timesheet_Report{rec_id}.pdf"
    elif "project" in fn_lower:
        return f"Project_Report{rec_id}.pdf"
    
    clean_name = re.sub(r'[^a-zA-Z0-9_\-]', '', fn.replace('.pdf', ''))
    words = clean_name.replace('_', ' ').title().split()
    return f"{'_'.join(words) or 'Report'}{rec_id}.pdf"


async def _process_user_input(update: Update, user_id: int, user_text: str) -> None:
    logger.info(f"[user={user_id}] → {user_text!r}")

    target_msg = update.effective_message
    
    # Show typing indicator
    await update.effective_chat.send_action(ChatAction.TYPING)

    # Send placeholder that we'll edit with the real answer
    thinking_msg = await target_msg.reply_text("🤖 Thinking…")

    pdf_reports = []
    brand_images = []
    reply_markup = None
    try:
        answer = await process_query(user_id, user_text)

        if not answer:
            answer = "I received an empty response. Please try again."

        # Parse composite response if it contains PDF reports, brand images, or buttons
        clean_answer = answer.strip()
        if clean_answer.startswith("```json"):
            clean_answer = clean_answer[7:]
        if clean_answer.startswith("```"):
            clean_answer = clean_answer[3:]
        if clean_answer.endswith("```"):
            clean_answer = clean_answer[:-3]
        clean_answer = clean_answer.strip()

        if "is_composite_response" in clean_answer:
            try:
                comp = _extract_composite_payload(clean_answer)
                if comp:
                    parsed = comp["parsed"]
                    answer = comp["text"]
                    pdf_reports = parsed.get("pdf_reports", [])
                    brand_images = parsed.get("brand_images", [])
                    
                    # Build inline keyboard if buttons exist
                    buttons_data = comp.get("buttons", [])
                    if buttons_data:
                        reply_markup = _build_inline_keyboard(buttons_data)
            except Exception as e:
                logger.error(f"Failed to parse composite response: {e}")

        logger.info(f"[user={user_id}] ← {answer[:120]!r}…")

    except Exception:
        logger.error(traceback.format_exc())
        answer = (
            "❌ Something went wrong while processing your request.\n"
            "Please try again or use /clear to reset the conversation."
        )

    try:
        await send_long_message(update, thinking_msg, answer, reply_markup)
    except Exception as e:
        logger.error(f"Failed to edit message: {e}")
        # Strip all HTML tags completely for plain text fallback
        plain_text = re.sub(r'<[^>]+>', '', answer)
        try:
            await target_msg.reply_text(plain_text, reply_markup=reply_markup)
        except Exception as e2:
            logger.error(f"Ultimate fallback message failed: {e2}")

    # Delivery of generated PDFs
    for report in pdf_reports:
        raw_fn = report.get("filename", "report.pdf")
        filename = _clean_pdf_filename(raw_fn)
        pdf_base64 = report.get("pdf_base64", "")
        if pdf_base64:
            try:
                import base64
                pdf_bytes = base64.b64decode(pdf_base64)
                
                # Write to temp file
                os.makedirs("temp_reports", exist_ok=True)
                temp_path = os.path.join("temp_reports", filename)
                with open(temp_path, "wb") as f:
                    f.write(pdf_bytes)
                
                # Send to Telegram cleanly without duplicate text bubble
                with open(temp_path, "rb") as f:
                    await target_msg.reply_document(
                        document=f,
                        filename=filename
                    )
                
                # Clean up
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception as doc_err:
                logger.error(f"Failed to send PDF report document: {doc_err}")
                await target_msg.reply_text(
                    f"⚠️ Failed to deliver PDF file '{filename}' due to an error: {doc_err}"
                )

    # Delivery of generated Brand PNG Images
    for img_item in brand_images:
        img_path = img_item.get("path")
        caption = img_item.get("caption", "📄 Branded Report")
        if img_path and os.path.exists(img_path):
            try:
                with open(img_path, "rb") as f:
                    await target_msg.reply_photo(
                        photo=f,
                        caption=caption
                    )
            except Exception as img_err:
                logger.error(f"Failed to send branded image photo: {img_err}")



# ─── Startup hook ─────────────────────────────────────────────────────────────


import time

async def cleanup_temp_reports_daemon() -> None:
    """Background task that purges temporary PDF/PNG reports older than 1 hour."""
    while True:
        try:
            temp_dir = "temp_reports"
            if os.path.exists(temp_dir):
                now = time.time()
                for fname in os.listdir(temp_dir):
                    fpath = os.path.join(temp_dir, fname)
                    if os.path.isfile(fpath) and (now - os.path.getmtime(fpath)) > 3600:
                        try:
                            os.remove(fpath)
                            logger.info(f"Cleaned up old temp report: {fname}")
                        except Exception:
                            pass
        except Exception as e:
            logger.error(f"Error in temp reports cleanup daemon: {e}")
        await asyncio.sleep(1800)


async def on_startup(app: Application) -> None:
    """Pre-warm the MCP server connection and start background cleanup daemon."""
    logger.info("Pre-warming MCP session …")
    try:
        tools = await get_tools()
        logger.info(f"MCP ready: {len(tools)} tool(s) loaded ✓")
    except Exception as exc:
        logger.warning(f"MCP pre-warm failed (will retry on first message): {exc}")

    # Launch background temp report cleanup daemon
    asyncio.create_task(cleanup_temp_reports_daemon())
    logger.info("Temporary reports cleanup daemon started ✓")


# ─── Main ─────────────────────────────────────────────────────────────────────


async def run_bots() -> None:
    from telegram.request import HTTPXRequest
    
    # Use a custom request client with a higher timeout to accommodate slow network conditions
    request_client = HTTPXRequest(connect_timeout=20.0, read_timeout=20.0)

    # 1. Initialize user bot
    user_app = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(request_client)
        .post_init(on_startup)
        .build()
    )
    user_app.add_handler(CommandHandler("start", cmd_start))
    user_app.add_handler(CommandHandler("clear", cmd_clear))
    user_app.add_handler(CommandHandler("logout", cmd_logout))
    user_app.add_handler(CommandHandler("help", cmd_help))
    user_app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    user_app.add_handler(CallbackQueryHandler(handle_callback))
    
    admin_manager.set_user_bot(user_app.bot)
    admin_manager.set_process_input_callback(_process_user_input)
    
    # 2. Check and initialize admin bot if token is present
    admin_app = None
    if ADMIN_BOT_TOKEN:
        logger.info("ADMIN_BOT_TOKEN detected. Starting Admin Bot...")
        admin_app = (
            Application.builder()
            .token(ADMIN_BOT_TOKEN)
            .request(request_client)
            .build()
        )
        admin_app.add_handler(CommandHandler("start", admin_manager.admin_cmd_start))
        admin_app.add_handler(CommandHandler("logout", admin_manager.admin_cmd_logout))
        admin_app.add_handler(CommandHandler("clear", cmd_clear))
        admin_app.add_handler(CommandHandler("help", cmd_help))
        admin_app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, admin_manager.admin_handle_message)
        )
        admin_app.add_handler(CallbackQueryHandler(admin_manager.admin_handle_callback))
        
        admin_manager.set_admin_bot(admin_app.bot)
    else:
        logger.warning("ADMIN_BOT_TOKEN not configured in .env. Admin features will be disabled.")

    # Initialize applications
    await user_app.initialize()
    await user_app.start()
    await user_app.updater.start_polling(drop_pending_updates=True)
    logger.info("🚀 Normal User Bot is polling for updates...")

    if admin_app:
        try:
            await admin_app.initialize()
            await admin_app.start()
            await admin_app.updater.start_polling(drop_pending_updates=True)
            logger.info("🚀 Admin Bot is polling for updates...")
        except Exception as e:
            logger.error(f"❌ Failed to start Admin Bot: {e}")
            logger.warning("⚠️ Running in NORMAL USER BOT mode only. Admin features will be disabled.")
            admin_app = None
            admin_manager.set_admin_bot(None)

    # Wait until cancelled
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
        logger.info("Stop signal received. Shutting down bots...")
    finally:
        # Shutdown
        try:
            await user_app.updater.stop()
            await user_app.stop()
            await user_app.shutdown()
        except Exception as e:
            logger.error(f"Error shutting down user bot: {e}")
        
        if admin_app:
            try:
                await admin_app.updater.stop()
                await admin_app.stop()
                await admin_app.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down admin bot: {e}")
        
        logger.info("Bots shut down successfully.")


def main() -> None:
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN not set in .env")

    import asyncio
    try:
        asyncio.run(run_bots())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()