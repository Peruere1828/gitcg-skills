"""PvE-v1 gate runner (PLAN.md §4.4 / D17 / D19).

Runs a **decklab-built pool-C deck** against all 16 Mondstadt/Liyue NPC stages
over a grid of seeds, and reports the gate metric — **three-star rate** (all
challenge objectives met) — plus win rate, per-stage and per-objective
breakdowns, and a cluster CI over stages.

It also runs a small set of ablations that separate *deck quality* from *play
quality* (the B5 brief):

* play quality: same pool-C deck x {contestant, contestant_plain, heuristic}
* deck quality: same contestant policy x {pool-C deck, pool-C variant}
* cross-check: the B2/B3 smoke config (``superconduct_aggro`` deck x its own
  scripted expert) re-run under the same seed grid.

Everything streams to ``data/npc/<tag>__<arm>.jsonl`` (gitignored) and resumes by
``task_index``; the aggregate summary goes to ``reports/npc/<tag>.json`` and a
markdown report to ``reports/npc/<tag>.md``.

Usage::

    python -m scripts.run_npc_gate --seeds 24 --workers 8 --tag pve_v1_gate
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import random
from pathlib import Path
from typing import Any, Sequence

from common.paths import data_dir, ensure_dir, reports_dir
from decklab.builder import (
    POOL_C_FILL,
    POOL_C_META_CORE,
    POOL_C_SUPERCONDUCT_CHARACTERS,
    build_deck,
    build_pool_c_superconduct,
    deck_summary,
    load_pool,
)
from envs.decks import deck_spec
from eval.stats import estimate_rate
from npc.eval import NpcEvalResult, NpcEvalSpec, run_npc_eval
from npc.spec import stage_names

GATE_THRESHOLD = 0.60

# A deliberately different pool-C deck (same superconduct trio, defensive fill)
# used only to show that *deck quality* moves the gate number.
POOL_C_V2_FILL: tuple[int, ...] = (
    312001, 312001,  # 冒险家头带 (heal on normal attack)
    321006, 321006,  # 西风大教堂 (end-phase heal)
    322005, 322005,  # 卯师傅 (food support)
    333005, 333005, 333005,  # 甜甜花酿鸡
    333006, 333006,  # 蒙德土豆饼
    311501,          # one weapon so the weapon objective stays reachable
    322003, 322003,
    312005, 312005,
    322011, 322011,
    322012, 322012,
)


def _unique(values: Sequence[Any]) -> tuple[Any, ...]:
    """Order-preserving de-duplication.

    A repeated ``--stages``/``--seeds`` entry would otherwise be run twice,
    inflating the game count and (by counting the same game twice) shrinking the
    Wilson/cluster CI around a rate that never actually got more samples.
    """
    out: list[Any] = []
    seen: set[Any] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return tuple(out)


def _stages(value: str | None) -> tuple[str, ...]:
    if not value or value == "all":
        return _unique(stage_names())
    return _unique(part.strip() for part in value.split(",") if part.strip())


def _seeds(value: str | None, count: int) -> tuple[int, ...]:
    if value:
        return _unique(int(part.strip()) for part in value.split(",") if part.strip())
    return _unique(range(1, int(count) + 1))


def _reproduce_command(
    *,
    seeds_value: str | None,
    seeds: Sequence[int],
    stages: Sequence[str],
    workers: int,
    tag: str,
) -> str:
    """The command that reproduces exactly the stages/seeds that were measured.

    ``--seed-count N`` is only emitted when seeds were derived from the count;
    an explicit ``--seeds 5,7`` is echoed back verbatim (rendering ``--seed-count
    2`` there would re-run seeds 1,2 instead).
    """
    if seeds_value:
        seeds_arg = "--seeds " + ",".join(str(s) for s in seeds)
    else:
        seeds_arg = f"--seed-count {len(seeds)}"
    return (
        f"python -m scripts.run_npc_gate --stages {','.join(stages)} "
        f"{seeds_arg} --workers {workers} --tag {tag}"
    )


def build_decks() -> dict[str, Any]:
    """Return the decks used by the gate and its ablations."""
    primary = build_pool_c_superconduct()
    pool = load_pool()
    variant = build_deck(
        pool,
        POOL_C_SUPERCONDUCT_CHARACTERS,
        name="decklab_poolc_superconduct_defensive",
        core=POOL_C_META_CORE,
        fill=POOL_C_V2_FILL,
    )
    return {
        "poolc_superconduct": primary,
        "poolc_superconduct_defensive": variant,
        "superconduct_aggro": deck_spec("superconduct_aggro"),
    }


def arms(seeds: tuple[int, ...], stages: tuple[str, ...]) -> list[dict[str, Any]]:
    """The gate arm plus the deck-vs-play ablations."""
    return [
        {
            "arm": "gate",
            "deck": "poolc_superconduct",
            "policy0": "contestant",
            "seeds": seeds,
            "stages": stages,
            "note": "PRIMARY GATE: decklab pool-C deck x contestant policy",
        },
        {
            "arm": "play_plain",
            "deck": "poolc_superconduct",
            "policy0": "contestant_plain",
            "seeds": seeds,
            "stages": stages,
            "note": "play-quality: same deck, contestant WITHOUT objective injection",
        },
        {
            "arm": "play_heuristic",
            "deck": "poolc_superconduct",
            "policy0": "heuristic",
            "seeds": seeds,
            "stages": stages,
            "note": "play-quality: same deck, generic heuristic baseline policy",
        },
        {
            "arm": "deck_defensive",
            "deck": "poolc_superconduct_defensive",
            "policy0": "contestant",
            "seeds": seeds,
            "stages": stages,
            "note": "deck-quality: same contestant policy, defensive pool-C variant",
        },
        {
            "arm": "deck_meta",
            "deck": "superconduct_aggro",
            "policy0": "contestant",
            "seeds": seeds,
            "stages": stages,
            "note": "deck-quality: same contestant policy, out-of-pool tuned meta deck (ceiling)",
        },
        {
            "arm": "smoke_ref",
            "deck": "superconduct_aggro",
            "policy0": "expert:superconduct_aggro",
            "seeds": seeds,
            "stages": stages,
            "note": "cross-check: B2/B3 smoke config (tuned deck + scripted expert)",
        },
    ]


def run_arm(arm: dict[str, Any], decks: dict[str, Any], *, tag: str, workers: int) -> NpcEvalResult:
    raw_path = ensure_dir(data_dir("npc")) / f"{tag}__{arm['arm']}.jsonl"
    spec = NpcEvalSpec(
        stage_names=tuple(arm["stages"]),
        contestant=decks[arm["deck"]],
        seeds=tuple(int(s) for s in arm["seeds"]),
        policy0=arm["policy0"],
        tag=f"{tag}:{arm['arm']}",
    )
    return run_npc_eval(spec, raw_path=raw_path, workers=workers)


def _rows_by_stage(rows: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["stage"]), []).append(row)
    return grouped


def cluster_bootstrap_ci(
    rows: Sequence[dict[str, Any]],
    key: str = "three_star",
    *,
    iterations: int = 4000,
    seed: int = 20261012,
) -> dict[str, float]:
    """Cluster (stage) bootstrap CI for an overall rate.

    Resamples whole stages with replacement, so within-stage correlation (same
    stage/enemy lineup) does not shrink the interval the way a naive binomial
    Wilson interval would.
    """
    grouped = _rows_by_stage(rows)
    stage_names_sorted = sorted(grouped)
    if not stage_names_sorted:
        return {"rate": 0.0, "ci_low": 0.0, "ci_high": 0.0, "stages": 0}
    rng = random.Random(seed)
    rates: list[float] = []
    for _ in range(int(iterations)):
        picked = [rng.choice(stage_names_sorted) for _ in stage_names_sorted]
        total = 0
        hits = 0
        for stage in picked:
            games = grouped[stage]
            total += len(games)
            hits += sum(1 for row in games if bool(row[key]))
        rates.append(hits / total if total else 0.0)
    rates.sort()
    overall = sum(1 for row in rows if bool(row[key])) / len(rows)
    low = rates[int(0.025 * len(rates))]
    high = rates[min(len(rates) - 1, int(0.975 * len(rates)))]
    return {"rate": overall, "ci_low": low, "ci_high": high, "stages": len(stage_names_sorted)}


def summarize_arm(result: NpcEvalResult) -> dict[str, Any]:
    summary = result.summary()
    overall = summary["overall"]
    overall["three_star_cluster_ci"] = cluster_bootstrap_ci(result.rows, "three_star")
    overall["truncated"] = sum(1 for row in result.rows if row.get("truncated"))
    overall["fallbacks0"] = sum(int(row.get("fallbacks0", 0)) for row in result.rows)
    overall["io_errors0"] = sum(len(row.get("io_errors0") or ()) for row in result.rows)
    # win-rate cluster CI over stages (same resampling, boolean predicate)
    grouped = _rows_by_stage(result.rows)
    rng = random.Random(7)
    stage_list = sorted(grouped)
    wins_boot: list[float] = []
    for _ in range(4000):
        picked = [rng.choice(stage_list) for _ in stage_list]
        total = sum(len(grouped[s]) for s in picked)
        hits = sum(1 for s in picked for row in grouped[s] if row["winner"] == 0)
        wins_boot.append(hits / total if total else 0.0)
    wins_boot.sort()
    overall["win_cluster_ci"] = {
        "rate": overall["win_rate"],
        "ci_low": wins_boot[int(0.025 * len(wins_boot))],
        "ci_high": wins_boot[min(len(wins_boot) - 1, int(0.975 * len(wins_boot)))],
        "stages": len(stage_list),
    }
    summary["overall"] = overall
    return summary


def write_report(payload: dict[str, Any], *, tag: str) -> dict[str, Path]:
    out_dir = ensure_dir(reports_dir("npc"))
    json_path = out_dir / f"{tag}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = out_dir / f"{tag}.md"
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    return {"json": json_path, "md": md_path}


def _pct(value: float) -> str:
    return f"{100.0 * float(value):.1f}%"


def render_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# PvE-v1 门禁报告（decklab 组牌 × 16 关 × 多种子）")
    lines.append("")
    lines.append(
        f"> 里程碑：PLAN §4.4 `PvE-v1`（D17/D19）。口径：**纯策略**，"
        f"contestant = decklab 池 C 组牌，NPC = 关卡 lineup + greedy `NpcPolicy`。"
    )
    lines.append(f"> 生成：{payload['generated_at']}；tag=`{payload['tag']}`。")
    lines.append("")
    gate = payload["gate"]
    verdict = "**PASS**" if gate["passed"] else "**FAIL**"
    lines.append(
        f"## 门禁判定：{verdict} — 三星达成率 {_pct(gate['three_star_rate'])} "
        f"(门槛 ≥ {_pct(gate['threshold'])})，Wilson95 "
        f"[{_pct(gate['three_star_ci_low'])}, {_pct(gate['three_star_ci_high'])}]，"
        f"cluster95 [{_pct(gate['three_star_cluster_low'])}, {_pct(gate['three_star_cluster_high'])}]"
    )
    lines.append("")
    lines.append(
        f"- 胜率 {_pct(gate['win_rate'])}（Wilson95 "
        f"[{_pct(gate['win_ci_low'])}, {_pct(gate['win_ci_high'])}]）"
    )
    lines.append(
        f"- 样本：{gate['games']} 局 = {gate['n_stages']} 关 × {gate['n_seeds']} 种子；"
        f"error {gate['errors']} / truncated {gate['truncated']}；平均星 {gate['mean_stars']:.2f}"
    )
    lines.append("")
    lines.append("## 逐关（primary gate 组）")
    lines.append("")
    lines.append("| 关卡 | 局数 | 胜 | 胜率 | 三星 | 三星率 | 平均星 | 平均回合 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for stage, stats in gate["stages"].items():
        lines.append(
            f"| {stage} | {stats['games']} | {stats['wins']} | {_pct(stats['win_rate'])} "
            f"| {stats['three_star']} | {_pct(stats['three_star_rate'])} "
            f"| {stats['mean_stars']:.2f} | {stats['mean_rounds']:.1f} |"
        )
    lines.append("")
    lines.append("## 逐目标（primary gate 组）")
    lines.append("")
    lines.append("| 目标 | 出现局数 | 达成 | 达成率 |")
    lines.append("|---|---|---|---|")
    for objective, stats in gate["objectives"].items():
        lines.append(
            f"| {objective} | {stats['games']} | {stats['achieved']} | {_pct(stats['achieved_rate'])} |"
        )
    lines.append("")
    lines.append("## 组牌质量 vs 打牌质量（消融）")
    lines.append("")
    lines.append("| 组 | 牌组 | 策略 | 局数 | 胜率 | 三星率 | Wilson95 | cluster95 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for arm_name, arm in payload["arms"].items():
        overall = arm["overall"]
        lines.append(
            f"| {arm_name} | {arm['deck']} | {arm['policy0']} | {overall['games']} "
            f"| {_pct(overall['win_rate'])} | {_pct(overall['three_star_rate'])} "
            f"| [{_pct(overall['three_star_ci']['ci_low'])}, {_pct(overall['three_star_ci']['ci_high'])}] "
            f"| [{_pct(overall['three_star_cluster_ci']['ci_low'])}, "
            f"{_pct(overall['three_star_cluster_ci']['ci_high'])}] |"
        )
    lines.append("")
    lines.append("## decklab 组牌口径（D19）")
    lines.append("")
    lines.append(payload["decklab_note"])
    lines.append("")
    lines.append("## 与 B2/B3 冒烟的差异")
    lines.append("")
    lines.append(payload["smoke_note"])
    lines.append("")
    lines.append("## 未决问题")
    lines.append("")
    for issue in payload["open_issues"]:
        lines.append(f"- {issue}")
    lines.append("")
    lines.append("## 复现")
    lines.append("")
    lines.append("```bash")
    lines.append(payload["command"])
    lines.append("```")
    lines.append("")
    lines.append("原始逐局记录：`data/npc/<tag>__<arm>.jsonl`（gitignore，可 `--tag` 断点续跑）。")
    lines.append("")
    return "\n".join(lines)


DECKLAB_NOTE = (
    "decklab 当前**没有**学习/Agent 式自动组牌（`decklab/README.md` 待办，"
    "`decklab.sensitivity` 只做固定模板内单卡换卡评测）。本门禁用 "
    "`decklab/builder.py` 的**确定性规则组牌**近似 D19：以固定 meta 牌组"
    "（`superconduct_aggro`）的**池 C 可用核心**（含张数）为骨架，再按人工写定的"
    "池 C 填充序（节奏/aggro + 目标覆盖，含 1 张武器保证 `play_equipment_card` 可达成）"
    "补到 30 张（每卡 ≤2 张、全部来自池 C）。这是「固定 meta deck + decklab 变体」"
    "口径，**不是自动组牌 Agent**；偏差已在此记录。角色：甘雨(1101,冰)+刻晴(1403,雷)+北斗(1405,雷)。"
)

# B2/B3 published smoke (reports/npc/npc_pve_smoke_v1.json): 16 stages x 3 seeds.
SMOKE_REFERENCE = {
    "source": "reports/npc/npc_pve_smoke_v1.json (PvE-B2/B3)",
    "games": 48,
    "win_rate": 0.979,
    "three_star_rate": 0.625,
    "note": "contestant = 脚本专家 superconduct_aggro（自带 deck 专属规则，不打装备牌）",
}

OPEN_ISSUES = (
    "`swan` 的 `objectives_verified=false`（eurogamer 只列 1 条目标）→ 其「三星」等于「赢下」，"
    "会把总三星率略微抬高；门禁口径未按 3 条目标标准化。",
    "`shitou` 的 `win_within_rounds.n=44` 未核实（疑为 4/14 笔误）→ 该目标实际从不失败，"
    "对该关三星率无约束。",
    "关卡特殊规则未在引擎内模拟：`artifact_cards_free`（蒂玛乌斯）、`chef_mao_support_present` + "
    "`pyro_application_on_action_phase_start`（卯师傅）；对局按普通规则跑，蒂玛乌斯/卯师傅偏难/偏易需另立项。",
    "decklab 无 Agent 式自动组牌：本门禁用 `decklab/builder.py` 规则组牌近似（偏差见上）。"
    "后续若实现学习/Agent 组牌，需重跑本门禁。",
    "PvE 机制近似（D17）：NPC 无手牌/无骰子/不打出行动牌；引擎只有全局手牌/骰子配置，"
    "NPC 仍持有被 `NpcPolicy` 闲置的手牌与骰子。",
    "`win_within_rounds`（多数 n=5）是主要失分项：打装备/事件牌会牺牲节奏，"
    "「目标达成」与「速胜」存在张力，是当前三星率的主要上限。",
)


def _smoke_note(payload: dict[str, Any]) -> str:
    rerun = payload["arms"].get("smoke_ref", {}).get("overall", {})
    ref = payload["smoke_reference"]
    return (
        f"B2/B3 冒烟（`{ref['source']}`）：{ref['games']} 局 = 16 关 × 3 种子，"
        f"{ref['note']}，win {ref['win_rate']:.3f} / 三星 {ref['three_star_rate']:.3f}。"
        f"本门禁把同一配置在 {payload['n_seeds']} 种子下复跑（`smoke_ref` 组）："
        f"win {rerun.get('win_rate', 0.0):.3f} / 三星 {rerun.get('three_star_rate', 0.0):.3f}。"
        f"primary 门禁组换成池 C 组牌 + contestant 策略；两组差异来自① 种子数（3→"
        f"{payload['n_seeds']}）② deck（`superconduct_aggro` → 池 C 组牌）③ 策略"
        f"（脚本专家 → contestant：装备/事件目标由 0% 提升，见逐目标表）。"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PvE-v1 gate: decklab deck x 16 stages x seeds")
    parser.add_argument("--stages", default="all")
    parser.add_argument("--seeds", default=None, help="explicit comma list")
    parser.add_argument("--seed-count", type=int, default=24, help="seeds 1..N when --seeds absent")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--tag", default="pve_v1_gate")
    args = parser.parse_args(argv)

    stages = _stages(args.stages)
    seeds = _seeds(args.seeds, args.seed_count)
    decks = build_decks()

    arm_results: dict[str, Any] = {}
    arm_meta: dict[str, dict[str, Any]] = {}
    for arm in arms(seeds, stages):
        result = run_arm(arm, decks, tag=args.tag, workers=args.workers)
        summary = summarize_arm(result)
        arm_results[arm["arm"]] = summary
        arm_meta[arm["arm"]] = {
            "deck": arm["deck"],
            "policy0": arm["policy0"],
            "note": arm["note"],
            "raw": str(data_dir("npc") / f"{args.tag}__{arm['arm']}.jsonl"),
            "resumed_matches": result.metadata.get("resumed_matches"),
        }

    primary = arm_results["gate"]
    overall = primary["overall"]
    gate_passed = float(overall["three_star_rate"]) >= GATE_THRESHOLD

    command = _reproduce_command(
        seeds_value=args.seeds,
        seeds=seeds,
        stages=stages,
        workers=args.workers,
        tag=args.tag,
    )
    payload: dict[str, Any] = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "tag": args.tag,
        "command": command,
        "threshold": GATE_THRESHOLD,
        "seeds": list(seeds),
        "n_stages": len(stages),
        "n_seeds": len(seeds),
        "decks": {
            name: deck_summary(deck) for name, deck in decks.items()
        },
        "decklab_note": DECKLAB_NOTE,
        "gate": {
            "passed": gate_passed,
            "threshold": GATE_THRESHOLD,
            "three_star_rate": overall["three_star_rate"],
            "three_star_ci_low": overall["three_star_ci"]["ci_low"],
            "three_star_ci_high": overall["three_star_ci"]["ci_high"],
            "three_star_cluster_low": overall["three_star_cluster_ci"]["ci_low"],
            "three_star_cluster_high": overall["three_star_cluster_ci"]["ci_high"],
            "win_rate": overall["win_rate"],
            "win_ci_low": overall["win_rate_ci"]["ci_low"],
            "win_ci_high": overall["win_rate_ci"]["ci_high"],
            "games": overall["games"],
            "n_stages": len(stages),
            "n_seeds": len(seeds),
            "errors": overall["errors"],
            "truncated": overall["truncated"],
            "mean_stars": overall["mean_stars"],
            "stages": primary["stages"],
            "objectives": primary["objectives"],
        },
        "arms": {
            name: {
                "overall": arm_results[name]["overall"],
                "stages": arm_results[name]["stages"],
                "objectives": arm_results[name]["objectives"],
                **arm_meta[name],
            }
            for name in arm_results
        },
        "arm_summaries": arm_results,
        "raw_line_counts": {
            name: _count_lines(Path(meta["raw"])) for name, meta in arm_meta.items()
        },
    }
    payload["smoke_reference"] = SMOKE_REFERENCE
    payload["open_issues"] = list(OPEN_ISSUES)
    payload["smoke_note"] = _smoke_note(payload)
    paths = write_report(payload, tag=args.tag)
    print(
        f"[npc-gate] {'PASS' if gate_passed else 'FAIL'} "
        f"three_star={overall['three_star_rate']:.3f} "
        f"win={overall['win_rate']:.3f} games={overall['games']} -> {paths['json']}"
    )
    return 0


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
