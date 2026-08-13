"""
mcp_client.py
─────────────
Persistent MCP session manager.

Maintains multi-tenant, per-user ClientSessions and reconnects automatically.
"""

from __future__ import annotations

import os
import sys
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

import auth_manager

logger = logging.getLogger(__name__)

# ─── Multi-tenant session state ──────────────────────────────────────────────

_sessions: Dict[int, ClientSession] = {}
_stdio_ctxs: Dict[int, Any] = {}
_session_locks: Dict[int, asyncio.Lock] = {}
_global_lock = asyncio.Lock()
_tools_cache: Optional[List[Dict[str, Any]]] = None

def _get_user_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _session_locks:
        _session_locks[user_id] = asyncio.Lock()
    return _session_locks[user_id]


async def _ensure_session(user_id: int) -> ClientSession:
    """Return a live ClientSession for a specific user, creating one if needed."""
    global _sessions, _stdio_ctxs

    async with _get_user_lock(user_id):
        if user_id in _sessions:
            return _sessions[user_id]

        creds = auth_manager.get_credentials(user_id)
        if not creds:
            raise Exception("User not authenticated")

        logger.info(f"Starting MCP server process for user {user_id} …")
        
        env = os.environ.copy()
        env['ODOO_USERNAME'] = creds['username']
        env['ODOO_PASSWORD'] = creds['password']
        
        server = StdioServerParameters(
            command=sys.executable,
            args=["odoo_crm_mcp.py"],
            env=env
        )
        
        _stdio_ctxs[user_id] = stdio_client(server)
        read_ctx, write_ctx = await _stdio_ctxs[user_id].__aenter__()

        session = ClientSession(read_ctx, write_ctx)
        await session.__aenter__()
        await session.initialize()

        _sessions[user_id] = session
        logger.info(f"MCP session for user {user_id} ready ✓")
        return session


async def authenticate_user(user_id: int) -> bool:
    """Attempt to spin up the MCP server to validate credentials."""
    try:
        await _reset_session(user_id)
        await _ensure_session(user_id)
        return True
    except Exception as e:
        logger.error(f"Authentication failed for {user_id}: {e}")
        await _reset_session(user_id)
        return False


async def get_tools() -> List[Dict[str, Any]]:
    """Return OpenAI-style tool definitions, cached globally."""
    global _tools_cache

    async with _global_lock:
        if _tools_cache is not None:
            return _tools_cache

        logger.info("Spawning temporary schema-only MCP session to extract tools...")
        env = os.environ.copy()
        env['SKIP_AUTH_FOR_SCHEMA'] = "1"
        
        server = StdioServerParameters(
            command=sys.executable,
            args=["odoo_crm_mcp.py"],
            env=env
        )
        
        stdio_ctx = stdio_client(server)
        read_ctx, write_ctx = await stdio_ctx.__aenter__()

        session = ClientSession(read_ctx, write_ctx)
        await session.__aenter__()
        await session.initialize()

        tools_result = await session.list_tools()

        result = []
        for tool in tools_result.tools:
            result.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.inputSchema,
                },
            })

        _tools_cache = result
        logger.info(f"Cached {len(result)} MCP tools globally")
        
        # Cleanup dummy session
        try:
            await session.__aexit__(None, None, None)
        except Exception:
            pass
        try:
            await stdio_ctx.__aexit__(None, None, None)
        except Exception:
            pass
        
        return result


async def call_tool(user_id: int, tool_name: str, arguments: Dict[str, Any]) -> Any:
    """Call an MCP tool for a specific user, retrying once if the session has died."""
    for attempt in range(2):
        try:
            session = await _ensure_session(user_id)
            result = await session.call_tool(tool_name, arguments)
            return result
        except Exception as exc:
            logger.warning(f"MCP call failed for user {user_id} (attempt {attempt + 1}): {exc}")
            if attempt == 0:
                await _reset_session(user_id)
            else:
                raise


async def _reset_session(user_id: int) -> None:
    """Tear down the current session for a user so the next call rebuilds it."""
    global _sessions, _stdio_ctxs

    async with _get_user_lock(user_id):
        try:
            if user_id in _sessions:
                await _sessions[user_id].__aexit__(None, None, None)
        except Exception:
            pass
        try:
            if user_id in _stdio_ctxs:
                await _stdio_ctxs[user_id].__aexit__(None, None, None)
        except Exception:
            pass
        
        _sessions.pop(user_id, None)
        _stdio_ctxs.pop(user_id, None)
        logger.info(f"MCP session for user {user_id} reset")