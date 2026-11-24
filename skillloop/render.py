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
    "只输出 JSON：{\"choice\": <动作编号>, \"used\": "
    "[<本决策引用的技能 id（技能块方括号里的 id），最多 2 个>]}。"
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
class ObjectiveDetail:
    """Machine-readable objective data used by the per-decision progress block."""

    type: str
    text: str = ""
    count: int | None = None
    kind: str | None = None
    n: int | None = None
    k: int | None = None


@dataclass(frozen=True)
class TaskSpec:
    """One natural-language task: a stage (+ its three-star objectives) and a deck."""

    stage: str
    display: str
    objectives: tuple[str, ...]          # human-readable objective texts (prompt)
    deck: str
    objective_types: tuple[str, ...] = ()  # machine types (retrieval conditions)
    max_rounds: int | None = None          # e.g. the n in win_within_rounds
    objective_details: tuple[ObjectiveDetail, ...] = ()
    # Static per-game facts for the cached system prefix (empty = not assembled).
    characters: tuple[int, ...] = ()       # our lineup
    cards: tuple[int, ...] = ()            # our full deck
    opponent_characters: tuple[int, ...] = ()

    def describe(self) -> str:
        goals = "；".join(self.objectives) if self.objectives else "赢得胜利"
        return f"关卡【{self.display}】用【{self.deck}】出战。三星目标：{goals}。"


@dataclass
class ProgressSignals:
    """Cheap, self-counted progress at one decision.

    Only things the agent can observe from its *own* chosen actions (card type,
    burst skill, current defeated characters) are tracked. Everything else stays
    ``unknown`` in the prompt instead of being guessed.
    """

    played_weapon: int = 0
    played_artifact: int = 0
    played_event: int = 0
    played_support: int = 0
    played_other: int = 0
    bursts: int = 0
    defeated: int = 0


def _objective_details(task: TaskSpec) -> tuple[ObjectiveDetail, ...]:
    if task.objective_details:
        return task.objective_details
    types = task.objective_types
    return tuple(
        ObjectiveDetail(type=types[index] if index < len(types) else "", text=text)
        for index, text in enumerate(task.objectives)
    )


def _played_for(detail: ObjectiveDetail, signals: ProgressSignals) -> int | None:
    """Own-card count for an equipment objective, or ``None`` when not countable."""
    kind = detail.kind or "equipment"
    if kind == "weapon":
        return signals.played_weapon
    if kind == "artifact":
        return signals.played_artifact
    if kind == "equipment":
        return signals.played_weapon + signals.played_artifact
    return None  # e.g. kind="talent": the agent does not classify talents


def progress_block(task: TaskSpec, context: Any, signals: ProgressSignals) -> str:
    """Compact ``回合 R/最大回合数；未完成目标：...`` line for the user message.

    What it knows exactly:

    * round number (from the public state) and the stage's round limit;
    * equipment / event cards **we** have played (counted from our own decisions);
    * burst skills **we** have used (same counting);
    * defeated characters on our side (from the public state).

    What it cannot know at decision time: whether we will win, whether the final
    ``defeated_less_than`` check will pass, and elemental-reaction counts (the public
    view does not expose a reliable running total). Those are rendered as ``未知``;
    the ``win`` objective is the exception: it can only be shown unfinished while
    the game is still running, so it reads ``尚未达成``.
    A countable objective is omitted once our own count reaches its threshold; a
    round limit is marked failed only when the current round already exceeds it.
    """
    state = context.player_view
    round_number = int(state.round_number) if state is not None else None
    phase = str(getattr(state, "phase", "")) if state is not None else ""
    details = _objective_details(task)
    max_rounds = task.max_rounds
    if max_rounds is None:
        max_rounds = next(
            (detail.n for detail in details if detail.type == "win_within_rounds" and detail.n is not None),
            None,
        )
    if phase == "init_hands" or round_number == 0:
        # Mulligan happens before round 1: "回合 0/5" read like a failed round.
        header = "准备阶段"
    elif max_rounds is not None:
        # Never print "6/5": the stage's 3-star cap and the engine's 15-round
        # game limit are different bounds, so both are stated explicitly.
        header = (
            f"第{round_number if round_number is not None else '?'}轮"
            f"（三星要求≤{max_rounds}轮；对局上限15轮）"
        )
    else:
        header = (
            f"第{round_number if round_number is not None else '?'}轮（对局上限15轮）"
        )

    unfinished: list[str] = []
    for detail in details:
        text = detail.text or detail.type or "目标"
        kind = detail.type

        if kind == "play_equipment_card":
            have = _played_for(detail, signals)
            if have is None:
                unfinished.append(f"{text}（未知）")
                continue
            required = detail.count if detail.count is not None else 1
            if have < required:
                unfinished.append(f"{text}（我方已打{have}/{required}）")
            continue

        if kind == "play_event_cards":
            required = detail.count if detail.count is not None else 1
            if signals.played_event < required:
                unfinished.append(f"{text}（我方已打{signals.played_event}/{required}）")
            continue

        if kind == "use_elemental_burst":
            required = detail.count if detail.count is not None else 1
            if signals.bursts < required:
                unfinished.append(f"{text}（我方已用{signals.bursts}/{required}）")
            continue

        if kind == "defeated_less_than":
            if detail.k is None:
                unfinished.append(f"{text}（未知）")
            elif signals.defeated >= int(detail.k):
                unfinished.append(f"{text}（已失败：我方已倒下{signals.defeated}人）")
            else:
                unfinished.append(f"{text}（我方已倒下{signals.defeated}人，未知最终）")
            continue

        if kind == "win_within_rounds":
            limit = detail.n if detail.n is not None else max_rounds
            if round_number is not None and limit is not None and round_number > int(limit):
                unfinished.append(f"{text}（已失败：当前第{round_number}回合）")
            else:
                unfinished.append(f"{text}（未知）")
            continue

        # win / trigger_elemental_reactions / anything new: no cheap exact signal.
        # Reaching this branch means the "win" objective is still unfulfilled (a win
        # would have ended the game), so "尚未达成" is exact where "未知" was vague;
        # the other kinds really have no cheap signal and stay "未知".
        unfinished.append(f"{text}（{'尚未达成' if kind == 'win' else '未知'}）")

    tail = "；".join(unfinished) if unfinished else "无（按我方动作计数）"
    return f"{header}；未完成目标：{tail}"


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
            f"{opponent_status_label(self.opponent_name)}：{side(opp)}；手牌{len(opp.hand_cards)}张，骰子{len(opp.dice)}个",
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
            subject = self._tuning_text(spec)
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

    def _tuning_text(self, spec: LowLevelActionSpec) -> str:
        """Elemental tuning: ``used_dice`` is the die being converted, not the result.

        The engine fills ``ElementalTuningAction.target_dice`` with the element the
        die becomes (``reps/action_quality.py`` uses it for alignment), while
        ``used_dice`` holds the die that is spent. Rendering ``used_dice`` as the
        outcome showed "弃「X」换 水 骰" for a Hydro die that actually turned into
        Pyro. Old recordings have no ``target_dice``, so they fall back to reporting
        only the spent die instead of guessing the result.
        """
        card = self._card(spec.subject_definition_id)
        source = "".join(_dice_symbol(d) for d in spec.used_dice)
        target = int(getattr(spec, "target_dice", 0) or 0)
        if target:
            origin = f"{source}骰" if source else "骰"
            return f"弃「{card}」把 1 个{origin}转为{_dice_symbol(target)}骰"
        return f"弃「{card}」换 1 个{source}骰" if source else f"弃「{card}」换 1 个骰"

    # -- prompt ------------------------------------------------------------
    def knowledge_message(self, task: TaskSpec) -> str:
        """Static facts that never change during a game (opt-in renderers override).

        The base renderer intentionally keeps the compact prompt unchanged; rich
        renderers add full character-skill and deck-card descriptions here so the
        dynamic state can stay name-only.
        """
        return ""

    def system_message(self, *, task: TaskSpec, skills: Sequence[Skill], rules_note: str = "") -> dict[str, str]:
        parts = [SYSTEM_ROLE]
        if rules_note:
            parts.append(rules_note)
        parts.append(task.describe())
        knowledge = self.knowledge_message(task)
        if knowledge:
            parts.append(knowledge)
        if skills:
            parts.append("可参考的技能（来自历史经验，优先级低于关卡目标）：")
            parts.extend(skill.prompt_block() for skill in skills)
        parts.append(OUTPUT_CONTRACT)
        return {"role": "system", "content": "\n".join(parts)}

    def user_message(
        self,
        context: DecisionContext,
        candidates: Sequence[tuple[int, LowLevelActionSpec]],
        *,
        progress: str | None = None,
    ) -> dict[str, str]:
        lines: list[str] = []
        if progress:
            # Dynamic, per-decision text. It must stay in the *user* message so the
            # system prefix (and therefore the provider cache key) is unchanged.
            lines.append(progress)
        lines.extend([self.describe_state(context), "", "合法动作："])
        lines.extend(
            self.describe_candidate(context, spec, index)
            for index, (_code, spec) in enumerate(candidates)
        )
        return {"role": "user", "content": "\n".join(lines)}


# A 5-card mulligan has 2**5 = 32 keep/swap combinations; the per-decision cap of 16
# dropped every combination that touched the 5th card.
SWITCH_HANDS_MAX_CANDIDATES = 32


def _candidate_dedup_key(spec: LowLevelActionSpec) -> tuple[Any, ...]:
    """Stable identity of a candidate for de-duplication.

    ``SWITCH_HANDS`` is keyed by the whole ``switch_hand_slot_mask`` instead of the
    first swapped card: mulligan combos (including "keep all", mask 0) are distinct
    choices, and the old ``subject_definition_id`` key collapsed every combo that
    happened to start with the same card.
    """
    if spec.kind == OptionKind.SWITCH_HANDS:
        return (spec.kind, int(getattr(spec, "switch_hand_slot_mask", 0) or 0))
    if spec.kind == OptionKind.ACTION_ELEMENTAL_TUNING:
        # Tuning differs by the discarded card; without these fields two tunings
        # of *different* hand cards collapsed whenever their paid die matched,
        # which could still drop a hand card from the candidate set.
        return (
            spec.kind,
            spec.subject_definition_id,
            spec.target_slots,
            spec.used_dice,
            int(getattr(spec, "discarded_hand_slot", -1)),
            int(getattr(spec, "discarded_card_definition_id", 0)),
        )
    return (spec.kind, spec.subject_definition_id, spec.target_slots, spec.used_dice)


def _hand_card_id(spec: LowLevelActionSpec, hand_ids: Sequence[int]) -> int | None:
    """Definition id of the hand card an action consumes, or ``None``.

    Play-card actions name the card directly; elemental tuning names the discarded
    slot (older recordings only had the slot, so it falls back to the hand order).
    """
    if spec.kind == OptionKind.ACTION_PLAY_CARD:
        card_id = int(spec.subject_definition_id or 0)
    elif spec.kind == OptionKind.ACTION_ELEMENTAL_TUNING:
        card_id = int(getattr(spec, "discarded_card_definition_id", 0) or 0)
        if not card_id:
            slot = int(getattr(spec, "discarded_hand_slot", -1))
            if 0 <= slot < len(hand_ids):
                card_id = int(hand_ids[slot])
    else:
        return None
    if not card_id:
        return None
    if hand_ids and card_id not in hand_ids:
        return None
    return card_id


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

    The engine emits actions as skills → every hand card → switches → declare-end,
    so a plain 16-item prefix silently dropped "宣布结束" and whole hand cards. The
    guarantees below are therefore applied *before* the cap:

    1. ``ACTION_DECLARE_END`` is always kept when it is legal;
    2. every hand card keeps at least one action (a play is preferred over tuning);
    3. remaining slots are filled in the engine's original order.

    ``SWITCH_HANDS`` candidates are ordered by ascending slot mask so "keep all"
    (mask 0) and multi-card combos survive the cap. A mulligan request is allowed a
    higher cap (``SWITCH_HANDS_MAX_CANDIDATES``): a 5-card hand has 32 keep/swap
    combinations and the 16 cap cut off every combo of the 5th card. Indices are
    re-numbered from 0 by the caller, independent of the engine order.
    """
    codes = [int(code) for code in context.legal_low_level_codes]
    specs = list(context.legal_low_level_specs)
    pairs = list(zip(codes, specs))
    if skip_forced and len(pairs) <= 1:
        return []

    deduped: list[tuple[int, LowLevelActionSpec]] = []
    seen: set[tuple[Any, ...]] = set()
    for code, spec in pairs:
        key = _candidate_dedup_key(spec)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((code, spec))

    # Mulligan requests contain only SWITCH_HANDS; reorder the whole list by mask
    # so the cap keeps the smallest, most legible combos first (incl. mask 0).
    cap = max(0, int(max_candidates))
    if deduped and all(spec.kind == OptionKind.SWITCH_HANDS for _code, spec in deduped):
        deduped.sort(key=lambda item: int(getattr(item[1], "switch_hand_slot_mask", 0) or 0))
        cap = max(cap, SWITCH_HANDS_MAX_CANDIDATES)

    state = getattr(context, "player_view", None)
    hand_ids: list[int] = []
    if state is not None and 0 <= int(context.acting_player) < len(state.players):
        hand_ids = [
            int(getattr(card, "definition_id", 0) or 0)
            for card in state.players[int(context.acting_player)].hand_cards
        ]

    required: list[int] = []

    def _require(index: int) -> None:
        if index not in required:
            required.append(index)

    for index, (_code, spec) in enumerate(deduped):
        if spec.kind == OptionKind.ACTION_DECLARE_END:
            _require(index)
            break

    if hand_ids:
        for card_id in dict.fromkeys(hand_ids):
            options = [
                index
                for index, (_code, spec) in enumerate(deduped)
                if _hand_card_id(spec, hand_ids) == card_id
            ]
            if not options:
                continue
            play = next(
                (
                    index
                    for index in options
                    if deduped[index][1].kind == OptionKind.ACTION_PLAY_CARD
                ),
                None,
            )
            _require(play if play is not None else options[0])

    selected: list[tuple[int, LowLevelActionSpec]] = []
    for index in required:
        selected.append(deduped[index])
        if len(selected) >= cap:
            return selected
    chosen = set(required)
    for index, item in enumerate(deduped):
        if index in chosen:
            continue
        selected.append(item)
        if len(selected) >= cap:
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


def opponent_status_label(name: Any) -> str:
    """Status-line label for the opponent: ``NPC(布兰琪)`` → ``对方·布兰琪``.

    The runner passes ``NPC(<stage display>)`` so the system prompt can name the
    enemy; the live board uses the player-relative wording instead. Unknown names
    are left untouched so a caller that already supplies its own label (e.g. a
    deck name) does not silently lose it.
    """
    text = str(name or "").strip()
    if text.startswith("NPC(") and text.endswith(")"):
        text = text[4:-1].strip()
    if text in ("", "NPC", "对手", "对方"):
        return "对方"
    return f"对方·{text}"


def _dice_symbol(value: int) -> str:
    return _DICE_SYMBOL.get(int(value), str(value))
