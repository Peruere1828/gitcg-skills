#!/usr/bin/env python3
"""Apply the difficulty HP rule to the NPC enemies and their stage lineups.

Rule (PLAN.md D4): ``hp <= 6 -> 6``, otherwise ``hp += 2``.

The engine's real HP lives in the compiled data package (``.gts`` files), **not** in
``configs/npc``: our 17 enemy cards are overlay files, while two enemies
(``fatui_pyro_agent``, ``mirror_maiden``) come from upstream and are overridden by
copying their file into the overlay tree (``apply-overlay.sh`` overwrites in place).

The script also repairs the display side (``hp_card`` in the enemy table, ``hp`` in the
stage lineups) so the configs stop disagreeing with the engine, and writes an audit
snapshot ``configs/npc/hp_table.json`` used by ``--restore``.

Usage::

    python scripts/patch_npc_hp.py --dry-run
    python scripts/patch_npc_hp.py                  # apply (refuses if already applied)
    python scripts/patch_npc_hp.py --restore        # undo using the audit snapshot
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OVERLAY_CHARS = REPO / "engine-overlay/overlay/packages/data/src/characters"
ENEMIES_JSON = REPO / "configs/npc/npc_enemies_all.json"
STAGES_JSON = REPO / "configs/npc/npc_stages_mond_liyue.json"
AUDIT_JSON = REPO / "configs/npc/hp_table.json"

# Enemy slug -> overlay file stem. ``camel -> snake`` covers most; these do not.
ALIASES = {
    "fatuiskirmisheranemoboxervanguard": "fatui_anemoboxer_vanguard",
    "fatuiskirmisherhydrogunnerlegionnaire": "fatui_hydrogunner_legionnaire",
    "fatuipyroagent": "fatui_pyro_agent",
    "mirrormaiden": "mirror_maiden",
}
UPSTREAM_ONLY = {"fatuipyroagent", "mirrormaiden"}


def camel_to_snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _index(root: Path) -> dict[str, Path]:
    """normalised file stem -> path (``hydro_slime`` and ``hydroslime`` both work)."""
    out: dict[str, Path] = {}
    for path in root.glob("*/**/*.gts"):
        out[_norm(path.stem)] = path
    return out


def resolve_file(enemy_id: str, engine_root: Path | None) -> tuple[Path | None, str]:
    """Find the enemy's ``.gts``; returns ``(path, source)``.

    ``source`` is ``overlay`` when we already own the file, ``upstream-override``
    when it must be copied out of the upstream tree, and ``missing`` otherwise.
    ``ALIASES`` first, then plain normalised matching (``hydroslime`` == ``hydro_slime``).
    """
    overlay = _index(OVERLAY_CHARS)
    key = _norm(ALIASES.get(enemy_id.lower(), enemy_id))
    if key in overlay:
        return overlay[key], "overlay"
    if engine_root is not None and engine_root.exists():
        upstream = _index(engine_root / "packages/data/src/characters")
        if key in upstream:
            return upstream[key], "upstream-override"
    return None, "missing"


def read_health(text: str) -> int | None:
    m = re.search(r"^\s*health (\d+);", text, re.M)
    return int(m.group(1)) if m else None


def rewrite_health(text: str, hp: int) -> str:
    text, n1 = re.subn(r"^(\s*health )(\d+)(;)", rf"\g<1>{hp}\g<3>", text, flags=re.M)
    text, n2 = re.subn(r"^(\s*\* @hp )(\d+)$", rf"\g<1>{hp}", text, flags=re.M)
    if n1 != 1:
        raise SystemExit(f"expected exactly one `health N;`, found {n1}")
    return text


def new_hp(hp: int) -> int:
    return 6 if hp <= 6 else hp + 2


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def enemies_of(doc) -> list[dict]:
    return doc["enemies"] if isinstance(doc, dict) and "enemies" in doc else doc


def stages_of(doc) -> list[dict]:
    return doc["stages"] if isinstance(doc, dict) and "stages" in doc else doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--engine-root", default=str(REPO / "build/engine"),
                    help="materialised engine tree (needed to copy the 2 upstream enemies)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--force", action="store_true", help="re-apply even if an audit exists")
    args = ap.parse_args(argv)

    engine_root = Path(args.engine_root)

    if args.restore:
        if not AUDIT_JSON.exists():
            raise SystemExit(f"no audit snapshot at {AUDIT_JSON}")
        audit = load_json(AUDIT_JSON)
        for entry in audit["enemies"]:
            path = REPO / entry["file"]
            text = path.read_text(encoding="utf-8")
            path.write_text(rewrite_health(text, entry["health_before"]), encoding="utf-8")
        doc = load_json(ENEMIES_JSON)
        before = {e["id"]: e for e in audit["enemies"]}
        for enemy in enemies_of(doc):
            if enemy["id"] in before:
                enemy["hp_card"] = before[enemy["id"]]["hp_card_before"]
        save_json(ENEMIES_JSON, doc)
        stages_doc = load_json(STAGES_JSON)
        orig = {s["name"]: s for s in audit["stages"]}
        for stage in stages_of(stages_doc):
            if stage["name"] in orig:
                for slot, hp in zip(stage.get("lineup") or [], orig[stage["name"]]["lineup_hp"]):
                    slot["hp"] = hp
        save_json(STAGES_JSON, stages_doc)
        AUDIT_JSON.unlink()
        print("[restore] done")
        return 0

    if AUDIT_JSON.exists() and not args.force:
        raise SystemExit(f"{AUDIT_JSON} exists -> already applied (use --force or --restore)")

    enemy_doc = load_json(ENEMIES_JSON)
    enemies = enemies_of(enemy_doc)
    entries: list[dict] = []
    for enemy in enemies:
        eid = enemy["id"]
        path, source = resolve_file(eid, engine_root)
        if path is None:
            raise SystemExit(
                f"{eid}: no .gts in overlay and none in {engine_root}; "
                "copy build/engine from the upstream repository first"
            )
        if source == "upstream-override":
            rel = path.relative_to(engine_root / "packages/data/src/characters")
            target = OVERLAY_CHARS / rel
            file_for_audit = target
            if not args.dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                path = target
        else:
            file_for_audit = path
        text = path.read_text(encoding="utf-8")
        hp = read_health(text)
        if hp is None:
            raise SystemExit(f"{path}: no `health N;` found")
        target = new_hp(hp)
        if not args.dry_run:
            path.write_text(rewrite_health(text, target), encoding="utf-8")
            enemy["hp_card"] = target
        entries.append({
            "id": eid,
            "file": str(file_for_audit.relative_to(REPO)),
            "source": source,
            "health_before": hp,
            "health_after": target,
            "hp_card_before": enemy.get("hp_card"),
        })
        print(f"{eid:38s} {hp:3d} -> {target:3d}  ({source})")

    stage_doc = load_json(STAGES_JSON)
    stage_entries = []
    by_id = {e["id"]: e for e in entries}
    for stage in stages_of(stage_doc):
        lineup = stage.get("lineup") or []
        stage_entries.append({
            "name": stage["name"],
            "lineup_hp": [int(slot.get("hp") or 0) for slot in lineup],
        })
        if not args.dry_run:
            for slot in lineup:
                entry = by_id.get(slot["id"])
                if entry:
                    slot["hp"] = entry["health_after"]

    if args.dry_run:
        print("\n[dry-run] no files written")
        return 0

    save_json(ENEMIES_JSON, enemy_doc)
    save_json(STAGES_JSON, stage_doc)
    audit = {
        "rule": "hp<=6 -> 6; else hp+2",
        "note": "engine HP is authoritative; hp_card/lineup.hp mirror it for display + docs",
        "enemies": entries,
        "stages": stage_entries,
    }
    save_json(AUDIT_JSON, audit)
    print(f"\n[apply] wrote {AUDIT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
