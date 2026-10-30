"""Opponent pool registry (PLAN.md WS2).

The fixed假想敌 pool is the set of ported scripted expert decks. Baselines
(heuristic / legal-random) are included so early experiments have a lower bound
to compare against.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents.scripted.registry import RAW_DECKS
from envs.decks import expert_deck_specs


@dataclass(frozen=True)
class Opponent:
    name: str
    deck: str
    policy: str  # policy spec understood by envs.policy.build_policy
    kind: str  # "scripted" | "baseline"
    display: str = ""


def scripted_opponents() -> tuple[Opponent, ...]:
    """All ported scripted decks as opponents (policy == deck slug)."""
    entries = {entry.slug: entry for entry in RAW_DECKS}
    ordered = [spec.name for spec in expert_deck_specs()]
    opponents: list[Opponent] = []
    for slug in ordered:
        entry = entries.get(slug)
        opponents.append(
            Opponent(
                name=slug,
                deck=slug,
                policy=f"expert:{slug}",
                kind="scripted",
                display=entry.name if entry else slug,
            )
        )
    return tuple(opponents)


def baseline_opponents() -> tuple[Opponent, ...]:
    return (
        Opponent(name="heuristic", deck="superconduct_aggro", policy="heuristic", kind="baseline"),
        Opponent(
            name="legal_random",
            deck="superconduct_aggro",
            policy="legal_random",
            kind="baseline",
        ),
    )


def all_opponents() -> tuple[Opponent, ...]:
    return scripted_opponents() + baseline_opponents()


def opponent_names() -> list[str]:
    return [opp.name for opp in scripted_opponents()]


def opponent_by_name(name: str) -> Opponent:
    for opponent in all_opponents():
        if opponent.name == name:
            return opponent
    raise KeyError(f"unknown opponent: {name!r}")
