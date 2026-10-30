"""Append-only JSONL helpers: torn-line repair + tolerant resume reads."""

from __future__ import annotations

import json

import pytest

from common.jsonl import append_jsonl, ensure_trailing_newline, read_jsonl


def test_append_jsonl_repairs_torn_trailing_line(tmp_path):
    path = tmp_path / "s.jsonl"
    append_jsonl(path, {"a": 1})
    # Simulate a hard kill mid-write: a torn line with no trailing newline.
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"a": 2, "b"')
    append_jsonl(path, {"a": 3})
    text = path.read_text(encoding="utf-8")
    # The torn bytes are isolated on their own line; the new record is intact.
    assert '{"a": 2, "b"\n' in text
    assert text.endswith('{"a": 3}\n')
    assert read_jsonl(path, tolerate_torn=True) == [{"a": 1}, {"a": 3}]


def test_ensure_trailing_newline_safe_on_missing_and_empty(tmp_path):
    ensure_trailing_newline(tmp_path / "missing.jsonl")  # no error
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    ensure_trailing_newline(empty)
    assert empty.read_text(encoding="utf-8") == ""
    ok = tmp_path / "ok.jsonl"
    ok.write_text('{"a": 1}\n', encoding="utf-8")
    ensure_trailing_newline(ok)
    assert ok.read_text(encoding="utf-8") == '{"a": 1}\n'


def test_read_jsonl_strict_raises_on_torn(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text('{"a": 1}\n{"b":\n', encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        read_jsonl(path)
    assert read_jsonl(path, tolerate_torn=True) == [{"a": 1}]
