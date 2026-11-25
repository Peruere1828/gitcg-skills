"""``envs.match`` event projection + card-kind alignment (PvE-B3 / M4).

The engine counts reactions triggered by *pure elemental application* (an
``applyAura`` mutation with a reaction and no damage) as well as by damage; both
must reach ``MatchEvent``. These tests drive ``_collect_events`` with hand-built
protobuf notifications (no engine), so they stay fast.
"""

from __future__ import annotations

from types import SimpleNamespace

from gitcg.proto import enums_pb2, mutation_pb2

from envs.match import PolicyPlayer, _card_kind_from_entity, _reaction_name

SUPERCONDUCT = int(enums_pb2.REACTION_TYPE_SUPERCONDUCT)
VAPORIZE = int(enums_pb2.REACTION_TYPE_VAPORIZE)


def _apply_aura(reaction: int, *, target_definition_id: int = 3203) -> mutation_pb2.ExposedMutation:
    mutation = mutation_pb2.ExposedMutation()
    mutation.apply_aura.reaction_type = int(reaction)
    mutation.apply_aura.target_definition_id = int(target_definition_id)
    return mutation


def _skill_used(who: int, skill_definition_id: int = 32031) -> mutation_pb2.ExposedMutation:
    mutation = mutation_pb2.ExposedMutation()
    mutation.skill_used.who = int(who)
    mutation.skill_used.skill_definition_id = int(skill_definition_id)
    return mutation


def _damage(reaction: int, *, source_id: int, source_definition_id: int = 14031) -> mutation_pb2.ExposedMutation:
    mutation = mutation_pb2.ExposedMutation()
    mutation.damage.reaction_type = int(reaction)
    mutation.damage.source_id = int(source_id)
    mutation.damage.source_definition_id = int(source_definition_id)
    return mutation


def _collect(mutations: list[mutation_pb2.ExposedMutation]) -> list:
    sink: list = []
    player = PolicyPlayer(0, None, event_sink=sink)
    player._collect_events(SimpleNamespace(state=None, mutation=mutations))
    return sink


def test_apply_aura_reaction_is_recorded():
    sink = _collect([_skill_used(1), _apply_aura(SUPERCONDUCT)])
    reactions = [event for event in sink if event.kind == "reaction"]
    assert len(reactions) == 1
    assert reactions[0].reaction_type == _reaction_name(SUPERCONDUCT)
    # Attributed to the skill that applied the element in the same batch.
    assert reactions[0].player == 1
    # No source entity on the pure-application path.
    assert reactions[0].source_definition_id == 0


def test_apply_aura_attributes_to_nearest_preceding_skill():
    """Cross-player batches (end-phase summon ticks interleave sides) must not be
    misattributed to the batch's *last* skill (review follow-up #3)."""
    sink = _collect(
        [
            _skill_used(1),
            _apply_aura(SUPERCONDUCT),
            _skill_used(0),
            _apply_aura(VAPORIZE),
        ]
    )
    reactions = [event for event in sink if event.kind == "reaction"]
    assert [event.player for event in reactions] == [1, 0]


def test_apply_aura_without_reaction_is_ignored():
    mutation = mutation_pb2.ExposedMutation()
    mutation.apply_aura.reaction_type = 0
    assert [event for event in _collect([mutation]) if event.kind == "reaction"] == []


def test_apply_aura_without_batch_skill_has_no_player():
    sink = _collect([_apply_aura(VAPORIZE)])
    reactions = [event for event in sink if event.kind == "reaction"]
    assert len(reactions) == 1
    assert reactions[0].player is None


def test_damage_reaction_uses_owner_map():
    player = PolicyPlayer(0, None, event_sink=[])
    player._owner_by_entity_id[1234] = 1
    sink = player.event_sink
    player._collect_events(
        SimpleNamespace(state=None, mutation=[_damage(VAPORIZE, source_id=1234)])
    )
    reactions = [event for event in sink if event.kind == "reaction"]
    assert len(reactions) == 1
    assert reactions[0].player == 1
    assert reactions[0].source_definition_id == 14031


def test_talent_card_kind_matches_asset_vocabulary():
    # 114031 = 雷楔 (Keqing talent): the engine reports it as an event card in
    # hand, but the asset-tag vocabulary used by npc.contestant says "talent".
    assert _card_kind_from_entity(1, 0, definition_id=114031) == "talent"
    # A plain event card stays "event".
    assert _card_kind_from_entity(1, 0, definition_id=332001) == "event"
    # Engine-distinguishable kinds are unchanged.
    assert _card_kind_from_entity(6, 1) == "weapon"
    assert _card_kind_from_entity(6, 2) == "artifact"
    assert _card_kind_from_entity(5, 0) == "support"


# -- card_move projection ----------------------------------------------------
HAND = int(mutation_pb2.ENTITY_AREA_HAND)
PILE = int(mutation_pb2.ENTITY_AREA_PILE)


def _move(
    *,
    from_where: int,
    to_where: int,
    definition_id: int,
    reason: int,
    from_who: int = 0,
    to_who: int = 0,
) -> mutation_pb2.ExposedMutation:
    mutation = mutation_pb2.ExposedMutation()
    mutation.move_entity.from_where = int(from_where)
    mutation.move_entity.to_where = int(to_where)
    mutation.move_entity.from_who = int(from_who)
    mutation.move_entity.to_who = int(to_who)
    mutation.move_entity.entity.definition_id = int(definition_id)
    mutation.move_entity.reason = int(reason)
    return mutation


def _create(definition_id: int, who: int = 0) -> mutation_pb2.ExposedMutation:
    mutation = mutation_pb2.ExposedMutation()
    mutation.create_entity.who = int(who)
    mutation.create_entity.where = HAND
    mutation.create_entity.entity.definition_id = int(definition_id)
    return mutation


def _remove(definition_id: int, *, who: int = 0, reason: int = 1) -> mutation_pb2.ExposedMutation:
    mutation = mutation_pb2.ExposedMutation()
    mutation.remove_entity.who = int(who)
    mutation.remove_entity.where = HAND
    mutation.remove_entity.entity.definition_id = int(definition_id)
    mutation.remove_entity.reason = int(reason)
    return mutation


def test_draw_into_hand_is_recorded_as_card_move():
    sink = _collect([_move(from_where=PILE, to_where=HAND, definition_id=333003,
                           reason=2, to_who=0)])
    moves = [event for event in sink if event.kind == "card_move"]
    assert len(moves) == 1
    move = moves[0]
    assert (move.player, move.definition_id, move.from_area, move.to_area) == (0, 333003, PILE, HAND)
    assert move.reason == "draw"


def test_create_entity_in_hand_and_hand_remove_are_recorded():
    sink = _collect([
        _create(322012),
        _remove(332009, reason=1),
    ])
    moves = [event for event in sink if event.kind == "card_move"]
    assert [(m.definition_id, m.reason) for m in moves] == [
        (322012, "draw"),
        (332009, "played"),
    ]
    assert moves[1].from_area == HAND and moves[1].to_area == 0


def test_moves_outside_the_hand_are_ignored():
    sink = _collect([_move(from_where=PILE, to_where=PILE, definition_id=333003, reason=2)])
    assert [event for event in sink if event.kind == "card_move"] == []


def test_card_move_decision_index_uses_the_shared_recorder():
    sink: list = []
    recorder = [object(), object()]
    player = PolicyPlayer(0, None, recorder=recorder, event_sink=sink)
    player._collect_events(SimpleNamespace(state=None, mutation=[_create(333003)]))
    assert sink[0].kind == "card_move"
    assert sink[0].decision_index == 2


def test_card_move_events_stop_at_the_cap_with_one_marker():
    sink = _collect([_create(100000 + index) for index in range(401)])
    assert len(sink) == 401
    assert sum(event.kind == "card_move" for event in sink) == 400
    assert sink[-1].kind == "truncated"
