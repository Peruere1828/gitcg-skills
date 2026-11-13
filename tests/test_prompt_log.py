from __future__ import annotations

import json

from scripts.export_prompt_log import main as export_main
from skillloop.prompt_log import filter_trace, load_trace, render_markdown, summarize

ROWS = [
    {
        "ts": 1763040000.0,
        "session": "s1",
        "who": 0,
        "step": 3,
        "method": "action",
        "model": "deepseek-v4.1-flash",
        "reasoning_effort": "low",
        "max_tokens": 8192,
        "messages": [
            {"role": "system", "content": "你是对局助手"},
            {"role": "user", "content": "[0] 使用技能"},
        ],
        "content": '{"choice": 0, "used": ["rule_dice"]}',
        "reasoning_content": "先算骰子再出手",
        "finish_reason": "stop",
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    },
    {
        "ts": 1763040001.0,
        "session": "s1",
        "who": 0,
        "step": 4,
        "method": "action",
        "messages": [],
        "content": "",
        "reasoning_content": "想太久",
        "finish_reason": "length",
        "max_tokens": 8192,
        "usage": {"prompt_tokens": 100, "completion_tokens": 8192},
    },
]


def _write(tmp_path):
    path = tmp_path / "trace.jsonl"
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in ROWS),
        encoding="utf-8",
    )
    return path


def test_load_and_filter_trace(tmp_path):
    rows = load_trace(_write(tmp_path))
    assert len(rows) == 2
    assert len(filter_trace(rows, session="s1", who=0)) == 2
    assert filter_trace(rows, session="other") == []


def test_render_markdown_contains_prompt_reply_and_thinking():
    markdown = render_markdown(ROWS, title="T")
    assert "# T" in markdown
    assert "truncated (finish=length): **1**" in markdown
    assert "你是对局助手" in markdown
    assert '{"choice": 0' in markdown
    assert "先算骰子再出手" in markdown
    assert "思考（" in markdown
    assert "step 4" in markdown


def test_summarize_counts_empty_and_truncated():
    summary = summarize(ROWS)
    assert summary["calls"] == 2
    assert summary["truncated"] == 1
    assert summary["empty_content"] == 1


def test_export_cli_writes_markdown(tmp_path):
    trace = _write(tmp_path)
    out = tmp_path / "prompts.md"
    assert export_main(["--trace", str(trace), "--session", "s1", "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "先算骰子再出手" in text
    assert "step 3" in text
