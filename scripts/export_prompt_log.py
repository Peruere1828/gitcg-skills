#!/usr/bin/env python3
"""Export a GITCG_LLM_TRACE JSONL as a readable Markdown prompt log.

Usage::

    # default: $GITCG_LLM_TRACE or data/web/llm_trace.jsonl
    python scripts/export_prompt_log.py --out /tmp/prompts.md

    # one web session / one seat
    python scripts/export_prompt_log.py --session <sid> --who 0 --out /tmp/s.md

The trace is written by ``common.llm`` when ``GITCG_LLM_TRACE`` is set (the web
launcher defaults it to ``data/web/llm_trace.jsonl``). API keys are never in the
trace; text is redacted again on export.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from skillloop.prompt_log import (  # noqa: E402
    filter_trace,
    load_trace,
    render_markdown,
    summarize,
)

DEFAULT_TRACE = "data/web/llm_trace.jsonl"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", default=os.environ.get("GITCG_LLM_TRACE") or DEFAULT_TRACE)
    parser.add_argument("--session", default=None)
    parser.add_argument("--who", type=int, default=None)
    parser.add_argument("--stage", default=None)
    parser.add_argument("--out", default=None, help="Markdown output path (default: stdout)")
    parser.add_argument("--max-reasoning", type=int, default=None,
                        help="truncate each thinking block to N characters")
    args = parser.parse_args(argv)

    rows = load_trace(args.trace)
    rows = filter_trace(rows, session=args.session, who=args.who, stage=args.stage)
    if not rows:
        print(f"no trace rows in {args.trace} (filters: session={args.session}, "
              f"who={args.who}, stage={args.stage})", file=sys.stderr)
        return 1
    markdown = render_markdown(
        rows,
        title=f"Prompt log · {Path(args.trace).name}",
        max_reasoning=args.max_reasoning,
    )
    if args.out:
        Path(args.out).write_text(markdown, encoding="utf-8")
        print(f"wrote {args.out} ({summarize(rows)['calls']} calls)", file=sys.stderr)
    else:
        sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
