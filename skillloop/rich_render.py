"""Shared experimental renderer with richer state / candidate explanations.

This is now the **default prompt layer** for every LLM group. ``--compact-render``
keeps the old minimal prompt only for reproducing historical runs.

Ported from the web-only ``WebStateRenderer`` but deliberately stripped of the
web-specific pieces: no browser tactics, no web system role, no library
filtering and no candidate re-ordering. Only presentation is enriched:

* auras are rendered as element names (``附着:火`` / ``无附着``);
* the hand line carries the ``N/10`` capacity;
* both sides' supports / summons / combat statuses and each character's
  attachments (weapons, artifacts, talents) are listed;
* the opponent's declared-end flag is shown;
* skills get their type label, an 80-character description summary and a
  damage attachment/reaction hint; cards get a description summary;
* ``knowledge_message`` puts the static per-game facts (our/opponent character
  skill sheets and the whole deck) into the cached system prefix, so the
  per-turn state only needs names and numbers.

``system_message`` and ``user_message`` stay inherited from ``StateRenderer``,
so the role, objective progress block and output contract are unchanged.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from reps.schema import OptionKind
from skillloop.render import StateRenderer, _dice_symbol, _phase_label

_SKILL_TYPE_LABELS = {
    "GCG_SKILL_TAG_A": "普通攻击",
    "GCG_SKILL_TAG_E": "元素战技",
    "GCG_SKILL_TAG_Q": "元素爆发",
    "GCG_SKILL_TAG_PASSIVE": "被动",
}

# Aura ids used by the masked state snapshot (0 = no aura).
_AURA_ELEMENTS = {1: "冰", 2: "水", 3: "火", 4: "雷", 5: "风", 6: "岩", 7: "草"}

# Symmetric reaction table. Anemo/geo react with the four "attachable" elements
# but never attach an aura themselves.
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
_REACTION_EFFECTS = {
    "融化": "本次伤害+2",
    "蒸发": "本次伤害+2",
    "超载": "本次伤害+2，并强制目标切换到下一角色",
    "超导": "本次伤害+1，并对目标以外的敌方角色各1点穿透伤害",
    "感电": "本次伤害+1，并对目标以外的敌方角色各1点穿透伤害",
    "冻结": "本次伤害+1，目标冻结（本回合不能使用技能；受火/物理伤害+2并解除）",
    "扩散": "对目标以外的敌方角色各1点被扩散元素伤害",
    "结晶": "本次伤害+1，获得1点护盾（最多2）",
    "燃烧": "本次伤害+1，生成燃烧烈焰（结束阶段1点火伤，可用1，最多叠到2）",
    "绽放": "本次伤害+1，生成草原核（我方对敌出战角色造成火/雷伤害+2，可用1）",
    "原激化": "本次伤害+1，生成激化领域（我方对敌出战角色造成雷/草伤害+1，可用3）",
}
_RULES_33 = """规则要点（3.3）：
- 回合流程：投掷8个元素骰（可重投1次）→ 双方轮流行动 → 都宣布结束后进入结束阶段（双方各抓2张牌）。
- 行动类型：战斗行动（使用技能/切换角色/宣布结束）后换对方行动；快速行动（打出手牌/元素调和）后可继续行动。
- 费用记法：N火/N水/N冰/N雷/N风/N岩/N草 = N个该元素骰；N任意 = N个任意元素骰；N同色 = N个相同元素的骰；N能量 = N点充能。万能元素可当任意元素。
- 切换角色花1任意骰；角色被击倒后免费选择新的出战角色。打出元素爆发需支付其充能。
- 充能：使用普通攻击或元素战技后+1；充能达到上限才能用元素爆发。
- 区域上限：支援区最多4张支援牌；召唤物区最多4个召唤物；手牌上限10张，回合结束各抓2张。
- 击倒：HP降到0并在结算后击倒，装备/状态弃置、充能清空；有“免于击倒”效果时不视为被击倒。
- 伤害：穿透伤害不受伤害加成，也不被护盾抵消；护盾先抵消伤害。
- 元素附着：冰/水/火/雷/草元素伤害会附着对应元素；风/岩元素伤害不附着。
- 元素反应（对已附着目标）：
  融化/蒸发：本次伤害+2。
  超载：本次伤害+2，并强制目标切换到下一角色。
  超导/感电：本次伤害+1，并对目标以外的敌方角色各1点穿透伤害。
  冻结：本次伤害+1，目标冻结（本回合不能使用技能；受到火或物理伤害+2并解除）。
  扩散：对目标以外的敌方角色各1点被扩散元素伤害。
  结晶：本次伤害+1，获得1点护盾（最多叠到2）。
  燃烧：本次伤害+1，生成燃烧烈焰（结束阶段造成1点火伤，可用1，最多叠到2）。
  绽放：本次伤害+1，生成草原核（我方对敌方出战角色造成火/雷伤害+2，可用1）。
  原激化：本次伤害+1，生成激化领域（我方对敌方出战角色造成雷/草伤害+1，可用3）。
- 胜负：击倒对方全部角色即胜利；第15回合开始仍未分胜负则双方判负。
- 资料使用：当前局面里的角色/装备/支援/召唤物/状态/手牌，其效果见本资料或局面行内说明。"""
_REACTING_AURAS = frozenset(("火", "水", "冰", "雷"))
_ANEMO_GEO = frozenset(("风", "岩"))

_WHITESPACE = re.compile(r"\s+")
_DAMAGE = re.compile(
    r"造成\s*(\d+)\s*点\s*(?:(物理)伤害|([火水冰雷风岩草])元素伤害)"
)

_DESCRIPTION_LIMIT = 80
_HAND_DESCRIPTION_LIMIT = 60

_ELEMENT_TAGS = {
    "GCG_TAG_ELEMENT_CRYO": "冰",
    "GCG_TAG_ELEMENT_HYDRO": "水",
    "GCG_TAG_ELEMENT_PYRO": "火",
    "GCG_TAG_ELEMENT_ELECTRO": "雷",
    "GCG_TAG_ELEMENT_ANEMO": "风",
    "GCG_TAG_ELEMENT_GEO": "岩",
    "GCG_TAG_ELEMENT_DENDRO": "草",
}
_WEAPON_TAGS = {
    "GCG_TAG_WEAPON_BOW": "弓",
    "GCG_TAG_WEAPON_CATALYST": "法器",
    "GCG_TAG_WEAPON_CLAYMORE": "双手剑",
    "GCG_TAG_WEAPON_SWORD": "单手剑",
    "GCG_TAG_WEAPON_POLEARM": "长柄武器",
}
_NPC_ELEMENTS = {
    "cryo": "冰", "hydro": "水", "pyro": "火", "electro": "雷",
    "anemo": "风", "geo": "岩", "dendro": "草", "physical": "物理",
}
_NPC_SKILL_KINDS = {"A": "普通攻击", "E": "元素战技", "Q": "元素爆发"}
_COST_NAMES = {
    "cryo": "冰", "hydro": "水", "pyro": "火", "electro": "雷",
    "anemo": "风", "geo": "岩", "dendro": "草",
    "unaligned": "任意", "void": "任意", "same": "同色",
    "energy": "能量", "legend": "秘传", "physical": "物理",
}

_COST_LABELS = {
    "GCG_COST_DICE_SAME": "同色",
    "GCG_COST_DICE_VOID": "任意",
    "GCG_COST_DICE_CRYO": "冰",
    "GCG_COST_DICE_HYDRO": "水",
    "GCG_COST_DICE_PYRO": "火",
    "GCG_COST_DICE_ELECTRO": "雷",
    "GCG_COST_DICE_ANEMO": "风",
    "GCG_COST_DICE_GEO": "岩",
    "GCG_COST_DICE_DENDRO": "草",
    "GCG_COST_ENERGY": "能量",
    "GCG_COST_LEGEND": "秘传",
}


def _one_line(text: Any, limit: int = _DESCRIPTION_LIMIT) -> str:
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

    Returns ``None`` when the description has no such phrase (buffs, passives,
    "本角色附属…"), so callers never fabricate damage.
    """
    match = _DAMAGE.search(str(description or ""))
    if match is None:
        return None
    amount = int(match.group(1))
    if match.group(2) == "物理":
        return ("物理", amount)
    return (match.group(3), amount)


def _opponent_active_character(context: Any) -> Any:
    """Opponent's active (not defeated) character from the masked player view."""
    state = getattr(context, "player_view", None)
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


def _reaction_name(element: str | None, aura: str | None) -> str | None:
    """Reaction name for an attacking element against an existing aura."""
    if not element or not aura or element == "物理":
        return None
    if element in _ANEMO_GEO:
        if aura not in _REACTING_AURAS:
            return None
        return "扩散" if element == "风" else "结晶"
    return _REACTIONS.get(frozenset((element, aura)))


class RichStateRenderer(StateRenderer):
    """``StateRenderer`` with element-name auras and skill/card effect hints."""

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

    # -- static knowledge (cached system prefix) ---------------------------
    def _skill_lines(self, character_id: int) -> list[str]:
        try:
            info = self.assets.character(int(character_id))
        except Exception:
            return []
        lines = []
        for skill_id in getattr(info, "skill_ids", ()) or ():
            skill = self._skill_info(int(skill_id))
            if skill is None:
                continue
            label = _SKILL_TYPE_LABELS.get(str(getattr(skill, "skill_type", "")), "")
            cost = self._cost(skill)
            description = _one_line(getattr(skill, "description", ""), 90)
            head = str(getattr(skill, "name", "") or f"技能#{skill_id}")
            if label or cost:
                head += f"（{label}{cost}）"
            lines.append(f"    - {head}" + (f"：{description}" if description else ""))
        return lines

    def _npc_enemy_map(self) -> dict[int, Any]:
        try:
            from npc.spec import load_enemies

            return {
                int(enemy.engine_id): enemy
                for enemy in load_enemies().values()
                if int(enemy.engine_id or 0)
            }
        except Exception:
            return {}

    def _npc_block(self, character_id: int) -> list[str]:
        """Enemy skill sheet from ``configs/npc`` (not in the official assets)."""
        enemy = self._npc_enemy_map().get(int(character_id))
        if enemy is None:
            return []
        element = _NPC_ELEMENTS.get(str(enemy.element), str(enemy.element))
        lines = [
            f"  - {enemy.name}[{element}] HP上限{enemy.hp}，能量上限{enemy.energy}"
        ]
        skills = [
            enemy.skills.get(kind) for kind in ("A", "E", "Q")
        ] + list(enemy.extra_skills)
        for skill in skills:
            if skill is None:
                continue
            label = _NPC_SKILL_KINDS.get(skill.kind, "")
            cost = self._npc_cost(skill.cost)
            detail = f"{label}{cost}"
            lines.append(
                f"    - {skill.name_cn or skill.name}"
                + (f"（{detail}）" if detail else "")
                + f"：{skill.effect}"
            )
        return lines

    def _character_block(self, character_id: int) -> list[str]:
        try:
            info = self.assets.character(int(character_id))
        except Exception:
            return []
        header = (
            f"  - {self._char(character_id)}{self._char_tags(character_id)}"
            f" HP上限{getattr(info, 'max_health', '?')}"
            f"，能量上限{getattr(info, 'max_energy', '?')}"
        )
        return [header, *self._skill_lines(character_id)]

    def knowledge_message(self, task: Any) -> str:
        """Full character skills + entire deck, fixed for the whole game."""
        ours = tuple(getattr(task, "characters", ()) or ())
        theirs = tuple(getattr(task, "opponent_characters", ()) or ())
        cards = tuple(getattr(task, "cards", ()) or ())
        if not (ours or theirs or cards):
            return ""
        lines = [_RULES_33, "", "对局资料（整局固定；当前局面只给名称/数值）："]
        if ours:
            lines.append("我方角色与技能：")
            for character_id in ours:
                lines.extend(self._character_block(character_id))
        if theirs:
            lines.append("对方角色与技能：")
            for character_id in theirs:
                block = self._character_block(character_id)
                lines.extend(block or self._npc_block(character_id))
        if cards:
            counts = Counter(cards)
            lines.append(f"我方牌组（{len(cards)}张）：")
            for card_id in dict.fromkeys(cards):
                info = self._card_info(int(card_id))
                name = self._card(int(card_id))
                cost = self._cost(info)
                description = _one_line(
                    getattr(info, "description", "") if info is not None else "",
                    _HAND_DESCRIPTION_LIMIT,
                )
                copies = f"×{counts[card_id]}" if counts[card_id] > 1 else ""
                lines.append(
                    f"  - {name}{cost}{copies}"
                    + (f"：{description}" if description else "")
                )
        return "\n".join(lines)

    # -- state -------------------------------------------------------------
    def _entity(self, snapshot: Any, *, detail: bool = False) -> str:
        """Name a support/summon/status/attachment (they share the card pool)."""
        definition_id = int(getattr(snapshot, "definition_id", 0) or 0)
        name = self._card(definition_id)
        variables = getattr(snapshot, "variables", None) or {}
        usage = variables.get("usage") or variables.get("usages")
        label = f"{name}({usage})" if usage not in (None, "", 0) else name
        if detail:
            info = self._card_info(definition_id)
            description = _one_line(
                getattr(info, "description", "") if info is not None else "",
                _HAND_DESCRIPTION_LIMIT,
            )
            if description:
                label += f"：{description}"
        return label

    def _entities(self, items: Any, *, detail: bool = False) -> str:
        names = [self._entity(item, detail=detail) for item in (items or ())]
        return "；".join(names) if names else "无"

    def _char_tags(self, definition_id: int) -> str:
        """``[雷·单手剑]`` so the model knows the element/weapon at a glance."""
        info = None
        try:
            info = self.assets.character(int(definition_id))
        except Exception:
            return ""
        tags = tuple(getattr(info, "tags", ()) or ())
        element = next((_ELEMENT_TAGS[t] for t in tags if t in _ELEMENT_TAGS), "")
        weapon = next((_WEAPON_TAGS[t] for t in tags if t in _WEAPON_TAGS), "")
        parts = [part for part in (element, weapon) if part]
        return f"[{'·'.join(parts)}]" if parts else ""

    def _cost(self, info: Any) -> str:
        costs = getattr(info, "play_cost", ()) if info is not None else ()
        parts = []
        for kind, count in costs or ():
            label = _COST_LABELS.get(str(kind), str(kind))
            parts.append(f"{int(count)}{label}")
        return f"［{'+'.join(parts)}］" if parts else ""

    def _npc_cost(self, raw: Any) -> str:
        """Normalise config strings like ``Cryo1+Unaligned2`` to ``1冰+2任意``."""
        text = str(raw or "").strip()
        if not text:
            return ""
        parts = re.findall(r"([A-Za-z]+)(\d+)", text)
        if not parts:
            return text
        labels = []
        for name, count in parts:
            label = _COST_NAMES.get(name.lower(), name)
            labels.append(f"{count}{label}")
        return "［" + "+".join(labels) + "］"

    def _hand(self, cards: Any) -> str:
        cards = tuple(cards or ())
        if not cards:
            return "空"
        lines = []
        for card in cards:
            definition_id = int(getattr(card, "definition_id", 0) or 0)
            info = self._card_info(definition_id)
            # Repeat the effect here as well: generated cards (e.g. 雷楔) are not
            # part of the static deck knowledge, and a hand card must never be
            # identifiable by name only.
            description = _one_line(
                getattr(info, "description", "") if info is not None else "",
                _HAND_DESCRIPTION_LIMIT,
            )
            suffix = f"：{description}" if description else ""
            lines.append(
                f"  - {self._card(definition_id)}{self._cost(info)}{suffix}"
            )
        return "\n" + "\n".join(lines)

    def _attachments(self, character: Any) -> str:
        names = [self._entity(item) for item in (getattr(character, "entities", ()) or ())]
        return f"（装备:{'、'.join(names)}）" if names else ""

    def describe_state(self, context: Any) -> str:
        """Parent formatting, plus auras as elements and every public zone."""
        state = context.player_view
        if state is None:
            return "（无状态）"
        me = state.players[context.acting_player]
        opp = state.players[1 - context.acting_player]

        def side(player: Any) -> str:
            return "，".join(
                f"{self._char(c.definition_id)}{self._char_tags(c.definition_id)} "
                f"{c.health}/{c.max_health}"
                + (f" 能量{c.energy}/{c.max_energy}" if getattr(c, "max_energy", 0) else "")
                + f" {_aura_label(c)}"
                + self._attachments(c)
                + ("★出战" if c.is_active else "")
                + ("✖" if c.defeated else "")
                for c in player.characters
            )

        dice = "".join(_dice_symbol(d) for d in me.dice) or "无"
        hand = self._hand(me.hand_cards)
        return "\n".join([
            f"第{state.round_number}轮 {_phase_label(state.phase)} 行动方={'我方' if state.current_turn == context.acting_player else '对方'}",
            f"我方：{side(me)}",
            f"我方骰子：{dice}（{len(me.dice)}个）",
            f"我方手牌（{len(me.hand_cards)}/10）：{hand}",
            f"我方支援：{self._entities(getattr(me, 'supports', ()), detail=True)}",
            f"我方召唤物：{self._entities(getattr(me, 'summons', ()), detail=True)}",
            f"我方状态：{self._entities(getattr(me, 'combat_statuses', ()), detail=True)}",
            f"{self.opponent_name}：{side(opp)}；手牌{len(opp.hand_cards)}张，骰子{len(opp.dice)}个",
            f"{self.opponent_name}支援：{self._entities(getattr(opp, 'supports', ()), detail=True)}",
            f"{self.opponent_name}召唤物：{self._entities(getattr(opp, 'summons', ()), detail=True)}",
            f"{self.opponent_name}状态：{self._entities(getattr(opp, 'combat_statuses', ()), detail=True)}",
            f"我方已宣布结束={bool(me.declared_end)}",
            f"对方已宣布结束={bool(opp.declared_end)}",
        ])

    # -- reaction hints ----------------------------------------------------
    def _damage_hint(self, context: Any, element: str | None) -> str:
        """One-line attachment/reaction preview; empty when unparseable."""
        if not element:
            return ""
        if element == "物理":
            return "物理伤害（不附着元素）"
        active = _opponent_active_character(context)
        aura = _aura_element(active)
        if element in _ANEMO_GEO:
            reaction = _reaction_name(element, aura)
            if reaction:
                return (
                    f"将对附着{aura}的出战角色触发{reaction}"
                    f"（{_REACTION_EFFECTS.get(reaction, '')}）"
                )
            return f"{element}元素伤害（不附着元素）"
        if not aura:
            return f"将附着{element}"
        reaction = _reaction_name(element, aura)
        if reaction:
            return (
                f"将对附着{aura}的出战角色触发{reaction}"
                f"（{_REACTION_EFFECTS.get(reaction, '')}）"
            )
        if element == aura:
            return f"出战角色已附着{aura}（同元素，不触发新反应）"
        return f"将附着{element}（与当前{aura}附着不触发反应）"

    # -- candidates --------------------------------------------------------
    def describe_candidate(self, context: Any, spec: Any, index: int) -> str:
        line = super().describe_candidate(context, spec, index)
        kind = spec.kind
        used_dice = tuple(getattr(spec, "used_dice", ()) or ())
        if used_dice and kind != OptionKind.ACTION_ELEMENTAL_TUNING:
            counts = Counter(_dice_symbol(int(die)) for die in used_dice)
            grouped = "+".join(f"{count}{symbol}" for symbol, count in counts.items())
            cut = line.rfind(" 花费")
            if cut >= 0:
                line = line[:cut] + f" 花费［{grouped}］"
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
