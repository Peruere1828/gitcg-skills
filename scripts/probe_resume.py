#!/usr/bin/env python3
"""Probe: can we park a game at an LLM decision and resume it later?

Motivation (throughput): a game spends most of its wall time waiting for the model,
so one process per game wastes CPU. If the engine state can be snapshotted at the
decision boundary, one process could park many games, fire their model requests
concurrently, and resume each game when the answer arrives.

The upstream engine investigation (``gitcg`` 0.21.0) established two rules:

* snapshots are exact forks only at ``Game.is_resumable()`` pause points;
* game-level attributes outside ``GameState`` must be re-applied on resume.

This probe checks those rules **on the decision types our agent actually uses**
(``action``, ``switch_hands``, ``choose_active``) for a PvP game and a PvE stage:
it compares the terminal state of an uninterrupted game with one that was parked
at that decision and then resumed from its snapshot.

Note on the mechanism: exceptions raised inside a cffi callback are swallowed, so a
policy cannot "unwind" the engine. The parked policy therefore returns a fallback
action (the live game is then discarded) and the probe resumes from the snapshot
afterwards -- which is exactly what the scheduler would do.

Usage::

    python scripts/probe_resume.py
    python scripts/probe_resume.py --stage timmie --deck superconduct_aggro
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any, Callable

from gitcg import Game, State
from gitcg import low_level

from envs.decks import deck_spec
from envs.match import PolicyPlayer, _build_create_param
from envs.policy import expert_policy


@dataclass
class Capture:
    request_type: str
    snapshot: str
    resumable: bool
    round_number: int
    fallback_code: int


class _CapturePolicy:
    """Answers normally until the target decision, then snapshots and answers anyway."""

    def __init__(self, base: Any, *, request_type: str, occurrence: int,
                 snapshotter: Callable[[], str], is_resumable: Callable[[], bool],
                 fallback: Callable[[Any], int]) -> None:
        self.base = base
        self.name = f"capture({base.name})"
        self.request_type = request_type
        self.occurrence = occurrence
        self.snapshotter = snapshotter
        self.is_resumable = is_resumable
        self.fallback = fallback
        self.seen = 0
        self.capture: Capture | None = None

    def choose(self, built):
        code = int(self.base.choose(built))
        if built.context.request_type.value == self.request_type:
            if self.seen == self.occurrence and self.capture is None:
                self.seen += 1
                self.capture = Capture(
                    request_type=self.request_type,
                    snapshot=self.snapshotter(),
                    resumable=bool(self.is_resumable()),
                    round_number=-1,
                    fallback_code=int(self.fallback(built)),
                )
                return self.capture.fallback_code
            self.seen += 1
        return code


def _new_game(create_param, policy0, policy1) -> Game:
    game = Game(create_param=create_param)
    game.set_player(0, PolicyPlayer(0, policy0))
    game.set_player(1, PolicyPlayer(1, policy1))
    return game


def _fallback(built) -> int:
    from envs.match import _fallback_code

    return int(_fallback_code(built))


def _drive(game: Game, capture_policy: _CapturePolicy | None = None) -> tuple[str | None, Capture | None]:
    game.start()
    while game.is_running():
        game.step()
        if capture_policy is not None and capture_policy.capture is not None:
            cap = capture_policy.capture
            cap.round_number = int(game.round_number())
            return None, cap          # the live game is discarded by the caller
    return game.state().json(), None


def probe_case(*, name: str, create_param: Callable[[], Any],
               policy0: Callable[[], Any], policy1: Callable[[], Any],
               request_type: str, occurrence: int, seed: int) -> dict[str, Any]:
    baseline = _new_game(create_param(), policy0(), policy1())
    terminal, _ = _drive(baseline)

    live = _new_game(create_param(), policy0(), policy1())
    capture_policy = _CapturePolicy(
        policy0(), request_type=request_type, occurrence=occurrence,
        snapshotter=lambda: live.state().json(),
        is_resumable=lambda: live.is_resumable(),
        fallback=_fallback,
    )
    live.set_player(0, PolicyPlayer(0, capture_policy))
    _terminal_live, cap = _drive(live, capture_policy)
    if cap is None:
        return {"name": name, "request_type": request_type, "parked": False}

    resumed = Game(state=State(json=cap.snapshot))
    # State attributes (seed, dice/hand limits) travel inside GameState; only
    # *player-level* attrs such as ATTR_PLAYER_ALWAYS_OMNI_* live outside and would
    # need re-applying. Our decks do not use them (verified: no ALWAYS_OMNI in envs/).
    resumed.set_player(0, PolicyPlayer(0, policy0()))
    resumed.set_player(1, PolicyPlayer(1, policy1()))
    terminal_resumed, _ = _drive(resumed)

    return {
        "name": name,
        "request_type": request_type,
        "parked": True,
        "round": cap.round_number,
        "resumable_flag": cap.resumable,
        "terminal_equal": terminal == terminal_resumed,
        "baseline_bytes": len(terminal or ""),
        "resumed_bytes": len(terminal_resumed or ""),
    }


def _sweep(args) -> int:
    from npc.policy import npc_policy_for_stage
    from npc.spec import get_stage

    rows = []
    for seed in (1, 2, 3, 4):
        for occurrence in (0, 1, 2):
            rows.append(probe_case(
                name="pvp", create_param=lambda s=seed: _build_create_param(
                    deck_spec(args.deck), deck_spec("dvalin_bonk"), seed=s),
                policy0=lambda s=seed: expert_policy(args.deck, seed=s),
                policy1=lambda s=seed: expert_policy("dvalin_bonk", seed=s),
                request_type="action", occurrence=occurrence, seed=seed,
            ))
    for stage_name in [s for s in args.stages.split(",") if s]:
        stage = get_stage(stage_name)
        contestant = deck_spec(args.deck)
        npc_deck = type(contestant)(name=f"npc_{stage.name}",
                                    characters=stage.deck_characters, cards=contestant.cards)
        for seed in (1, 2):
            for occurrence in (0, 1, 2):
                rows.append(probe_case(
                    name=f"pve:{stage.name}",
                    create_param=lambda s=seed, c=contestant, n=npc_deck: _build_create_param(c, n, seed=s),
                    policy0=lambda s=seed: expert_policy(args.deck, seed=s),
                    policy1=lambda st=stage: npc_policy_for_stage(st),
                    request_type="action", occurrence=occurrence, seed=seed,
                ))
    parked = [r for r in rows if r.get("parked")]
    ok = [r for r in parked if r.get("terminal_equal")]
    for row in rows:
        flag = "OK " if row.get("terminal_equal") else ("-- " if not row.get("parked") else "BAD")
        print(f"{flag} {row['name']:18s} type={row['request_type']:13s} "
              f"resumable={row.get('resumable_flag')} round={row.get('round')}")
    print(f"\n[probe] parked={len(parked)} identical={len(ok)}/{len(parked)}")
    return 0 if len(ok) == len(parked) else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage", default="timmie")
    ap.add_argument("--deck", default="superconduct_aggro")
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--occurrence", type=int, default=0)
    ap.add_argument("--sweep", action="store_true",
                    help="action decisions: seeds 1..4 x occurrences 0..2, PvP + PvE")
    ap.add_argument("--stages", default="timmie,wagner,blanche")
    args = ap.parse_args(argv)

    from npc.policy import npc_policy_for_stage
    from npc.spec import get_stage

    if args.sweep:
        return _sweep(args)

    results = []

    def pvp_cp():
        return _build_create_param(deck_spec(args.deck), deck_spec("dvalin_bonk"), seed=args.seed)

    for request_type in ("switch_hands", "choose_active", "action"):
        results.append(probe_case(
            name="pvp", create_param=pvp_cp,
            policy0=lambda: expert_policy(args.deck, seed=args.seed),
            policy1=lambda: expert_policy("dvalin_bonk", seed=args.seed),
            request_type=request_type, occurrence=args.occurrence, seed=args.seed,
        ))

    stage = get_stage(args.stage)
    contestant = deck_spec(args.deck)
    npc_deck = type(contestant)(name=f"npc_{stage.name}",
                                characters=stage.deck_characters, cards=contestant.cards)

    def pve_cp():
        return _build_create_param(contestant, npc_deck, seed=args.seed)

    results.append(probe_case(
        name=f"pve:{stage.name}", create_param=pve_cp,
        policy0=lambda: expert_policy(args.deck, seed=args.seed),
        policy1=lambda: npc_policy_for_stage(stage),
        request_type="action", occurrence=args.occurrence, seed=args.seed,
    ))

    print(json.dumps(results, ensure_ascii=False, indent=2))
    parked = [item for item in results if item.get("parked")]
    ok = bool(parked) and all(item.get("terminal_equal") for item in parked)
    print(f"\n[probe] parked={len(parked)} all resumed identically: {ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
