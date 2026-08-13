"""
openrouter.py
─────────────
Async HTTP client for OpenRouter, Groq, Gemini (Google AI Studio), Ollama, and Anthropic (Claude) chat completion APIs.

Uses httpx (async) instead of requests (blocking) so the Telegram bot's
event loop is never stalled while waiting for the LLM.
"""

from __future__ import annotations

import os
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Switcher configuration: "openrouter", "groq", "gemini", "ollama", "claude", or "anthropic"
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openrouter").lower()

# OpenRouter Configuration
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip()

# Groq Configuration
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama3-70b-8192").strip()

# Gemini Configuration (Google AI Studio)
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

# Ollama Configuration
OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "localhost").strip()
OLLAMA_PORT: str = os.getenv("OLLAMA_PORT", "11434").strip()
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", f"http://{OLLAMA_HOST}:{OLLAMA_PORT}").strip()
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b-instruct").strip()

# Anthropic / Claude Configuration
ANTHROPIC_API_KEY: str = (os.getenv("ANTHROPIC_API_KEY", "") or os.getenv("CLAUDE_API_KEY", "")).strip()
CLAUDE_MODEL: str = (os.getenv("CLAUDE_MODEL", "") or os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")).strip()

# Shared clients pool — created once, reused across requests.
_openrouter_client: Optional[httpx.AsyncClient] = None
_groq_client: Optional[httpx.AsyncClient] = None
_gemini_client: Optional[httpx.AsyncClient] = None
_ollama_client: Optional[httpx.AsyncClient] = None
_claude_client: Optional[httpx.AsyncClient] = None


def _get_client(provider: str) -> httpx.AsyncClient:
    global _openrouter_client, _groq_client, _gemini_client, _ollama_client, _claude_client
    
    if provider == "groq":
        if _groq_client is None or _groq_client.is_closed:
            _groq_client = httpx.AsyncClient(
                base_url="https://api.groq.com/openai",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(120.0),
            )
        return _groq_client
    elif provider == "gemini":
        if _gemini_client is None or _gemini_client.is_closed:
            _gemini_client = httpx.AsyncClient(
                base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                headers={
                    "Authorization": f"Bearer {GEMINI_API_KEY}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(120.0),
            )
        return _gemini_client
    elif provider == "ollama":
        if _ollama_client is None or _ollama_client.is_closed:
            base_url = OLLAMA_BASE_URL.strip().rstrip("/")
            if not base_url.endswith("/v1"):
                base_url = f"{base_url}/v1"
            _ollama_client = httpx.AsyncClient(
                base_url=base_url,
                headers={
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(120.0),
            )
        return _ollama_client
    elif provider in ("claude", "anthropic"):
        api_key = (os.getenv("ANTHROPIC_API_KEY", "") or os.getenv("CLAUDE_API_KEY", "")).strip()
        if _claude_client is None or _claude_client.is_closed or _claude_client.headers.get("x-api-key") != api_key:
            _claude_client = httpx.AsyncClient(
                base_url="https://api.anthropic.com",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(120.0),
            )
        return _claude_client
    else:
        if _openrouter_client is None or _openrouter_client.is_closed:
            _openrouter_client = httpx.AsyncClient(
                base_url="https://openrouter.ai",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/odoo-telegram-mcp",
                    "X-Title": "Odoo CRM Telegram Bot",
                },
                timeout=httpx.Timeout(120.0),
            )
        return _openrouter_client


def reset_client(provider: str) -> None:
    global _openrouter_client, _groq_client, _gemini_client, _ollama_client, _claude_client
    p = provider.lower()
    if p in ("claude", "anthropic") and _claude_client is not None:
        _claude_client = None
    elif p == "groq" and _groq_client is not None:
        _groq_client = None
    elif p == "gemini" and _gemini_client is not None:
        _gemini_client = None
    elif p == "openrouter" and _openrouter_client is not None:
        _openrouter_client = None


def _convert_openai_messages_to_anthropic(messages: List[Dict[str, Any]]) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    system_parts = []
    anthropic_msgs: List[Dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content") or ""

        if role == "system":
            if isinstance(content, str) and content.strip():
                system_parts.append(content)
        elif role == "user":
            anthropic_msgs.append({
                "role": "user",
                "content": content
            })
        elif role == "assistant":
            blocks = []
            if isinstance(content, str) and content:
                blocks.append({"type": "text", "text": content})
            tool_calls = msg.get("tool_calls") or []
            for tc in tool_calls:
                fn = tc.get("function", {})
                raw_args = fn.get("arguments", "{}")
                if isinstance(raw_args, str):
                    try:
                        args_dict = json.loads(raw_args)
                    except Exception:
                        args_dict = {}
                else:
                    args_dict = raw_args or {}
                
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "input": args_dict
                })
            anthropic_msgs.append({
                "role": "assistant",
                "content": blocks if blocks else ""
            })
        elif role == "tool":
            tool_call_id = msg.get("tool_call_id", "")
            tool_result_content = content if isinstance(content, str) else json.dumps(content)
            
            tool_block = {
                "type": "tool_result",
                "tool_use_id": tool_call_id,
                "content": tool_result_content
            }
            
            if anthropic_msgs and anthropic_msgs[-1]["role"] == "user" and isinstance(anthropic_msgs[-1]["content"], list):
                anthropic_msgs[-1]["content"].append(tool_block)
            else:
                anthropic_msgs.append({
                    "role": "user",
                    "content": [tool_block]
                })

    system_prompt = "\n\n".join(system_parts) if system_parts else None
    return system_prompt, anthropic_msgs


def _convert_openai_tools_to_anthropic(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    anthropic_tools = []
    for t in tools:
        fn = t.get("function", {})
        anthropic_tools.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}})
        })
    return anthropic_tools


def _convert_anthropic_response_to_openai(res_json: Dict[str, Any]) -> Dict[str, Any]:
    content_list = res_json.get("content", [])
    text_content = ""
    tool_calls = []

    for block in content_list:
        b_type = block.get("type")
        if b_type == "text":
            text_content += block.get("text", "")
        elif b_type == "tool_use":
            tool_calls.append({
                "id": block.get("id", ""),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input", {}))
                }
            })

    stop_reason = res_json.get("stop_reason", "")
    finish_reason = "stop"
    if stop_reason == "tool_use":
        finish_reason = "tool_calls"
    elif stop_reason == "max_tokens":
        finish_reason = "length"

    msg_dict: Dict[str, Any] = {
        "role": "assistant",
        "content": text_content if text_content else None,
    }
    if tool_calls:
        msg_dict["tool_calls"] = tool_calls

    return {
        "choices": [
            {
                "message": msg_dict,
                "finish_reason": finish_reason
            }
        ]
    }


async def chat(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: str = "auto",
    temperature: float = 0.1,
    max_tokens: int = 4096,
) -> Dict[str, Any]:
    """
    Call the configured LLM provider's chat completions endpoint (async).

    Args:
        tool_choice: "auto"     — model decides whether to call a tool
                     "required" — model MUST call a tool (forces tool use)
                     "none"     — model must NOT call any tool

    Returns the full JSON response dict.
    Raises httpx.HTTPStatusError on non-2xx responses.
    """
    provider = os.getenv("LLM_PROVIDER", "openrouter").lower()
    
    if provider in ("claude", "anthropic"):
        model = (os.getenv("CLAUDE_MODEL", "") or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")).strip()
        endpoint = "/v1/messages"
        system_str, anthropic_msgs = _convert_openai_messages_to_anthropic(messages)

        payload: Dict[str, Any] = {
            "model": model,
            "messages": anthropic_msgs,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_str:
            payload["system"] = system_str

        if tools:
            payload["tools"] = _convert_openai_tools_to_anthropic(tools)
            if tool_choice == "required":
                payload["tool_choice"] = {"type": "any"}
            elif tool_choice == "auto":
                payload["tool_choice"] = {"type": "auto"}

        try:
            client = _get_client(provider)
            response = await client.post(endpoint, json=payload)
            logger.debug(f"{provider.upper()} status: {response.status_code}")
            if response.status_code != 200:
                logger.error(f"{provider.upper()} error body: {response.text}")
            response.raise_for_status()
            return _convert_anthropic_response_to_openai(response.json())
        except Exception as err:
            if "ssl" in str(err).lower() or "bad record mac" in str(err).lower():
                logger.warning(f"SSL error on {provider}: {err}. Resetting connection pool...")
                reset_client(provider)
            raise

    if provider == "groq":
        model = os.getenv("GROQ_MODEL", "llama3-70b-8192")
        endpoint = "/v1/chat/completions"
    elif provider == "gemini":
        model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        endpoint = "/v1/chat/completions"
    elif provider == "ollama":
        model = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b-instruct")
        endpoint = "/chat/completions"
    else:
        model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
        endpoint = "/api/v1/chat/completions"

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if tools:
        payload["tools"] = tools
        # Gemini, Groq, and Ollama's OpenAI endpoints do not support tool_choice: "required"
        if provider in ("gemini", "groq", "ollama") and tool_choice == "required":
            payload["tool_choice"] = "auto"
        else:
            payload["tool_choice"] = tool_choice

    client = _get_client(provider)
    response = await client.post(endpoint, json=payload)

    logger.debug(f"{provider.upper()} status: {response.status_code}")

    if response.status_code != 200:
        logger.error(f"{provider.upper()} error body: {response.text}")

    response.raise_for_status()
    return response.json()