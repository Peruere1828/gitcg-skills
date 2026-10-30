"""Worker entrypoint for ``npc.eval``: JSON-lines in, JSON-lines out.

Run as ``python -m npc.eval_worker``. Reads one ``npc.eval.NpcTask`` dict per line
on stdin and writes one result-row dict per line on stdout. Kept quiet on stdout
(the engine must not print there); a failure forwards the traceback to stderr and
exits non-zero so ``envs.rollout.run_task_shard`` reports it.
"""

from __future__ import annotations

import json
import sys
import traceback

from npc.eval import _worker


def _process_line(line: str) -> dict:
    return _worker(json.loads(line))


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
        sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
