#!/usr/bin/env python3
"""Turn one group's results into a candidate skill library (one evolution round).

Pipeline (the "dynamic" arm's first half):

1. read the per-game rows of a run (e.g. ``llm_empty`` or a previous library version);
2. build a per-stage failure profile (which objectives fail, how often, round stats)
   plus one close loss and one 3★ trace per stage when rows carry ``trace``;
3. attach the stage's fixed deck facts (``configs/stage_decks.json``), its special
   rules (``configs/npc/special_rules.json``) and the relevant non-rule skills from
   the base library, so candidates can name concrete characters/cards and replace
   a known-bad skill instead of repeating it. Relevance is stage-first and demotes
   strategy text that names another stage; at least two generic ``strat_*`` skills
   are kept (B5);
4. ask the model to write at most 2 injected prompts per failing stage
   (``skillloop.extract``). A candidate whose valid ``replaces`` id points at a
   skill of the same layer (and, for ``stage_specific``, the same stage) rewrites
   that skill in place (description/conditions) instead of adding a contradictory
   twin; any other id is ignored and the candidate is added;
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
import re
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

REPO = Path(__file__).resolve().parents[1]
STAGES_JSON = REPO / "configs/npc/npc_stages_mond_liyue.json"
STAGE_DECKS_JSON = REPO / "configs/stage_decks.json"

EXISTING_SKILLS_LIMIT = 8
#: B5: the old 120-char cut landed mid-clause ("…切…"), destroying the
#: trigger→action structure. 300 keeps a full rule; cutting happens only at a
#: sentence end (see :func:`_one_line`).
EXISTING_DESCRIPTION_LIMIT = 300
#: Generic cross-stage ``strat_*`` skills the prompt must still show (B5), so a
#: stage full of near-duplicate stage-named strategy text cannot crowd them out.
GENERIC_STRATEGY_RESERVE = 2
#: Descriptions sharing this prefix are the same advice (B5 synonym dedupe).
DEDUPE_PREFIX = 24
_SENTENCE_ENDS = "。；！？"
_CLAUSE_ENDS = "，、,;："
_GENERIC_STRATEGY_PREFIX = "strat_"

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


def build_stage_tasks(
    stages: Sequence[str],
    *,
    decks: dict[str, str] | None = None,
    rows: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """``stage -> TaskSpec`` built with the decision side's ``loadout.stage_task``.

    The deck is the one the run actually used: the per-stage config first
    (``configs/stage_decks.json``), then a ``deck`` field on any row of that stage.
    Stages that cannot be resolved are simply omitted, so the reflection prompt
    falls back to its legacy form for them instead of failing the whole round.
    """
    from npc.spec import get_stage
    from skillloop.loadout import stage_task

    row_decks: dict[str, str] = {}
    for row in rows or ():
        name = str(row.get("stage") or "")
        deck = row.get("deck")
        if name and deck and name not in row_decks:
            row_decks[name] = str(deck)

    tasks: dict[str, Any] = {}
    for stage_name in stages:
        name = str(stage_name)
        deck = (decks or {}).get(name) or row_decks.get(name) or ""
        try:
            tasks[name] = stage_task(get_stage(name), deck)
        except Exception:  # missing stage/deck registry: degrade, never fail extraction
            continue
    return tasks


def stage_context(stage: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(objective_types, enemy_ids_or_names)`` used to match strategy skills.

    Enemy entries carry both the engine id (``fatuipyroagent``, what new
    candidates and ``parse_skills`` write) and the Chinese display name (what
    older extracted conditions stored), so historical skills keep matching.
    """
    try:
        from npc.spec import get_stage

        spec = get_stage(stage)
    except Exception:
        return (), ()
    objectives = tuple(objective.type for objective in spec.objectives)
    enemies: list[str] = []
    for entry in (*spec.lineup, *spec.reserve):
        for value in (getattr(entry, "id", ""), getattr(entry, "name", "")):
            text = str(value or "").strip()
            if text and text not in enemies:
                enemies.append(text)
    return objectives, tuple(enemies)


def _one_line(text: Any, limit: int = EXISTING_DESCRIPTION_LIMIT) -> str:
    """Flatten to one line, truncating only at a sentence end (B5).

    A hard cut used to leave half a clause ("…切…"), which reads as a broken rule
    and invites the model to "repair" it. Within ``limit`` we cut *after* the last
    sentence end; only when the window has none do we fall back to a clause
    boundary, and only then to a hard cut. Both fallbacks still mark the cut.
    """
    flat = " ".join(str(text or "").split())
    if len(flat) <= limit:
        return flat
    window = flat[:limit]
    cut = max(window.rfind(char) for char in _SENTENCE_ENDS)
    if cut > 0:
        return window[: cut + 1]
    cut = max(window.rfind(char) for char in _CLAUSE_ENDS)
    if cut > 0:
        return window[: cut + 1] + "…"
    return window[: limit - 1] + "…"


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


def _names_other_stage(skill: Any, *, stage: str, known_stages: set[str]) -> bool:
    """True when a strategy skill names a *different* stage in id/name/body (B5).

    Extracted strategy text is often a stage rule that lost its ``stage_specific``
    label (e.g. ``skill_blanche``, ``frontload_reaction_into_kill_line_shitou``).
    It was injected into every stage's reflection prompt, wasting slots and asking
    the model to copy advice about characters this deck does not have. Such rows
    are demoted behind every generic strategy instead of being shown first.
    """
    others = {name for name in known_stages if name and name != stage}
    if not others:
        return False
    if set(str(s) for s in skill.conditions.stages) - {stage}:
        return True
    tokens = {token for token in re.split(r"[^a-z0-9]+", skill.skill_id.lower()) if token}
    if tokens & others:
        return True
    text = f"{skill.skill_name}\n{skill.description}".lower()
    return any(name in text for name in others if len(name) >= 4)


def relevant_existing_skills(
    library: Any,
    *,
    stage: str,
    objective_types: Sequence[str] = (),
    enemies: Sequence[str] = (),
    limit: int = EXISTING_SKILLS_LIMIT,
    all_stages: Sequence[str] = (),
) -> tuple[str, ...]:
    """Rendered ``[id] name: description`` for non-rule skills relevant to ``stage``.

    Ordering (B5), because the prompt asks the model to critique/replace these and
    the wrong eight entries produce critique of the wrong stage:

    1. ``stage_specific`` skills that belong to **this** stage;
    2. strategy skills whose ``conditions.objective_types`` match this stage's set
       exactly (same advice, same difficulty);
    3. remaining relevant generic strategy skills (``strat_*`` reserve below);
    4. demoted: strategy skills that name another stage (see
       :func:`_names_other_stage`) — they only appear when nothing else fills the
       slots.

    Near-duplicate advice is de-duplicated by its first :data:`DEDUPE_PREFIX`
    characters (highest rank wins), and at least
    :data:`GENERIC_STRATEGY_RESERVE` slots stay for generic ``strat_*`` skills so
    a stage never only sees stage-named text. Rule-layer skills are excluded
    (they arrive via ``rules_note``).
    """
    cap = max(0, int(limit))
    if cap == 0:
        return ()
    known_stages = {str(name) for name in all_stages if str(name)}
    for skill in library:  # older callers pass no stage list; learn what we can
        known_stages.update(str(name) for name in skill.conditions.stages if str(name))
    current_objectives = set(objective_types)

    ranked: list[tuple[int, str, Any]] = []
    for skill in library:
        if skill.layer == "rule" or skill.status != "active":
            continue
        if skill.layer == "stage_specific":
            if skill.skill_id.endswith(f"_{stage}") or stage in skill.conditions.stages:
                ranked.append((0, skill.skill_id, skill))
            continue
        if not _is_relevant(
            skill, stage=stage, objective_types=objective_types, enemies=enemies
        ):
            continue
        if _names_other_stage(skill, stage=stage, known_stages=known_stages):
            ranked.append((3, skill.skill_id, skill))
        elif (
            current_objectives
            and set(skill.conditions.objective_types) == current_objectives
        ):
            ranked.append((1, skill.skill_id, skill))
        else:
            ranked.append((2, skill.skill_id, skill))
    ranked.sort(key=lambda entry: (entry[0], entry[1]))

    deduped: list[tuple[int, str, Any]] = []
    seen_prefixes: set[str] = set()
    for entry in ranked:
        prefix = " ".join(str(entry[2].description).split())[:DEDUPE_PREFIX]
        if prefix and prefix in seen_prefixes:
            continue
        if prefix:
            seen_prefixes.add(prefix)
        deduped.append(entry)

    generic = [
        entry for entry in deduped
        if entry[1].startswith(_GENERIC_STRATEGY_PREFIX) and entry[0] < 3
    ]
    reserve = min(GENERIC_STRATEGY_RESERVE, len(generic), cap)
    chosen_ids: list[str] = []
    if reserve:
        generic_ids = {entry[1] for entry in generic}
        # Reserve the generic slots first, but only from ranks that are not
        # demoted; otherwise six other-stage rules would eat the whole budget.
        preferred = [
            entry for entry in deduped
            if entry[0] < 3 and entry[1] not in generic_ids
        ]
        chosen_ids.extend(entry[1] for entry in preferred[: cap - reserve])
        chosen_ids.extend(entry[1] for entry in generic[:reserve])
    for entry in deduped:
        if len(chosen_ids) >= cap:
            break
        if entry[1] not in chosen_ids:
            chosen_ids.append(entry[1])
    selected = set(chosen_ids)
    return tuple(
        f"[{skill.skill_id}] {skill.skill_name}: {_one_line(skill.description)}"
        for _rank, skill_id, skill in deduped
        if skill_id in selected
    )[:cap]


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


def replacement_target(skill: Any, library: Any) -> str:
    """The existing id this candidate may rewrite, or ``""`` to add it as new.

    ``parse_skills`` only checks that ``replaces`` **exists** (B6). That is not
    enough: the model may point a ``stage_specific`` candidate at a cross-stage
    ``strategy`` rule (e.g. ``strat_burst_timing``) and overwrite universal
    advice with stage-local text. A rewrite is therefore accepted only when

    * the target exists and has the same ``layer`` as the candidate, and
    * for ``stage_specific``, the target belongs to the candidate's own stage —
      ``stage in target.conditions.stages`` or the id suffix ``_<stage>``.

    Anything else is reported on stderr and the candidate is written as a new
    skill (the ``replaces`` field is cleared by the caller).
    """
    target_id = str(getattr(skill, "replaces", "") or "")
    if not target_id:
        return ""
    target = library.get(target_id)
    if target is None:
        print(
            f"[extract] {skill.skill_id}: replaces unknown skill {target_id!r}; "
            "adding as a new skill",
            file=sys.stderr,
        )
        return ""
    if target.layer != skill.layer:
        print(
            f"[extract] {skill.skill_id}: replaces {target_id!r} of layer "
            f"{target.layer!r} but candidate layer is {skill.layer!r}; "
            "keeping it as a new skill",
            file=sys.stderr,
        )
        return ""
    if skill.layer == "stage_specific":
        stages = tuple(str(name) for name in (skill.conditions.stages or ()))
        stage = stages[0] if stages else ""
        if not stage:
            print(
                f"[extract] {skill.skill_id}: cannot tell its stage, so "
                f"{target_id!r} stays unchanged; adding as a new skill",
                file=sys.stderr,
            )
            return ""
        target_stages = {str(name) for name in target.conditions.stages}
        if stage not in target_stages and not target_id.endswith(f"_{stage}"):
            print(
                f"[extract] {skill.skill_id}: replaces {target_id!r}, which is not "
                f"a {stage!r} skill; keeping it as a new skill",
                file=sys.stderr,
            )
            return ""
    return target_id


def enrich_profiles(
    profiles: Sequence[Any],
    *,
    decks: dict[str, str],
    library: Any,
    all_stages: Sequence[str] = (),
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
                    all_stages=all_stages,
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
    ap.add_argument(
        "--max-tokens", type=int, default=24576,
        help="completion budget; hidden reasoning and the JSON body share it "
             "(default 24576; one truncation retry doubles it, capped at 49152)",
    )
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
    objectives_by_stage = stage_objectives()
    profiles = failure_profiles(
        rows,
        objectives_by_stage=objectives_by_stage,
        round_caps_by_stage=stage_round_caps(),
        max_replays=args.max_replays,
    )

    base_path = Path(args.base)
    library = SkillLibrary.load(base_path) if (base_path / "library.json").exists() else SkillLibrary()
    # The fixed deck + stage rules + existing skills are what make a candidate
    # concrete; a missing config degrades that block rather than failing extraction.
    decks = load_stage_decks()
    profiles = enrich_profiles(
        profiles,
        decks=decks,
        library=library,
        # Known stage names let the selector demote strategy text that names
        # another stage (B5) instead of spending a slot on it.
        all_stages=tuple(objectives_by_stage),
    )
    rules_note = library.rules_text()
    # Same TaskSpec / special-rules text the decision prompt uses, so the
    # reflection system message and the in-game system message share one source.
    tasks = build_stage_tasks(
        [profile.stage for profile in profiles], decks=decks, rows=rows
    )
    special_rules_by_stage = {
        profile.stage: profile.special_rules for profile in profiles
        if profile.special_rules
    }

    client = build_llm_client()
    if client is None:
        raise SystemExit("no LLM config; fill .env (see scripts/probe_llm.py)")

    candidates = propose_candidates(
        client, profiles, model=args.model, rules_note=rules_note,
        min_failure_rate=args.min_failure_rate,
        effort=args.effort, max_tokens=args.max_tokens,
        tasks=tasks, special_rules_by_stage=special_rules_by_stage,
        # ``replaces`` is validated against the library the candidates are
        # written into, so a hallucinated id can never rewrite the wrong skill.
        existing_ids=tuple(library.skills),
    )
    print(f"[extract] {len(profiles)} stage profiles -> {len(candidates)} candidates "
          f"({client.usage.calls} calls, {getattr(client.usage, 'errors', 0)} errors, "
          f"${client.usage.cost_usd:.4f})")
    for skill in candidates:
        print(f"  - {skill.skill_id:40s} [{skill.layer}] {skill.skill_name}")
        print(f"      {skill.description[:100]}")

    if args.dry_run or not candidates:
        print("[extract] dry-run / nothing to add")
        return 0

    for skill in candidates:
        skill.status = "active"          # active only in this new version; the gate decides
        # B6/B7: only a same-layer (and, for stage_specific, same-stage) target
        # may be rewritten in place; anything else becomes a new skill.
        target = replacement_target(skill, library)
        skill.replaces = target
        if target:
            # B6: the model declared this rule a replacement. Rewrite that skill
            # in place so the id (and its gate history) survives, instead of
            # adding a contradictory twin next to the advice it contradicts.
            library.update(target, description=skill.description,
                           conditions=skill.conditions)
            print(f"[extract] replaced {target} (candidate {skill.skill_id})")
        elif skill.skill_id in library.skills:
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
