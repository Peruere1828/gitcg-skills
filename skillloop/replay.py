"""Compact, deterministic single-game traces for skill extraction.

Aggregate statistics say *how often* a stage fails; a short replay says *where*
the play went wrong. The trace is deliberately lossy: one line per meaningful
action (plus the first mechanical decision of each kind), every elemental
reaction, and one closing scoreboard line. It is built from the public
``DecisionRecord`` / ``MatchEvent`` projections that ``envs.match.run_match``
already records -- never from a full ``GameState``.

The output is stable for a given match: ranking/tie-breaking happens upstream in
``skillloop.extract``, and this module preserves recorder order.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Iterable, Sequence

# Action kinds shown as one short Chinese label. Unknown kinds fall back to the
# ``chosen_label`` prefix (e.g. ``use_skill``) so a new engine kind cannot crash
# extraction.
_KIND_TEXT = {
    "action_use_skill": "技能",
    "action_play_card": "打牌",
    "action_switch_active": "切人",
    "action_elemental_tuning": "调和",
    "action_declare_end": "结束",
}

# Mechanical requests: keep only the first occurrence per (player, type), so the
# reader sees the opening mulligan/reroll without drowning in UI noise.
_FIRST_ONLY = {
    "reroll_dice": "重掷",
    "switch_hands": "换牌",
    "choose_active": "选人",
}
_IGNORED_REQUESTS = frozenset({"select_card", "terminal"})

_MAX_LABEL = 28
_MAX_DICE = 8


@lru_cache(maxsize=1)
def _catalog() -> Any:
    """The renderer's asset catalog, loaded at most once per process.

    ``load_assets`` would hit the network when its cache is missing; traces are
    also used in offline tests, so return ``None`` instead and fall back to ids.
    """
    try:
        from agents.scripted.assets import _cache_path, load_assets

        if not _cache_path().exists():
            return None
        return load_assets()
    except Exception:
        return None


@lru_cache(maxsize=1)
def _skill_owner_map() -> dict[int, str]:
    """Character name for each of its skills (catalog skills lack owners)."""
    catalog = _catalog()
    if catalog is None:
        return {}
    owners: dict[int, str] = {}
    try:
        for character in catalog.characters.values():
            for skill_id in getattr(character, "skill_ids", ()):
                owners[int(skill_id)] = str(character.name)
    except Exception:
        return {}
    return owners


@lru_cache(maxsize=None)
def _skill_name(definition_id: int) -> str:
    catalog = _catalog()
    if catalog is not None:
        try:
            skill = catalog.skill(int(definition_id))
        except (KeyError, TypeError, ValueError):
            skill = None
        if skill is not None:
            owner = _skill_owner_map().get(int(definition_id)) or str(
                getattr(skill, "character_name", "") or ""
            )
            name = f"{owner}·{skill.name}".strip("·")
            if name:
                return name
    return f"#{int(definition_id)}"


@lru_cache(maxsize=None)
def _card_name(definition_id: int) -> str:
    catalog = _catalog()
    if catalog is not None:
        try:
            card = catalog.card(int(definition_id))
        except (KeyError, TypeError, ValueError):
            card = None
        if card is not None and card.name:
            return str(card.name)
    return f"#{int(definition_id)}"


@lru_cache(maxsize=None)
def _character_name(definition_id: int) -> str:
    catalog = _catalog()
    if catalog is not None:
        try:
            character = catalog.character(int(definition_id))
        except (KeyError, TypeError, ValueError):
            character = None
        if character is not None and character.name:
            return str(character.name)
    return f"#{int(definition_id)}"


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """Read a field from a dataclass-like record or its dict projection."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _round_of(decision: Any, fallback: int) -> int:
    """Prefer the recorded player-view round; otherwise use the running counter."""
    view = _field(decision, "player_view")
    if isinstance(view, dict):
        raw = view.get("round_number")
        if raw is not None:
            return _as_int(raw, fallback)
    return fallback


def _side(player: Any) -> str:
    """``P0`` is always the contestant; ``P1`` is the NPC in this project."""
    number = _as_int(player, -1)
    if number == 0:
        return "P0"
    if number == 1:
        return "P1(NPC)"
    return f"P{number}" if number >= 0 else "P?"


def _dice_text(decision: Any) -> str:
    dice = _field(decision, "used_dice", ()) or ()
    try:
        values = [_as_int(value) for value in dice][:_MAX_DICE]
    except TypeError:
        return ""
    if not values:
        return ""
    return " 骰" + ",".join(str(value) for value in values)


def _label_head(label: str) -> str:
    head = label.split(":", 1)[0].strip()
    return head[:_MAX_LABEL] if head else "?"


def _action_line(round_number: int, player: Any, decision: Any) -> str:
    kind = str(_field(decision, "chosen_kind") or "")
    label = str(_field(decision, "chosen_label") or "")
    subject = _as_int(_field(decision, "subject_definition_id", 0))
    card_kind = str(_field(decision, "card_kind") or "")

    if kind not in _KIND_TEXT and label:
        # ``chosen_label`` looks like ``use_skill:<action_index>:<dice>``; the
        # head maps back to a kind even when ``chosen_kind`` is absent.
        head = _label_head(label)
        if "action_" + head in _KIND_TEXT:
            kind = "action_" + head
    kind_text = _KIND_TEXT.get(kind)
    if kind_text is None:
        kind_text = _label_head(label) if label else "?"

    detail = ""
    if kind == "action_use_skill" and subject:
        detail = f" {_skill_name(subject)}"
    elif kind == "action_play_card":
        name = _card_name(subject) if subject else ""
        if name:
            detail = f" {name}"
        if card_kind:
            detail += f" ({card_kind})"
    elif kind == "action_switch_active" and subject:
        detail = f" {_character_name(subject)}"
    elif kind == "action_elemental_tuning" and subject:
        detail = f" {_card_name(subject)}"
    elif subject:
        detail = f" #{subject}"

    return f"R{round_number} {_side(player)} {kind_text}{detail}{_dice_text(decision)}"


def _mechanical_line(round_number: int, player: Any, request: str, decision: Any) -> str:
    kind_text = _FIRST_ONLY[request]
    label = str(_field(decision, "chosen_label") or "")
    tail = label.split(":", 1)[1].strip() if ":" in label else ""
    if tail.lower() == "none":
        tail = ""
    if not tail:
        subject = _as_int(_field(decision, "subject_definition_id", 0))
        tail = str(subject) if subject > 0 else ""
    # ``choose_active``'s label carries an internal entity id (negative), which
    # means nothing to the reader; keep just "选人".
    if request == "choose_active":
        tail = ""
    return f"R{round_number} {_side(player)} {kind_text}{(' ' + tail[:_MAX_LABEL]) if tail else ''}"


def _reaction_lines(events: Iterable[Any]) -> list[str]:
    """Reaction events in recorder order, coalescing consecutive duplicates."""
    lines: list[str] = []
    previous: str | None = None
    count = 0
    for event in events:
        if str(_field(event, "kind", "reaction") or "") != "reaction":
            continue
        name = str(_field(event, "reaction_type") or "?").strip() or "?"
        round_number = _as_int(_field(event, "round_number", 0))
        player = _field(event, "player")
        prefix = f"R{round_number if round_number > 0 else '?'} 反应 {name}"
        if player is not None:
            prefix += f" {_side(player)}"
        if prefix == previous:
            count += 1
            continue
        if previous is not None:
            lines.append(previous + (f" x{count}" if count > 1 else ""))
        previous, count = prefix, 1
    if previous is not None:
        lines.append(previous + (f" x{count}" if count > 1 else ""))
    return lines


def _final_line(row: dict[str, Any]) -> str:
    winner = row.get("winner")
    if winner == 0:
        outcome = "winner=0（我方胜）"
    elif winner == 1:
        outcome = "winner=1（对方胜）"
    elif winner is None:
        outcome = "winner=None"
    else:
        outcome = f"winner={winner}"
    rounds = row.get("rounds")
    rounds_text = str(rounds) if rounds is not None else "?"

    objectives = row.get("objectives")
    if isinstance(objectives, dict) and objectives:
        marks = " ".join(
            f"{name}{'✔' if achieved else '✘'}" for name, achieved in objectives.items()
        )
    else:
        marks = "无记录"
    return f"结局 {outcome}，{rounds_text} 回合；目标 {marks}"


def compact_trace(
    row: dict[str, Any],
    decisions: Sequence[Any] | None,
    events: Sequence[Any] | None,
    *,
    max_actions: int = 40,
    meta: dict[str, Any] | None = None,
) -> list[str]:
    """Render one match as at most ``max_actions`` short lines.

    * one line per ``action`` decision for both sides (``P0`` = contestant,
      ``P1(NPC)`` = the stage enemy), in recorder order;
    * the first ``reroll_dice`` / ``switch_hands`` / ``choose_active`` per player;
    * reaction events, coalesced when consecutive;
    * one closing line with winner, rounds and objective marks (always kept).

    ``meta`` (optional out-parameter) receives ``n_action_lines`` and
    ``truncated`` so the runner can record why a trace was cut. Missing labels,
    round numbers, dice, or a malformed row never raise.
    """
    decisions = list(decisions or ())
    events = list(events or ())

    action_lines: list[str] = []
    seen_mechanical: set[tuple[int, str]] = set()
    ended: set[int] = set()
    round_number = 1

    for decision in decisions:
        player = _as_int(_field(decision, "player", 0))
        round_number = max(1, _round_of(decision, round_number))
        request = str(_field(decision, "request_type", "") or "")
        kind = str(_field(decision, "chosen_kind", "") or "")

        if request == "action" or kind.startswith("action_"):
            action_lines.append(_action_line(round_number, player, decision))
        elif request in _FIRST_ONLY:
            key = (player, request)
            if key in seen_mechanical:
                continue
            seen_mechanical.add(key)
            action_lines.append(_mechanical_line(round_number, player, request, decision))
        elif request in _IGNORED_REQUESTS:
            continue
        elif not request and not kind:
            # Unknown/empty record: keep it visible instead of silently dropping it.
            action_lines.append(f"R{round_number} {_side(player)} ?")
        else:
            continue

        # Round bookkeeping is only needed when no player_view was recorded.
        if kind == "action_declare_end":
            ended.add(player)
            if len(ended) >= 2:
                round_number += 1
                ended.clear()

    reaction_lines = _reaction_lines(events)
    body = action_lines + reaction_lines
    cap = max(1, _as_int(max_actions, 40))
    truncated = len(body) > cap - 1
    body = body[: cap - 1]

    if meta is not None:
        meta["n_action_lines"] = len(action_lines)
        meta["n_lines"] = len(body) + 1
        meta["truncated"] = truncated

    return body + [_final_line(row)]
