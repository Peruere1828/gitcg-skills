"""Parked-game scheduler: decouple simulation from model latency.

The problem: ``game.step()`` invokes the policy *synchronously* (a cffi callback), so a
process that asks the model blocks for the whole round-trip. One process per game
therefore caps concurrency at what the RAM can hold (~200 MB each).

The fix (validated by ``scripts/probe_resume.py``: 30/30 parked+resumed games ended in
a byte-identical terminal state): at a parkable decision we **snapshot the engine
state and abandon the live game**, hand the prompt to a thread pool, and resume from
the snapshot when the answer arrives. A parked game costs ~10 KB, so one process can
keep hundreds in flight while the engine stays single-threaded.

Rules from the engine investigation that this module respects:

* only ``action`` requests are parked -- the probe showed ``switch_hands`` does *not*
  resume identically (``choose_active`` happened to in the sampled game, but it is
  excluded conservatively; ``reroll_dice`` / ``select_card`` were never validated);
* state attributes (seed, dice/hand limits) travel inside ``GameState``, so a resume
  only needs the players re-attached;
* each game carries its own action codebook. ``run_match`` resets the process-global
  codebook per game, but parked games are interleaved, and expert tie-breaks use
  ``-action_code``: without isolation, the same seed could play differently depending
  on which game ran first.

Bookkeeping that keeps rows identical to ``skillloop.run``:

* the parked decision and the events of its fallback step are recorded by
  ``PolicyPlayer`` on a game that is thrown away, so the driver truncates both lists
  back to the marks taken *at the decision*, not at the start of the engine step.
  Truncating to the step start would also drop notifications that the engine emitted
  earlier in the same step (a skill event followed by the next action request) -- the
  snapshot already includes their effects, so they would never be replayed;
* the resumed segment re-issues the same decision, this time answered with the model's
  choice, and that record is the one that survives. The answer is stored as a
  low-level *code*, not as the engine's candidate index: the engine's action order is
  not guaranteed to be identical after a resume, while codes stay stable within the
  game;
* the system prompt and its retrieved skills are built once per **game** and carried
  across segments (``ParkedGame.system`` / ``.skills``), so the cached prefix is
  byte-identical for every call of that game, exactly like ``SkillAgentPolicy``;
* the per-game call budget is carried across segments too: ``ParkablePolicy.calls``
  starts from ``ParkedGame.calls`` instead of 0;
* model calls inherit ``AgentConfig``'s D10 in-game settings and pass
  ``reasoning_effort`` explicitly, so ``GITCG_LLM_REASONING`` cannot re-enable paid
  reasoning in the parked path;
* ``GITCG_LLM_BUDGET_USD`` is read once per ``run`` invocation: once ``spent``
  reaches it no new game is launched, in-flight games finish, and ``last_summary``
  carries ``aborted`` / ``budget_usd`` / ``spent_usd`` like ``skillloop.run``.

INNOVATION: latency decoupling without changing the decision stream -- one engine
thread, many in-flight prompts, and rows the existing paired gate can consume.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, Callable

from gitcg import Game, State

from envs.decks import deck_spec
from envs.match import MatchEvent, PolicyPlayer, _build_create_param
from envs.policy import expert_policy
from npc.objectives import evaluate_stage
from npc.policy import npc_policy_for_stage
from npc.spec import NpcStage
from skillloop.agent import AgentConfig, parse_choice
from skillloop.library import SkillLibrary
from skillloop.render import StateRenderer, select_candidates
from skillloop.run import (
    RunSpec,
    _assets,
    budget_exhausted,
    budget_from_env,
    summarize,
    task_spec,
)

PARKABLE_REQUESTS = ("action",)
MAX_STEPS = 5000          # mirrors ``envs.match.run_match``'s default guard
_CLIENT_UNSET = object()


@dataclass
class PendingDecision:
    """One decision waiting for an answer, plus everything needed to replay it."""

    key: tuple[str, tuple[int, ...]]
    prompt: list[dict[str, str]]
    codes: list[int]
    step: int
    request: str
    snapshot: str
    fallback_code: int
    # Copied from ``AgentConfig`` by ``choose`` so the call site never restates
    # D10's cheap in-game settings (``reasoning_effort="none"``, ``max_tokens=64``).
    max_tokens: int
    temperature: float
    model: str | None
    reasoning_effort: str | None
    retrieved: tuple[str, ...] = ()
    system_hash: str = ""
    # List lengths *at the decision point*: truncating to these drops the fallback
    # choice and anything it caused, while keeping notifications/decisions that the
    # engine had already made before asking us.
    decision_mark: int = 0
    event_mark: int = 0


class ParkablePolicy:
    """Agent policy that parks at the first parkable decision of each segment."""

    def __init__(self, base: Any, *, renderer: StateRenderer, task, library,
                 config: AgentConfig, calls: int = 0,
                 book: "ParkedGame | None" = None) -> None:
        self.base = base
        self.name = f"park({getattr(base, 'name', 'base')})"
        self.renderer = renderer
        self.task = task
        self.library = library
        self.config = config
        self.book = book                     # the ParkedGame lists (marks + truncation)
        self.snapshot_fn: Callable[[], str] | None = None
        self.pending: PendingDecision | None = None
        self.override: dict[tuple[str, tuple[int, ...]], int] = {}
        self.calls = int(calls)              # per-GAME budget, carried across resumes
        self.system: dict[str, str] | None = None
        self.system_hash = ""
        self.skills: list[Any] = []

    def _system(self, context) -> dict[str, str]:
        if self.system is None:
            if self.library is not None and self.config.top_k > 0:
                ctx = {
                    "stage": self.task.stage,
                    "objective_types": tuple(self.task.objective_types),
                    "round": int(context.player_view.round_number) if context.player_view else None,
                }
                self.skills = self.library.retrieve(self.task.describe(), ctx, k=self.config.top_k)
            rules = self.library.rules_text() if self.library is not None else ""
            self.system = self.renderer.system_message(task=self.task, skills=self.skills, rules_note=rules)
            self.system_hash = hashlib.sha1(self.system["content"].encode("utf-8")).hexdigest()[:12]
        return self.system

    def take_pending(self) -> PendingDecision | None:
        pending, self.pending = self.pending, None
        return pending

    def choose(self, built) -> int:
        context = built.context
        candidates = select_candidates(context, max_candidates=self.config.candidate_cap)
        codes = tuple(int(code) for code, _spec in candidates)
        key = (context.request_type.value, codes)

        if key in self.override:               # resume: answer the parked decision
            return self.override.pop(key)

        base_code = int(self.base.choose(built))
        parkable = (
            self.pending is None               # at most one park per segment
            and context.request_type.value in PARKABLE_REQUESTS
            and context.request_type.value in self.config.request_types
            and self.calls < self.config.max_calls_per_game
            and len(candidates) >= 2
        )
        if not parkable:
            return base_code

        assert self.snapshot_fn is not None, "snapshot_fn not wired"
        self.calls += 1
        self.pending = PendingDecision(
            key=key,
            prompt=[self._system(context), self.renderer.user_message(context, candidates)],
            codes=list(codes),
            step=int(context.step_index),
            request=context.request_type.value,
            snapshot=self.snapshot_fn(),
            fallback_code=base_code,
            retrieved=tuple(skill.skill_id for skill in self.skills),
            system_hash=self.system_hash,
            decision_mark=len(self.book.decisions) if self.book is not None else 0,
            event_mark=len(self.book.events) if self.book is not None else 0,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            model=self.config.model,
            reasoning_effort=self.config.reasoning_effort,
        )
        return base_code  # the engine proceeds; this game object is discarded


@dataclass
class ParkedGame:
    stage: NpcStage
    seed: int
    spec: RunSpec
    task_index: int
    snapshot: str | None = None
    pending: PendingDecision | None = None
    decisions: list[Any] = field(default_factory=list)
    events: list[MatchEvent] = field(default_factory=list)
    calls: int = 0
    errors: int = 0
    used: set[str] = field(default_factory=set)
    winner: int | None = None
    rounds: int = 0
    final_state: str | None = None
    error: str | None = None
    truncated: bool = False
    started: float = field(default_factory=time.perf_counter)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost: float = 0.0
    llm_seconds: float = 0.0
    # The prompt prefix is built once per game (like ``SkillAgentPolicy``) and must
    # survive the resume, otherwise the provider cache key changes mid-game.
    system: dict[str, str] | None = None
    skills: list[Any] = field(default_factory=list)
    # Per-game action codebook. ``reps.action_adapter`` keeps a process-global codebook
    # that ``run_match`` resets per game; parked games are interleaved, so they each
    # carry their own and ``advance`` installs it around the engine call. Without
    # this, codes (and expert tie-breaks, which use ``-action_code``) depend on which
    # game happened to run first, breaking same-seed reproducibility.
    codebook: Any = None

    @property
    def key(self) -> str:
        return f"{self.stage.name}:{self.seed}"


class ParkedRunner:
    """Runs a stage×seed grid with at most ``concurrency`` model calls in flight."""

    def __init__(self, spec: RunSpec, *, concurrency: int = 96,
                 client: Any = _CLIENT_UNSET) -> None:
        self.spec = spec
        self.concurrency = max(1, concurrency)
        self.client = None if client is _CLIENT_UNSET else client
        # The library is immutable during a run: load it once, not per segment and
        # definitely not per row (the row used to re-read it from disk).
        self.library: SkillLibrary | None = None
        if spec.group in ("static", "dynamic"):
            if not spec.library_root:
                raise ValueError(f"group {spec.group!r} needs a library_root")
            self.library = SkillLibrary.load(spec.library_root)
        self.library_version = self.library.version if self.library is not None else 0
        self.config = AgentConfig(
            model=spec.model,
            max_calls_per_game=spec.max_calls,
            top_k=spec.top_k,
            # declare what the scheduler can actually park: anything else is answered
            # by the expert, so advertising it would only desynchronise the budget
            # from the synchronous agent.
            request_types=PARKABLE_REQUESTS,
        )
        self._meter: _UsageMeter | None = None
        self._client_ready = client is not _CLIENT_UNSET
        # Populated by ``run``: ``skillloop.run``-shaped summary, including the
        # per-invocation budget fields (``budget_usd`` / ``spent_usd`` / ``aborted``).
        self.last_summary: dict[str, Any] | None = None

    # -- engine side -------------------------------------------------------
    def _ensure_client(self) -> Any:
        if not self._client_ready:
            from common.llm import build_llm_client

            self.client = build_llm_client()
            self._client_ready = True
        if self.client is not None and self._meter is None:
            self._meter = _UsageMeter(self.client)
        return self.client

    def _policy(self, task: ParkedGame) -> ParkablePolicy:
        renderer = StateRenderer(_assets(), opponent_name=f"NPC({task.stage.display})")
        policy = ParkablePolicy(
            expert_policy(self.spec.deck, seed=0),
            renderer=renderer,
            task=task_spec(task.stage, self.spec.deck),
            library=self.library,
            config=self.config,
            calls=task.calls,                 # carry the per-game budget across resumes
            book=task,
        )
        policy.system = task.system           # carry the cached prefix across resumes
        policy.skills = list(task.skills)
        return policy

    def _make_game(self, task: ParkedGame, policy: ParkablePolicy) -> Game:
        contestant = deck_spec(self.spec.deck)
        npc_deck = type(contestant)(name=f"npc_{task.stage.name}",
                                    characters=task.stage.deck_characters, cards=contestant.cards)
        if task.snapshot is None:
            game = Game(create_param=_build_create_param(contestant, npc_deck, seed=task.seed))
        else:
            game = Game(state=State(json=task.snapshot))
        game.set_player(0, PolicyPlayer(0, policy, recorder=task.decisions, event_sink=task.events))
        game.set_player(1, PolicyPlayer(1, npc_policy_for_stage(task.stage, rotation=self.spec.rotation)))
        policy.snapshot_fn = lambda: game.state().json()
        return game

    def advance(self, task: ParkedGame, override: PendingDecision | None, code: int | None) -> str:
        """Run to the next park or the end: ``parked`` | ``finished`` | ``truncated`` | ``error``."""
        from reps import action_hierarchy

        policy = self._policy(task)
        if override is not None and code is not None:
            policy.override[override.key] = int(code)
        # Install this game's codebook for the duration of the engine call. The lock
        # is held across ``game.step`` (RLock, re-entered by the encoder), so two
        # runners in one process cannot interleave their codebooks; model calls in
        # the pool never touch it.
        with action_hierarchy._DEFAULT_CODEBOOK_LOCK:
            previous_codebook = action_hierarchy._DEFAULT_CODEBOOK
            if task.codebook is None:
                task.codebook = action_hierarchy.build_default_hierarchical_action_codebook()
            action_hierarchy._DEFAULT_CODEBOOK = task.codebook
            try:
                game = self._make_game(task, policy)
                game.start()
                steps = 0
                while game.is_running():
                    if steps >= MAX_STEPS:
                        task.truncated = True
                        self._capture_terminal(task, game)
                        return "truncated"
                    game.step()
                    steps += 1
                    pending = policy.take_pending()
                    if pending is not None:
                        # Keep what happened *before* the decision (it is already in
                        # the snapshot, so the resume will not replay it) and drop the
                        # fallback choice and everything it caused.
                        del task.decisions[pending.decision_mark:]
                        del task.events[pending.event_mark:]
                        task.snapshot = pending.snapshot
                        task.pending = pending
                        return "parked"
                task.winner = game.winner()
                task.rounds = game.round_number()
                task.final_state = game.state().json()
                return "finished"
            except Exception as exc:  # engine/IO failures must not kill the scheduler
                task.error = f"{type(exc).__name__}: {exc}"
                return "error"
            finally:
                task.codebook = action_hierarchy._DEFAULT_CODEBOOK
                action_hierarchy._DEFAULT_CODEBOOK = previous_codebook
                task.system = policy.system
                task.skills = list(policy.skills)

    @staticmethod
    def _capture_terminal(task: ParkedGame, game: Game) -> None:
        """Mirror ``run_match``: a truncated game still gets its partial final state."""
        try:
            task.winner = game.winner()
            task.rounds = game.round_number()
            task.final_state = game.state().json()
        except Exception:
            pass

    # -- scheduling --------------------------------------------------------
    def run(self, tasks: list[ParkedGame], *, progress: bool = True) -> list[dict[str, Any]]:
        """Play ``tasks`` with a per-invocation spend guard; return completed rows.

        The budget is read here (not at construction) so ``GITCG_LLM_BUDGET_USD`` is
        per invocation, exactly like ``skillloop.run``: only rows finished by *this*
        call count, never history from an earlier run. Once the cap is crossed no new
        game is launched; games already in flight run to completion (so ``spent`` may
        overshoot by at most ``concurrency`` games' cost, same as ``run.py``'s worker
        batch). The summary lives on ``last_summary``.
        """
        started = time.perf_counter()
        budget = budget_from_env()
        spent = 0.0
        aborted: str | None = None

        if self._ensure_client() is None:
            for task in tasks:
                task.error = "no_llm_config"
            rows = [self._row(task) for task in tasks]
            self.last_summary = self._summary(rows, budget, None, started, spent)
            return rows

        rows: list[dict[str, Any]] = []
        ready: deque[ParkedGame] = deque(tasks)
        inflight: dict[Any, tuple[ParkedGame, PendingDecision]] = {}

        def submit(pool: ThreadPoolExecutor, task: ParkedGame) -> None:
            pending = task.pending
            assert pending is not None
            task.calls += 1
            inflight[pool.submit(self._call, pending)] = (task, pending)

        def finish(task: ParkedGame) -> None:
            nonlocal spent
            rows.append(self._row(task))
            spent += float(rows[-1].get("cost_usd") or 0.0)
            if not progress:
                return
            row = rows[-1]
            flag = "3★" if row["three_star"] else f"{row['stars']}★"
            print(f"  {task.stage.name:16s} seed={task.seed:<4d} {flag:>2s} "
                  f"calls={task.calls:<3d} {row['seconds']:>6.1f}s "
                  f"{row['error'] or ''}", flush=True)

        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            while ready or inflight:
                while ready and len(inflight) < self.concurrency:
                    # Only a *new* game is gated; a resumed game is already in the
                    # batch and must finish (the contract is "in-flight games run").
                    if budget_exhausted(spent, budget):
                        aborted = "budget"
                        ready.clear()
                        break
                    task = ready.popleft()
                    if self.advance(task, None, None) != "parked":
                        finish(task)
                    else:
                        submit(pool, task)

                if not inflight:
                    continue
                done, _ = wait(list(inflight), return_when=FIRST_COMPLETED, timeout=1.0)
                for future in done:
                    task, pending = inflight.pop(future)
                    try:
                        raw, usage = future.result()
                    except Exception:       # a broken client must degrade, not crash
                        raw, usage = None, {"seconds": 0.0}
                    task.prompt_tokens += int(usage.get("prompt_tokens", 0))
                    task.completion_tokens += int(usage.get("completion_tokens", 0))
                    task.cached_tokens += int(usage.get("cached_tokens", 0))
                    task.cost += float(usage.get("cost_usd", 0.0))
                    task.llm_seconds += float(usage.get("seconds", 0.0))
                    index, used, error = parse_choice(raw, len(pending.codes))
                    task.used.update(sid for sid in used if sid in pending.retrieved)
                    if index is None:
                        task.errors += 1
                        code = pending.fallback_code      # model failed -> expert
                    else:
                        code = pending.codes[index]
                    if self.advance(task, pending, code) != "parked":
                        finish(task)
                    else:
                        # The resumed segment parked at its *next* decision: that
                        # decision still needs an answer, so submit it now. Sending
                        # the task back to ``ready`` would re-run (and re-park) the
                        # same decision without its answer, wasting a call.
                        submit(pool, task)

        if aborted and budget is not None and progress:
            print(f"[parked] budget ${budget:.4f} reached (${round(spent, 6):.4f}); "
                  f"stopped launching new games", flush=True)
        self.last_summary = self._summary(rows, budget, aborted, started, spent)
        return rows

    def run_with_summary(self, tasks: list[ParkedGame], *,
                         progress: bool = True) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """``run`` plus the ``skillloop.run``-shaped summary in one call."""
        rows = self.run(tasks, progress=progress)
        assert self.last_summary is not None
        return rows, self.last_summary

    def _summary(self, rows: list[dict[str, Any]], budget: float | None,
                 aborted: str | None, started: float, spent: float) -> dict[str, Any]:
        """Same shape/fields as ``skillloop.run.run``'s summary."""
        summary = summarize(rows)
        summary["seconds"] = round(time.perf_counter() - started, 1)
        summary["out"] = str(self.spec.out)
        if budget is not None:
            summary["budget_usd"] = budget
            summary["spent_usd"] = round(spent, 6)
        if aborted:
            summary["aborted"] = aborted
        return summary

    # -- model call --------------------------------------------------------
    def _call(self, pending: PendingDecision) -> tuple[str | None, dict[str, Any]]:
        """One blocking model call in a worker thread; returns text + usage delta."""
        meter = self._meter
        if meter is not None:
            meter.begin()
        started = time.perf_counter()
        raw: str | None = None
        try:
            raw = self.client.chat(
                pending.prompt,
                max_tokens=pending.max_tokens,
                temperature=pending.temperature,
                response_format_json=True,
                model=pending.model,
                # Explicit: ``LLMClient.chat`` falls back to ``GITCG_LLM_REASONING``
                # when this is None, which would silently re-enable paid reasoning
                # in the highest-concurrency path (PLAN.md D10).
                reasoning_effort=pending.reasoning_effort,
            )
        except Exception:
            raw = None
        usage = meter.take() if meter is not None else {}
        usage["seconds"] = time.perf_counter() - started
        return raw, usage

    # -- row ---------------------------------------------------------------
    def _row(self, task: ParkedGame) -> dict[str, Any]:
        stars = None
        if task.final_state is not None:
            record = _MiniRecord(winner=task.winner, rounds=task.rounds,
                                 decisions=task.decisions, events=task.events,
                                 final_state_json=task.final_state)
            stars = evaluate_stage(task.stage, record)
        return {
            "task_index": task.task_index,
            "stage": task.stage.name,
            "seed": task.seed,
            "deck": self.spec.deck,
            "group": self.spec.group,
            "model": self.spec.model,
            "library_version": self.library_version,
            "winner": task.winner,
            "rounds": task.rounds,
            "three_star": bool(stars.three_star) if stars else False,
            "stars": int(stars.stars) if stars else 0,
            "n_objectives": len(stars.objectives) if stars else 0,
            "objectives": {item.type: bool(item.achieved) for item in stars.objectives} if stars else {},
            "error": task.error or ("evaluation_failed" if stars is None else None),
            "truncated": bool(task.truncated),
            "seconds": round(time.perf_counter() - task.started, 2),
            "llm_calls": task.calls,
            "llm_errors": task.errors,
            "llm_seconds": round(task.llm_seconds, 2),
            "cited_skills": sorted(task.used),
            "prompt_tokens": task.prompt_tokens,
            "completion_tokens": task.completion_tokens,
            "cached_tokens": task.cached_tokens,
            "cost_usd": round(task.cost, 6),
        }


class _ThreadUsage:
    """``usage`` proxy that accumulates each ``record()`` into a thread-local slot.

    ``LLMClient.chat`` calls ``self.usage.record(...)`` from the calling thread, so one
    shared client can be used from the whole pool while every worker still reads an
    exact per-call delta (before/after reads on the shared counters would overlap).
    Attribute reads/writes (``errors += 1``) are forwarded to the real object.
    """

    def __init__(self, inner: Any, local: threading.local) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_local", local)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._inner, name, value)

    def record(self, *, prompt: int, completion: int, seconds: float,
               cached: int = 0, cost: float = 0.0) -> None:
        slot = getattr(self._local, "slot", None)
        if slot is not None:
            slot["prompt_tokens"] += int(prompt)
            slot["completion_tokens"] += int(completion)
            slot["cached_tokens"] += int(cached)
            slot["cost_usd"] += float(cost)
        self._inner.record(prompt=prompt, completion=completion, seconds=seconds,
                           cached=cached, cost=cost)


class _UsageMeter:
    """Per-thread usage deltas for one client shared by the scheduler's thread pool."""

    _FIELDS = ("prompt_tokens", "completion_tokens", "cached_tokens", "cost_usd")

    def __init__(self, client: Any) -> None:
        self.client = client
        self.usage = getattr(client, "usage", None)
        self._local = threading.local()
        if self.usage is not None and hasattr(self.usage, "record"):
            try:
                client.usage = _ThreadUsage(self.usage, self._local)
            except Exception:       # read-only usage attribute: deltas stay zero
                self.usage = None

    def begin(self) -> None:
        self._local.slot = {name: 0 for name in self._FIELDS}

    def take(self) -> dict[str, Any]:
        return dict(getattr(self._local, "slot", {}))


class _MiniRecord:
    """The subset of ``MatchRecord`` that ``npc.objectives`` reads."""

    def __init__(self, *, winner, rounds, decisions, events, final_state_json) -> None:
        self.winner = winner
        self.rounds = rounds
        self.decisions = decisions
        self.events = events
        self.final_state_json = final_state_json
