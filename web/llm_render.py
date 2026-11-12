"""Web-only prompt rendering and library filtering for the browser bridge.

This module deliberately lives outside ``skillloop``:

* the experiment pipeline (PvE stage runs, gate, replay) must keep its exact
  prompt format, otherwise a web-only tweak would silently change the measured
  library effect;
* the browser bridge has different needs — the model plays an ordinary match (no
  stage objectives), and the candidate list must explain what a skill or card
  actually does, because a bare name cannot distinguish a normal attack from an
  elemental skill.

Pieces:

1. ``WebStateRenderer`` — element names for auras, a web-specific system role,
   skill/card effect summaries, and a reaction preview per skill candidate. The
   parent renderer stays byte-identical for PvE.
2. ``WEB_TACTICS`` — browser-match tactics injected as the rules note.
3. ``filter_library_for_web`` — drops stage-specific / named PvE skills (and the
   mulligan strategy) that would otherwise inject advice like "起手换牌" or
   "五回合窗口" into a normal match.
"""

from __future__ import annotations

import re
from typing import Any

from reps.schema import OptionKind
from skillloop.library import SkillLibrary
from skillloop.render import (
    OUTPUT_CONTRACT,
    StateRenderer,
    _dice_symbol,
    _phase_label,
)

# Browser-match rules. Kept as one stable string (prefix caching) and phrased so
# it is useful with an empty library too (``llm:none``).
WEB_TACTICS = (
    "网页对局战术：\n"
    "- 普通攻击只造成物理伤害、不附着元素；元素战技/元素爆发会附着元素。\n"
    "- 先看对方出战角色的附着（状态行里的附着）：用合适元素的技能附着或触发反应"
    "（蒸发/融化/超载/感电/冻结/扩散/结晶/绽放等）通常优于无元素普通攻击。\n"
    "- 切人是为了让合适元素出场打反应或规避击倒；切人本身消耗一次行动，不要无意义换人。\n"
    "- 能量满且骰子够时优先考虑元素爆发；能击杀对方出战角色时优先击杀；"
    "不要为了铺场牺牲当轮伤害。\n"
    "- 候选编号只是标识，不代表优先级。\n"
    "- 「起手换牌」类技能只在起手换牌阶段参考，之后不要引用。"
)

# Web matches have no stage objectives; the parent role talks about PvE stages.
WEB_SYSTEM_ROLE = (
    "你是《七圣召唤》网页对局的助手，为「我方」逐决策选择动作；"
    "目标是赢下这局，只能从合法动作里选一个。"
)

_SKILL_TYPE_LABELS = {
    "GCG_SKILL_TAG_A": "普通攻击",
    "GCG_SKILL_TAG_E": "元素战技",
    "GCG_SKILL_TAG_Q": "元素爆发",
    "GCG_SKILL_TAG_PASSIVE": "被动",
}

# Aura ids used by the masked state snapshot.
_AURA_ELEMENTS = {1: "冰", 2: "水", 3: "火", 4: "雷", 5: "风", 6: "岩", 7: "草"}

# Symmetric reaction table. Anemo/geo are handled separately (they react with
# pyro/hydro/cryo/electro but never attach an aura themselves).
_REACTIONS = {
    frozenset(("火", "冰")): "融化",
    frozenset(("火", "水")): "蒸发",
    frozenset(("火", "雷")): "超载",
    frozenset(("水", "冰")): "冻结",
    frozenset(("水", "雷")): "感电",
    frozenset(("冰", "雷")): "超导",
    frozenset(("草", "水")): "绽放",
    frozenset(("草", "火")): "燃烧",
    frozenset(("草", "雷")): "原激化",
}
_REACTING_AURAS = frozenset(("火", "水", "冰", "雷"))
_ANEMO_GEO = frozenset(("风", "岩"))

_WHITESPACE = re.compile(r"\s+")
_DAMAGE = re.compile(
    r"造成\s*(\d+)\s*点\s*(?:(物理)伤害|([火水冰雷风岩草])元素伤害)"
)
_WEB_SKILL_PREFIX = "strat_"
_WEB_SKILL_EXCLUDED = frozenset(("strat_mulligan",))
_WEB_SKILL_EXCLUDED_PREFIXES = ("规则_目标_",)


def _one_line(text: Any, limit: int = 80) -> str:
    """Collapse whitespace (descriptions contain newlines) and truncate."""
    collapsed = _WHITESPACE.sub(" ", str(text or "")).strip()
    if len(collapsed) > limit:
        collapsed = collapsed[:limit].rstrip() + "…"
    return collapsed


def _aura_element(character: Any) -> str | None:
    if character is None:
        return None
    try:
        return _AURA_ELEMENTS.get(int(getattr(character, "aura", 0) or 0))
    except (TypeError, ValueError):
        return None


def _aura_label(character: Any) -> str:
    element = _aura_element(character)
    return f"附着:{element}" if element else "无附着"


def _parse_damage(description: str) -> tuple[str, int] | None:
    """``(element, amount)`` for "造成N点X元素伤害"; element is ``"物理"``.

    Returns ``None`` when the description has no such phrase (e.g. buffs,
    passives, "本角色附属…"), so callers never fabricate damage.
    """
    match = _DAMAGE.search(str(description or ""))
    if match is None:
        return None
    amount = int(match.group(1))
    if match.group(2) == "物理":
        return ("物理", amount)
    return (match.group(3), amount)


def is_web_skill(skill_id: str, layer: str) -> bool:
    """Whether a library skill belongs to the browser match.

    Rules apply everywhere; generic ``strat_*`` strategies are kept except the
    mulligan-only one. Named PvE skills (``skill_*``, ``tempo_budget_*``,
    ``frontload_*``) and per-objective ``规则_目标_*`` blocks are dropped.
    """
    skill_id = str(skill_id)
    if skill_id in _WEB_SKILL_EXCLUDED:
        return False
    if skill_id.startswith(_WEB_SKILL_EXCLUDED_PREFIXES):
        return False
    return layer == "rule" or skill_id.startswith(_WEB_SKILL_PREFIX)


def filter_library_for_web(library: SkillLibrary | None) -> SkillLibrary | None:
    """Drop stage-specific / mulligan skills; keep rules plus ``strat_*``.

    ``None`` and empty libraries are returned unchanged so callers can keep the
    "no library" path. Version/meta are preserved.
    """
    if library is None or not library.skills:
        return library
    kept = {
        skill_id: skill
        for skill_id, skill in library.skills.items()
        if is_web_skill(skill_id, skill.layer)
    }
    if len(kept) == len(library.skills):
        return library
    return SkillLibrary(
        skills=kept,
        version=library.version,
        meta=dict(library.meta),
    )


class WebStateRenderer(StateRenderer):
    """``StateRenderer`` with web-specific role, aura names and effect hints."""

    # -- assets ------------------------------------------------------------
    def _skill_info(self, definition_id: int) -> Any:
        try:
            return self.assets.skill(int(definition_id))
        except Exception:
            return None

    def _card_info(self, definition_id: int) -> Any:
        try:
            return self.assets.card(int(definition_id))
        except Exception:
            return None

    # -- state -------------------------------------------------------------
    def describe_state(self, context: Any) -> str:
        """Parent formatting, except auras are element names (not raw ids)."""
        state = context.player_view
        if state is None:
            return "（无状态）"
        me = state.players[context.acting_player]
        opp = state.players[1 - context.acting_player]

        def side(player: Any) -> str:
            return "，".join(
                f"{self._char(c.definition_id)} {c.health}/{c.max_health}"
                + (f" 能量{c.energy}/{c.max_energy}" if getattr(c, "max_energy", 0) else "")
                + f" {_aura_label(c)}"
                + ("★出战" if c.is_active else "")
                + ("✖" if c.defeated else "")
                for c in player.characters
            )

        dice = "".join(_dice_symbol(d) for d in me.dice) or "无"
        hand = "，".join(self._card(c.definition_id) for c in me.hand_cards) or "空"
        return "\n".join([
            f"第{state.round_number}轮 {_phase_label(state.phase)} 行动方={'我方' if state.current_turn == context.acting_player else '对方'}",
            f"我方：{side(me)}",
            f"我方骰子：{dice}（{len(me.dice)}个）",
            f"我方手牌：{hand}",
            f"{self.opponent_name}：{side(opp)}；手牌{len(opp.hand_cards)}张，骰子{len(opp.dice)}个",
            f"我方已宣布结束={bool(me.declared_end)}",
        ])

    # -- reaction hints ----------------------------------------------------
    def _opponent_active(self, context: Any) -> Any:
        state = context.player_view
        if state is None:
            return None
        opponent = state.players[1 - context.acting_player]
        return next(
            (
                character
                for character in opponent.characters
                if getattr(character, "is_active", False)
                and not getattr(character, "defeated", False)
            ),
            None,
        )

    def _damage_hint(self, context: Any, element: str | None) -> str:
        """One-line attachment/reaction preview; empty when unparseable."""
        if not element:
            return ""
        if element == "物理":
            return "物理伤害（不附着元素）"
        active = self._opponent_active(context)
        aura = _aura_element(active)
        if element in _ANEMO_GEO:
            if aura in _REACTING_AURAS:
                reaction = "扩散" if element == "风" else "结晶"
                return f"将对附着{aura}的出战角色触发{reaction}"
            return f"{element}元素伤害（不附着元素）"
        if not aura:
            return f"将附着{element}"
        reaction = _REACTIONS.get(frozenset((element, aura)))
        if reaction:
            return f"将对附着{aura}的出战角色触发{reaction}"
        if element == aura:
            return f"出战角色已附着{aura}（同元素，不触发新反应）"
        return f"将附着{element}（与当前{aura}附着不触发反应）"

    # -- candidates --------------------------------------------------------
    def describe_candidate(self, context: Any, spec: Any, index: int) -> str:
        line = super().describe_candidate(context, spec, index)
        kind = spec.kind
        extras: list[str] = []
        if kind == OptionKind.ACTION_USE_SKILL:
            info = self._skill_info(int(spec.subject_definition_id or 0))
            label = _SKILL_TYPE_LABELS.get(str(getattr(info, "skill_type", "")), "")
            if label:
                line = line.replace("使用技能：", f"{label}：", 1)
            description = getattr(info, "description", "") if info else ""
            summary = _one_line(description)
            if summary:
                extras.append(summary)
            damage = _parse_damage(description)
            if damage is not None:
                hint = self._damage_hint(context, damage[0])
                if hint:
                    extras.append(hint)
        elif kind == OptionKind.ACTION_PLAY_CARD:
            info = self._card_info(int(spec.subject_definition_id or 0))
            summary = _one_line(getattr(info, "description", "") if info else "")
            if summary:
                extras.append(summary)
        elif kind == OptionKind.ACTION_SWITCH_ACTIVE:
            # No damage is invented here; switching is just an action.
            extras.append("切换出战角色")
        if extras:
            line = f"{line}（{'；'.join(extras)}）"
        return line

    # -- prompt ------------------------------------------------------------
    @staticmethod
    def _task_line(task: Any) -> str:
        if getattr(task, "stage", "") == "web":
            return f"网页对局【{task.display}】用【{task.deck}】出战；目标：赢下这局。"
        return task.describe()

    def system_message(self, *, task: Any, skills: Any, rules_note: str = "") -> dict[str, str]:
        """Web role + merged rules + web task + skills + output contract.

        Mirrors ``StateRenderer.system_message`` ordering; only the role and the
        task line differ (the parent's role/task talk about PvE three-star goals).
        """
        parts = [WEB_SYSTEM_ROLE]
        if rules_note:
            parts.append(rules_note)
        parts.append(self._task_line(task))
        if skills:
            parts.append("可参考的历史经验技能（仅作参考，优先级低于赢下这局）：")
            parts.extend(skill.prompt_block() for skill in skills)
        parts.append(OUTPUT_CONTRACT)
        return {"role": "system", "content": "\n".join(parts)}
