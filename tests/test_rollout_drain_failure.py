"""A10: a dead reader thread / stuck worker must fail fast, never hang.

``_drive_worker`` drains stdout/stderr on threads while the parent writes tasks
and then waits. If a drain thread raised (a non-JSON line on stdout -- exactly
the engine-prints-to-stdout hazard ``npc/eval_worker`` warns about -- or an
``on_result`` callback raising, e.g. the collector's per-result disk append
hitting ENOSPC), the thread died silently, the worker blocked on the full stdout
pipe, and the parent hung forever in ``process.wait()``. These tests drive the
real subprocess plumbing with fake workers and assert the parent raises within
seconds instead of hanging.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

from envs.rollout import MatchTask, _drive_worker, stream_task_shard

REPO_ROOT = Path(__file__).resolve().parents[1]

NORMAL_WORKER = """\
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    task = json.loads(line)
    sys.stdout.write(json.dumps({"index": task["index"], "task_index": task["index"]}) + "\\n")
    sys.stdout.flush()
"""

# Prints a non-JSON line (engine noise), then floods stdout and sleeps. Without a
# live reader the worker blocks on the full pipe forever -> old parent hang.
NOISY_WORKER = """\
import sys, time
sys.stdout.write("V8: [warn] engine noise that is not JSON\\n")
sys.stdout.flush()
pad = "x" * 1024
for i in range(20000):
    sys.stdout.write('{"index": %d, "pad": "%s"}\\n' % (i, pad))
sys.stdout.flush()
time.sleep(600)
"""

# Consumes stdin, writes nothing, never exits: only the timeout guard can save us.
HANG_WORKER = """\
import sys, time
for _line in sys.stdin:
    pass
time.sleep(600)
"""

# Emits fewer results than tasks, then exits cleanly (truncated shard contract).
TRUNCATED_WORKER = """\
import json, sys
for i, line in enumerate(sys.stdin):
    if i >= 2:
        break
    line = line.strip()
    if not line:
        continue
    task = json.loads(line)
    sys.stdout.write(json.dumps({"index": task["index"]}) + "\\n")
    sys.stdout.flush()
"""


def _env(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(tmp_path), str(REPO_ROOT)])
    return env


def _tasks(count: int) -> list[MatchTask]:
    return [MatchTask(index=i, deck0="deck_a", deck1="deck_b", seed=i) for i in range(count)]


def _run_with_guard(fn, *, timeout: float = 30.0) -> dict:
    """Run ``fn`` on a thread; assert it finishes (i.e. does not hang)."""
    outcome: dict = {}

    def _run() -> None:
        try:
            outcome["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - surfaced via outcome
            outcome["error"] = exc

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    assert not thread.is_alive(), "parent hung: a reader failure did not fail fast"
    return outcome


def test_non_json_line_fails_fast_not_hang(tmp_path: Path) -> None:
    (tmp_path / "noisy_worker.py").write_text(NOISY_WORKER, encoding="utf-8")

    outcome = _run_with_guard(
        lambda: _drive_worker(
            _tasks(4),
            python=sys.executable,
            env=_env(tmp_path),
            repo_root=str(tmp_path),
            worker_module="noisy_worker",
            timeout=30.0,
        ),
        timeout=30.0,
    )

    assert isinstance(outcome.get("error"), RuntimeError), outcome
    message = str(outcome["error"])
    assert "non-JSON" in message
    assert "JSONDecodeError" in message


def test_on_result_exception_fails_fast(tmp_path: Path) -> None:
    (tmp_path / "normal_worker.py").write_text(NORMAL_WORKER, encoding="utf-8")

    def _boom(_match: dict) -> None:
        raise OSError(28, "No space left on device")

    outcome = _run_with_guard(
        lambda: _drive_worker(
            _tasks(5),
            python=sys.executable,
            env=_env(tmp_path),
            repo_root=str(tmp_path),
            worker_module="normal_worker",
            on_result=_boom,
            retain_results=False,
            timeout=30.0,
        ),
        timeout=30.0,
    )

    assert isinstance(outcome.get("error"), RuntimeError), outcome
    message = str(outcome["error"])
    assert "on_result failed" in message
    assert "No space left on device" in message


def test_hung_worker_times_out_and_is_killed(tmp_path: Path) -> None:
    (tmp_path / "hang_worker.py").write_text(HANG_WORKER, encoding="utf-8")

    outcome = _run_with_guard(
        lambda: _drive_worker(
            _tasks(3),
            python=sys.executable,
            env=_env(tmp_path),
            repo_root=str(tmp_path),
            worker_module="hang_worker",
            timeout=2.0,
        ),
        timeout=30.0,
    )

    assert isinstance(outcome.get("error"), RuntimeError), outcome
    assert "hung" in str(outcome["error"])


def test_truncated_shard_still_raises(tmp_path: Path) -> None:
    (tmp_path / "truncated_worker.py").write_text(TRUNCATED_WORKER, encoding="utf-8")

    outcome = _run_with_guard(
        lambda: _drive_worker(
            _tasks(4),
            python=sys.executable,
            env=_env(tmp_path),
            repo_root=str(tmp_path),
            worker_module="truncated_worker",
        ),
        timeout=30.0,
    )

    assert isinstance(outcome.get("error"), RuntimeError), outcome
    assert "2/4 results" in str(outcome["error"])


def test_normal_path_unchanged(tmp_path: Path) -> None:
    (tmp_path / "normal_worker.py").write_text(NORMAL_WORKER, encoding="utf-8")

    results, received = _drive_worker(
        _tasks(6),
        python=sys.executable,
        env=_env(tmp_path),
        repo_root=str(tmp_path),
        worker_module="normal_worker",
    )

    assert received == 6
    assert sorted(r["index"] for r in results) == list(range(6))


def test_stream_semantics_preserved(tmp_path: Path) -> None:
    (tmp_path / "normal_worker.py").write_text(NORMAL_WORKER, encoding="utf-8")

    seen: list[dict] = []
    results, received = _drive_worker(
        _tasks(4),
        python=sys.executable,
        env=_env(tmp_path),
        repo_root=str(tmp_path),
        worker_module="normal_worker",
        on_result=seen.append,
        retain_results=False,
    )

    assert results == []  # retain_results=False keeps nothing in the parent
    assert received == 4
    assert sorted(m["index"] for m in seen) == list(range(4))


def test_stream_task_shard_noisy_worker_fails_fast(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "noisy_worker.py").write_text(NOISY_WORKER, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))

    outcome = _run_with_guard(
        lambda: stream_task_shard(
            _tasks(4),
            worker_module="noisy_worker",
            workers=1,
            on_result=lambda _m: None,
        ),
        timeout=30.0,
    )

    assert isinstance(outcome.get("error"), RuntimeError), outcome
    assert "non-JSON" in str(outcome["error"])
