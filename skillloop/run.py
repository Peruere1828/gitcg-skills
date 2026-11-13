"""Experiment runner: one group x stage x seed grid, streamed to JSONL.

Reuses the environment as-is (``envs.match.run_match`` + ``npc`` judging) and adds
only what the course experiments need:

* a **group** switch (scripted / contestant / llm_empty / static / dynamic) so the
  four conditions differ in exactly one thing: the skill library;
* a per-game row with the fields the paired gate needs (``stage``, ``seed``,
  ``three_star``) plus cost/audit fields (``llm_calls``, ``library_version``,
  ``model``, ``prompt`` hashes are inside the agent decisions);
* resume by ``(stage, seed)`` and a fingerprint sidecar so a run cannot be mixed
  with a different library version or engine build.

Usage::

    python -m skillloop.run --group scripted --stages all --seeds 3 --workers 6
    python -m skillloop.run --group llm_empty --stages all --seeds 50   # rules_v1
    python -m skillloop.run --group static --loadout reports/loadouts/v1 --seeds 50
    python -m skillloop.run --group dynamic --library data/skills/dyn_v4 \
        --stage-decks configs/stage_decks.json --loadout reports/loadouts/v4 \
        --seeds 50 --out data/runs/dynamic_loadout.jsonl
    python -m skillloop.run --group scripted --stages all --seeds 3 --stage-rules

Group defaults (``--library`` may only override static/dynamic)::

    llm_empty -> data/skills/rules_v1   (17 rule; no strategy/stage_specific, no loadout)
    static    -> data/skills/static_v1  (17 rule + 10 strategy)
    dynamic   -> data/skills/dyn_v4     (17 rule + 23 strategy)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import sys
import time
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common.seeding import derive_seed
from envs.match import run_match
from npc.objectives import evaluate_stage
from npc.policy import npc_policy_for_stage
from npc.spec import NpcStage, get_stage, stage_names
from npc.stage_rules import stage_rules_json
from skillloop.agent import AgentConfig, SkillAgentPolicy
from skillloop.era import resolve_base_policy, resolve_deck
from skillloop.library import SkillLibrary
from skillloop.loadout import (
    Loadout,
    engine_lock_hash,
    load_loadouts,
    load_stage_decks,
    loadout_matches,
    stage_task,
)
from skillloop.render import StateRenderer, TaskSpec
from skillloop.replay import compact_trace

GROUPS = ("scripted", "contestant", "llm_empty", "static", "dynamic")
# Groups that load a skill library. ``llm_empty`` is **not** an empty prompt: it is
# the "LLM + game rules only" arm, so it loads the 17-rule rules_v1 library.
LIBRARY_GROUPS = ("llm_empty", "static", "dynamic")
# Frozen stage loadouts only make sense when the library has strategy candidates;
# llm_empty deliberately ignores ``--loadout``.
LOADOUT_GROUPS = ("static", "dynamic")
DEFAULT_LIBRARY_ROOTS = {
    "llm_empty": "data/skills/rules_v1",   # 17 rule skills, no strategy/stage_specific
    "static": "data/skills/static_v1",     # 17 rule + 10 strategy
    "dynamic": "data/skills/dyn_v4",       # 17 rule + 23 strategy
}


@dataclass(frozen=True)
class RunSpec:
    group: str
    stages: tuple[str, ...]
    seeds: tuple[int, ...]
    deck: str
    workers: int = 1
    library_root: str | None = None
    top_k: int = 5
    model: str | None = None
    max_calls: int = 80
    rotation: str | None = None
    out: str = "data/runs/run.jsonl"
    # Per-stage fixed deck (``configs/stage_decks.json``), identical in every arm.
    stage_decks_root: str | None = None
    # Frozen per-(stage, library version) skill loadouts.
    loadout_root: str | None = None
    # Card pool to pin the game to (``CreateParam(version=...)``, e.g. "v3.3.0").
    # ``None`` keeps the engine default, i.e. today's behaviour. A pinned pool
    # rejects any deck whose characters/cards were released after it.
    card_version: str | None = None
    # Send the stage's adventure-challenge rules (initial supports / effects) to
    # the engine through ``ATTR_CREATEPARAM_STAGE_RULES``. The rule belongs to the
    # stage, so it applies identically to every group.
    stage_rules: bool = False
    # Store a compact single-game trace per row (``skillloop.replay``), so skill
    # extraction can show *where* a game went wrong instead of only statistics.
    trace: bool = False
    # Optional in-game notebook (A/B arm): the model may write a short ``note`` that
    # is echoed into the next decision's user message. Off by default; recorded in
    # the fingerprint so scratchpad and baseline runs cannot be mixed on resume.
    scratchpad: bool = False
    # Experimental prompt/agent levers (default off; separate fingerprints):
    #   rich_render: candidate/state effects, auras, reaction hints, hand count
    #   round_plan: structured per-round scratchpad plan (implies scratchpad)
    #   base_guard: keep the scripted expert action unless the LLM cites a non-rule skill
    rich_render: bool = False
    round_plan: bool = False
    base_guard: bool = False
    base_guard_strict: bool = False

    def fingerprint(self) -> dict[str, Any]:
        library_root = resolved_library_root(self)
        library_version = 0
        if library_root:
            try:
                library_version = SkillLibrary.load(library_root).version
            except FileNotFoundError:
                library_version = -1
        loadout_root = self.loadout_root if self.group in LOADOUT_GROUPS else None
        return {
            "group": self.group,
            "stages": list(self.stages),
            "seeds": list(self.seeds),
            "deck": self.deck,
            "card_version": self.card_version,
            "library_root": library_root,
            "library_version": library_version,
            "top_k": self.top_k,
            "model": self.model,
            "rotation": self.rotation,
            "stage_rules": self.stage_rules,
            "trace": self.trace,
            "scratchpad": self.scratchpad,
            "rich_render": self.rich_render,
            "round_plan": self.round_plan,
            "base_guard": self.base_guard,
            "base_guard_strict": self.base_guard_strict,
            "special_rules": (
                _sha1(Path("configs/npc/special_rules.json"))
                if self.stage_rules
                else "off"
            ),
            "engine_lock": _sha1(Path("configs/engine.lock")),
            "hp_table": _sha1(Path("configs/npc/hp_table.json")),
            "stages_file_sha1": _sha1(Path("configs/npc/npc_stages_mond_liyue.json")),
            "stage_decks_root": self.stage_decks_root,
            "stage_decks_hash": (
                _sha1(Path(self.stage_decks_root)) if self.stage_decks_root else "none"
            ),
            "loadout_root": loadout_root,
            "loadout_hash": (
                _dir_hash(Path(loadout_root)) if loadout_root else "none"
            ),
        }


def resolved_library_root(spec: "RunSpec") -> str | None:
    """The library actually used by ``spec.group`` (defaults applied).

    ``llm_empty`` is fixed to ``rules_v1`` and ignores an explicit ``library_root``;
    ``static``/``dynamic`` may override their default; the other groups load none.
    """
    if spec.group in LIBRARY_GROUPS:
        if spec.group == "llm_empty":
            return DEFAULT_LIBRARY_ROOTS["llm_empty"]
        return spec.library_root or DEFAULT_LIBRARY_ROOTS[spec.group]
    return None


def _sha1(path: Path) -> str:
    if not path.exists():
        return "missing"
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12]


def _dir_hash(path: Path) -> str:
    """Hash file names + contents so any loadout edit invalidates resume."""
    if not path.exists():
        return "missing"
    if path.is_file():
        return _sha1(path)
    digest = hashlib.sha1()
    for file in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(str(file.relative_to(path)).encode("utf-8"))
        digest.update(file.read_bytes())
    return digest.hexdigest()[:12]


def budget_from_env() -> float | None:
    """Read the optional ``GITCG_LLM_BUDGET_USD`` cap for this invocation."""
    raw = os.environ.get("GITCG_LLM_BUDGET_USD", "").strip()
    if not raw:
        return None
    try:
        budget = float(raw)
    except ValueError as exc:
        raise SystemExit(f"GITCG_LLM_BUDGET_USD must be a number, got {raw!r}") from exc
    if budget < 0:
        raise SystemExit("GITCG_LLM_BUDGET_USD must be >= 0")
    return budget


def budget_exhausted(spent: float, budget: float | None) -> bool:
    """True once a set budget has been reached; the guard used before new games."""
    return budget is not None and spent >= budget


def _assets():
    from envs.policy import _asset_catalog

    return _asset_catalog()


def task_spec(stage: NpcStage, deck: str) -> TaskSpec:
    """The stage prompt spec (kept here for callers; data assembly lives in loadout)."""
    return stage_task(stage, deck)


def _stage_decks_for_spec(spec: RunSpec) -> dict[str, str]:
    """Load the fixed stage->deck map once, or return ``{}`` without the flag."""
    if not spec.stage_decks_root:
        return {}
    path = Path(spec.stage_decks_root)
    if not path.exists():
        raise SystemExit(f"--stage-decks file not found: {path}")
    return load_stage_decks(path)


def _loadouts_for_spec(spec: RunSpec, *, library: SkillLibrary | None = None) -> dict[str, Loadout]:
    """Load + validate frozen loadouts once (static/dynamic only)."""
    if not spec.loadout_root or spec.group not in LOADOUT_GROUPS:
        return {}
    library_root = resolved_library_root(spec)
    if not library_root:
        raise SystemExit(f"group {spec.group!r} needs a skill library for --loadout")
    lib = library or SkillLibrary.load(library_root)
    root = Path(spec.loadout_root)
    versioned = root / f"v{lib.version}"
    source = versioned if versioned.is_dir() else root
    loadouts = load_loadouts(source)
    if not loadouts:
        raise SystemExit(
            f"no loadouts in {source}; run: python -m scripts.plan_loadout "
            f"--library {library_root} --out {root}"
        )
    current_lock = engine_lock_hash()
    for stage in spec.stages:
        loadout = loadouts.get(stage)
        if loadout is None:
            continue
        if not loadout_matches(loadout, library_version=lib.version, engine_lock=current_lock):
            raise SystemExit(
                f"stale loadout for {stage!r}: file has library v{loadout.library_version} / "
                f"engine {loadout.engine_lock or 'missing'}, run uses v{lib.version} / "
                f"engine {current_lock}; re-run scripts.plan_loadout"
            )
    return loadouts


def build_policy(
    spec: RunSpec,
    stage: NpcStage,
    *,
    client: Any = None,
    deck: str | None = None,
    pinned_skills: tuple[str, ...] = (),
):
    """Return the policy for one group (the only thing that differs across groups)."""
    if deck is None:
        deck = _stage_decks_for_spec(spec).get(stage.name, spec.deck)
    if spec.group == "scripted":
        return resolve_base_policy(deck, seed=0)
    if spec.group == "contestant":
        from npc.contestant import build_contestant_policy

        return build_contestant_policy(resolve_deck(deck), stage, inject_objectives=True)
    if spec.group not in GROUPS:
        raise SystemExit(f"unknown group {spec.group!r}; expected one of {GROUPS}")

    library = None
    if spec.group in LIBRARY_GROUPS:
        library_root = resolved_library_root(spec)
        if not library_root:
            raise SystemExit(f"group {spec.group!r} needs a skill library")
        library = SkillLibrary.load(library_root)
    if spec.rich_render:
        from skillloop.rich_render import RichStateRenderer

        renderer = RichStateRenderer(
            _assets(), opponent_name=f"NPC({stage.display or stage.name})"
        )
    else:
        renderer = StateRenderer(_assets(), opponent_name=f"NPC({stage.display or stage.name})")
    scratchpad = spec.scratchpad or spec.round_plan
    return SkillAgentPolicy(
        resolve_base_policy(deck, seed=0),
        client,
        renderer=renderer,
        task=task_spec(stage, deck),
        library=library,
        config=AgentConfig(
            model=spec.model,
            max_calls_per_game=spec.max_calls,
            top_k=spec.top_k,
            pinned_skills=tuple(pinned_skills),
            scratchpad=scratchpad,
            round_plan=spec.round_plan,
            base_guard=spec.base_guard,
            base_guard_strict=spec.base_guard_strict,
            # A note plus the usual JSON needs more than the 64-token reply budget;
            # keep the default arm at 64 so its cost/behaviour is unchanged.
            max_tokens=160 if scratchpad else 64,
        ),
    )


def _loadout_from_payload(payload: dict[str, Any]) -> Loadout | None:
    raw = payload.get("loadout")
    if isinstance(raw, Loadout):
        return raw
    if isinstance(raw, dict) and raw.get("stage"):
        return Loadout.from_dict(raw)
    return None


def play_one(payload: dict[str, Any]) -> dict[str, Any]:
    """Play one (stage, seed) game and return a JSON-safe row.

    Runs inside a worker process: the LLM client and the library are constructed
    here so nothing unpicklable (sockets, file handles) crosses the process boundary.
    """
    spec = RunSpec(**payload["spec"])
    stage = get_stage(payload["stage"])
    seed = int(payload["seed"])
    rules_json = stage_rules_json(stage.name) if spec.stage_rules else None

    stage_decks = payload.get("stage_decks")
    if stage_decks is None:
        stage_decks = _stage_decks_for_spec(spec)
    loadout = _loadout_from_payload(payload) if spec.group in LOADOUT_GROUPS else None
    if loadout is None and spec.loadout_root and spec.group in LOADOUT_GROUPS:
        loadout = _loadouts_for_spec(spec).get(stage.name)
    if loadout is not None and loadout.stage != stage.name:
        return {
            "stage": stage.name, "seed": seed, "group": spec.group,
            "card_version": spec.card_version,
            "stage_rules": bool(spec.stage_rules),
            "scratchpad": bool(spec.scratchpad),
            "error": f"loadout_stage_mismatch:{loadout.stage}",
        }

    deck = loadout.deck if loadout is not None else stage_decks.get(stage.name, spec.deck)
    pinned_skills = tuple(loadout.skills) if loadout is not None else ()

    client = None
    if spec.group in LIBRARY_GROUPS:
        from common.llm import build_llm_client

        client = build_llm_client()
        if client is None:
            return {
                "stage": stage.name, "seed": seed, "group": spec.group,
                "card_version": spec.card_version,
                "stage_rules": bool(spec.stage_rules),
                "scratchpad": bool(spec.scratchpad),
                "error": "no_llm_config",
            }

    policy0 = build_policy(
        spec, stage, client=client, deck=deck, pinned_skills=pinned_skills
    )
    contestant = resolve_deck(deck)
    npc_deck = type(contestant)(
        name=f"npc_{stage.name}", characters=stage.deck_characters, cards=contestant.cards
    )
    started = time.perf_counter()
    try:
        record = run_match(
            contestant,
            npc_deck,
            policy0,
            npc_policy_for_stage(stage, rotation=spec.rotation),
            seed=seed,
            version=spec.card_version,
            record_decisions=True,
            record_events=True,
            record_final_state=True,
            stage_rules=rules_json,
        )
    except Exception as exc:
        # The engine validates the pinned pool while it builds the initial state,
        # which happens inside ``run_match`` but *before* its own try/except: an
        # out-of-pool card raises ``AssertionError`` (message printed by the C++
        # side as "Unknown card id N"). Turn that into a normal error row so one
        # bad deck cannot kill a whole grid; other engine errors keep the old
        # behaviour when no pool is pinned.
        if spec.card_version is None:
            raise
        detail = f": {exc}" if str(exc).strip() else ""
        return {
            "task_index": payload.get("task_index", 0),
            "stage": stage.name,
            "seed": seed,
            "deck": deck,
            "card_version": spec.card_version,
            "group": spec.group,
            "stage_rules": bool(spec.stage_rules),
            "scratchpad": bool(spec.scratchpad),
            "error": f"{type(exc).__name__}{detail} (card_version={spec.card_version})",
            "seconds": round(time.perf_counter() - started, 2),
        }
    elapsed = time.perf_counter() - started
    stars = evaluate_stage(stage, record)
    decisions = getattr(policy0, "decisions", []) or []
    usage = getattr(client, "usage", None)
    library_root = resolved_library_root(spec)
    if loadout is not None:
        library_version = loadout.library_version
    elif library_root:
        try:
            library_version = SkillLibrary.load(library_root).version
        except FileNotFoundError:
            library_version = 0
    else:
        library_version = 0
    row = {
        "task_index": payload.get("task_index", 0),
        "stage": stage.name,
        "seed": seed,
        "deck": deck,
        "card_version": spec.card_version,
        "group": spec.group,
        "stage_rules": bool(spec.stage_rules),
        "scratchpad": bool(spec.scratchpad or spec.round_plan),
        "rich_render": bool(spec.rich_render),
        "round_plan": bool(spec.round_plan),
        "base_guard": bool(spec.base_guard),
        "base_guard_strict": bool(spec.base_guard_strict),
        "model": spec.model,
        "library_root": library_root,
        "library_version": library_version,
        "skill_loadout_hash": loadout.fingerprint() if loadout is not None else "",
        "pinned_skills": list(pinned_skills),
        "winner": record.winner,
        "rounds": record.rounds,
        "three_star": bool(stars.three_star),
        "stars": int(stars.stars),
        "n_objectives": len(stars.objectives),
        "objectives": {item.type: bool(item.achieved) for item in stars.objectives},
        "error": record.error,
        "truncated": record.truncated,
        "seconds": round(elapsed, 2),
        "llm_calls": len(decisions),
        "llm_errors": sum(1 for d in decisions if d.error),
        "llm_seconds": round(sum(d.latency for d in decisions), 2),
        "cited_skills": sorted({sid for d in decisions for sid in d.used}),
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "cached_tokens": int(getattr(usage, "cached_tokens", 0) or 0),
        "cost_usd": round(float(getattr(usage, "cost_usd", 0.0) or 0.0), 6),
    }
    if spec.trace:
        match_decisions = list(getattr(record, "decisions", None) or ())
        match_events = list(getattr(record, "events", None) or ())
        trace_meta: dict[str, Any] = {}
        row["trace"] = compact_trace(row, match_decisions, match_events, meta=trace_meta)
        row["trace_meta"] = {
            "n_decisions": len(match_decisions),
            "truncated": bool(trace_meta.get("truncated", False)),
        }
    return row


def run(spec: RunSpec, *, progress: bool = True) -> dict[str, Any]:
    out = Path(spec.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Read the two per-stage configs once per invocation; workers get the resolved
    # values in their payload instead of re-reading them per game.
    stage_decks = _stage_decks_for_spec(spec)
    if progress and spec.group == "llm_empty" and spec.library_root:
        print(
            f"[run] --library ignored for group 'llm_empty' "
            f"(fixed library: {DEFAULT_LIBRARY_ROOTS['llm_empty']})",
            flush=True,
        )
    loadouts = _loadouts_for_spec(spec)
    if loadouts:
        missing = [stage for stage in spec.stages if stage not in loadouts]
        if missing:
            raise SystemExit(
                f"no loadout for stage(s) {missing} under {spec.loadout_root}; "
                "run scripts.plan_loadout for every stage in this grid"
            )
        for stage in spec.stages:
            expected = stage_decks.get(stage, spec.deck)
            if loadouts[stage].deck != expected:
                raise SystemExit(
                    f"loadout for {stage!r} was planned for deck "
                    f"{loadouts[stage].deck!r}, but --stage-decks/RunSpec.deck gives "
                    f"{expected!r}; re-run scripts.plan_loadout"
                )
    elif spec.loadout_root and spec.group not in LOADOUT_GROUPS and progress:
        print(
            f"[run] --loadout ignored for group {spec.group!r} "
            "(only static/dynamic accept loadouts)",
            flush=True,
        )

    meta_path = out.with_suffix(out.suffix + ".meta.json")
    fingerprint = spec.fingerprint()

    if meta_path.exists():
        previous = json.loads(meta_path.read_text(encoding="utf-8"))
        if previous.get("fingerprint") != fingerprint:
            raise SystemExit(
                f"{out} was produced by a different spec; use another --out or delete it\n"
                f"  old: {previous.get('fingerprint')}\n  new: {fingerprint}"
            )
    else:
        meta_path.write_text(json.dumps({"fingerprint": fingerprint}, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")

    done: set[tuple[str, int]] = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if not row.get("error"):
                done.add((str(row["stage"]), int(row["seed"])))

    pending = [
        {
            "spec": spec.__dict__,
            "stage": stage,
            "seed": seed,
            "task_index": index,
            "stage_decks": stage_decks,
            "loadout": loadouts[stage].as_dict() if stage in loadouts else None,
        }
        for index, (stage, seed) in enumerate(
            (stage, seed) for stage in spec.stages for seed in spec.seeds
        )
        if (stage, seed) not in done
    ]
    if progress:
        print(f"[run] {len(pending)} pending / {len(done)} done -> {out}", flush=True)

    rows: list[dict[str, Any]] = []
    budget = budget_from_env()
    spent = 0.0
    aborted: str | None = None
    started = time.perf_counter()
    with out.open("a", encoding="utf-8") as handle:
        if spec.workers <= 1:
            for item in pending:
                if budget_exhausted(spent, budget):
                    aborted = "budget"
                    break
                row = play_one(item)
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                rows.append(row)
                spent += float(row.get("cost_usd") or 0.0)
                if progress:
                    _print_row(row)
        else:
            # ``spawn`` (not fork): the parent has already imported the engine, and
            # forking a process with a live V8 isolate deadlocks the child on its first
            # engine call. Spawn costs ~2 s per worker and is safe.
            context = mp.get_context("spawn")
            with ProcessPoolExecutor(max_workers=spec.workers, mp_context=context) as pool:
                # Sliding window: keep ``workers`` games in flight and refill a slot
                # whenever one finishes. A slow game must not hold back the next
                # pending game (no batch barrier); rows come out in completion order.
                pending_iter = iter(pending)
                waiting = next(pending_iter, None)
                in_flight: set[Future] = set()

                def submit_next() -> bool:
                    nonlocal waiting, aborted
                    if waiting is None:
                        return False
                    if budget_exhausted(spent, budget):
                        aborted = "budget"
                        return False
                    in_flight.add(pool.submit(play_one, waiting))
                    waiting = next(pending_iter, None)
                    return True

                while len(in_flight) < spec.workers and submit_next():
                    pass
                while in_flight:
                    done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                    for future in done:
                        in_flight.discard(future)
                        row = future.result()
                        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                        handle.flush()
                        rows.append(row)
                        spent += float(row.get("cost_usd") or 0.0)
                        if progress:
                            _print_row(row)
                    while len(in_flight) < spec.workers and submit_next():
                        pass

    if aborted and progress:
        print(f"[run] budget ${budget:.4f} reached (${round(spent, 6):.4f}); "
              f"stopped launching new games", flush=True)

    summary = summarize(rows)
    summary["seconds"] = round(time.perf_counter() - started, 1)
    summary["out"] = str(out)
    if budget is not None:
        summary["budget_usd"] = budget
        summary["spent_usd"] = round(spent, 6)
    if aborted:
        summary["aborted"] = aborted
    return summary


def _print_row(row: dict[str, Any]) -> None:
    flag = "3★" if row.get("three_star") else f"{row.get('stars', 0)}★"
    print(f"  {row['stage']:16s} seed={row['seed']:<5d} {flag:>2s} calls={row.get('llm_calls', 0):<3d}"
          f" {row.get('seconds', 0):>5.1f}s {row.get('error') or ''}", flush=True)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in rows if not row.get("error") and not row.get("truncated")]
    if not valid:
        return {"games": 0, "valid": 0}
    per_stage: dict[str, dict[str, Any]] = {}
    for row in valid:
        bucket = per_stage.setdefault(row["stage"], {"n": 0, "three_star": 0, "stars": 0})
        bucket["n"] += 1
        bucket["three_star"] += int(bool(row["three_star"]))
        bucket["stars"] += int(row.get("stars") or 0)
    return {
        "games": len(rows),
        "valid": len(valid),
        "errors": len(rows) - len(valid),
        "three_star_rate": round(sum(1 for r in valid if r["three_star"]) / len(valid), 4),
        "mean_stars_per_objective": round(
            sum((r.get("stars") or 0) / max(1, r.get("n_objectives") or 1) for r in valid) / len(valid), 4
        ),
        "llm_calls": sum(int(r.get("llm_calls") or 0) for r in valid),
        "prompt_tokens": sum(int(r.get("prompt_tokens") or 0) for r in valid),
        "completion_tokens": sum(int(r.get("completion_tokens") or 0) for r in valid),
        "cached_tokens": sum(int(r.get("cached_tokens") or 0) for r in valid),
        "cost_usd": round(sum(float(r.get("cost_usd") or 0.0) for r in valid), 6),
        "per_stage": {
            stage: {
                "n": bucket["n"],
                "three_star_rate": round(bucket["three_star"] / bucket["n"], 4),
                "mean_stars": round(bucket["stars"] / bucket["n"], 3),
            }
            for stage, bucket in sorted(per_stage.items())
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", required=True, choices=GROUPS)
    parser.add_argument("--stages", default="all", help="'all' or comma-separated stage names")
    parser.add_argument("--seeds", default="1,2,3", help="count (N -> 0..N-1) or comma-separated list")
    parser.add_argument("--deck", default="superconduct_aggro")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--library", default=None,
        help="skill library override (static/dynamic only; llm_empty is fixed to rules_v1)",
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-calls", type=int, default=80)
    parser.add_argument("--rotation", default=None)
    parser.add_argument("--stage-decks", default=None,
                        help="JSON with one fixed deck per stage (configs/stage_decks.json)")
    parser.add_argument("--loadout", default=None,
                        help="frozen skill loadouts (reports/loadouts); static/dynamic only")
    parser.add_argument("--card-version", default=None,
                        help="pin the engine card pool (e.g. v3.3.0); default: engine default pool")
    parser.add_argument("--stage-rules", action="store_true",
                        help="send each stage's special rules (configs/npc/special_rules.json)")
    parser.add_argument("--trace", action="store_true",
                        help="store a compact single-game trace per row (for skill extraction)")
    parser.add_argument("--scratchpad", action="store_true",
                        help="in-game notebook (局内记录板), default off; for A/B only "
                             "(adds the previous note to every prompt and raises max_tokens to 160)")
    parser.add_argument("--round-plan", action="store_true",
                        help="structured per-round plan in the scratchpad (implies --scratchpad)")
    parser.add_argument("--base-guard", action="store_true",
                        help="keep the scripted expert action unless the LLM cites a non-rule skill")
    parser.add_argument("--base-guard-strict", action="store_true",
                        help="with --base-guard: only stage-specific/stage-named skills count")
    parser.add_argument("--rich-render", action="store_true",
                        help="richer state/candidate rendering: effects, auras, reactions, hand count")
    parser.add_argument("--out", default=None)
    return parser


def _seed_list(value: str) -> tuple[int, ...]:
    if "," in value:
        return tuple(int(item) for item in value.split(",") if item.strip())
    return tuple(range(int(value)))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    stages = tuple(stage_names()) if args.stages == "all" else tuple(
        item.strip() for item in args.stages.split(",") if item.strip()
    )
    spec = RunSpec(
        group=args.group,
        stages=stages,
        seeds=_seed_list(args.seeds),
        deck=args.deck,
        workers=max(1, args.workers),
        library_root=args.library,
        top_k=args.top_k,
        model=args.model,
        max_calls=args.max_calls,
        rotation=args.rotation,
        out=args.out or f"data/runs/{args.group}_{args.deck}.jsonl",
        stage_decks_root=args.stage_decks,
        loadout_root=args.loadout,
        card_version=args.card_version,
        stage_rules=args.stage_rules,
        trace=args.trace,
        scratchpad=args.scratchpad,
        rich_render=args.rich_render,
        round_plan=args.round_plan,
        base_guard=args.base_guard,
        base_guard_strict=args.base_guard_strict,
    )
    summary = run(spec)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("valid") else 1


if __name__ == "__main__":
    sys.exit(main())
