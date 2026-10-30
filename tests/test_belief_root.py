"""M3-CPU S-A: public-belief rooting for fork search (PLAN.md §4.3, gaps G1+G3).

The practical resolver forked the *live* boundary snapshot, whose hidden state is
the single true determinization (charter §5.2 violation). These tests pin the
replacement: roots are built from the player-masked public state + a sampled
particle, and are **invariant to the scaffold's hidden truth**.

Two layers:

* fast, engine-free tests on hand-built ``store``/``$`` state JSON: the patcher
  rewrites hidden zones and inline dice, public ranges depend only on public
  fields, and reconstruction is byte-identical for two scaffolds that share a
  public view but differ in hidden truth.
* slow engine tests: determinized roots load/fork/play out, and a belief-rooted
  search match runs clean with the expected number of roots per decision.
"""

from __future__ import annotations

import json

import pytest

from reps.belief import (
    _StatePatcher,
    build_public_belief_state,
    reconstruct_state_json,
    sample_belief_roots,
)
from reps.public_state import mask_state_for_player
from reps.snapshot import snapshot_state

DECK_A, DECK_B = "superconduct_aggro", "natlan_battleship"


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


# --------------------------------------------------------------------------- #
# fork-search integration (fast, no engine)
# --------------------------------------------------------------------------- #
def test_belief_roots_for_empty_decklist_falls_back_to_live_snapshot():
    from envs.fork_search import SearchContext

    context = SearchContext(
        bridge=None,
        game_attrs={},
        rollout_specs=(None, None),
        base_seed=0,
        belief_root=True,
        deck_cards=((), ()),
    )
    assert context.belief_roots_for(snapshot="SNAPSHOT", acting_player=0) == ["SNAPSHOT"]


def test_observe_public_record_tracks_opponent_reveals():
    from envs.fork_search import _observe_public_record
    from reps.public_state import PublicStateTracker
    from reps.schema import DecisionType, OptionKind

    class _Spec:
        request_type = DecisionType.ACTION
        kind = OptionKind.ACTION_PLAY_CARD
        subject_definition_id = 42
        select_card_definition_id = 0
        discarded_card_definition_id = 0
        target_slots = ()
        used_dice = (1, 2)
        auto_selected_dice = ()
        choose_active_slot = 0
        switch_hand_slot_mask = 0
        reroll_dice_mask = 0
        discarded_hand_slot = 0
        target_dice = 0

    class _Context:
        legal_low_level_codes = (7,)
        legal_low_level_specs = (_Spec(),)
        request_type = DecisionType.ACTION
        player_view = None

    class _Built:
        context = _Context()

    tracker = PublicStateTracker(player_id=0)
    _observe_public_record(tracker, player=1, built=_Built(), code=7)
    assert tracker.revealed_opponent_card_definition_ids() == (42,)
    # The acting player's own plays are not counted as opponent reveals.
    _observe_public_record(tracker, player=0, built=_Built(), code=7)
    assert tracker.revealed_opponent_card_definition_ids() == (42,)


def test_observe_public_record_marks_own_decision_payload_known():
    from envs.fork_search import _observe_public_record
    from reps.public_state import PublicStateTracker
    from reps.schema import DecisionType

    class _Context:
        legal_low_level_codes = ()
        legal_low_level_specs = ()
        request_type = DecisionType.REROLL_DICE  # hidden by request type
        player_view = None

    class _Built:
        context = _Context()

    own = PublicStateTracker(player_id=0)
    other = PublicStateTracker(player_id=1)
    _observe_public_record(own, player=0, built=_Built(), code=0)
    _observe_public_record(other, player=0, built=_Built(), code=0)
    assert own.public_history()[-1].known_payload is True   # own decision
    assert other.public_history()[-1].known_payload is False  # hidden reroll


def test_observe_public_record_spec_less_uses_code_lookup(monkeypatch):
    import reps.action_hierarchy as action_hierarchy

    from envs.fork_search import _observe_public_record
    from reps.public_state import PublicStateTracker
    from reps.schema import DecisionType

    monkeypatch.setattr(
        action_hierarchy, "try_semantic_action_key_for_code",
        lambda code: (("card_definition_id", 77),),
    )
    monkeypatch.setattr(action_hierarchy, "try_public_spent_dice_for_code", lambda code: 3)

    class _Context:
        legal_low_level_codes = ()
        legal_low_level_specs = ()
        request_type = DecisionType.ACTION
        player_view = None

    class _Built:
        context = _Context()

    tracker = PublicStateTracker(player_id=0)
    _observe_public_record(tracker, player=1, built=_Built(), code=5)
    record = tracker.public_history()[-1]
    # No matching spec -> fall back to the canonical code lookup (as observe_step does).
    assert dict(record.semantic_key)["card_definition_id"] == 77
    assert record.used_dice_count == 3
    assert tracker.revealed_opponent_card_definition_ids() == (77,)


# --------------------------------------------------------------------------- #
# slow engine tests
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def live_boundary():
    from common.seeding import derive_seed
    from envs.decks import deck_spec
    from envs.match import PolicyPlayer, _build_create_param
    from envs.policy import expert_policy
    from envs.snapshot import capture_snapshot
    from gitcg import Game, low_level

    seed = 3
    game = Game(create_param=_build_create_param(deck_spec(DECK_A), deck_spec(DECK_B), seed=seed))
    game.set_attr(low_level.ATTR_PLAYER_ALWAYS_OMNI_0, 1)
    game.set_attr(low_level.ATTR_PLAYER_ALWAYS_OMNI_1, 1)
    game.set_player(0, PolicyPlayer(0, expert_policy(DECK_A, seed=derive_seed(seed, "p0"))))
    game.set_player(1, PolicyPlayer(1, expert_policy(DECK_B, seed=derive_seed(seed, "p1"))))
    game.start()
    snap = None
    for steps in range(120):
        if game.is_resumable() and steps > 45:
            snap = capture_snapshot(game)
            break
        game.step()
    assert snap is not None
    return snap


@pytest.mark.slow
def test_determinized_roots_fork_and_play_out(live_boundary):
    from envs.decks import deck_spec
    from envs.fork_bridge import ForkTask, run_fork_task
    from gitcg import low_level

    game_attrs = {
        low_level.ATTR_PLAYER_ALWAYS_OMNI_0: 1,
        low_level.ATTR_PLAYER_ALWAYS_OMNI_1: 1,
    }
    roots = sample_belief_roots(
        live_boundary, root_player=0, opponent_deck_cards=deck_spec(DECK_B).cards, samples=2
    )
    for index, root in enumerate(roots):
        task = ForkTask(
            index=index,
            snapshot=root,
            game_attrs=game_attrs,
            deck0=DECK_A,
            deck1=DECK_B,
            prefix0=(),
            prefix1=(),
            inject_player=0,
            inject_ordinal=1,
            inject_option=0,
            rollout0=f"expert:{DECK_A}",
            rollout1=f"expert:{DECK_B}",
            rollout_seed=1000 + index,
            max_steps=5000,
        )
        result = run_fork_task(task)
        assert result["error"] is None, result["error"]
        assert result["winner"] in (0, 1)


@pytest.mark.slow
def test_belief_roots_invariant_to_live_hidden_truth(live_boundary):
    from envs.decks import deck_spec
    from reps.belief import _sample_branch_random

    deck = deck_spec(DECK_B).cards
    state = snapshot_state(None, live_boundary)
    public = mask_state_for_player(state, perspective_player=0)
    belief = build_public_belief_state(
        public_state=public,
        root_player=0,
        opponent_deck_cards=deck,
        belief_samples=3,
        opponent_dice_count=len(state.players[1].dice),
    )
    self_hyp = belief.self_range.hypotheses[0]
    alt = belief.opponent_range.hypotheses[-1]
    alt_scaffold = reconstruct_state_json(
        live_boundary,
        public_belief=belief,
        self_hypothesis=self_hyp,
        opponent_hypothesis=alt,
        branch_random=_sample_branch_random(
            public_belief=belief,
            self_hypothesis=self_hyp,
            opponent_hypothesis=alt,
            reconstruction_counter=999,
        ),
    )
    roots_a = sample_belief_roots(live_boundary, root_player=0, opponent_deck_cards=deck, samples=4)
    roots_b = sample_belief_roots(alt_scaffold, root_player=0, opponent_deck_cards=deck, samples=4)
    assert roots_a == roots_b


@pytest.mark.slow
def test_belief_root_search_runs_clean():
    from envs.fork_bridge import ForkBridge
    from envs.fork_search import run_search_match
    from gitcg import low_level

    game_attrs = {
        low_level.ATTR_PLAYER_ALWAYS_OMNI_0: 1,
        low_level.ATTR_PLAYER_ALWAYS_OMNI_1: 1,
    }
    with ForkBridge(workers=2) as bridge:
        result = run_search_match(
            DECK_A,
            DECK_B,
            bridge=bridge,
            seed=5,
            search_players=(0,),
            rollouts=1,
            top_k=2,
            max_searches=3,
            rollout_specs=(f"expert:{DECK_A}", f"expert:{DECK_B}"),
            game_attrs=game_attrs,
            belief_root=True,
            belief_samples=3,
        )
    assert result.record.error is None
    assert result.searches, "expected at least one searched decision"
    assert all(s.belief_roots >= 2 for s in result.searches)
