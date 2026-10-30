"""HTTP-level behaviour of the LLM client (no network).

The OpenCode Go endpoint needs two headers our upstream client did not send
(``User-Agent`` -- the CDN blocks urllib's default, and ``x-opencode-session`` --
mandatory there and used as the prompt-cache key) and returns an *empty* content
string when the token budget is eaten by hidden reasoning. All three are easy to
regress, so they are pinned here.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

import common.llm as llm
from common.llm import LLMClient, LLMConfig


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _config(**overrides) -> LLMConfig:
    base = dict(
        base_url="https://example.test/inference/go/openai/v1",
        model="deepseek-v4.1-flash",
        api_key="oc_sk_test_key_1234567890",
        timeout=5.0,
        session_id="ses_unit",
        reasoning_effort="none",
        user_agent="gitcg-skills/test",
    )
    base.update(overrides)
    return LLMConfig(**base)


def _body(content: str, *, cached: int = 0, cost: float = 0.0, prompt: int = 10,
          completion: int = 3) -> _FakeResponse:
    payload = {
        "choices": [{"message": {"content": content}}],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "prompt_tokens_details": {"cached_tokens": cached},
            "cost": cost,
        },
    }
    return _FakeResponse(json.dumps(payload).encode())


def test_chat_sends_session_and_user_agent(monkeypatch):
    seen: list = []

    def fake_urlopen(request, timeout=None):
        seen.append(request)
        return _body('{"choice": 1}')

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    client = LLMClient(_config())
    assert client.chat([{"role": "user", "content": "hi"}]) == '{"choice": 1}'

    request = seen[0]
    headers = {key.lower(): value for key, value in request.header_items()}
    assert headers["x-opencode-session"] == "ses_unit"
    assert headers["user-agent"] == "gitcg-skills/test"
    assert headers["authorization"].startswith("Bearer oc_sk_")
    payload = json.loads(request.data.decode())
    assert payload["reasoning_effort"] == "none"
    assert payload["model"] == "deepseek-v4.1-flash"


def test_chat_treats_empty_content_as_failure(monkeypatch):
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda *a, **k: _body(""))
    client = LLMClient(_config())
    assert client.chat([{"role": "user", "content": "hi"}]) is None
    assert client.usage.errors == 1


def test_chat_records_cache_and_cost(monkeypatch):
    monkeypatch.setattr(
        llm.urllib.request, "urlopen",
        lambda *a, **k: _body('{"choice": 0}', cached=800, cost=0.00002, prompt=1000),
    )
    client = LLMClient(_config())
    client.chat([{"role": "user", "content": "hi"}])
    usage = client.usage.as_dict()
    assert usage["cached_tokens"] == 800
    assert usage["cost_usd"] == pytest.approx(0.00002)
    assert usage["prompt_tokens"] == 1000


def test_chat_honours_retry_after_and_retries_once(monkeypatch):
    calls = {"n": 0}

    def flaky(request, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.HTTPError(
                request.full_url, 429, "rate limited",
                {"Retry-After": "0"}, io.BytesIO(b'{"error":"rate"}'),
            )
        return _body('{"choice": 2}')

    monkeypatch.setattr(llm.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)
    client = LLMClient(_config())
    assert client.chat([{"role": "user", "content": "hi"}]) == '{"choice": 2}'
    assert calls["n"] == 2
    assert client.usage.errors == 0


def test_config_reads_skkey_from_env_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "SKKEY=oc_sk_from_file\n"
        "DEEPSEEK_OPENAI_BASEURL=https://example.test/v1\n"
        "DEEPSEEK_MODEL=deepseek-v4.1-flash\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(llm, "_ENV_FILE", env)
    for key in ("GITCG_LLM_API_KEY", "DEEPSEEK_API_KEY", "SKKEY",
                "GITCG_LLM_BASE_URL", "DEEPSEEK_OPENAI_BASEURL",
                "GITCG_LLM_MODEL", "DEEPSEEK_MODEL"):
        monkeypatch.delenv(key, raising=False)
    config = llm.load_llm_config()
    assert config is not None
    assert config.api_key == "oc_sk_from_file"
    assert config.model == "deepseek-v4.1-flash"
    assert config.reasoning_effort == "none"
