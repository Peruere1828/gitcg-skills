#!/usr/bin/env python3
"""Build the two committed prompt samples from a real ``GITCG_LLM_TRACE`` file.

The samples in ``reports/prompts/`` are evidence, not illustrations: every fenced
block must be byte-identical to what ``common/llm.py`` sent. This script only
formats a trace into markdown; it never re-renders a prompt from a different code
path (that drift is exactly what made the old ``*_render_*`` files unauditable).

Usage::

    python -m scripts.prompt_samples decision \\
        --trace /tmp/opencode/trace_decision.jsonl \\
        --rows /tmp/opencode/decision_sample.jsonl \\
        --command 'GITCG_LLM_TRACE=... python -m skillloop.run --group static ...' \\
        --out reports/prompts/decision_prompt.md

    python -m scripts.prompt_samples reflection \\
        --trace /tmp/opencode/trace_extract.jsonl \\
        --rows /tmp/opencode/reflection_rows.jsonl \\
        --base data/skills/dyn_v4 \\
        --command 'GITCG_LLM_TRACE=... python -m scripts.extract_skills ...' \\
        --out reports/prompts/reflection_prompt.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

REPO = Path(__file__).resolve().parents[1]
FENCE = "````"


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def sha12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def fence(content: str) -> str:
    """Wrap ``content`` verbatim; the block round-trips as ``"\\n".join(lines)``."""
    for line in content.splitlines():
        if line == FENCE:
            raise SystemExit("content contains a fence line; refusing to corrupt it")
    return f"{FENCE}\n{content}\n{FENCE}"


def message_text(record: dict[str, Any], role: str) -> str:
    from common.llm import redact

    for message in record.get("messages") or ():
        if message.get("role") == role:
            return redact(str(message.get("content") or ""))
    return ""


def redacted_messages(records: Sequence[dict[str, Any]]) -> int:
    """How many messages ``redact()`` would change (expected: 0)."""
    from common.llm import redact

    return sum(
        1
        for record in records
        for message in record.get("messages") or ()
        if redact(str(message.get("content") or "")) != str(message.get("content") or "")
    )


def usage_totals(records: Sequence[dict[str, Any]]) -> dict[str, float]:
    prompt = completion = cached = reasoning = 0
    cost = 0.0
    for record in records:
        usage = record.get("usage") or {}
        prompt += int(usage.get("prompt_tokens") or 0)
        completion += int(usage.get("completion_tokens") or 0)
        cached += int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        reasoning += int(
            (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        )
        cost += float(usage.get("cost") or (usage.get("opencode") or {}).get("list_cost") or 0.0)
    return {
        "calls": len(records),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cached_tokens": cached,
        "reasoning_tokens": reasoning,
        "cost_usd": cost,
    }


def replies_with_choice(records: Sequence[dict[str, Any]]) -> tuple[int, dict[str, int]]:
    """Count replies whose content is a JSON object with an int ``choice``.

    This is the *format* half of ``skillloop.agent.parse_choice`` (range needs the
    per-call candidate count, not in the trace), i.e. exactly what tells us whether
    the model answered in the expected shape.
    """
    from common.llm import extract_json_object

    errors: dict[str, int] = {}
    ok = 0
    for record in records:
        parsed = extract_json_object(str(record.get("content") or ""))
        if parsed is None:
            errors["no_json"] = errors.get("no_json", 0) + 1
        elif not isinstance(parsed.get("choice"), int):
            errors["choice_not_int"] = errors.get("choice_not_int", 0) + 1
        else:
            ok += 1
    return ok, errors


def _settings(record: dict[str, Any]) -> str:
    return (
        f"model `{record.get('model')}` · temperature 0.0（`.env` 未设 "
        f"`GITCG_LLM_TEMPERATURE`，client 默认 0.0）"
        f" · max_tokens {record.get('max_tokens')}"
        f" · reasoning_effort `{record.get('reasoning_effort')}`"
    )


def _valid_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if not row.get("error") and not row.get("truncated")]


def successful_records(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Trace records usable as evidence: not truncated and with non-empty content.

    A ``finish_reason="length"`` call was cut off by the completion budget (hidden
    reasoning ate it) and its content is partial or empty; it is not evidence of
    what the prompt produced, so the reflection sample reports only successful
    calls. The caller keeps the original count for the metadata line.
    """
    return [
        record
        for record in records
        if record.get("finish_reason") != "length"
        and str(record.get("content") or "").strip()
    ]


def render_decision(
    records: Sequence[dict[str, Any]], rows: Sequence[dict[str, Any]], command: str
) -> str:
    valid = _valid_rows(rows)
    row = valid[0]
    totals = usage_totals(records)
    system_hashes = {sha12(message_text(record, "system")) for record in records}
    consistent = len(system_hashes) == 1
    system_sha = next(iter(system_hashes)) if consistent else "mixed"
    lines = [
        "# 决策 prompt（真实 LLM 调用原文）",
        "",
        "本文件由 `python -m scripts.prompt_samples decision` 从真实调用的 trace 生成：",
        "每个 fence 块都是 `common/llm.py` 送进 client 的 `messages[k].content` 原文，",
        "未做任何裁剪或重排；仅应用 `redact()`（本次命中数见下）后逐字节落盘。",
        "",
        "**本文件是「全部调用去重后的等价形式」**：system 在所有调用中逐字节相同，",
        "因此只在 §SYSTEM 保留一份；任何一次调用的完整 prompt = 本文件的 SYSTEM + 该 step 的 USER。",
        "USER 才是每次调用变化的部分，逐条全量保留在 §调用原文。",
        "",
        "## 元数据",
        "",
        f"- 采集命令：`{command}`",
        f"- stage `{row.get('stage')}` · seed `{row.get('seed')}` · deck `{row.get('deck')}`"
        f" · card-version `{row.get('card_version') or '默认（未指定，用引擎内置池）'}`",
    ]
    if len(valid) == 1:
        lines.append(
            f"- seed 口径：`skillloop.run --seeds N`（无逗号）表示 N 局 = seed 1..N，"
            f"故本局实际 seed `{row.get('seed')}`"
        )
    lines += [
        f"- library `{row.get('library_root')}`（version {row.get('library_version')}）"
        f" · loadout hash `{row.get('skill_loadout_hash')}`",
        f"- group `{row.get('group')}` · stage_rules {row.get('stage_rules')}"
        f" · rich_render {row.get('rich_render')} · scratchpad {row.get('scratchpad')}"
        f" · reasoning {row.get('reasoning')}",
        f"- {_settings(records[0])}",
        f"- 调用总数 **{totals['calls']}**（trace `{totals['calls']}` 条，单局 "
        f"`llm_calls={row.get('llm_calls')}`, `llm_errors={row.get('llm_errors')}`）",
        f"- 对局结果：winner `{row.get('winner')}`（0=我方） · rounds `{row.get('rounds')}`"
        f" · three_star `{row.get('three_star')}` · stars `{row.get('stars')}/"
        f"{row.get('n_objectives')}` · objectives `{json.dumps(row.get('objectives'), ensure_ascii=False)}`",
        f"- 引用技能：`{', '.join(row.get('cited_skills') or [])}`",
        f"- usage 汇总：prompt {totals['prompt_tokens']} · completion "
        f"{totals['completion_tokens']}（reasoning {totals['reasoning_tokens']}）"
        f" · cached {totals['cached_tokens']} · **cost ${totals['cost_usd']:.6f}**"
        f"（provider `cost=0`，取 `usage.opencode.list_cost`，与行内 "
        f"`cost_usd={row.get('cost_usd')}` 一致）",
        f"- system 一致性：{totals['calls']} 次调用 system 全部相同 = `{consistent}`，"
        f"sha256 前 12 位 `{system_sha}`（全文只在 §SYSTEM 出现一次）",
        f"- `redact()` 检查：{redacted_messages(records)} 条消息内容被改动"
        f"（0 = trace 原文里不含疑似密钥）",
    ]
    ok, errors = replies_with_choice(records)
    if errors:
        lines.append(f"- 回复格式：{ok}/{totals['calls']} 条含 int `choice`，异常 `{errors}`")
    else:
        lines.append(f"- 回复格式：{ok}/{totals['calls']} 条均可解析出 int `choice`（无解析回退）")
    lines += [
        "",
        "## 调用索引",
        "",
        f"system 列省略：{totals['calls']} 次调用的 system 逐字节相同"
        f"（sha256 前 12 位 `{system_sha}`），见 §SYSTEM。",
        "",
        "| step | method | user 字符数 | user sha256[:12] | completion tokens |",
        "|---|---|---|---|---|",
    ]
    for index, record in enumerate(records, 1):
        usage = record.get("usage") or {}
        user = message_text(record, "user")
        lines.append(
            f"| {index} | `{record.get('method')}` | {len(user)} "
            f"| `{sha12(user)}` | {usage.get('completion_tokens')} |"
        )
    lines.extend(
        [
            "",
            f"## SYSTEM（{totals['calls']} 次调用完全相同，sha256={system_sha}）",
            "",
            fence(message_text(records[0], "system")),
            "",
            "## 调用原文",
            "",
            "每条只列该次调用的 USER；完整 prompt = 上面的 SYSTEM + 这里的 USER。",
            "",
        ]
    )
    for index, record in enumerate(records, 1):
        user = message_text(record, "user")
        lines.append(f"### step {index} · user（{record.get('method')}, {len(user)} 字符）")
        lines.append(fence(user))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _failing_stages(rows: Sequence[dict[str, Any]]) -> list[str]:
    """Stage order ``propose_candidates`` iterates (sorted, skipping 3★-only stages)."""
    from scripts.extract_skills import stage_objectives
    from skillloop.extract import failure_profiles

    profiles = failure_profiles(rows, objectives_by_stage=stage_objectives())
    return [
        profile.stage
        for profile in profiles
        if profile.three_star_rate < 1.0 and profile.failed_objectives
    ]


def _parsed_skills(
    record: dict[str, Any], stage: str, existing_ids: Sequence[str] = ()
) -> list[Any]:
    """What the production parser would keep from this reply (0 = discarded).

    ``existing_ids`` must be the base library's ids, exactly what
    ``scripts.extract_skills`` passes to ``propose_candidates``: without it a
    legitimate ``replaces`` looks like a hallucinated one and the sample reports
    a bogus "replaces unknown skill" warning.
    """
    from skillloop.extract import parse_skills

    return parse_skills(
        str(record.get("content") or ""), stage=stage, existing_ids=existing_ids
    )


def render_reflection(
    records: Sequence[dict[str, Any]],
    rows: Sequence[dict[str, Any]],
    base: str,
    command: str,
    *,
    trace_calls: int | None = None,
) -> str:
    from scripts.extract_skills import stage_objectives
    from skillloop.extract import failure_profiles

    valid = _valid_rows(rows)
    profiles = failure_profiles(valid, objectives_by_stage=stage_objectives())
    by_stage = {profile.stage: profile for profile in profiles}
    generated = _failing_stages(valid)
    if len(generated) != len(records):
        raise SystemExit(
            f"{len(generated)} failing stages but {len(records)} trace calls: "
            "cannot attribute calls to stages"
        )
    worst_failures = max(sum(p.failed_objectives.values()) for p in profiles)
    chosen = max(
        generated,
        key=lambda stage: sum(by_stage[stage].failed_objectives.values()),
    )
    chosen_index = generated.index(chosen)
    record = records[chosen_index]
    totals = usage_totals(records)
    base_payload = json.loads(
        (REPO / base / "library.json").read_text(encoding="utf-8")
    )
    base_version = base_payload.get("library_version")
    # The sample must be parsed with the same known ids as the production script,
    # otherwise a valid ``replaces`` is reported as unknown.
    base_ids = tuple(
        str(entry.get("skill_id"))
        for entry in base_payload.get("skills") or ()
        if isinstance(entry, dict) and entry.get("skill_id")
    )
    objectives = stage_objectives().get(chosen, ())
    truncated_note = (
        f"；trace 共 {trace_calls} 条，其中 finish_reason=length 的截断调用已剔除"
        if trace_calls and trace_calls != totals["calls"]
        else ""
    )
    calls_note = (
        "1 次成功调用"
        if totals["calls"] == 1
        else f"{totals['calls']} 次成功调用（每关一次）"
    )

    lines = [
        "# 复盘（技能抽取）prompt（真实 LLM 调用原文）",
        "",
        "本文件由 `python -m scripts.prompt_samples reflection` 从真实调用的 trace 生成：",
        "fence 块是 `common/llm.py` 送进 client 的 `messages[k].content` 原文，未做裁剪；",
        "仅应用 `redact()`（本次命中数见下）后逐字节落盘。",
        "",
        "## 元数据",
        "",
        f"- 抽取命令：`{command}`",
        f"- rows 来源：`skillloop.run --group {valid[0].get('group')}`"
        f"（真实 LLM 对局，stage `{', '.join(sorted({str(r.get('stage')) for r in valid}))}`，"
        f"seeds `{', '.join(str(seed) for seed in sorted({int(r.get('seed')) for r in valid}))}`，"
        f"n={len(valid)} 局）",
        f"- 三星率 {sum(1 for r in valid if r.get('three_star'))}/{len(valid)}"
        f" = {sum(1 for r in valid if r.get('three_star')) / len(valid):.2f}",
        f"- base 库 `{base}`（version {base_version}）",
        f"- 样本关 `{chosen}`（失败条目最多；重放 {len(by_stage[chosen].replays)} 条）",
        f"- 该关目标：{'；'.join(objectives)}",
        f"- 失败分布：`{json.dumps(by_stage[chosen].failed_objectives, ensure_ascii=False)}`"
        f"（全部关最大失败条目数 {worst_failures}）",
        f"- {_settings(record)}（`scripts.extract_skills` 默认 `--effort high --max-tokens 32768`）",
        f"- 调用次数 **{totals['calls']}**（{calls_note}{truncated_note}；"
        f"cost ${totals['cost_usd']:.6f}）",
        f"- usage 汇总：prompt {totals['prompt_tokens']} · completion "
        f"{totals['completion_tokens']}（reasoning {totals['reasoning_tokens']}）"
        f" · cached {totals['cached_tokens']} · **cost ${totals['cost_usd']:.6f}**",
        f"- 首次真实可用性：finish_reason `{record.get('finish_reason')}`，"
        f"回复 {len(str(record.get('content') or ''))} 字符；"
        f"`skillloop.extract.parse_skills` 解出 "
        f"{len(_parsed_skills(record, chosen, base_ids))} 条候选"
        f"（0 = 该次调用被丢弃/回退）",
        f"- `redact()` 检查：{redacted_messages(records)} 条消息内容被改动"
        f"（0 = trace 原文里不含疑似密钥）",
        "",
        "## 该次抽取调用原文",
        "",
    ]
    for role in ("system", "user"):
        lines.append(f"### step {chosen_index + 1} · {role}")
        lines.append(fence(message_text(record, role)))
        lines.append("")
    lines.extend(["## 其余关的调用清单", ""])
    if len(records) == 1:
        lines.append("本次运行只触发了 1 关调用，无其余关。")
    else:
        lines.extend(
            ["| step | stage | user 字符数 | system sha256[:12] |", "|---|---|---|---|"]
        )
        for index, item in enumerate(records, 1):
            if index - 1 == chosen_index:
                continue
            lines.append(
                f"| {index} | `{generated[index - 1]}` "
                f"| {len(message_text(item, 'user'))} "
                f"| `{sha12(message_text(item, 'system'))}` |"
            )
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("decision", "reflection"))
    parser.add_argument("--trace", required=True)
    parser.add_argument("--rows", required=True)
    parser.add_argument("--base", default=None, help="base library (reflection only)")
    parser.add_argument("--command", default="", help="exact shell command used for collection")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    records = load_jsonl(args.trace)
    rows = load_jsonl(args.rows)
    if not records:
        raise SystemExit(f"empty trace: {args.trace}")
    if args.kind == "decision":
        text = render_decision(records, rows, args.command)
    else:
        if not args.base:
            raise SystemExit("--base is required for reflection")
        usable = successful_records(records)
        if not usable:
            raise SystemExit(
                f"no successful (non-truncated, non-empty) calls in {args.trace}"
            )
        if len(usable) != len(records):
            print(
                f"[prompt_samples] dropped {len(records) - len(usable)} truncated/"
                "empty trace records (finish_reason=length)",
                file=sys.stderr,
            )
        text = render_reflection(
            usable, rows, args.base, args.command, trace_calls=len(records)
        )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"[prompt_samples] wrote {out} ({len(text)} chars, {len(records)} calls)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
