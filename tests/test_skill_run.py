from __future__ import annotations

from skillloop.run import RunSpec, _seed_list, summarize


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
