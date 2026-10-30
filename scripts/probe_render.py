#!/usr/bin/env python3
"""Render one real PvE decision as a prompt (no LLM call) and print the sizes.

Why a probe and not a test: the renderer's job is to be *small* (prompt-cache and
cost) and *faithful* (the state it shows must be what the agent acts on). Both are
easier to judge by eye on one real decision than by asserting strings in a unit test.

Usage::

    python scripts/probe_render.py --stage timmie --deck superconduct_aggro --seed 1
    python scripts/probe_render.py --stage wagner --skills   # with a dummy skill block
"""

from __future__ import annotations

import argparse
import sys

from envs.decks import deck_spec
from envs.match import run_match
from envs.policy import expert_policy
from npc.policy import npc_policy_for_stage
from npc.spec import get_stage
from skillloop.render import StateRenderer, TaskSpec, select_candidates
from skillloop.schema import Skill, SkillConditions


class _Capture:
    """Delegate to ``base`` but remember the first renderable decision."""

    def __init__(self, base, *, target=1, request_type: str = "action") -> None:
        self.base = base
        self.name = f"capture({base.name})"
        self.captured: list = []
        self.target = target
        self.request_type = request_type

    def choose(self, built):
        if len(self.captured) < self.target:
            if self.request_type in ("any", built.context.request_type.value):
                candidates = select_candidates(built.context)
                if candidates:
                    self.captured.append((built.context, candidates))
        return int(self.base.choose(built))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage", default="timmie")
    ap.add_argument("--deck", default="superconduct_aggro")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--skills", action="store_true", help="inject two dummy skills")
    ap.add_argument("--request", default="action",
                    help="capture the first decision of this request type (or 'any')")
    ap.add_argument("--pvp-opponent", default=None,
                    help="skip the NPC deck and play a scripted deck instead "
                         "(useful before the NPC engine overlay is installed)")
    args = ap.parse_args(argv)

    stage = get_stage(args.stage)
    task = TaskSpec(
        stage=stage.name,
        display=stage.display or stage.name,
        objectives=tuple(obj.text for obj in stage.objectives),
        deck=args.deck,
    )
    contestant = deck_spec(args.deck)
    if args.pvp_opponent:
        opponent_deck = deck_spec(args.pvp_opponent)
        opponent_policy = expert_policy(args.pvp_opponent, seed=args.seed)
    else:
        opponent_deck = type(contestant)(
            name=f"npc_{stage.name}", characters=stage.deck_characters, cards=contestant.cards
        )
        opponent_policy = npc_policy_for_stage(stage)
    capture = _Capture(expert_policy(args.deck, seed=args.seed), request_type=args.request)
    record = run_match(
        contestant,
        opponent_deck,
        capture,
        opponent_policy,
        seed=args.seed,
        record_decisions=False,
    )
    print(f"[probe] stage={stage.name} deck={args.deck} winner={record.winner} rounds={record.rounds}")
    print(f"[probe] task: {task.describe()}")

    if not capture.captured:
        print("[probe] no renderable decision captured (game too short?)")
        return 1

    skills = []
    if args.skills:
        skills = [
            Skill(
                skill_id="demo_speed_equip",
                skill_name="速攻关：装备只打一张",
                description="当关卡要求 N 回合内胜利时，第二张装备牌的节奏损失大于收益，最多打一张。",
                conditions=SkillConditions(objective_types=("win_within_rounds",)),
                layer="stage_specific",
            ),
            Skill(
                skill_id="demo_end_round",
                skill_name="目标没做完别结束回合",
                description="若我方还能行动且关卡目标未达成，不要宣布结束回合。",
                conditions=SkillConditions(),
                layer="strategy",
            ),
        ]

    context, candidates = capture.captured[0]
    renderer = StateRenderer(_assets(), opponent_name=f"NPC({stage.display})")
    system = renderer.system_message(task=task, skills=skills)
    user = renderer.user_message(context, candidates)
    for label, message in (("SYSTEM", system), ("USER", user)):
        body = message["content"]
        print(f"\n===== {label} ({len(body)} chars, ~{len(body) // 2} tokens 估算) =====")
        print(body)
    print(f"\n[probe] candidates={len(candidates)} step={context.step_index} request={context.request_type.value}")
    return 0


def _assets():
    from envs.policy import _asset_catalog

    return _asset_catalog()


if __name__ == "__main__":
    sys.exit(main())
