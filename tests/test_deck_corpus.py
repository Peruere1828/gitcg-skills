"""Public 七圣召唤 deck corpus (exploration task 1).

Fast, network-free. Skips when the corpus JSON is absent (it is produced by
``python -m scripts.explore.fetch_deck_corpus`` and lives in the gitignored
``data/deck_corpus/``; a compact copy is committed under ``reports/explore/``).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from decklab import corpus as corpus_mod

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS_CACHE = REPO_ROOT / "data" / "assets" / "expert_system_assets_chs_latest.json"

pytestmark = pytest.mark.skipif(
    corpus_mod.corpus_path() is None,
    reason="deck corpus JSON absent; run scripts.explore.fetch_deck_corpus",
)


def _assets_available() -> bool:
    return ASSETS_CACHE.exists() or os.environ.get("GITCG_ASSETS_OFFLINE") == "1"


def test_corpus_entries_are_legal_decks():
    entries = corpus_mod.load_corpus()
    assert entries, "corpus should not be empty"
    for entry in entries:
        assert entry["share_code"], entry["slug"]
        assert len(entry["characters"]) == 3, entry["slug"]
        assert len(entry["cards"]) == 30, entry["slug"]
        assert entry["archetype"], entry["slug"]
        assert entry["validation"]["deck_shape_ok"] is True, entry["slug"]


def test_corpus_deck_specs_are_valid_and_unique():
    specs = corpus_mod.corpus_deck_specs()
    assert len(specs) >= 5
    assert len({spec.name for spec in specs}) == len(specs)
    for spec in specs:
        assert len(spec.characters) == 3
        assert len(spec.cards) == 30


def test_corpus_lookup_and_metadata():
    metadata = corpus_mod.corpus_metadata()
    assert "counts" in metadata
    entries = corpus_mod.load_corpus()
    first = entries[0]
    assert corpus_mod.corpus_entry(first["slug"])["share_code"] == first["share_code"]


def test_share_codes_decode_to_stored_ids():
    if not _assets_available():
        pytest.skip("assets cache unavailable (would require network)")
    from agents.scripted.assets import load_assets
    from agents.scripted.codec import decode_share_code

    assets = load_assets()
    for entry in corpus_mod.load_corpus():
        chars, cards = decode_share_code(entry["share_code"], assets.share_to_id)
        assert tuple(chars) == tuple(entry["characters"]), entry["slug"]
        assert tuple(cards) == tuple(entry["cards"]), entry["slug"]
