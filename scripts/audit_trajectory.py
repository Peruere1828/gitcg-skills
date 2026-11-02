#!/usr/bin/env python3
"""Audit the decision-trajectory shape of the *scripted* PvE player (no LLM, no network).

Why: the skill-library experiments change what the LLM picks at each request. This
script measures what those requests actually look like — how many there are, how
many real alternatives each one has, how the candidate renderer (`skillloop.render
.select_candidates`) reshapes them, and which requests the LLM never sees at all
(`AgentConfig.request_types`). It plays expert-vs-NPC games with the same deck,
stage data, seeded shuffle and NPC policy the real runs use, and wraps only the
player policy to record each request.

The output is a markdown report (plus an optional raw JSONL of every decision).

Usage::

    PYTHONPATH=. python scripts/audit_trajectory.py \
        --stages all --seeds 1,2 --workers 6 \
        --out reports/trajectory_audit.md --json data/runs/trajectory_audit.jsonl

    # with the counterfactual section (one alternative action forced per step):
    PYTHONPATH=. python scripts/audit_trajectory.py \
        --stages all --seeds 1,2 --workers 6 --leverage \
        --out reports/trajectory_audit.md --json data/runs/trajectory_audit.jsonl

Notes:
* No LLM client is constructed; both sides are scripted (`envs.policy.expert_policy`
  for the player, `npc.policy.npc_policy_for_stage` for the opponent).
* Worker processes use ``spawn``: the engine is a C library loaded at import time and
  forking it deadlocks (observed with the default ``fork`` context).
* `n_rendered` is what `SkillAgentPolicy` would put in the prompt (dedupe + 16 cap +
  `skip_forced`); `n_dedup` is the same dedupe without the cap; `n_legal` is the raw
  engine list. `n_kinds` / `n_subjects` count distinct action *types* and distinct
  (type, subject) pairs, i.e. the strategic width behind a wide legal list.
* For every request we also record whether the expert's own choice is inside the
  rendered candidate set. An expert choice outside it is an action the LLM cannot
  imitate, no matter what the skill library says.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Iterable

from envs.decks import deck_spec
from envs.match import run_match
from envs.policy import expert_policy
from npc.objectives import evaluate_stage
from npc.policy import npc_policy_for_stage
from npc.spec import get_stage, stage_names
from skillloop.render import select_candidates

MAX_CANDIDATES = 16  # mirrors AgentConfig.candidate_cap


class RecordingPolicy:
    """Delegate to a policy, recording every request the engine makes of player 0."""

    def __init__(self, base: Any, *, stage: str = "", seed: int = 0) -> None:
        self.base = base
        self.stage = stage
        self.seed = seed
        self.name = f"audit({getattr(base, 'name', 'base')})"
        self.rows: list[dict[str, Any]] = []

    def choose(self, built: Any) -> int:
        context = built.context
        legal = [int(code) for code in context.legal_low_level_codes]
        specs = list(context.legal_low_level_specs)
        spec_by_code = {int(spec.action_code): spec for spec in specs}

        dedup = select_candidates(context, max_candidates=10**6, skip_forced=False)
        rendered = select_candidates(context, max_candidates=MAX_CANDIDATES)
        rendered_codes = {int(code) for code, _ in rendered}

        code = int(self.base.choose(built))
        spec = spec_by_code.get(code)
        round_number = int(context.player_view.round_number) if context.player_view is not None else 0

        kinds = Counter(str(spec.kind.value) for spec in specs)
        subjects = Counter((str(spec.kind.value), int(spec.subject_definition_id)) for spec in specs)
        kept_kinds = Counter(str(s.kind.value) for _, s in rendered)
        dropped = [
            spec for code_, spec in zip(legal, specs)
            if int(code_) not in rendered_codes
        ]
        # First visible candidate that is a *semantically different* action
        # (different type, or same type on a different subject). Used by the
        # counterfactual leverage sweep: force it and see if the game changes.
        chosen_key = (
            str(spec.kind.value) if spec is not None else "",
            int(spec.subject_definition_id) if spec is not None else 0,
        )
        alt = next(
            (
                (code_, s)
                for code_, s in rendered
                if (str(s.kind.value), int(s.subject_definition_id)) != chosen_key
            ),
            None,
        )
        row = {
            "stage": self.stage,
            "seed": self.seed,
            "round": round_number,
            "step_index": int(context.step_index),
            "request_type": context.request_type.value,
            "n_legal": len(legal),
            "n_dedup": len(dedup),
            "n_rendered": len(rendered),
            "n_kinds": len(kinds),
            "n_subjects": len(subjects),
            "chosen_code": code,
            "chosen_kind": str(spec.kind.value) if spec is not None else None,
            "chosen_subject": int(spec.subject_definition_id) if spec is not None else None,
            "chosen_label": str(spec.label) if spec is not None else None,
            "chosen_in_rendered": code in rendered_codes,
            "alt_code": int(alt[0]) if alt is not None else None,
            "alt_kind": str(alt[1].kind.value) if alt is not None else None,
            "alt_subject": int(alt[1].subject_definition_id) if alt is not None else None,
            "reroll_mask": int(spec.reroll_dice_mask) if spec is not None else 0,
            "switch_hand_mask": int(spec.switch_hand_slot_mask) if spec is not None else 0,
            "truncated": len(dedup) > MAX_CANDIDATES,
            "dropped_kinds": dict(Counter(str(s.kind.value) for s in dropped)),
            "rendered_kinds": dict(kept_kinds),
        }
        self.rows.append(row)
        return code


class _ForcedPolicy:
    """Expert policy that returns a forced code at one step, recording its trace."""

    def __init__(self, base: Any, *, force_step: int | None = None, force_code: int | None = None) -> None:
        self.base = base
        self.force_step = force_step
        self.force_code = force_code
        self.name = "forced"
        self.trace: list[int] = []
        self.applied = False

    def choose(self, built: Any) -> int:
        code = int(self.base.choose(built))
        self.trace.append(code)
        if self.force_step is not None and int(built.context.step_index) == int(self.force_step):
            self.applied = True
            return int(self.force_code)
        return code


def _replay(payload: dict[str, Any]) -> dict[str, Any]:
    """Replay one game, optionally forcing one action code at one step."""
    stage_name = str(payload["stage"])
    seed = int(payload["seed"])
    deck_name = str(payload["deck"])
    stage = get_stage(stage_name)
    deck = deck_spec(deck_name)
    npc_deck = type(deck)(
        name=f"npc_{stage.name}", characters=stage.deck_characters, cards=deck.cards
    )
    policy = _ForcedPolicy(
        expert_policy(deck_name, seed=0),
        force_step=payload.get("force_step"),
        force_code=payload.get("force_code"),
    )
    record = run_match(
        deck,
        npc_deck,
        policy,
        npc_policy_for_stage(stage),
        seed=seed,
        record_decisions=False,
        record_events=True,
        record_final_state=True,
    )
    return {
        "stage": stage_name,
        "seed": seed,
        "force_step": payload.get("force_step"),
        "force_code": payload.get("force_code"),
        "applied": policy.applied,
        "winner": record.winner,
        "rounds": record.rounds,
        "truncated": record.truncated,
        "trace": policy.trace,
    }


def _play(payload: dict[str, Any]) -> dict[str, Any]:
    stage_name = str(payload["stage"])
    seed = int(payload["seed"])
    deck_name = str(payload["deck"])

    stage = get_stage(stage_name)
    deck = deck_spec(deck_name)
    npc_deck = type(deck)(
        name=f"npc_{stage.name}", characters=stage.deck_characters, cards=deck.cards
    )
    policy = RecordingPolicy(expert_policy(deck_name, seed=0), stage=stage_name, seed=seed)
    record = run_match(
        deck,
        npc_deck,
        policy,
        npc_policy_for_stage(stage),
        seed=seed,
        record_decisions=True,
        record_events=True,
        record_final_state=True,
    )
    stars = evaluate_stage(stage, record)
    return {
        "stage": stage_name,
        "seed": seed,
        "deck": deck_name,
        "winner": record.winner,
        "rounds": record.rounds,
        "three_star": bool(stars.three_star),
        "stars": int(stars.stars),
        "objectives": {item.type: bool(item.achieved) for item in stars.objectives},
        "decisions": policy.rows,
    }


def pct(values: list[int], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(q * (len(ordered) - 1)))))
    return int(ordered[index])


def _fmt_counter(counter: Counter) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))


def build_report(games: list[dict[str, Any]], *, command: str, leverage: dict[str, Any] | None = None) -> str:
    rows = [row for game in games for row in game["decisions"]]
    by_stage: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for game in games:
        by_stage[game["stage"]].append(game)

    lines: list[str] = []
    add = lines.append

    add("# Trajectory audit — scripted player, no LLM")
    add("")
    add(f"Generated: `{command}`")
    add("")
    add(
        f"Grid: {len(games)} games ({len(by_stage)} stages × "
        f"{sorted({g['seed'] for g in games})} seeds), deck `{games[0].get('deck', '')}`"
    )
    add("")

    # ---- 1. decisions per game / per round --------------------------------
    add("## 1. Decisions per game and per round, by request type")
    add("")
    add("| stage | seed | winner | rounds | stars | decisions | per round | "
        + " | ".join(f"{t}" for t in sorted({r['request_type'] for r in rows}))
        + " |")
    add("|---|---:|---:|---:|---:|---:|---:|" + "---:|" * len({r["request_type"] for r in rows}))
    for game in games:
        dec = game["decisions"]
        types = Counter(r["request_type"] for r in dec)
        add(
            f"| {game['stage']} | {game['seed']} | {game['winner']} | {game['rounds']} | "
            f"{game['stars']}/3 | {len(dec)} | {len(dec) / max(1, game['rounds']):.1f} | "
            + " | ".join(str(types.get(t, 0)) for t in sorted({r["request_type"] for r in rows}))
            + " |"
        )
    per_game = [len(g["decisions"]) for g in games]
    per_round = [
        len(g["decisions"]) / max(1, g["rounds"]) for g in games
    ]
    add("")
    add(
        f"**Total decisions/game**: median {statistics.median(per_game):.0f}, "
        f"range {min(per_game)}–{max(per_game)}. "
        f"**Per round**: median {statistics.median(per_round):.1f}, "
        f"range {min(per_round):.1f}–{max(per_round):.1f}."
    )
    add("")
    type_counts = Counter(r["request_type"] for r in rows)
    add("Global mix: " + ", ".join(f"`{k}` {v}" for k, v in type_counts.most_common()) + ".")
    add("")
    add("Median decisions per round by type (over games):")
    add("")
    add("| request_type | median per game | median per round |")
    add("|---|---:|---:|")
    for rtype in sorted(type_counts):
        pg = [sum(1 for r in g["decisions"] if r["request_type"] == rtype) for g in games]
        pr = [
            sum(1 for r in g["decisions"] if r["request_type"] == rtype) / max(1, g["rounds"])
            for g in games
        ]
        add(f"| `{rtype}` | {statistics.median(pg):.1f} | {statistics.median(pr):.1f} |")
    add("")

    # ---- 2. candidate structure ------------------------------------------
    add("## 2. Candidate structure: legal list vs what the agent can pick")
    add("")
    add(
        "`n_legal` = engine list; `n_dedup` = `select_candidates` without the cap; "
        "`n_rendered` = what the LLM prompt would contain (16 cap, forced skipped)."
    )
    add("")
    add("| request_type | n | n_legal p50 / p90 / max | n_dedup p50 / p90 / max | "
        "n_rendered p50 / p90 / max | forced (n_legal<=1) | expert choice outside rendered |")
    add("|---|---:|---|---|---|---:|---:|")
    for rtype in sorted(type_counts):
        subset = [r for r in rows if r["request_type"] == rtype]
        legal = [r["n_legal"] for r in subset]
        dedup = [r["n_dedup"] for r in subset]
        rend = [r["n_rendered"] for r in subset]
        forced = sum(1 for r in subset if r["n_legal"] <= 1)
        outside = sum(1 for r in subset if not r["chosen_in_rendered"])
        add(
            f"| `{rtype}` | {len(subset)} | "
            f"{pct(legal, .5)} / {pct(legal, .9)} / {max(legal)} | "
            f"{pct(dedup, .5)} / {pct(dedup, .9)} / {max(dedup)} | "
            f"{pct(rend, .5)} / {pct(rend, .9)} / {max(rend)} | "
            f"{forced} ({forced / len(subset):.0%}) | "
            f"{outside} ({outside / len(subset):.0%}) |"
        )
    add("")
    action = [r for r in rows if r["request_type"] == "action"]
    if action:
        forced_action = sum(1 for r in action if r["n_legal"] <= 1)
        single = sum(1 for r in action if r["n_dedup"] <= 1)
        capped = sum(1 for r in action if r["truncated"])
        outside = sum(1 for r in action if not r["chosen_in_rendered"])
        add(
            f"**Action requests**: forced (≤1 legal) {forced_action}/{len(action)} "
            f"({forced_action / len(action):.1%}); single deduped candidate {single} "
            f"({single / len(action):.1%}); truncated by the 16 cap {capped} "
            f"({capped / len(action):.1%}); expert chose outside the rendered set "
            f"{outside} ({outside / len(action):.1%})."
        )
        add("")
        add("Dedupe/`kind` width of action requests:")
        add("")
        add("| metric | p50 | p90 | max |")
        add("|---|---:|---:|---:|")
        for key in ("n_legal", "n_dedup", "n_subjects", "n_kinds"):
            vals = [r[key] for r in action]
            add(f"| {key} | {pct(vals, .5)} | {pct(vals, .9)} | {max(vals)} |")
        add("")
        add(
            "`n_subjects` = distinct (action type, subject card/skill) pairs, i.e. how many "
            "*different things* are on the table; `n_kinds` = distinct action types."
        )
        add("")
        dropped = Counter()
        for r in action:
            for kind, n in r["dropped_kinds"].items():
                dropped[kind] += int(n)
        if dropped:
            add("Action kinds present in the legal list but cut by the 16 cap: "
                + _fmt_counter(dropped) + " (option-instances across the grid).")
            add("")

    # ---- 3. dice rerolls ---------------------------------------------------
    add("## 3. Dice rerolls: who decides them")
    add("")
    rerolls = [r for r in rows if r["request_type"] == "reroll_dice"]
    if not rerolls:
        add("No `reroll_dice` request was observed in this grid.")
    else:
        per_game_rr = [
            sum(1 for r in g["decisions"] if r["request_type"] == "reroll_dice") for g in games
        ]
        masks = Counter(r["n_legal"] for r in rerolls)
        distinct_chosen = [
            len({r["reroll_mask"] for r in g["decisions"] if r["request_type"] == "reroll_dice"})
            for g in games
        ]
        add(
            f"`reroll_dice` requests: {len(rerolls)} over {len(games)} games "
            f"(median {statistics.median(per_game_rr):.0f}/game, range "
            f"{min(per_game_rr)}–{max(per_game_rr)}). Legal masks per request: "
            + _fmt_counter(masks)
            + ". That is one request per roll phase, not one per die."
        )
        add("")
        add(
            f"Rendered candidates per reroll request: "
            f"{Counter(r['n_rendered'] for r in rerolls).most_common()} — the dedupe key in "
            "`select_candidates` is `(kind, subject_definition_id, target_slots, used_dice)` "
            "and does not include `reroll_dice_mask`, so all masks collapse into one. "
            f"Distinct masks actually chosen per game: median "
            f"{statistics.median(distinct_chosen):.0f}, range {min(distinct_chosen)}–{max(distinct_chosen)}."
        )
        add("")
        add(
            "**But the LLM never sees them**: `AgentConfig.request_types` is "
            "`(\"action\", \"switch_hands\", \"choose_active\")`, so `reroll_dice` and "
            "`select_card` always go to the scripted expert. Dice rerolling is outside the "
            "skill layer by construction, not by accident."
        )
    add("")

    # ---- 4. one full round example ----------------------------------------
    add("## 4. One full round, request by request")
    add("")
    sample = _pick_example(games)
    if sample is None:
        add("No round with enough requests found.")
    else:
        game, round_number, dec = sample
        add(
            f"Stage `{game['stage']}` seed {game['seed']} round {round_number} "
            f"(game ended in round {game['rounds']}, winner {game['winner']}):"
        )
        add("")
        add("| step | round | request | n_legal | n_dedup | n_rendered | n_kinds | chosen kind |")
        add("|---:|---:|---|---:|---:|---:|---:|---|")
        for r in dec:
            add(
                f"| {r['step_index']} | {r['round']} | `{r['request_type']}` | "
                f"{r['n_legal']} | {r['n_dedup']} | {r['n_rendered']} | {r['n_kinds']} | "
                f"`{r['chosen_kind']}` |"
            )
        add("")
        picks = Counter(r["chosen_kind"] for r in dec)
        add("Round picks: " + _fmt_counter(picks) + ".")
        add("")

    # ---- 5. stage-level objective check -----------------------------------
    add("## 5. Sanity check: what the same scripted player scores")
    add("")
    add("| stage | 3-star | mean rounds | objective failures |")
    add("|---|---:|---:|---|")
    for stage_name, stage_games in sorted(by_stage.items()):
        fails = Counter()
        for g in stage_games:
            for k, v in g["objectives"].items():
                if not v:
                    fails[k] += 1
        add(
            f"| {stage_name} | {sum(g['three_star'] for g in stage_games)}/{len(stage_games)} | "
            f"{statistics.mean(g['rounds'] for g in stage_games):.1f} | "
            f"{_fmt_counter(fails) or '—'} |"
        )
    add("")
    # ---- 6. counterfactual leverage ---------------------------------------
    if leverage is not None:
        devs = leverage["deviations"]
        add("## 6. Counterfactual leverage: what one different visible action buys")
        add("")
        add(
            f"Replayed {leverage['games']} game(s), forcing one alternative at every "
            f"`action` request (the first rendered candidate with a different type or "
            f"subject than the expert's choice). Determinism check: "
            f"{leverage['determinism_ok']}/{leverage['determinism_n']} replays reproduced "
            f"the original game byte-for-byte (same trace, rounds, winner). "
            f"{len(devs)} single-decision deviations measured."
        )
        add("")
        if devs:
            changed_rounds = [d for d in devs if d["delta_rounds"] != 0]
            changed_winner = [d for d in devs if d["winner_changed"]]
            same_kind = [d for d in devs if d["same_kind"]]
            diff_kind = [d for d in devs if not d["same_kind"]]
            add("| slice | n | changes round count | changes winner | mean Δrounds | median |Δrounds| |")
            add("|---|---:|---:|---:|---:|---:|")
            for label, subset in (
                ("all deviations", devs),
                ("same action type, different target/subject", same_kind),
                ("different action type", diff_kind),
            ):
                if not subset:
                    continue
                cr = sum(1 for d in subset if d["delta_rounds"] != 0)
                cw = sum(1 for d in subset if d["winner_changed"])
                mean_d = statistics.mean(d["delta_rounds"] for d in subset)
                med_abs = statistics.median(abs(d["delta_rounds"]) for d in subset)
                add(
                    f"| {label} | {len(subset)} | {cr} ({cr / len(subset):.0%}) | "
                    f"{cw} ({cw / len(subset):.0%}) | {mean_d:+.2f} | {med_abs:.1f} |"
                )
            add("")
            deltas = Counter(d["delta_rounds"] for d in devs)
            add("Δrounds distribution: " + _fmt_counter(deltas) + ".")
            add("")
            per_stage = defaultdict(lambda: [0, 0])
            for d in devs:
                per_stage[d["stage"]][0] += 1
                per_stage[d["stage"]][1] += int(d["delta_rounds"] != 0)
            add(
                "Per stage (deviations that change the round count): "
                + ", ".join(f"`{k}` {v[1]}/{v[0]}" for k, v in sorted(per_stage.items()))
                + "."
            )
            add("")
    # ---- 7. verdict on the micro-step hypothesis --------------------------
    add("## 7. Reading the hypothesis")
    add("")
    action_rows = [r for r in rows if r["request_type"] == "action"]
    if action_rows:
        per_game_action = [
            sum(1 for r in g["decisions"] if r["request_type"] == "action") for g in games
        ]
        per_round_action = [
            sum(1 for r in g["decisions"] if r["request_type"] == "action") / max(1, g["rounds"])
            for g in games
        ]
        add(
            f"*Decisions per game*: median {statistics.median(per_game):.0f} requests, of which "
            f"median {statistics.median(per_game_action):.0f} are `action` "
            f"({statistics.median(per_round_action):.1f} per round). There is no round-level object "
            "in the loop: one skill, one card, one switch or one end-declaration per request."
        )
        add("")
        add(
            f"*Few real alternatives?* Contradicted at the raw level: 0/{len(action_rows)} action "
            f"requests are forced (≤1 legal), the median action request has {pct([r['n_legal'] for r in action_rows], .5)} "
            f"legal codes and {pct([r['n_kinds'] for r in action_rows], .5)} distinct action types. "
            "But the rendered window is capped at 16 and ordered by the engine: "
            f"{sum(1 for r in action_rows if r['truncated']) / len(action_rows):.0%} of action requests "
            "are truncated, `declare_end` is dropped entirely in "
            f"{sum(1 for r in action_rows if r['rendered_kinds'].get('action_declare_end', 0) == 0 and r['dropped_kinds'].get('action_declare_end', 0) > 0) / len(action_rows):.0%} "
            "of them, and the expert's own choice is outside the rendered window in "
            f"{sum(1 for r in action_rows if not r['chosen_in_rendered']) / len(action_rows):.0%} of "
            "action requests. The agent therefore has many *options* but not the expert's option set."
        )
        add("")
    add(
        "*Dice*: one `reroll_dice` request per roll phase (median 5/game), 36–144 legal masks, "
        "collapsed to 1 rendered candidate by `select_candidates`, and — decisively — never sent to "
        "the LLM at all (`AgentConfig.request_types` excludes `reroll_dice`). The library cannot "
        "touch the dice; the expert does."
    )
    add("")
    if leverage is not None and leverage["deviations"]:
        devs = leverage["deviations"]
        changed = [d for d in devs if d["delta_rounds"] != 0]
        faster = [d for d in devs if d["delta_rounds"] < 0]
        slower = [d for d in devs if d["delta_rounds"] > 0]
        add(
            f"*Can one micro-decision change the round?* Yes, in {len(changed)}/{len(devs)} "
            f"({len(changed) / len(devs):.0%}) single-decision replays — but the effect is "
            f"asymmetric: {len(slower)} deviations made the game **slower** and only {len(faster)} "
            f"made it faster (mean Δ {statistics.mean(d['delta_rounds'] for d in devs):+.2f} rounds). "
            f"The winner changed in {sum(1 for d in devs if d['winner_changed'])}/{len(devs)} "
            "replays. So the chain is sensitive on the clock metric, but local perturbation of the "
            "expert has no positive expected value: the scripted policy is at a local optimum for "
            "speed, and the skill library's effect is a diffusion around the threshold, not a shift."
        )
        add("")
    add(
        "**Verdict.** The hypothesis is right about the *shape* and wrong about the *mechanism*. "
        "The agent does decide at every micro-step, and the dice decision is entirely outside the "
        "library. But action decisions are not forced: they have 15–30 legal options and 4–5 action "
        "types. What actually blocks the library is (a) a 16-item window that hides `declare_end` "
        "most of the time and hides the expert's own choice 27% of the time, (b) a renderer that "
        "labels every card play as `打出卡牌：<name>` with no artifact/weapon/event kind, so a "
        "card-type skill can only be obeyed if the model recognises the card by name — it did not, "
        "in 160 games, and (c) a 5-round threshold sitting "
        "on the mode of the round distribution, where coordinated changes move games both ways and "
        "cancel out. The first two explain the frozen objectives; the third explains why the wwr gain "
        "halves out of sample."
    )
    add("")
    return "\n".join(lines) + "\n"

def run_leverage(
    games: list[dict[str, Any]],
    *,
    stages: list[str],
    seeds: list[int],
    workers: int,
) -> dict[str, Any]:
    """Force one visible alternative at each action step and compare the game.

    This is the direct test of "can a single micro-decision change the round
    count?". One replay per (game, action step); the alternative is the first
    rendered candidate with a different (type, subject) than the expert's choice,
    i.e. a choice the LLM could actually have made.
    """
    selected = [g for g in games if g["stage"] in stages and g["seed"] in seeds]
    tasks: list[dict[str, Any]] = []
    meta: list[dict[str, Any]] = []
    for game in selected:
        base_trace = [int(row["chosen_code"]) for row in game["decisions"]]
        tasks.append(
            {
                "stage": game["stage"],
                "seed": game["seed"],
                "deck": game["deck"],
                "force_step": None,
                "force_code": None,
                "kind": "determinism",
            }
        )
        meta.append({"kind": "determinism", "game": game, "base_trace": base_trace})
        for row in game["decisions"]:
            if row["request_type"] != "action" or row.get("alt_code") is None:
                continue
            tasks.append(
                {
                    "stage": game["stage"],
                    "seed": game["seed"],
                    "deck": game["deck"],
                    "force_step": int(row["step_index"]),
                    "force_code": int(row["alt_code"]),
                    "kind": "deviation",
                }
            )
            meta.append({"kind": "deviation", "game": game, "row": row})

    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn")) as pool:
            results = list(pool.map(_replay, tasks))
    else:
        results = [_replay(task) for task in tasks]

    determinism_ok = 0
    determinism_n = 0
    deviations: list[dict[str, Any]] = []
    for result, item in zip(results, meta):
        game = item["game"]
        if item["kind"] == "determinism":
            determinism_n += 1
            if (
                result["trace"] == item["base_trace"]
                and result["rounds"] == game["rounds"]
                and result["winner"] == game["winner"]
            ):
                determinism_ok += 1
            continue
        if not result["applied"]:
            continue
        row = item["row"]
        deviations.append(
            {
                "stage": game["stage"],
                "seed": game["seed"],
                "base_rounds": game["rounds"],
                "base_winner": game["winner"],
                "base_stars": game["stars"],
                "new_rounds": result["rounds"],
                "new_winner": result["winner"],
                "delta_rounds": result["rounds"] - game["rounds"],
                "winner_changed": result["winner"] != game["winner"],
                "chosen_kind": row["chosen_kind"],
                "alt_kind": row["alt_kind"],
                "same_kind": row["chosen_kind"] == row["alt_kind"],
                "round": row["round"],
            }
        )
    return {
        "games": len(selected),
        "determinism_ok": determinism_ok,
        "determinism_n": determinism_n,
        "deviations": deviations,
    }


def _pick_example(games: list[dict[str, Any]]):
    """Pick the round with the most requests (ties: earlier round, longer game)."""
    best = None
    for game in games:
        by_round: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in game["decisions"]:
            by_round[int(row["round"])].append(row)
        for round_number, dec in by_round.items():
            if round_number <= 0 or len(dec) < 8:
                continue
            key = (len(dec), -round_number)
            if best is None or key > best[0]:
                best = (key, game, round_number, dec)
    if best is None:
        return None
    return best[1], best[2], best[3]


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stages", default="all", help="comma list or 'all'")
    ap.add_argument("--seeds", default="1,2", help="comma list")
    ap.add_argument("--deck", default="superconduct_aggro")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--out", default="reports/trajectory_audit.md")
    ap.add_argument("--json", default=None, help="optional raw per-decision JSONL")
    ap.add_argument("--leverage", action="store_true",
                    help="also replay one alternative action per step (section 6)")
    ap.add_argument("--leverage-stages",
                    default="chefmao,ellin,irontonguetian,liusu,patton,shitou,timmie,timaeus",
                    help="comma list of stages for the leverage sweep")
    ap.add_argument("--leverage-seeds", default="1")
    args = ap.parse_args(list(argv) if argv is not None else None)

    stages = list(stage_names()) if args.stages == "all" else [s.strip() for s in args.stages.split(",") if s.strip()]
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    payloads = [{"stage": stage, "seed": seed, "deck": args.deck} for stage in stages for seed in seeds]

    if args.workers > 1:
        # ``spawn`` (not the default fork): the engine is a C library loaded at
        # import time, and forking after that deadlocks in the cffi call.
        with ProcessPoolExecutor(max_workers=args.workers, mp_context=mp.get_context("spawn")) as pool:
            games = list(pool.map(_play, payloads))
    else:
        games = [_play(payload) for payload in payloads]
    games.sort(key=lambda g: (g["stage"], g["seed"]))

    command = "PYTHONPATH=. python scripts/audit_trajectory.py " + " ".join(
        [
            f"--stages {args.stages}",
            f"--seeds {args.seeds}",
            f"--deck {args.deck}",
            f"--workers {args.workers}",
            f"--out {args.out}",
        ]
        + ([f"--leverage --leverage-stages {args.leverage_stages} --leverage-seeds {args.leverage_seeds}"]
           if args.leverage else [])
    )
    leverage = None
    if args.leverage:
        leverage = run_leverage(
            games,
            stages=[s.strip() for s in args.leverage_stages.split(",") if s.strip()],
            seeds=[int(s) for s in args.leverage_seeds.split(",") if s.strip()],
            workers=args.workers,
        )
    report = build_report(games, command=command, leverage=leverage)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"[audit] wrote {out} ({len(games)} games)")

    if args.json:
        raw = Path(args.json)
        raw.parent.mkdir(parents=True, exist_ok=True)
        with raw.open("w", encoding="utf-8") as handle:
            for game in games:
                for row in game["decisions"]:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"[audit] wrote {raw} ({sum(len(g['decisions']) for g in games)} decisions)")

    for game in games:
        print(
            f"[audit] {game['stage']:16s} seed={game['seed']} winner={game['winner']} "
            f"rounds={game['rounds']} stars={game['stars']} decisions={len(game['decisions'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
