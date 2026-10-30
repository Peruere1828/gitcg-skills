"""Human policy (terminal play): option parsing, quit handling, and the
quit-must-not-be-scored regression.

``q`` / Ctrl-C must (a) actually stop the match and (b) leave the game
unscored. It used to raise ``KeyboardInterrupt`` from inside an engine callback,
where cffi swallows the exception: ``run_match`` then played on to the end and
recorded the human's side as a loss.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from agents.human import HumanPolicy, HumanQuit


@dataclass
class _Ctx:
    legal_low_level_codes: tuple[int, ...]
    legal_low_level_specs: tuple = ()
    request_type: str = "action"
    player_view: object = None


@dataclass
class _Built:
    context: _Ctx


class _Inputs:
    def __init__(self, values):
        self.values = list(values)

    def __call__(self, _prompt):
        return self.values.pop(0)


def _policy(values):
    return HumanPolicy(player=1, input_fn=_Inputs(values), output_fn=lambda *_a: None)


def test_human_policy_returns_chosen_code():
    policy = _policy(["1"])
    built = _Built(_Ctx((10, 20, 30)))
    assert policy.choose(built) == 20


def test_human_policy_retries_on_bad_input():
    policy = _policy(["x", "9", "2"])
    built = _Built(_Ctx((10, 20, 30)))
    assert policy.choose(built) == 30


def test_human_policy_quit_raises_human_quit_and_marks():
    policy = _policy(["q"])
    with pytest.raises(HumanQuit):
        policy.choose(_Built(_Ctx((1, 2))))
    assert policy.quit_requested is True


def test_human_quit_is_not_keyboardinterrupt():
    """退出信号必须是 Exception：KeyboardInterrupt 会被 cffi 吞掉。"""
    assert issubclass(HumanQuit, Exception)
    assert not issubclass(HumanQuit, KeyboardInterrupt)


@pytest.mark.parametrize("exc", [KeyboardInterrupt, EOFError])
def test_human_policy_ctrl_c_becomes_quit(exc):
    def boom(_prompt):
        raise exc

    policy = HumanPolicy(player=1, input_fn=boom, output_fn=lambda *_a: None)
    with pytest.raises(HumanQuit):
        policy.choose(_Built(_Ctx((7, 8))))
    assert policy.quit_requested is True


def test_human_policy_after_quit_does_not_read_input():
    """同一 step 里引擎可能连着问几次：退出后不再读输入，回一个合法码。"""
    policy = _policy(["q"])
    with pytest.raises(HumanQuit):
        policy.choose(_Built(_Ctx((1, 2))))
    assert policy.choose(_Built(_Ctx((10, 20)))) == 10


def test_human_policy_empty_options():
    policy = _policy([])
    assert policy.choose(_Built(_Ctx(()))) == -1


def test_run_match_records_quit_as_invalid_game():
    """回归：run_match 里输入 q → 跳出对局、记 error、不算胜负。"""
    from envs.decks import deck_spec
    from envs.match import run_match
    from envs.policy import legal_random_policy

    human = HumanPolicy(player=1, input_fn=lambda _p: "q", output_fn=lambda *_a: None)
    record = run_match(
        deck_spec("superconduct_aggro"),
        deck_spec("natlan_battleship"),
        legal_random_policy(seed=1),
        human,
        seed=3,
    )
    assert record.error == "human quit: P1"
    assert record.winner is None, "中途退出不能被记成对方胜"
    assert record.truncated is False
