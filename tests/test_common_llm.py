"""Accounting and ``finish_reason`` exposure of the LLM client (no network).

Regression guard: the provider bills every response, including one whose
``content`` is empty (hidden reasoning ate ``max_tokens``) or truncated with
``finish_reason="length"``. Those responses used to take an error branch before
``usage`` was accumulated, so run-level cost audits read ``0 calls, $0.0000``
while the trace's ``list_cost`` showed the real spend.
"""

from __future__ import annotations

import io
import json

import pytest

import common.llm as llm
from common.llm import LLMClient, LLMConfig, LLMReply


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _config(**overrides) -> LLMConfig:
    base = dict(
        base_url="https://example.test/v1",
        model="deepseek-v4.1-flash",
        api_key="unit-test-placeholder",
        timeout=5.0,
        session_id="ses_unit",
        reasoning_effort="none",
        user_agent="gitcg-skills/test",
    )
    base.update(overrides)
    return LLMConfig(**base)


def _response(payload: dict) -> _FakeResponse:
    return _FakeResponse(json.dumps(payload).encode())


def _body(content: str, *, finish_reason: str = "stop", prompt: int = 10,
          completion: int = 3, cached: int = 0, cost: float = 0.0) -> _FakeResponse:
    return _response({
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "prompt_tokens_details": {"cached_tokens": cached},
            "cost": cost,
        },
    })


def test_empty_content_still_accumulates_usage(monkeypatch):
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda *a, **k: _body(
        "", finish_reason="length", prompt=1200, completion=256,
        cached=900, cost=0.0063,
    ))
    client = LLMClient(_config())
    assert client.chat([{"role": "user", "content": "hi"}]) is None
    usage = client.usage.as_dict()
    assert usage["errors"] == 1                     # still treated as a failure
    assert usage["calls"] == 1                      # ...but the call is accounted
    assert usage["prompt_tokens"] == 1200
    assert usage["completion_tokens"] == 256
    assert usage["cached_tokens"] == 900
    assert usage["cost_usd"] == pytest.approx(0.0063)


def test_length_truncation_accumulates_and_exposes_finish_reason(monkeypatch):
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda *a, **k: _body(
        '{"skills": [', finish_reason="length", prompt=2000, completion=8192,
        cost=0.012,
    ))
    client = LLMClient(_config())
    reply = client.chat([{"role": "user", "content": "hi"}])
    assert reply == '{"skills": ['
    assert isinstance(reply, LLMReply)              # str subclass, not a wrapper
    assert reply.finish_reason == "length"
    assert client.last_finish_reason == "length"
    usage = client.usage.as_dict()
    assert usage["errors"] == 0
    assert usage["calls"] == 1
    assert usage["prompt_tokens"] == 2000
    assert usage["completion_tokens"] == 8192
    assert usage["cost_usd"] == pytest.approx(0.012)


def test_normal_response_behaviour_unchanged(monkeypatch):
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda *a, **k: _body(
        '{"choice": 1}', finish_reason="stop", prompt=1000, completion=12,
        cached=800, cost=0.00002,
    ))
    client = LLMClient(_config())
    reply = client.chat([{"role": "user", "content": "hi"}])
    assert reply == '{"choice": 1}'
    assert json.loads(reply) == {"choice": 1}
    assert reply.strip() == '{"choice": 1}'
    assert llm.extract_json_object(reply) == {"choice": 1}
    assert reply.finish_reason == "stop"
    usage = client.usage.as_dict()
    assert usage["errors"] == 0
    assert usage["calls"] == 1
    assert usage["prompt_tokens"] == 1000
    assert usage["completion_tokens"] == 12
    assert usage["cached_tokens"] == 800
    assert usage["cost_usd"] == pytest.approx(0.00002)


def test_malformed_response_usage_is_accounted(monkeypatch):
    """Account before classification: even an unparseable body was billed."""
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda *a, **k: _response({
        "choices": [],
        "usage": {"prompt_tokens": 500, "completion_tokens": 7,
                  "opencode": {"list_cost": 0.0011}},
    }))
    client = LLMClient(_config())
    assert client.chat([{"role": "user", "content": "hi"}]) is None
    usage = client.usage.as_dict()
    assert usage["errors"] == 1
    assert usage["calls"] == 1
    assert usage["prompt_tokens"] == 500
    assert usage["cost_usd"] == pytest.approx(0.0011)  # opencode.list_cost fallback


def test_response_without_usage_is_not_invented(monkeypatch):
    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda *a, **k: _response({
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
    }))
    client = LLMClient(_config())
    assert client.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert client.usage.as_dict()["calls"] == 0
    assert client.usage.as_dict()["cost_usd"] == 0.0
