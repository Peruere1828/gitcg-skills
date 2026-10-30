"""Loader for the public 七圣召唤 deck corpus (exploration task 1).

The corpus is curated by ``scripts/explore/fetch_deck_corpus.py`` and every entry
is validated by decoding its official share code through the ported codec
(``agents.scripted.codec``) and, for most entries, by playing one scripted game
(``envs.match.run_match``). See ``reports/explore/deck_corpus.md``.

Two JSON layouts are accepted (both expose ``{"decks": [...]}``):

- ``data/deck_corpus/decks.json`` (gitignored; full data incl. author guides)
- ``reports/explore/deck_corpus.json`` (committed; compact summary)

This module never touches the network.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from reps.schema import DeckSpec

REPO_ROOT = Path(__file__).resolve().parents[1]

_DEFAULT_CANDIDATES = (
    REPO_ROOT / "data" / "deck_corpus" / "decks.json",
    REPO_ROOT / "reports" / "explore" / "deck_corpus.json",
)


def corpus_path() -> Path | None:
    """Return the first existing corpus JSON, or ``None`` if none is present."""
    override = os.environ.get("GITCG_DECK_CORPUS")
    if override:
        path = Path(override)
        return path if path.exists() else None
    for candidate in _DEFAULT_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def load_corpus(*, validated_only: bool = True) -> list[dict[str, Any]]:
    """Load raw corpus entries as dicts.

    ``validated_only`` drops entries whose share code did not decode to a legal
    3-character + 30-card deck.
    """
    path = corpus_path()
    if path is None:
        raise FileNotFoundError(
            "deck corpus JSON not found; run `python -m scripts.explore.fetch_deck_corpus` "
            "(or set GITCG_DECK_CORPUS)"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    decks = payload.get("decks", payload if isinstance(payload, list) else [])
    if validated_only:
        decks = [
            d
            for d in decks
            if d.get("validation", {}).get("deck_shape_ok") is not False
            and d.get("characters")
            and d.get("cards")
        ]
    return list(decks)


def corpus_metadata() -> dict[str, Any]:
    """Return the top-level metadata (sources/counts/generated_at) if available."""
    path = corpus_path()
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return {}
    return {k: v for k, v in payload.items() if k != "decks"}


def deck_spec_from_entry(entry: dict[str, Any]) -> DeckSpec:
    """Convert one corpus entry to a :class:`reps.schema.DeckSpec`."""
    return DeckSpec(
        name=str(entry["slug"]),
        characters=tuple(int(c) for c in entry["characters"]),
        cards=tuple(int(c) for c in entry["cards"]),
    )


def corpus_deck_specs(*, validated_only: bool = True) -> tuple[DeckSpec, ...]:
    """All corpus decks as ``DeckSpec``s (name = slug)."""
    return tuple(
        deck_spec_from_entry(entry) for entry in load_corpus(validated_only=validated_only)
    )


def corpus_decks_by_name(*, validated_only: bool = True) -> dict[str, DeckSpec]:
    """Corpus decks keyed by slug."""
    return {spec.name: spec for spec in corpus_deck_specs(validated_only=validated_only)}


def corpus_entry(slug: str) -> dict[str, Any]:
    """Look up one raw corpus entry by slug."""
    for entry in load_corpus(validated_only=False):
        if entry.get("slug") == slug:
            return entry
    raise KeyError(f"unknown corpus slug: {slug!r}")


__all__ = [
    "corpus_path",
    "load_corpus",
    "corpus_metadata",
    "deck_spec_from_entry",
    "corpus_deck_specs",
    "corpus_decks_by_name",
    "corpus_entry",
]
