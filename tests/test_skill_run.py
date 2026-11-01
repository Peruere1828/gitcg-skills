from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import skillloop.run as run_module
from skillloop.run import (
    RunSpec,
    _seed_list,
    budget_exhausted,
    budget_from_env,
    run,
    summarize,
)


def _row(stage: str, seed: int, three_star: bool, *, stars: int = 3, n_obj: int = 3, **extra) -> dict:
    return {
        "stage": stage,
        "seed": seed,
        "three_star": three_star,
        "stars": stars,
        "n_objectives": n_obj,
        "llm_calls": extra.pop("llm_calls", 0),
        "error": extra.pop("error", None),
        "truncated": extra.pop("truncated", False),
        **extra,
    }


def test_seed_list_accepts_count_or_list():
    assert _seed_list("4") == (0, 1, 2, 3)
    assert _seed_list("3,7,9") == (3, 7, 9)


def test_summarize_computes_rates_and_ignores_invalid_rows():
    rows = [
        _row("wagner", 1, True),
        _row("wagner", 2, False, stars=2),
        _row("timmie", 1, True),
        _row("timmie", 2, False, error="boom"),
        _row("timmie", 3, False, truncated=True),
    ]
    summary = summarize(rows)
    assert summary["games"] == 5
    assert summary["valid"] == 3
    assert summary["errors"] == 2
    assert summary["three_star_rate"] == round(2 / 3, 4)
    assert set(summary["per_stage"]) == {"wagner", "timmie"}
    assert summary["per_stage"]["wagner"] == {"n": 2, "three_star_rate": 0.5, "mean_stars": 2.5}
    # mean stars normalised by the number of objectives (5/6 here)
    assert summary["mean_stars_per_objective"] == round((1 + 2 / 3 + 1) / 3, 4)


def test_summarize_handles_empty():
    assert summarize([]) == {"games": 0, "valid": 0}


def test_fingerprint_tracks_spec_changes():
    base = dict(group="scripted", stages=("wagner",), seeds=(1, 2), deck="superconduct_aggro")
    a = RunSpec(**base).fingerprint()
    b = RunSpec(**{**base, "deck": "dvalin_bonk"}).fingerprint()
    c = RunSpec(**{**base, "seeds": (1, 2, 3)}).fingerprint()
    assert a != b and a != c
    assert a["group"] == "scripted" and a["library_version"] == 0
    assert "engine_lock" in a and "hp_table" in a


# -- budget guard ------------------------------------------------------------
def test_budget_from_env_and_exhausted(monkeypatch):
    monkeypatch.delenv("GITCG_LLM_BUDGET_USD", raising=False)
    assert budget_from_env() is None
    monkeypatch.setenv("GITCG_LLM_BUDGET_USD", "0.25")
    assert budget_from_env() == 0.25
    assert not budget_exhausted(0.2499, 0.25)
    assert budget_exhausted(0.25, 0.25)
    assert budget_exhausted(1.0, 0.25)
    assert not budget_exhausted(1.0, None)


def test_budget_guard_stops_runner_and_marks_summary(tmp_path, monkeypatch):
    launched: list[tuple[str, int]] = []

    def fake_play_one(item):
        launched.append((item["stage"], item["seed"]))
        return _row(item["stage"], item["seed"], True, cost_usd=0.03)

    monkeypatch.setattr(run_module, "play_one", fake_play_one)
    monkeypatch.setenv("GITCG_LLM_BUDGET_USD", "0.05")
    spec = RunSpec(
        group="llm_empty",
        stages=("wagner",),
        seeds=(0, 1, 2, 3, 4),
        deck="superconduct_aggro",
        workers=1,
        out=str(tmp_path / "run.jsonl"),
    )
    summary = run(spec, progress=False)

    assert len(launched) == 2                     # stopped before launching game 3
    assert summary["games"] == 2
    assert summary["aborted"] == "budget"
    assert summary["budget_usd"] == 0.05
    assert summary["spent_usd"] == 0.06
    rows = [json.loads(line) for line in
            (tmp_path / "run.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2


class _ThreadExecutor:
    """ProcessPoolExecutor stand-in: runs the fake games in local threads."""

    def __init__(self, max_workers=None, mp_context=None, **_kwargs):
        self._pool = ThreadPoolExecutor(max_workers=max_workers)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._pool.shutdown(wait=True)
        return False

    def submit(self, fn, *args, **kwargs):
        return self._pool.submit(fn, *args, **kwargs)


def test_parallel_window_stays_full_without_batch_barrier(tmp_path, monkeypatch):
    lock = threading.Lock()
    active = 0
    max_active = 0
    starts: dict[int, float] = {}
    ends: dict[int, float] = {}

    def fake_play_one(item):
        nonlocal active, max_active
        seed = item["seed"]
        with lock:
            starts[seed] = time.perf_counter()
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05 if seed == 0 else 0.2)      # game 0 frees its slot early
        with lock:
            active -= 1
            ends[seed] = time.perf_counter()
        return _row(item["stage"], seed, True, cost_usd=0.0)

    monkeypatch.setattr(run_module, "ProcessPoolExecutor", _ThreadExecutor)
    monkeypatch.setattr(run_module, "play_one", fake_play_one)
    monkeypatch.delenv("GITCG_LLM_BUDGET_USD", raising=False)
    spec = RunSpec(
        group="llm_empty",
        stages=("wagner",),
        seeds=tuple(range(8)),
        deck="superconduct_aggro",
        workers=3,
        out=str(tmp_path / "run.jsonl"),
    )
    summary = run(spec, progress=False)

    assert summary["games"] == 8 and len(starts) == 8
    assert max_active == 3                         # window stays full
    # A batch/barrier scheduler would wait for games 1 and 2 before starting 3.
    assert starts[3] < ends[1] and starts[3] < ends[2]


def test_parallel_budget_stops_new_submissions(tmp_path, monkeypatch):
    lock = threading.Lock()
    launched: list[int] = []

    def fake_play_one(item):
        with lock:
            launched.append(item["seed"])
        time.sleep(0.05 if item["seed"] == 0 else 0.4)
        return _row(item["stage"], item["seed"], True, cost_usd=0.03)

    monkeypatch.setattr(run_module, "ProcessPoolExecutor", _ThreadExecutor)
    monkeypatch.setattr(run_module, "play_one", fake_play_one)
    monkeypatch.setenv("GITCG_LLM_BUDGET_USD", "0.05")
    spec = RunSpec(
        group="llm_empty",
        stages=("wagner",),
        seeds=tuple(range(8)),
        deck="superconduct_aggro",
        workers=3,
        out=str(tmp_path / "run.jsonl"),
    )
    summary = run(spec, progress=False)

    # 3 start immediately; the first costs $0.03, so one more (game 3) is allowed;
    # the second completion reaches $0.06 and no further games are submitted.
    assert len(launched) == 4
    assert summary["games"] == 4
    assert summary["aborted"] == "budget"
    assert summary["budget_usd"] == 0.05
    assert summary["spent_usd"] == 0.12
