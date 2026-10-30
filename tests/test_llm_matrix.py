"""LLM matrix runner: task grid, streaming/resume, aggregation (no network).

The LLM path is exercised with ``MockLLMClient`` so these tests never call an API.
"""

from __future__ import annotations

import json

import pytest

from common.llm import LLMUsage, MockLLMClient
from scripts.run_llm_matrix import (
    LLM,
    PURE,
    MatrixSpec,
    _open_stream,
    _usage_delta,
    aggregate,
    run_matrix,
    run_task,
)


class _ErroringClient:
    """Client whose every ``chat`` fails like a client-side API error."""

    def __init__(self) -> None:
        self.usage = LLMUsage()

    def chat(self, *args, **kwargs):
        self.usage.errors += 1
        return None


def _spec(**overrides) -> MatrixSpec:
    base = dict(
        deck="superconduct_aggro",
        opponents=("natlan_battleship",),
        seeds=(0,),
        modes=(PURE, LLM),
    )
    base.update(overrides)
    return MatrixSpec(**base)


def test_task_grid_is_deterministic_and_indexed():
    spec = _spec(opponents=("a", "b"), seeds=(0, 1))
    tasks = spec.tasks()
    assert [t.index for t in tasks] == list(range(8))
    assert [(t.opponent, t.seed, t.mode) for t in tasks[:4]] == [
        ("a", 0, PURE),
        ("a", 0, LLM),
        ("a", 1, PURE),
        ("a", 1, LLM),
    ]
    # Fingerprint is stable for identical specs and changes with the grid.
    assert spec.fingerprint() == _spec(opponents=("a", "b"), seeds=(0, 1)).fingerprint()
    assert spec.fingerprint() != _spec(opponents=("a",), seeds=(0, 1)).fingerprint()


def test_usage_delta():
    before = {"calls": 1, "prompt_tokens": 10, "completion_tokens": 2, "errors": 0}
    after = {"calls": 3, "prompt_tokens": 40, "completion_tokens": 7, "errors": 1}
    assert _usage_delta(before, after) == {
        "calls": 2, "prompt_tokens": 30, "completion_tokens": 5, "errors": 1,
    }


def test_run_pure_task_shape():
    spec = _spec(modes=(PURE,))
    row = run_task(spec, spec.tasks()[0], client=None)
    assert row["mode"] == PURE
    assert row["opponent"] == "natlan_battleship"
    assert row["score0"] in (0.0, 0.5, 1.0)
    assert "llm_usage" not in row and "interventions" not in row


def test_run_llm_task_records_interventions_and_usage():
    spec = _spec(modes=(LLM,), budget=2)
    client = MockLLMClient(['{"choice": 0, "reason": "x"}'] * 8)
    row = run_task(spec, spec.tasks()[0], client=client)
    assert row["mode"] == LLM
    assert row["llm_usage"]["calls"] == len(row["interventions"])
    assert row["llm_usage"]["calls"] <= 2  # budget respected
    assert row["interventions_changed"] >= 0
    assert all("changed" in iv for iv in row["interventions"])
    assert row["degraded"] is False  # mock returns content, no client errors


def test_run_llm_task_flags_client_failures_as_degraded():
    spec = _spec(modes=(LLM,), budget=2)
    row = run_task(spec, spec.tasks()[0], client=_ErroringClient())
    assert row["llm_usage"]["errors"] >= 1
    assert row["degraded"] is True


def test_aggregate_reports_modes_and_delta():
    spec = _spec(modes=(PURE, LLM))
    rows = [
        {"task_index": 0, "opponent": "natlan_battleship", "seed": 0, "mode": PURE,
         "score0": 1.0, "winner": 0, "error": None, "truncated": False},
        {"task_index": 1, "opponent": "natlan_battleship", "seed": 0, "mode": LLM,
         "score0": 0.0, "winner": 1, "error": None, "truncated": False,
         "interventions": [{"changed": True, "error": None}],
         "interventions_changed": 1, "interventions_failed": 0,
         "llm_usage": {"calls": 3, "prompt_tokens": 10, "completion_tokens": 4, "errors": 0}},
    ]
    rep = aggregate(rows, spec)
    assert rep["overall"][PURE]["rate"] == 1.0
    assert rep["overall"][LLM]["rate"] == 0.0
    assert rep["overall_delta"] == -1.0
    assert rep["per_opponent"][0]["delta"] == -1.0
    assert rep["llm_usage"]["calls"] == 3
    assert rep["interventions"]["changed"] == 1
    assert rep["interventions"]["changed_rate"] == 1.0


def test_aggregate_surfaces_degraded_matches():
    spec = _spec(modes=(PURE, LLM))
    rows = [
        {"task_index": 0, "opponent": "natlan_battleship", "seed": 0, "mode": PURE,
         "score0": 1.0, "winner": 0, "error": None, "truncated": False},
        {"task_index": 1, "opponent": "natlan_battleship", "seed": 0, "mode": LLM,
         "score0": 1.0, "winner": 0, "error": None, "truncated": False,
         "degraded": True, "interventions": [], "interventions_changed": 0,
         "interventions_failed": 0,
         "llm_usage": {"calls": 1, "prompt_tokens": 0, "completion_tokens": 0, "errors": 1}},
        {"task_index": 2, "opponent": "natlan_battleship", "seed": 1, "mode": LLM,
         "score0": 0.0, "winner": 1, "error": None, "truncated": False,
         "degraded": False, "interventions": [], "interventions_changed": 0,
         "interventions_failed": 0,
         "llm_usage": {"calls": 2, "prompt_tokens": 5, "completion_tokens": 2, "errors": 0}},
    ]
    rep = aggregate(rows, _spec(opponents=("natlan_battleship",), seeds=(0, 1)))
    assert rep["llm_quality"]["matches_using_llm"] == 2
    assert rep["llm_quality"]["degraded_matches"] == 1
    assert rep["llm_quality"]["degraded_rate"] == 0.5
    # Clean rate excludes the degraded match (1.0 vs 0.0 -> 0.5 overall).
    assert rep["llm_quality"]["clean"]["games"] == 1
    assert rep["per_opponent"][0]["llm_degraded"] == 1


def test_abort_after_consecutive_degraded(tmp_path):
    spec = _spec(opponents=("natlan_battleship",), seeds=(0,), modes=(LLM,))
    raw = tmp_path / "degraded.jsonl"
    with pytest.raises(SystemExit):
        run_matrix(
            spec, client=_ErroringClient(), raw_path=raw, progress=False,
            max_consecutive_errors=1,
        )
    lines = [line for line in raw.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 1
    assert json.loads(lines[0])["degraded"] is True


def test_stream_resume_skips_done_and_guards_fingerprint(tmp_path):
    spec = _spec(modes=(PURE,))
    raw = tmp_path / "run.jsonl"
    rows = run_matrix(spec, client=None, raw_path=raw, progress=False)
    assert len(rows) == 1 and raw.exists()
    # Torn trailing line (hard kill) is tolerated, not fatal.
    with raw.open("a", encoding="utf-8") as handle:
        handle.write('{"task_index": 99, "partial"')
    again = run_matrix(spec, client=None, raw_path=raw, progress=False)
    assert len(again) == 1  # no duplicate, no crash

    other = _spec(opponents=("natlan_battleship", "dvalin_bonk"), modes=(PURE,))
    with pytest.raises(RuntimeError):
        run_matrix(other, client=None, raw_path=raw, progress=False)


def test_resume_requires_meta(tmp_path):
    spec = _spec(modes=(PURE,))
    raw = tmp_path / "run.jsonl"
    raw.write_text(json.dumps({"task_index": 0}) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        _open_stream(spec, raw)
