"""Regression test for the rollout pipe deadlock on large shards.

``_run_shard_in_subprocess`` used to write *all* tasks to the worker's stdin
before reading any stdout. With a shard large enough to fill both pipes
(64 KiB each) this deadlocks: the worker blocks writing results while the
parent blocks writing tasks. Small runs (smoke/benchmark) never filled the
pipes, so only a full-size arena exposed it.

The fake worker below inflates every task into a large result line so the
deadlock reproduces in well under a second with a few hundred tasks.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

from envs.rollout import MatchTask, _run_shard_in_subprocess

FAKE_WORKER = """\
import json
import sys

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    task = json.loads(line)
    sys.stdout.write(json.dumps({"index": task["index"], "pad": "x" * 262144}) + "\\n")
    sys.stdout.flush()
"""

PIPE_BYTES = 64 * 1024  # default Linux pipe capacity; both directions must exceed it


def _make_tasks(count: int) -> list[MatchTask]:
    return [
        MatchTask(
            index=i,
            deck0="deck_a",
            deck1="deck_b",
            seed=i,
            tag="t" * 1024,  # push stdin payload well past the pipe buffer
        )
        for i in range(count)
    ]


def test_large_shard_does_not_deadlock(tmp_path: Path) -> None:
    module = tmp_path / "fake_rollout_worker.py"
    module.write_text(FAKE_WORKER, encoding="utf-8")

    tasks = _make_tasks(300)
    assert len(tasks) * 1024 > PIPE_BYTES  # stdin side must overflow the pipe

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(tmp_path), str(Path(__file__).resolve().parents[1])])

    outcome: dict[str, object] = {}

    def _run() -> None:
        try:
            outcome["results"] = _run_shard_in_subprocess(
                tasks,
                python=sys.executable,
                env=env,
                repo_root=str(tmp_path),
                worker_module="fake_rollout_worker",
            )
        except BaseException as exc:  # noqa: BLE001 - surfaced via outcome
            outcome["error"] = exc

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=60)
    assert not thread.is_alive(), (
        "rollout subprocess plumbing deadlocked on a large shard "
        "(stdout must drain concurrently with stdin writes)"
    )
    assert "error" not in outcome, outcome.get("error")
    results = outcome["results"]
    assert isinstance(results, list) and len(results) == len(tasks)
    assert sorted(r["index"] for r in results) == list(range(len(tasks)))
