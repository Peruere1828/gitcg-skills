"""Prompt rendering for the PvE skill agent.

Layout matters for two reasons:

* **Cost** — a stable prefix (role + task + injected skills) is what the provider's
  prompt cache can hit (¥0.02/M cached vs ¥1/M uncached), so the dynamic part must
  come last and the prefix must not change between decisions of the same game.
* **Attribution** — every candidate gets a stable integer index in the order the
  engine produced it, so the model's answer maps back to an action code without
  re-parsing names.

INNOVATION: stable-prefix rendering + numbered candidates + optional skill citation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from reps.schema import DecisionContext, LowLevelActionSpec, OptionKind
from skillloop.schema import Skill

SYSTEM_ROLE = (
    "你是《七圣召唤》的对局助手。你在一个单人 PvE 关卡里为「我方」做决策，"
    "只能从给出的合法动作里选一个。优先满足关卡的三星目标，其次才是赢得更快。"
)

OUTPUT_CONTRACT = (
    "只输出 JSON：{\"choice\": <动作编号>, \"used\": [<影响本决策的技能 id，最多 2 个>]}。"
    "不要解释，不要输出其它文本。"
)


def _kind_label(kind: OptionKind) -> str:
    return {
        OptionKind.ACTION_USE_SKILL: "使用技能",
        OptionKind.ACTION_PLAY_CARD: "打出卡牌",
        OptionKind.ACTION_SWITCH_ACTIVE: "切换出战",
        OptionKind.ACTION_ELEMENTAL_TUNING: "元素调和",
        OptionKind.ACTION_DECLARE_END: "宣布结束回合",
        OptionKind.CHOOSE_ACTIVE: "选择出战角色",
        OptionKind.SELECT_CARD: "选择卡牌",
        OptionKind.REROLL_DICE: "重掷骰子",
        OptionKind.SWITCH_HANDS: "起手换牌",
    }.get(kind, str(kind))


@dataclass(frozen=True)
class TaskSpec:
    """One natural-language task: a stage (+ its three-star objectives) and a deck."""

    stage: str
    display: str
    objectives: tuple[str, ...]          # human-readable objective texts (prompt)
    deck: str
    objective_types: tuple[str, ...] = ()  # machine types (retrieval conditions)

    def describe(self) -> str:
        goals = "；".join(self.objectives) if self.objectives else "赢得胜利"
        return f"关卡【{self.display}】用【{self.deck}】出战。三星目标：{goals}。"


class StateRenderer:
    """Compact public-state and candidate rendering (names come from the asset pack)."""

    def __init__(self, assets: Any, *, opponent_name: str = "对手",
                 npc_names: dict[int, str] | None = None) -> None:
        self.assets = assets
        self.opponent_name = opponent_name
        self._npc_names = npc_names

    def _npc_name_map(self) -> dict[int, str]:
        """Overlay enemies are not in the official asset dump; their names live in
        ``configs/npc`` (``NpcEnemy.engine_id`` -> name), so fall back to that."""
        if self._npc_names is None:
            try:
                from npc.spec import load_enemies

                self._npc_names = {
                    int(enemy.engine_id): str(enemy.name)
                    for enemy in load_enemies().values()
                    if int(enemy.engine_id or 0)
                }
            except Exception:
                self._npc_names = {}
        return self._npc_names

    def _char(self, definition_id: int) -> str:
        try:
            return str(self.assets.character(int(definition_id)).name)
        except Exception:
            return self._npc_name_map().get(int(definition_id)) or f"角色#{definition_id}"

    def _card(self, definition_id: int) -> str:
        try:
            return str(self.assets.card(int(definition_id)).name)
        except Exception:
            return f"卡牌#{definition_id}"

    def _skill(self, definition_id: int) -> str:
        try:
            skill = self.assets.skill(int(definition_id))
            owner = getattr(skill, "character_name", "") or ""
            return f"{owner}·{skill.name}".strip("·")
        except Exception:
            return f"技能#{definition_id}"

    # -- state -------------------------------------------------------------
    def describe_state(self, context: DecisionContext) -> str:
        state = context.player_view
        if state is None:
            return "（无状态）"
        me = state.players[context.acting_player]
        opp = state.players[1 - context.acting_player]

        def side(player: Any) -> str:
            return "，".join(
                f"{self._char(c.definition_id)} {c.health}/{c.max_health}"
                + (f" 能量{c.energy}/{c.max_energy}" if getattr(c, "max_energy", 0) else "")
                + (f" {c.aura}" if getattr(c, "aura", None) else "")
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

    # -- candidates --------------------------------------------------------
    def _targets_text(self, context: DecisionContext, spec: LowLevelActionSpec) -> str:
        """Render target slots, mapping character slots to names.

        Equipment/event cards differ only by *which character* they target, so
        omitting targets makes several candidates look identical to the model.
        """
        if not spec.target_slots:
            return ""
        state = context.player_view
        parts: list[str] = []
        for target in spec.target_slots:
            side = "我方" if target.owner == "self" else "对方"
            if state is not None and target.zone in ("character", "combat_status"):
                player = state.players[context.acting_player if target.owner == "self" else 1 - context.acting_player]
                if 0 <= target.index < len(player.characters):
                    parts.append(f"{side}·{self._char(player.characters[target.index].definition_id)}")
                    continue
            parts.append(f"{side}:{target.zone}[{target.index}]")
        return " → " + "，".join(parts)

    def describe_candidate(self, context: DecisionContext, spec: LowLevelActionSpec, index: int) -> str:
        kind = spec.kind
        hand = []
        dice: list[int] = []
        if context.player_view is not None:
            me = context.player_view.players[context.acting_player]
            hand = [self._card(c.definition_id) for c in me.hand_cards]
            dice = [int(d) for d in me.dice]
        if kind == OptionKind.ACTION_USE_SKILL:
            subject = self._skill(spec.subject_definition_id)
        elif kind == OptionKind.ACTION_PLAY_CARD:
            subject = self._card(spec.subject_definition_id)
        elif kind in (OptionKind.ACTION_SWITCH_ACTIVE, OptionKind.CHOOSE_ACTIVE):
            subject = self._char(spec.subject_definition_id)
        elif kind == OptionKind.ACTION_ELEMENTAL_TUNING:
            gain = "".join(_dice_symbol(d) for d in spec.used_dice)
            subject = f"弃「{self._card(spec.subject_definition_id)}」换 {gain or '1'} 骰"
        elif kind == OptionKind.SELECT_CARD:
            subject = self._card(spec.select_card_definition_id or spec.subject_definition_id)
        elif kind == OptionKind.REROLL_DICE:
            mask = int(spec.reroll_dice_mask)
            picked = [f"{_dice_symbol(dice[i])}" for i in range(len(dice)) if mask >> i & 1]
            subject = "重掷 " + ("，".join(picked) if picked else "0 个")
        elif kind == OptionKind.SWITCH_HANDS:
            mask = int(spec.switch_hand_slot_mask)
            picked = [hand[i] for i in range(len(hand)) if mask >> i & 1]
            subject = "换掉 " + ("，".join(picked) if picked else "（全部保留）")
        elif kind == OptionKind.ACTION_DECLARE_END:
            subject = ""
        else:
            subject = "—"
        if spec.used_dice and kind != OptionKind.ACTION_ELEMENTAL_TUNING:
            spent = "".join(_dice_symbol(d) for d in spec.used_dice)
            dice = f" 花费{spent}"
        else:
            dice = ""
        body = f"{_kind_label(kind)}：{subject}" if subject else _kind_label(kind)
        return f"[{index}] {body}{self._targets_text(context, spec)}{dice}"

    # -- prompt ------------------------------------------------------------
    def system_message(self, *, task: TaskSpec, skills: Sequence[Skill], rules_note: str = "") -> dict[str, str]:
        parts = [SYSTEM_ROLE]
        if rules_note:
            parts.append(rules_note)
        parts.append(task.describe())
        if skills:
            parts.append("可参考的技能（来自历史经验，优先级低于关卡目标）：")
            parts.extend(skill.prompt_block() for skill in skills)
        parts.append(OUTPUT_CONTRACT)
        return {"role": "system", "content": "\n".join(parts)}

    def user_message(self, context: DecisionContext, candidates: Sequence[tuple[int, LowLevelActionSpec]]) -> dict[str, str]:
        lines = [self.describe_state(context), "", "合法动作："]
        lines.extend(
            self.describe_candidate(context, spec, index)
            for index, (_code, spec) in enumerate(candidates)
        )
        return {"role": "user", "content": "\n".join(lines)}


def select_candidates(
    context: DecisionContext,
    *,
    max_candidates: int = 16,
    skip_forced: bool = True,
) -> list[tuple[int, LowLevelActionSpec]]:
    """Deduplicate the legal list down to a renderable, stable candidate set.

    ``skip_forced`` returns ``[]`` when there is exactly one legal action: the caller
    then plays it without an LLM call (mechanical picks are a large share of GITCG
    decisions and cost both latency and tokens).
    """
    codes = [int(code) for code in context.legal_low_level_codes]
    specs = list(context.legal_low_level_specs)
    pairs = list(zip(codes, specs))
    if skip_forced and len(pairs) <= 1:
        return []
    selected: list[tuple[int, LowLevelActionSpec]] = []
    seen: set[tuple[Any, ...]] = set()
    for code, spec in pairs:
        key = (spec.kind, spec.subject_definition_id, spec.target_slots, spec.used_dice)
        if key in seen:
            continue
        seen.add(key)
        selected.append((code, spec))
        if len(selected) >= max_candidates:
            break
    return selected


_DICE_SYMBOL = {
    0: "?",  # unspecified
    1: "冰", 2: "水", 3: "火", 4: "雷", 5: "风", 6: "岩", 7: "草", 8: "万",
}


_PHASE_LABEL = {
    "init_hands": "起手换牌",
    "action": "行动阶段",
    "end": "结束阶段",
    "roll": "投掷阶段",
}


def _phase_label(phase: Any) -> str:
    return _PHASE_LABEL.get(str(phase), str(phase))


def _dice_symbol(value: int) -> str:
    return _DICE_SYMBOL.get(int(value), str(value))
