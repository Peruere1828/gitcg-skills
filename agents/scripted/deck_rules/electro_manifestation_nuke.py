# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

from reps.schema import DecisionContext

from .common import choose_elemental_focus_reroll, make_rule_config

RULE_CONFIG = make_rule_config(
    slug="electro_manifestation_nuke",
    opener="罗莎莉亚",
    carry=("雷音权现", "罗莎莉亚", "优菈"),
    bench=("罗莎莉亚", "优菈"),
    preferred_elements=("GCG_TAG_ELEMENT_ELECTRO", "GCG_TAG_ELEMENT_CRYO"),
    style="superconduct_burst",
)


def choose_active(agent, context: DecisionContext):
    aura = agent._opponent_active_aura(context)
    preferred = ("雷音权现", "罗莎莉亚", "优菈") if aura > 0 else ("罗莎莉亚", "优菈", "雷音权现")
    return agent._choose_active_by_names(context, preferred, require_safe_target=True)


def choose_reroll(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    if active_name == "雷音权现":
        return choose_elemental_focus_reroll(
            agent,
            context,
            primary=("GCG_TAG_ELEMENT_ELECTRO",),
            secondary=("GCG_TAG_ELEMENT_CRYO",),
            rule_id="reroll.electro_nuke_curve",
        )
    return choose_elemental_focus_reroll(
        agent,
        context,
        primary=("GCG_TAG_ELEMENT_CRYO",),
        secondary=("GCG_TAG_ELEMENT_ELECTRO",),
        rule_id="reroll.electro_nuke_curve",
    )


def choose_action(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    if round_number <= 2:
        choice = agent._play_named_card(
            context,
            card_names=("元素幻变：超导祝佑", "骑士团图书馆", "凯瑟琳", "立本", "迪娜泽黛", "白手套和渔夫"),
            rule_id="action.electro_nuke_build_engine",
            min_tactical_score=1,
        )
        if choice is not None:
            return choice

    if active_name == "罗莎莉亚":
        if aura <= 0:
            return agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E", "GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A"),
                rule_id="action.electro_nuke_apply_cryo",
            )
        return agent._switch_to_named_targets(
            context,
            target_names=("雷音权现",),
            rule_id="action.electro_nuke_switch_electro",
            require_safe_target=True,
        )
    if active_name == "雷音权现":
        if aura > 0:
            return agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_E", "GCG_SKILL_TAG_A"),
                rule_id="action.electro_nuke_finish_window",
            )
        return agent._switch_to_named_targets(
            context,
            target_names=("罗莎莉亚", "优菈"),
            rule_id="action.electro_nuke_reset_cryo",
            require_safe_target=True,
        )
    if active_name == "优菈":
        choice = agent._use_active_skill_types(
            context,
            skill_types=("GCG_SKILL_TAG_E", "GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A"),
            rule_id="action.electro_nuke_backup_cryo",
        )
        if choice is not None and aura <= 0:
            return choice
        return agent._switch_to_named_targets(
            context,
            target_names=("雷音权现", "罗莎莉亚") if aura > 0 else ("罗莎莉亚", "雷音权现"),
            rule_id="action.electro_nuke_rotate_backup",
            require_safe_target=True,
        )
    return None


def card_context_bonus(agent, card, context: DecisionContext, mode: str) -> int:
    if card is None:
        return 0
    name = str(card.name)
    round_number = agent._round_number(context)
    active_name = agent._active_character_name(context)
    aura = agent._opponent_active_aura(context)
    dice_count = len(agent._player(context).dice)
    low_hp_exists = any(
        int(character.health) <= max(3, int(character.max_health) // 2)
        for character in agent._player(context).characters
        if not character.defeated
    )
    if name == "元素幻变：超导祝佑":
        return 5 if round_number <= 2 else 1
    if name == "元素共鸣：交织之冰":
        if active_name in {"罗莎莉亚", "优菈"} and round_number <= 3:
            return 3
        return -2 if active_name == "雷音权现" and round_number <= 2 else 0
    if name == "元素共鸣：粉碎之冰":
        return 4 if aura > 0 and active_name in {"罗莎莉亚", "优菈"} else -3
    if name in {"骑士团图书馆", "凯瑟琳", "立本", "迪娜泽黛", "白手套和渔夫"}:
        return 4 if round_number <= 2 else 1
    if name == "黄金剧团的奖赏":
        return 3 if active_name in {"雷音权现", "优菈"} and round_number >= 2 else 0
    if name == "赌徒的耳环":
        return 3 if active_name in {"雷音权现", "优菈"} else 0
    if name == "最好的伙伴！":
        return 4 if dice_count <= 2 else -2
    if name == "交给我吧！":
        if active_name == "罗莎莉亚" and aura <= 0:
            return 3
        if active_name == "雷音权现" and aura > 0:
            return 2
        return -1 if round_number >= 4 else 0
    if name == "自由的新风":
        return 2 if active_name == "罗莎莉亚" and aura > 0 else 0
    if name == "北地烟熏鸡":
        return 3 if active_name in {"罗莎莉亚", "优菈"} and aura > 0 else -2
    if name == "唐杜尔烤鸡":
        return 3 if active_name == "雷音权现" and round_number >= 3 else -2
    if name == "奇瑰之汤":
        return 3 if low_hp_exists else -2
    return 0
