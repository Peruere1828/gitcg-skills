"""Compact, deterministic single-game traces for skill extraction.

Aggregate statistics say *how often* a stage fails; a short replay says *where*
the play went wrong. The trace is deliberately lossy: one line per meaningful
action (plus the first mechanical decision of each kind), every elemental
reaction, and one closing scoreboard line. It is built from the public
``DecisionRecord`` / ``MatchEvent`` projections that ``envs.match.run_match``
already records -- never from a full ``GameState``.

The output is stable for a given match: ranking/tie-breaking happens upstream in
``skillloop.extract``, and this module preserves recorder order.

Two optional enrichments read only recorded player views (never a full game
state):

* dice are rendered as element symbols. On an action line they are the dice the
  engine *paid* for the action (``自动支付[雷,雷,万]``), which is not the same thing
  as the dice a player chose to reroll (``重掷 [水,水]``); the two labels are kept
  distinct so a reader cannot mistake an auto-selected cost for a live pool, and
  every mechanical row says it came from the script policy, not the LLM (H10);
* when the recorder captured ``DecisionRecord.player_view`` (``record_views=True``),
  every action line gets a bounded ``→ 此后场上：...`` suffix describing the
  public-state delta up to the *next* recorded decision. Games recorded without
  views keep exactly the old line set (no suffix, no error).

A recorded final state (``final_state=``) adds the closing ``最终：...`` board and
completes the last action's delta, which the next-view pairing cannot see (H9); a
trace cut by ``max_actions`` says so on its second line.
"""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
from typing import Any, Iterable, Sequence

from skillloop.render import _DICE_SYMBOL

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
# reader sees the opening mulligan/choose-active without drowning in UI noise.
# ``reroll_dice`` is the exception: one line is kept per player *per round*
# (see ``compact_trace``) because "what did each side reroll this round" stays
# useful in later rounds.
_MECHANICAL_TEXT = {
    "reroll_dice": "重掷",
    "switch_hands": "换牌",
    "choose_active": "选人",
}
#: H10: mechanical rows are labelled by who actually chose them: the LLM (sync
#: runner's ``switch_hands``/``choose_active``) or the script policy (every
#: ``reroll_dice``, plus all fallback/forced picks).
_MECHANICAL_NOTE_LLM = "（LLM 决策）"
_MECHANICAL_NOTE_SCRIPT = "（脚本策略）"
#: Hand area constant from the engine's ``EntityArea`` enum.
_HAND_AREA = 5
_IGNORED_REQUESTS = frozenset({"select_card", "terminal"})

_MAX_LABEL = 28
_MAX_DICE = 8
#: A roll line may list at most this many symbols before ``（N个）``.
_MAX_DICE_LINE = 16
#: At most this many newly drawn card names per round-boundary line.
_MAX_HAND_ADD = 4

#: Engine ``ReactionType`` enum names (lowercased) -> readable Chinese. Unknown
#: values keep their raw name with a trailing ``?`` so a new enum cannot crash the
#: trace and is still greppable.
_REACTION_TEXT = {
    "melt": "融化",
    "vaporize": "蒸发",
    "overloaded": "超载",
    "superconduct": "超导",
    "electro_charged": "感电",
    "frozen": "冻结",
    "swirl_cryo": "扩散（冰）",
    "swirl_hydro": "扩散（水）",
    "swirl_pyro": "扩散（火）",
    "swirl_electro": "扩散（雷）",
    "crystallize_cryo": "结晶（冰）",
    "crystallize_hydro": "结晶（水）",
    "crystallize_pyro": "结晶（火）",
    "crystallize_electro": "结晶（雷）",
    "burning": "燃烧",
    "bloom": "绽放",
    "quicken": "原激化",
    "lunar_electro_charged": "月感电",
    "lunar_bloom": "月绽放",
}
#: Upper bound for one skill description appended to an action line.
_MAX_DESCRIPTION = 40
#: Asset names for "statuses" sometimes carry a transient phase marker; the
#: marker is engine bookkeeping, not part of the status identity.
_STATUS_NAME_SUFFIXES = ("（已创建）", "（生效中）")
_CARD_KIND_TEXT = {
    "weapon": "武器",
    "artifact": "圣遗物",
    "talent": "天赋",
    "event": "事件",
    "support": "支援",
}
#: Upper bound for one ``→ 此后场上：...`` effect suffix (including the arrow).
_MAX_EFFECT = 120
#: At most this many public changes per line; the rest collapse into ``等N项``.
_MAX_EFFECT_ITEMS = 6
#: At most this many preview entries (hand create/remove/attach) per action line.
_MAX_PREVIEW_ITEMS = 4
_EFFECT_LEAD = "→ 此后场上："
_SIDE_TEXT = {0: "我方", 1: "对方"}
#: H9: cap for the one-line ``最终：...`` HP board.
_MAX_FINAL = 160
#: Entity variables that count as "remaining uses / stacks / duration" for the
#: suffix. ``duration`` is included because NPC-only statuses (e.g. 水光破镜)
#: expose their remaining rounds there instead of ``usage``.
_STATUS_VALUE_KEYS = (
    "usage", "usages",
    "level", "stack", "layer",
    "count", "shield",
    "duration",
)
#: Unit printed with the counter, so ``(2)`` cannot be mistaken for damage. A bare
#: ``(2)`` stays for the keys whose meaning is already the number itself.
_STATUS_VALUE_UNITS = {
    "usage": "次",
    "usages": "次",
    "level": "层",
    "stack": "层",
    "layer": "层",
    "duration": "回合",
    "count": "",
    "shield": "",
}


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


@lru_cache(maxsize=1)
def _npc_skill_table() -> dict[int, tuple[str, str]]:
    """NPC skill definition id -> ``(owner·skill name, official effect text)``.

    Overlay enemies are absent from the official asset catalog. ``configs/npc``
    maps an enemy's engine id to its A/E/Q skill entries; the engine skill id is
    ``engine_id * 10 + slot`` (see ``NpcEnemy.skill_definition_ids``). Failure to
    load the config just yields an empty table, so traces fall back to ``#id``.
    """
    try:
        from npc.spec import load_enemies
    except Exception:
        return {}
    table: dict[int, tuple[str, str]] = {}
    try:
        for enemy in load_enemies().values():
            owner = str(enemy.name)
            for kind, definition_id in enemy.skill_definition_ids().items():
                skill = enemy.skills.get(kind)
                if skill is None:
                    continue
                name = str(skill.name_cn or skill.name or "").strip()
                table[int(definition_id)] = (
                    f"{owner}·{name}".strip("·"),
                    str(skill.effect or ""),
                )
            for definition_id, skill in zip(
                enemy.extra_skill_definition_ids(), enemy.extra_skills
            ):
                name = str(skill.name_cn or skill.name or "").strip()
                table[int(definition_id)] = (
                    f"{owner}·{name}".strip("·"),
                    str(skill.effect or ""),
                )
    except Exception:
        return {}
    return table


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
    npc_skill = _npc_skill_table().get(int(definition_id))
    if npc_skill is not None and npc_skill[0]:
        return npc_skill[0]
    return f"#{int(definition_id)}"


def _short_description(text: Any) -> str:
    """One-line, trailing-period-free skill effect capped at 40 characters."""
    cleaned = " ".join(str(text or "").split()).rstrip("。.")
    if not cleaned:
        return ""
    if len(cleaned) > _MAX_DESCRIPTION:
        return cleaned[: _MAX_DESCRIPTION - 1] + "…"
    return cleaned


@lru_cache(maxsize=None)
def _skill_description(definition_id: int) -> str:
    """Official effect text for a skill (catalog first, then ``configs/npc``)."""
    catalog = _catalog()
    if catalog is not None:
        try:
            skill = catalog.skill(int(definition_id))
        except (KeyError, TypeError, ValueError):
            skill = None
        if skill is not None:
            description = _short_description(getattr(skill, "description", ""))
            if description:
                return description
    npc_skill = _npc_skill_table().get(int(definition_id))
    if npc_skill is not None:
        return _short_description(npc_skill[1])
    return ""


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


@lru_cache(maxsize=1)
def _npc_character_names() -> dict[int, str]:
    """Display names for overlay enemies, whose ids are absent from the catalog.

    ``configs/npc`` maps ``NpcEnemy.engine_id`` to a name; the lookup is lazy and
    failure-tolerant so replay works in an environment without config data.
    """
    try:
        from npc.spec import load_enemies

        return {
            int(enemy.engine_id): str(enemy.name)
            for enemy in load_enemies().values()
            if int(enemy.engine_id or 0)
        }
    except Exception:
        return {}


def _effect_character_name(definition_id: int) -> str:
    """Catalog name, falling back to the NPC config, then a neutral placeholder."""
    name = _character_name(definition_id)
    if name.startswith("#"):
        name = _npc_character_names().get(int(definition_id), name)
    return "未知角色" if name.startswith("#") else name


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
    """Dice the engine *paid* for this action, as `` 自动支付[雷,雷,万]``.

    A reroll label also carries dice ids, but those are the dice a player chose
    to discard (rendered separately as ``重掷 [...]``). Calling this suffix
    ``自动支付`` states what it is: ``used_dice`` is the engine's auto-selected
    cost for the chosen action (the model picked the action, not the dice), at
    most :data:`_MAX_DICE` values in recorder order.

    Values map through the renderer's ``_DICE_SYMBOL`` (1 冰, 2 水, 3 火, 4 雷,
    5 风, 6 岩, 7 草, 8 万; 0 is the engine's "unspecified" and renders as ``?``).
    A value outside that table keeps its decimal number, so a future element or a
    malformed record cannot crash the trace. No paid dice -> empty string.
    """
    dice = _field(decision, "used_dice", ()) or ()
    try:
        values = [_as_int(value) for value in dice][:_MAX_DICE]
    except TypeError:
        return ""
    if not values:
        return ""
    return " 自动支付[" + ",".join(_dice_symbol(value) for value in values) + "]"


def _dice_symbol(value: Any) -> str:
    return _DICE_SYMBOL.get(_as_int(value), str(value))


def _view_round(decision: Any) -> int | None:
    """The explicitly recorded view round (``0`` is the pre-game init phase)."""
    view = _field(decision, "player_view")
    if not isinstance(view, dict):
        return None
    raw = view.get("round_number")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _view_dice(decision: Any) -> list[int] | None:
    """The acting player's own dice pool from this decision's view.

    Only a real snapshot (``players`` present) counts: test/legacy views may carry
    just ``round_number``, in which case the dice are unknown and no row is added.
    Each seat's view shows its own dice, so P1's pool is read from P1's view (the
    contestant's view masks it).
    """
    view = _field(decision, "player_view")
    if not isinstance(view, dict):
        return None
    players = _view_players(view)
    if len(players) < 2:
        return None
    player = _as_int(_field(decision, "player", 0))
    if player < 0 or player >= len(players):
        return None
    dice = players[player].get("dice")
    if not isinstance(dice, (list, tuple)):
        return []
    values: list[int] = []
    for value in dice:
        number = _as_int(value)
        if number:
            values.append(number)
    return values


def _dice_pool_line(round_number: int, player: int, dice: Sequence[int]) -> str:
    """``R1 我方骰子：[火,雷]（2个）`` / ``R1 对方骰子：（无）``."""
    side = _SIDE_TEXT.get(player, f"P{player}")
    shown = [_dice_symbol(value) for value in dice[:_MAX_DICE_LINE]]
    body = (
        "[" + ",".join(shown) + f"]（{len(dice)}个）" if shown else "（无）"
    )
    return f"R{round_number} {side}骰子：{body}"


def _hand_entry_definition_id(card: Any) -> int:
    """Definition id from a snapshot hand card (dict), or 0 (incl. masked NPC)."""
    if isinstance(card, dict):
        return _as_int(card.get("definition_id") or card.get("card_definition_id"))
    return _as_int(card)


def _hand_entry_id(card: Any) -> int:
    """Entity id from a snapshot hand card (dict), or 0 when unavailable."""
    if isinstance(card, dict):
        return _as_int(card.get("id"))
    return 0


def _view_hand_entries(decision: Any, player: int) -> list[Any] | None:
    """One seat's hand from a real view (``None`` when the view is absent)."""
    view = _field(decision, "player_view")
    if not isinstance(view, dict):
        return None
    players = _view_players(view)
    if player < 0 or player >= len(players):
        return None
    hand = players[player].get("hand_cards")
    if not isinstance(hand, (list, tuple)):
        return None
    return list(hand)


def _view_hand_defs(decision: Any, player: int) -> list[int] | None:
    """One seat's hand definition ids in order (0 marks a masked card)."""
    entries = _view_hand_entries(decision, player)
    if entries is None:
        return None
    return [_hand_entry_definition_id(card) for card in entries]


def _view_hand_ids(decision: Any) -> list[int] | None:
    """P0's hand definition ids from a real view (``None`` when unavailable).

    The contestant's view is the only one whose hand-card ids are trustworthy;
    the NPC's hand is masked, so it is never read into a drawn-card line.
    """
    defs = _view_hand_defs(decision, 0)
    if defs is None:
        return None
    return [definition_id for definition_id in defs if definition_id]


def _hand_additions(previous: Sequence[int], current: Sequence[int]) -> list[int]:
    """Multiset difference ``current - previous`` in current-hand order."""
    remaining = Counter(previous)
    added: list[int] = []
    for definition_id in current:
        if remaining[definition_id] > 0:
            remaining[definition_id] -= 1
        else:
            added.append(definition_id)
    return added


def _hand_add_line(round_number: int, definition_ids: Sequence[int]) -> str:
    """``R2 手牌新增：「X」、「Y」``; unresolvable ids are omitted, max 4 names."""
    names: list[str] = []
    for definition_id in definition_ids:
        name = _card_name(int(definition_id))
        if name.startswith("#"):
            continue
        names.append(name)
        if len(names) >= _MAX_HAND_ADD:
            break
    if not names:
        return ""
    return f"R{round_number} 手牌新增：" + "、".join(f"「{name}」" for name in names)


def _next_same_seat_view(
    decisions: Sequence[Any], index: int, player: int
) -> int | None:
    """Index of the next record for ``player`` that carries a real view."""
    for candidate in range(index + 1, len(decisions)):
        other = decisions[candidate]
        if _as_int(_field(other, "player", -1)) != player:
            continue
        view = _field(other, "player_view")
        if isinstance(view, dict) and len(_view_players(view)) >= 2:
            return candidate
    return None


def _dice_values(values: Any) -> list[int]:
    """Strict int list from a view dice tuple (drops the engine's 0 filler)."""
    if not isinstance(values, (list, tuple)):
        return []
    result: list[int] = []
    for value in values:
        number = _as_int(value)
        if number:
            result.append(number)
    return result


def _dice_line(values: Sequence[int]) -> str:
    """``[水,水,雷]`` with the shared element symbols, capped like a pool row."""
    return "[" + ",".join(_dice_symbol(value) for value in values[:_MAX_DICE_LINE]) + "]"


def _label_tail(decision: Any) -> str:
    label = str(_field(decision, "chosen_label") or "")
    return label.split(":", 1)[1].strip() if ":" in label else ""


def _switch_removed_ids(decision: Any) -> list[int]:
    """Entity ids from ``switch_hands:<entity>,<entity>`` (negatives included)."""
    tail = _label_tail(decision)
    if not tail or tail.lower() == "none":
        return []
    ids: list[int] = []
    for part in tail.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            continue
    return ids


def _card_move_events(events: Sequence[Any], *, kind: str | None = None) -> list[Any]:
    result: list[Any] = []
    for event in events:
        if str(_field(event, "kind", "") or "") != "card_move":
            continue
        if kind is not None and str(_field(event, "reason", "") or "") != kind:
            continue
        result.append(event)
    return result


def _event_defs(events: Sequence[Any], player: int) -> tuple[list[int], bool]:
    """Definition ids entering the hand; ``masked`` when an NPC card is hidden."""
    definition_ids: list[int] = []
    masked = False
    for event in events:
        if player is not None and _as_int(_field(event, "player", -1)) != player:
            continue
        definition_id = _as_int(_field(event, "definition_id", 0))
        if definition_id == 0:
            masked = True
        definition_ids.append(definition_id)
    return definition_ids, masked


def _draw_line(round_number: int, player: int, events: Sequence[Any]) -> str:
    """``R1 P0 抽牌：「雷楔」、「莲花酥」`` / ``R1 P1(NPC) 抽牌 2 张``."""
    if not events:
        return ""
    definition_ids, masked = _event_defs(events, player)
    if not definition_ids:
        return ""
    side = _side(player)
    if player == 1 or masked:
        return f"R{round_number} {side} 抽牌 {len(definition_ids)} 张"
    names = [_card_name(int(definition_id)) for definition_id in definition_ids]
    return f"R{round_number} {side} 抽牌：" + "、".join(f"「{name}」" for name in names)


def _draw_buckets(events: Sequence[Any]) -> dict[int, list[Any]]:
    """Card entries into hand (excluding switch gains) keyed by decision cursor."""
    buckets: dict[int, list[Any]] = {}
    for event in _card_move_events(events):
        if _as_int(_field(event, "to_area", 0)) != _HAND_AREA:
            continue
        # Switch-hands gains are stated by the switch row itself; drawing them
        # again would duplicate the same cards.
        if str(_field(event, "reason", "") or "") == "switch":
            continue
        after = max(0, _as_int(_field(event, "decision_index", 0)))
        buckets.setdefault(after, []).append(event)
    return buckets


def _draw_lines(bucket: Sequence[Any]) -> list[str]:
    """One draw row per seat/round, in event order, for one notification batch."""
    lines: list[str] = []
    order: list[tuple[int, int]] = []
    grouped: dict[tuple[int, int], list[Any]] = {}
    for event in bucket:
        player = _as_int(_field(event, "player", 0))
        round_number = max(0, _as_int(_field(event, "round_number", 0)))
        key = (player, round_number)
        if key not in grouped:
            order.append(key)
            grouped[key] = []
        grouped[key].append(event)
    for key in order:
        player, round_number = key
        line = _draw_line(round_number, player, grouped[key])
        if line:
            lines.append(line)
    return lines


def _switch_gains(
    decision: Any,
    *,
    index: int,
    decisions: Sequence[Any],
    events: Sequence[Any],
    player: int,
) -> tuple[list[int], bool]:
    """Cards that entered the hand after a mulligan: view diff first, events next."""
    current = _view_hand_defs(decision, player)
    next_index = _next_same_seat_view(decisions, index, player)
    if current is not None and next_index is not None:
        following = _view_hand_defs(decisions[next_index], player)
        if following is not None:
            masked = any(value == 0 for value in (*current, *following))
            if not masked:
                return _hand_additions(current, following), False
    candidates = [
        event
        for event in _card_move_events(events, kind="switch")
        if _as_int(_field(event, "to_area", 0)) == _HAND_AREA
        and _as_int(_field(event, "player", -1)) == player
        and _as_int(_field(event, "decision_index", 0)) in (0, index + 1)
    ]
    found, masked = _event_defs(candidates, player)
    if not found:
        return [], masked
    return [definition_id for definition_id in found if definition_id], masked


def _switch_line(
    round_number: int,
    player: int,
    decision: Any,
    *,
    index: int,
    decisions: Sequence[Any],
    events: Sequence[Any],
) -> str:
    """``R1 P0 换牌：弃置「A」、「B」→ 获得「C」、「D」``."""
    removed = _switch_removed_ids(decision)
    if not removed:
        return ""
    entries = _view_hand_entries(decision, player)
    by_id = {
        _hand_entry_id(card): card
        for card in (entries or ())
        if _hand_entry_id(card)
    }
    names: list[str] = []
    masked = False
    for entity_id in removed:
        card = by_id.get(entity_id)
        if card is None:
            if player == 1:
                masked = True
            else:
                names.append(f"#{entity_id}")
            continue
        definition_id = _hand_entry_definition_id(card)
        if definition_id == 0:
            masked = True
        else:
            names.append(_card_name(definition_id))
    if masked or (player == 1 and not names):
        return f"R{round_number} {_side(player)} 换牌 {len(removed)} 张"
    if not names:
        return ""
    text = "弃置" + "、".join(f"「{name}」" for name in names)
    gains, gains_masked = _switch_gains(
        decision, index=index, decisions=decisions, events=events, player=player
    )
    if gains:
        text += "→ 获得" + "、".join(f"「{_card_name(gid)}」" for gid in gains)
    elif gains_masked:
        text += f"→ 获得 {len(removed)} 张"
    return f"R{round_number} {_side(player)} 换牌：{text}"


def _reroll_line(
    round_number: int,
    player: int,
    decision: Any,
    *,
    index: int,
    decisions: Sequence[Any],
) -> str:
    """``R1 P0 重掷 [旧] → [新]``; a ``none`` reroll is omitted as pure noise."""
    tail = _label_tail(decision)
    if not tail or tail.lower() == "none":
        return ""
    old_dice = _dice_values(_view_dice(decision))
    old_text = _dice_line(old_dice) if old_dice else _reroll_tail(tail)
    if not old_text:
        return ""
    body = f"R{round_number} {_side(player)} 重掷 {old_text}"
    next_index = _next_same_seat_view(decisions, index, player)
    if next_index is not None:
        new_dice = _dice_values(_view_dice(decisions[next_index]))
        if new_dice:
            body += " → " + _dice_line(new_dice)
    return body


def _choose_active_name(decision: Any, player: int) -> str:
    """Character name for a choose-active pick, or ``""`` when unresolvable."""
    subject = _as_int(_field(decision, "subject_definition_id", 0))
    names: list[str] = []
    if subject > 0:
        names.append(subject)
    tail = _label_tail(decision)
    if tail:
        entity_id = _as_int(tail, 0)
        view = _field(decision, "player_view")
        players = _view_players(view)
        if 0 <= player < len(players):
            for character in _characters(players[player]):
                if _as_int(character.get("id")) == entity_id:
                    definition_id = _as_int(character.get("definition_id"))
                    if definition_id:
                        names.append(definition_id)
                    break
    for definition_id in names:
        name = _character_name(int(definition_id))
        if name.startswith("#"):
            name = _npc_character_names().get(int(definition_id), "")
        if name and not name.startswith("#") and name != "未知角色":
            return name
    return ""


def _mechanical_note(request: str, decision: Any) -> str:
    if request == "reroll_dice":
        return _MECHANICAL_NOTE_SCRIPT
    decider = str(_field(decision, "decider", "script") or "script")
    return _MECHANICAL_NOTE_LLM if decider == "llm" else _MECHANICAL_NOTE_SCRIPT


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
        description = _skill_description(subject)
        if description:
            detail += f"（{description}）"
    elif kind == "action_play_card":
        name = _card_name(subject) if subject else ""
        if name:
            detail = f" {name}"
        if card_kind:
            detail += f" ({_CARD_KIND_TEXT.get(card_kind, card_kind)})"
    elif kind == "action_switch_active" and subject:
        detail = f" {_character_name(subject)}"
    elif kind == "action_elemental_tuning" and subject:
        detail = f" {_card_name(subject)}"
    elif subject:
        detail = f" #{subject}"

    return f"R{round_number} {_side(player)} {kind_text}{detail}{_dice_text(decision)}"


def _preview_name(definition_id: int) -> str | None:
    """Name for a preview entity, or ``None`` when only ``#id`` is resolvable.

    NPC/overlay entities are namespaced at ``official_id + 70000`` (same rule as
    ``_entity_name``), so the offset is tried before giving up. Returning
    ``None`` (instead of ``#id``) lets the caller omit an unresolvable entry.
    """
    name = _card_name(int(definition_id))
    if not name.startswith("#"):
        return name
    if int(definition_id) > 70000:
        variant = _card_name(int(definition_id) - 70000)
        if not variant.startswith("#"):
            return variant
    return None


def _preview_effect(decision: Any) -> str:
    """``；手牌：弃置「雷楔」、附属「雷元素附魔」`` from the action's own preview.

    ``DecisionRecord.preview`` is the engine's forecast for the chosen action
    (projected by ``reps.action_adapter._action_preview``). It explains a hand
    change the state delta cannot: e.g. 星斗归位 replaces an existing 雷楔 with
    雷元素附魔, so the hand drops from 7 to 6 with no visible cause otherwise.

    Only ``hand_remove`` / ``hand_add`` / ``attach`` entries are stated; a name
    that cannot be resolved is dropped rather than emitting a raw ``#id``. At
    most :data:`_MAX_PREVIEW_ITEMS` entries are listed. The leading ``；`` keeps
    this segment distinct from the ``→ 此后场上`` state delta.
    """
    raw = _field(decision, "preview", ()) or ()
    parts: list[str] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind") or "")
        definition_id = _as_int(entry.get("definition_id"))
        if not definition_id or kind not in ("hand_remove", "hand_add", "attach"):
            continue
        name = _preview_name(definition_id)
        if name is None:
            continue
        if kind == "hand_remove":
            parts.append(f"手牌：弃置「{name}」")
        elif kind == "hand_add":
            parts.append(f"手牌：获得「{name}」")
        else:
            parts.append(f"附属「{name}」")
        if len(parts) >= _MAX_PREVIEW_ITEMS:
            break
    if not parts:
        return ""
    return "；" + "、".join(parts)


def _reroll_tail(tail: str) -> str:
    """``2,2,7`` -> ``[水,水,草]``; ``none``/empty stays empty.

    The reroll label carries the dice values the player chose to reroll. They are
    the same element ids as ``used_dice``, so render them with the shared symbol
    table and the same 8-die cap as action lines.
    """
    parts = [part.strip() for part in str(tail).split(",") if part.strip()]
    if not parts:
        return ""
    symbols: list[str] = []
    for part in parts[:_MAX_DICE]:
        try:
            value = int(part)
        except ValueError:
            symbols.append(part[:_MAX_LABEL])
        else:
            symbols.append(_dice_symbol(value))
    return "[" + ",".join(symbols) + "]"


def _mechanical_line(
    round_number: int,
    player: Any,
    request: str,
    decision: Any,
    *,
    index: int,
    decisions: Sequence[Any],
    events: Sequence[Any],
) -> str:
    note = _mechanical_note(request, decision)
    side = _side(player)
    if request == "reroll_dice":
        body = _reroll_line(
            round_number,
            int(player),
            decision,
            index=index,
            decisions=decisions,
        )
        return body + note if body else ""
    if request == "switch_hands":
        body = _switch_line(
            round_number,
            int(player),
            decision,
            index=index,
            decisions=decisions,
            events=events,
        )
        return body + note if body else ""
    kind_text = _MECHANICAL_TEXT[request]
    label = str(_field(decision, "chosen_label") or "")
    tail = label.split(":", 1)[1].strip() if ":" in label else ""
    if tail.lower() == "none":
        tail = ""
    body = f"R{round_number} {side} {kind_text}"
    if request == "choose_active":
        name = _choose_active_name(decision, int(player))
        if name:
            body += f"：{name}"
    elif tail:
        body += f" {tail[:_MAX_LABEL]}"
    return body + note


def _view_players(view: Any) -> list[dict[str, Any]]:
    """The two player dicts of a ``dataclasses.asdict(StateSnapshot)`` view."""
    if not isinstance(view, dict):
        return []
    players = view.get("players")
    if not isinstance(players, (list, tuple)):
        return []
    return [player for player in players if isinstance(player, dict)]


def _characters(player: dict[str, Any]) -> list[dict[str, Any]]:
    characters = player.get("characters")
    if not isinstance(characters, (list, tuple)):
        return []
    return [character for character in characters if isinstance(character, dict)]


def _entity_name(entity: Any) -> str:
    """Resolve a public entity name without ever exposing an instance id.

    ``definition_id`` is authoritative; some older/newer projections carry
    ``card_definition_id`` instead, so that is the second choice. NPC/overlay
    entities are not in the official catalog under their own id, but the engine
    namespaces their variant definitions at ``official_id + 70000`` (verified:
    ``193011 -> 123011 潜行``, ``192021 -> 122021 水光破镜``,
    ``192011 -> 122011 纯水幻形·花鼠``). That offset is tried only after the
    direct lookup misses, and the numeric instance ``id`` is never a name. If no
    path resolves, return ``未知状态``.
    """
    if not isinstance(entity, dict):
        return "未知状态"
    for field in ("definition_id", "card_definition_id"):
        definition_id = _as_int(entity.get(field))
        if not definition_id:
            continue
        name = _card_name(definition_id)
        if not name.startswith("#"):
            return _clean_status_name(name)
        if definition_id > 70000:
            variant = _card_name(definition_id - 70000)
            if not variant.startswith("#"):
                return _clean_status_name(variant)
    return "未知状态"


def _clean_status_name(name: str) -> str:
    """Drop transient ``（已创建）`` / ``（生效中）`` markers from a status name."""
    cleaned = str(name).strip()
    changed = True
    while changed:
        changed = False
        for suffix in _STATUS_NAME_SUFFIXES:
            if cleaned.endswith(suffix):
                cleaned = cleaned[: -len(suffix)].strip()
                changed = True
    return cleaned or str(name)


def _entity_key(entity: Any) -> tuple[str, str | None]:
    """``(display name, counter text)`` for one support/summon/status/entity.

    The counter carries the unit of the variable it came from (``2次`` / ``2回合`` /
    ``3层``), because a bare ``(2)`` next to health and damage numbers reads like
    more damage.
    """
    if not isinstance(entity, dict):
        return ("未知状态", None)
    name = _entity_name(entity)
    value: str | None = None
    variables = entity.get("variables")
    if isinstance(variables, dict):
        for key in _STATUS_VALUE_KEYS:
            if key not in variables:
                continue
            number = _as_int(variables.get(key))
            if number is None:
                continue
            value = f"{number}{_STATUS_VALUE_UNITS.get(key, '')}"
            break
    return (name, value)


def _side_entity_counter(
    players: Sequence[dict[str, Any]],
) -> Counter[tuple[int, str, str | None]]:
    """Multiset of public supports/summons/statuses/attachments, both sides.

    Counting by ``(side, name, counter)`` implements the requested "same name with
    different numbers are different stacks, identical ones are counted together".
    Only names and use/layer/duration counters are kept -- never hand-card
    identities.
    """
    counter: Counter[tuple[int, str, str | None]] = Counter()
    for side, player in enumerate(players):
        if side not in _SIDE_TEXT:
            continue
        entities: list[Any] = []
        for character in _characters(player):
            attached = character.get("entities")
            if isinstance(attached, (list, tuple)):
                entities.extend(attached)
        for group in ("combat_statuses", "summons", "supports"):
            group_entities = player.get(group)
            if isinstance(group_entities, (list, tuple)):
                entities.extend(group_entities)
        for entity in entities:
            name, value = _entity_key(entity)
            counter[(side, name, value)] += 1
    return counter


def _character_changes(
    before_players: Sequence[dict[str, Any]],
    after_players: Sequence[dict[str, Any]],
) -> list[str]:
    """Health/energy/aura/defeated deltas per character slot, both sides."""
    items: list[str] = []
    for side in (0, 1):
        if side >= len(before_players) or side >= len(after_players):
            continue
        before_chars = _characters(before_players[side])
        after_chars = _characters(after_players[side])
        for slot in range(min(len(before_chars), len(after_chars))):
            before = before_chars[slot]
            after = after_chars[slot]
            definition_id = _as_int(
                after.get("definition_id") or before.get("definition_id")
            )
            label = (
                f"{_SIDE_TEXT[side]}·"
                f"{_effect_character_name(definition_id)}"
            )
            before_hp = _as_int(before.get("health"))
            after_hp = _as_int(after.get("health"))
            newly_defeated = not bool(before.get("defeated")) and bool(
                after.get("defeated")
            )
            if before_hp != after_hp:
                item = f"{label} {before_hp}→{after_hp}"
                if newly_defeated:
                    item += " 击倒"
                items.append(item)
            elif newly_defeated:
                items.append(f"{label} 击倒")
            # A defeat resets energy/aura in the engine; the HP+击倒 item already
            # says what happened, so do not repeat the cleanup numbers.
            if newly_defeated:
                continue
            before_energy = _as_int(before.get("energy"))
            after_energy = _as_int(after.get("energy"))
            if before_energy != after_energy:
                items.append(f"{label} 能量 {before_energy}→{after_energy}")
            before_aura = _as_int(before.get("aura"))
            after_aura = _as_int(after.get("aura"))
            if before_aura != after_aura:
                items.append(
                    f"{label} 附着 {_aura_symbol(before_aura)}→{_aura_symbol(after_aura)}"
                )
    return items


def _counter_sort_key(value: str | None) -> tuple[int, int, str]:
    """Order counters by their number, not the whole ``"2次"`` string.

    ``None`` (a status without a counter) sorts first; the trailing text keeps the
    order deterministic when two counters share a number but differ in unit.
    """
    if value is None:
        return (-1, 0, "")
    text = str(value)
    digits = ""
    for character in text:
        if not character.isdigit():
            break
        digits += character
    return (int(digits) if digits else 0, 0, text)


def _value_multiset(values: Counter[str | None]) -> str:
    """``{"2次": 1}`` -> ``2次``; ``{"2": 2}`` -> ``2×2``; empty -> ``无``."""
    if not values:
        return "无"
    parts: list[str] = []
    for value, count in sorted(values.items(), key=lambda item: _counter_sort_key(item[0])):
        text = "无" if value is None else str(value)
        if count > 1:
            text += f"×{count}"
        parts.append(text)
    return ",".join(parts)


def _status_changes(
    before_players: Sequence[dict[str, Any]],
    after_players: Sequence[dict[str, Any]],
) -> list[str]:
    """Added/removed/changed public supports/summons/statuses/attachments, per side.

    Same-name entities are compared as a multiset of counters: a new name reads
    ``获得「X」(2次)``, a vanished one ``消失「X」(2次)``, and a value change (e.g. a
    summon ticking 2 -> 1) reads ``「X」2次→1次`` instead of a misleading
    simultaneous gain+loss.
    """
    before = _side_entity_counter(before_players)
    after = _side_entity_counter(after_players)
    before_grouped: dict[tuple[int, str], Counter[str | None]] = {}
    after_grouped: dict[tuple[int, str], Counter[str | None]] = {}
    for (side, name, value), count in before.items():
        before_grouped.setdefault((side, name), Counter())[value] += count
    for (side, name, value), count in after.items():
        after_grouped.setdefault((side, name), Counter())[value] += count

    items: list[str] = []
    for key in sorted(set(before_grouped) | set(after_grouped)):
        side, name = key
        old_values = before_grouped.get(key, Counter())
        new_values = after_grouped.get(key, Counter())
        if old_values == new_values:
            continue
        if not old_values:
            for value, count in sorted(
                new_values.items(), key=lambda item: _counter_sort_key(item[0])
            ):
                suffix = "" if value is None else f"({value})"
                item = f"{_SIDE_TEXT[side]} 获得「{name}」{suffix}"
                if count > 1:
                    item += f"×{count}"
                items.append(item)
        elif not new_values:
            for value, count in sorted(
                old_values.items(), key=lambda item: _counter_sort_key(item[0])
            ):
                suffix = "" if value is None else f"({value})"
                item = f"{_SIDE_TEXT[side]} 消失「{name}」{suffix}"
                if count > 1:
                    item += f"×{count}"
                items.append(item)
        else:
            items.append(
                f"{_SIDE_TEXT[side]}「{name}」"
                f"{_value_multiset(old_values)}→{_value_multiset(new_values)}"
            )
    return items


def _global_changes(
    before: dict[str, Any],
    after: dict[str, Any],
    before_players: Sequence[dict[str, Any]],
    after_players: Sequence[dict[str, Any]],
    *,
    before_actor: int | None,
    after_actor: int | None,
) -> list[str]:
    """Dice counts, hand count, end declaration and round number.

    Private fields are only compared when both views belong to the same player
    *and* that player is the contestant (side 0, ``我方``): a masked view zeroes
    the opponent's dice and zeroes hand-card definition ids, so a cross-seat
    comparison is not trustworthy, and the NPC's private hand/dice should not be
    surfaced in a contestant-facing trace. Public fields (declared end, round
    number) are compared from either seat.
    """
    items: list[str] = []
    before_round = _as_int(before.get("round_number"))
    after_round = _as_int(after.get("round_number"))
    same_viewer = before_actor == 0 and after_actor == 0
    if same_viewer:
        side = 0
        if side < min(len(before_players), len(after_players)):
            before_dice = before_players[side].get("dice")
            after_dice = after_players[side].get("dice")
            before_count = (
                len(before_dice) if isinstance(before_dice, (list, tuple)) else 0
            )
            after_count = (
                len(after_dice) if isinstance(after_dice, (list, tuple)) else 0
            )
            if before_count != after_count:
                items.append(
                    f"{_SIDE_TEXT[side]}骰 {before_count}→{after_count}"
                )
            before_hand = before_players[side].get("hand_cards")
            after_hand = after_players[side].get("hand_cards")
            before_hand_count = (
                len(before_hand) if isinstance(before_hand, (list, tuple)) else 0
            )
            after_hand_count = (
                len(after_hand) if isinstance(after_hand, (list, tuple)) else 0
            )
            if before_hand_count != after_hand_count:
                items.append(
                    f"{_SIDE_TEXT[side]}手牌 {before_hand_count}→{after_hand_count}张"
                )
    # A new round resets ``declared_end`` automatically; reporting "撤回结束"
    # there would be an engine artifact, not a decision.
    if before_round == after_round:
        for side in (0, 1):
            if side >= len(before_players) or side >= len(after_players):
                continue
            before_end = bool(before_players[side].get("declared_end"))
            after_end = bool(after_players[side].get("declared_end"))
            if before_end != after_end:
                items.append(
                    f"{_SIDE_TEXT[side]} {'宣布结束' if after_end else '撤回结束'}"
                )
    if before_round != after_round:
        items.append(f"回合 {before_round}→{after_round}")
    return items


def _aura_symbol(value: Any) -> str:
    """Elemental aura id as ``冰``/``雷``/...; ``0`` means no aura."""
    number = _as_int(value)
    if number == 0:
        return "无"
    return _DICE_SYMBOL.get(number, str(number))


def _cap_effect(text: str) -> str:
    """Hard cap one suffix at :data:`_MAX_EFFECT` characters (``…`` on overflow)."""
    text = str(text)
    if len(text) <= _MAX_EFFECT:
        return text
    return text[: _MAX_EFFECT - 1] + "…"


def _effect_suffix(
    before: Any,
    after: Any,
    *,
    before_actor: int | None = None,
    after_actor: int | None = None,
) -> str:
    """Bounded public-state delta between two recorded player views.

    ``before`` is the view attached to the action's own decision (the state the
    actor saw); ``after`` is the next decision's view. The delta is labelled
    ``→ 此后场上：`` on purpose: between the two decisions the engine may have
    resolved the opponent's action, end-phase summons or a turn boundary, so the
    changes are *observed after this action*, not necessarily caused by it.

    Only public facts are compared: character HP/energy/aura/defeated flags,
    support/summon/status/attachment names with remaining uses or layers, and the
    round number. Hand counts and dice are private: they are compared only when
    both views come from the same player and that player is the contestant
    (``我方``), because the NPC's private hand/dice are neither needed nor safe to
    surface. A cross-seat window (the usual case after a combat action, where the
    next decision belongs to the opponent) therefore never reports hand or dice
    numbers -- the masked view zeroes the opponent's dice and zeroes hand-card
    definition ids, so a number inferred across seats would be a guess. Same-name
    entities are merged by name+value; additions and removals read ``获得`` /
    ``消失``, while a value change reads ``「X」2→1``. At most
    :data:`_MAX_EFFECT_ITEMS` changes are listed (the rest collapse into ``等N项``)
    and the whole suffix is capped at :data:`_MAX_EFFECT` characters. Nothing is
    emitted when the views are missing or no public fact changed.
    """
    before_players = _view_players(before)
    after_players = _view_players(after)
    if len(before_players) < 2 or len(after_players) < 2:
        return ""

    items: list[str] = []
    items.extend(_character_changes(before_players, after_players))
    items.extend(_status_changes(before_players, after_players))
    items.extend(
        _global_changes(
            before,
            after,
            before_players,
            after_players,
            before_actor=before_actor,
            after_actor=after_actor,
        )
    )
    if not items:
        return ""
    extra = len(items) - _MAX_EFFECT_ITEMS
    if extra > 0:
        items = items[:_MAX_EFFECT_ITEMS] + [f"等{extra}项"]
    return " " + _cap_effect(_EFFECT_LEAD + "；".join(items))


def _nearest_view_indices(
    views: Sequence[Any],
) -> tuple[list[int | None], list[int | None]]:
    """For each decision: its own/last previous view, and the next later view.

    ``previous[i]`` is ``i`` when decision ``i`` has a view, else the index of the
    closest earlier decision with one (``None`` if none). ``following[i]`` is the
    closest index ``j > i`` with a view (``None`` after the last one).
    """
    count = len(views)
    present = [isinstance(view, dict) for view in views]
    previous: list[int | None] = [None] * count
    last: int | None = None
    for index in range(count):
        if present[index]:
            last = index
        previous[index] = last
    following: list[int | None] = [None] * count
    nxt: int | None = None
    for index in range(count - 1, -1, -1):
        following[index] = nxt
        if present[index]:
            nxt = index
    return previous, following


def _reaction_text(value: Any) -> str:
    """Localize a lowercased engine reaction enum name; unknown -> ``name?``."""
    raw = str(value or "").strip()
    if not raw:
        return "?"
    return _REACTION_TEXT.get(raw.lower(), raw + "?")


def _reaction_lines(events: Iterable[Any]) -> list[str]:
    """Reaction events in recorder order, coalescing consecutive duplicates."""
    lines: list[str] = []
    previous: str | None = None
    count = 0
    for event in events:
        if str(_field(event, "kind", "reaction") or "") != "reaction":
            continue
        name = _reaction_text(_field(event, "reaction_type"))
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


def _as_view(state: Any) -> dict[str, Any] | None:
    """Normalize a recorded final state to a view dict.

    The recorder stores ``MatchRecord.final_state_json``, which callers decode
    into either a plain dict (``json.loads``) or a ``StateSnapshot`` dataclass
    (``reps.snapshot.snapshot_state``). Both are accepted; anything else is
    ignored so a trace never fails on an odd payload.
    """
    if isinstance(state, dict):
        return state
    if state is None:
        return None
    if isinstance(state, str):
        # ``MatchRecord.final_state_json``: decode with the same snapshot helper the
        # objective judge uses, so one argument works at the call site.
        try:
            from reps.snapshot import snapshot_state

            return _as_view(snapshot_state(None, state))
        except Exception:
            return None
    try:
        from dataclasses import asdict, is_dataclass

        if is_dataclass(state) and not isinstance(state, type):
            return asdict(state)
    except Exception:
        return None
    return None


def _hp_board(players: Sequence[dict[str, Any]], side: int) -> str:
    """``刻晴 0/10、柯莱 2/11`` for one side, in slot order."""
    if side >= len(players):
        return ""
    parts: list[str] = []
    for character in _characters(players[side]):
        definition_id = _as_int(character.get("definition_id"))
        name = (
            _effect_character_name(definition_id) if definition_id else "未知角色"
        )
        health = _as_int(character.get("health"))
        max_health = _as_int(character.get("max_health")) or health
        parts.append(f"{name} {health}/{max_health}")
    return "、".join(parts)


def _final_state_line(state: Any) -> str:
    """H9: ``最终：我方 <name hp/max>…；对方 …`` from the final snapshot.

    Skipped when the snapshot is missing or lacks the per-character HP the line
    needs, so old/partial records keep the pre-existing trace.
    """
    players = _view_players(_as_view(state))
    if len(players) < 2:
        return ""
    ours = _hp_board(players, 0)
    theirs = _hp_board(players, 1)
    if not ours or not theirs:
        return ""
    text = f"最终：我方 {ours}；对方 {theirs}"
    return text if len(text) <= _MAX_FINAL else text[:_MAX_FINAL - 1] + "…"


def _truncation_note(n_decisions: int, shown: int) -> str:
    """H9: one line saying the replay was cut (and how much of it is missing)."""
    return f"（回放已截断：共 {n_decisions} 个决策，仅显示前 {shown} 行）"


def compact_trace(
    row: dict[str, Any],
    decisions: Sequence[Any] | None,
    events: Sequence[Any] | None,
    *,
    max_actions: int = 60,
    meta: dict[str, Any] | None = None,
    final_state: Any = None,
) -> list[str]:
    """Render one match as at most ``max_actions`` short lines.

    * one line per ``action`` decision for both sides (``P0`` = contestant,
      ``P1(NPC)`` = the stage enemy), in recorder order;
    * the first ``switch_hands`` / ``choose_active`` per player, and one
      ``reroll_dice`` line per player *per round*. Each row says who chose it:
      ``（LLM 决策）`` or ``（脚本策略）`` from ``DecisionRecord.decider``;
      rerolls are always script. A ``reroll:none`` row is omitted (no noise).
      With recorded views a reroll row reads ``[旧池] → [新池]``; switch rows
      name the discarded and gained cards, and choose-active rows name the
      character;
    * one ``R{n} 我方/对方骰子：[...]（N个）`` row per player per round, taken from
      that player's own view (a real snapshot only; masked/absent views add
      nothing). It shows the *pool* the engine paid from;
    * when the recorder captured ``card_move`` events, every card that enters a
      hand is one ``R{n} P0 抽牌：「X」`` row (``抽牌 N 张`` for a masked NPC hand),
      placed right after the action that caused it. Without those events the old
      cross-round ``R{n} 手牌新增`` fallback is kept;
    * reaction events (localized Chinese names), coalesced when consecutive;
    * one closing line with winner, rounds and objective marks (always kept).

    Action lines carry an ``→ 此后场上：...`` suffix only when the recorder stored
    ``player_view`` (``record_views=True``); see :func:`_effect_suffix` for what is
    compared and why the wording does not claim causality. Paid dice read
    ``自动支付[...]``: the engine auto-selected them for the chosen action, they are
    not a model decision. Without views the line set is exactly the
    pre-enrichment trace (no suffix, no new exceptions).

    ``final_state`` (optional, H9) is the recorded final snapshot (a view dict or
    a ``StateSnapshot``). When given, it supplies a one-line ``最终：...`` HP board
    before the scoreboard and completes the last action's delta when that line has
    no ``→ 此后场上`` (it is the usual case: the last decision has no successor
    view). Without it, the HP board falls back to the last recorded view.

    ``meta`` (optional out-parameter) receives ``n_action_lines``,
    ``truncated`` and ``deciders`` (LLM/script counts) so the runner can record
    why a trace was cut and who made its decisions. When the body
    exceeds the cap, the oldest mechanical rows (``重掷``/``换牌``/``选人``) are
    dropped first so decision lines survive; if that is still not enough, the
    trace keeps its earliest rows (the pre-existing behaviour). A cut trace says
    so on its second line. Missing labels, round numbers, dice, or a malformed
    row never raise.
    """
    decisions = list(decisions or ())
    events = list(events or ())
    views = [_field(decision, "player_view") for decision in decisions]
    previous_view, following_view = _nearest_view_indices(views)
    card_events = _card_move_events(events)
    has_card_moves = bool(card_events)
    draw_buckets = _draw_buckets(events)

    action_entries: list[tuple[str, bool, int | None]] = []
    # Draws that happened before the first recorded decision (opening hand)
    # cannot be attributed to an action; put them at the head of the trace.
    for line in _draw_lines(draw_buckets.pop(0, ())):
        action_entries.append((line, False, None))
    seen_mechanical: set[tuple[Any, ...]] = set()
    seen_rolls: set[tuple[int, int]] = set()
    p0_round: int | None = None
    p0_hand: list[int] = []
    ended: set[int] = set()
    round_number = 1

    for index, decision in enumerate(decisions):
        player = _as_int(_field(decision, "player", 0))
        round_number = max(1, _round_of(decision, round_number))
        request = str(_field(decision, "request_type", "") or "")
        kind = str(_field(decision, "chosen_kind", "") or "")

        # One roll row per (player, round), before that decision's own line. Each
        # seat's dice are read from that seat's view (the opponent's pool is
        # masked), so "why did it pay [万,岩,风]" can be checked against the pool.
        # Init-phase views (round 0: mulligan/choose-active) are skipped because
        # the dice have not been rolled yet.
        view_round = _view_round(decision)
        if view_round is not None and view_round > 0:
            dice = _view_dice(decision)
            roll_key = (player, round_number)
            if dice is not None and roll_key not in seen_rolls:
                seen_rolls.add(roll_key)
                action_entries.append(
                    (_dice_pool_line(round_number, player, dice), False, None)
                )
            # Cross-round draws: the contestant's view is the only trustworthy
            # hand, so a round change is compared against the previous P0 view.
            # New ``card_move`` events already state every drawn card, so the
            # old summary row is only a fallback for legacy/no-event records.
            if player == 0 and not has_card_moves:
                hand_ids = _view_hand_ids(decision)
                if hand_ids is not None:
                    if p0_round is not None and round_number > p0_round:
                        line = _hand_add_line(
                            round_number, _hand_additions(p0_hand, hand_ids)
                        )
                        if line:
                            action_entries.append((line, False, None))
                    p0_round = round_number
                    p0_hand = hand_ids

        if request == "action" or kind.startswith("action_"):
            line = _action_line(round_number, player, decision)
            line += _preview_effect(decision)
            before_index = previous_view[index]
            after_index = following_view[index]
            if before_index is not None and after_index is not None:
                line += _effect_suffix(
                    views[before_index],
                    views[after_index],
                    before_actor=_as_int(
                        _field(decisions[before_index], "player", 0)
                    ),
                    after_actor=_as_int(
                        _field(decisions[after_index], "player", 0)
                    ),
                )
            action_entries.append((line, False, index))
        elif request in _MECHANICAL_TEXT:
            key: tuple[Any, ...] = (player, request)
            if request == "reroll_dice":
                # One reroll row per player per round: later rounds stay legible.
                key = (player, request, round_number)
            if key not in seen_mechanical:
                seen_mechanical.add(key)
                line = _mechanical_line(
                    round_number,
                    player,
                    request,
                    decision,
                    index=index,
                    decisions=decisions,
                    events=events,
                )
                if line:
                    action_entries.append((line, True, None))
        elif request in _IGNORED_REQUESTS:
            pass
        elif not request and not kind:
            # Unknown/empty record: keep it visible instead of silently dropping it.
            action_entries.append((f"R{round_number} {_side(player)} ?", False, None))
        else:
            pass

        # A card entering the hand arrives in the notification *after* the
        # decision that caused it; keep the draw row adjacent to that action.
        for line in _draw_lines(draw_buckets.pop(index + 1, ())):
            action_entries.append((line, False, None))

        # Round bookkeeping is only needed when no player_view was recorded.
        if kind == "action_declare_end":
            ended.add(player)
            if len(ended) >= 2:
                round_number += 1
                ended.clear()

    # End-phase draws (or anything after the last recorded decision) still show.
    for bucket_index in sorted(draw_buckets):
        for line in _draw_lines(draw_buckets[bucket_index]):
            action_entries.append((line, False, None))

    n_action_lines = len(action_entries)
    reaction_lines = _reaction_lines(events)
    body: list[tuple[str, bool, int | None]] = action_entries + [
        (line, False, None) for line in reaction_lines
    ]
    cap = max(1, _as_int(max_actions, 60))
    limit = cap - 1
    truncated = len(body) > limit
    if truncated:
        # Drop the oldest mechanical rows first (rerolls/mulligans are context,
        # not evidence); if that still is not enough, keep the earliest rows so
        # the trace remains a coherent opening-to-close narrative.
        while len(body) > limit:
            index = next(
                (i for i, (_, mechanical, _src) in enumerate(body) if mechanical), None
            )
            if index is None:
                break
            body.pop(index)
        if len(body) > limit:
            body = body[:limit]

    # H9: the final snapshot may only be knowable *after* the last decision, so the
    # last surviving action line gets its delta filled in from it.
    final_view = _as_view(final_state)
    last_action: int | None = None
    for position in range(len(body) - 1, -1, -1):
        _line, _mechanical, _source = body[position]
        if not _mechanical and _source is not None:
            last_action = position
            break
    if final_view is not None and last_action is not None:
        line, mechanical, source = body[last_action]
        if _EFFECT_LEAD not in line:
            before_index = following_view[source]
            if before_index is None:
                before_index = previous_view[source]
            if before_index is not None:
                suffix = _effect_suffix(
                    views[before_index],
                    final_view,
                    before_actor=_as_int(
                        _field(decisions[before_index], "player", 0)
                    ),
                    # The final snapshot is the full state, so its private fields
                    # are comparable only for the contestant's own hand/dice.
                    after_actor=0,
                )
                if suffix:
                    body[last_action] = (line + suffix, mechanical, source)

    lines = [line for line, _mechanical, _source in body]
    n_shown = len(lines)
    if truncated and lines:
        lines.insert(1, _truncation_note(len(decisions), n_shown))
    hp_view = final_view
    if hp_view is None:
        for index in range(len(views) - 1, -1, -1):
            if isinstance(views[index], dict):
                hp_view = views[index]
                break
    hp_line = _final_state_line(hp_view)
    if hp_line:
        lines.append(hp_line)
    lines.append(_final_line(row))

    if meta is not None:
        meta["n_action_lines"] = n_action_lines
        meta["n_lines"] = len(lines)
        meta["truncated"] = truncated
        meta["deciders"] = {
            "llm": sum(
                1
                for decision in decisions
                if str(_field(decision, "decider", "script") or "script") == "llm"
            ),
            "script": sum(
                1
                for decision in decisions
                if str(_field(decision, "decider", "script") or "script") != "llm"
            ),
        }

    return lines
