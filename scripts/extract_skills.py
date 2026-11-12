#!/usr/bin/env python3
"""Turn one group's results into a candidate skill library (one evolution round).

Pipeline (the "dynamic" arm's first half):

1. read the per-game rows of a run (e.g. ``llm_empty`` or a previous library version);
2. build a per-stage failure profile (which objectives fail, how often, round stats)
   plus one close loss and one 3★ trace per stage when rows carry ``trace``;
3. ask the model to write 1-2 injected prompts per failing stage (``skillloop.extract``);
4. write a **new library version** = current library + the candidates, snapshot it, and
   print the candidates so the gate can be run on unseen seeds.

The candidates are written with ``status="active"`` in the *new version file only*; the
gate decides whether that version is worth keeping. The previous version stays on disk
under ``history/`` so a failed gate is just "don't promote".

Usage::

    python -m scripts.extract_skills --rows data/runs/smoke2_llm_empty.jsonl \\
        --base data/skills/rules_v1 --out data/skills/dyn_v1
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STAGES_JSON = REPO / "configs/npc/npc_stages_mond_liyue.json"


def stage_objectives(path: Path = STAGES_JSON) -> dict[str, tuple[str, ...]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    stages = doc if isinstance(doc, list) else doc.get("stages", [])
    return {str(s["name"]): tuple(str(o.get("text") or "") for o in s.get("objectives") or [])
            for s in stages}


def stage_round_caps(path: Path = STAGES_JSON) -> dict[str, int]:
    """Per-stage round cap from ``win_within_rounds`` (used to rank close losses)."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    stages = doc if isinstance(doc, list) else doc.get("stages", [])
    caps: dict[str, int] = {}
    for stage in stages:
        for objective in stage.get("objectives") or []:
            if objective.get("type") == "win_within_rounds" and objective.get("n") is not None:
                caps[str(stage["name"])] = int(objective["n"])
                break
    return caps


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", required=True, help="per-game JSONL of the run to learn from")
    ap.add_argument("--base", default="data/skills/rules_v1", help="library to extend (may be absent)")
    ap.add_argument("--out", default="data/skills/dyn_v1")
    ap.add_argument("--model", default=None)
    ap.add_argument("--min-failure-rate", type=float, default=0.15)
    ap.add_argument("--effort", default="high", help="thinking effort for extraction (default high)")
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--max-replays", type=int, default=2,
                    help="representative game traces per stage (max 2: one loss + one 3★)")
    ap.add_argument("--dry-run", action="store_true", help="print candidates, do not write")
    args = ap.parse_args(argv)

    from common.llm import build_llm_client
    from skillloop.extract import failure_profiles, propose_candidates
    from skillloop.library import SkillLibrary

    rows = [json.loads(line) for line in Path(args.rows).read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [row for row in rows if not row.get("error") and not row.get("truncated")]
    if not rows:
        raise SystemExit(f"no valid rows in {args.rows}")

    # Rows written with ``skillloop.run --trace`` carry ``trace``; older rows do
    # not and simply contribute statistics with no replay block.
    profiles = failure_profiles(
        rows,
        objectives_by_stage=stage_objectives(),
        round_caps_by_stage=stage_round_caps(),
        max_replays=args.max_replays,
    )
    client = build_llm_client()
    if client is None:
        raise SystemExit("no LLM config; fill .env (see scripts/probe_llm.py)")

    base_path = Path(args.base)
    library = SkillLibrary.load(base_path) if (base_path / "library.json").exists() else SkillLibrary()
    rules_note = library.rules_text()

    candidates = propose_candidates(
        client, profiles, model=args.model, rules_note=rules_note,
        min_failure_rate=args.min_failure_rate,
        effort=args.effort, max_tokens=args.max_tokens,
    )
    print(f"[extract] {len(profiles)} stage profiles -> {len(candidates)} candidates "
          f"({client.usage.calls} calls, ${client.usage.cost_usd:.4f})")
    for skill in candidates:
        print(f"  - {skill.skill_id:40s} [{skill.layer}] {skill.skill_name}")
        print(f"      {skill.description[:100]}")

    if args.dry_run or not candidates:
        print("[extract] dry-run / nothing to add")
        return 0

    for skill in candidates:
        skill.status = "active"          # active only in this new version; the gate decides
        if skill.skill_id in library.skills:
            library.update(skill.skill_id, description=skill.description,
                           conditions=skill.conditions)
        else:
            library.add(skill)

    out = Path(args.out) if Path(args.out).is_absolute() else REPO / args.out
    library.meta.update({
        "kind": "evolved",
        "parent": str(args.base),
        "source_rows": str(args.rows),
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_candidates": len(candidates),
    })
    library.bump_version(note=f"+{len(candidates)} candidates from {Path(args.rows).name}")
    library.save(out)
    library.save(out, snapshot=True)
    print(f"[extract] wrote {out} (version {library.version}, {len(library)} skills)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
