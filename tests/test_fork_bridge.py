"""L5.4 fork bridge + fork-based search policy (PLAN.md D13/I10, WS3).

Two layers:

* fast unit tests (no engine): policy plumbing, task JSON round-trip, candidate
  selection, and the search policy's choice logic against a fake bridge.
* slow engine tests: a pure-replay fork reproduces the live terminal both
  in-process (``run_fork_task``) and across a subprocess worker
  (``ForkBridge``), and a bounded search match runs clean.

The in-process path must never be used from inside a live callback -- that nests
two engines in one JS runtime and corrupts it; ``run_search_match`` refuses
``workers == 0`` for this reason.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from common.seeding import derive_seed
from envs.decks import deck_spec
from envs.fork_bridge import (
    ForkBridge,
    ForkResult,
    ForkTask,
    InjectionPolicy,
    PositionalReplayPolicy,
    PrefixRolloutPolicy,
    replay_fork_task,
    run_fork_task,
)
from envs.fork_search import (
    ForkSearchPolicy,
    SearchContext,
    _select_candidates,
    run_search_match,
)
from envs.match import PolicyPlayer, _build_create_param
from envs.policy import build_policy
from envs.snapshot import capture_snapshot
from gitcg import Game

A, B, SEED = "superconduct_aggro", "natlan_battleship", 3


# --------------------------------------------------------------------------- #
# fast unit tests
# --------------------------------------------------------------------------- #
@dataclass
class _Ctx:
    legal_low_level_codes: tuple[int, ...]
    request_type: str = "action"


@dataclass
class _Built:
    context: _Ctx


class _Base:
    name = "base"

    def __init__(self, index: int) -> None:
        self.index = index
        self.calls = 0

    def choose(self, built) -> int:
        self.calls += 1
        return int(built.context.legal_low_level_codes[self.index])


class _FakeBridge:
    workers = 1

    def __init__(self, payoffs: dict[int, int | None]) -> None:
        self.payoffs = payoffs
        self.calls = 0

    def evaluate(self, tasks):
        self.calls += 1
        return [
            ForkResult(index=t.index, winner=self.payoffs[t.inject_option], rounds=5)
            for t in tasks
        ]


def test_replay_policy_returns_positional_option():
    policy = PositionalReplayPolicy([1, 0])
    built = _Built(_Ctx((100, 101, 102)))
    assert policy.choose(built) == 101
    assert policy.choose(built) == 100


def test_replay_policy_raises_when_exhausted():
    policy = PositionalReplayPolicy([])
    with pytest.raises(RuntimeError, match="exhausted"):
        policy.choose(_Built(_Ctx((1, 2, 3))))


def test_injection_policy_forces_only_its_ordinal():
    prefix = _Base(0)
    rollout = _Base(2)
    inject = InjectionPolicy(prefix=prefix, rollout=rollout, ordinal=2, option_index=1)
    built = _Built(_Ctx((10, 11, 12)))
    assert inject.choose(built) == 10  # ordinal 1 -> prefix option 0
    assert inject.choose(built) == 11  # ordinal 2 -> forced option 1
    assert inject.choose(built) == 12  # ordinal 3 -> rollout option 2


def test_prefix_rollout_switches_after_prefix():
    replay = _Base(0)
    rollout = _Base(2)
    policy = PrefixRolloutPolicy(replay=replay, rollout=rollout, prefix_len=2)
    built = _Built(_Ctx((10, 11, 12)))
    assert [policy.choose(built) for _ in range(3)] == [10, 10, 12]


def test_select_candidates_spreads_and_caps():
    assert _select_candidates(3, None) == [0, 1, 2]
    assert _select_candidates(1, 4) == [0]
    spread = _select_candidates(11, 4)
    assert spread[0] == 0 and spread[-1] == 10 and len(spread) == 4


def test_select_candidates_uses_prior_and_keeps_required():
    # top-2 by prior are indices 1 and 3; the required base choice 0 is kept too.
    chosen = _select_candidates(5, 2, prior=[0.1, 0.9, 0.2, 0.8, 0.3], required=0)
    assert chosen == [0, 1, 3]
    # top_k None -> all options regardless of prior.
    assert _select_candidates(5, None, prior=[0.1, 0.9, 0.2, 0.8, 0.3]) == [0, 1, 2, 3, 4]


def test_fork_task_json_roundtrip():
    task = ForkTask(
        index=7,
        snapshot="{snap}",
        game_attrs={101: 1, 102: 1},
        deck0=A,
        deck1=B,
        prefix0=(1, 2),
        prefix1=(0,),
        inject_player=0,
        inject_ordinal=3,
        inject_option=2,
        rollout_seed=99,
    )
    restored = ForkTask.from_dict(task.as_dict())
    assert restored == task


def test_search_policy_picks_highest_mean_option():
    codes = (10, 11, 12, 13)
    base = _Base(0)
    # Option 2 always wins for player 0; base option 0 always loses.
    bridge = _FakeBridge({0: 1, 1: 1, 2: 0, 3: 1})
    context = SearchContext(
        bridge=bridge, game_attrs={}, rollout_specs=(None, None), base_seed=1
    )
    context.update_boundary("snap")
    policy = ForkSearchPolicy(
        player=0, context=context, deck0=A, deck1=B, base=base, rollouts=1, top_k=None
    )
    chosen = policy.choose(_Built(_Ctx(codes)))
    assert chosen == 12  # option index 2 -> code 12
    assert base.calls == 1
    assert bridge.calls == 1
    assert len(context.searches) == 1
    decision = context.searches[0]
    assert decision.searched and decision.chosen_option == 2
    assert decision.means[2] == pytest.approx(1.0)
    assert decision.means[0] == pytest.approx(0.0)


def test_search_policy_ties_prefer_base_choice():
    codes = (10, 11, 12)
    base = _Base(1)
    bridge = _FakeBridge({0: 1, 1: 1, 2: 1})  # all win -> tie
    context = SearchContext(
        bridge=bridge, game_attrs={}, rollout_specs=(None, None), base_seed=1
    )
    context.update_boundary("snap")
    policy = ForkSearchPolicy(
        player=0, context=context, deck0=A, deck1=B, base=base, rollouts=1, top_k=None
    )
    assert policy.choose(_Built(_Ctx(codes))) == 11  # base option 1


def test_search_policy_budget_knobs_skip_search():
    codes = (10, 11)
    bridge = _FakeBridge({0: 0, 1: 1})
    context = SearchContext(
        bridge=bridge, game_attrs={}, rollout_specs=(None, None), base_seed=1
    )
    context.update_boundary("snap")
    every_other = ForkSearchPolicy(
        player=0, context=context, deck0=A, deck1=B, base=_Base(0),
        rollouts=1, top_k=None, search_every=2,
    )
    assert every_other.choose(_Built(_Ctx(codes))) == 10  # call 1 -> skipped
    assert bridge.calls == 0
    capped = ForkSearchPolicy(
        player=0, context=context, deck0=A, deck1=B, base=_Base(0),
        rollouts=1, top_k=None, max_searches=1,
    )
    capped.choose(_Built(_Ctx(codes)))
    capped.choose(_Built(_Ctx(codes)))
    assert len(context.searches) == 1


class _FakePrior:
    """Prior scorer over the legal options (mirrors PriorResolver.distribution)."""

    def __init__(self, probs) -> None:
        self.probs = list(probs)
        self.calls = 0

    def distribution(self, built):
        self.calls += 1
        return list(self.probs)


def test_search_policy_prior_ranks_candidates_and_keeps_base():
    codes = (10, 11, 12, 13, 14)
    base = _Base(0)
    prior = _FakePrior([0.05, 0.6, 0.1, 0.2, 0.05])  # top-2 = indices 1, 3
    bridge = _FakeBridge({0: 0, 1: 1, 2: 0, 3: 1, 4: 0})
    context = SearchContext(
        bridge=bridge, game_attrs={}, rollout_specs=(None, None), base_seed=2
    )
    context.update_boundary("snap")
    policy = ForkSearchPolicy(
        player=0, context=context, deck0=A, deck1=B, base=base, prior=prior,
        rollouts=1, top_k=2,
    )
    policy.choose(_Built(_Ctx(codes)))
    decision = context.searches[0]
    assert decision.candidates == (0, 1, 3)  # top-2 prior + required base 0
    assert decision.prior is not None and set(decision.prior) == {0, 1, 3}
    assert decision.base_option == 0
    assert prior.calls == 1  # prior != base -> consulted once for ranking


def test_search_policy_prior_equals_base_uses_one_forward():
    codes = (10, 11, 12)

    class _PriorBase(_FakePrior):
        name = "prior_base"

        def choose(self, built):
            probs = self.distribution(built)
            return built.context.legal_low_level_codes[
                max(range(len(probs)), key=lambda i: probs[i])
            ]

    base = _PriorBase([0.2, 0.7, 0.1])
    bridge = _FakeBridge({0: 0, 1: 1, 2: 0})
    context = SearchContext(
        bridge=bridge, game_attrs={}, rollout_specs=(None, None), base_seed=3
    )
    context.update_boundary("snap")
    policy = ForkSearchPolicy(
        player=0, context=context, deck0=A, deck1=B, base=base, prior=base,
        rollouts=1, top_k=2,
    )
    assert policy.choose(_Built(_Ctx(codes))) in codes
    assert base.calls == 1  # prior IS base -> a single distribution forward


# --------------------------------------------------------------------------- #
# slow engine tests
# --------------------------------------------------------------------------- #
class _Recorder:
    def __init__(self, inner, player, records):
        self.inner = inner
        self.player = player
        self.records = records
        self.name = f"rec:{inner.name}"

    def choose(self, built):
        codes = [int(c) for c in built.context.legal_low_level_codes]
        code = int(self.inner.choose(built))
        self.records[self.player].append(codes.index(code) if code in codes else -1)
        return code


@pytest.fixture(scope="module")
def live_run():
    from reps.action_hierarchy import reset_default_hierarchical_action_codebook

    reset_default_hierarchical_action_codebook()
    game = Game(create_param=_build_create_param(deck_spec(A), deck_spec(B), seed=SEED))
    records: list[list[int]] = [[], []]
    p0 = build_policy(f"expert:{A}", seed=derive_seed(SEED, "p0", "search-base"), role="p0")
    p1 = build_policy(f"expert:{B}", seed=derive_seed(SEED, "p1", "search-base"), role="p1")
    game.set_player(0, PolicyPlayer(0, _Recorder(p0, 0, records)))
    game.set_player(1, PolicyPlayer(1, _Recorder(p1, 1, records)))
    boundaries: list[tuple[str, int, int]] = [(capture_snapshot(game), 0, 0)]
    game.start()
    while game.is_running():
        if game.is_resumable():
            boundaries.append((capture_snapshot(game), len(records[0]), len(records[1])))
        game.step()
    terminal = capture_snapshot(game)
    return {
        "winner": game.winner(),
        "terminal": terminal,
        "records": records,
        "boundaries": boundaries,
    }


def _mid_replay_task(live_run) -> ForkTask:
    snap, b0, b1 = live_run["boundaries"][len(live_run["boundaries"]) // 2]
    task = replay_fork_task(
        snap,
        index=0,
        deck0=A,
        deck1=B,
        prefix0=live_run["records"][0][b0:],
        prefix1=live_run["records"][1][b1:],
    )
    return ForkTask(**{**task.__dict__, "capture_terminal": True})


@pytest.mark.slow
def test_pure_replay_fork_reproduces_live_terminal(live_run):
    task = _mid_replay_task(live_run)
    result = run_fork_task(task)
    assert result["error"] is None
    assert result["winner"] == live_run["winner"]
    assert result["terminal"] == live_run["terminal"]


@pytest.mark.slow
def test_subprocess_bridge_replay_matches_live(live_run):
    task = _mid_replay_task(live_run)
    with ForkBridge(workers=1) as bridge:
        results = bridge.evaluate([task])
    assert len(results) == 1
    result = results[0]
    assert result.error is None
    assert result.winner == live_run["winner"]
    assert result.terminal == live_run["terminal"]


@pytest.mark.slow
def test_search_match_runs_clean():
    with ForkBridge(workers=2) as bridge:
        result = run_search_match(
            A,
            B,
            bridge=bridge,
            seed=SEED,
            rollouts=1,
            top_k=2,
            max_searches=2,
        )
    record = result.record
    assert record.error is None, record.error
    assert not record.truncated
    assert record.fallbacks0 == 0 and record.fallbacks1 == 0
    assert len(result.searches) == 2
    for decision in result.searches:
        assert decision.searched
        assert decision.chosen_option in decision.candidates
        assert decision.counts[decision.chosen_option] >= 1


@pytest.mark.slow
def test_search_match_is_deterministic():
    def run() -> tuple:
        with ForkBridge(workers=1) as bridge:
            result = run_search_match(
                A, B, bridge=bridge, seed=SEED, rollouts=1, top_k=2, max_searches=1
            )
        return (
            result.record.winner,
            result.record.rounds,
            [(s.chosen_option, s.base_option, tuple(sorted(s.means.items()))) for s in result.searches],
        )

    assert run() == run()


@pytest.mark.slow
def test_search_match_with_cvpn_prior(tmp_path):
    """CVPN 接入搜索: checkpoint as base+prior (candidate ranking) + neural rollout."""
    from envs.observation import default_encoder
    from train.model import CVPN, config_for_encoder
    from train.resolver import PriorResolver, neural_rollout_spec

    encoder = default_encoder()
    path = tmp_path / "ckpt.pt"
    CVPN(config_for_encoder(encoder)).save(str(path), encoder_config=encoder.to_dict())
    prior = PriorResolver(model=CVPN.load(str(path)), encoder=encoder, temperature=1.0)

    with ForkBridge(workers=2) as bridge:
        result = run_search_match(
            A,
            B,
            bridge=bridge,
            seed=SEED,
            rollouts=1,
            top_k=2,
            max_searches=2,
            base_policies=(prior, None),
            prior_policies=(prior, None),
            rollout_specs=(neural_rollout_spec(str(path)), "legal_random"),
        )
    record = result.record
    assert record.error is None, record.error
    assert record.fallbacks0 == 0 and record.fallbacks1 == 0
    assert result.searches
    for decision in result.searches:
        assert decision.base_option in decision.candidates  # base always a candidate
        assert decision.prior is not None
        assert set(decision.prior).issubset(set(decision.candidates))


def test_search_match_rejects_inprocess_bridge():
    with pytest.raises(ValueError, match="subprocess"):
        run_search_match(A, B, bridge=ForkBridge(workers=0), seed=SEED)
