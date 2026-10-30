"""Deck-building sensitivity (PLAN.md WS4 decklab, M5).

v1 scope (per PLAN): within a fixed template, test swapping **flex cards** to see
which changes help against the fixed scripted opponent pool. We keep exactly 30
cards: remove one copy of a card and add one copy of a candidate.

Because mutated decks have no dedicated scripted rule profile, evaluations use a
fixed generic policy (``heuristic``) on our side and the scripted expert on the
opponent side. Results are small-sample win-rate deltas with Wilson CIs.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Sequence

from common.paths import ensure_dir, reports_dir
from envs.decks import deck_spec, expert_deck_specs
from envs.rollout import MatchTask, run_tasks
from eval.stats import estimate_rate
from reps.schema import DeckSpec


def mutate_deck(base_name: str, remove_id: int, add_id: int, *, tag: str = "swap") -> DeckSpec:
    """Return a DeckSpec with one copy of ``remove_id`` replaced by ``add_id``."""
    base = deck_spec(base_name)
    cards = list(base.cards)
    if remove_id not in cards:
        raise ValueError(f"card {remove_id} not in deck {base_name}")
    cards.remove(int(remove_id))
    cards.append(int(add_id))
    if len(cards) != len(base.cards):
        raise RuntimeError("card count changed during swap")
    name = f"{base_name}__{tag}_{remove_id}_to_{add_id}"
    return DeckSpec(name=name, characters=base.characters, cards=tuple(cards))


def candidate_cards(*, exclude: Iterable[int] = (), limit: int | None = None) -> tuple[int, ...]:
    """Candidate flex cards: cards in other scripted decks, most common first."""
    exclude_set = set(int(x) for x in exclude)
    counter: Counter[int] = Counter()
    for spec in expert_deck_specs():
        for card in spec.cards:
            if card not in exclude_set:
                counter[int(card)] += 1
    ordered = [card for card, _ in counter.most_common()]
    if limit is not None:
        ordered = ordered[:limit]
    return tuple(ordered)


@dataclass
class SwapEval:
    remove_card: int
    add_card: int
    games: int
    wins: int
    rate: float
    ci_low: float
    ci_high: float
    delta: float

    def as_dict(self) -> dict[str, float | int]:
        return {
            "remove_card": self.remove_card,
            "add_card": self.add_card,
            "games": self.games,
            "wins": self.wins,
            "rate": round(self.rate, 4),
            "ci_low": round(self.ci_low, 4),
            "ci_high": round(self.ci_high, 4),
            "delta_vs_baseline": round(self.delta, 4),
        }


def _inline(spec: DeckSpec) -> dict:
    return {"name": spec.name, "characters": list(spec.characters), "cards": list(spec.cards)}


def _win_rate_p0(matches: Sequence[dict]) -> tuple[int, int, float]:
    wins = sum(1 for m in matches if m["winner"] == 0)
    draws = sum(1 for m in matches if m["winner"] is None)
    n = len(matches)
    return wins, n, (wins + 0.5 * draws) / n if n else 0.0


def _tasks_for(
    deck: DeckSpec,
    opponents: Sequence[str],
    seeds: Sequence[int],
    *,
    policy0: str,
) -> list[MatchTask]:
    inline = _inline(deck)
    return [
        MatchTask(
            index=i,
            deck0=deck.name,
            deck1=opp,
            seed=int(seed),
            policy0=policy0,
            policy1="expert",
            deck0_inline=inline,
        )
        for i, (opp, seed) in enumerate((o, s) for o in opponents for s in seeds)
    ]


def evaluate_swaps(
    base_name: str,
    *,
    opponents: Sequence[str],
    seeds: Sequence[int],
    swaps: Sequence[tuple[int, int]],
    policy0: str = "heuristic",
    workers: int = 1,
) -> dict:
    """Evaluate each (remove, add) swap against the base deck at paired seeds."""
    base = deck_spec(base_name)
    base_matches = run_tasks(_tasks_for(base, opponents, seeds, policy0=policy0), workers=workers)
    base_wins, base_n, base_rate = _win_rate_p0(base_matches)

    results: list[SwapEval] = []
    for remove_id, add_id in swaps:
        mutated = mutate_deck(base_name, remove_id, add_id)
        matches = run_tasks(
            _tasks_for(mutated, opponents, seeds, policy0=policy0), workers=workers
        )
        wins, n, rate = _win_rate_p0(matches)
        draws = sum(1 for m in matches if m["winner"] is None)
        est = estimate_rate(wins + 0.5 * draws, n)
        results.append(
            SwapEval(
                remove_card=int(remove_id),
                add_card=int(add_id),
                games=n,
                wins=wins,
                rate=rate,
                ci_low=est.low,
                ci_high=est.high,
                delta=rate - base_rate,
            )
        )
    results.sort(key=lambda r: -r.delta)
    return {
        "base": {"deck": base_name, "games": base_n, "wins": base_wins, "rate": base_rate},
        "opponents": list(opponents),
        "seeds": list(seeds),
        "policy0": policy0,
        "swaps": [r.as_dict() for r in results],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Flex-card swap sensitivity.")
    parser.add_argument("--deck", default="superconduct_aggro")
    parser.add_argument("--opponents", nargs="*", default=["natlan_battleship", "dual_mualani_stacks"])
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--flex-cards", type=int, default=3, help="top-N most duplicated cards in deck")
    parser.add_argument("--candidates", type=int, default=5)
    parser.add_argument("--workers", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    base = deck_spec(args.deck)
    counts = Counter(base.cards)
    flex_cards = [card for card, _ in counts.most_common(args.flex_cards)]
    add_pool = list(candidate_cards(exclude=base.cards, limit=args.candidates))
    swaps = [(f, a) for f in flex_cards for a in add_pool]
    result = evaluate_swaps(
        args.deck,
        opponents=args.opponents,
        seeds=tuple(range(args.seeds)),
        swaps=swaps,
        workers=args.workers,
    )
    stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    out = ensure_dir(reports_dir("decklab")) / f"swaps_{args.deck}_{stamp}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2)[:3000])
    print(f"[decklab] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
