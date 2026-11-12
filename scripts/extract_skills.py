#!/usr/bin/env python3
"""Turn one group's results into a candidate skill library (one evolution round).

Pipeline (the "dynamic" arm's first half):

1. read the per-game rows of a run (e.g. ``llm_empty`` or a previous library version);
2. build a per-stage failure profile (which objectives fail, how often, round stats)
   plus one close loss and one 3★ trace per stage when rows carry ``trace``;
3. attach the stage's fixed deck facts (``configs/stage_decks.json``), its special
   rules (``configs/npc/special_rules.json``) and the relevant non-rule skills from
   the base library, so candidates can name concrete characters/cards and replace
   a known-bad skill instead of repeating it;
4. ask the model to write at most 2 injected prompts per failing stage
   (``skillloop.extract``);
5. write a **new library version** = current library + the candidates, snapshot it, and
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
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

REPO = Path(__file__).resolve().parents[1]
STAGES_JSON = REPO / "configs/npc/npc_stages_mond_liyue.json"
STAGE_DECKS_JSON = REPO / "configs/stage_decks.json"

EXISTING_SKILLS_LIMIT = 8
EXISTING_DESCRIPTION_LIMIT = 120

_ELEMENT_CN = {
    "CRYO": "冰", "HYDRO": "水", "PYRO": "火", "ELECTRO": "雷",
    "ANEMO": "风", "GEO": "岩", "DENDRO": "草",
}
_TARGET_CN = {
    "friendly_all": "我方全体角色",
    "friendly_active": "我方出战角色",
    "enemy_active": "敌方出战角色",
    "both_active": "双方出战角色",
}
_VIA_SUPPORT_CN = {
    "artifact_free_equip": "圣遗物免费装备",
    "weapon_free_equip": "武器免费装备",
}


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


def load_stage_decks(path: Path = STAGE_DECKS_JSON) -> dict[str, str]:
    """``stage -> deck slug`` from ``configs/stage_decks.json`` (empty on failure)."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    stages = doc.get("stages") if isinstance(doc, dict) else None
    if not isinstance(stages, dict):
        return {}
    decks: dict[str, str] = {}
    for name, entry in stages.items():
        deck = entry.get("deck") if isinstance(entry, dict) else entry
        if isinstance(deck, str) and deck.strip():
            decks[str(name)] = deck.strip()
    return decks


def deck_facts_text(deck: str) -> str:
    """One-line character/card summary for the fixed deck (``""`` when unavailable)."""
    if not deck:
        return ""
    try:
        from skillloop.cards import deck_facts
        from skillloop.era import resolve_deck

        facts = deck_facts(resolve_deck(deck))
    except Exception:  # missing registry/assets/gitcg: degrade, never fail extraction
        return ""
    characters = "、".join(facts.get("characters") or ()) or "未知"
    elements = "、".join(
        f"{label}x{count}" for label, count in (facts.get("elements") or {}).items()
    ) or "未知"
    counts = facts.get("cards") or {}
    card_text = "、".join(
        f"{label}x{counts.get(key, 0)}"
        for key, label in (
            ("weapon", "武器"), ("artifact", "圣遗物"), ("event", "事件"),
            ("support", "支援"), ("other", "其它"), ("unknown", "未知"),
        )
    )
    return f"{characters}；元素：{elements}；卡牌：{card_text}"


def stage_special_rules_text(stage: str) -> str:
    """Human-readable special rules (``""`` when the stage has none)."""
    try:
        from npc.spec import get_stage

        spec = get_stage(stage)
    except Exception:
        return ""
    text = str(getattr(spec, "special_rules_text", "") or "").strip()
    if text:
        return text

    parts: list[str] = []
    try:
        from npc.stage_rules import load_rules

        rule = load_rules().get(stage)
    except Exception:
        rule = None
    if rule is not None:
        for support in rule.supports:
            parts.append(f"开局我方支援区有「{support.name or support.card_id}」")
        for effect in rule.effects:
            if effect.kind == "initial_energy":
                target = _TARGET_CN.get(effect.target, effect.target)
                parts.append(f"{target}初始能量+{effect.value}")
            elif effect.kind == "element_application":
                target = _TARGET_CN.get(effect.target, effect.target)
                raw_element = str(effect.element or "")
                element = _ELEMENT_CN.get(raw_element.upper(), raw_element)
                parts.append(f"行动阶段开始为{target}附着{element}元素")
        if rule.via_support_effects:
            via = "、".join(
                _VIA_SUPPORT_CN.get(kind, kind) for kind in rule.via_support_effects
            )
            parts.append(f"支援牌自身效果：{via}")
    if parts:
        return "；".join(parts)

    # Last resorts: raw engine payload (stage_rules_json), then the rule ids.
    try:
        from npc.stage_rules import stage_rules_json

        payload = stage_rules_json(stage)
    except Exception:
        payload = None
    if payload:
        return payload
    return "、".join(str(rule_id) for rule_id in getattr(spec, "special_rules", ()) or ())


def stage_context(stage: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(objective_types, enemy_slugs)`` used to match strategy skills."""
    try:
        from npc.spec import get_stage

        spec = get_stage(stage)
    except Exception:
        return (), ()
    objectives = tuple(objective.type for objective in spec.objectives)
    enemies = tuple(
        entry.id for entry in (*spec.lineup, *spec.reserve)
    )
    return objectives, enemies


def _one_line(text: Any, limit: int = EXISTING_DESCRIPTION_LIMIT) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _is_relevant(skill: Any, *, stage: str, objective_types: Sequence[str],
                 enemies: Sequence[str]) -> bool:
    conditions = skill.conditions
    if conditions.stages and stage not in conditions.stages:
        return False
    if conditions.objective_types and not (
        set(conditions.objective_types) & set(objective_types)
    ):
        return False
    if conditions.enemies and not (set(conditions.enemies) & set(enemies)):
        return False
    return True


def relevant_existing_skills(
    library: Any,
    *,
    stage: str,
    objective_types: Sequence[str] = (),
    enemies: Sequence[str] = (),
    limit: int = EXISTING_SKILLS_LIMIT,
) -> tuple[str, ...]:
    """Rendered ``[id] name: description`` for non-rule skills relevant to ``stage``.

    Stage-specific skills whose id ends in ``_<stage>`` always qualify; strategy
    skills qualify when their conditions match the stage/objectives/enemies.
    Rule-layer skills are excluded because the prompt already carries
    ``rules_note``. Ordering is stable (stage-specific first, then by id).
    """
    picked: list[tuple[int, Any]] = []
    for skill in library:
        if skill.layer == "rule" or skill.status != "active":
            continue
        if skill.layer == "stage_specific":
            if skill.skill_id.endswith(f"_{stage}") or stage in skill.conditions.stages:
                picked.append((0, skill))
            continue
        if _is_relevant(
            skill, stage=stage, objective_types=objective_types, enemies=enemies
        ):
            picked.append((1, skill))
    picked.sort(key=lambda item: (item[0], item[1].skill_id))
    return tuple(
        f"[{skill.skill_id}] {skill.skill_name}: {_one_line(skill.description)}"
        for _rank, skill in picked[: max(0, int(limit))]
    )


def backfill_stage_specific_stages(library: Any, stages: Sequence[str]) -> list[str]:
    """Pin ``stage_specific`` skills to the stage encoded in their id suffix.

    Older extracted libraries could leave ``conditions.stages`` empty even for
    ``layer == "stage_specific"``; the loadout planner then offered them to every
    stage. We only fill the condition when the id ends in ``_<stage>`` (longest
    stage name wins) and never guess otherwise. Returns the changed ids.
    """
    ordered = sorted({str(stage) for stage in stages}, key=lambda name: (-len(name), name))
    changed: list[str] = []
    for skill in library:
        if skill.layer != "stage_specific" or skill.conditions.stages:
            continue
        matched = next(
            (stage for stage in ordered if skill.skill_id.endswith(f"_{stage}")),
            None,
        )
        if matched is None:
            continue
        skill.conditions = replace(skill.conditions, stages=(matched,))
        changed.append(skill.skill_id)
    return sorted(changed)


def enrich_profiles(
    profiles: Sequence[Any],
    *,
    decks: dict[str, str],
    library: Any,
) -> list[Any]:
    """Attach deck facts, special rules and relevant existing skills to each profile."""
    enriched = []
    for profile in profiles:
        deck = decks.get(profile.stage, "")
        objective_types, enemies = stage_context(profile.stage)
        enriched.append(
            replace(
                profile,
                deck=deck,
                deck_facts=deck_facts_text(deck),
                special_rules=stage_special_rules_text(profile.stage),
                existing_skills=relevant_existing_skills(
                    library,
                    stage=profile.stage,
                    objective_types=objective_types,
                    enemies=enemies,
                ),
            )
        )
    return enriched


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

    base_path = Path(args.base)
    library = SkillLibrary.load(base_path) if (base_path / "library.json").exists() else SkillLibrary()
    # The fixed deck + stage rules + existing skills are what make a candidate
    # concrete; a missing config degrades that block rather than failing extraction.
    profiles = enrich_profiles(
        profiles, decks=load_stage_decks(), library=library
    )
    rules_note = library.rules_text()

    client = build_llm_client()
    if client is None:
        raise SystemExit("no LLM config; fill .env (see scripts/probe_llm.py)")

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
