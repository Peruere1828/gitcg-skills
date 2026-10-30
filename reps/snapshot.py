# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

import json
from typing import Any

from .schema import (
    AttachmentSnapshot,
    CharacterSnapshot,
    EntitySnapshot,
    ExtensionSnapshot,
    PlayerSnapshot,
    StateSnapshot,
)

PHASE_NAMES = {
    0: "init_hands",
    1: "init_actives",
    2: "roll",
    3: "action",
    4: "end",
    5: "game_end",
}

_JSON_PHASE_NAMES = {
    "initHands": "init_hands",
    "initActives": "init_actives",
    "roll": "roll",
    "action": "action",
    "end": "end",
    "gameEnd": "game_end",
}


def snapshot_state(state: Any, state_json: str | None = None) -> StateSnapshot:
    root = _decode_state_json(state_json if state_json is not None else state.json())
    players = tuple(_snapshot_player_from_json(player) for player in root["players"])
    if len(players) != 2:
        raise ValueError(f"expected 2 players in state json, got {len(players)}")
    iterators = dict(root.get("iterators", {}) or {})
    return StateSnapshot(
        phase=_JSON_PHASE_NAMES.get(root.get("phase"), str(root.get("phase"))),
        round_number=int(root.get("roundNumber", 0)),
        current_turn=int(root.get("currentTurn", 0)),
        winner=_winner_from_json(root.get("winner")),
        players=(players[0], players[1]),
        iterator_id=int(iterators.get("id", 0) or 0),
        extension_states=tuple(
            _snapshot_extension_from_json(extension)
            for extension in root.get("extensions", ())
        ),
    )


def snapshot_notification(notification: Any) -> StateSnapshot:
    state = notification.state
    winner = None
    if hasattr(state, "HasField") and state.HasField("winner"):
        winner = int(state.winner)
    elif getattr(state, "winner", None) is not None:
        winner = int(state.winner)
    players = tuple(_snapshot_player_from_proto(player) for player in state.player)
    if len(players) != 2:
        raise ValueError(f"expected 2 players in notification, got {len(players)}")
    return StateSnapshot(
        phase=PHASE_NAMES.get(int(state.phase), f"phase_{state.phase}"),
        round_number=int(state.round_number),
        current_turn=int(state.current_turn),
        winner=winner,
        players=(players[0], players[1]),
    )


def _snapshot_player_from_json(player: dict[str, Any]) -> PlayerSnapshot:
    active_character_id = _normalize_active_character_id(player.get("activeCharacterId"))
    characters = tuple(
        _snapshot_character_from_json(character, active_character_id)
        for character in player.get("characters", [])
    )
    return PlayerSnapshot(
        active_character_id=active_character_id,
        characters=characters,
        combat_statuses=tuple(
            _snapshot_entity_from_json(entity) for entity in player.get("combatStatuses", [])
        ),
        summons=tuple(
            _snapshot_entity_from_json(entity) for entity in player.get("summons", [])
        ),
        supports=tuple(
            _snapshot_entity_from_json(entity) for entity in player.get("supports", [])
        ),
        hand_cards=tuple(
            _snapshot_entity_from_json(entity) for entity in player.get("hands", [])
        ),
        pile_cards=tuple(
            _snapshot_entity_from_json(entity) for entity in player.get("pile", [])
        ),
        dice=tuple(int(die) for die in player.get("dice", []) if int(die) != 0),
        declared_end=bool(player.get("declaredEnd", False)),
        legend_used=bool(player.get("legendUsed", False)),
        can_charged=bool(player.get("canCharged", False)),
        can_plunging=bool(player.get("canPlunging", False)),
        has_defeated=bool(player.get("hasDefeated", False)),
        skip_next_turn=bool(player.get("skipNextTurn", False)),
        defeated_switching=bool(player.get("defeatedSwitching", False)),
        round_skill_log=_json_like_copy(player.get("roundSkillLog", {})),
        phase_damage_log=tuple(_json_like_copy(player.get("phaseDamageLog", ()))),
        phase_reaction_log=tuple(_json_like_copy(player.get("phaseReactionLog", ()))),
        removed_entities=tuple(_json_like_copy(player.get("removedEntities", ()))),
    )


def _snapshot_player_from_proto(player: Any) -> PlayerSnapshot:
    active_character_id = _optional_scalar(player, "active_character_id")
    characters = tuple(
        CharacterSnapshot(
            id=int(character.id),
            definition_id=int(character.definition_id),
            health=int(character.health),
            max_health=int(character.max_health),
            energy=int(character.energy),
            max_energy=int(character.max_energy),
            defeated=bool(character.defeated),
            is_active=active_character_id is not None and int(character.id) == active_character_id,
            aura=int(getattr(character, "aura", 0)),
            entities=tuple(_snapshot_proto_entity(entity) for entity in character.entity),
        )
        for character in player.character
    )
    return PlayerSnapshot(
        active_character_id=active_character_id,
        characters=characters,
        combat_statuses=tuple(_snapshot_proto_entity(entity) for entity in player.combat_status),
        summons=tuple(_snapshot_proto_entity(entity) for entity in player.summon),
        supports=tuple(_snapshot_proto_entity(entity) for entity in player.support),
        hand_cards=tuple(_snapshot_proto_entity(entity) for entity in player.hand_card),
        pile_cards=tuple(_snapshot_proto_entity(entity) for entity in player.pile_card),
        dice=tuple(int(die) for die in player.dice if int(die) != 0),
        declared_end=bool(player.declared_end),
        legend_used=bool(player.legend_used),
        can_charged=bool(getattr(player, "can_charged", False)),
        can_plunging=bool(getattr(player, "can_plunging", False)),
        has_defeated=bool(getattr(player, "has_defeated", False)),
    )


def _snapshot_character_from_json(
    character: dict[str, Any],
    active_character_id: int | None,
) -> CharacterSnapshot:
    variables = character.get("variables", {})
    character_id = int(character.get("id", 0))
    return CharacterSnapshot(
        id=character_id,
        definition_id=int(character.get("definition", {}).get("id", 0)),
        health=int(variables.get("health", 0)),
        max_health=int(variables.get("maxHealth", 0)),
        energy=int(variables.get("energy", 0)),
        max_energy=int(variables.get("maxEnergy", 0)),
        defeated=not bool(variables.get("alive", 1)),
        is_active=active_character_id is not None and character_id == active_character_id,
        aura=int(variables.get("aura", 0)),
        entities=tuple(
            _snapshot_entity_from_json(entity) for entity in character.get("entities", [])
        ),
    )


def _snapshot_entity_from_json(entity: dict[str, Any]) -> EntitySnapshot:
    variables = {
        key: int(value)
        for key, value in entity.get("variables", {}).items()
        if isinstance(value, (bool, int))
    }
    return EntitySnapshot(
        id=int(entity.get("id", 0)),
        definition_id=int(entity.get("definition", {}).get("id", 0)),
        variables=variables,
        attachments=tuple(
            _snapshot_attachment_from_json(attachment)
            for attachment in entity.get("attachments", ())
        ),
    )


def _snapshot_proto_entity(entity: Any) -> EntitySnapshot:
    variables = {}
    variable_name = _optional_scalar(entity, "variable_name")
    variable_value = _optional_scalar(entity, "variable_value")
    if isinstance(variable_name, str) and variable_name and variable_value is not None:
        variables[variable_name] = int(variable_value)
    return EntitySnapshot(
        id=int(entity.id),
        definition_id=int(entity.definition_id),
        variables=variables,
        attachments=tuple(
            _snapshot_proto_attachment(attachment)
            for attachment in getattr(entity, "attachment", ())
        ),
    )


def _snapshot_attachment_from_json(attachment: dict[str, Any]) -> AttachmentSnapshot:
    variables: dict[str, int] = {}
    variable_name = attachment.get("variable_name")
    variable_value = attachment.get("variable_value")
    if isinstance(variable_name, str) and variable_name and isinstance(variable_value, (bool, int)):
        variables[str(variable_name)] = int(variable_value)
    extra_variables = attachment.get("variables", {})
    if isinstance(extra_variables, dict):
        for key, value in extra_variables.items():
            if isinstance(value, (bool, int)):
                variables[str(key)] = int(value)
    return AttachmentSnapshot(
        id=int(attachment.get("id", 0)),
        definition_id=int(attachment.get("definition", {}).get("id", attachment.get("definition_id", 0))),
        variables=variables,
    )


def _snapshot_proto_attachment(attachment: Any) -> AttachmentSnapshot:
    variables: dict[str, int] = {}
    variable_name = _optional_scalar(attachment, "variable_name")
    variable_value = _optional_scalar(attachment, "variable_value")
    if isinstance(variable_name, str) and variable_name and variable_value is not None:
        variables[variable_name] = int(variable_value)
    return AttachmentSnapshot(
        id=int(getattr(attachment, "id", 0)),
        definition_id=int(getattr(attachment, "definition_id", 0)),
        variables=variables,
    )


def _snapshot_extension_from_json(extension: dict[str, Any]) -> ExtensionSnapshot:
    definition = extension.get("definition", {}) if isinstance(extension, dict) else {}
    return ExtensionSnapshot(
        definition_id=int(definition.get("id", 0)),
        state=_json_like_copy(extension.get("state")),
    )


def _optional_scalar(message: Any, field_name: str) -> Any:
    if hasattr(message, "HasField"):
        try:
            if message.HasField(field_name):
                return getattr(message, field_name)
            return None
        except ValueError:
            pass
    return getattr(message, field_name, None)


def _decode_state_json(raw_json: str) -> dict[str, Any]:
    payload = json.loads(raw_json)
    store = payload["store"]
    cache: dict[int, Any] = {}

    def resolve_index(index: int) -> Any:
        if index in cache:
            return cache[index]
        cache[index] = None
        cache[index] = resolve_node(store[index])
        return cache[index]

    def resolve_node(node: Any) -> Any:
        if isinstance(node, dict):
            if "$" in node and len(node) == 1:
                return resolve_index(int(node["$"]))
            if node.get("__type") == "map":
                return {
                    resolve_node(entry[0]): resolve_node(entry[1])
                    for entry in node.get("entries", [])
                }
            if node.get("__type") == "set":
                return [resolve_node(value) for value in node.get("values", [])]
            return {key: resolve_node(value) for key, value in node.items()}
        if isinstance(node, list):
            return [resolve_node(value) for value in node]
        return node

    return resolve_index(len(store) - 1)


def _json_like_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_like_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_like_copy(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_json_like_copy(item) for item in value)
    return value


def _normalize_active_character_id(value: Any) -> int | None:
    if value in (None, 0):
        return None
    return int(value)


def _winner_from_json(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)
