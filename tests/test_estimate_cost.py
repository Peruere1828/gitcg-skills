from __future__ import annotations

import json

import pytest

from scripts.estimate_cost import estimate_cost, llm_games, main


def _row(*, calls: int, completion: int, cost: float) -> dict:
    return {"llm_calls": calls, "completion_tokens": completion, "cost_usd": cost}


def test_estimate_cost_arithmetic():
    rows = [
        _row(calls=10, completion=150, cost=0.02),
        _row(calls=30, completion=450, cost=0.04),
        _row(calls=0, completion=0, cost=0.0),   # scripted row: not an LLM game
    ]
    est = estimate_cost(rows, target_games=100)
    assert est["n_games"] == 2
    assert est["total_cost_usd"] == pytest.approx(0.06)
    assert est["usd_per_game"] == pytest.approx(0.03)
    assert est["calls_per_game"] == pytest.approx(20.0)
    assert est["completion_tokens_per_call"] == pytest.approx(15.0)
    assert est["projected_cost_usd"] == pytest.approx(3.0)


def test_llm_games_keeps_cost_only_rows_and_rejects_empty():
    rows = [{"llm_calls": 0, "cost_usd": 0.01}, {"llm_calls": 0, "cost_usd": 0.0}]
    assert len(llm_games(rows)) == 1
    with pytest.raises(ValueError):
        estimate_cost(rows[1:], target_games=10)


def test_main_accepts_per_stage_seeds(tmp_path, capsys):
    path = tmp_path / "run.jsonl"
    path.write_text("\n".join(json.dumps(_row(calls=10, completion=150, cost=0.02))
                              for _ in range(2)) + "\n", encoding="utf-8")
    assert main(["--rows", str(path), "--per-stage-seeds", "2", "--stages", "3"]) == 0
    out = capsys.readouterr().out
    assert "projected : $0.1200" in out
