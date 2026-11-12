#!/usr/bin/env python3
"""Plan and save the per-(stage, library version) skill loadouts.

One model call per stage (temperature 0), frozen for every seed of that stage.
Loadouts are only used by the static/dynamic groups::

    # static group -> reports/loadouts/v1
    python -m scripts.plan_loadout --library data/skills/static_v1 \\
        --out reports/loadouts/v1 --stages all --client real

    # dynamic group -> reports/loadouts/v4
    python -m scripts.plan_loadout --library data/skills/dyn_v4 \\
        --out reports/loadouts/v4 --stages all --client real

``--client stub`` never builds a network client; it answers with the library's
BM25 top-k so the full parse/validate/save path can be exercised offline.
``llm_empty``/``scripted``/``contestant`` do not use loadouts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common.llm import MockLLMClient, build_llm_client
from npc.spec import get_stage, stage_names
from skillloop.library import SkillLibrary
from skillloop.loadout import (
    load_stage_decks,
    plan_loadout,
    save_loadout,
    stage_context,
    stage_task,
)


def _stages(value: str) -> tuple[str, ...]:
    if value == "all":
        return tuple(stage_names())
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _stub_client(library: SkillLibrary, stage, deck: str, max_skills: int) -> MockLLMClient:
    """Offline stand-in: valid JSON built from the same BM25 fallback path."""
    task = stage_task(stage, deck)
    picked = library.retrieve(task.describe(), stage_context(stage), k=max_skills)
    reply = json.dumps(
        {
            "skills": [skill.skill_id for skill in picked],
            "rationale": "stub: BM25 top-k",
        },
        ensure_ascii=False,
    )
    return MockLLMClient([reply], name="stub")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--library", default="data/skills/dyn_v4",
        help=(
            "library whose version the loadout is frozen against "
            "(static: data/skills/static_v1; dynamic: data/skills/dyn_v4); "
            "loadouts are only used by static/dynamic"
        ),
    )
    parser.add_argument("--out", default="reports/loadouts")
    parser.add_argument("--stages", default="all", help="'all' or comma-separated stage names")
    parser.add_argument("--client", choices=("real", "stub"), default="stub")
    parser.add_argument("--stage-decks", default="configs/stage_decks.json")
    parser.add_argument("--deck", default="superconduct_aggro",
                        help="fallback for stages missing from --stage-decks")
    parser.add_argument("--max-skills", type=int, default=5)
    parser.add_argument("--model", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    library = SkillLibrary.load(args.library)

    stage_decks: dict[str, str] = {}
    decks_path = Path(args.stage_decks)
    if decks_path.exists():
        stage_decks = load_stage_decks(decks_path)

    real_client = None
    if args.client == "real":
        real_client = build_llm_client()
        if real_client is None:
            raise SystemExit(
                "no LLM config available; set GITCG_LLM_* / DEEPSEEK_* or use --client stub"
            )

    rows: list[tuple[str, str, int, bool]] = []
    for name in _stages(args.stages):
        stage = get_stage(name)
        deck = stage_decks.get(name, args.deck)
        if args.client == "stub":
            client = _stub_client(library, stage, deck, args.max_skills)
        else:
            client = real_client
        loadout = plan_loadout(
            stage,
            library,
            deck,
            client,
            model=args.model,
            max_skills=args.max_skills,
        )
        path = save_loadout(args.out, loadout)
        rows.append((name, deck, len(loadout.skills), loadout.fallback))
        print(f"  saved {path}", flush=True)

    width = max((len(row[0]) for row in rows), default=5)
    print(f"\n{'stage':{width}s}  {'deck':28s}  skills  fallback")
    print("-" * (width + 45))
    for name, deck, n_skills, fallback in rows:
        print(f"{name:{width}s}  {deck:28s}  {n_skills:6d}  {'yes' if fallback else 'no'}")
    fallbacks = sum(1 for row in rows if row[3])
    print(
        f"\n{len(rows)} stages -> {args.out} (library v{library.version}, "
        f"client={args.client}, fallbacks={fallbacks})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
