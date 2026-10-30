"""Human policy: drive a match from the terminal (play against the AI).

A ``Policy`` that renders the current decision (a compact state line + the legal
options) and reads the chosen option index from stdin. It plugs into
``envs.match.run_match`` like any other policy, so a human can play either side
against a scripted / neural / search opponent (see ``scripts/play_vs_ai.py``).

Observation is the same masked ``player_view`` the encoder uses (no hidden
information), so a human sees exactly what the AI is allowed to see.

Quitting (``q`` / Ctrl-C) raises :class:`HumanQuit`, which the engine adapter
turns into a match-level "quit" (the game stops and is *not* scored); see the
class docstring for why ``KeyboardInterrupt`` cannot be used for this.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

_PHASE = {
    "init_hands": "起手换牌",
    "init_actives": "选择出战",
    "roll": "掷骰",
    "action": "行动",
    "end": "结束",
    "game_end": "对局结束",
}


def _option_label(spec: Any, index: int) -> str:
    label = getattr(spec, "label", None)
    if label:
        return str(label)
    return f"选项 {index}"


class HumanQuit(Exception):
    """人机对局里「人要求退出本局」的信号（不是错误，也不该记成胜负）。

    为什么不用 ``KeyboardInterrupt``：引擎回调里抛出的异常会被 cffi 吞掉，而
    ``PolicyPlayer._decide`` 只 ``except Exception``（拦不住 ``KeyboardInterrupt``），
    于是 ``run_match`` 会照常跑到终局、把这一局记成对方胜。``HumanQuit`` 是
    ``Exception``：``PolicyPlayer`` 显式捕获它 → 置 ``quit_requested`` →
    ``run_match`` 下一步跳出循环并把该局写进 ``MatchRecord.error``
    （评分口径按 ``error is None and not truncated`` 过滤，见 ``eval/scoreboard.py``）。
    """


@dataclass
class HumanPolicy:
    """Reads the human's move for ``player`` from stdin."""

    player: int = 1
    input_fn: Callable[[str], str] = input
    output_fn: Callable[[str], None] = print
    name: str = "human"
    _games: int = field(default=0, init=False, repr=False)
    #: 已经请求退出（输入 q / Ctrl-C）。置位后 ``choose`` 不再读输入。
    quit_requested: bool = field(default=False, init=False)

    def choose(self, built: Any) -> int:
        codes = [int(c) for c in built.context.legal_low_level_codes]
        if not codes:
            return -1
        if self.quit_requested:
            # 已经要退出了，而引擎在一个 step 里可能连着问好几次：不再要人输入，
            # 回一个合法码把这一步走完（`run_match` 之后立刻跳出循环）。
            return codes[0]
        specs = tuple(getattr(built.context, "legal_low_level_specs", ()) or ())
        self._render_state(built, specs, len(codes))
        prompt = f"  你的选择 [0..{len(codes) - 1}]（q 退出）: "
        while True:
            try:
                raw = str(self.input_fn(prompt)).strip()
            except (EOFError, KeyboardInterrupt) as exc:
                # Ctrl-C / 输入流结束 ≈ 输入 q：必须在这里转成 HumanQuit，
                # 否则它会被 cffi 吞掉（见 HumanQuit 的说明）。
                self.quit_requested = True
                raise HumanQuit(f"human quit ({type(exc).__name__})") from exc
            if raw in {"q", "quit", "exit"}:
                self.quit_requested = True
                raise HumanQuit("human quit")
            if raw.isdigit() and 0 <= int(raw) < len(codes):
                return codes[int(raw)]
            self.output_fn(f"  非法输入 {raw!r}，请输入 0..{len(codes) - 1}")

    # -- rendering ----------------------------------------------------------- #
    def _render_state(self, built: Any, specs: tuple, n_options: int) -> None:
        ctx = built.context
        view = getattr(ctx, "player_view", None)
        phase = _PHASE.get(str(getattr(view, "phase", "")), str(getattr(view, "phase", "?")))
        req = getattr(ctx.request_type, "value", str(ctx.request_type))
        lines = [f"\n— 第 {getattr(view, 'round_number', '?')} 回合 / {phase} / 请求: {req}"]
        if view is not None:
            me = view.players[self.player]
            active = next((c for c in me.characters if c.is_active), None)
            if active is not None:
                lines.append(
                    f"  出战: id{active.definition_id} HP {active.health}/{active.max_health} "
                    f"能量 {active.energy}/{active.max_energy}"
                )
            lines.append(f"  手牌 {len(me.hand_cards)} 张 · 骰子 {len(me.dice)} 个 · 牌堆 {len(me.pile_cards)}")
        for index in range(n_options):
            spec = specs[index] if index < len(specs) else None
            lines.append(f"  [{index}] {_option_label(spec, index)}")
        self.output_fn("\n".join(lines))
