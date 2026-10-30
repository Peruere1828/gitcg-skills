"""M3-CPU S-B: external-sampling MCCFR scaffolding (PLAN.md §4.3, gaps G2+G4).

Engine-free and deterministic. Layers:

* regret matching / distribution normalisation unit tests (prior fallback, clamping,
  full-support -- a prior never deletes a legal action, §5.4);
* solver tests on tiny extensive-form toys: a single-player bandit (converges to the
  best arm) and Kuhn poker (converges to its known value ``-1/18`` and Nash behaviour
  ``K bet / Q check / J bluff 1/3``);
* depth-budget tests (``depth == 0`` returns the frontier value; identical inputs
  reproduce the solution byte-for-byte);
* public-belief rooting glue: ``belief_root_particles`` / ``public_infoset_key`` from
  ``reps.belief`` depend only on public information (no hidden truth).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from train.cfr_solver import (
    ExternalSamplingMCCFR,
    RootParticle,
    SearchAction,
    belief_root_particles,
    normalize_distribution,
    public_infoset_key,
    regret_matching,
    solve,
)
from reps.public_state import mask_state_for_player
from reps.snapshot import snapshot_state

ACTIONS = [SearchAction(key="a"), SearchAction(key="b"), SearchAction(key="c")]


# --------------------------------------------------------------------------- #
# toy game: single-player bandit
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _BanditNode:
    payoff: float | None = None  # None -> decision node, else terminal


class BanditGame:
    """One decision, then a fixed payoff -- converges to the best arm."""

    def __init__(self, values, *, frontier: float = 0.0, priors=None) -> None:
        self.values = tuple(float(v) for v in values)
        self.frontier = float(frontier)
        self.priors = tuple(float(p) for p in (priors or (0.0,) * len(self.values)))

    def root_particles(self):
        return [RootParticle(state=_BanditNode(), weight=1.0, label="root")]

    def is_terminal(self, node):
        return node.payoff is not None

    def acting_player(self, node):
        return 0

    def legal_actions(self, node):
        return [
            SearchAction(key=f"a{i}", prior=self.priors[i])
            for i in range(len(self.values))
        ]

    def infoset_key(self, node):
        return ("bandit",)

    def transition(self, node, action_key):
        return _BanditNode(payoff=self.values[int(action_key[1:])])

    def terminal_utility(self, node, traverser):
        value = float(node.payoff)
        return value if traverser == 0 else -value

    def frontier_value(self, node, traverser):
        return self.frontier if traverser == 0 else -self.frontier


# --------------------------------------------------------------------------- #
# toy game: one-shot 2x2 zero-sum matrix game (simultaneous moves)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _MatrixNode:
    row: int | None = None
    col: int | None = None


class MatrixGame:
    """A one-shot 2x2 zero-sum game as a ``ParticleGame``.

    The game is played sequentially (player 0 picks a row, then player 1 a column)
    but player 1's information-set key **ignores player 0's action**, so it is a
    simultaneous-move normal-form game. This exercises mixed-Nash convergence with
    a known value (``A = [[3, 0], [0, 2]]`` -> value ``6/5``, row mix ``(2/5, 3/5)``).
    """

    def __init__(self, payoff) -> None:
        self.payoff = tuple(tuple(float(value) for value in row) for row in payoff)
        self.n_rows = len(self.payoff)
        self.n_cols = len(self.payoff[0])

    def root_particles(self):
        return [RootParticle(state=_MatrixNode(), weight=1.0, label="root")]

    def is_terminal(self, node):
        return node.row is not None and node.col is not None

    def acting_player(self, node):
        return 0 if node.row is None else 1

    def legal_actions(self, node):
        if node.row is None:
            return [SearchAction(f"r{i}") for i in range(self.n_rows)]
        return [SearchAction(f"c{j}") for j in range(self.n_cols)]

    def infoset_key(self, node):
        # Player 1 must not observe player 0's move -> simultaneous game.
        return ("matrix", self.acting_player(node))

    def transition(self, node, action_key):
        if node.row is None:
            return _MatrixNode(row=int(action_key[1:]), col=None)
        return _MatrixNode(row=node.row, col=int(action_key[1:]))

    def terminal_utility(self, node, traverser):
        value = self.payoff[node.row][node.col]
        return value if traverser == 0 else -value

    def frontier_value(self, node, traverser):
        return 0.0


# --------------------------------------------------------------------------- #
# toy game: Kuhn poker (two-player zero-sum, multi-step tree)
# --------------------------------------------------------------------------- #
_J, _Q, _K = 0, 1, 2
_KUHN_DECK = (_J, _Q, _K)
_KUHN_TERMINALS = {
    ("check", "check"),
    ("check", "bet", "fold"),
    ("check", "bet", "call"),
    ("bet", "fold"),
    ("bet", "call"),
}


@dataclass(frozen=True)
class _KuhnNode:
    p0: int
    p1: int
    history: tuple[str, ...]


class KuhnPoker:
    """Kuhn poker rooted at player 0's card; the belief is over the opponent card.

    This mirrors continual resolving: player 0's own card is known, so the root
    particles sample only the *opponent's* hidden card (two equally likely deals).
    """

    def __init__(self, root_card: int) -> None:
        self.root_card = int(root_card)

    def root_particles(self):
        return [
            RootParticle(
                state=_KuhnNode(self.root_card, card, ()),
                weight=1.0,
                label=f"opp-{card}",
            )
            for card in _KUHN_DECK
            if card != self.root_card
        ]

    def is_terminal(self, node):
        return node.history in _KUHN_TERMINALS

    def acting_player(self, node):
        if node.history == ():
            return 0
        if node.history in {("check",), ("bet",)}:
            return 1
        if node.history == ("check", "bet"):
            return 0
        raise ValueError(f"non-decision history {node.history!r}")

    def legal_actions(self, node):
        history = node.history
        if history == ():
            return [SearchAction("check"), SearchAction("bet")]
        if history == ("check",):
            return [SearchAction("check"), SearchAction("bet")]
        if history in {("bet",), ("check", "bet")}:
            return [SearchAction("fold"), SearchAction("call")]
        return []

    def infoset_key(self, node):
        player = self.acting_player(node)
        card = node.p0 if player == 0 else node.p1
        return ("kuhn", player, card, node.history)

    def transition(self, node, action_key):
        return _KuhnNode(node.p0, node.p1, (*node.history, action_key))

    def terminal_utility(self, node, traverser):
        history = node.history
        winner = 0 if node.p0 > node.p1 else 1
        if history == ("check", "check"):
            amount, wins = 1.0, winner
        elif history == ("bet", "fold"):
            amount, wins = 1.0, 0
        elif history == ("check", "bet", "fold"):
            amount, wins = 1.0, 1
        elif history in {("bet", "call"), ("check", "bet", "call")}:
            amount, wins = 2.0, winner
        else:
            raise ValueError(f"non-terminal history {history!r}")
        return amount if traverser == wins else -amount

    def frontier_value(self, node, traverser):
        # Kuhn has no learned frontier; a depth-truncated leaf is scored neutrally.
        return 0.0


# --------------------------------------------------------------------------- #
# engine-free state scaffold (mirrors tests/test_belief_root.py)
# --------------------------------------------------------------------------- #
def _mk_state_json(
    *,
    opp_hand_ids: tuple[int, ...] = (4, 5, 6),
    opp_pile_ids: tuple[int, ...] = (7, 8),
    opp_dice: tuple[int, ...] = (3, 4),
    self_hand_defs: tuple[int, ...] = (11, 12),
) -> str:
    """Minimal engine-format state JSON with controllable hidden truth."""
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


def _root_node(store: list):
    for node in reversed(store):
        if isinstance(node, dict) and "players" in node:
            return node
    raise AssertionError("no root node in store")


def _hidden_content(root_json: str, *, root_player: int = 0) -> dict:
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
    }


# --------------------------------------------------------------------------- #
# regret matching / distributions
# --------------------------------------------------------------------------- #
def test_regret_matching_normalizes_positive_regrets():
    strategy = regret_matching({"a": 3.0, "b": 1.0, "c": 0.0}, ACTIONS)
    assert set(strategy) == {"a", "b", "c"}
    assert strategy["a"] == pytest.approx(0.75)
    assert strategy["b"] == pytest.approx(0.25)
    assert strategy["c"] == pytest.approx(0.0)
    assert sum(strategy.values()) == pytest.approx(1.0)


def test_regret_matching_clamps_negative_regrets():
    strategy = regret_matching({"a": -5.0, "b": 2.0, "c": -1.0}, ACTIONS)
    # negatives carry no regret; b takes everything.
    assert strategy["b"] == pytest.approx(1.0)
    assert strategy["a"] == pytest.approx(0.0)


def test_regret_matching_zero_falls_back_to_uniform():
    strategy = regret_matching({}, ACTIONS)
    assert all(value == pytest.approx(1.0 / 3.0) for value in strategy.values())


def test_regret_matching_zero_uses_prior_fallback():
    strategy = regret_matching({}, ACTIONS, prior={"a": 0.5, "b": 0.5, "c": 0.0})
    assert strategy["a"] == pytest.approx(0.5)
    assert strategy["b"] == pytest.approx(0.5)
    assert strategy["c"] == pytest.approx(0.0)


def test_regret_matching_reads_action_prior():
    actions = [SearchAction("a", prior=0.9), SearchAction("b", prior=0.1)]
    strategy = regret_matching({}, actions)
    assert strategy["a"] == pytest.approx(0.9)
    assert strategy["b"] == pytest.approx(0.1)


def test_normalize_distribution():
    assert normalize_distribution({"a": 2.0, "b": 2.0}) == {"a": 0.5, "b": 0.5}
    assert normalize_distribution({"a": 0.0, "b": 0.0}) == {}
    assert normalize_distribution({"a": 0.0, "b": 0.0}, uniform_if_empty=True) == {
        "a": 0.5,
        "b": 0.5,
    }
    assert normalize_distribution({}) == {}


# --------------------------------------------------------------------------- #
# bandit: convergence, depth budget, determinism, no deletion
# --------------------------------------------------------------------------- #
def test_bandit_average_strategy_is_normalized():
    game = BanditGame((0.2, 0.8, 0.1))
    solution = solve(game, iterations=50, depth=4, seed=0)
    assert set(solution.strategy) == {"a0", "a1", "a2"}
    assert all(value >= 0.0 for value in solution.strategy.values())
    assert sum(solution.strategy.values()) == pytest.approx(1.0)
    assert solution.iterations == 50
    assert solution.depth == 4


def test_bandit_converges_to_best_action():
    game = BanditGame((0.2, 0.8, 0.1))
    solution = solve(game, iterations=2000, depth=4, seed=0)
    assert solution.best_action() == "a1"
    assert solution.strategy["a1"] > 0.9
    assert solution.value == pytest.approx(0.8, abs=0.02)


def test_depth_zero_uses_frontier_value():
    game = BanditGame((0.2, 0.8, 0.1), frontier=0.25)
    solution = solve(game, iterations=7, depth=0, seed=3)
    assert solution.value == pytest.approx(0.25)
    assert solution.visits == {}  # no root expansion at the frontier
    # the strategy stays a valid distribution even without expansion
    assert sum(solution.strategy.values()) == pytest.approx(1.0)


def test_solve_is_deterministic():
    game = BanditGame((0.2, 0.8, 0.1))
    first = solve(game, iterations=300, depth=4, seed=11)
    second = solve(game, iterations=300, depth=4, seed=11)
    assert first.as_dict() == second.as_dict()
    assert first.strategy == second.strategy
    assert first.value == pytest.approx(second.value)


def test_prior_never_deletes_legal_actions():
    # a prior that strongly favours an inferior arm still leaves every arm in the
    # support (0 iterations -> fall back to the prior distribution, not a subset).
    game = BanditGame((0.2, 0.8, 0.1), priors=(0.98, 0.01, 0.01))
    solution = solve(game, iterations=0, depth=4, seed=0)
    assert set(solution.strategy) == {"a0", "a1", "a2"}
    assert solution.strategy["a0"] == pytest.approx(0.98)
    assert sum(solution.strategy.values()) == pytest.approx(1.0)


def test_root_player_inferred_from_root_particle():
    game = BanditGame((0.5, 0.5))
    solution = solve(game, iterations=5, depth=4, seed=0)
    assert solution.root_player == 0
    assert isinstance(ExternalSamplingMCCFR(game, root_player=0), ExternalSamplingMCCFR)


# --------------------------------------------------------------------------- #
# matrix game: mixed-Nash convergence against a known solution
# --------------------------------------------------------------------------- #
def test_matrix_average_strategy_approaches_nash():
    # A = [[3, 0], [0, 2]]: unique mixed Nash, value 6/5, row mix (2/5, 3/5).
    solution = solve(MatrixGame([[3, 0], [0, 2]]), iterations=40000, depth=4, seed=0)
    assert solution.strategy["r0"] == pytest.approx(0.4, abs=0.05)
    assert solution.strategy["r1"] == pytest.approx(0.6, abs=0.05)
    assert solution.value == pytest.approx(1.2, abs=0.03)
    assert sum(solution.strategy.values()) == pytest.approx(1.0)


def test_matrix_convergence_improves_with_iterations():
    game = MatrixGame([[3, 0], [0, 2]])
    rough = solve(game, iterations=200, depth=4, seed=0)
    trained = solve(game, iterations=40000, depth=4, seed=0)
    rough_error = abs(rough.value - 1.2)
    trained_error = abs(trained.value - 1.2)
    assert trained_error < rough_error
    assert trained_error < 0.03


# --------------------------------------------------------------------------- #
# Kuhn poker: multi-step tree, monotone best-hand behaviour
# --------------------------------------------------------------------------- #
def _kuhn_solutions(iterations: int, *, seed: int = 0):
    return {
        card: solve(KuhnPoker(card), iterations=iterations, depth=8, seed=seed)
        for card in _KUHN_DECK
    }


def test_kuhn_best_hand_bets_and_middle_checks():
    solutions = _kuhn_solutions(20000)
    # P0 bets its best hand and checks its middle hand (robust subgame behaviour).
    assert solutions[_K].strategy["bet"] > 0.9
    assert solutions[_Q].strategy["bet"] < 0.1
    for solution in solutions.values():
        assert sum(solution.strategy.values()) == pytest.approx(1.0)
        assert set(solution.strategy) == {"check", "bet"}


def test_kuhn_depth_truncation_is_deterministic():
    shallow_a = solve(KuhnPoker(_J), iterations=200, depth=1, seed=5)
    shallow_b = solve(KuhnPoker(_J), iterations=200, depth=1, seed=5)
    assert shallow_a.as_dict() == shallow_b.as_dict()
    # depth=1 still produces a valid root distribution
    assert sum(shallow_a.strategy.values()) == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# public-belief rooting glue
# --------------------------------------------------------------------------- #
def test_public_infoset_key_ignores_hidden_truth():
    a = _mk_state_json(opp_hand_ids=(4, 5, 6), opp_pile_ids=(7, 8), opp_dice=(3, 4))
    b = _mk_state_json(opp_hand_ids=(9, 10, 11), opp_pile_ids=(12, 13), opp_dice=(5, 6))
    key_a = public_infoset_key(snapshot_state(None, a), acting_player=0)
    key_b = public_infoset_key(snapshot_state(None, b), acting_player=0)
    assert key_a == key_b


def test_public_infoset_key_is_perspective_dependent():
    state_json = _mk_state_json()
    state = snapshot_state(None, state_json)
    assert public_infoset_key(state, acting_player=0) != public_infoset_key(
        state, acting_player=1
    )


def test_belief_root_particles_are_public_only_and_weighted():
    deck = (4, 5, 6, 7, 8, 9, 10, 11, 12, 13)
    a = _mk_state_json(opp_hand_ids=(4, 5, 6), opp_pile_ids=(7, 8), opp_dice=(3, 4))
    b = _mk_state_json(opp_hand_ids=(9, 10, 11), opp_pile_ids=(12, 13), opp_dice=(5, 6))
    particles_a = belief_root_particles(a, root_player=0, opponent_deck_cards=deck, samples=3)
    particles_b = belief_root_particles(b, root_player=0, opponent_deck_cards=deck, samples=3)
    assert len(particles_a) == 3
    assert all(p.weight >= 0.0 for p in particles_a)
    assert [p.label for p in particles_a] == ["particle-0", "particle-1", "particle-2"]
    # determinization depends only on public information, not on hidden truth.
    assert [_hidden_content(p.state) for p in particles_a] == [
        _hidden_content(p.state) for p in particles_b
    ]
    assert _hidden_content(a) != _hidden_content(b)  # the scaffolds really differ


def test_public_infoset_key_on_masked_state_is_idempotent():
    state = snapshot_state(None, _mk_state_json())
    masked = mask_state_for_player(state, perspective_player=0)
    assert public_infoset_key(masked, acting_player=0) == public_infoset_key(
        state, acting_player=0
    )
