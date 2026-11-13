"""Render ``GITCG_LLM_TRACE`` JSONL as a human-readable prompt log.

The trace is written by ``common.llm`` when ``GITCG_LLM_TRACE`` is set. Each
line is one model call: ``messages`` (the exact prompt), ``content`` (reply),
``reasoning_content`` (provider hidden reasoning, when present), finish reason
and usage. This module is shared by ``scripts/export_prompt_log.py`` and the
web bridge's ``/ai-prompt-log`` endpoint.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from common.llm import redact


def load_trace(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.exists():
        return []
    rows: list[dict[str, Any]] = []
    with source.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def filter_trace(
    rows: Iterable[dict[str, Any]],
    *,
    session: str | None = None,
    who: int | None = None,
    stage: str | None = None,
) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        if session is not None and str(row.get("session", "")) != str(session):
            continue
        if who is not None and row.get("who") is not None and int(row["who"]) != int(who):
            continue
        if stage is not None and str(row.get("stage", "")) != str(stage):
            continue
        out.append(row)
    return out


def summarize(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    truncated = sum(1 for row in rows if row.get("finish_reason") == "length")
    empty = sum(1 for row in rows if not str(row.get("content") or "").strip())
    seconds = sum(float(row.get("seconds") or 0.0) for row in rows)
    return {
        "calls": len(rows),
        "truncated": truncated,
        "empty_content": empty,
        "seconds": round(seconds, 1),
        "models": sorted({str(row.get("model")) for row in rows if row.get("model")}),
        "efforts": sorted(
            {str(row.get("reasoning_effort")) for row in rows if row.get("reasoning_effort")}
        ),
    }


def _stamp(row: dict[str, Any]) -> str:
    ts = row.get("ts")
    if not isinstance(ts, (int, float)):
        return ""
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _usage(row: dict[str, Any]) -> str:
    usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
    parts = [
        f"prompt={usage.get('prompt_tokens', '?')}",
        f"completion={usage.get('completion_tokens', '?')}",
    ]
    details = usage.get("prompt_tokens_details") or {}
    if details.get("cached_tokens"):
        parts.append(f"cached={details['cached_tokens']}")
    if usage.get("cost"):
        parts.append(f"cost=${float(usage['cost']):.6f}")
    return " · ".join(parts)


def _messages(row: dict[str, Any]) -> str:
    messages = row.get("messages")
    if not isinstance(messages, list):
        return "（无 messages）"
    blocks = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "?")
        content = redact(str(message.get("content") or ""))
        blocks.append(f"### {role}\n\n```text\n{content}\n```")
    return "\n\n".join(blocks) or "（空 prompt）"


def render_markdown(
    rows: Sequence[dict[str, Any]],
    *,
    title: str = "Prompt log",
    max_reasoning: int | None = None,
) -> str:
    summary = summarize(rows)
    lines = [
        f"# {title}",
        "",
        f"- calls: **{summary['calls']}**",
        f"- truncated (finish=length): **{summary['truncated']}**",
        f"- empty content: **{summary['empty_content']}**",
        f"- wall time: {summary['seconds']}s",
        f"- models: {', '.join(summary['models']) or '?'}",
        f"- reasoning effort: {', '.join(summary['efforts']) or 'none'}",
        "",
    ]
    for index, row in enumerate(rows, 1):
        step = row.get("step")
        method = row.get("method") or row.get("request") or "?"
        session = row.get("session")
        who = row.get("who")
        stage = row.get("stage")
        label = f"## {index}. step {step if step is not None else '?'} · {method}"
        if session is not None:
            label += f" · session={session}"
        if who is not None:
            label += f" · who={who}"
        if stage:
            label += f" · stage={stage}"
        lines += [
            label,
            "",
            f"- {_stamp(row)}",
            f"- finish: **{row.get('finish_reason') or '?'}** · "
            f"max_tokens={row.get('max_tokens')} · "
            f"effort={row.get('reasoning_effort') or 'none'} · "
            f"{float(row.get('seconds') or 0.0):.1f}s",
            f"- usage: {_usage(row)}",
            "",
            _messages(row),
            "",
        ]
        reasoning = redact(str(row.get("reasoning_content") or ""))
        if reasoning:
            if max_reasoning is not None and len(reasoning) > max_reasoning:
                reasoning = reasoning[:max_reasoning].rstrip() + "\n…（已截断）"
            lines += [
                f"<details><summary>思考（{len(reasoning)} 字）</summary>",
                "",
                "```text",
                reasoning,
                "```",
                "",
                "</details>",
                "",
            ]
        content = redact(str(row.get("content") or ""))
        lines += ["### reply", "", "```json", content or "（空）", "```", ""]
    return "\n".join(lines).rstrip() + "\n"
