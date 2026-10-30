# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

from reps.schema import DecisionContext

from .common import choose_elemental_focus_reroll, make_rule_config

RULE_CONFIG = make_rule_config(
    slug="double_geo_navia",
    opener="黄金王兽",
    carry=("娜维娅", "黄金王兽", "爱可菲"),
    bench=("黄金王兽", "爱可菲"),
    preferred_elements=("GCG_TAG_ELEMENT_GEO", "GCG_TAG_ELEMENT_CRYO"),
    style="crystallize",
)


def choose_active(agent, context: DecisionContext):
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    if aura > 0:
        preferred = ("娜维娅", "黄金王兽", "爱可菲")
    elif round_number <= 2:
        preferred = ("爱可菲", "黄金王兽", "娜维娅")
    else:
        preferred = ("黄金王兽", "娜维娅", "爱可菲")
    return agent._choose_active_by_names(context, preferred, require_safe_target=True)


def choose_reroll(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    if active_name == "爱可菲":
        primary = ("GCG_TAG_ELEMENT_CRYO",)
        secondary = ("GCG_TAG_ELEMENT_GEO",)
    else:
        primary = ("GCG_TAG_ELEMENT_GEO",)
        secondary = ("GCG_TAG_ELEMENT_CRYO",)
    return choose_elemental_focus_reroll(
        agent,
        context,
        primary=primary,
        secondary=secondary,
        rule_id="reroll.double_geo_navia_curve",
    )


def choose_action(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)

    if round_number <= 2:
        choice = agent._play_named_card(
            context,
            card_names=("迪娜泽黛", "卯师傅", "太郎丸", "骑士团图书馆", "最好的伙伴！", "交给我吧！", "海中寻宝"),
            rule_id="action.double_geo_navia_build_engine",
            min_tactical_score=1,
        )
        if choice is not None:
            return choice

    if active_name == "爱可菲":
        if aura <= 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E", "GCG_SKILL_TAG_A", "GCG_SKILL_TAG_Q"),
                rule_id="action.double_geo_navia_setup_cryo",
            )
            if choice is not None:
                return choice
        if aura > 0 and round_number <= 3:
            return agent._switch_to_named_targets(
                context,
                target_names=("娜维娅", "黄金王兽"),
                rule_id="action.double_geo_navia_handoff_finish",
                require_safe_target=True,
            )
        if round_number >= 5:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A", "GCG_SKILL_TAG_E"),
                rule_id="action.double_geo_navia_cryo_pressure",
            )
            if choice is not None:
                return choice
        return None

    if active_name == "黄金王兽":
        choice = agent._play_named_card(
            context,
            card_names=("异兽侵蚀",),
            rule_id="action.double_geo_navia_arm_wolflord",
        )
        if choice is not None:
            return choice
        if aura > 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_E", "GCG_SKILL_TAG_A"),
                rule_id="action.double_geo_navia_geo_pressure",
            )
            if choice is not None:
                return choice
        if round_number >= 4:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A", "GCG_SKILL_TAG_E"),
                rule_id="action.double_geo_navia_geo_pressure",
            )
            if choice is not None:
                return choice
        return agent._switch_to_named_targets(
            context,
            target_names=("爱可菲", "娜维娅"),
            rule_id="action.double_geo_navia_reset_chain",
            require_safe_target=True,
        )

    if active_name == "娜维娅":
        choice = agent._play_named_card(
            context,
            card_names=("不明流通渠道",),
            rule_id="action.double_geo_navia_arm_navia",
        )
        if choice is not None:
            return choice
        if aura > 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E", "GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A"),
                rule_id="action.double_geo_navia_finish_window",
            )
            if choice is not None:
                return choice
        if round_number >= 4:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A", "GCG_SKILL_TAG_E"),
                rule_id="action.double_geo_navia_navia_pressure",
            )
            if choice is not None:
                return choice
        return agent._switch_to_named_targets(
            context,
            target_names=("爱可菲", "黄金王兽"),
            rule_id="action.double_geo_navia_reset_chain",
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
    injured_exists = any(
        int(character.health) < int(character.max_health)
        for character in agent._player(context).characters
        if not character.defeated
    )
    if name == "不明流通渠道":
        return 5 if active_name == "娜维娅" else -3
    if name == "异兽侵蚀":
        return 5 if active_name == "黄金王兽" else -3
    if name in {"迪娜泽黛", "卯师傅", "太郎丸", "骑士团图书馆"}:
        return 4 if round_number <= 3 else 1
    if name == "元素共鸣：交织之岩":
        return 3 if active_name in {"娜维娅", "黄金王兽"} and dice_count <= 4 else 0
    if name == "元素共鸣：坚定之岩":
        return 4 if aura > 0 and active_name in {"娜维娅", "黄金王兽"} else -2
    if name == "为「死」而战":
        return 3 if aura > 0 and active_name in {"娜维娅", "黄金王兽"} else 0
    if name == "水与正义":
        return 2 if round_number <= 3 else 0
    if name == "海中寻宝":
        return 3 if round_number <= 3 else 1
    if name == "交给我吧！":
        return 3 if round_number <= 3 and active_name in {"爱可菲", "黄金王兽"} else -1
    if name == "纵声欢唱":
        return 2 if injured_exists else 0
    if name == "奇瑰之汤":
        return -2 if round_number <= 2 else 1
    return 0
