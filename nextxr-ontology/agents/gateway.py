"""
gateway.py — the LLM Gateway every agent graph calls.

ONE provider: Claude (Anthropic). The platform previously ran OpenAI here and
Claude in copilot/, which meant two keys, two model policies and two failure
modes for one product. Both now share `copilot.config` — a single
ANTHROPIC_API_KEY and a single model choice govern every agent on the platform.

This is the single choke-point for model access, so the whole platform has ONE
place that:
  * threads tenant_id through every call (for quota / audit / isolation),
  * enforces a per-session call cap (spec: max_calls_per_session: 100),
  * returns structured JSON when asked,
  * degrades gracefully: with no ANTHROPIC_API_KEY configured (or the SDK/network
    unavailable) it falls back to a deterministic STUB so the entire agent flow
    and demo still run — exactly like the event bus's Redis/in-memory fallback.

Agents never import `anthropic` directly — they call `gateway.complete(...)` or
`gateway.complete_json(...)`. The public surface here is unchanged from the
OpenAI era, so no caller needed editing:

    complete() · complete_json() · complete_vision() · complete_json_vision()
    .backend · .stats() · .reset_session() · .last_vision_error

NOTE ON SAMPLING PARAMETERS: callers still pass `temperature=`. Claude Opus 4.8
REJECTS temperature/top_p/top_k with a 400, so this gateway accepts the argument
and deliberately ignores it. Steer these agents with prompt wording, not
sampling. The parameter is kept in the signature only so the swap needed no
edits at ~20 call sites; treat it as deprecated.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass

# One key, one model policy, for the whole platform.
from copilot.config import config as _claude


def _claude_model_or_default(env_name: str, default: str = "") -> str:
    """Honour a per-gateway model override ONLY if it names a Claude model.

    These env vars predate the move to a single provider and are still set to
    OpenAI ids (`gpt-4o-mini`) in existing .env files and deployments. Passing
    one to the Anthropic API 404s on every call — and because every method here
    falls back to a stub, the platform would look like it was merely running
    keyless instead of misconfigured. Ignoring a non-Claude value keeps a stale
    setting from silently disabling the whole agent layer.
    """
    val = (os.getenv(env_name) or "").strip()
    if not val:
        return default
    if val.startswith("claude"):
        return val
    print(f"[gateway] ignoring {env_name}={val!r} — this platform is Claude-only. "
          f"Use NXR_CLAUDE_MODEL to override the model.", flush=True)
    return default


# Kept for callers/telemetry that read them. A per-gateway override applies only
# if it names a Claude model; otherwise the platform-wide choice wins.
DEFAULT_MODEL = _claude_model_or_default("NXR_LLM_MODEL", _claude.CLAUDE_MODEL)
DEFAULT_VISION_MODEL = _claude_model_or_default("NXR_VISION_MODEL", "")
MAX_CALLS_PER_SESSION = int(os.getenv("NXR_LLM_MAX_CALLS", "100"))

# Plan parsing reads dense architectural drawings — worth thinking about, and
# worth a large budget. Everything else here is short structured extraction
# where thinking would just eat the token budget.
VISION_MIN_TOKENS = 16000

_JSON_ONLY = "\nReturn ONLY the JSON object, with no prose and no code fences."


def _salvage_json(text: str) -> dict | None:
    """Best-effort: parse a JSON object from a reply that may be wrapped in prose
    or ```json fences, or lightly truncated."""
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
            # Truncated: trim to the last complete array/brace and close it.
            for cut in (frag.rfind("}]"), frag.rfind("]"), frag.rfind("}")):
                if cut > 0:
                    head = frag[:cut + 1]
                    try:
                        return json.loads(
                            head.rstrip(", \n")
                            + ("]" if head.count("[") > head.count("]") else "")
                            + "}" * max(0, head.count("{") - head.count("}")))
                    except Exception:
                        continue
    return None


def _image_blocks(image_urls: list[str], limit: int = 4) -> list[dict]:
    """Anthropic image content blocks from data URLs or http(s) URLs."""
    blocks = []
    for url in (image_urls or [])[:limit]:
        if isinstance(url, str) and url.startswith("data:") and "," in url:
            head, data = url.split(",", 1)
            media = head[5:].split(";")[0] or "image/png"
            blocks.append({"type": "image", "source": {
                "type": "base64", "media_type": media, "data": data}})
        elif isinstance(url, str) and url.startswith("http"):
            blocks.append({"type": "image", "source": {"type": "url", "url": url}})
    return blocks


@dataclass
class LLMResult:
    text: str
    backend: str            # "anthropic" | "stub"
    model: str | None = None
    raw: dict | None = None


class LLMGateway:
    """Process-wide gateway. One instance is shared (see get_gateway())."""

    def __init__(self):
        self._client = None
        self._client_key: str | None = None
        self.last_vision_error = None
        self.last_vision_backend = None
        self._lock = threading.Lock()
        self._session_calls: dict[str, int] = {}

    # ---- client ------------------------------------------------------
    def _anthropic(self):
        """Lazily built, rebuilt if the key changes underneath us. Returns None
        when no key is configured — every caller falls back to its stub."""
        key = _claude.ANTHROPIC_API_KEY
        if not key:
            return None
        if self._client is None or self._client_key != key:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=key)
                self._client_key = key
            except Exception:
                self._client = None
        return self._client

    @property
    def backend(self) -> str:
        """'anthropic' when a key is configured, else 'stub'.

        Callers branch on `== "stub"` to decide whether output is real, so this
        must stay honest.
        """
        return "anthropic" if _claude.ANTHROPIC_API_KEY else "stub"

    def stats(self) -> dict:
        return {"backend": self.backend, "provider": "anthropic",
                "model": DEFAULT_MODEL if self.backend != "stub" else None,
                "sessions": len(self._session_calls),
                "max_calls_per_session": MAX_CALLS_PER_SESSION}

    # ---- call accounting ---------------------------------------------
    def _check_and_count(self, session_id: str) -> bool:
        """True if the call is allowed; increments the counter."""
        with self._lock:
            n = self._session_calls.get(session_id, 0)
            if n >= MAX_CALLS_PER_SESSION:
                return False
            self._session_calls[session_id] = n + 1
            return True

    def reset_session(self, session_id: str):
        with self._lock:
            self._session_calls.pop(session_id, None)

    # ---- internal ----------------------------------------------------
    def _message(self, *, system, content, max_tokens: int, model: str | None,
                 thinking: bool = False) -> str:
        """One Claude call -> concatenated text. Raises on failure."""
        kwargs = {
            "model": model or DEFAULT_MODEL,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": content}],
        }
        if thinking:
            kwargs["thinking"] = {"type": "adaptive"}
        resp = self._anthropic().messages.create(**kwargs)
        if getattr(resp, "stop_reason", None) == "refusal":
            raise RuntimeError("model declined the request")
        return "".join(b.text for b in resp.content
                       if getattr(b, "type", "") == "text").strip()

    # ---- core completion ---------------------------------------------
    def complete(self, *, tenant_id: str, session_id: str, system: str,
                 user: str, temperature: float = 0.3,
                 max_tokens: int = 700, model: str | None = None,
                 stub) -> LLMResult:
        """Free-text completion. `stub` is a zero-arg callable returning the
        deterministic fallback string — REQUIRED so every call works keyless.
        `temperature` is accepted and ignored (see module docstring)."""
        if self._anthropic() is None or not self._check_and_count(session_id):
            return LLMResult(text=stub(), backend="stub")
        try:
            text = self._message(system=system, content=user,
                                 max_tokens=max_tokens, model=model)
            return LLMResult(text=text or stub(),
                             backend="anthropic" if text else "stub",
                             model=model or DEFAULT_MODEL)
        except Exception:
            # Any API/network error -> deterministic stub, flow continues.
            return LLMResult(text=stub(), backend="stub")

    # ---- structured JSON completion ----------------------------------
    def complete_json(self, *, tenant_id: str, session_id: str, system: str,
                      user: str, stub: dict, temperature: float = 0.1,
                      max_tokens: int = 700,
                      model: str | None = None) -> dict:
        """Structured JSON completion. Always returns a dict (never raises): on
        any failure or invalid JSON the stub is returned, so routing logic always
        has a valid shape."""
        if self._anthropic() is None or not self._check_and_count(session_id):
            return dict(stub)
        try:
            text = self._message(system=system + _JSON_ONLY, content=user,
                                 max_tokens=max_tokens, model=model)
            parsed = _salvage_json(text)
            return parsed if isinstance(parsed, dict) else dict(stub)
        except Exception:
            return dict(stub)

    # ---- multimodal (vision) completion ------------------------------
    def complete_vision(self, *, tenant_id: str, session_id: str, system: str,
                        user_text: str, image_urls: list[str],
                        temperature: float = 0.3, max_tokens: int = 1200,
                        model: str | None = None, stub) -> LLMResult:
        """Vision completion: text + images."""
        if self._anthropic() is None or not self._check_and_count(session_id):
            return LLMResult(text=stub(), backend="stub")
        try:
            content = [{"type": "text", "text": user_text}] + _image_blocks(image_urls, 5)
            text = self._message(system=system, content=content,
                                 max_tokens=max_tokens, model=model)
            return LLMResult(text=text or stub(),
                             backend="anthropic" if text else "stub",
                             model=model or DEFAULT_MODEL)
        except Exception:
            return LLMResult(text=stub(), backend="stub")

    def complete_json_vision(self, *, tenant_id: str, session_id: str,
                             system: str, user_text: str, image_urls: list[str],
                             stub: dict, temperature: float = 0.1,
                             max_tokens: int = 8000,
                             model: str | None = None) -> dict:
        """Structured JSON vision completion — the floor-plan parser's path.

        Claude is strong on dense architectural drawings, and this is the one
        call worth thinking budget: a misread plan produces a wrong building.
        Salvages lightly-truncated JSON. Returns the stub only on hard failure,
        recording why on `self.last_vision_error` so the cause stays visible in
        the UI rather than silently becoming a synthesized floor plan.
        """
        self.last_vision_error = None
        if self._anthropic() is None:
            self.last_vision_error = "no ANTHROPIC_API_KEY configured"
            return dict(stub)
        if not self._check_and_count(session_id):
            self.last_vision_error = "session call cap reached"
            return dict(stub)
        try:
            content = [{"type": "text", "text": user_text}] + _image_blocks(image_urls, 4)
            text = self._message(
                system=system + _JSON_ONLY, content=content,
                max_tokens=max(max_tokens, VISION_MIN_TOKENS),
                model=model or DEFAULT_VISION_MODEL or None, thinking=True)
            parsed = _salvage_json(text)
            if isinstance(parsed, dict):
                self.last_vision_backend = "anthropic"
                return parsed
            self.last_vision_error = "model returned unparseable JSON"
        except Exception as e:  # noqa: BLE001
            self.last_vision_error = f"anthropic error: {e}"
        return dict(stub)


_gateway: LLMGateway | None = None
_gw_lock = threading.Lock()


def get_gateway() -> LLMGateway:
    global _gateway
    if _gateway is None:
        with _gw_lock:
            if _gateway is None:
                _gateway = LLMGateway()
    return _gateway
