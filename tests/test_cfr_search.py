"""M3-CPU S-C (first step): engine-backed MCCFR search (PLAN.md §4.3).

Two layers, mirroring ``tests/test_fork_bridge.py``:

* fast, engine-free tests drive the ``ParticleGame`` adapter and the search policy
  with an injected ``probe_fn`` (a fake single-step fork), pinning the invariants:
  public infoset keys, full legal-action support (a prior/limit never deletes the
  required action), zero-sum frontier/terminal values, and root-solution
  convergence;
* a slow engine smoke runs one ``run_cfr_search_match`` (subprocess fork bridge)
  and checks it stays clean and logs at least one root solution.

The adapter's ``transition`` goes through the fork bridge -- forking must run in
a subprocess, so ``run_cfr_search_match`` refuses ``workers == 0``.
"""

from __future__ import annotations

import json

import pytest

from envs.cfr_search import (
    CFRSearchPolicy,
    CfrMatchResult,
    EngineParticleGame,
    run_cfr_search_match,
)
from envs.fork_bridge import ForkBridge, ForkResult, ForkTask
from envs.fork_search import SearchContext
from train.cfr_solver import RootParticle, public_infoset_key, solve

from reps.public_state import mask_state_for_player
from reps.snapshot import snapshot_state

A, B, SEED = "superconduct_aggro", "natlan_battleship", 5

ROOT_KEYS = (("unknown", 10), ("unknown", 11))


# --------------------------------------------------------------------------- #
# fake single-step fork (a tiny deterministic engine)
# --------------------------------------------------------------------------- #
def _terminal_probe(snapshot: str, prefix0: tuple[int, ...], prefix1: tuple[int, ...]) -> ForkResult:
    """Root decision (player 0, two options) -> terminal child (option 0 wins p0)."""
    if not prefix0 and not prefix1:
        return ForkResult(
            index=0,
            winner=None,
            rounds=0,
            kind="probe",
            acting_player=0,
            legal_options=(0, 1),
            legal_keys=ROOT_KEYS,
            request_type="action",
            infoset_key=("root", 0),
            node_terminal=False,
            frontier_p0=0.5,
        )
    if len(prefix0) == 1 and not prefix1:
        return ForkResult(
            index=0,
            winner=(0 if prefix0[0] == 0 else 1),
            rounds=0,
            kind="probe",
            acting_player=-1,
            legal_options=(),
            legal_keys=(),
            request_type="terminal",
            infoset_key=("terminal", prefix0[0]),
            node_terminal=True,
            frontier_p0=None,
        )
    raise AssertionError(f"unexpected fake prefix ({prefix0}, {prefix1})")


def _frontier_probe(snapshot: str, prefix0: tuple[int, ...], prefix1: tuple[int, ...]) -> ForkResult:
    """A single non-terminal root node with a fixed public frontier estimate."""
    if not prefix0 and not prefix1:
        return ForkResult(
            index=0,
            winner=None,
            rounds=0,
            kind="probe",
            acting_player=0,
            legal_options=(0, 1),
            legal_keys=ROOT_KEYS,
            request_type="action",
            infoset_key=("root",),
            node_terminal=False,
            frontier_p0=0.7,
        )
    raise AssertionError(f"unexpected fake prefix ({prefix0}, {prefix1})")


def _make_game(probe, **overrides) -> EngineParticleGame:
    defaults = dict(
        bridge=None,
        deck0=A,
        deck1=B,
        game_attrs={},
        root_fn=lambda: [RootParticle(state="SNAP", weight=1.0, label="root")],
        root_player=0,
        probe_fn=probe,
    )
    defaults.update(overrides)
    return EngineParticleGame(**defaults)


def _root_node(game: EngineParticleGame):
    return game.root_particles()[0].state


# --------------------------------------------------------------------------- #
# adapter invariants (fast)
# --------------------------------------------------------------------------- #
def test_adapter_reports_probe_infoset_and_legal_actions():
    game = _make_game(_frontier_probe)
    node = _root_node(game)
    assert not game.is_terminal(node)
    assert game.acting_player(node) == 0
    actions = game.legal_actions(node)
    # every captured legal action is exposed, keyed by the semantic (public) key
    assert [a.key for a in actions] == list(ROOT_KEYS)
    assert game.infoset_key(node) == ("root",)


def test_adapter_infoset_key_ignores_hidden_truth():
    """The adapter surfaces ``public_infoset_key``: two states that differ only in
    hidden truth (opponent hand/pile/dice) yield the same node infoset key."""
    a = _mk_state_json(opp_hidden=(4, 5, 6))
    b = _mk_state_json(opp_hidden=(9, 10, 11))
    key_a = public_infoset_key(snapshot_state(None, a), acting_player=0)
    key_b = public_infoset_key(snapshot_state(None, b), acting_player=0)
    assert key_a == key_b

    def probe(snapshot, p0, p1):
        key = public_infoset_key(snapshot_state(None, snapshot), acting_player=0)
        return ForkResult(
            index=0, winner=None, rounds=0, kind="probe", acting_player=0,
            legal_options=(0,), legal_keys=(("k",),), request_type="action",
            infoset_key=key, node_terminal=False, frontier_p0=0.5,
        )

    game_a = EngineParticleGame(
        bridge=None, deck0=A, deck1=B, game_attrs={},
        root_fn=lambda: [RootParticle(state=a)], root_player=0, probe_fn=probe,
    )
    game_b = EngineParticleGame(
        bridge=None, deck0=A, deck1=B, game_attrs={},
        root_fn=lambda: [RootParticle(state=b)], root_player=0, probe_fn=probe,
    )
    assert game_a.infoset_key(_root_node(game_a)) == game_b.infoset_key(_root_node(game_b))


def test_adapter_frontier_value_is_zero_sum():
    game = _make_game(_frontier_probe)
    node = _root_node(game)
    v0 = game.frontier_value(node, 0)
    v1 = game.frontier_value(node, 1)
    assert v0 == pytest.approx(0.4)  # 2 * 0.7 - 1
    assert v1 == pytest.approx(-0.4)
    assert v0 == pytest.approx(-v1)


def test_adapter_terminal_utility_is_zero_sum():
    game = _make_game(_terminal_probe)
    node = _root_node(game)
    winner0 = game.transition(node, ROOT_KEYS[0])  # option 0 -> player 0 wins
    winner1 = game.transition(node, ROOT_KEYS[1])  # option 1 -> player 1 wins
    assert game.is_terminal(winner0) and game.is_terminal(winner1)
    assert game.terminal_utility(winner0, 0) == 1.0
    assert game.terminal_utility(winner0, 1) == -1.0
    assert game.terminal_utility(winner1, 0) == -1.0
    assert game.terminal_utility(winner1, 1) == 1.0


def test_adapter_transition_unknown_key_returns_none():
    game = _make_game(_frontier_probe)
    node = _root_node(game)
    assert game.transition(node, ("not", "a", "key")) is None


def test_candidate_limit_keeps_required_action_and_top_prior():
    # three root options; keep the top-prior option (by prior) + the required base
    keys = (("k", 0), ("k", 1), ("k", 2))

    def probe(snapshot, p0, p1):
        return ForkResult(
            index=0, winner=None, rounds=0, kind="probe", acting_player=0,
            legal_options=(0, 1, 2), legal_keys=keys, request_type="action",
            infoset_key=("root",), node_terminal=False, frontier_p0=0.5,
        )

    game = _make_game(
        probe,
        prior_by_key={keys[0]: 0.1, keys[1]: 0.9, keys[2]: 0.2},
        required_key=keys[2],
        candidate_limit=1,
    )
    node = _root_node(game)
    chosen = [a.key for a in game.legal_actions(node)]
    # top-1 prior is keys[1]; the required base keys[2] is never deleted
    assert chosen == [keys[1], keys[2]]
    # with no limit every legal action survives
    unlimited = _make_game(probe)
    assert [a.key for a in unlimited.legal_actions(_root_node(unlimited))] == list(keys)


# --------------------------------------------------------------------------- #
# solver / teacher targets (fast)
# --------------------------------------------------------------------------- #
def test_solver_converges_to_best_action_and_root_value():
    game = _make_game(_terminal_probe)
    solution = solve(game, iterations=100, depth=1, root_player=0, seed=0)
    assert solution.best_action() == ROOT_KEYS[0]
    assert solution.strategy[ROOT_KEYS[0]] > 0.9
    assert solution.value == pytest.approx(1.0, abs=0.05)


def test_depth_zero_uses_frontier_value():
    game = _make_game(_frontier_probe)
    solution = solve(game, iterations=5, depth=0, root_player=0, seed=0)
    assert solution.value == pytest.approx(0.4, abs=1e-6)
    assert sum(solution.strategy.values()) == pytest.approx(1.0)


def test_root_option_mapping_round_trips():
    game = _make_game(_terminal_probe)
    _root_node(game)
    assert game.root_option_for_key(ROOT_KEYS[1]) == 1
    assert game.root_option_for_key(("nope",)) is None


# --------------------------------------------------------------------------- #
# search policy (fast, fake probe)
# --------------------------------------------------------------------------- #
class _Ctx:
    legal_low_level_codes = (10, 11)
    legal_low_level_specs = ()
    request_type = "action"


class _Built:
    def __init__(self, request_type=None):
        self.context = _Ctx()
        if request_type is not None:
            self.context.request_type = request_type


class _Base:
    name = "base"

    def __init__(self, index: int = 1) -> None:
        self.index = index
        self.calls = 0

    def choose(self, built) -> int:
        self.calls += 1
        return int(built.context.legal_low_level_codes[self.index])


def _policy(probe, **overrides):
    context = SearchContext(
        bridge=None, game_attrs={}, rollout_specs=(None, None), base_seed=1, belief_root=False
    )
    context.update_boundary("SNAP")
    defaults = dict(
        player=0,
        context=context,
        deck0=A,
        deck1=B,
        base=_Base(1),
        iterations=30,
        depth=1,
        belief_root=False,
        probe_fn=probe,
    )
    defaults.update(overrides)
    return CFRSearchPolicy(**defaults), context


def test_search_policy_picks_solution_argmax_and_logs_teacher_targets():
    policy, context = _policy(_terminal_probe)
    code = policy.choose(_Built())
    assert code == 10  # root option 0 (the winning action), not base option 1
    assert policy.base.calls == 1  # base consulted once (RNG parity)
    assert len(context.searches) == 1
    decision = context.searches[0]
    assert decision.base_option == 1
    assert decision.chosen_option == 0
    assert decision.strategy[ROOT_KEYS[0]] > 0.9
    assert decision.teacher_value() == pytest.approx(decision.value)
    assert decision.teacher_policy() == decision.strategy
    payload = decision.as_dict()
    assert payload["searched"] is True and payload["value"] == decision.value


def test_search_policy_budget_knobs_skip_search():
    policy, context = _policy(_terminal_probe, max_searches=0)
    assert policy.choose(_Built()) == 11  # base option 1
    assert context.searches == []

    filtered, context2 = _policy(_terminal_probe, request_types=("choose_active",))
    assert filtered.choose(_Built(request_type="action")) == 11
    assert context2.searches == []


def test_search_policy_skips_unavailable_root_without_spending_budget():
    import envs.cfr_search as cfr_search

    calls = {"n": 0}

    def failing_belief(*args, **kwargs):
        calls["n"] += 1
        raise ValueError("scaffold node is missing 'hands'")

    original = cfr_search.belief_root_particles
    cfr_search.belief_root_particles = failing_belief
    try:
        policy, context = _policy(
            _terminal_probe,
            belief_root=True,
            max_searches=1,
        )
        policy.context.deck_cards = ((1, 2, 3), (4, 5, 6))
        assert policy.choose(_Built()) == 11  # degrades to base
        assert context.searches == []
        assert policy._n_searched == 0  # budget not consumed
        assert calls["n"] == 1
    finally:
        cfr_search.belief_root_particles = original


# --------------------------------------------------------------------------- #
# task/result serialization (fast)
# --------------------------------------------------------------------------- #
def test_fork_task_probe_roundtrip_preserves_kind_and_value_spec():
    task = ForkTask(
        index=0,
        snapshot="{}",
        game_attrs={},
        deck0=A,
        deck1=B,
        prefix0=(0, 1),
        prefix1=(2,),
        kind="probe",
        value_spec="/tmp/ckpt.pt",
    )
    restored = ForkTask.from_dict(task.as_dict())
    assert restored == task
    assert restored.kind == "probe" and restored.value_spec == "/tmp/ckpt.pt"


def test_fork_result_parses_probe_fields():
    payload = {
        "index": 0,
        "winner": None,
        "rounds": 3,
        "kind": "probe",
        "node_terminal": False,
        "acting_player": 1,
        "legal_options": [0, 1],
        "legal_keys": [[["k", 4]], [["k", 5]]],
        "request_type": "action",
        "infoset_key": [["root", 1]],
        "frontier_p0": 0.25,
    }
    result = ForkResult.from_dict(payload)
    assert result.kind == "probe"
    assert result.acting_player == 1
    assert result.legal_keys == ((("k", 4),), (("k", 5),))  # lists -> tuples
    assert result.infoset_key == (("root", 1),)
    assert result.frontier_p0 == pytest.approx(0.25)


def test_run_cfr_search_match_rejects_inprocess_bridge():
    with pytest.raises(ValueError, match="subprocess"):
        run_cfr_search_match(A, B, bridge=ForkBridge(workers=0), seed=SEED)


# --------------------------------------------------------------------------- #
# frontier value: documented heuristic + checkpoint fallback (fast)
# --------------------------------------------------------------------------- #
def test_heuristic_frontier_prefers_the_healthier_side():
    from envs.cfr_probe import heuristic_frontier_probability

    state = snapshot_state(None, _mk_state_json(opp_hidden=(4,)))
    p = heuristic_frontier_probability(state)
    assert 0.0 <= p <= 1.0
    assert p > 0.5  # p0 holds more cards with otherwise equal boards

    state_opp = snapshot_state(None, _mk_state_json(opp_hidden=(4, 5, 6, 7)))
    assert heuristic_frontier_probability(state_opp) < 0.5


def test_frontier_falls_back_to_heuristic_on_bad_checkpoint():
    from envs.cfr_probe import _frontier_probability, heuristic_frontier_probability

    state = snapshot_state(None, _mk_state_json(opp_hidden=(4,)))

    class _Ctx:
        player_view = state

    class _Built:
        context = _Ctx()

    expected = heuristic_frontier_probability(state)
    assert _frontier_probability(_Built(), value_spec="/nonexistent/checkpoint.pt") == pytest.approx(expected)
    assert _frontier_probability(_Built(), value_spec="") == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# engine smoke (slow, subprocess fork bridge)
# --------------------------------------------------------------------------- #
@pytest.mark.slow
def test_run_cfr_search_match_smoke():
    with ForkBridge(workers=2) as bridge:
        result = run_cfr_search_match(
            A,
            B,
            bridge=bridge,
            seed=SEED,
            search_players=(0,),
            iterations=2,
            depth=1,
            candidate_limit=2,
            belief_root=True,
            belief_samples=1,
            max_searches=1,
            request_types=("action",),
        )
    assert isinstance(result, CfrMatchResult)
    record = result.record
    assert record.error is None, record.error
    assert not record.truncated
    assert record.fallbacks0 == 0 and record.fallbacks1 == 0
    assert result.searches, "expected at least one searched decision"
    for decision in result.searches:
        assert decision.searched
        assert 0 <= decision.chosen_option < decision.option_count
        assert decision.strategy  # a valid root average strategy
        assert sum(decision.strategy.values()) == pytest.approx(1.0, abs=1e-6)
        assert all(-1.0 <= v <= 1.0 for v in decision.action_values.values())


# --------------------------------------------------------------------------- #
# minimal engine-format state scaffold (mirrors tests/test_belief_root.py)
# --------------------------------------------------------------------------- #
def _mk_state_json(*, opp_hidden: tuple[int, ...]) -> str:
    store: list = []

    def add(node):
        store.append(node)
        return {"$": len(store) - 1}

    char_def = add({"$$": "characters", "id": 100})

    def entity(card_id, definition_id):
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
                            "health": 10, "maxHealth": 10, "energy": 0,
                            "maxEnergy": 3, "alive": 1,
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

    p0 = player(hand_defs=(11, 12), pile_defs=(21, 22), dice=(1, 2), base_id=1)
    p1 = player(hand_defs=opp_hidden, pile_defs=(7, 8), dice=(3, 4), base_id=50)
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
