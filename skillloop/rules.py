"""The *rules* layer of the skill library (group ② baseline).

The course asks for a **static** condition that only knows the game's rules, not any
strategy learned from experience. This module builds exactly that, deterministically,
from data we already have:

* the objective types the PvE stages use (``configs/npc`` + ``npc.objectives``), which
  define what "three stars" means;
* a small, hand-written set of engine rules (dice, reactions, switches, end of round,
  equipment/food/support cards) that any player must know regardless of strategy.

Keeping this layer *generated* (rather than hand-copied for each experiment) matters:
the static condition must be reproducible and contain no accidentally-learned
strategy, otherwise the ② vs ③ comparison is not measuring self-evolution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from skillloop.library import SkillLibrary
from skillloop.schema import Skill, SkillConditions

# Engine rules: stable, deck-independent, and *not* strategy.
GLOBAL_RULES: tuple[tuple[str, str, str], ...] = (
    ("rule_dice", "骰子与费用",
     "每轮开始投掷 8 个骰子；打出牌/使用技能要求支付对应元素或万能骰。元素调和可以弃 1 张手牌换 1 个当前出战角色元素的骰子。"),
    ("rule_switch", "切换角色",
     "切换出战角色通常花费 1 个任意骰；被击倒时强制切换不花费骰子。切换会消耗一次行动。"),
    ("rule_end_round", "结束回合",
     "宣布结束后本回合不再行动；双方都宣布后进入结束阶段并结算。先宣布的一方在下一轮先手。"),
    ("rule_reaction", "元素反应",
     "给角色附着元素后再用另一种元素攻击会触发反应（蒸发/融化/超载/感电/冻结/扩散/结晶/绽放等），反应通常带来额外伤害或控制。"),
    ("rule_burst", "元素爆发",
     "元素爆发需要角色能量充满（能量通过使用技能或受到伤害积累）；爆发通常是最强的一击，但需要预留骰子。"),
    ("rule_equipment", "装备牌",
     "武器/圣遗物装备后永久增益对应角色；装备需要花费骰子与一次行动，收益在后续回合体现。"),
    ("rule_food", "食物牌",
     "食物牌通常每回合只能使用一次，提供治疗或增益；治疗对存活与「角色被击倒次数」类目标有帮助。"),
    ("rule_support", "支援牌",
     "支援牌放入支援区，在后续回合持续提供效果；支援区有数量上限，满了需要替换。"),
    ("rule_mulligan", "起手换牌",
     "起手可以替换任意张手牌；换牌不消耗骰子，但换掉的牌不会回来。优先保留与关卡目标相关的牌。"),
    ("rule_draw", "抽牌与手牌上限",
     "每轮开始抽 2 张牌；手牌上限为 10 张，超出会弃牌。手牌少时优先考虑过牌手段。"),
)

# Hand-written **simple card-playing strategies** (the static arm is "rules + simple
# strategy", per the course brief). Deliberately generic: no learned data, no stage
# tuning -- that is what the dynamic arm adds on top.
SIMPLE_STRATEGY: tuple[tuple[str, str, str], ...] = (
    ("strat_dice_economy", "骰子经济",
     "每轮先用最少的骰子完成本轮目标：优先支付关键技能/卡牌，避免为了低价值动作花掉最后一个骰子；同色骰不足时优先元素调和而不是放弃关键动作。"),
    ("strat_mulligan", "起手保留低费与目标相关牌",
     "起手换牌保留 1-2 费的技能/事件/装备牌；换掉当前用不上的高费牌；若关卡目标是装备或事件牌，优先保留对应类别的牌。"),
    ("strat_focus_fire", "集火残血角色",
     "优先攻击对方血量最低的角色以尽快减少对方行动数；对方有护盾/免疫时先用低费手段消耗或绕开，再打伤害。"),
    ("strat_reaction", "先附着再补元素",
     "需要触发元素反应时，先给目标附着一种元素，再用另一种元素攻击；注意不要给已附着同元素的角色重复附着，避免浪费一次行动。"),
    ("strat_burst_timing", "爆发留给斩杀窗口",
     "元素爆发伤害最高但需要能量与骰子：能量满后先确认对方角色血量是否进入斩杀线，不是斩杀窗口时优先用普通技能创造窗口。"),
    ("strat_switch_cost", "换人只在必要时",
     "主动换人花费 1 个骰子，只在能规避被击倒或收掉残血人头时使用；被击倒时的强制换人不花骰子，不要提前浪费主动换人。"),
    ("strat_end_round", "没收益就结束回合",
     "当本轮已无收益动作（无法击杀、无法完成目标、没有需要保留的骰子）时，尽早宣布结束回合抢下一轮先手；但关卡目标未完成时不要提前结束。"),
    ("strat_equipment_timing", "装备前两轮打",
     "装备牌只有在前两轮打出才有回本空间；后期为了完成装备目标时可以打，但不要因此放弃当回合的击杀机会。"),
    ("strat_protect_low_hp", "低血量角色保护",
     "出战角色血量偏低时优先治疗、换下或用减伤手段，而不是继续对拼；一次阵亡的节奏损失远大于一次换人。"),
    ("strat_card_draw", "手牌少时优先过牌",
     "手牌 ≤2 张时优先使用过牌/抽牌手段补充资源；手牌接近上限（10 张）时先打掉会溢出的牌，避免弃牌。"),
)

# Objective type -> the rule that explains what the objective is asking for.
OBJECTIVE_RULES: dict[str, tuple[str, str]] = {
    "win": ("规则_目标_胜利", "目标「赢得胜利」：把对方所有角色击倒。"),
    "win_within_rounds": (
        "规则_目标_回合上限",
        "目标「N 回合内胜利」：必须在第 N 轮结束前击倒对方全部角色，拖延即失败；节奏优先于长期收益。",
    ),
    "defeated_less_than": (
        "规则_目标_少被击倒",
        "目标「角色被击倒次数小于 K」：我方阵亡次数受限，需要更保守地保护低血量角色（治疗、换人、减少无谓对拼）。",
    ),
    "play_equipment_card": (
        "规则_目标_装备牌",
        "目标「打出装备牌」：需要在局内实际打出指定数量的武器/圣遗物牌，装备牌要尽早打出才不挤占后期节奏。",
    ),
    "play_event_cards": (
        "规则_目标_事件牌",
        "目标「打出事件牌」：需要在局内打出指定数量的事件牌，事件牌是即时效果，注意留够骰子。",
    ),
    "use_elemental_burst": (
        "规则_目标_元素爆发",
        "目标「使用元素爆发」：需要实际释放指定次数的爆发，注意提前攒能量并预留骰子。",
    ),
    "trigger_elemental_reactions": (
        "规则_目标_元素反应",
        "目标「触发元素反应」：需要造成指定次数的元素反应，先附着再补另一种元素；注意对方已有附着。",
    ),
}


@dataclass(frozen=True)
class RuleLibraryOptions:
    objective_types: Iterable[str] = ()
    include_globals: bool = True


def rule_skills(
    objective_types: Iterable[str] = (),
    *,
    include_globals: bool = True,
    include_strategy: bool = True,
) -> list[Skill]:
    """Rules layer as ``Skill`` objects (``layer="rule"``, no evidence required)."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    skills: list[Skill] = []
    if include_globals:
        for skill_id, name, text in GLOBAL_RULES:
            skills.append(
                Skill(
                    skill_id=skill_id,
                    skill_name=name,
                    description=text,
                    conditions=SkillConditions(),
                    layer="rule",
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
    if include_strategy:
        for skill_id, name, text in SIMPLE_STRATEGY:
            skills.append(
                Skill(
                    skill_id=skill_id,
                    skill_name=name,
                    description=text,
                    conditions=SkillConditions(),
                    layer="strategy",
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
    for objective in sorted(set(objective_types)):
        entry = OBJECTIVE_RULES.get(objective)
        if entry is None:
            continue
        skill_id, text = entry
        skills.append(
            Skill(
                skill_id=skill_id,
                skill_name=objective,
                description=text,
                conditions=SkillConditions(objective_types=(objective,)),
                layer="rule",
                created_at=stamp,
                updated_at=stamp,
            )
        )
    return skills


def build_rules_library(
    objective_types: Iterable[str] = (),
    *,
    version: int = 1,
    include_strategy: bool = True,
) -> SkillLibrary:
    """Group ②'s library: game rules + hand-written simple strategy (no learned content)."""
    skills = rule_skills(objective_types, include_strategy=include_strategy)
    return SkillLibrary.from_skills(
        skills,
        version=version,
        meta={"kind": "rules", "note": "generated by skillloop.rules; no learned content"},
    )
