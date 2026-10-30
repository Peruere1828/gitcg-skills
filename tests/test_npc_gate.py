"""PvE-v1 gate runner helpers (``scripts.run_npc_gate``, PLAN.md §4.4).

Engine-free: exercises the pure aggregation/reporting helpers on synthetic rows.
"""

from __future__ import annotations

from scripts.run_npc_gate import (
    GATE_THRESHOLD,
    build_decks,
    cluster_bootstrap_ci,
    render_markdown,
    _reproduce_command,
    _seeds,
    _stages,
    _unique,
)


def _row(stage: str, seed: int, *, three_star: bool, winner: int | None = 0) -> dict:
    return {
        "stage": stage,
        "seed": seed,
        "winner": winner,
        "three_star": three_star,
        "stars": 3 if three_star else 2,
        "rounds": 4,
        "objectives": {"win": winner == 0},
        "error": None,
        "truncated": False,
    }


def test_cluster_bootstrap_ci_brackets_rate():
    rows = [_row("a", s, three_star=(s % 2 == 0)) for s in range(10)]
    rows += [_row("b", s, three_star=True) for s in range(10)]
    ci = cluster_bootstrap_ci(rows, "three_star", iterations=500)
    assert 0.0 <= ci["ci_low"] <= ci["rate"] <= ci["ci_high"] <= 1.0
    assert ci["stages"] == 2


def test_cluster_bootstrap_ci_empty():
    ci = cluster_bootstrap_ci([], "three_star", iterations=10)
    assert ci == {"rate": 0.0, "ci_low": 0.0, "ci_high": 0.0, "stages": 0}


def test_stage_and_seed_parsing():
    assert _stages("a,b") == ("a", "b")
    assert _stages("all") == tuple(sorted(_stages("all")))
    assert _seeds("1,2,3", 10) == (1, 2, 3)
    assert _seeds(None, 3) == (1, 2, 3)


def test_stage_and_seed_dedup():
    # A repeated entry would run (and count) the same game twice, shrinking the CI.
    assert _seeds("5,7,5", 10) == (5, 7)
    assert _stages("a,b,a") == ("a", "b")
    assert _unique(["x", "y", "x", "y", "z"]) == ("x", "y", "z")


def test_reproduce_command_reflects_explicit_seeds():
    explicit = _reproduce_command(
        seeds_value="5,7", seeds=(5, 7), stages=("a", "b"), workers=4, tag="t"
    )
    assert "--seeds 5,7" in explicit
    assert "--seed-count" not in explicit
    derived = _reproduce_command(
        seeds_value=None, seeds=(1, 2, 3), stages=("a",), workers=8, tag="t"
    )
    assert "--seed-count 3" in derived
    assert "--seeds " not in derived


def test_build_decks_shapes():
    decks = build_decks()
    assert len(decks["poolc_superconduct"].cards) == 30
    assert len(decks["poolc_superconduct_defensive"].cards) == 30
    assert decks["superconduct_aggro"].name == "superconduct_aggro"
    assert decks["poolc_superconduct"] != decks["poolc_superconduct_defensive"]


def test_render_markdown_has_gate_and_ablation_sections():
    payload = {
        "generated_at": "2026-10-12T00:00:00",
        "tag": "t",
        "command": "python -m scripts.run_npc_gate",
        "decklab_note": "note",
        "smoke_note": "smoke note",
        "open_issues": ["issue one"],
        "gate": {
            "passed": True,
            "threshold": GATE_THRESHOLD,
            "three_star_rate": 0.7,
            "three_star_ci_low": 0.6,
            "three_star_ci_high": 0.8,
            "three_star_cluster_low": 0.55,
            "three_star_cluster_high": 0.85,
            "win_rate": 0.95,
            "win_ci_low": 0.9,
            "win_ci_high": 0.99,
            "games": 16,
            "n_stages": 16,
            "n_seeds": 1,
            "errors": 0,
            "truncated": 0,
            "mean_stars": 2.7,
            "stages": {"a": {"games": 1, "wins": 1, "win_rate": 1.0, "three_star": 1, "three_star_rate": 1.0, "mean_stars": 3.0, "mean_rounds": 4.0}},
            "objectives": {"win": {"games": 1, "achieved": 1, "achieved_rate": 1.0}},
        },
        "arms": {
            "gate": {
                "deck": "d",
                "policy0": "contestant",
                "overall": {
                    "games": 1,
                    "win_rate": 1.0,
                    "three_star_rate": 1.0,
                    "three_star_ci": {"ci_low": 0.5, "ci_high": 1.0},
                    "three_star_cluster_ci": {"ci_low": 0.5, "ci_high": 1.0},
                },
            }
        },
    }
    md = render_markdown(payload)
    assert "PASS" in md
    assert "逐关" in md
    assert "组牌质量 vs 打牌质量" in md
    assert "decklab 组牌口径" in md
