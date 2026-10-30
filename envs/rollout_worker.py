"""Worker entrypoint for ``envs.rollout``: JSON-lines in, JSON-lines out.

Run as ``python -m envs.rollout_worker``. Reads one ``MatchTask`` dict per line
on stdin and writes one match-result dict per line on stdout. Anything the engine
prints to stdout would corrupt the protocol, so this must stay quiet; run with
``GITCG_WORKER_DEBUG=1`` to forward a traceback to stderr on failure.
"""

from __future__ import annotations

import json
import os
import sys
import traceback

from envs.rollout import MatchTask, run_task


def _process_line(line: str) -> dict:
    payload = json.loads(line)
    task = MatchTask(
        index=int(payload["index"]),
        deck0=payload["deck0"],
        deck1=payload["deck1"],
        seed=int(payload["seed"]),
        policy0=payload.get("policy0", "expert"),
        policy1=payload.get("policy1", "expert"),
        record_decisions=bool(payload.get("record_decisions", False)),
        record_views=bool(payload.get("record_views", False)),
        tag=payload.get("tag", ""),
        deck0_inline=payload.get("deck0_inline"),
        deck1_inline=payload.get("deck1_inline"),
    )
    return run_task(task)


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            result = _process_line(line)
        except Exception:
            print(traceback.format_exc(), file=sys.stderr, flush=True)
            return 1
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
