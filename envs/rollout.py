"""Multiprocess rollout: run independent matches across worker subprocesses.

Each worker is a fresh interpreter (``python -m envs.rollout_worker``) speaking a
JSON-lines protocol over pipes. This avoids ``multiprocessing`` fork, which
deadlocks the engine's embedded JS runtime, and spawn's ``__main__`` re-import,
which breaks under pytest / ``-m``. Tasks and results stay plain dicts.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from common.lock import runtime_metadata


@dataclass(frozen=True)
class MatchTask:
    """A single match request, addressable by (deck0, deck1, seed).

    ``deck0_inline``/``deck1_inline`` allow evaluating *mutated* decks that have no
    registry entry (deck-building sensitivity); they are plain
    ``{"name", "characters", "cards"}`` dicts so they cross the worker boundary.
    """

    index: int
    deck0: str
    deck1: str
    seed: int
    policy0: str = "expert"
    policy1: str = "expert"
    record_decisions: bool = False
    record_views: bool = False
    tag: str = ""
    deck0_inline: dict[str, Any] | None = None
    deck1_inline: dict[str, Any] | None = None

    def policy_spec(self, role: int) -> str:
        """Resolve ``"expert"`` to ``expert:<same slug as the deck>``."""
        spec = self.policy0 if role == 0 else self.policy1
        deck = self.deck0 if role == 0 else self.deck1
        if spec == "expert":
            return f"expert:{deck}"
        return spec

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "deck0": self.deck0,
            "deck1": self.deck1,
            "seed": self.seed,
            "policy0": self.policy_spec(0),
            "policy1": self.policy_spec(1),
            "record_decisions": self.record_decisions,
            "record_views": self.record_views,
            "tag": self.tag,
            "deck0_inline": self.deck0_inline,
            "deck1_inline": self.deck1_inline,
        }


def _resolve_deck(name: str, inline: dict[str, Any] | None):
    from reps.schema import DeckSpec

    if inline is not None:
        return DeckSpec(
            name=str(inline["name"]),
            characters=tuple(int(c) for c in inline["characters"]),
            cards=tuple(int(c) for c in inline["cards"]),
        )
    from envs.decks import deck_spec

    return deck_spec(name)


def run_task(task: MatchTask) -> dict[str, Any]:
    """Execute one task in the current process (top-level: picklable for pools)."""
    from envs.match import run_match
    from envs.policy import build_policy

    record = run_match(
        _resolve_deck(task.deck0, task.deck0_inline),
        _resolve_deck(task.deck1, task.deck1_inline),
        build_policy(task.policy_spec(0), seed=task.seed, role="p0"),
        build_policy(task.policy_spec(1), seed=task.seed, role="p1"),
        seed=task.seed,
        record_decisions=task.record_decisions,
        record_views=task.record_views,
    )
    payload = record.as_dict(include_decisions=task.record_decisions)
    payload["task_index"] = task.index
    payload["policy0"] = task.policy_spec(0)
    payload["policy1"] = task.policy_spec(1)
    payload["tag"] = task.tag
    return payload


def _mp_context():  # noqa: D401 - kept for reference/back-compat
    """Deprecated: multiprocessing fork deadlocks the gitcg C/JS runtime.

    Kept only so external callers do not break. Use ``run_tasks``, which spawns
    independent worker subprocesses instead.
    """
    return mp.get_context("spawn")


def _run_tasks_sequential(tasks: list[MatchTask]) -> list[dict[str, Any]]:
    return [run_task(task) for task in tasks]


_TIMEOUT_BASE_SECONDS = 600.0
_TIMEOUT_PER_TASK_SECONDS = 30.0
_MAX_DRAIN_ERRORS = 8


def _shard_timeout(shard_len: int) -> float:
    """Generous hang guard for one worker shard.

    The baseline is ~4.4 s/game single-core (``reports/LOCAL_BASELINE.md``), so
    ``30 s/task + 10 min`` gives ~7x headroom for the slowest worker: this must
    only ever trip on a *permanent* stall, never on a merely slow run. Override
    with ``GITCG_ROLLOUT_TIMEOUT`` (seconds) for unusual shards.
    """
    raw = os.environ.get("GITCG_ROLLOUT_TIMEOUT")
    if raw:
        try:
            override = float(raw)
        except ValueError:
            override = 0.0
        if override > 0:
            return override
    return _TIMEOUT_BASE_SECONDS + _TIMEOUT_PER_TASK_SECONDS * max(0, int(shard_len))


def _drive_worker(
    shard: list[Any],
    *,
    python: str,
    env: dict[str, str],
    repo_root: str,
    worker_module: str = "envs.rollout_worker",
    on_result: Callable[[dict[str, Any]], None] | None = None,
    retain_results: bool = True,
    timeout: float | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Run one shard through a fresh worker subprocess.

    Returns ``(results, received)``. ``received`` is always the number of result
    lines the worker emitted (used to detect a truncated/failed shard). When
    ``retain_results`` is false the parent keeps *no* results: each line is handed
    to ``on_result`` and immediately dropped, so a 500-game collector shard does
    not pin multiple GB in the parent (the A5 scaling blocker). ``results`` is
    empty in that mode.

    A reader thread that dies (a non-JSON line on stdout, or ``on_result``
    raising -- e.g. the collector's per-result disk append hitting ENOSPC) would
    otherwise leave the worker blocked on a full stdout pipe while the parent
    waits forever in ``process.wait()``. So every drain failure is recorded,
    the child is killed, and the parent raises promptly with a diagnosable
    message. ``timeout`` (default :func:`_shard_timeout`) is a last-resort hang
    guard around the joins/``wait``; on expiry the child is killed too.
    """
    import subprocess
    import threading

    process = subprocess.Popen(
        [python, "-m", worker_module],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=repo_root,
        env=env,
    )

    stderr_chunks: list[str] = []
    results: list[dict[str, Any]] = []
    received = 0
    drain_errors: list[str] = []
    killed = threading.Event()

    def _record_error(message: str) -> None:
        if len(drain_errors) < _MAX_DRAIN_ERRORS:
            drain_errors.append(message)

    def _kill_process() -> None:
        # SIGKILL closes the worker's pipe write ends, so the drain loops see
        # EOF and the parent never blocks on a full pipe. Idempotent.
        if killed.is_set():
            return
        killed.set()
        try:
            process.kill()
        except Exception:  # noqa: BLE001 - already dead / not started
            pass

    def _drain_stderr() -> None:
        assert process.stderr is not None
        try:
            for line in process.stderr:
                stderr_chunks.append(line)
        except Exception as exc:  # noqa: BLE001 - never let a reader die silently
            _record_error(f"stderr reader crashed: {type(exc).__name__}: {exc}")
            _kill_process()

    def _drain_stdout() -> None:
        nonlocal received
        assert process.stdout is not None
        try:
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                if drain_errors:
                    # Already failing: keep draining so the worker is never
                    # blocked on the pipe, but drop the work.
                    continue
                try:
                    match = json.loads(line)
                except Exception as exc:  # noqa: BLE001 - engine noise on stdout
                    _record_error(
                        f"non-JSON line from worker ({type(exc).__name__}: {exc}): {line[:200]!r}"
                    )
                    _kill_process()
                    continue
                received += 1
                if retain_results:
                    results.append(match)
                if on_result is not None:
                    try:
                        on_result(match)
                    except Exception as exc:  # noqa: BLE001 - e.g. ENOSPC on append
                        _record_error(
                            f"on_result failed ({type(exc).__name__}: {exc}) on result "
                            f"index={match.get('index')!r}"
                        )
                        _kill_process()
        except Exception as exc:  # noqa: BLE001 - never let a reader die silently
            _record_error(f"stdout reader crashed: {type(exc).__name__}: {exc}")
            _kill_process()

    # Both pipes must drain *while* tasks are being written: a large shard can
    # fill the worker's stdout pipe (64 KiB) before stdin is fully written, which
    # deadlocked the old write-all-then-read-all version (worker blocks on
    # stdout, parent blocks on stdin).
    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stdout_thread = threading.Thread(target=_drain_stdout, daemon=True)
    stderr_thread.start()
    stdout_thread.start()

    assert process.stdin is not None and process.stdout is not None
    stdin_error: Exception | None = None
    try:
        for task in shard:
            process.stdin.write(json.dumps(task.as_dict(), ensure_ascii=False) + "\n")
        process.stdin.close()
    except (BrokenPipeError, OSError) as exc:
        # Worker died before consuming stdin: surface its stderr below instead
        # of leaking a bare BrokenPipeError.
        stdin_error = exc
        try:
            process.stdin.close()
        except Exception:  # noqa: BLE001
            pass

    limit = _shard_timeout(len(shard)) if timeout is None else float(timeout)
    stdout_thread.join(timeout=limit)
    if stdout_thread.is_alive():
        _kill_process()
        stdout_thread.join(timeout=5.0)
        raise RuntimeError(
            f"rollout worker hung: stdout not closed after {limit:.0f}s "
            f"({received}/{len(shard)} results); killed pid={process.pid}"
        )
    try:
        returncode = process.wait(timeout=limit)
    except subprocess.TimeoutExpired:
        _kill_process()
        process.wait(timeout=5.0)
        raise RuntimeError(
            f"rollout worker hung: process did not exit within {limit:.0f}s "
            f"({received}/{len(shard)} results); killed pid={process.pid}"
        ) from None
    stderr_thread.join(timeout=1)

    detail = "".join(stderr_chunks)[-2000:]
    if drain_errors:
        joined = "\n".join(drain_errors)
        raise RuntimeError(
            f"rollout worker stream failed (rc={returncode}, {received}/{len(shard)} "
            f"results):\n{joined}\n{detail}"
        )
    if stdin_error is not None:
        raise RuntimeError(
            f"rollout worker stdin failed (rc={returncode}, {received}/{len(shard)} "
            f"results): {type(stdin_error).__name__}: {stdin_error}\n{detail}"
        )
    if returncode != 0 or received != len(shard):
        raise RuntimeError(
            f"rollout worker failed (rc={returncode}, {received}/{len(shard)} results):\n{detail}"
        )
    return results, received


def _run_shard_in_subprocess(
    shard: list[MatchTask],
    *,
    python: str,
    env: dict[str, str],
    repo_root: str,
    worker_module: str = "envs.rollout_worker",
    on_result: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Back-compat wrapper over :func:`_drive_worker` that retains all results."""
    results, _received = _drive_worker(
        shard,
        python=python,
        env=env,
        repo_root=repo_root,
        worker_module=worker_module,
        on_result=on_result,
        retain_results=True,
    )
    return results


def _resolve_shard_workers(shard: list[Any], workers: int | None) -> int:
    import os as _os

    resolved = workers if workers is not None else (_os.cpu_count() or 1)
    return max(1, min(int(resolved), len(shard)))


def _shard_exec_env() -> tuple[str, dict[str, str], str]:
    import os as _os
    import sys

    from common.paths import repo_root

    root = str(repo_root())
    env = dict(_os.environ)
    env["PYTHONPATH"] = root + _os.pathsep + env.get("PYTHONPATH", "")
    return sys.executable, env, root


def run_task_shard(
    shard: list[Any],
    *,
    worker_module: str,
    workers: int | None = None,
    on_result: Callable[[dict[str, Any]], None] | None = None,
    retain_results: bool = True,
) -> list[dict[str, Any]]:
    """Run a list of ``as_dict()``-able tasks through a JSON-lines worker module.

    A thin public wrapper over the (deadlock-safe) subprocess plumbing used by
    rollout, so other task types reuse it instead of re-implementing the two-pipe
    drain. Results are merged and sorted by ``index``.

    Set ``retain_results=False`` to stream every result to ``on_result`` without
    accumulating it in the parent (see :func:`stream_task_shard`); the returned
    list is then empty. The default keeps the historical retain-all behaviour.
    """
    shard = list(shard)
    if not shard:
        return []
    resolved = _resolve_shard_workers(shard, workers)
    python, env, root = _shard_exec_env()

    if resolved == 1:
        results, _received = _drive_worker(
            shard,
            python=python,
            env=env,
            repo_root=root,
            worker_module=worker_module,
            on_result=on_result,
            retain_results=retain_results,
        )
        return results

    from concurrent.futures import ThreadPoolExecutor

    chunks = [shard[i::resolved] for i in range(resolved)]
    chunks = [chunk for chunk in chunks if chunk]
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(chunks)) as pool:
        futures = [
            pool.submit(
                _drive_worker,
                chunk,
                python=python,
                env=env,
                repo_root=root,
                worker_module=worker_module,
                on_result=on_result,
                retain_results=retain_results,
            )
            for chunk in chunks
        ]
        for future in futures:
            part, _received = future.result()
            results.extend(part)
    results.sort(key=lambda item: item.get("index", 0))
    return results


def stream_task_shard(
    shard: list[Any],
    *,
    worker_module: str,
    workers: int | None = None,
    on_result: Callable[[dict[str, Any]], None],
) -> int:
    """Stream a shard's results through ``on_result`` without retaining them.

    Like :func:`run_task_shard`, but the parent never accumulates results: each
    worker line is handed to ``on_result`` (typically an append to disk) and then
    dropped, so parent RSS stays bounded regardless of shard size. Returns the
    number of results received. This is the collector's path (A8): a 500-game
    shard used to pin multiple GB in the parent and push the box into swap.
    """
    if on_result is None:
        raise ValueError("stream_task_shard requires an on_result callback")
    shard = list(shard)
    if not shard:
        return 0
    resolved = _resolve_shard_workers(shard, workers)
    python, env, root = _shard_exec_env()

    if resolved == 1:
        _results, received = _drive_worker(
            shard,
            python=python,
            env=env,
            repo_root=root,
            worker_module=worker_module,
            on_result=on_result,
            retain_results=False,
        )
        return received

    from concurrent.futures import ThreadPoolExecutor

    chunks = [shard[i::resolved] for i in range(resolved)]
    chunks = [chunk for chunk in chunks if chunk]
    total = 0
    with ThreadPoolExecutor(max_workers=len(chunks)) as pool:
        futures = [
            pool.submit(
                _drive_worker,
                chunk,
                python=python,
                env=env,
                repo_root=root,
                worker_module=worker_module,
                on_result=on_result,
                retain_results=False,
            )
            for chunk in chunks
        ]
        for future in futures:
            _results, received = future.result()
            total += received
    return total


def normalize_workers(workers: int | None) -> int | None:
    """Resolve a CLI ``--workers`` value.

    ``None`` and ``0`` both mean "all cores" (the historical CLI meaning of 0);
    negative values are rejected. Keeping this in one place removes the
    ``--workers 0 -> 1 sequential worker`` footgun (which also disabled
    incremental streaming).
    """
    if workers is None:
        return None
    workers = int(workers)
    if workers < 0:
        raise ValueError(f"workers must be >= 0, got {workers}")
    return None if workers == 0 else workers


def run_tasks(
    tasks: Iterable[MatchTask],
    *,
    workers: int | None = None,
    progress: bool = False,
    on_result: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Run tasks, sequentially when ``workers <= 1`` else across worker processes.

    ``on_result`` is invoked from reader threads as each match result arrives,
    so callers can stream results to disk instead of losing everything when a
    long run is interrupted (PLAN.md D7: everything must be resumable).

    Worker processes are spawned as fresh interpreters running
    ``envs.rollout_worker`` over a JSON-lines pipe. This deliberately avoids
    ``multiprocessing`` fork (which deadlocks the engine's embedded JS runtime)
    and spawn's ``__main__`` re-import (which breaks under pytest / ``-m``).
    """
    import os as _os
    import sys

    from common.paths import repo_root

    tasks = list(tasks)
    if not tasks:
        return []
    resolved = workers if workers is not None else (_os.cpu_count() or 1)
    resolved = max(1, min(int(resolved), len(tasks)))
    if resolved == 1:
        results = _run_tasks_sequential(tasks)
        if on_result is not None:
            for match in results:
                on_result(match)
        return results

    shards = [tasks[i::resolved] for i in range(resolved)]
    shards = [shard for shard in shards if shard]
    env = dict(_os.environ)
    root = str(repo_root())
    env["PYTHONPATH"] = root + _os.pathsep + env.get("PYTHONPATH", "")
    python = sys.executable

    from concurrent.futures import ThreadPoolExecutor

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=len(shards)) as pool:
        futures = [
            pool.submit(
                _run_shard_in_subprocess,
                shard,
                python=python,
                env=env,
                repo_root=root,
                on_result=on_result,
            )
            for shard in shards
        ]
        for future in futures:
            results.extend(future.result())
    results.sort(key=lambda item: item.get("task_index", 0))
    return results


def make_task_grid(
    deck_pairs: Iterable[tuple[str, str]],
    seeds: Iterable[int],
    *,
    policy0: str = "expert",
    policy1: str = "expert",
    tag: str = "",
    base_seed: int = 0,
) -> list[MatchTask]:
    """Build a deterministic task list for every (deck pair, seed) combination.

    The *same* seed set is reused for every pairing (paired seeds), which keeps
    variance low for A/B comparison (PLAN.md §4.1 L1).
    """
    seed_list = [int(s) for s in seeds]
    tasks: list[MatchTask] = []
    index = 0
    for deck0, deck1 in deck_pairs:
        for raw_seed in seed_list:
            tasks.append(
                MatchTask(
                    index=index,
                    deck0=deck0,
                    deck1=deck1,
                    seed=int(base_seed) + raw_seed,
                    policy0=policy0,
                    policy1=policy1,
                    tag=tag,
                )
            )
            index += 1
    return tasks


def rollout_metadata(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return runtime_metadata(extra)
