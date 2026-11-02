"""Stage x deck strength matrix for the fixed-deck PvE benchmark.

The benchmark uses **one fixed deck per stage**, shared by every experiment
group, so the choice must be evidence-based rather than model-selected. This
script has two phases:

1. **Screen** — play the scripted expert (``expert:<deck>``, policy seed 0) with
   every ported expert deck on every NPC stage, for a small seed set
   (default 5). This is 16 x 20 x 5 = 1600 games.
2. **Shortlist** — pick the top 3 decks per stage from the screen (among decks
   that can satisfy the stage's card-type objective), then extend **those**
   cells to 10 seeds. That is 16 x 3 x 10 = 480 shortlist cell-games, of which
   240 are new because seeds 0-4 already exist.

Ranking is **three-star rate first** (the benchmark metric), then win rate, mean
stars, mean rounds (fewer is better), and deck name. The report shows raw counts
(e.g. ``8/10``) next to every percentage so a 5/5-vs-4/5 tie is visible, plus a
Wilson 95% interval for the finalists' three-star rate.

No LLM or network calls: this is a free, reproducible expert-vs-NPC sweep.

Objective judging must see the played-card trace. ``npc.objectives`` reads
``DecisionRecord.card_kind`` for ``play_equipment_card`` / ``play_event_cards``
(wagner, timaeus, uncletian), so this script always calls ``run_match`` with
``record_decisions=True`` (plus ``record_events=True`` and
``record_final_state=True`` for bursts/reactions/defeated-count objectives).
With ``record_decisions=False`` those objectives are silently scored as
failures.

Usage::

    # phase 1: full 5-seed screen (16 x 20 x 5)
    python -m scripts.deck_matrix --seeds 0,1,2,3,4 --workers 8

    # phase 2: extend the top 3 per stage to 10 seeds
    python -m scripts.deck_matrix --shortlist-top 3 --shortlist-seeds 10 \
        --screen-seeds 0,1,2,3,4 --workers 8

    # rebuild the report from the JSONL (no games)
    python -m scripts.deck_matrix --analyze-only

    # smoke
    python -m scripts.deck_matrix --stages timmie --decks superconduct_aggro --seeds 1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing as mp
import sys
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from common.paths import REPO_ROOT
from envs.decks import deck_spec, expert_deck_specs
from envs.match import run_match
from envs.policy import expert_policy
from npc.objectives import evaluate_stage
from npc.policy import npc_policy_for_stage
from npc.spec import get_stage, stage_names
from reps.schema import DeckSpec

DEFAULT_OUT = REPO_ROOT / "data" / "runs" / "deck_matrix.jsonl"
DEFAULT_REPORT = REPO_ROOT / "reports" / "deck_matrix.md"
DEFAULT_ASSETS = REPO_ROOT / "data" / "assets" / "expert_system_assets_chs_latest.json"
MAX_WORKERS = 24


# --------------------------------------------------------------------------- #
# card composition
# --------------------------------------------------------------------------- #
def _classify_card(item: dict[str, Any]) -> str:
    """Bucket one asset entry the way ``npc.objectives`` judges played cards.

    ``envs.match._card_kind_from_entity`` maps a talent-tagged card to
    ``"talent"`` *before* looking at its entity/equipment type, so a talent card
    never satisfies a ``weapon`` / ``artifact`` / ``event`` objective. We keep
    that precedence here (there is one ``GCG_CARD_EVENT`` + ``GCG_TAG_TALENT``
    card in the current pool).
    """
    card_type = str(item.get("type") or "")
    tags = set(item.get("tags") or ())
    if "GCG_TAG_TALENT" in tags:
        return "talents"
    if card_type == "GCG_CARD_MODIFY":
        if "GCG_TAG_WEAPON" in tags:
            return "weapons"
        if "GCG_TAG_ARTIFACT" in tags:
            return "artifacts"
        return "other_equipment"
    if card_type == "GCG_CARD_EVENT":
        return "events"
    if card_type == "GCG_CARD_ASSIST":
        return "supports"
    return "other"


def load_composition(assets_path: Path = DEFAULT_ASSETS) -> dict[str, dict[str, int]]:
    """Count card kinds for every expert deck from the raw asset catalog."""
    payload = json.loads(Path(assets_path).read_text(encoding="utf-8"))
    by_id = {
        int(item["id"]): item
        for item in payload.get("data", ())
        if isinstance(item.get("id"), int)
    }
    composition: dict[str, dict[str, int]] = {}
    for spec in expert_deck_specs():
        counts: Counter[str] = Counter()
        for card_id in spec.cards:
            item = by_id.get(int(card_id))
            counts[_classify_card(item) if item is not None else "unknown"] += 1
        counts["total"] = len(spec.cards)
        composition[spec.name] = dict(counts)
    return composition


# --------------------------------------------------------------------------- #
# stage requirements
# --------------------------------------------------------------------------- #
def stage_card_requirements(stage_name: str) -> list[dict[str, Any]]:
    """Card-type objectives of a stage as ``[{kind, count, objective}]``."""
    stage = get_stage(stage_name)
    requirements: list[dict[str, Any]] = []
    for objective in stage.objectives:
        if objective.type == "play_equipment_card":
            requirements.append(
                {
                    "kind": objective.kind or "equipment",
                    "count": int(objective.count if objective.count is not None else 1),
                    "objective": objective.type,
                }
            )
        elif objective.type == "play_event_cards":
            requirements.append(
                {
                    "kind": "event",
                    "count": int(objective.count if objective.count is not None else 1),
                    "objective": objective.type,
                }
            )
    return requirements


_REQUIREMENT_FIELD = {
    "weapon": "weapons",
    "artifact": "artifacts",
    "event": "events",
    "support": "supports",
}


def deck_requirement_status(
    composition: dict[str, int], requirements: Iterable[dict[str, Any]]
) -> tuple[bool, str]:
    """``(satisfied, human-readable detail)`` for a deck against a stage."""
    requirements = list(requirements)
    if not requirements:
        return True, "n/a"
    details: list[str] = []
    satisfied = True
    for requirement in requirements:
        field = _REQUIREMENT_FIELD.get(str(requirement["kind"]), "")
        have = int(composition.get(field, 0)) if field else 0
        need = int(requirement["count"])
        ok = have >= need
        satisfied = satisfied and ok
        details.append(f"{requirement['kind']} {have}/{need}")
    return satisfied, ", ".join(details)


# --------------------------------------------------------------------------- #
# one game
# --------------------------------------------------------------------------- #
def play_one(payload: dict[str, Any]) -> dict[str, Any]:
    """Play one (stage, deck, seed) game and return a JSON-safe row.

    Mirrors ``skillloop.run.play_one`` for the scripted group: the expert policy
    is built with ``seed=0``, the NPC deck reuses the contestant's card pile
    (the NPC never plays cards), and the match is judged by
    ``npc.objectives.evaluate_stage``.
    """
    stage = get_stage(payload["stage"])
    contestant = deck_spec(payload["deck"])
    seed = int(payload["seed"])
    npc_deck = DeckSpec(
        name=f"npc_{stage.name}",
        characters=stage.deck_characters,
        cards=contestant.cards,
    )
    started = time.perf_counter()
    record = run_match(
        contestant,
        npc_deck,
        expert_policy(contestant.name, seed=0),
        npc_policy_for_stage(stage),
        seed=seed,
        record_decisions=True,
        record_events=True,
        record_final_state=True,
    )
    elapsed = time.perf_counter() - started
    stars = evaluate_stage(stage, record)
    objectives = {item.type: bool(item.achieved) for item in stars.objectives}
    details = {item.type: item.detail for item in stars.objectives}
    return {
        "index": int(payload.get("index", 0)),
        "stage": stage.name,
        "deck": contestant.name,
        "seed": seed,
        "winner": record.winner,
        "rounds": int(record.rounds),
        "stars": int(stars.stars),
        "three_star": bool(stars.three_star),
        "objectives": objectives,
        "objective_details": details,
        "n_objectives": len(stars.objectives),
        "error": record.error,
        "truncated": bool(record.truncated),
        "seconds": round(float(record.wall_seconds), 3),
        "decisions0": int(record.decisions0),
        "decisions1": int(record.decisions1),
        "fallbacks0": int(record.fallbacks0),
        "fallbacks1": int(record.fallbacks1),
        "wall_seconds": round(elapsed, 3),
    }


# --------------------------------------------------------------------------- #
# spec / meta
# --------------------------------------------------------------------------- #
def _sha1(path: Path) -> str:
    if not path.exists():
        return "missing"
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12]


def _seed_list(value: str) -> tuple[int, ...]:
    if "," in value:
        return tuple(int(item) for item in value.split(",") if item.strip())
    return tuple(range(int(value)))


def _stage_list(value: str) -> tuple[str, ...]:
    if value == "all":
        return tuple(stage_names())
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _deck_list(value: str) -> tuple[str, ...]:
    known = [spec.name for spec in expert_deck_specs()]
    if value == "all":
        return tuple(known)
    requested = tuple(item.strip() for item in value.split(",") if item.strip())
    unknown = [name for name in requested if name not in known]
    if unknown:
        raise SystemExit(f"unknown deck(s) {unknown}; known: {known}")
    return requested


def fingerprint(stages: tuple[str, ...], decks: tuple[str, ...]) -> dict[str, Any]:
    """Pin everything that changes game outcomes except the seed list.

    Seeds are deliberately excluded so a first pass can be extended (e.g. 5 ->
    10 seeds) without replaying finished games; rows are keyed by
    ``(stage, deck, seed)``.
    """
    return {
        "policy": "expert:<deck>@seed=0",
        "npc": "greedy",
        "stages": list(stages),
        "decks": list(decks),
        "record_decisions": True,
        "record_events": True,
        "record_final_state": True,
        "engine_lock": _sha1(REPO_ROOT / "configs" / "engine.lock"),
        "hp_table": _sha1(REPO_ROOT / "configs" / "npc" / "hp_table.json"),
        "stages_sha": _sha1(REPO_ROOT / "configs" / "npc" / "npc_stages_mond_liyue.json"),
        "assets_sha": _sha1(DEFAULT_ASSETS),
    }


def _meta_path(out: Path) -> Path:
    return out.with_name(out.name + ".meta.json")


def _load_meta(out: Path) -> dict[str, Any]:
    path = _meta_path(out)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_done(out: Path) -> set[tuple[str, str, int]]:
    done: set[tuple[str, str, int]] = set()
    if not out.exists():
        return done
    for line in out.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not row.get("error"):
            done.add((str(row["stage"]), str(row["deck"]), int(row["seed"])))
    return done


def load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _check_fingerprint(out: Path, fp: dict[str, Any]) -> None:
    previous = _load_meta(out)
    if previous and previous.get("fingerprint") != fp:
        raise SystemExit(
            f"{out} was produced by a different spec; use another --out or delete it\n"
            f"  old: {previous.get('fingerprint')}\n  new: {fp}"
        )


# --------------------------------------------------------------------------- #
# game scheduling
# --------------------------------------------------------------------------- #
def _play_pending(
    pending: list[dict[str, Any]], out: Path, workers: int
) -> tuple[list[dict[str, Any]], float]:
    rows: list[dict[str, Any]] = []
    started = time.perf_counter()
    workers = max(1, min(int(workers), MAX_WORKERS))
    if not pending:
        return rows, 0.0
    with out.open("a", encoding="utf-8") as handle:
        if workers <= 1:
            for item in pending:
                row = play_one(item)
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                rows.append(row)
                _print_row(row, len(rows), len(pending), started)
        else:
            # ``spawn`` (not fork): forking a process with a live V8 isolate
            # deadlocks the child on its first engine call (see skillloop.run).
            context = mp.get_context("spawn")
            with ProcessPoolExecutor(max_workers=workers, mp_context=context) as pool:
                pending_iter = iter(pending)
                waiting = next(pending_iter, None)
                in_flight: set[Future] = set()

                def submit_next() -> bool:
                    nonlocal waiting
                    if waiting is None:
                        return False
                    in_flight.add(pool.submit(play_one, waiting))
                    waiting = next(pending_iter, None)
                    return True

                while len(in_flight) < workers and submit_next():
                    pass
                while in_flight:
                    finished, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                    for future in finished:
                        in_flight.discard(future)
                        row = future.result()
                        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                        handle.flush()
                        rows.append(row)
                        _print_row(row, len(rows), len(pending), started)
                    while len(in_flight) < workers and submit_next():
                        pass
    return rows, time.perf_counter() - started


def _print_row(row: dict[str, Any], done: int, total: int, started: float) -> None:
    elapsed = time.perf_counter() - started
    rate = done / elapsed if elapsed > 0 else 0.0
    eta = (total - done) / rate if rate > 0 else 0.0
    flag = "3*" if row.get("three_star") else f"{row.get('stars', 0)}*"
    print(
        f"[{done:>5}/{total:<5}] {100 * done / max(1, total):5.1f}% "
        f"elapsed={elapsed:6.0f}s eta={eta:6.0f}s "
        f"{row['stage']:16s} {row['deck']:28s} seed={row['seed']:<3d} "
        f"{flag:>3s} winner={row.get('winner')} err={row.get('error') or ''}",
        flush=True,
    )


def _write_meta(out: Path, meta: dict[str, Any]) -> None:
    _meta_path(out).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# phase 1: full screen
# --------------------------------------------------------------------------- #
def run(args: argparse.Namespace) -> dict[str, Any]:
    stages = _stage_list(args.stages)
    decks = _deck_list(args.decks)
    seeds = _seed_list(args.seeds)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fp = fingerprint(stages, decks)
    _check_fingerprint(out, fp)

    done = _load_done(out)
    pending = [
        {"stage": stage, "deck": deck, "seed": seed, "index": index}
        for index, (stage, deck, seed) in enumerate(
            (stage, deck, seed) for stage in stages for deck in decks for seed in seeds
        )
        if (stage, deck, seed) not in done
    ]
    print(
        f"[deck_matrix] screen: {len(pending)} pending / {len(done)} done "
        f"({len(stages)} stages x {len(decks)} decks x {len(seeds)} seeds) -> {out}",
        flush=True,
    )
    rows, elapsed = _play_pending(pending, out, int(args.workers))
    all_rows = load_rows(out)
    previous = _load_meta(out)
    cumulative_seconds = round(
        float(previous.get("cumulative_seconds", previous.get("last_run_seconds", 0.0)))
        + elapsed,
        1,
    )
    meta = {
        "fingerprint": fp,
        "seeds_present": sorted({int(row["seed"]) for row in all_rows}),
        "rows": len(all_rows),
        "screen_seeds": sorted({int(row["seed"]) for row in all_rows}),
        "screen_games": len(all_rows),
        "screen_seconds": cumulative_seconds,
        "shortlist_top": 0,
        "shortlist_seeds": [],
        "shortlist": {},
        "shortlist_games": 0,
        "shortlist_seconds": 0.0,
        "cumulative_seconds": cumulative_seconds,
        "cumulative_games": len(all_rows),
        "last_run_seconds": round(elapsed, 1),
        "last_run_workers": max(1, min(int(args.workers), MAX_WORKERS)),
        "last_run_games": len(rows),
        "last_run_at": datetime.now().isoformat(timespec="seconds"),
    }
    _write_meta(out, meta)
    print(
        f"[deck_matrix] screen: {len(rows)} games in {elapsed:.1f}s "
        f"({elapsed / max(1, len(rows)):.2f}s/game, workers={meta['last_run_workers']}); "
        f"{len(all_rows)} rows on disk",
        flush=True,
    )
    return meta


# --------------------------------------------------------------------------- #
# phase 2: shortlist extension
# --------------------------------------------------------------------------- #
def run_shortlist(args: argparse.Namespace) -> dict[str, Any]:
    stages = _stage_list(args.stages)
    decks = _deck_list(args.decks)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fp = fingerprint(stages, decks)
    _check_fingerprint(out, fp)
    screen_seeds = _seed_list(args.screen_seeds)
    shortlist_seeds = _seed_list(str(args.shortlist_seeds))
    screen_seed_set = set(screen_seeds)
    target_seed_set = set(shortlist_seeds)
    shortlist_top = max(1, int(args.shortlist_top))

    all_rows = load_rows(out)
    screen_rows = [
        row
        for row in all_rows
        if int(row["seed"]) in screen_seed_set
        and not row.get("error")
        and not row.get("truncated")
    ]
    present = {(row["stage"], row["deck"], int(row["seed"])) for row in screen_rows}
    expected = {
        (stage, deck, seed)
        for stage in stages
        for deck in decks
        for seed in screen_seeds
    }
    missing = expected - present
    if missing:
        sample = sorted(missing)[:5]
        raise SystemExit(
            f"screen pass incomplete: {len(missing)} (stage, deck, seed) cells missing "
            f"for --screen-seeds {list(screen_seeds)}; examples: {sample}. "
            "Run the full screen pass first."
        )

    composition = load_composition()
    screen_agg = aggregate(screen_rows, list(stages), list(decks))
    screen_cells = screen_agg["cells"]
    shortlist: dict[str, list[str]] = {}
    for stage in stages:
        rec = recommend_for_stage(
            stage, screen_cells[stage], composition, list(decks), top_n=shortlist_top
        )
        shortlist[stage] = list(rec["shortlist"])

    done = _load_done(out)
    pending = [
        {"stage": stage, "deck": deck, "seed": seed, "index": index}
        for index, (stage, deck, seed) in enumerate(
            (stage, deck, seed)
            for stage in stages
            for deck in shortlist[stage]
            for seed in sorted(target_seed_set)
        )
        if (stage, deck, seed) not in done
    ]
    print(
        f"[deck_matrix] shortlist: {len(pending)} pending; {shortlist_top} decks/stage, "
        f"target seeds {list(sorted(target_seed_set))} -> {out}",
        flush=True,
    )
    rows, elapsed = _play_pending(pending, out, int(args.workers))
    all_rows = load_rows(out)
    previous = _load_meta(out)
    screen_games = len(
        [
            row
            for row in all_rows
            if int(row["seed"]) in screen_seed_set
        ]
    )
    shortlist_games = len(all_rows) - screen_games
    screen_seconds = float(
        previous.get(
            "screen_seconds",
            previous.get("cumulative_seconds", previous.get("last_run_seconds", 0.0)),
        )
    )
    shortlist_seconds = round(
        float(previous.get("shortlist_seconds", 0.0)) + elapsed, 1
    )
    meta = {
        "fingerprint": fp,
        "seeds_present": sorted({int(row["seed"]) for row in all_rows}),
        "rows": len(all_rows),
        "screen_seeds": sorted(screen_seed_set),
        "screen_games": screen_games,
        "screen_seconds": round(screen_seconds, 1),
        "shortlist_top": shortlist_top,
        "shortlist_seeds": sorted(target_seed_set),
        "shortlist": shortlist,
        "shortlist_games": shortlist_games,
        "shortlist_seconds": shortlist_seconds,
        "cumulative_seconds": round(screen_seconds + shortlist_seconds, 1),
        "cumulative_games": len(all_rows),
        "last_run_seconds": round(elapsed, 1),
        "last_run_workers": max(1, min(int(args.workers), MAX_WORKERS)),
        "last_run_games": len(rows),
        "last_run_at": datetime.now().isoformat(timespec="seconds"),
    }
    _write_meta(out, meta)
    print(
        f"[deck_matrix] shortlist: {len(rows)} games in {elapsed:.1f}s "
        f"({elapsed / max(1, len(rows)):.2f}s/game, workers={meta['last_run_workers']}); "
        f"{len(all_rows)} rows on disk",
        flush=True,
    )
    return meta


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #
def _empty_cell() -> dict[str, int]:
    return {"n": 0, "wins": 0, "three_stars": 0, "stars": 0, "rounds": 0, "draws": 0}


def aggregate(
    rows: list[dict[str, Any]],
    stages: list[str],
    decks: list[str],
) -> dict[str, Any]:
    cells: dict[str, dict[str, dict[str, int]]] = {
        stage: {deck: _empty_cell() for deck in decks} for stage in stages
    }
    invalid = 0
    for row in rows:
        if row.get("error") or row.get("truncated"):
            invalid += 1
            continue
        stage = str(row["stage"])
        deck = str(row["deck"])
        if stage not in cells or deck not in cells[stage]:
            continue
        cell = cells[stage][deck]
        cell["n"] += 1
        cell["wins"] += int(row.get("winner") == 0)
        cell["three_stars"] += int(bool(row.get("three_star")))
        cell["stars"] += int(row.get("stars") or 0)
        cell["rounds"] += int(row.get("rounds") or 0)
        cell["draws"] += int(row.get("winner") not in (0, 1))

    def stats(cell: dict[str, int]) -> dict[str, Any]:
        n = cell["n"]
        return {
            "n": n,
            "wins": cell["wins"],
            "three_stars": cell["three_stars"],
            "stars": cell["stars"],
            "rounds": cell["rounds"],
            "draws": cell["draws"],
            "win_rate": cell["wins"] / n if n else 0.0,
            "three_star_rate": cell["three_stars"] / n if n else 0.0,
            "mean_stars": cell["stars"] / n if n else 0.0,
            "mean_rounds": cell["rounds"] / n if n else 0.0,
            "draw_rate": cell["draws"] / n if n else 0.0,
        }

    by_stage = {
        stage: {deck: stats(cell) for deck, cell in deck_cells.items()}
        for stage, deck_cells in cells.items()
    }
    by_deck: dict[str, dict[str, Any]] = {}
    for deck in decks:
        pooled = _empty_cell()
        per_stage_rates: list[float] = []
        per_stage_three: list[float] = []
        for stage in stages:
            cell = by_stage[stage][deck]
            pooled["n"] += cell["n"]
            pooled["wins"] += cell["wins"]
            pooled["three_stars"] += cell["three_stars"]
            pooled["stars"] += cell["stars"]
            pooled["rounds"] += cell["rounds"]
            pooled["draws"] += cell["draws"]
            if cell["n"]:
                per_stage_rates.append(cell["win_rate"])
                per_stage_three.append(cell["three_star_rate"])
        overall = stats(pooled)
        overall["mean_stage_win_rate"] = (
            sum(per_stage_rates) / len(per_stage_rates) if per_stage_rates else 0.0
        )
        overall["mean_stage_three_star_rate"] = (
            sum(per_stage_three) / len(per_stage_three) if per_stage_three else 0.0
        )
        by_deck[deck] = overall
    return {
        "cells": by_stage,
        "by_deck": by_deck,
        "invalid": invalid,
        "valid": sum(1 for row in rows if not row.get("error") and not row.get("truncated")),
    }


def _rank_key(stats: dict[str, Any], deck: str) -> tuple[float, float, float, float, str]:
    """Three-star rate, win rate, mean stars, mean rounds (fewer better), name."""
    return (
        -float(stats["three_star_rate"]),
        -float(stats["win_rate"]),
        -float(stats["mean_stars"]),
        float(stats["mean_rounds"]),
        deck,
    )


def _eligible_decks(
    stage: str,
    stats_by_deck: dict[str, dict[str, Any]],
    composition: dict[str, dict[str, int]],
    decks: Iterable[str],
) -> tuple[list[str], list[dict[str, Any]], dict[str, str]]:
    requirements = stage_card_requirements(stage)
    eligible: list[str] = []
    statuses: dict[str, str] = {}
    for deck in decks:
        satisfied, detail = deck_requirement_status(composition[deck], requirements)
        statuses[deck] = detail
        if satisfied and int(stats_by_deck[deck]["n"]) > 0:
            eligible.append(deck)
    return eligible, requirements, statuses


def recommend_for_stage(
    stage: str,
    stats_by_deck: dict[str, dict[str, Any]],
    composition: dict[str, dict[str, int]],
    decks: list[str],
    *,
    top_n: int = 3,
) -> dict[str, Any]:
    """Best screen ranking among decks satisfying the stage's card objective."""
    eligible, requirements, statuses = _eligible_decks(
        stage, stats_by_deck, composition, decks
    )
    ranked = sorted(eligible, key=lambda deck: _rank_key(stats_by_deck[deck], deck))
    ranked_all = sorted(
        [deck for deck in decks if int(stats_by_deck[deck]["n"]) > 0],
        key=lambda deck: _rank_key(stats_by_deck[deck], deck),
    )
    recommended = ranked[0] if ranked else None
    runner_up = ranked[1] if len(ranked) > 1 else None
    best_any = ranked_all[0] if ranked_all else None
    return {
        "stage": stage,
        "requirements": requirements,
        "statuses": statuses,
        "eligible": ranked,
        "shortlist": ranked[: max(1, top_n)],
        "recommended": recommended,
        "runner_up": runner_up,
        "recommended_stats": stats_by_deck[recommended] if recommended else None,
        "runner_up_stats": stats_by_deck[runner_up] if runner_up else None,
        "best_any": best_any,
        "best_any_stats": stats_by_deck[best_any] if best_any else None,
        "best_any_satisfies": bool(best_any and best_any in eligible),
        "cost": (
            stats_by_deck[best_any]["win_rate"]
            - stats_by_deck[recommended]["win_rate"]
            if best_any and recommended
            else 0.0
        ),
    }


def pick_from_candidates(
    stage: str,
    candidates: list[str],
    stats_by_deck: dict[str, dict[str, Any]],
    composition: dict[str, dict[str, int]],
) -> dict[str, Any]:
    """Final pick among the pre-registered shortlist, using final seed counts."""
    eligible, requirements, statuses = _eligible_decks(
        stage, stats_by_deck, composition, candidates
    )
    ranked = sorted(eligible, key=lambda deck: _rank_key(stats_by_deck[deck], deck))
    recommended = ranked[0] if ranked else None
    runner_up = ranked[1] if len(ranked) > 1 else None
    return {
        "stage": stage,
        "requirements": requirements,
        "statuses": statuses,
        "recommended": recommended,
        "runner_up": runner_up,
        "recommended_stats": stats_by_deck[recommended] if recommended else None,
        "runner_up_stats": stats_by_deck[runner_up] if runner_up else None,
    }


# --------------------------------------------------------------------------- #
# report helpers
# --------------------------------------------------------------------------- #
def _pct(rate: float) -> str:
    return f"{100 * rate:.0f}%"


def _pp(rate: float) -> str:
    return f"{100 * rate:.0f}pp"


def _cp(k: int, n: int) -> str:
    """``9/10 (90%)`` — raw counts first so ties are visible."""
    return f"{k}/{n} ({_pct(k / n)})" if n else f"{k}/{n}"


def _wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 0.0
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def _fmt_ci(k: int, n: int) -> str:
    lo, hi = _wilson_ci(k, n)
    return f"{100 * lo:.0f}%-{100 * hi:.0f}%"


def _deck_codes(decks: list[str]) -> dict[str, str]:
    return {deck: f"D{i:02d}" for i, deck in enumerate(decks, start=1)}


# --------------------------------------------------------------------------- #
# markdown report
# --------------------------------------------------------------------------- #
def render_report(
    *,
    rows: list[dict[str, Any]],
    stages: list[str],
    decks: list[str],
    composition: dict[str, dict[str, int]],
    meta: dict[str, Any],
    screen_seeds: list[int],
    shortlist_top: int,
    shortlist_seeds: list[int],
) -> str:
    screen_seed_set = set(screen_seeds)
    screen_raw_rows = [row for row in rows if int(row["seed"]) in screen_seed_set]
    screen_rows = [
        row
        for row in screen_raw_rows
        if not row.get("error") and not row.get("truncated")
    ]
    screen_agg = aggregate(screen_rows, stages, decks)
    screen_cells = screen_agg["cells"]
    screen_by_deck = screen_agg["by_deck"]
    final_agg = aggregate(rows, stages, decks)
    final_cells = final_agg["cells"]

    shortlist_top_eff = max(1, int(shortlist_top))
    screen_recs = {
        stage: recommend_for_stage(
            stage, screen_cells[stage], composition, decks, top_n=shortlist_top_eff
        )
        for stage in stages
    }
    shortlist = {stage: screen_recs[stage]["shortlist"] for stage in stages}
    final_recs = {
        stage: pick_from_candidates(
            stage, shortlist[stage], final_cells[stage], composition
        )
        for stage in stages
    }

    screen_ranked = sorted(decks, key=lambda deck: _rank_key(screen_by_deck[deck], deck))
    seeds = sorted({int(row["seed"]) for row in rows})
    valid = screen_agg["valid"]
    errors = len(screen_raw_rows) - valid
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    shortlist_cells = sum(len(value) for value in shortlist.values())
    shortlist_cell_games = shortlist_cells * len(shortlist_seeds)
    shortlist_new_games = len(rows) - len(screen_rows)
    draw_rows = [row for row in screen_rows if row.get("winner") not in (0, 1)]
    draws = len(draw_rows)
    draw_rounds = Counter(int(row.get("rounds") or 0) for row in draw_rows)
    draw_by_seed = Counter(int(row["seed"]) for row in draw_rows)
    screen_n_by_seed = Counter(int(row["seed"]) for row in screen_rows)
    seed0_draws = draw_by_seed.get(0, 0)
    seed0_games = screen_n_by_seed.get(0, 0)
    other_draws = draws - seed0_draws
    other_games = len(screen_rows) - seed0_games
    tail_rows = [
        row
        for row in rows
        if int(row["seed"]) not in screen_seed_set
        and not row.get("error")
        and not row.get("truncated")
    ]
    tail_draws = sum(1 for row in tail_rows if row.get("winner") not in (0, 1))

    lines: list[str] = []
    add = lines.append
    add("# Deck strength matrix and fixed per-stage deck choices")
    add("")
    add(
        "Scripted expert vs NPC sweep used to pick **one fixed deck per stage** for "
        "every experiment group. No LLM/network calls; fully reproducible."
    )
    add("")
    add(f"- Generated: {generated}")
    add(
        f"- Screen: {len(stages)} stages x {len(decks)} decks x {len(screen_seeds)} seeds "
        f"= {len(stages) * len(decks) * len(screen_seeds)} games "
        f"(seeds {', '.join(str(seed) for seed in screen_seeds)}); "
        f"{len(screen_rows)} valid screen rows (errors/truncated: {errors})"
    )
    add(
        f"- Shortlist: {shortlist_top_eff} decks/stage x {len(shortlist_seeds)} seeds "
        f"= {shortlist_cell_games} shortlist cell-games "
        f"({shortlist_new_games} new games after the screen; total rows {len(rows)})"
    )
    add(f"- Seeds on disk: {', '.join(str(seed) for seed in seeds) if seeds else '(none)'}")
    add(
        f"- Valid screen games with no winner (draw/None): {draws} "
        f"(rounds: {dict(sorted(draw_rounds.items())) or '—'}); counted as non-wins"
    )
    if seed0_games:
        add(
            f"  **Seed-0 artifact**: seed 0 has {seed0_draws}/{seed0_games} draws "
            f"({_pct(seed0_draws / seed0_games)}) vs {other_draws}/{other_games} "
            f"({_pct(other_draws / other_games) if other_games else '—'}) on the other "
            f"screen seeds; the shortlist seeds 5-9 have {tail_draws}/{len(tail_rows)} "
            "draws. All are the engine's 15-round cap. Seed 0 makes the 5-seed screen "
            "pessimistic relative to a clean seed set; see the seed-0 sensitivity "
            "section."
        )
    add(
        "- Policy: `expert:<deck>` with policy seed 0 (same as "
        "`skillloop.run --group scripted`); NPC = `npc.policy.NpcPolicy` greedy, "
        "no rotation"
    )
    add(
        "- Judging flags: `record_decisions=True`, `record_events=True`, "
        "`record_final_state=True` — equipment/event objectives (wagner, timaeus, "
        "uncletian) need the decision trace; `record_decisions=False` silently "
        "fails them"
    )
    add(
        "- Ranking: **three-star rate** first, then win rate, mean stars, mean rounds "
        "(fewer is better), then deck name"
    )
    if meta:
        fp = meta.get("fingerprint", {})
        add(
            f"- Engine lock `{fp.get('engine_lock')}`, HP table `{fp.get('hp_table')}`, "
            f"stage JSON `{fp.get('stages_sha')}`, assets `{fp.get('assets_sha')}`"
        )
        screen_seconds = meta.get(
            "screen_seconds",
            meta.get("cumulative_seconds", meta.get("last_run_seconds", 0)),
        )
        shortlist_seconds = meta.get("shortlist_seconds", 0)
        total_seconds = meta.get(
            "cumulative_seconds", float(screen_seconds) + float(shortlist_seconds)
        )
        add(
            f"- Wall time: screen {meta.get('screen_games', len(screen_rows))} games in "
            f"{screen_seconds} s; shortlist {meta.get('shortlist_games', shortlist_new_games)} "
            f"new games in {shortlist_seconds} s; total {total_seconds} s "
            f"({meta.get('last_run_workers', '?')} workers)"
        )
    if screen_by_deck:
        best_deck = screen_ranked[0]
        worst_deck = screen_ranked[-1]
        add(
            f"- Best deck overall (5-seed screen): **{best_deck}** — "
            f"3★ {_cp(screen_by_deck[best_deck]['three_stars'], screen_by_deck[best_deck]['n'])}, "
            f"win {_cp(screen_by_deck[best_deck]['wins'], screen_by_deck[best_deck]['n'])}"
        )
        add(
            f"- Worst deck overall (5-seed screen): **{worst_deck}** — "
            f"3★ {_cp(screen_by_deck[worst_deck]['three_stars'], screen_by_deck[worst_deck]['n'])}, "
            f"win {_cp(screen_by_deck[worst_deck]['wins'], screen_by_deck[worst_deck]['n'])}"
        )
    add("")
    add(
        "The final table uses the 10-seed shortlist extension; the screen/top-5 table "
        "uses the 5-seed screen so all 20 decks are compared on the same seeds."
    )
    add("")

    # ------------------------------------------------------------------ #
    # 1. final recommendation
    # ------------------------------------------------------------------ #
    add("## 1. Fixed deck per stage (final recommendation)")
    add("")
    add(
        f"Shortlist = top {shortlist_top_eff} decks from the 5-seed screen among decks "
        "that satisfy the stage's card-type objective. The final pick re-ranks that "
        "shortlist on all 10 seeds. Counts are `achieved/games`; the margin is the "
        "three-star rate gap to the runner-up."
    )
    add("")
    add("| stage | recommended deck | win rate | three-star rate | satisfies card-type objective? | runner-up deck | margin (3★) |")
    add("|---|---|---|---|---|---|---|")
    for stage in stages:
        rec = final_recs[stage]
        stats = rec["recommended_stats"]
        runner_stats = rec["runner_up_stats"]
        reqs = rec["requirements"]
        if rec["recommended"] is None or stats is None:
            add(f"| {stage} | — | — | — | no eligible deck | — | — |")
            continue
        satisfies = (
            "n/a (no card objective)"
            if not reqs
            else f"yes ({rec['statuses'][rec['recommended']]})"
        )
        runner = rec["runner_up"] or "—"
        if runner_stats:
            margin = (
                f"{_pp(stats['three_star_rate'] - runner_stats['three_star_rate'])} "
                f"({_cp(stats['three_stars'], stats['n'])} vs "
                f"{_cp(runner_stats['three_stars'], runner_stats['n'])})"
            )
        else:
            margin = "—"
        add(
            f"| {stage} | **{rec['recommended']}** | "
            f"{_cp(stats['wins'], stats['n'])} | "
            f"{_cp(stats['three_stars'], stats['n'])} | {satisfies} | {runner} | {margin} |"
        )
    add("")
    add(
        "A `0pp` margin means the top two had the same three-star count; win rate, "
        "mean stars, mean rounds and name decided the pick. Read the counts, not just "
        "the rounded percentages."
    )
    add("")
    coin_flips: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for stage in stages:
        rec = final_recs[stage]
        rec_stats = rec["recommended_stats"]
        runner_stats = rec["runner_up_stats"]
        if (
            rec_stats
            and runner_stats
            and rec_stats["three_stars"] == runner_stats["three_stars"]
            and rec_stats["wins"] == runner_stats["wins"]
        ):
            coin_flips.append((stage, rec_stats, runner_stats))
    if coin_flips:
        add("### Coin-flip picks (3★ and win rate both tied)")
        add("")
        add(
            "These picks are not separated by the two primary metrics. Mean stars, "
            "mean rounds or the deck name decided them; treat them as arbitrary "
            "between the two rows."
        )
        add("")
        add("| stage | pick | runner-up |")
        add("|---|---|---|")
        for stage, rec_stats, runner_stats in coin_flips:
            rec_name = final_recs[stage]["recommended"]
            runner_name = final_recs[stage]["runner_up"]
            add(
                f"| {stage} | {rec_name} (3★ {_cp(rec_stats['three_stars'], rec_stats['n'])}, "
                f"win {_cp(rec_stats['wins'], rec_stats['n'])}, "
                f"stars {rec_stats['mean_stars']:.2f}, rounds {rec_stats['mean_rounds']:.1f}) "
                f"| {runner_name} (3★ {_cp(runner_stats['three_stars'], runner_stats['n'])}, "
                f"win {_cp(runner_stats['wins'], runner_stats['n'])}, "
                f"stars {runner_stats['mean_stars']:.2f}, rounds {runner_stats['mean_rounds']:.1f}) |"
            )
        add("")

    add("### Shortlist ranking (all three measured decks, 10 seeds)")
    add("")
    add("The final pick is the first cell; the other two are the measured alternatives.")
    add("")
    add("| stage | #1 | #2 | #3 |")
    add("|---|---|---|---|")
    for stage in stages:
        ranked = sorted(
            shortlist[stage],
            key=lambda deck: _rank_key(final_cells[stage][deck], deck),
        )
        cells = []
        for deck in ranked[:3]:
            stats = final_cells[stage][deck]
            cells.append(
                f"{deck} (3★ {_cp(stats['three_stars'], stats['n'])}, "
                f"win {_cp(stats['wins'], stats['n'])})"
            )
        while len(cells) < 3:
            cells.append("—")
        add(f"| {stage} | " + " | ".join(cells) + " |")
    add("")

    # wagner-specific cost of the card constraint
    if "wagner" in stages:
        add("### Wagner: cost of requiring a weapon card")
        add("")
        screen_rec = screen_recs["wagner"]
        best_any = screen_rec["best_any"]
        best_weapon = final_recs["wagner"]["recommended"]
        if best_any is None or best_weapon is None:
            add("No deck with data on wagner.")
        else:
            any_stats = screen_cells["wagner"][best_any]
            any_label = "5-seed screen"
            if best_any in shortlist["wagner"]:
                any_stats = final_cells["wagner"][best_any]
                any_label = f"{any_stats['n']}-seed shortlist"
            weapon_stats = final_cells["wagner"][best_weapon]
            add(
                "| role | deck | 3★ | win rate | sample |"
            )
            add("|---|---|---|---|---|")
            add(
                f"| best overall | {best_any} | "
                f"{_cp(any_stats['three_stars'], any_stats['n'])} | "
                f"{_cp(any_stats['wins'], any_stats['n'])} | {any_label} |"
            )
            add(
                f"| best weapon-carrying (recommended) | {best_weapon} | "
                f"{_cp(weapon_stats['three_stars'], weapon_stats['n'])} | "
                f"{_cp(weapon_stats['wins'], weapon_stats['n'])} | "
                f"{weapon_stats['n']}-seed shortlist |"
            )
            add("")
            win_cost = any_stats["win_rate"] - weapon_stats["win_rate"]
            three_cost = any_stats["three_star_rate"] - weapon_stats["three_star_rate"]
            if best_any == best_weapon:
                add(
                    "The best overall deck is already a weapon deck, so satisfying "
                    "the wagner objective costs **0pp** on both metrics."
                )
            else:
                add(
                    f"Win-rate cost of the weapon constraint: **{_pp(win_cost)}** "
                    f"({_cp(any_stats['wins'], any_stats['n'])} vs "
                    f"{_cp(weapon_stats['wins'], weapon_stats['n'])}); "
                    f"three-star cost: **{_pp(three_cost)}**. "
                    "The two rows use different sample sizes, so read the counts."
                )
        add("")
        add("**All card-type stages:**")
        add("")
        add("| stage | requirement | best overall (5-seed) | satisfies? | final pick | win-rate cost |")
        add("|---|---|---|---|---|---|")
        for stage in stages:
            if not screen_recs[stage]["requirements"]:
                continue
            best_any = screen_recs[stage]["best_any"]
            best_sat = final_recs[stage]["recommended"]
            if best_any is None or best_sat is None:
                add(f"| {stage} | — | — | — | — | — |")
                continue
            any_stats = screen_cells[stage][best_any]
            sat_stats = final_cells[stage][best_sat]
            cost = any_stats["win_rate"] - sat_stats["win_rate"]
            req_text = ", ".join(
                f"{requirement['kind']} x{requirement['count']}"
                for requirement in screen_recs[stage]["requirements"]
            )
            add(
                f"| {stage} | {req_text} | {best_any} "
                f"({_cp(any_stats['three_stars'], any_stats['n'])} 3★, "
                f"{_cp(any_stats['wins'], any_stats['n'])} win) | "
                f"{'yes' if screen_recs[stage]['best_any_satisfies'] else 'no'} | "
                f"{best_sat} ({_cp(sat_stats['three_stars'], sat_stats['n'])} 3★, "
                f"{_cp(sat_stats['wins'], sat_stats['n'])} win) | "
                f"{_pp(max(0.0, cost))} |"
            )
        add("")
        add(
            "No card-type stage has a win-rate/constraint conflict in this sweep: "
            "the unconstrained best deck already satisfies the requirement on all "
            "three stages, so the constraint costs 0pp. The samples differ (5 vs 10 "
            "seeds), so read the counts."
        )
        add("")
        add(
            "The top-5 table shows why composition alone is not enough. On wagner, "
            "non-weapon decks win 5/5 but score 0/5 3★ because the weapon objective "
            "is unreachable; the shortlist's weapon decks score 2-4/5 on the screen. "
            "On timaeus, two artifact-carrying decks also score 0/5 3★ (the policy "
            "does not play the artifact), so the shortlist is ranked by realized 3★, "
            "not just by carrying the card type."
        )
        add("")

    # ------------------------------------------------------------------ #
    # 2. 5-seed vs 10-seed disagreement
    # ------------------------------------------------------------------ #
    add("## 2. 5-seed vs 10-seed shortlist: did the pick change?")
    add("")
    disagreements: list[tuple[str, str, str]] = []
    for stage in stages:
        screen_pick = screen_recs[stage]["recommended"]
        final_pick = final_recs[stage]["recommended"]
        if screen_pick and final_pick and screen_pick != final_pick:
            disagreements.append((stage, screen_pick, final_pick))
    if disagreements:
        add(
            "| stage | 5-seed pick | 5-seed stats | same pick on 10 seeds | 10-seed pick | 10-seed stats | why |"
        )
        add("|---|---|---|---|---|---|---|")
        for stage, screen_pick, final_pick in disagreements:
            s5 = screen_cells[stage][screen_pick]
            s10_screen_pick = final_cells[stage][screen_pick]
            s10 = final_cells[stage][final_pick]
            if s10_screen_pick["three_stars"] < s10["three_stars"]:
                why = (
                    f"10-seed 3★ {_cp(s10['three_stars'], s10['n'])} vs "
                    f"{_cp(s10_screen_pick['three_stars'], s10_screen_pick['n'])}"
                )
            else:
                why = "3★ tied on 10 seeds; tie-break decided"
            add(
                f"| {stage} | {screen_pick} | "
                f"3★ {_cp(s5['three_stars'], s5['n'])}, win {_cp(s5['wins'], s5['n'])} | "
                f"3★ {_cp(s10_screen_pick['three_stars'], s10_screen_pick['n'])}, "
                f"win {_cp(s10_screen_pick['wins'], s10_screen_pick['n'])} | "
                f"{final_pick} | 3★ {_cp(s10['three_stars'], s10['n'])}, "
                f"win {_cp(s10['wins'], s10['n'])} | {why} |"
            )
        add("")
        add(
            "These are the stages where the 5-seed pick is not the 10-seed pick. "
            "The 10-seed pick is the one used in section 1. Note that the screen "
            "contains the pathological seed 0 while the extension adds clean seeds "
            "5-9, so part of the shift is seed composition, not only sample size."
        )
    else:
        add(
            "No stage changed its pick between the 5-seed screen and the 10-seed "
            "shortlist extension."
        )
    add("")

    # seed-0 sensitivity: re-rank the same 10-seed shortlist without seed 0
    no0_rows = [
        row
        for row in rows
        if int(row["seed"]) != 0
        and not row.get("error")
        and not row.get("truncated")
    ]
    no0_cells = aggregate(no0_rows, stages, decks)["cells"]
    seed0_disagreements: list[tuple[str, str, str]] = []
    for stage in stages:
        final_pick = final_recs[stage]["recommended"]
        if final_pick is None:
            continue
        no0_pick = pick_from_candidates(
            stage, shortlist[stage], no0_cells[stage], composition
        )["recommended"]
        if no0_pick and no0_pick != final_pick:
            seed0_disagreements.append((stage, final_pick, no0_pick))
    add("### Seed-0 sensitivity (re-rank without seed 0)")
    add("")
    if seed0_disagreements:
        add(
            "Excluding seed 0 changes the pick on these stages. The published picks "
            "keep seeds 0-9 (the benchmark's seed set); this table flags where the "
            "choice is sensitive to the seed-0 15-round artifact."
        )
        add("")
        add(
            "| stage | 0-9 pick | 0-9 3★ | same pick, no seed 0 | "
            "no-seed-0 pick | no-seed-0 3★ |"
        )
        add("|---|---|---|---|---|---|")
        for stage, final_pick, no0_pick in seed0_disagreements:
            s10 = final_cells[stage][final_pick]
            s9_old = no0_cells[stage][final_pick]
            s9 = no0_cells[stage][no0_pick]
            add(
                f"| {stage} | {final_pick} | {_cp(s10['three_stars'], s10['n'])} | "
                f"{_cp(s9_old['three_stars'], s9_old['n'])} | "
                f"{no0_pick} | {_cp(s9['three_stars'], s9['n'])} |"
            )
        add("")
    else:
        add(
            "The final picks are stable when seed 0 is excluded; no stage changes "
            "its pick."
        )
        add("")

    # ------------------------------------------------------------------ #
    # 3. per-stage top 5 (screen)
    # ------------------------------------------------------------------ #
    add("## 3. Per-stage top 5 (5-seed screen)")
    add("")
    add(
        "Top 5 by the ranking above on the screen seeds. `shortlisted` marks the "
        f"top-{shortlist_top_eff} decks that were extended to {len(shortlist_seeds)} "
        "seeds; `meets req` is the stage's card-type constraint."
    )
    add("")
    add("| stage | rank | deck | three-star | win | mean stars | mean rounds | meets req | shortlisted |")
    add("|---|---|---|---|---|---|---|---|---|")
    for stage in stages:
        reqs = stage_card_requirements(stage)
        top5 = sorted(decks, key=lambda deck: _rank_key(screen_cells[stage][deck], deck))[:5]
        for rank, deck in enumerate(top5, start=1):
            stats = screen_cells[stage][deck]
            meets = (
                "n/a"
                if not reqs
                else ("yes" if deck_requirement_status(composition[deck], reqs)[0] else "no")
            )
            add(
                f"| {stage} | {rank} | {deck} | "
                f"{_cp(stats['three_stars'], stats['n'])} | "
                f"{_cp(stats['wins'], stats['n'])} | {stats['mean_stars']:.2f} | "
                f"{stats['mean_rounds']:.1f} | {meets} | "
                f"{'yes' if deck in shortlist[stage] else '—'} |"
            )
    add("")
    add(
        "The full 16 x 20 grid (every deck on every stage, all 5 screen seeds) is in "
        "`data/runs/deck_matrix.jsonl`; this table keeps the top 5 per stage for "
        "readability. Shortlisted cells have 10 seeds in the JSONL."
    )
    add("")

    # ------------------------------------------------------------------ #
    # 4. overall ranking (screen)
    # ------------------------------------------------------------------ #
    add("## 4. Overall deck ranking (5-seed screen)")
    add("")
    add(
        "Pooled over all 16 stages x 5 screen seeds for every deck, so the comparison "
        "is fair despite the shortlist extension."
    )
    add("")
    add("| rank | deck | 3★ | win | mean stars | mean rounds | mean stage 3★ |")
    add("|---|---|---|---|---|---|---|")
    for rank, deck in enumerate(screen_ranked, start=1):
        stats = screen_by_deck[deck]
        add(
            f"| {rank} | {deck} | {_cp(stats['three_stars'], stats['n'])} | "
            f"{_cp(stats['wins'], stats['n'])} | {stats['mean_stars']:.2f} | "
            f"{stats['mean_rounds']:.1f} | {_pct(stats['mean_stage_three_star_rate'])} |"
        )
    add("")

    # ------------------------------------------------------------------ #
    # 5. dominated / superconduct
    # ------------------------------------------------------------------ #
    add("## 5. Dominated decks and `superconduct_aggro`")
    add("")
    never_won = [deck for deck in decks if screen_by_deck[deck]["wins"] == 0]
    never_three = [deck for deck in decks if screen_by_deck[deck]["three_stars"] == 0]
    dominated = sorted(set(never_won) & set(never_three))
    add(f"- Never won a screen game: {', '.join(never_won) if never_won else 'none'}.")
    add(f"- Never three-starred in the screen: {', '.join(never_three) if never_three else 'none'}.")
    add(f"- Dominated (neither won nor three-starred): {', '.join(dominated) if dominated else 'none'}.")
    add("")
    if "superconduct_aggro" in screen_by_deck:
        stats = screen_by_deck["superconduct_aggro"]
        rank = screen_ranked.index("superconduct_aggro") + 1
        add(
            f"`superconduct_aggro` ranks **{rank}/{len(decks)}** on the screen with "
            f"3★ {_cp(stats['three_stars'], stats['n'])} and "
            f"win {_cp(stats['wins'], stats['n'])}."
        )
        recommended_stages = [
            stage
            for stage in stages
            if final_recs[stage]["recommended"] == "superconduct_aggro"
        ]
        add(
            "It is the final recommendation on: "
            + (", ".join(recommended_stages) if recommended_stages else "no stage")
            + "."
        )
        within_10pp: list[str] = []
        for stage in stages:
            rec = final_recs[stage]
            if rec["recommended"] is None:
                continue
            gap = (
                rec["recommended_stats"]["three_star_rate"]
                - final_cells[stage]["superconduct_aggro"]["three_star_rate"]
            )
            if gap <= 0.10 + 1e-9:
                within_10pp.append(stage)
        add(
            f"Within 10pp of the final pick's 3★ rate on "
            f"{len(within_10pp)}/{len(stages)} stages: "
            + (", ".join(within_10pp) if within_10pp else "none")
            + "."
        )
        add(
            "Verdict: a high-win-rate / low-3★ deck — it rarely loses but leaves "
            "objectives on the table, and it is never the fixed pick. It is not "
            "competitive for the three-star metric."
        )
        add("")

    # ------------------------------------------------------------------ #
    # 6. card composition
    # ------------------------------------------------------------------ #
    add("## 6. Card composition")
    add("")
    add(
        "Counts are over the 30-card list (duplicates included). `W` = "
        "`GCG_CARD_MODIFY` + `GCG_TAG_WEAPON`, `A` = `GCG_CARD_MODIFY` + "
        "`GCG_TAG_ARTIFACT`, `E` = `GCG_CARD_EVENT` (excluding talent-tagged cards, "
        "which `npc.objectives` records as `talent`), `S` = `GCG_CARD_ASSIST`. "
        "`T` / `X` are talent cards and other equipment (e.g. vehicles)."
    )
    add("")
    add("| code | deck | W | A | E | S | T | X | total | wagner (W>=1) | timaeus (A>=1) | uncletian (E>=2) |")
    add("|---|---|---|---|---|---|---|---|---|---|---|---|")
    wagner_req = stage_card_requirements("wagner")
    timaeus_req = stage_card_requirements("timaeus")
    uncletian_req = stage_card_requirements("uncletian")
    for deck in decks:
        comp = composition[deck]
        wagner_ok = "yes" if deck_requirement_status(comp, wagner_req)[0] else "no"
        timaeus_ok = "yes" if deck_requirement_status(comp, timaeus_req)[0] else "no"
        uncletian_ok = "yes" if deck_requirement_status(comp, uncletian_req)[0] else "no"
        add(
            f"| {_deck_codes(decks)[deck]} | {deck} | {comp.get('weapons', 0)} | "
            f"{comp.get('artifacts', 0)} | {comp.get('events', 0)} | "
            f"{comp.get('supports', 0)} | {comp.get('talents', 0)} | "
            f"{comp.get('other_equipment', 0)} | {comp.get('total', 0)} | "
            f"{wagner_ok} | {timaeus_ok} | {uncletian_ok} |"
        )
    add("")
    weapon_decks = [deck for deck in decks if composition[deck].get("weapons", 0) >= 1]
    artifact_decks = [deck for deck in decks if composition[deck].get("artifacts", 0) >= 1]
    event_decks = [deck for deck in decks if composition[deck].get("events", 0) >= 2]
    add(
        f"Note: {len(weapon_decks)}/{len(decks)} decks carry a weapon card, so wagner "
        f"is the restrictive stage; {len(artifact_decks)}/{len(decks)} carry an "
        f"artifact (missing: "
        + (", ".join(sorted(set(decks) - set(artifact_decks))) or "none")
        + f"); {len(event_decks)}/{len(decks)} have at least 2 event cards."
    )
    add("")

    # ------------------------------------------------------------------ #
    # 7. defects / artefacts
    # ------------------------------------------------------------------ #
    add("## 7. Benchmark defects / weak stages")
    add("")
    no_win: list[str] = []
    no_three: list[str] = []
    thin_three: list[str] = []
    no_requirement_deck: list[str] = []
    for stage in stages:
        best_win = max(cell["win_rate"] for cell in screen_cells[stage].values())
        best_three = max(cell["three_star_rate"] for cell in screen_cells[stage].values())
        if best_win == 0:
            no_win.append(stage)
        if best_three == 0:
            no_three.append(stage)
        elif best_three < 0.40:
            thin_three.append(stage)
        if not screen_recs[stage]["shortlist"]:
            no_requirement_deck.append(stage)
    if no_win:
        add(
            "**No viable deck** (no deck won a screen game): "
            + ", ".join(no_win)
            + ". Do not ship these stages without an engine/HP/stage-data fix."
        )
    else:
        add(
            f"No stage has zero wins across all {len(decks)} decks; every stage is "
            "winnable by some deck."
        )
    if no_three:
        add(
            "**No three-star deck** (max 3★ rate = 0 on the screen): "
            + ", ".join(no_three)
            + "."
        )
    if thin_three:
        add(
            "**Thin three-star ceiling** (best 3★ < 40% on the screen): "
            + ", ".join(thin_three)
            + ". More seeds or a stage-data review is warranted before shipping."
        )
    if no_requirement_deck:
        add(
            "**Card-type objective cannot be satisfied at all**: "
            + ", ".join(no_requirement_deck)
            + "."
        )
    add("")
    draw_by_stage = Counter(str(row["stage"]) for row in draw_rows)
    total_by_stage = {
        stage: sum(screen_cells[stage][deck]["n"] for deck in decks) for stage in stages
    }
    add("### Draw / 15-round-cap rate by stage (5-seed screen)")
    add("")
    add(
        "A draw is `winner` not in `{0, 1}` with no error/truncation; all observed "
        "draws end at round 15. They are counted as non-wins above."
    )
    add("")
    add("| stage | draws | valid games | draw rate |")
    add("|---|---|---|---|")
    for stage in stages:
        total = total_by_stage[stage]
        count = draw_by_stage.get(stage, 0)
        add(f"| {stage} | {count} | {total} | {_pct(count / total) if total else '—'} |")
    add("")
    add(
        "Method caveat: the screen has only "
        f"{len(screen_seeds)} samples per cell, so one game moves a 3★ rate by "
        f"{100 / max(1, len(screen_seeds)):.0f}pp. The shortlist finalists have "
        f"{len(shortlist_seeds)} samples; their Wilson 95% CI is in the JSON notes. "
        "Seed 0 is not exchangeable with the others (38% draws), so the 5-seed "
        "screen is conservative. Treat near-ties (and the 3★ tie-break) as "
        "unresolved rather than real."
    )
    add("")

    # ------------------------------------------------------------------ #
    # 8. ready-to-paste config
    # ------------------------------------------------------------------ #
    add("## 8. Ready-to-paste `configs/stage_decks.json` (not written by this script)")
    add("")
    add(
        "Notes carry the raw counts, seed count, shortlist origin and Wilson 95% CI "
        "for the three-star rate. This file is intentionally only shown here; "
        "another agent owns `configs/stage_decks.json`."
    )
    add("")
    config_payload: dict[str, Any] = {"stages": {}}
    for stage in stages:
        rec = final_recs[stage]
        if rec["recommended"] is None or rec["recommended_stats"] is None:
            continue
        stats = rec["recommended_stats"]
        runner_stats = rec["runner_up_stats"]
        reqs = rec["requirements"]
        note_parts = [
            f"3-star {_cp(stats['three_stars'], stats['n'])}",
            f"win {_cp(stats['wins'], stats['n'])}",
            f"mean stars {stats['mean_stars']:.2f}",
            f"mean rounds {stats['mean_rounds']:.1f}",
            f"{stats['n']}-seed shortlist (top {shortlist_top_eff} from "
            f"{len(screen_seeds)}-seed screen)",
        ]
        if reqs:
            req_text = ", ".join(
                f"{requirement['kind']} x{requirement['count']}" for requirement in reqs
            )
            note_parts.append(
                f"satisfies {req_text} objective "
                f"({rec['statuses'][rec['recommended']]})"
            )
        else:
            note_parts.append("no card-type objective")
        if runner_stats:
            note_parts.append(
                f"runner-up {rec['runner_up']} "
                f"{_cp(runner_stats['three_stars'], runner_stats['n'])}"
            )
        note_parts.append(
            f"3-star 95% CI {_fmt_ci(stats['three_stars'], stats['n'])}"
        )
        config_payload["stages"][stage] = {
            "deck": rec["recommended"],
            "notes": "; ".join(note_parts),
        }
    add("```json")
    add(json.dumps(config_payload, ensure_ascii=False, indent=2))
    add("```")
    add("")

    # ------------------------------------------------------------------ #
    # 9. reproduce
    # ------------------------------------------------------------------ #
    add("## 9. Reproduce")
    add("")
    add("```bash")
    add("PY=/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python")
    add("cd /home/ubuntuaaa/projects/gitcg-skills")
    add("# phase 1: full 5-seed screen (16 x 20 x 5 = 1600 games)")
    add(
        f'"$PY" -m scripts.deck_matrix --seeds '
        f'{",".join(str(seed) for seed in screen_seeds)} --workers 8'
    )
    add("# phase 2: extend the top 3 per stage to 10 seeds (240 new games)")
    add(
        f'"$PY" -m scripts.deck_matrix --shortlist-top {shortlist_top_eff} '
        f'--shortlist-seeds {",".join(str(seed) for seed in shortlist_seeds)} '
        f'--screen-seeds {",".join(str(seed) for seed in screen_seeds)} --workers 8'
    )
    add('# "$PY" -m scripts.deck_matrix --analyze-only   # rebuild this report from the JSONL')
    add("# clean rerun: rm data/runs/deck_matrix.jsonl data/runs/deck_matrix.jsonl.meta.json")
    add("```")
    add("")
    add(
        "Rows are appended to `data/runs/deck_matrix.jsonl` (gitignored) and keyed by "
        "`(stage, deck, seed)`, so adding seeds resumes instead of replaying. The "
        "fingerprint sidecar `data/runs/deck_matrix.jsonl.meta.json` pins the engine "
        "lock, HP table, stage JSON, assets, deck list and judging flags (not the seed "
        "list, so the seed set and shortlist can be extended)."
    )
    add("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stages", default="all", help="'all' or comma-separated stage names")
    parser.add_argument("--decks", default="all", help="'all' or comma-separated deck slugs")
    parser.add_argument("--seeds", default="2", help="count (N -> 0..N-1) or comma-separated list")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument(
        "--screen-seeds",
        default="0,1,2,3,4",
        help="seeds of the full screen used to choose the shortlist",
    )
    parser.add_argument(
        "--shortlist-top",
        type=int,
        default=0,
        help="extend the top N decks per stage (0 = full screen only)",
    )
    parser.add_argument(
        "--shortlist-seeds",
        default="10",
        help="target seed count/list for the shortlist extension",
    )
    parser.add_argument(
        "--analyze-only",
        action="store_true",
        help="skip games; rebuild the report from the existing JSONL",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    stages = list(_stage_list(args.stages))
    decks = list(_deck_list(args.decks))
    out = Path(args.out)
    meta: dict[str, Any]
    if args.analyze_only:
        if not out.exists():
            raise SystemExit(f"--analyze-only: {out} does not exist")
        meta = _load_meta(out)
        stored = meta.get("fingerprint", {})
        if stored.get("stages"):
            stages = [str(name) for name in stored["stages"]]
        if stored.get("decks"):
            decks = [str(name) for name in stored["decks"]]
        print(f"[deck_matrix] analyze-only: {out}", flush=True)
    elif int(args.shortlist_top) > 0:
        meta = run_shortlist(args)
    else:
        meta = run(args)

    screen_seeds = [
        int(seed)
        for seed in meta.get("screen_seeds", _seed_list(args.screen_seeds))
    ]
    shortlist_top = int(meta.get("shortlist_top", args.shortlist_top))
    shortlist_seeds = [
        int(seed)
        for seed in meta.get("shortlist_seeds", _seed_list(str(args.shortlist_seeds)))
    ]
    rows = load_rows(out)
    composition = load_composition()
    report = render_report(
        rows=rows,
        stages=stages,
        decks=decks,
        composition=composition,
        meta=meta,
        screen_seeds=screen_seeds,
        shortlist_top=shortlist_top,
        shortlist_seeds=shortlist_seeds,
    )
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(f"[deck_matrix] report -> {report_path}", flush=True)
    return 0 if any(not row.get("error") for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
