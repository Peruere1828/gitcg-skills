"""Deck-corpus fetch guards (review fix): reject API error bodies / empty lists.

The miHoYo cardsquare API is unofficial and returns HTTP 200 even for
application errors; a renamed field or a rate-limit body must fail loudly rather
than silently overwriting the validated corpus with a truncated one.
"""

from __future__ import annotations

import json

import pytest

from scripts.explore.fetch_deck_corpus import _check_api_payload, _guard_corpus_write


def test_check_api_payload_accepts_valid():
    payload = {"retcode": 0, "data": {"list": [{"a": 1}]}}
    assert _check_api_payload("match_pool_list", payload, "list") == [{"a": 1}]


def test_check_api_payload_rejects_retcode():
    payload = {"retcode": -1, "message": "rate limited", "data": {"list": []}}
    with pytest.raises(RuntimeError, match="retcode"):
        _check_api_payload("x", payload, "list")


def test_check_api_payload_rejects_missing_or_empty_list():
    with pytest.raises(RuntimeError, match="non-empty list"):
        _check_api_payload("x", {"retcode": 0, "data": {"list": []}}, "list")
    with pytest.raises(RuntimeError, match="non-empty list"):
        _check_api_payload("x", {"retcode": 0, "data": {}}, "list")


def test_check_api_payload_rejects_non_object():
    with pytest.raises(RuntimeError, match="expected an object"):
        _check_api_payload("x", ["not", "a", "dict"], "list")


def _write_corpus(path, validated: int) -> None:
    path.write_text(json.dumps({"counts": {"validated": validated}}), encoding="utf-8")


def test_guard_corpus_write_refuses_empty_and_unvalidated(tmp_path):
    out = tmp_path / "decks.json"
    with pytest.raises(SystemExit, match="no deck entries"):
        _guard_corpus_write(out, n_entries=0, n_valid=0)
    with pytest.raises(SystemExit, match="0 validated"):
        _guard_corpus_write(out, n_entries=10, n_valid=0)


def test_guard_corpus_write_refuses_shrink_without_force(tmp_path):
    out = tmp_path / "decks.json"
    _write_corpus(out, 73)
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        _guard_corpus_write(out, n_entries=20, n_valid=20)
    # --force allows an intentional shrink.
    _guard_corpus_write(out, n_entries=20, n_valid=20, force=True)
    # Growing is always fine.
    _guard_corpus_write(out, n_entries=90, n_valid=80)
