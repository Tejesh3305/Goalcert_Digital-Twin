"""
gateway.py — the LLM Gateway every agent calls.

Single choke-point for model access, so the whole platform has ONE place that:
  * threads tenant_id through every call (for quota / audit / isolation),
  * enforces a per-session call cap (spec: max_calls_per_session: 100),
  * returns structured JSON when asked (response_format=json_object),
  * degrades gracefully: if no OPENAI_API_KEY is configured (or the SDK/network
    is unavailable), it falls back to a deterministic STUB so the entire agent
    flow and demo still run — exactly like the event bus's Redis/in-memory
    fallback. Add the key to light up real reasoning; change nothing else.

Agents never import `openai` directly — they call `gateway.complete(...)` or
`gateway.complete_json(...)`. Swapping providers (OpenAI → Anthropic Gateway)
is a change here only.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from typing import Optional

# Load .env once so OPENAI_API_KEY / NXR_LLM_* are available even when the
# process wasn't started with them exported.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

DEFAULT_MODEL = os.getenv("NXR_LLM_MODEL", "gpt-4o-mini")
MAX_CALLS_PER_SESSION = int(os.getenv("NXR_LLM_MAX_CALLS", "100"))


@dataclass
class LLMResult:
    text: str
    backend: str            # "openai" | "stub"
    model: Optional[str] = None
    raw: Optional[dict] = None


class LLMGateway:
    """Process-wide gateway. One instance is shared (see get_gateway())."""

    def __init__(self):
        self._client = None
        self._backend = "stub"
        self._lock = threading.Lock()
        self._session_calls: dict[str, int] = {}
        self._init_client()

    def _init_client(self):
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            self._backend = "stub"
            return
        try:
            from openai import OpenAI
            self._client = OpenAI(api_key=key)
            self._backend = "openai"
        except Exception:
            self._client = None
            self._backend = "stub"

    @property
    def backend(self) -> str:
        return self._backend

    def stats(self) -> dict:
        return {"backend": self._backend, "model": DEFAULT_MODEL,
                "sessions": len(self._session_calls),
                "max_calls_per_session": MAX_CALLS_PER_SESSION}

    # ---- call accounting ---------------------------------------------
    def _check_and_count(self, session_id: str) -> bool:
        """Returns True if the call is allowed; increments the counter."""
        with self._lock:
            n = self._session_calls.get(session_id, 0)
            if n >= MAX_CALLS_PER_SESSION:
                return False
            self._session_calls[session_id] = n + 1
            return True

    def reset_session(self, session_id: str):
        with self._lock:
            self._session_calls.pop(session_id, None)

    # ---- core completion ---------------------------------------------
    def complete(self, *, tenant_id: str, session_id: str, system: str,
                 user: str, temperature: float = 0.3,
                 max_tokens: int = 700, model: Optional[str] = None,
                 stub) -> LLMResult:
        """Free-text completion. `stub` is a zero-arg callable returning the
        deterministic fallback string — REQUIRED so every call works keyless."""
        if self._backend != "openai" or not self._check_and_count(session_id):
            return LLMResult(text=stub(), backend="stub")
        try:
            resp = self._client.chat.completions.create(
                model=model or DEFAULT_MODEL,
                temperature=temperature,
                max_tokens=max_tokens,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
            )
            return LLMResult(text=resp.choices[0].message.content or "",
                             backend="openai", model=resp.model)
        except Exception:
            # Any API/network error -> deterministic stub, flow continues.
            return LLMResult(text=stub(), backend="stub")

    # ---- multimodal (vision) completion --------------------------------
    def complete_vision(self, *, tenant_id: str, session_id: str, system: str,
                        user_text: str, image_urls: list[str],
                        temperature: float = 0.3, max_tokens: int = 1200,
                        model: Optional[str] = None, stub) -> LLMResult:
        """Vision completion: text + images. `stub` is a zero-arg callable
        returning the fallback string. Uses gpt-4o (vision-capable) by default."""
        if self._backend != "openai" or not self._check_and_count(session_id):
            return LLMResult(text=stub(), backend="stub")
        try:
            content: list[dict] = [{"type": "text", "text": user_text}]
            for url in image_urls[:5]:  # cap at 5 images to control cost
                content.append({"type": "image_url", "image_url": {"url": url}})
            resp = self._client.chat.completions.create(
                model=model or "gpt-4o",
                temperature=temperature,
                max_tokens=max_tokens,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": content}],
            )
            return LLMResult(text=resp.choices[0].message.content or "",
                             backend="openai", model=resp.model)
        except Exception:
            return LLMResult(text=stub(), backend="stub")

    def complete_json_vision(self, *, tenant_id: str, session_id: str,
                             system: str, user_text: str, image_urls: list[str],
                             stub: dict, temperature: float = 0.1,
                             max_tokens: int = 1200,
                             model: Optional[str] = None) -> dict:
        """Structured JSON vision completion. `stub` is the fallback dict.
        Always returns a dict — on any failure, the stub is returned."""
        if self._backend != "openai" or not self._check_and_count(session_id):
            return dict(stub)
        try:
            content: list[dict] = [{"type": "text", "text": user_text}]
            for url in image_urls[:5]:
                content.append({"type": "image_url", "image_url": {"url": url}})
            resp = self._client.chat.completions.create(
                model=model or "gpt-4o",
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": content}],
            )
            parsed = json.loads(resp.choices[0].message.content or "{}")
            return parsed if isinstance(parsed, dict) else dict(stub)
        except Exception:
            return dict(stub)

    # ---- structured JSON completion --------------------------------------
    def complete_json(self, *, tenant_id: str, session_id: str, system: str,
                      user: str, stub: dict, temperature: float = 0.1,
                      max_tokens: int = 700, model: Optional[str] = None) -> dict:
        """Structured JSON completion. `stub` is the deterministic fallback
        dict. Always returns a dict (never raises): on any failure or invalid
        JSON, the stub is returned so routing logic always has a valid shape."""
        if self._backend != "openai" or not self._check_and_count(session_id):
            return dict(stub)
        try:
            resp = self._client.chat.completions.create(
                model=model or DEFAULT_MODEL,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
            )
            content = resp.choices[0].message.content or "{}"
            parsed = json.loads(content)
            return parsed if isinstance(parsed, dict) else dict(stub)
        except Exception:
            return dict(stub)


_gateway: Optional[LLMGateway] = None
_gw_lock = threading.Lock()


def get_gateway() -> LLMGateway:
    global _gateway
    if _gateway is None:
        with _gw_lock:
            if _gateway is None:
                _gateway = LLMGateway()
    return _gateway
