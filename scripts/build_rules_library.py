#!/usr/bin/env python3
"""Build the static (rules-only) skill library used by group ②.

The library is generated, not hand-edited, so the static condition contains no
accidentally-learned strategy: it is derived from the stage objective types in
``configs/npc`` plus the engine rules in ``skillloop.rules``.

Usage::

    python scripts/build_rules_library.py                    # -> data/skills/rules_v1
    python scripts/build_rules_library.py --out data/skills/rules_v2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STAGES_JSON = REPO / "configs/npc/npc_stages_mond_liyue.json"


def objective_types(path: Path = STAGES_JSON) -> list[str]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    stages = doc if isinstance(doc, list) else doc.get("stages", [])
    found: set[str] = set()
    for stage in stages:
        for objective in stage.get("objectives") or []:
            if objective.get("type"):
                found.add(str(objective["type"]))
    return sorted(found)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="data/skills/rules_v1")
    ap.add_argument("--version", type=int, default=1)
    args = ap.parse_args(argv)

    from skillloop.rules import build_rules_library

    types = objective_types()
    library = build_rules_library(types, version=args.version)
    library.meta.update({
        "kind": "rules",
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "objective_types": types,
        "source_sha1": hashlib.sha1(STAGES_JSON.read_bytes()).hexdigest()[:12],
        "builder": "scripts/build_rules_library.py",
    })

    out = REPO / args.out
    path = library.save(out)
    library.save(out, snapshot=True)
    rules = library.rules_text()

    print(f"[rules] {len(library)} skills {library.by_layer()} -> {path}")
    print(f"[rules] objective types: {', '.join(types)}")
    print(f"[rules] always-on rules text: {len(rules)} chars (~{len(rules) // 2} tokens)")
    print(f"[rules] snapshot: {out}/history/v{library.version}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
