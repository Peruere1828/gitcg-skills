"""M0 acceptance: player-view masking hides opponent private information.

This is the state-level half of the information-leak gate. The observation-encoder
half (two states differing only in hidden info must encode identically) lands with
``reps/observation_encoder`` in the L2 milestone.
"""

from __future__ import annotations

from gitcg import CreateParam, State, low_level

from envs.decks import deck_spec, to_gitcg_deck
from reps.public_state import mask_state_for_player
from reps.snapshot import snapshot_state


def _full_snapshot():
    d0, d1 = deck_spec("superconduct_aggro"), deck_spec("natlan_battleship")
    cp = CreateParam(deck0=to_gitcg_deck(d0), deck1=to_gitcg_deck(d1))
    cp.set_attr(low_level.ATTR_STATE_CONFIG_RANDOM_SEED, 1)
    cp.set_attr(low_level.ATTR_CREATEPARAM_NO_SHUFFLE_0, 1)
    cp.set_attr(low_level.ATTR_CREATEPARAM_NO_SHUFFLE_1, 1)
    return snapshot_state(State(create_param=cp))


def test_mask_hides_opponent_private_info():
    full = _full_snapshot()
    masked = mask_state_for_player(full, perspective_player=0)
    me, opp = masked.players[0], masked.players[1]

    # Opponent private zones are erased ...
    assert opp.dice == ()
    assert all(card.definition_id == 0 for card in opp.hand_cards)
    assert all(card.definition_id == 0 for card in opp.pile_cards)
    # ... while the *counts* remain visible (public information).
    assert len(opp.hand_cards) == len(full.players[1].hand_cards)

    # My private info is preserved exactly.
    assert me.dice == full.players[0].dice
    assert [c.definition_id for c in me.hand_cards] == [
        c.definition_id for c in full.players[0].hand_cards
    ]


def test_masking_is_idempotent():
    full = _full_snapshot()
    once = mask_state_for_player(full, perspective_player=0)
    twice = mask_state_for_player(once, perspective_player=0)
    assert once == twice


def test_character_public_info_survives_masking():
    """Character health/energy/aura are public and must not be scrubbed."""
    full = _full_snapshot()
    masked = mask_state_for_player(full, perspective_player=0)
    for before, after in zip(full.players[1].characters, masked.players[1].characters):
        assert after.definition_id == before.definition_id
        assert after.health == before.health
        assert after.energy == before.energy
        assert after.aura == before.aura
