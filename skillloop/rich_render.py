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
* skills get their type label, a 120-character description summary and a
  damage attachment/reaction hint; cards get a description summary;
* a candidate whose ``metadata["preview"]`` carries the engine's own settlement can
  end with ``；预计：…`` (target, settled damage, reaction, defeat, heal), which
  outranks the static card text and replaces the static reaction hint;
* ``knowledge_message`` puts the static per-game facts (our/opponent character
  skill sheets and the whole deck) into the cached system prefix, so the
  per-turn state only needs names and numbers. That reference copy prints the
  **full** description (only a 400-character safety cap), because a card's cost
  and effect must be reproducible from the prompt alone; the per-decision user
  message keeps a 120-character cap as a cost guard.

``system_message`` is overridden here (2026-11 refactor) to the fixed layout:
role → game rules → stage special rules (when the stage has any) → card knowledge
(+ glossary) → objectives → candidate-forecast precedence note → learned skills →
output contract. ``user_message`` stays inherited, so the per-decision progress
block, board and candidate list are unchanged.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from reps.schema import OptionKind
from skillloop.passive_skills import passives_for
from skillloop.prompt_v2 import (
    CARD_INFO_HEADER,
    GAME_RULES_V2,
    PREVIEW_PRECEDENCE_NOTE,
    SKILLS_HEADER,
    STAGE_RULES_HEADER,
    glossary_section,
    objective_message,
    stage_special_rules_text,
)
from skillloop.render import (
    OUTPUT_CONTRACT,
    SYSTEM_ROLE,
    StateRenderer,
    _dice_symbol,
    _phase_label,
    opponent_status_label,
)

_SKILL_TYPE_LABELS = {
    "GCG_SKILL_TAG_A": "普通攻击",
    "GCG_SKILL_TAG_E": "元素战技",
    "GCG_SKILL_TAG_Q": "元素爆发",
    "GCG_SKILL_TAG_PASSIVE": "被动",
}

# Aura ids used by the masked state snapshot (0 = no aura).
_AURA_ELEMENTS = {1: "冰", 2: "水", 3: "火", 4: "雷", 5: "风", 6: "岩", 7: "草", 113: "冰草"}

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
    "超载": "本次伤害+2；若目标是出战角色，强制切换到下一角色",
    "超导": "本次伤害+1，并对目标以外的敌方角色各1点穿透伤害",
    "感电": "本次伤害+1，并对目标以外的敌方角色各1点穿透伤害",
    "冻结": "本次伤害+1，目标冻结（本回合不能使用技能；受火/物理伤害+2并解除）",
    "扩散": "对目标以外的敌方角色各1点目标原附着元素伤害",
    "结晶": "本次伤害+1，获得1点护盾（最多2）",
    "燃烧": "本次伤害+1，生成燃烧烈焰（结束阶段1点火伤，可用1，最多叠到2）",
    "绽放": "本次伤害+1，生成草原核（我方对敌出战角色造成火/雷伤害+2，可用1）",
    "原激化": "本次伤害+1，生成激化领域（我方对敌出战角色造成雷/草伤害+1，可用2）",
}
_REACTING_AURAS = frozenset(("火", "水", "冰", "雷"))
_ANEMO_GEO = frozenset(("风", "岩"))

# Engine ``DamageEM.damage_type`` (build/engine/proto/enums.proto). 8 is piercing,
# 9 is HEAL (handled separately: it is not an element).
_PREVIEW_DAMAGE_TYPES = {
    0: "物理",
    1: "冰",
    2: "水",
    3: "火",
    4: "雷",
    5: "风",
    6: "岩",
    7: "草",
    8: "穿透",
}
_PREVIEW_HEAL_TYPE = 9
# ``Action.preview`` reuses its own reaction numbering (101+, as the engine writes it
# into ``DamageEM.reaction_type``); it is *not* the 1..17 ``ReactionType`` enum used
# by the mutation log, so this table is separate from ``_REACTIONS`` above.
_PREVIEW_REACTIONS = {
    101: "融化",
    102: "蒸发",
    103: "超载",
    104: "超导",
    105: "感电",
    106: "冻结",
    107: "扩散·冰",
    108: "扩散·水",
    109: "扩散·火",
    110: "扩散·雷",
    111: "结晶·冰",
    112: "结晶·水",
    113: "结晶·火",
    114: "结晶·雷",
    115: "燃烧",
    116: "绽放",
    117: "原激化",
}
_PREVIEW_MAX_LINES = 6
_NO_ELEMENT = frozenset(("物理", "穿透"))

_WHITESPACE = re.compile(r"\s+")
_DAMAGE = re.compile(
    r"造成\s*(\d+)\s*点\s*(?:(物理)伤害|([火水冰雷风岩草])元素伤害)"
)
# Talent cards: "角色装备此牌后，立刻使用一次星斗归位。" → capture the skill name.
_TALENT_USE = re.compile(r"立刻使用一次\s*[「『]?([^」，。；、\n）)]+)")

# Card/skill text length caps. The system prefix is the *reference* copy of a card, so
# it prints the full description and only keeps a generous safety cap for anomalous
# data; the per-decision user message is a cost guard, so its card/skill summaries keep
# a (raised) cap and end with "…" when cut.
_KNOWLEDGE_LIMIT = 400
_USER_DESCRIPTION_LIMIT = 120
_DESCRIPTION_LIMIT = 120

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

# Engine ``DiceRequirementType`` → prompt label (build/engine/proto/enums.proto).
# 0 is VOID (任意), *not* "unspecified": a normal attack really is 1火+2任意.
_REQUIREMENT_LABELS = {
    0: "任意",
    1: "冰", 2: "水", 3: "火", 4: "雷", 5: "风", 6: "岩", 7: "草",
    8: "同色", 9: "能量", 10: "秘传",
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
    """Collapse whitespace (descriptions contain newlines) and cap at ``limit``.

    Callers in the system prefix pass ``_KNOWLEDGE_LIMIT`` (full text), callers in
    the user message pass ``_USER_DESCRIPTION_LIMIT``; a cut always ends with "…".
    """
    collapsed = _WHITESPACE.sub(" ", str(text or "")).strip()
    if len(collapsed) > limit:
        # The ellipsis counts towards the cap, so a cut line is never limit+1 long.
        collapsed = collapsed[: limit - 1].rstrip() + "…"
    return collapsed


def _no_period(text: str) -> str:
    """Drop one trailing "。" so "描述。；提示" never appears in a candidate line."""
    return text[:-1] if text.endswith("。") else text


def _format_cost(items: Any) -> str:
    """``[(label, count), ...]`` → ``［3火+2能量］`` (empty list → ``""``)."""
    parts = [f"{int(count)}{label}" for label, count in items]
    return f"［{'+'.join(parts)}］" if parts else ""


def _cost_signature(items: Any) -> tuple[tuple[str, int], ...]:
    """Order-insensitive comparison key for two cost breakdowns.

    ``万`` is the engine's universal die and ``任意`` is how the asset dump spells
    the same requirement, so both collapse to one label; otherwise an unchanged
    cost would look "different" purely because of vocabulary.
    """
    return tuple(
        sorted(
            ("任意" if str(label) == "万" else str(label), int(count))
            for label, count in items
        )
    )


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

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Play-card descriptions are printed once per render pass (M12): the first
        # target variant carries the effect, later variants only add "→ 目标".
        self._seen_card_descriptions: set[int] = set()

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
            if str(getattr(skill, "skill_type", "")) == "GCG_SKILL_TAG_PASSIVE":
                continue  # rendered from the GTS passive sheet below
            label = _SKILL_TYPE_LABELS.get(str(getattr(skill, "skill_type", "")), "")
            cost = self._cost(skill)
            description = _one_line(getattr(skill, "description", ""), _KNOWLEDGE_LIMIT)
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
            f"  - {enemy.name}[{element}] 生命值上限{enemy.hp}，能量上限{enemy.energy}"
        ]
        skills = [
            enemy.skills.get(kind) for kind in ("A", "E", "Q")
        ] + list(enemy.extra_skills)
        names = set()
        for skill in skills:
            if skill is None:
                continue
            label = _NPC_SKILL_KINDS.get(skill.kind, "")
            cost = self._npc_cost(skill.cost)
            detail = f"{label}{cost}"
            name = skill.name_cn or skill.name
            names.add(str(name))
            lines.append(
                f"    - {name}"
                + (f"（{detail}）" if detail else "")
                + f"：{skill.effect}"
            )
        for line in self._passive_lines(character_id):
            name = line.strip().split("（被动）", 1)[0].lstrip("- ")
            if name not in names:
                lines.append(line)
        return lines

    def _passive_lines(self, character_id: int) -> list[str]:
        lines = []
        seen = set()
        for passive in passives_for(character_id):
            description = _one_line(
                str(passive.get("description") or "").replace("【被动】", ""),
                _KNOWLEDGE_LIMIT,
            )
            key = description.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            name = str(passive.get("name") or "被动")
            lines.append(f"    - {name}（被动）：{description}")
        return lines

    def _character_block(self, character_id: int) -> list[str]:
        try:
            info = self.assets.character(int(character_id))
        except Exception:
            return []
        header = (
            f"  - {self._char(character_id)}{self._char_tags(character_id)}"
            f" 生命值上限{getattr(info, 'max_health', '?')}"
            f"，能量上限{getattr(info, 'max_energy', '?')}"
        )
        return [header, *self._skill_lines(character_id), *self._passive_lines(character_id)]

    def knowledge_message(self, task: Any) -> str:
        """Segment 3: full character skills + entire deck, plus the glossary.

        Fixed for the whole game, so it belongs in the cached system prefix. The
        ``名词解释`` block is appended last: it only explains names that the lines
        above actually use, and is omitted when nothing matches.
        """
        blocks = [*self._character_blocks(task), *self._deck_block(task)]
        if not blocks:
            return ""
        body = "\n".join([CARD_INFO_HEADER, *blocks])
        glossary = glossary_section(body)
        return f"{body}\n{glossary}" if glossary else body

    def _character_blocks(self, task: Any) -> list[str]:
        """Our sheet first, then the opponent's (NPC overlays fall back to configs)."""
        lines: list[str] = []
        ours = tuple(getattr(task, "characters", ()) or ())
        theirs = tuple(getattr(task, "opponent_characters", ()) or ())
        if ours:
            lines.append("我方角色与技能：")
            for character_id in ours:
                lines.extend(self._character_block(character_id))
        if theirs:
            lines.append("对方角色与技能：")
            for character_id in theirs:
                block = self._character_block(character_id)
                lines.extend(block or self._npc_block(character_id))
        return lines

    def _deck_block(self, task: Any) -> list[str]:
        cards = tuple(getattr(task, "cards", ()) or ())
        if not cards:
            return []
        counts = Counter(cards)
        lines = [f"我方牌组（{len(cards)}张）："]
        for card_id in dict.fromkeys(cards):
            info = self._card_info(int(card_id))
            name = self._card(int(card_id))
            cost = self._cost(info)
            description = _one_line(
                getattr(info, "description", "") if info is not None else "",
                _KNOWLEDGE_LIMIT,
            )
            copies = f"×{counts[card_id]}" if counts[card_id] > 1 else ""
            lines.append(
                f"  - {name}{cost}{copies}"
                + (f"：{description}" if description else "")
            )
        return lines

    # -- prompt ------------------------------------------------------------
    def system_parts(self, *, task: Any, skills: Any) -> list[tuple[str, str]]:
        """Labelled segments of the system message, in prompt order.

        Single source for both the rendered prompt and the per-segment sizes in
        reports, so a report can never claim a layout the prompt does not have.
        """
        parts: list[tuple[str, str]] = [
            ("SYSTEM_ROLE", SYSTEM_ROLE),
            ("游戏规则（GAME_RULES_V2）", GAME_RULES_V2),
        ]
        special_rules = stage_special_rules_text(str(getattr(task, "stage", "") or ""))
        if special_rules:
            parts.append(
                ("关卡特殊规则", f"{STAGE_RULES_HEADER}\n{special_rules}")
            )
        knowledge = self.knowledge_message(task)
        if knowledge:
            parts.append(("卡组信息 + 名词解释", knowledge))
        parts.append(("获胜目标 + 目标判定", objective_message(task)))
        # One fixed line, before the (optional) skill block: the engine forecast in
        # a candidate line already includes infusion/equipment/shield/reaction, so it
        # outranks the base numbers printed in a skill's own text.
        parts.append(("候选「预计」优先级", PREVIEW_PRECEDENCE_NOTE))
        if skills:
            parts.append(
                (
                    "学习得来的技能",
                    "\n".join(
                        [SKILLS_HEADER, *(skill.prompt_block() for skill in skills)]
                    ),
                )
            )
        parts.append(("OUTPUT_CONTRACT", OUTPUT_CONTRACT))
        return parts

    def system_message(
        self,
        *,
        task: Any,
        skills: Any,
        rules_note: str = "",
    ) -> dict[str, str]:
        """The fixed six-part system message (role → rules → knowledge → goals → skills).

        ``rules_note`` is accepted but **ignored**: the always-on rules now come from
        ``prompt_v2.GAME_RULES_V2`` instead of the library's 17 ``rule_*`` skills.
        Those skills stay in ``data/skills/*`` (the paired gate and historical
        runs need them) but no longer appear as prompt lines, so keeping the
        parameter only avoids touching every caller.
        """
        content = "\n".join(text for _name, text in self.system_parts(task=task, skills=skills))
        return {"role": "system", "content": content}

    # -- state -------------------------------------------------------------
    def _entity(self, snapshot: Any, *, detail: bool = False) -> str:
        """Name a support/summon/status/attachment (they share the card pool).

        Counters carry their unit so a bare number cannot be mistaken for damage:
        ``usage``/``usages`` → ``(N次)``, a ``duration`` variable → ``(N回合)``;
        other numeric variables (e.g. stacks/layers) stay a bare ``(N)``.
        """
        definition_id = int(getattr(snapshot, "definition_id", 0) or 0)
        name = self._card(definition_id)
        variables = getattr(snapshot, "variables", None) or {}
        usage = variables.get("usage")
        if usage in (None, "", 0):
            usage = variables.get("usages")
        duration = variables.get("duration")
        if duration in (None, "", 0):
            duration = getattr(snapshot, "duration", None)
        if usage not in (None, "", 0):
            label = f"{name}({int(usage)}次)"
        elif duration not in (None, "", 0):
            label = f"{name}({int(duration)}回合)"
        else:
            numeric = next(
                (
                    int(value)
                    for key, value in sorted(variables.items())
                    if isinstance(value, int) and value not in (0,)
                ),
                None,
            )
            label = f"{name}({numeric})" if numeric is not None else name
        if detail:
            info = self._card_info(definition_id)
            description = _one_line(
                getattr(info, "description", "") if info is not None else "",
                _USER_DESCRIPTION_LIMIT,
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

    def _cost_items(self, info: Any) -> list[tuple[str, int]]:
        """Asset ``play_cost`` as ``[(label, count), ...]`` (includes 能量 etc.)."""
        costs = getattr(info, "play_cost", ()) if info is not None else ()
        return [
            (_COST_LABELS.get(str(kind), str(kind)), int(count))
            for kind, count in (costs or ())
        ]

    def _cost(self, info: Any) -> str:
        return _format_cost(self._cost_items(info))

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
                _USER_DESCRIPTION_LIMIT,
            )
            suffix = f"：{description}" if description else ""
            lines.append(
                f"  - {self._card(definition_id)}{self._cost(info)}{suffix}"
            )
        return "\n" + "\n".join(lines)

    def _attachments(self, character: Any) -> str:
        equipment: list[str] = []
        statuses: list[str] = []
        equipment_tags = {"GCG_TAG_WEAPON", "GCG_TAG_ARTIFACT", "GCG_TAG_TALENT"}
        for item in (getattr(character, "entities", ()) or ()):
            info = self._card_info(int(getattr(item, "definition_id", 0) or 0))
            tags = set(getattr(info, "tags", ()) or ())
            label = self._entity(item)
            (equipment if tags & equipment_tags else statuses).append(label)
        parts = []
        if equipment:
            parts.append(f"装备:{'、'.join(equipment)}")
        if statuses:
            parts.append(f"状态:{'、'.join(statuses)}")
        return f"（{'；'.join(parts)}）" if parts else ""

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
                + (
                    "（可重击）"
                    if c.is_active and bool(getattr(player, "can_charged", False))
                    else ""
                )
                + (
                    "（可下落攻击）"
                    if c.is_active and bool(getattr(player, "can_plunging", False))
                    else ""
                )
                for c in player.characters
            )

        dice = "".join(_dice_symbol(d) for d in me.dice) or "无"
        hand = self._hand(me.hand_cards)
        # Before the first roll (mulligan / choose-active) there are no dice at all, so
        # "无（0个）" would read like a spent pool. Both sides say "尚未投掷".
        before_roll = (
            int(getattr(state, "round_number", 0) or 0) == 0
            or str(getattr(state, "phase", "")) == "init_hands"
        )
        my_dice_text = "尚未投掷" if before_roll else f"{dice}（{len(me.dice)}个）"
        # The engine masks our *view* of the opponent's dice (``players[1].dice == ()``),
        # but the dice themselves are public, so the unmasked ``full_state`` is read
        # first. Nothing else of the opponent is taken from it: their hand stays a
        # count from ``player_view``, so hidden information cannot leak into the prompt.
        opp_dice = tuple(getattr(opp, "dice", ()) or ())
        full_state = getattr(context, "full_state", None)
        if full_state is not None:
            try:
                unmasked = full_state.players[1 - context.acting_player]
            except (AttributeError, IndexError, TypeError):
                unmasked = None
            if unmasked is not None:
                real_dice = tuple(getattr(unmasked, "dice", ()) or ())
                if real_dice:
                    opp_dice = real_dice
        if before_roll:
            opp_dice_text = "尚未投掷"
        elif opp_dice:
            symbols = "".join(_dice_symbol(d) for d in opp_dice)
            opp_dice_text = f"{symbols}（{len(opp_dice)}个）"
        else:
            # Not "hidden by the rules": the renderer simply has no unmasked source.
            opp_dice_text = "引擎视图未提供"
        # Remaining deck counts are public on both sides (the masked view keeps the
        # list length while hiding each opponent card's definition_id).
        me_pile = len(getattr(me, "pile_cards", ()) or ())
        opp_pile = len(getattr(opp, "pile_cards", ()) or ())
        return "\n".join([
            f"第{state.round_number}轮 {_phase_label(state.phase)} 行动方={'我方' if state.current_turn == context.acting_player else '对方'}",
            f"我方：{side(me)}",
            f"我方骰子：{my_dice_text}",
            f"我方手牌（{len(me.hand_cards)}/10）：{hand}",
            f"我方牌堆：{me_pile} 张",
            f"我方支援：{self._entities(getattr(me, 'supports', ()), detail=True)}",
            f"我方召唤物：{self._entities(getattr(me, 'summons', ()), detail=True)}",
            f"我方状态：{self._entities(getattr(me, 'combat_statuses', ()), detail=True)}",
            f"{opponent_status_label(self.opponent_name)}：{side(opp)}；手牌{len(opp.hand_cards)}张，牌堆 {opp_pile} 张",
            # Own line, symmetric with "我方骰子：": the opponent's dice are public,
            # and burying them mid-sentence hid the types behind a count.
            f"对方骰子：{opp_dice_text}",
            f"对方支援：{self._entities(getattr(opp, 'supports', ()), detail=True)}",
            f"对方召唤物：{self._entities(getattr(opp, 'summons', ()), detail=True)}",
            f"对方状态：{self._entities(getattr(opp, 'combat_statuses', ()), detail=True)}",
            f"我方已宣布结束={'是' if bool(me.declared_end) else '否'}",
            f"对方已宣布结束={'是' if bool(opp.declared_end) else '否'}",
        ])

    # -- reaction hints ----------------------------------------------------
    def _damage_hint(self, context: Any, element: str | None) -> str:
        """One-line attachment/reaction preview; empty when there is nothing to say.

        Wording avoids engine-log phrasing ("将附着火") so it reads as advice next to
        the skill's own description; the two are joined with "；".
        """
        if not element:
            return ""
        active = _opponent_active_character(context)
        aura = _aura_element(active)
        if element == "物理":
            # Physical damage never attaches an element: only explain the (missing)
            # reaction when the target actually carries an aura.
            return "物理伤害不触发元素反应" if aura else ""
        if aura == "冰草":
            return (
                f"目标附着冰+草：{element}元素将按冰或草相关反应结算（"
                "冰系触发融化/冻结/超导/扩散/结晶，草系触发绽放/燃烧/原激化）"
            )
        if element in _ANEMO_GEO:
            reaction = _reaction_name(element, aura)
            if reaction:
                return (
                    f"将对附着{aura}的出战角色触发{reaction}"
                    f"（{_REACTION_EFFECTS.get(reaction, '')}）"
                )
            return f"{element}元素伤害不附着元素"
        if not aura:
            return f"命中后为目标附着{element}元素"
        reaction = _reaction_name(element, aura)
        if reaction:
            return (
                f"将对附着{aura}的出战角色触发{reaction}"
                f"（{_REACTION_EFFECTS.get(reaction, '')}）"
            )
        if element == aura:
            return f"目标已附着{aura}，不触发新反应"
        return f"命中后为目标附着{element}元素（与当前{aura}附着不触发反应）"

    # -- candidates --------------------------------------------------------
    def _cost_subject(self, spec: Any) -> Any:
        """Asset entry whose ``play_cost`` is the *requirement* for this action.

        Switching actions have no asset entry (the 1-die switch cost is a game
        rule, not a card), so they return ``None`` and fall back to payment-only
        rendering.
        """
        if spec.kind == OptionKind.ACTION_USE_SKILL:
            return self._skill_info(int(spec.subject_definition_id or 0))
        if spec.kind == OptionKind.ACTION_PLAY_CARD:
            return self._card_info(int(spec.subject_definition_id or 0))
        return None

    def _requirement_items(self, spec: Any) -> list[tuple[str, int]]:
        """**Effective** cost requirement, as ``[(label, count), ...]``.

        ``metadata["required_cost"]`` is written by ``reps.action_adapter`` from the
        engine's ``action.required_cost``, i.e. *after* discounts. The asset
        ``play_cost`` is the undiscounted list price and could not explain why the
        same card showed ``需求 1火+2任意`` while the payment was 2 or 3 dice.

        Requirement types follow ``build/engine/proto/enums.proto``: 0=Void/任意,
        1–7=ice/hydro/pyro/electro/anemo/geo/dendro, 8=aligned/同色, 9=energy,
        10=legend. Entries with ``count <= 0`` are dropped, so a free card (or
        ``ACTION_DECLARE_END``, whose requirement is ``(8, 0)``) prints no 需求.
        Historical recordings without metadata fall back to the asset ``play_cost``.
        """
        metadata = getattr(spec, "metadata", None)
        if isinstance(metadata, dict) and "required_cost" in metadata:
            items: list[tuple[str, int]] = []
            for entry in metadata.get("required_cost") or ():
                try:
                    type_id, count = int(entry[0]), int(entry[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if count <= 0:
                    continue
                label = _REQUIREMENT_LABELS.get(type_id)
                if label is None:
                    continue
                items.append((label, count))
            return items
        info = self._cost_subject(spec)
        return self._cost_items(info) if info is not None else []

    # -- engine preview ----------------------------------------------------
    def _character_names(self, context: Any) -> dict[int, str]:
        """``instance id -> character name`` for both sides.

        ``Action.preview`` targets are *instance* ids (negative for the opponent),
        so the name of a target can only be resolved through the live state; the
        definition id is the fallback for a stale or empty view.
        """
        names: dict[int, str] = {}
        state = getattr(context, "player_view", None)
        if state is None:
            return names
        for player in getattr(state, "players", ()) or ():
            for character in getattr(player, "characters", ()) or ():
                try:
                    names[int(character.id)] = self._char(character.definition_id)
                except (AttributeError, TypeError, ValueError):
                    continue
        return names

    def _active_character_name(self, context: Any) -> str:
        state = getattr(context, "player_view", None)
        if state is None:
            return ""
        try:
            characters = state.players[context.acting_player].characters
        except (AttributeError, IndexError, TypeError):
            return ""
        for character in characters:
            if getattr(character, "is_active", False) and not getattr(character, "defeated", False):
                return self._char(character.definition_id)
        return ""

    def _preview_target_name(self, entry: dict[str, Any], names: dict[int, str]) -> str:
        try:
            target_id = int(entry.get("target_id", 0) or 0)
        except (TypeError, ValueError):
            target_id = 0
        name = names.get(target_id)
        if name:
            return name
        try:
            definition_id = int(entry.get("target_definition_id", 0) or 0)
        except (TypeError, ValueError):
            definition_id = 0
        if definition_id > 0:
            resolved = self._char(definition_id)
            if resolved and not str(resolved).startswith("角色#"):
                return resolved
        return "目标"

    def _preview_entry_text(self, entry: dict[str, Any], names: dict[int, str]) -> str:
        """One ``Action.preview`` entry as prompt text (``""`` = nothing to say)."""
        if str(entry.get("kind") or "") != "damage":
            # An aura attachment is already reflected in the damage entry's reaction
            # name; printing it again would repeat the same reaction twice.
            return ""
        try:
            value = int(entry.get("value", 0) or 0)
            damage_type = int(entry.get("damage_type", 0) or 0)
            heal_kind = int(entry.get("heal_kind", 0) or 0)
            reaction = int(entry.get("reaction_type", 0) or 0)
            old_health = int(entry.get("old_health", 0) or 0)
            new_health = int(entry.get("new_health", 0) or 0)
            defeated = bool(entry.get("cause_defeated", False))
        except (TypeError, ValueError):
            return ""
        target = self._preview_target_name(entry, names)
        if damage_type == _PREVIEW_HEAL_TYPE or heal_kind:
            # A heal entry on a full-HP character carries value=0 and changes nothing,
            # so it is dropped instead of printing "治疗0".
            return f"对{target}治疗{value}" if value > 0 else ""
        element = _PREVIEW_DAMAGE_TYPES.get(damage_type)
        if element is None:
            return ""
        element_text = element if element in _NO_ELEMENT else f"{element}元素"
        text = f"对{target}造成{value}点{element_text}伤害"
        reaction_name = _PREVIEW_REACTIONS.get(reaction) if element not in _NO_ELEMENT else None
        if reaction_name:
            text += f"（{reaction_name}）"
        if defeated:
            text += "，击倒"
        if old_health != new_health and (old_health - new_health) != value:
            # The engine settled shields / damage reduction already, so the health
            # delta disagrees with the nominal value: show both.
            text += f"（HP {old_health}→{new_health}）"
        return text

    def _preview_parts(self, context: Any, spec: Any) -> list[str]:
        """``预计：…`` segments from ``metadata["preview"]`` (empty for old data)."""
        metadata = getattr(spec, "metadata", None)
        if not isinstance(metadata, dict):
            return []
        entries = metadata.get("preview")
        if not isinstance(entries, (list, tuple)) or not entries:
            return []
        names = self._character_names(context)
        parts: list[str] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            text = self._preview_entry_text(entry, names)
            if text:
                parts.append(text)
        if len(parts) > _PREVIEW_MAX_LINES:
            return [f"{'；'.join(parts[:_PREVIEW_MAX_LINES])}等{len(parts)}项"]
        return parts

    def _switch_line(self, context: Any, spec: Any, index: int, line: str) -> str:
        """``切换出战：X → 我方·X`` → ``切换出战：当前·Y → X``.

        The target-only wording repeated itself; the current active character is the
        half of the change the model cannot read off the candidate list.
        """
        try:
            definition_id = int(getattr(spec, "subject_definition_id", 0) or 0)
        except (TypeError, ValueError):
            return line
        if definition_id <= 0:
            return line
        target = self._char(definition_id)
        current = self._active_character_name(context)
        if not current or current == target:
            return line
        head = f"[{index}] 切换出战：{target}"
        if not line.startswith(head):
            return line
        rest = line[len(head):]
        marker = f" → 我方·{target}"
        if rest.startswith(marker):
            rest = rest[len(marker):]
        return f"[{index}] 切换出战：当前·{current} → {target}{rest}"

    def _cost_line(self, line: str, spec: Any) -> str:
        """Replace the base renderer's raw ``花费`` with 需求 (effective) + 支付.

        ``used_dice`` is what the engine auto-selected to pay with, not what the
        skill/card *requires*: a burst with ``需求［3火+2能量］`` was rendered as
        ``花费［1火+2万］``, hiding the energy requirement. The requirement is the
        engine's effective ``required_cost`` (discounts included); the payment is
        only printed when its breakdown differs from the requirement. Without any
        cost entry the line never claims ``需求`` and simply says ``支付［…］``.
        """
        line = line[: line.rfind(" 花费")] if " 花费" in line else line
        used = tuple(getattr(spec, "used_dice", ()) or ())
        payment: list[tuple[str, int]] = []
        if used:
            counts = Counter(_dice_symbol(int(die)) for die in used)
            payment = list(counts.items())
        requirement = self._requirement_items(spec)
        suffix: list[str] = []
        if requirement:
            head = f"需求{_format_cost(requirement)}"
            if self._cost_is_discounted(spec, requirement):
                # The engine's effective cost is lower than the card's list price;
                # saying so once explains why 需求 ≠ the number in the card text.
                head += "（已计折扣）"
            suffix.append(head)
            if payment and _cost_signature(payment) != _cost_signature(requirement):
                suffix.append(f"支付{_format_cost(payment)}")
                energy = sum(count for label, count in requirement if label == "能量")
                if energy:
                    # 需求 already names the energy; the payment line adds what
                    # actually happens to the charges (a burst spends them all).
                    suffix[-1] += f"（清空{energy}能量）"
        elif payment:
            # No effective cost (switch/tuning) or a genuinely free card.
            suffix.append(f"支付{_format_cost(payment)}")
        return line + "".join(f" {part}" for part in suffix)

    def _cost_is_discounted(self, spec: Any, requirement: Any) -> bool:
        """True when the **effective** requirement differs from the asset list price.

        ``metadata["required_cost"]`` is what the engine charges *now* (artifact
        discounts, talent effects, stage rules); ``play_cost`` is the card's printed
        price. Both sides are compared as label/count multisets with the existing
        ``_cost_signature`` normalisation (``万`` == ``任意`` == VOID), so vocab
        differences alone never count as a discount. An action with no asset entry
        (switch / tuning) has no list price and is never annotated.
        """
        subject = self._cost_subject(spec)
        if subject is None:
            return False
        base = self._cost_items(subject)
        if not base or not requirement:
            return False
        return _cost_signature(base) != _cost_signature(requirement)

    def _skill_name_map(self, character_ids: Any) -> dict[str, int]:
        """Skill **name → id** for our own characters.

        Talent cards say "立刻使用一次星斗归位" by name; only our characters can be
        the target of our talent cards, so scanning our lineup keeps the mapping
        unambiguous (many characters share names like "普通攻击").
        """
        mapping: dict[str, int] = {}
        for character_id in character_ids or ():
            try:
                info = self.assets.character(int(character_id))
            except Exception:
                continue
            for skill_id in getattr(info, "skill_ids", ()) or ():
                skill = self._skill_info(int(skill_id))
                name = str(getattr(skill, "name", "") or "") if skill is not None else ""
                if name:
                    mapping.setdefault(name, int(skill_id))
        return mapping

    def _talent_followup(self, context: Any, raw: str, *, include_hint: bool = True) -> str:
        """Preview the skill a talent card triggers ("装备后，立刻使用一次X").

        Returns ``""`` unless the named skill belongs to our lineup and its
        description carries a damage figure; nothing is invented for buffs.
        ``include_hint`` is ``False`` when the engine preview already states the
        settled damage, so the static reaction hint would be a second number set.
        """
        match = _TALENT_USE.search(str(raw or ""))
        if match is None:
            return ""
        name = match.group(1).strip()
        if not name:
            return ""
        state = getattr(context, "player_view", None)
        if state is None:
            return ""
        try:
            characters = state.players[context.acting_player].characters
        except Exception:
            return ""
        skill_id = self._skill_name_map(
            getattr(character, "definition_id", 0) for character in characters
        ).get(name)
        if skill_id is None:
            return ""
        info = self._skill_info(skill_id)
        damage = _parse_damage(getattr(info, "description", "") if info is not None else "")
        if damage is None:
            return ""
        element, amount = damage
        element_text = "物理" if element == "物理" else f"{element}元素"
        segment = f"随后：{name} —— 造成{amount}点{element_text}伤害"
        if include_hint:
            hint = self._damage_hint(context, element)
            if hint:
                segment += f"；{hint}"
        return segment

    def user_message(self, context: Any, candidates: Any, *, progress: str | None = None) -> dict[str, str]:
        """Reset the per-pass card-description cache before rendering (M12)."""
        self._seen_card_descriptions = set()
        return super().user_message(context, candidates, progress=progress)

    def describe_candidate(self, context: Any, spec: Any, index: int) -> str:
        line = super().describe_candidate(context, spec, index)
        kind = spec.kind
        if kind != OptionKind.ACTION_ELEMENTAL_TUNING:
            # Tuning already renders "把 1 个X骰转为Y骰"; a cost suffix would repeat it.
            line = self._cost_line(line, spec)
        # The engine's settled forecast (damage / reaction / defeat / heal) outranks
        # the static card text, so the static reaction hint is dropped when it exists.
        preview_parts = self._preview_parts(context, spec)
        has_preview_damage = bool(preview_parts)
        extras: list[str] = []
        # True when ``extras[0]`` is the skill/card text: with a preview damage entry
        # that text is only the *card face* value, so the paren block is labelled
        # "卡面：" instead of reading as a second, contradictory damage claim.
        card_face = False
        if kind == OptionKind.ACTION_USE_SKILL:
            info = self._skill_info(int(spec.subject_definition_id or 0))
            label = _SKILL_TYPE_LABELS.get(str(getattr(info, "skill_type", "")), "")
            if label:
                line = line.replace("使用技能：", f"{label}：", 1)
            description = getattr(info, "description", "") if info else ""
            summary = _no_period(_one_line(description))
            if summary:
                extras.append(summary)
                card_face = True
            damage = _parse_damage(description)
            if damage is not None and not has_preview_damage:
                hint = self._damage_hint(context, damage[0])
                if hint:
                    extras.append(hint)
        elif kind == OptionKind.ACTION_PLAY_CARD:
            card_id = int(spec.subject_definition_id or 0)
            # One card with several targets differs only by the target: print the
            # effect once, then let the base renderer's "→ 目标" (plus 需求/支付)
            # identify the other variants (M12).
            first_variant = card_id not in self._seen_card_descriptions
            self._seen_card_descriptions.add(card_id)
            if first_variant:
                info = self._card_info(card_id)
                raw = str(getattr(info, "description", "") if info is not None else "")
                summary = _no_period(_one_line(raw))
                if summary:
                    extras.append(summary)
                    card_face = True
                followup = self._talent_followup(
                    context, raw, include_hint=not has_preview_damage
                )
                if followup:
                    extras.append(followup)
                tags = set(getattr(info, "tags", ()) or ()) if info is not None else set()
                # The engine marks combat-action cards with GCG_TAG_SLOWLY; the
                # description string is not authoritative (many fast cards mention
                # “战斗行动” only to say they are *not* one).
                extras.append(
                    "战斗行动（会换对方行动）"
                    if "GCG_TAG_SLOWLY" in tags
                    else "快速行动，不结束行动权"
                )
        elif kind == OptionKind.ACTION_SWITCH_ACTIVE:
            # No damage is invented here; switching is just an action. The base line
            # named the target twice, so it is rewritten to "当前·X → Y".
            line = self._switch_line(context, spec, index, line)
            extras.append("切换出战角色")
        if extras:
            body = "；".join(extras)
            if has_preview_damage and card_face:
                # The text is the card face, not the outcome: "（卡面：造成2点物理伤害）"
                # followed by "；预计：…0点…" reads as base value vs settled value.
                body = f"卡面：{body}"
            line = f"{line}（{body}）"
        if preview_parts:
            line = f"{line}；预计：{'；'.join(preview_parts)}"
        return line
