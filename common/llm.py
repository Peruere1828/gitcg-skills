"""Unified LLM client (PLAN.md D5/D6, §5).

An OpenAI-compatible chat client built on the standard library only (no `openai`
dependency). Config resolves in this order:

1. explicit constructor args,
2. environment variables (``GITCG_LLM_*`` / ``DEEPSEEK_*``),
3. the repo ``.env`` file (gitignored).

The client returns ``None`` (rather than raising) when unusable, so callers can
implement the forced degradation chain **local LLM -> commercial API -> pure
policy**. API keys are never logged; use :func:`redact` before emitting any
request/response text.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from common.paths import REPO_ROOT

_ENV_FILE = REPO_ROOT / ".env"
_REDACT_PREFIXES = ("sk-", "oc_sk_")


@dataclass
class LLMConfig:
    base_url: str
    model: str
    api_key: str
    timeout: float = 60.0
    max_tokens: int = 256
    temperature: float = 0.0
    name: str = "openai-compatible"
    # OpenCode Console (Go plan) requires a non-empty session id and rejects
    # urllib's default User-Agent at the CDN; both are harmless for other providers.
    session_id: str = "ses_gitcg_default"
    reasoning_effort: str | None = "none"
    user_agent: str = "gitcg-skills/1.0"

    @property
    def chat_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"


@dataclass
class LLMUsage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    errors: int = 0
    total_seconds: float = 0.0
    cached_tokens: int = 0
    cost_usd: float = 0.0

    def record(self, *, prompt: int, completion: int, seconds: float,
               cached: int = 0, cost: float = 0.0) -> None:
        self.calls += 1
        self.prompt_tokens += int(prompt)
        self.completion_tokens += int(completion)
        self.total_seconds += float(seconds)
        self.cached_tokens += int(cached)
        self.cost_usd += float(cost)

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "errors": self.errors,
            "total_seconds": round(self.total_seconds, 3),
            "cached_tokens": self.cached_tokens,
            "cost_usd": round(self.cost_usd, 6),
        }


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_llm_config(name: str | None = None) -> LLMConfig | None:
    """Resolve an LLM config, or ``None`` if no key is available (degrade)."""
    file_env = _read_env_file(_ENV_FILE)

    def pick(*keys: str) -> str | None:
        for key in keys:
            value = os.environ.get(key) or file_env.get(key)
            if value:
                return value
        return None

    base_url = pick("GITCG_LLM_BASE_URL", "DEEPSEEK_OPENAI_BASEURL")
    api_key = pick("GITCG_LLM_API_KEY", "DEEPSEEK_API_KEY", "SKKEY")
    model = pick("GITCG_LLM_MODEL", "DEEPSEEK_MODEL")
    if not (base_url and api_key and model):
        return None
    return LLMConfig(
        base_url=base_url,
        model=model,
        api_key=api_key,
        timeout=float(pick("GITCG_LLM_TIMEOUT") or 60.0),
        max_tokens=int(pick("GITCG_LLM_MAX_TOKENS") or 256),
        temperature=float(pick("GITCG_LLM_TEMPERATURE") or 0.0),
        name=name or (pick("GITCG_LLM_NAME") or "deepseek"),
        session_id=pick("GITCG_LLM_SESSION") or "ses_gitcg_default",
        reasoning_effort=(pick("GITCG_LLM_REASONING") or "none"),
        user_agent=pick("GITCG_LLM_USER_AGENT") or "gitcg-skills/1.0",
    )


def redact(text: str) -> str:
    """Remove anything that looks like an API key from a string."""
    out = text
    for token in out.split():
        if len(token) > 8 and any(token.startswith(prefix) for prefix in _REDACT_PREFIXES):
            out = out.replace(token, "sk-***")
    return out


class MockLLMClient:
    """Deterministic client for tests: returns queued responses in order."""

    def __init__(self, responses: Sequence[str], name: str = "mock") -> None:
        self.config = LLMConfig(base_url="mock://", model="mock", api_key="x", name=name)
        self.usage = LLMUsage()
        self._responses = list(responses)
        self.prompts: list[list[dict[str, str]]] = []

    def chat(self, messages: Sequence[dict[str, str]], **_: Any) -> str | None:
        self.prompts.append(list(messages))
        self.usage.calls += 1
        if not self._responses:
            return None
        return self._responses.pop(0)


class LLMClient:
    """OpenAI-compatible chat client.

    ``chat`` returns the assistant message text, or ``None`` on any failure so
    callers can fall back (the chain in PLAN.md §5 is enforced by callers).
    """

    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or load_llm_config()
        if self.config is None:
            raise RuntimeError("no LLM config available; use build_llm_client()")
        self.usage = LLMUsage()

    def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        response_format_json: bool = False,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> str | None:
        payload: dict[str, Any] = {
            "model": model or self.config.model,
            "messages": list(messages),
            "max_tokens": int(max_tokens or self.config.max_tokens),
            "temperature": float(
                self.config.temperature if temperature is None else temperature
            ),
        }
        if response_format_json:
            payload["response_format"] = {"type": "json_object"}
        # OpenCode's DeepSeek spends completion tokens on hidden reasoning; with a
        # small max_tokens that yields an EMPTY content string (not an error).
        effort = reasoning_effort if reasoning_effort is not None else self.config.reasoning_effort
        if effort:
            payload["reasoning_effort"] = effort

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
            "User-Agent": self.config.user_agent,
        }
        if self.config.session_id:
            # acts as the prompt-cache key on the Go endpoint (and is mandatory there)
            headers["x-opencode-session"] = self.config.session_id

        started = time.perf_counter()
        body: dict[str, Any] | None = None
        for attempt in range(3):
            request = urllib.request.Request(
                self.config.chat_url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
            )
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                    body = json.load(response)
                break
            except urllib.error.HTTPError as exc:
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if retryable and attempt < 2:
                    wait = 0.0
                    try:
                        wait = float(exc.headers.get("Retry-After") or 0.0)
                    except (TypeError, ValueError):
                        wait = 0.0
                    time.sleep(min(30.0, wait or (2 ** attempt)))
                    continue
                self.usage.errors += 1
                return None
            except (urllib.error.URLError, TimeoutError, ValueError, OSError):
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                self.usage.errors += 1
                return None

        elapsed = time.perf_counter() - started
        if body is None:
            self.usage.errors += 1
            return None
        try:
            message = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            self.usage.errors += 1
            return None
        if not str(message or "").strip():
            # truncated by max_tokens (reasoning ate the budget) -> fall back
            self.usage.errors += 1
            return None
        usage = body.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        opencode = usage.get("opencode") or {}
        cost = usage.get("cost")
        if not cost:
            cost = opencode.get("list_cost") or 0.0
        self.usage.record(
            prompt=int(usage.get("prompt_tokens", 0)),
            completion=int(usage.get("completion_tokens", 0)),
            seconds=elapsed,
            cached=int(details.get("cached_tokens", 0)),
            cost=float(cost or 0.0),
        )
        return message


def build_llm_client(
    *, config: LLMConfig | None = None, allow_mock: Sequence[str] | None = None
) -> Any | None:
    """Build a client, or ``None`` to signal "use pure policy" (degradation chain)."""
    if allow_mock is not None:
        return MockLLMClient(allow_mock)
    resolved = config or load_llm_config()
    if resolved is None:
        return None
    return LLMClient(resolved)


def extract_json_object(text: str | None) -> dict[str, Any] | None:
    """Best-effort parse of a JSON object from an LLM message."""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
