"""Elo ladder over synthetic match records."""

from __future__ import annotations

from eval.ladder import compute_elo


def _match(index: int, deck0: str, deck1: str, winner: int | None) -> dict:
    return {"task_index": index, "deck0": deck0, "deck1": deck1, "winner": winner}


def test_elo_winner_gains_rating():
    matches = [_match(i, "A", "B", 0) for i in range(10)]
    ratings = compute_elo(matches)
    assert ratings["A"] > 1500 > ratings["B"]
    assert abs((ratings["A"] - 1500) - (1500 - ratings["B"])) < 1e-6


def test_elo_draws_keep_parity():
    matches = [_match(i, "A", "B", None) for i in range(5)]
    ratings = compute_elo(matches)
    assert abs(ratings["A"] - ratings["B"]) < 1e-9


def test_elo_empty():
    assert compute_elo([]) == {}


def test_elo_prefers_explicit_player_identity():
    # Both sides pilot the *same* deck slug, but the identities differ: the
    # contestant must not collapse into the pool opponent's rating entry.
    matches = [
        {"task_index": 0, "player0": "contestant", "player1": "pool:a",
         "deck0": "a", "deck1": "a", "winner": 0}
    ]
    ratings = compute_elo(matches)
    assert set(ratings) == {"contestant", "pool:a"}
    assert ratings["contestant"] > 1500 > ratings["pool:a"]
