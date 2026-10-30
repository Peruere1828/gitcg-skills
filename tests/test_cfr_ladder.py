"""``scripts.run_cfr_ladder`` engine-free helpers (PLAN.md A11 defect fixes).

Exercises the aggregation/resume logic without touching the engine: the dedup
that aligns ``summarize`` with ``paired_difference``, the upper-median
convention, the sidecar resume guard (mirroring ``eval.arena``), and the
CLI-count validation.
"""

from __future__ import annotations

import concurrent.futures as _futures
import json
import types

import pytest

import scripts.run_cfr_ladder as ladder

from scripts.run_cfr_ladder import (
    CFR,
    EXPERT,
    LadderSpec,
    _dedup_rows,
    _median_upper,
    _open_stream,
    aggregate,
    main,
)


def _row(
    opponent: str,
    seed: int,
    mode: str,
    outcome: float,
    *,
    wall: float = 1.0,
    searched: int = 1,
    changed: int = 0,
    winner: int | None = 0,
) -> dict:
    return {
        "task_index": seed,
        "opponent": opponent,
        "seed": seed,
        "mode": mode,
        "winner": winner,
        "outcome": outcome,
        "rounds": 3,
        "decisions0": 5,
        "decisions1": 5,
        "fallbacks0": 0,
        "searched_decisions": searched,
        "changed_decisions": changed,
        "root_value_sum": 0.0,
        "wall_seconds": wall,
        "truncated": False,
        "error": None,
    }


def _spec(**overrides) -> LadderSpec:
    base: dict = {
        "deck": "d",
        "opponents": ("a",),
        "seeds": (0,),
        "workers": 1,
        "bridge_workers": 1,
    }
    base.update(overrides)
    return LadderSpec(**base)


def test_median_upper_takes_the_larger_middle():
    assert _median_upper([1.0, 2.0, 3.0]) == 2.0
    assert _median_upper([1.0, 2.0, 3.0, 4.0]) == 3.0


def test_dedup_keeps_last_row_per_task_key():
    rows = [_row("a", 0, CFR, 0.0), _row("a", 0, CFR, 1.0)]
    deduped, duplicates = _dedup_rows(rows)
    assert duplicates == 1
    assert len(deduped) == 1
    assert deduped[0]["outcome"] == 1.0


def test_aggregate_dedups_like_paired_difference():
    rows = [
        _row("a", 0, EXPERT, 0.0, winner=1),
        _row("a", 0, CFR, 0.0, winner=1),
        _row("a", 0, CFR, 1.0, winner=0),  # duplicate task key; last one wins
    ]
    report = aggregate(_spec(), rows)
    assert report["matches"] == 2
    assert report["duplicate_rows"] == 1
    assert report["pooled_cfr"] == 1.0
    assert report["pooled_expert"] == 0.0
    assert report["paired_cfr_minus_expert"]["n_pairs"] == 1


def test_open_stream_refuses_stream_without_sidecar(tmp_path):
    stream = tmp_path / "t.jsonl"
    meta = tmp_path / "t.meta.json"
    stream.write_text(json.dumps(_row("a", 0, CFR, 1.0)) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        _open_stream(stream, meta, _spec())


def test_open_stream_refuses_fingerprint_change(tmp_path):
    stream = tmp_path / "t.jsonl"
    meta = tmp_path / "t.meta.json"
    stream.write_text("", encoding="utf-8")
    meta.write_text(json.dumps({"fingerprint": "deadbeef"}), encoding="utf-8")
    with pytest.raises(SystemExit):
        _open_stream(stream, meta, _spec())


def test_open_stream_creates_sidecar_and_reads_done(tmp_path):
    stream = tmp_path / "t.jsonl"
    meta = tmp_path / "t.meta.json"
    spec = _spec()
    assert _open_stream(stream, meta, spec) == (set(), set())
    assert json.loads(meta.read_text(encoding="utf-8"))["fingerprint"] == spec.fingerprint()
    stream.write_text(json.dumps(_row("a", 0, CFR, 1.0)) + "\n", encoding="utf-8")
    assert _open_stream(stream, meta, spec) == ({"a|0|cfr"}, set())


def test_open_stream_separates_error_rows(tmp_path):
    """Error rows are still 'done' but reported separately so --retry-errors can
    re-queue them (review follow-up #1: otherwise their pairs vanish)."""
    stream = tmp_path / "t.jsonl"
    meta = tmp_path / "t.meta.json"
    spec = _spec()
    _open_stream(stream, meta, spec)  # seed the sidecar
    ok = _row("a", 0, CFR, 1.0)
    bad = _row("b", 1, CFR, 0.0)
    bad["error"] = "RuntimeError: boom"
    stream.write_text(json.dumps(ok) + "\n" + json.dumps(bad) + "\n", encoding="utf-8")
    done, errors = _open_stream(stream, meta, spec)
    assert done == {"a|0|cfr", "b|1|cfr"}
    assert errors == {"b|1|cfr"}


@pytest.mark.parametrize(
    "argv",
    [["--workers", "0"], ["--bridge-workers", "0"], ["--seeds", "0"]],
)
def test_main_rejects_nonpositive_counts(argv):
    with pytest.raises(SystemExit):
        main(argv)


def test_main_continues_after_a_single_task_failure(tmp_path, monkeypatch):
    """A task exception must not abort the run: the other tasks still run and a
    partial report is written (the M5 defect: ``future.result()`` was unguarded)."""
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setenv("GITCG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GITCG_REPORTS_DIR", str(tmp_path / "reports"))

    class _Shim(ThreadPoolExecutor):
        def __init__(self, max_workers=None, mp_context=None, initializer=None, initargs=(), **kw):
            super().__init__(max_workers=max_workers or 1)

    monkeypatch.setattr(
        ladder,
        "_futures",
        types.SimpleNamespace(ProcessPoolExecutor=_Shim, as_completed=_futures.as_completed),
    )

    def _fake_worker(task_dict):
        task = ladder.LadderTask(**task_dict)
        if task.mode == CFR:
            raise RuntimeError("boom")
        return _row(task.opponent, task.seed, task.mode, 1.0)

    monkeypatch.setattr(ladder, "_worker_run", _fake_worker)

    rc = main(
        [
            "--opponents",
            "superconduct_aggro",
            "--seeds",
            "2",
            "--workers",
            "2",
            "--bridge-workers",
            "1",
            "--tag",
            "t",
        ]
    )
    assert rc == 1
    reports = list((tmp_path / "reports" / "search").glob("t_*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["failures"]["count"] == 2  # the two CFR tasks raised
    assert report["matches"] == 2  # both expert tasks still completed
    assert report["pooled_expert"] == 1.0
