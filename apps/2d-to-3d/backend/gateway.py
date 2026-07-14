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
# Vision model for plan parsing. Prefer a strong model: if an Anthropic key is
# present we use Claude (excellent at dense architectural drawings); otherwise
# OpenAI gpt-4o. Override with NXR_VISION_MODEL.
DEFAULT_VISION_MODEL = os.getenv("NXR_VISION_MODEL", "")
ANTHROPIC_VISION_DEFAULT = "claude-sonnet-4-6"
OPENAI_VISION_DEFAULT = "gpt-4o"
MAX_CALLS_PER_SESSION = int(os.getenv("NXR_LLM_MAX_CALLS", "100"))


def _salvage_json(text: str) -> Optional[dict]:
    """Best-effort: parse a JSON object from a model reply that may be wrapped in
    prose or ```json fences, or lightly truncated."""
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    s = text.strip()
    if "```" in s:                       # strip code fences
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s
        s = s.lstrip("json").lstrip("JSON").strip()
    a, b = s.find("{"), s.rfind("}")
    if a >= 0 and b > a:
        frag = s[a:b + 1]
        try:
            return json.loads(frag)
        except Exception:
            # truncated: trim to the last complete top-level array/brace and close.
            for cut in (frag.rfind("}]"), frag.rfind("]"), frag.rfind("}")):
                if cut > 0:
                    try:
                        return json.loads(frag[:cut + 1].rstrip(", \n") +
                                          ("]" if frag[:cut + 1].count("[") > frag[:cut + 1].count("]") else "") +
                                          "}" * max(0, frag[:cut + 1].count("{") - frag[:cut + 1].count("}")))
                    except Exception:
                        continue
    return None


def _split_data_url(url: str):
    """('image/png', '<base64>') from a data URL, else (None, None)."""
    if isinstance(url, str) and url.startswith("data:") and "," in url:
        head, data = url.split(",", 1)
        media = head[5:].split(";")[0] or "image/png"
        return media, data
    return None, None


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
        self._anthropic = None
        self._backend = "stub"
        self.last_vision_error = None
        self.last_vision_backend = None
        self._lock = threading.Lock()
        self._session_calls: dict[str, int] = {}
        self._init_client()
        self._init_anthropic()

    def _init_anthropic(self):
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            return
        try:
            import anthropic
            self._anthropic = anthropic.Anthropic(api_key=key)
        except Exception:
            self._anthropic = None

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
                             max_tokens: int = 8000,
                             model: Optional[str] = None) -> dict:
        """Structured JSON vision completion for plan parsing. Prefers Claude
        (strong on dense architectural drawings) when ANTHROPIC_API_KEY is set,
        else OpenAI gpt-4o with HIGH-detail images so small room labels are read.
        Salvages lightly-truncated JSON. Returns the stub only on hard failure,
        recording why on self.last_vision_error so the cause is visible."""
        self.last_vision_error = None
        if not self._check_and_count(session_id):
            self.last_vision_error = "session call cap reached"
            return dict(stub)

        # --- Claude path (preferred when available) ---
        if self._anthropic is not None and (not DEFAULT_VISION_MODEL
                                            or DEFAULT_VISION_MODEL.startswith("claude")):
            try:
                blocks = [{"type": "text", "text": user_text}]
                for url in image_urls[:4]:
                    media, b64 = _split_data_url(url)
                    if b64:
                        blocks.append({"type": "image", "source": {
                            "type": "base64", "media_type": media, "data": b64}})
                    elif isinstance(url, str) and url.startswith("http"):
                        blocks.append({"type": "image", "source": {"type": "url", "url": url}})
                resp = self._anthropic.messages.create(
                    model=DEFAULT_VISION_MODEL or ANTHROPIC_VISION_DEFAULT,
                    max_tokens=max(max_tokens, 8000), temperature=temperature,
                    system=system + "\nReturn ONLY the JSON object, no prose.",
                    messages=[{"role": "user", "content": blocks}])
                text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
                parsed = _salvage_json(text)
                if isinstance(parsed, dict):
                    self.last_vision_backend = "anthropic"
                    return parsed
                self.last_vision_error = "anthropic returned unparseable JSON"
            except Exception as e:
                self.last_vision_error = f"anthropic error: {e}"

        # --- OpenAI gpt-4o path (HIGH detail + salvage) ---
        if self._backend == "openai":
            try:
                content: list[dict] = [{"type": "text", "text": user_text}]
                for url in image_urls[:4]:
                    content.append({"type": "image_url",
                                    "image_url": {"url": url, "detail": "high"}})
                resp = self._client.chat.completions.create(
                    model=model or DEFAULT_VISION_MODEL or OPENAI_VISION_DEFAULT,
                    temperature=temperature,
                    max_tokens=max(max_tokens, 4096),
                    response_format={"type": "json_object"},
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": content}])
                parsed = _salvage_json(resp.choices[0].message.content or "{}")
                if isinstance(parsed, dict):
                    self.last_vision_backend = "openai"
                    return parsed
                self.last_vision_error = "openai returned unparseable/truncated JSON"
            except Exception as e:
                self.last_vision_error = f"openai error: {e}"
        elif not self.last_vision_error:
            self.last_vision_error = "no vision backend configured"
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
