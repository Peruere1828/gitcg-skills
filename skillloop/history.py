"""Bounded, own-seat-only match history for the live decision prompt.

The decision prompt used to show only the *current* board: the model could not
see what either side did a moment ago, so it could not reason about tempo ("the
opponent just used a burst", "I already played a weapon"). This module turns two
public sources into a short, chronological log:

* ``DecisionContext.metadata["recent_events"]`` -- the engine mutation stream
  projected by ``envs.match._public_event_payload`` between the previous and the
  current decision of this seat. It is the primary source because it names the
  exact skills/cards that were used.
* a **public** state diff between two consecutive ``player_view`` snapshots --
  the fallback when a run records no events (e.g. the web debugger), and a
  supplement for the consequences events do not spell out (HP / energy / aura /
  attachments / summons / supports).

Information discipline (same rule as ``reps.public_state.mask_state_for_player``):
only what the acting seat can see enters a line. The opponent's hand is a count,
never names; the opponent's dice are never read here (the masked view does not
carry them); the opponent's pile is never read. ``history`` must never touch
``players[1].hand_cards[*]["definition_id"]`` -- the negative placeholder ids the
engine puts there would otherwise leak into the prompt.

Names come from one resolver, not from an ad-hoc second table: ``_name`` asks
``skillloop.replay._skill_name`` first (that is the resolver the replay traces
use, including the NPC skill table in ``configs/npc``), then the cached asset
dump, the NPC enemy card ids, the engine passive sheets, and the overlay variant
namespace (``official_id + 70000``). An id nothing resolves still falls back to
``#<id>`` so it stays greppable instead of crashing the prompt.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterable, Sequence

from skillloop.cards import character_name
from skillloop.render import _DICE_SYMBOL
from skillloop.replay import _REACTION_TEXT, _skill_name

#: Engine ``EntityArea`` value for "in hand" (see ``envs.match._ENTITY_AREA_HAND``).
_HAND_AREA = 5
#: Hard cap for one history line (prompt budget).
MAX_LINE_CHARS = 120
#: Hard cap for the state-diff supplement of a single decision.
MAX_DIFF_LINES = 8
#: Hard cap on recorded own actions awaiting their engine event (a card play may
#: wait one decision for its effect event).
MAX_PENDING_OWN = 8
#: Retained lines (``render`` keeps at most 12 of them).
MAX_STORED_LINES = 200

_HEADER = "对局历史（仅你方可见信息，按时间顺序，最后一条最新）："

#: The engine namespaces overlay/variant definitions at ``official_id + 70000``
#: (verified: ``193011 -> 123011 潜行``), so an id above it is also looked up
#: below it before giving up.
_VARIANT_OFFSET = 70000
#: A plain ``「…」`` object reference inside a history line.
_QUOTED = re.compile(r"「([^」]+)」")
#: Absolute state facts: the dice list is re-read every decision, so a newer line
#: makes the older one stale (a roll followed by a reroll is not two facts the
#: model needs, and the board already shows the current dice).
_SUPERSEDED_PREFIXES = ("我方骰子", "对方骰子")
#: Candidate labels that name a skill or a card play -- the only own actions whose
#: engine event would restate the very same line. Base renderer: ``使用技能`` /
#: ``打出卡牌``; rich renderer rewrites skills to ``普通攻击`` / ``元素战技`` /
#: ``元素爆发``. Other labels (``选择出战角色``, ``起手换牌``, ``元素调和``) must
#: not become a pending name: ``刻晴`` would otherwise swallow ``刻晴·星斗归位``.
_OWN_ACTION_PREFIXES = ("使用技能", "普通攻击", "元素战技", "元素爆发", "打出卡牌")

#: Reasons that make an own ``card_move`` out of our hand *our own action* (a
#: recorded label already describes it, and the raw event would duplicate it).
_OWN_MOVE_REASONS = frozenset({"played", "tuning", "switch"})
#: Engine areas a played card can land in (support / character / status / summon).
_PLAY_AREA = frozenset({1, 2, 3, 4})

_LEAVE_VERBS = {
    "played": "打出",
    "tuning": "调和弃置",
    "discarded": "弃置",
    "switch": "换掉",
    "steal": "被夺走",
}
#: Engine bookkeeping markers that are not part of a status/card identity.
_NAME_SUFFIXES = ("（已创建）", "（生效中）")


# -- names -------------------------------------------------------------------
@lru_cache(maxsize=1)
def _npc_names() -> dict[int, str]:
    """Overlay NPC ids -> names (they are not in the official asset dump)."""
    try:
        from npc.spec import load_enemies

        return {
            int(enemy.engine_id): str(enemy.name)
            for enemy in load_enemies().values()
            if int(enemy.engine_id or 0)
        }
    except Exception:
        return {}


def _name(definition_id: int) -> str:
    """Chinese name for a card / skill / character id, or ``""`` for id 0.

    Unknown ids fall back to ``#<id>`` so a new card cannot crash the prompt and
    stays greppable.
    """
    value = int(definition_id or 0)
    if value <= 0:
        return ""
    name = _resolved_name(value) or f"#{value}"
    for suffix in _NAME_SUFFIXES:
        name = name.replace(suffix, "")
    return name


def _resolved_name(definition_id: int) -> str:
    """Best public name for one definition id, or ``""`` when nothing resolves.

    Resolution order (first hit wins), tried for the id itself and -- for an
    overlay variant id -- for ``id - 70000``:

    1. ``skillloop.replay._skill_name`` -- the *same* resolver replay traces use:
       catalog skills, then the NPC skill table built from ``configs/npc``.
    2. the cached asset dump (cards / characters / statuses, ``skillloop.cards``).
    3. the NPC enemy card ids (``9301``/``9202`` are not in the asset dump).
    4. an engine passive skill id (``character_id * 10 + slot``), named
       ``owner·passive``; the NPC skill table only carries the A/E/Q slots and
       the extra prepared skills, so ``93014`` (潜行大师) needs this step.
    5. the overlay variant namespace ``official_id + 70000`` (``193011`` -> 潜行).
    """
    value = int(definition_id or 0)
    if value <= 0:
        return ""
    candidates = [value]
    if value > _VARIANT_OFFSET:
        candidates.append(value - _VARIANT_OFFSET)
    for probe in candidates:
        name = str(_skill_name(int(probe)) or "")
        if name and not name.startswith("#"):
            return name
        name = character_name(int(probe))
        if name != f"#{probe}":
            return name
        name = _npc_names().get(int(probe), "")
        if name:
            return name
        name = _passive_name(int(probe))
        if name:
            return name
    return ""


def _passive_name(definition_id: int) -> str:
    """``owner·name`` for an engine passive skill id, else ``""``.

    Passives are not in the asset dump and not in the NPC skill table (that table
    is A/E/Q plus prepared extras). ``skillloop.passive_skills`` parses the
    engine ``.gts`` sheets, which do carry them.

    The lookup is by *exact skill id* first: GTS aliases are only unique inside
    one file (all three hilichurl shooters call their passive ``Hide``), so the
    alias table can only be a fallback -- and only when the owner has exactly one
    passive, where there is nothing to confuse.
    """
    try:
        from skillloop.passive_skills import passive_by_id, passives_for
    except Exception:
        return ""
    value = int(definition_id)
    try:
        item = passive_by_id(value)
    except Exception:
        item = None
    owner_id = value // 10
    if item is None:
        try:
            owner_passives = [entry for entry in passives_for(owner_id) if entry.get("name")]
        except Exception:
            owner_passives = []
        if len(owner_passives) != 1:
            return ""
        item = owner_passives[0]
    name = str(item.get("name") or "").strip()
    if not name:
        return ""
    owner = _resolved_name(owner_id) if owner_id else ""
    return f"{owner}·{name}".strip("·")


def _is_own_action_move(event: dict[str, Any]) -> bool:
    reason = str(event.get("reason") or "")
    if reason in _OWN_MOVE_REASONS:
        return True
    return reason == "unspecified" and int(event.get("to") or 0) in _PLAY_AREA


def _element(value: Any) -> str:
    element = int(value or 0)
    if element == 0:
        return "无"
    return _DICE_SYMBOL.get(element, str(element))


def _reaction_name(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return _REACTION_TEXT.get(raw.lower(), raw)


def _clip(line: str, limit: int = MAX_LINE_CHARS) -> str:
    text = " ".join(str(line).split())
    return text if len(text) <= limit else text[: max(1, limit - 1)] + "…"


def _round_tag(state: Any) -> str:
    """Round label for a history line: ``"3"``, or ``"?"`` when unknown."""
    value = getattr(state, "round_number", None) if state is not None else None
    if value is None:
        return "?"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return "?"


def _event_round(event: dict[str, Any], fallback: str) -> str:
    """The event's own round when it carries one (round 0 included)."""
    value = event.get("round_number")
    if value is None:
        return fallback
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return fallback


# -- public projections ------------------------------------------------------
def _recent_events(context: Any) -> list[dict[str, Any]]:
    metadata = getattr(context, "metadata", None) or {}
    events = metadata.get("recent_events") or ()
    return [event for event in events if isinstance(event, dict)]


def _side_public(player: Any, *, own: bool) -> dict[str, Any]:
    """Public projection of one seat.

    ``own`` also captures the private-but-allowed fields (hand identity and dice)
    of that seat; for the opponent those tuples stay empty by construction.
    """
    characters = tuple(
        (
            int(getattr(character, "definition_id", 0) or 0),
            int(getattr(character, "health", 0) or 0),
            int(getattr(character, "energy", 0) or 0),
            int(getattr(character, "aura", 0) or 0),
            bool(getattr(character, "defeated", False)),
        )
        for character in (getattr(player, "characters", ()) or ())
    )
    attachments = tuple(
        (
            int(getattr(character, "definition_id", 0) or 0),
            tuple(
                int(getattr(entity, "definition_id", 0) or 0)
                for entity in (getattr(character, "entities", ()) or ())
            ),
        )
        for character in (getattr(player, "characters", ()) or ())
    )

    def ids(zone: str) -> tuple[int, ...]:
        return tuple(
            int(getattr(entity, "definition_id", 0) or 0)
            for entity in (getattr(player, zone, ()) or ())
        )

    hand_count = len(getattr(player, "hand_cards", ()) or ())
    # Only our own hand identity is read at all: for the opponent the masked
    # ``definition_id`` (0 / negative placeholder) is never touched.
    hand = (
        tuple(
            int(getattr(card, "definition_id", 0) or 0)
            for card in (getattr(player, "hand_cards", ()) or ())
        )
        if own
        else ()
    )
    return {
        "characters": characters,
        "attachments": attachments,
        "statuses": ids("combat_statuses"),
        "summons": ids("summons"),
        "supports": ids("supports"),
        "hand_count": hand_count,
        "hand": hand,
        "dice": tuple(int(die) for die in (getattr(player, "dice", ()) or ())) if own else (),
    }


def _added_removed(before: Iterable[int], after: Iterable[int]) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    old, new = Counter(int(v) for v in before), Counter(int(v) for v in after)
    return list((new - old).items()), list((old - new).items())


def _zone_lines(
    prev: dict[str, Any],
    now: dict[str, Any],
    label: str,
    *,
    zone: str,
    verb_add: str,
    verb_remove: str,
) -> list[str]:
    added, removed = _added_removed(prev[zone], now[zone])
    lines = []
    for definition_id, count in added:
        suffix = f"×{count}" if count > 1 else ""
        lines.append(f"{label} {verb_add}「{_name(definition_id)}」{suffix}")
    for definition_id, count in removed:
        suffix = f"×{count}" if count > 1 else ""
        lines.append(f"{label} {verb_remove}「{_name(definition_id)}」{suffix}")
    return lines


def _diff_lines(
    prev: dict[str, Any],
    now: dict[str, Any],
    label: str,
    *,
    own: bool,
    round_tag: str,
) -> list[str]:
    """Public-field diff of one seat between two consecutive decisions."""
    lines: list[str] = []

    for before, after in zip(prev["characters"], now["characters"]):
        if before == after:
            continue
        name = _name(after[0]) or "角色"
        if before[1] != after[1]:
            lines.append(f"{label}·{name} HP {before[1]}→{after[1]}")
        elif bool(before[4]) != bool(after[4]):
            lines.append(f"{label}·{name} {'倒下' if after[4] else '复活'}")
        if before[2] != after[2]:
            lines.append(f"{label}·{name} 能量 {before[2]}→{after[2]}")
        if before[3] != after[3]:
            lines.append(f"{label}·{name} 附着 {_element(before[3])}→{_element(after[3])}")

    for before, after in zip(prev["attachments"], now["attachments"]):
        name = _name(after[0]) or "角色"
        added, removed = _added_removed(before[1], after[1])
        for definition_id, count in added:
            suffix = f"×{count}" if count > 1 else ""
            lines.append(f"{label}·{name} 获得「{_name(definition_id)}」{suffix}")
        for definition_id, count in removed:
            suffix = f"×{count}" if count > 1 else ""
            lines.append(f"{label}·{name} 失去「{_name(definition_id)}」{suffix}")

    lines.extend(
        _zone_lines(
            prev, now, label,
            zone="statuses", verb_add="获得状态", verb_remove="失去状态",
        )
    )
    lines.extend(
        _zone_lines(
            prev, now, label,
            zone="summons", verb_add="召唤", verb_remove="召唤物消失",
        )
    )
    lines.extend(
        _zone_lines(
            prev, now, label,
            zone="supports", verb_add="打出支援", verb_remove="支援离场",
        )
    )

    if own:
        drawn, lost = _added_removed(prev["hand"], now["hand"])
        if drawn:
            names = "".join(f"「{_name(definition_id)}」" for definition_id, _count in drawn)
            lines.append(f"我方抽到{names}")
        elif prev["hand_count"] != now["hand_count"]:
            lines.append(f"我方手牌 {prev['hand_count']}→{now['hand_count']}")
        elif lost:
            names = "".join(f"「{_name(definition_id)}」" for definition_id, _count in lost)
            lines.append(f"我方手牌失去{names}")
        if prev["dice"] != now["dice"]:
            symbols = "".join(_DICE_SYMBOL.get(int(die), str(die)) for die in now["dice"]) or "无"
            if len(prev["dice"]) == len(now["dice"]):
                lines.append(f"我方骰子变为：{symbols}")
            else:
                lines.append(f"我方骰子 {len(prev['dice'])}→{len(now['dice'])}：{symbols}")
    elif prev["hand_count"] != now["hand_count"]:
        lines.append(f"{label}手牌 {prev['hand_count']}→{now['hand_count']}")

    # Every history line carries the same ``R{n}`` prefix, so the block reads as
    # one chronological log instead of "actions stamped, consequences not".
    return [f"R{round_tag} {line}" for line in lines]


def _side_label(player: Any, seat: int) -> str:
    if player is None:
        return "场上"
    return "我方" if int(player) == int(seat) else "对方"


def _is_stated(line: str, stated: set[str]) -> bool:
    """True when an event line already states this line's content.

    ``stated`` holds the names the event log (or a recorded own action) already
    put on screen plus those whole lines, so ``我方 打出支援「元素幻变」`` or
    ``我方 获得状态「换班时间」`` is not printed a second time.
    """
    if line in stated:
        return True
    return any(name in stated for name in _QUOTED.findall(line))


def _label_subject(text: str) -> str:
    """The object a candidate label names: ``打出卡牌：X → 我方·Y`` -> ``X``."""
    head, sep, tail = str(text or "").partition("：")
    if not sep:
        head, sep, tail = str(text or "").partition(":")
    if not sep:
        return ""
    return " ".join(tail.split("→", 1)[0].split()).strip()


def _own_action_name(label: str) -> tuple[bool, str]:
    """``(is_card_play, action name)`` of a recorded own label, or ``(False, "")``.

    ``record_own`` labels come from ``describe_candidate`` ("使用技能：好运剑",
    "普通攻击：好运剑" with the rich renderer). Only skill/card actions are kept,
    because only they have an engine event that would repeat them. A card play
    also fires a ``skill`` event carrying the *card* id, so it needs two
    consumptions where a skill needs one.
    """
    text = str(label or "").strip()
    head, sep, _tail = text.partition("：")
    if not sep:
        head, sep, _tail = text.partition(":")
    if not sep or head.strip() not in _OWN_ACTION_PREFIXES:
        return False, ""
    return head.strip() == "打出卡牌", _label_subject(text)


@dataclass
class _PendingOwn:
    """One recorded own action waiting for the engine event that restates it."""

    round_tag: str
    name: str
    card: bool = False
    skill_used: bool = False
    move_used: bool = False


def _normalize_name(text: str) -> str:
    """Case/space/quote-insensitive form used to compare two action names."""
    return (
        "".join(str(text).split())
        .replace("「", "")
        .replace("」", "")
        .lower()
    )


def _names_match(pending: str, event: str) -> bool:
    """True when an event name restates a recorded action name.

    Either may carry extra decoration (the owner prefix ``班尼特·`` in front, or a
    target suffix behind), so a containment match in both directions is used. An
    empty name never matches: an unknown event stays un-suppressed.
    """
    left, right = _normalize_name(pending), _normalize_name(event)
    if not left or not right:
        return False
    return left == right or left in right or right in left


def _names_in(line: str) -> set[str]:
    """Object names a history line states (``使用 X`` / ``「X」`` / ``打牌：X``)."""
    names = set(_QUOTED.findall(line))
    for marker in (" · 使用 ", " · 触发 "):
        if marker in line:
            names.add(line.split(marker, 1)[1].strip())
    tail = _label_subject(line)
    if tail and "「" not in tail and "」" not in tail:
        names.add(tail)
    names.discard("")
    return names


def _merge_repeats(action_lines: Sequence[str], state_lines: Sequence[str]) -> list[str]:
    """Collapse repeats, keeping the log free of duplicated lines.

    Action lines (``R…`` event / recorded lines) are merged with a ``×N`` count
    when adjacent: two identical skill triggers in one batch really happened
    twice. State lines carry no multiplicity (the same status may surface under
    two ids), so a duplicate -- adjacent or not, or one an action line already
    states -- is simply dropped.
    """
    merged: list[list[Any]] = []
    for line in action_lines:
        if merged and merged[-1][0] == line:
            merged[-1][1] += 1
        else:
            merged.append([line, 1])
    lines = [f"{line}（×{count}）" if count > 1 else line for line, count in merged]
    seen = set(lines)
    for line in state_lines:
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return lines


class MatchHistory:
    """Accumulates the public, in-game history of one match for the prompt."""

    def __init__(self) -> None:
        self._lines: list[str] = []
        # ``(seat, own_side, opponent_side)`` of the previous decision.
        self._prev: tuple[int, dict[str, Any], dict[str, Any]] | None = None
        # Own actions described by ``record_own`` whose engine event has not been
        # consumed yet. The *name* decides whether an event restates that action;
        # a bare count did not, so an unrelated own event in the same batch (a
        # passive trigger, an auto-played card) could eat the slot and let the
        # action print twice.
        self._pending_own: list[_PendingOwn] = []
        # Names our own recorded labels already stated since the previous
        # decision, so the following state diff does not repeat them.
        self._recent_own_names: set[str] = set()

    # -- public API ---------------------------------------------------------
    def consume(self, context: Any) -> list[str]:
        """Append every new public fact since the previous decision; return them."""
        state = getattr(context, "player_view", None)
        seat = int(getattr(context, "acting_player", 0) or 0)
        round_tag = _round_tag(state)
        events = _recent_events(context)

        action_lines = self._event_lines(events, seat, round_tag)
        state_lines: list[str] = []

        if state is not None:
            own_side = _side_public(state.players[seat], own=True)
            opp_side = _side_public(state.players[1 - seat], own=False)
            if self._prev is not None and self._prev[0] == seat:
                diff = _diff_lines(
                    self._prev[1], own_side, "我方", own=True, round_tag=round_tag
                )
                diff += _diff_lines(
                    self._prev[2], opp_side, "对方", own=False, round_tag=round_tag
                )
                # Hand traffic is already stated by the events (even when those
                # events were suppressed as duplicates of a recorded label), so
                # the diff must not repeat it as a count.
                diff = _drop_event_duplicates(diff, events, seat)
                # A consequence the events (or our own recorded label) already
                # name -- "使用 X" vs "获得「X」", "打出「X」" vs "打出支援「X」"
                # -- is stated once, not twice.
                stated = set(action_lines) | self._recent_own_names
                for line in action_lines:
                    stated |= _names_in(line)
                diff = [line for line in diff if not _is_stated(line, stated)]
                state_lines.extend(diff[:MAX_DIFF_LINES])
            self._prev = (seat, own_side, opp_side)
        else:
            self._prev = None

        self._pending_own = [
            entry
            for entry in self._pending_own
            # A skill event arrives in its own batch; only a card play may be
            # restated one decision later (the engine fires the card's effect as
            # a separate ``skill`` event), and never across a round boundary.
            if entry.card and entry.round_tag == round_tag
        ][:MAX_PENDING_OWN]
        self._recent_own_names = set()
        lines = _merge_repeats(action_lines, state_lines)
        lines = _dedupe_adjacent(_supersede([_clip(line) for line in lines]))
        if lines:
            # A newer dice list replaces the older one, in the batch and in the
            # stored log alike (the board already shows the current dice).
            self._lines = _dedupe_adjacent(_supersede([*self._lines, *lines]))
            if len(self._lines) > MAX_STORED_LINES:
                del self._lines[: len(self._lines) - MAX_STORED_LINES]
        return lines

    def record_own(self, context: Any, label: str) -> None:
        """Record this seat's own chosen action (LLM-only, see ``SkillAgentPolicy``)."""
        text = str(label or "").strip()
        if not text:
            return
        state = getattr(context, "player_view", None)
        # A label recorded without any view (offline harnesses) keeps the legacy
        # ``R0`` instead of ``R?``: there is no round to show, only a placeholder.
        round_tag = _round_tag(state) if state is not None else "0"
        line = _clip(f"R{round_tag} 我方 · {text}")
        if not (self._lines and self._lines[-1] == line):
            self._lines.append(line)
            if len(self._lines) > MAX_STORED_LINES:
                del self._lines[: len(self._lines) - MAX_STORED_LINES]
        self._recent_own_names |= _names_in(line)
        card, name = _own_action_name(text)
        if name:
            self._pending_own.append(_PendingOwn(round_tag, name, card=card))

    def _take_pending(self, name: str, round_number: str, *, move: bool = False) -> bool:
        """Consume the recorded own action this engine event restates.

        A skill event consumes one entry and that entry is gone, so two identical
        own skill events in one batch still show the second one (it is a second
        real use). A recorded *card* play is restated by two events -- the
        ``card_move`` that plays it and the ``skill`` event that carries the card
        id -- so such an entry survives its first consumption. The round only
        breaks ties between same-named entries; a name mismatch never suppresses.
        """
        if not name:
            return False
        fallback: int | None = None
        for index, entry in enumerate(self._pending_own):
            if entry.move_used if move else entry.skill_used:
                continue
            if not _names_match(entry.name, name):
                continue
            if entry.round_tag == round_number:
                self._consume_pending(index, move)
                return True
            if fallback is None:
                fallback = index
        if fallback is None:
            return False
        self._consume_pending(fallback, move)
        return True

    def _consume_pending(self, index: int, move: bool) -> None:
        entry = self._pending_own[index]
        if move:
            entry.move_used = True
        else:
            entry.skill_used = True
        if not entry.card or (entry.skill_used and entry.move_used):
            del self._pending_own[index]

    def render(self, max_lines: int = 12) -> str:
        """The prompt block, or ``""`` when nothing happened yet."""
        if not self._lines:
            return ""
        limit = max(1, int(max_lines))
        return _HEADER + "\n" + "\n".join(self._lines[-limit:])

    # -- internals ----------------------------------------------------------
    def _event_lines(self, events: Sequence[dict[str, Any]], seat: int, round_tag: str) -> list[str]:
        lines: list[str] = []
        own_draws: list[int] = []
        own_draw_round = round_tag
        own_draw_pos = 0
        opp_draws = 0
        opp_draw_round = round_tag
        opp_draw_pos = 0

        for event in events:
            kind = str(event.get("kind") or "")
            player = event.get("player")
            round_number = _event_round(event, round_tag)
            side = _side_label(player, seat)
            own = player is not None and int(player) == seat

            if kind == "skill":
                name = _name(int(event.get("definition_id") or 0))
                if own and self._take_pending(name, round_number):
                    # ``record_own`` already named this action (with its target);
                    # the raw event would be a second, poorer copy of the line.
                    continue
                if name:
                    lines.append(f"R{round_number} {side} · 使用 {name}")
            elif kind == "reaction":
                name = _reaction_name(event.get("reaction_type"))
                if name:
                    lines.append(f"R{round_number} {side} · 触发 {name}")
            elif kind == "card_move":
                if int(event.get("to") or 0) == _HAND_AREA:
                    if own:
                        if not own_draws:
                            own_draw_pos = len(lines)
                        own_draws.append(int(event.get("definition_id") or 0))
                        own_draw_round = round_number
                    elif player is not None:
                        if opp_draws == 0:
                            opp_draw_pos = len(lines)
                        opp_draws += 1
                        opp_draw_round = round_number
                    continue
                if not own:
                    # A card leaving the opponent's hand: the engine does not say
                    # which one, and the rules say we may not know. Their play is
                    # still visible through the ``skill`` event it produces.
                    continue
                reason = str(event.get("reason") or "")
                name = _name(int(event.get("definition_id") or 0))
                if _is_own_action_move(event) and self._take_pending(name, round_number, move=True):
                    continue
                if name:
                    verb = _LEAVE_VERBS.get(reason)
                    if verb is None:
                        verb = "打出" if int(event.get("to") or 0) in _PLAY_AREA else "离开手牌"
                    lines.append(f"R{round_number} 我方 · {verb}「{name}」")

        # The draw events are grouped into one line, but it is inserted where the
        # first draw happened so the log stays chronological.
        inserts: list[tuple[int, str]] = []
        if own_draws:
            names = "".join(f"「{_name(definition_id)}」" for definition_id in own_draws if _name(definition_id))
            if names:
                inserts.append((own_draw_pos, f"R{own_draw_round} 我方 · 抽到{names}"))
            else:
                inserts.append((own_draw_pos, f"R{own_draw_round} 我方 · 抽牌 {len(own_draws)} 张"))
        if opp_draws:
            inserts.append((opp_draw_pos, f"R{opp_draw_round} 对方 · 抽牌 {opp_draws} 张"))
        for offset, (position, line) in enumerate(sorted(inserts, key=lambda item: item[0])):
            lines.insert(min(position + offset, len(lines)), line)
        return lines


def _drop_event_duplicates(
    diff: Sequence[str], events: Sequence[dict[str, Any]], seat: int
) -> list[str]:
    """Drop diff lines the event log already states (hand traffic)."""
    hand_moves = {"own": False, "opp": False}
    for event in events:
        if event.get("kind") != "card_move":
            continue
        player = event.get("player")
        if player is None:
            continue
        hand_moves["own" if int(player) == seat else "opp"] = True

    kept = []
    for line in diff:
        body = _body(line)
        if hand_moves["own"] and (body.startswith("我方抽到") or body.startswith("我方手牌")):
            continue
        if hand_moves["opp"] and body.startswith("对方手牌"):
            continue
        kept.append(line)
    return kept


def _body(line: str) -> str:
    """A history line without its ``R{n}`` prefix."""
    head, _, rest = line.partition(" ")
    return rest if head.startswith("R") and rest else line


def _supersede(lines: Sequence[str]) -> list[str]:
    """Drop the older line of an absolute state fact (the dice list).

    Roll, reroll and tuning all restate the whole dice list; only the last one
    describes the dice the model is about to spend.
    """
    kept: list[str] = []
    for line in lines:
        body = _body(line)
        prefix = next((p for p in _SUPERSEDED_PREFIXES if body.startswith(p)), "")
        if prefix:
            kept = [old for old in kept if not _body(old).startswith(prefix)]
        kept.append(line)
    return kept


def _dedupe_adjacent(lines: Sequence[str]) -> list[str]:
    """Two identical lines in a row are one fact: keep the first."""
    kept: list[str] = []
    for line in lines:
        if kept and kept[-1] == line:
            continue
        kept.append(line)
    return kept
