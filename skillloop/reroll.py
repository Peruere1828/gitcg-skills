"""Fixed reroll rule for the scripted (P0) seat.

P0 never asks the model which dice to throw back: the rule is mechanical.  Keep
every omni die (8) and every die whose element belongs to one of our team
characters; reroll everything else.  With a Keqing (Electro) + Collei (Dendro)
+ Fischl (Electro) team the keep set is ``{4, 7, 8}``, so a pool such as
``[1, 1, 4, 4, 7, 8, 8, 2]`` throws back ``[1, 1, 2]``.

The engine enumerates every multiset subset of the visible dice as a legal
``reroll_dice`` candidate (``reps.action_adapter._reroll_specs``), each carrying
``payload={"dice_to_reroll": [...]}``.  ``preferred_reroll_code`` maps our keep
set onto that enumeration; when nothing is discardable it picks the explicit
"keep all" candidate, and when the payload is absent it returns ``None`` so the
caller can fall back to the scripted expert.
"""

from __future__ import annotations

from typing import Any

from skillloop.cards import character_element

OMNI_DICE = 8
# Dice values are 1..7 for the seven elements (see ``skillloop.rich_render``)
# and 8 for omni.  ``cards.character_element`` returns the Chinese label used by
# the asset dump; the English aliases cover callers/tests that pass raw tags.
_ELEMENT_DICE = {
    "冰": 1,
    "水": 2,
    "火": 3,
    "雷": 4,
    "风": 5,
    "岩": 6,
    "草": 7,
}
_ELEMENT_ALIASES = {
    "cryo": 1,
    "hydro": 2,
    "pyro": 3,
    "electro": 4,
    "anemo": 5,
    "geo": 6,
    "dendro": 7,
}
_ALIAS_PREFIXES = ("GCG_TAG_ELEMENT_", "ELEMENT_", "GCG_TAG_")


def _element_dice_value(element: Any) -> int | None:
    """Map one element label/tag to its dice value; ``None`` when unknown."""
    if element is None:
        return None
    text = str(element).strip()
    if not text:
        return None
    if text in _ELEMENT_DICE:
        return _ELEMENT_DICE[text]
    token = text.upper()
    for prefix in _ALIAS_PREFIXES:
        if token.startswith(prefix):
            token = token[len(prefix):]
            break
    return _ELEMENT_ALIASES.get(token.lower())


def keep_dice_values(player_view: Any, acting_player: int = 0) -> frozenset[int]:
    """Dice values to keep: omni plus the acting team's character elements.

    The team is read from ``player_view.players[acting_player]`` (the seat that is
    deciding), so the rule follows the actual actor instead of assuming seat 0.
    A missing view degrades to "keep only omni" rather than raising, so a
    surprising snapshot can never cost a game.
    """
    keep = {OMNI_DICE}
    if player_view is None:
        return frozenset(keep)
    players = getattr(player_view, "players", ()) or ()
    if not 0 <= int(acting_player) < len(players):
        return frozenset(keep)
    for character in getattr(players[int(acting_player)], "characters", ()) or ():
        value = _element_dice_value(character_element(int(character.definition_id)))
        if value is not None:
            keep.add(value)
    return frozenset(keep)


def preferred_reroll_code(built: Any) -> int | None:
    """Legal code whose payload rerolls exactly the non-keep dice, else ``None``.

    Matching is by multiset (sorted values), because the enumeration can contain
    duplicate dice and the engine records the chosen subset as dice values.
    """
    context = getattr(built, "context", None)
    player_view = getattr(context, "player_view", None)
    if context is None or player_view is None:
        return None
    players = getattr(player_view, "players", ()) or ()
    acting_player = int(getattr(context, "acting_player", 0) or 0)
    if not 0 <= acting_player < len(players):
        return None
    visible_dice = [int(value) for value in getattr(players[acting_player], "dice", ()) or ()]
    keep = keep_dice_values(player_view, acting_player)
    expected = sorted(value for value in visible_dice if value not in keep)
    payloads = getattr(built, "payload_by_low_level_code", {}) or {}
    for raw_code in getattr(context, "legal_low_level_codes", ()) or ():
        code = int(raw_code)
        payload = payloads.get(code)
        if not payload or "dice_to_reroll" not in payload:
            continue
        candidate = sorted(int(value) for value in payload["dice_to_reroll"])
        if candidate == expected:
            return code
    return None
