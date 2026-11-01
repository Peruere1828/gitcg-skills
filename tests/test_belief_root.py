"""M3-CPU S-A: public-belief rooting (PLAN.md §4.3, gaps G1+G3).

The old resolver forked the *live* boundary snapshot, whose hidden state is the
single true determinization (charter §5.2 violation). These tests pin the
replacement: roots are built from the player-masked public state + a sampled
particle, and are **invariant to the scaffold's hidden truth**.

Engine-free tests on hand-built ``store``/``$`` state JSON: the patcher rewrites
hidden zones and inline dice, public ranges depend only on public fields, and
reconstruction is byte-identical for two scaffolds that share a public view but
differ in hidden truth.
"""

from __future__ import annotations

import json

from reps.belief import (
    _StatePatcher,
    build_public_belief_state,
    reconstruct_state_json,
    sample_belief_roots,
)
from reps.public_state import mask_state_for_player
from reps.snapshot import snapshot_state


# --------------------------------------------------------------------------- #
# engine-free fixtures
# --------------------------------------------------------------------------- #
def _mk_state_json(
    *,
    opp_hand_ids: tuple[int, ...] = (4, 5, 6),
    opp_pile_ids: tuple[int, ...] = (7, 8),
    opp_dice: tuple[int, ...] = (3, 4),
    self_hand_defs: tuple[int, ...] = (11, 12),
) -> str:
    """A minimal engine-format state JSON with controllable hidden truth."""
    store: list = []

    def add(node):
        store.append(node)
        return {"$": len(store) - 1}

    char_def = add({"$$": "characters", "id": 100})

    def entity(card_id: int, definition_id: int):
        return add(
            {
                "id": int(card_id),
                "definition": add({"$$": "entities", "id": int(definition_id)}),
                "variables": {},
                "attachments": [],
            }
        )

    def player(*, hand_defs, pile_defs, dice, base_id):
        hands = add([entity(base_id + i, d) for i, d in enumerate(hand_defs)])
        pile = add([entity(base_id + 100 + i, d) for i, d in enumerate(pile_defs)])
        chars = add(
            [
                add(
                    {
                        "id": base_id,
                        "definition": char_def,
                        "variables": {
                            "health": 10,
                            "maxHealth": 10,
                            "energy": 0,
                            "maxEnergy": 3,
                            "alive": 1,
                        },
                        "entities": add([]),
                    }
                )
            ]
        )
        return add(
            {
                "activeCharacterId": base_id,
                "characters": chars,
                "combatStatuses": add([]),
                "summons": add([]),
                "supports": add([]),
                "hands": hands,
                "pile": pile,
                "dice": [int(d) for d in dice],
                "declaredEnd": False,
                "canCharged": False,
                "canPlunging": False,
                "hasDefeated": False,
                "legendUsed": False,
                "skipNextTurn": False,
                "defeatedSwitching": False,
                "roundSkillLog": {"__type": "map", "entries": []},
                "phaseDamageLog": [],
                "phaseReactionLog": [],
                "removedEntities": [],
            }
        )

    p0 = player(hand_defs=self_hand_defs, pile_defs=(21, 22), dice=(1, 2), base_id=1)
    p1 = player(hand_defs=opp_hand_ids, pile_defs=opp_pile_ids, dice=opp_dice, base_id=50)
    players = add([p0, p1])
    add(
        {
            "players": players,
            "iterators": add({"id": 0, "random": 1}),
            "phase": "action",
            "currentTurn": 0,
            "roundNumber": 2,
            "winner": None,
            "extensions": add([]),
        }
    )
    return json.dumps({"store": store})


def _belief_from_scaffold(scaffold_json: str, *, samples: int = 3, deck=None):
    state = snapshot_state(None, scaffold_json)
    public = mask_state_for_player(state, perspective_player=0)
    return build_public_belief_state(
        public_state=public,
        root_player=0,
        opponent_deck_cards=deck or (4, 5, 6, 7, 8, 9, 10, 11, 12, 13),
        belief_samples=samples,
        opponent_dice_count=len(state.players[1].dice),
    )


def _root_node(store: list):
    for node in reversed(store):
        if isinstance(node, dict) and "players" in node:
            return node
    raise AssertionError("no root node in store")


def _hidden_content(root_json: str, *, root_player: int = 0) -> dict:
    """Semantic view of the hidden zones a belief root must control."""
    store = json.loads(root_json)["store"]
    root = _root_node(store)
    players = store[int(root["players"]["$"])]
    opponent = store[int(players[1 - root_player]["$"])]
    return {
        "opp_hand_defs": sorted(
            int(store[int(store[int(ref["$"])]["definition"]["$"])].get("id", 0))
            for ref in store[int(opponent["hands"]["$"])]
        ),
        "opp_pile_defs": sorted(
            int(store[int(store[int(ref["$"])]["definition"]["$"])].get("id", 0))
            for ref in store[int(opponent["pile"]["$"])]
        ),
        "opp_dice": [int(d) for d in opponent["dice"]],
        "opp_hand_ids_negative": all(
            int(store[int(ref["$"])]["id"]) < 0 for ref in store[int(opponent["hands"]["$"])]
        ),
    }


# --------------------------------------------------------------------------- #
# fast tests
# --------------------------------------------------------------------------- #
def test_patcher_rewrites_hidden_ids_and_inline_dice():
    scaffold = _mk_state_json()
    belief = _belief_from_scaffold(scaffold)
    out = reconstruct_state_json(
        scaffold,
        public_belief=belief,
        self_hypothesis=belief.self_range.hypotheses[0],
        opponent_hypothesis=belief.opponent_range.hypotheses[0],
        branch_random=123,
    )
    store = json.loads(out)["store"]
    root = _root_node(store)
    players = store[int(root["players"]["$"])]
    opp_node = store[int(players[1]["$"])]
    # inline dice replaced by the sampled particle
    assert opp_node["dice"] == list(belief.opponent_range.hypotheses[0].hidden_dice)
    # opponent hidden card ids are synthetic (negative), not the scaffold's truth
    opp_ids = [store[int(c["$"])]["id"] for c in store[int(opp_node["hands"]["$"])]]
    assert all(int(i) < 0 for i in opp_ids)
    # public state (self ids) preserved
    self_node = store[int(players[0]["$"])]
    self_ids = [store[int(c["$"])]["id"] for c in store[int(self_node["hands"]["$"])]]
    assert all(int(i) > 0 for i in self_ids)


def test_belief_range_depends_only_on_public_view():
    a = _mk_state_json(opp_hand_ids=(4, 5, 6), opp_pile_ids=(7, 8), opp_dice=(3, 4))
    # same public counts, different hidden identities/types
    b = _mk_state_json(opp_hand_ids=(9, 10, 11), opp_pile_ids=(12, 13), opp_dice=(5, 6))
    belief_a = _belief_from_scaffold(a)
    belief_b = _belief_from_scaffold(b)
    assert belief_a.opponent_range == belief_b.opponent_range
    assert belief_a.self_range == belief_b.self_range


def test_reconstruction_hidden_content_invariant_to_hidden_truth():
    a = _mk_state_json(opp_hand_ids=(4, 5, 6), opp_pile_ids=(7, 8), opp_dice=(3, 4))
    b = _mk_state_json(opp_hand_ids=(9, 10, 11), opp_pile_ids=(12, 13), opp_dice=(5, 6))
    deck = (4, 5, 6, 7, 8, 9, 10, 11, 12, 13)
    roots_a = sample_belief_roots(a, root_player=0, opponent_deck_cards=deck, samples=3)
    roots_b = sample_belief_roots(b, root_player=0, opponent_deck_cards=deck, samples=3)
    assert [_hidden_content(r) for r in roots_a] == [_hidden_content(r) for r in roots_b]
    # sanity: the scaffolds' hidden truths genuinely differ
    assert _hidden_content(a)["opp_hand_defs"] != _hidden_content(b)["opp_hand_defs"]


def test_belief_roots_are_distinct_particles():
    scaffold = _mk_state_json()
    roots = sample_belief_roots(
        scaffold, root_player=0, opponent_deck_cards=(4, 5, 6, 7, 8, 9, 10, 11, 12, 13), samples=4
    )
    assert len(roots) == 4
    assert len(set(roots)) == len(roots)


def test_state_patcher_inline_dice_fallback():
    store: list = []
    patcher = _StatePatcher(store, synthetic_hidden_ids=True)
    owner = {"dice": [1, 2, 3]}
    patcher.patch_primitive_zone(owner, "dice", (7, 7))
    assert owner["dice"] == [7, 7]
