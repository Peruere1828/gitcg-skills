"""Import official TCG deck share codes for the v3.3.0-era benchmark.

Input format (``data/era_decks.txt``)::

    # comments and blank lines are ignored
    <name> <share_code>
    <name> <share_code>   # inline comment after the code is allowed

The importer:

1. decodes each code with the repo's ported codec
   (``agents.scripted.codec.decode_share_code``) against the asset dump's
   ``shareId -> id`` map (``decode_share_code_raw`` is used first to name any
   missing share id precisely);
2. prints the three characters (id, name, element, weapon) and the 30 action
   cards grouped by type (weapon / artifact / event / support / talent) with
   copy counts;
3. validates the deck at ``version="v3.3.0"`` by constructing a ``Game``.  On
   failure it reports the engine message and, per card/character, which item is
   outside the era plus the asset ``sinceVersion`` it belongs to;
4. re-checks the TCG deck-building rules the engine does not enforce
   (30 cards, <= 2 copies, talents only for the chosen characters, weapons only
   for a matching weapon type, at most 2 weapon cards per character slot, and
   character cards must be playable by asset metadata (`category ==
   "characters"`, `obtainable != false`), not by an id range);
5. with ``--write``, appends/updates the deck in ``configs/era_decks.json``
   (``source="share_code"``).  Invalid decks are printed but never written.
   Objective coverage (weapon / artifact / event counts) is reported but is not
   a write condition: a legal deck can lack a card type, and the per-stage
   selector will then not use it for that stage.

No network calls and no invented share codes.  If the input file has no
entries, the importer prints ``waiting for codes`` and exits 0.

Usage::

    PY=/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python
    # preview every entry in data/era_decks.txt
    "$PY" -m scripts.import_deck --file data/era_decks.txt --dry-run
    # write the valid decks to configs/era_decks.json
    "$PY" -m scripts.import_deck --file data/era_decks.txt --write
    # one code, with an explicit deck id
    "$PY" -m scripts.import_deck --code "<SHARE_CODE>" --name era_my_deck --dry-run
    # documented era variant (no share code is invented):
    "$PY" -m scripts.import_deck --file data/era_decks.txt --write \\
        --variant nvpu_shuangyan_era=nvpu_shuangyan --replace 333014=333006
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from agents.scripted.codec import decode_share_code, decode_share_code_raw

from scripts.era_decks import (
    DEFAULT_ASSETS,
    DEFAULT_CONFIG,
    DEFAULT_INPUT,
    ERA_VERSION,
    _card_kind,
    _character_element,
    _character_weapon,
    _probe_load,
    decorate_deck,
    load_asset_entries,
    share_to_id_map,
    validate_deck_rules,
)

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
GROUP_ORDER = ("weapon", "artifact", "event", "support", "talent", "other")


@dataclass(frozen=True)
class Entry:
    name: str
    code: str
    source: str  # "file:line" or "code"


def parse_entries(path: Path) -> list[Entry]:
    entries: list[Entry] = []
    seen: set[str] = set()
    if not path.exists():
        return entries
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if " #" in line:
            line = line.split(" #", 1)[0].strip()
        parts = line.split()
        if len(parts) < 2:
            raise SystemExit(
                f"{path}:{lineno}: expected '<name> <share_code>', got {raw!r}"
            )
        name, code = parts[0], parts[1]
        if not NAME_RE.match(name):
            raise SystemExit(
                f"{path}:{lineno}: deck name {name!r} must match {NAME_RE.pattern}"
            )
        if name in seen:
            raise SystemExit(f"{path}:{lineno}: duplicate deck name {name!r}")
        seen.add(name)
        entries.append(Entry(name=name, code=code, source=f"{path}:{lineno}"))
    return entries


def _describe(
    characters: Iterable[int],
    cards: Iterable[int],
    entries: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    characters = [int(character) for character in characters]
    cards = [int(card_id) for card_id in cards]
    deck = decorate_deck(
        {
            "name": "imported",
            "characters": characters,
            "cards": cards,
            "source": "share_code",
        },
        entries,
    )
    return deck


def _card_label(card_id: int, entries: dict[int, dict[str, Any]]) -> str:
    item = entries.get(card_id, {})
    name = str(item.get("name") or item.get("englishName") or "?")
    return f"{card_id} {name}"


def _character_label(character: int, entries: dict[int, dict[str, Any]]) -> str:
    item = entries.get(character, {})
    name = str(item.get("name") or item.get("englishName") or "?")
    element = _character_element(item) or "?"
    weapon = _character_weapon(item) or "?"
    return f"{character} {name} [{element} / {weapon}]"


def _print_deck(
    entry: Entry,
    deck: dict[str, Any],
    entries: dict[int, dict[str, Any]],
    legality: dict[str, Any],
    rule_errors: list[str],
) -> None:
    characters = [int(c) for c in deck["characters"]]
    cards = [int(c) for c in deck["cards"]]
    print(f"[import] {entry.name}  ({entry.source})", flush=True)
    code_label = "source share code" if entry.source.startswith("variant") else "share code"
    print(
        f"  {code_label}: {entry.code[:12]}...  "
        f"({len(characters)} characters + {len(cards)} cards)",
        flush=True,
    )
    print(f"  characters ({len(characters)}):", flush=True)
    for character in characters:
        print(f"    {_character_label(character, entries)}", flush=True)

    grouped: dict[str, list[int]] = {kind: [] for kind in GROUP_ORDER}
    for card_id in cards:
        kind = _card_kind(entries.get(card_id, {}))
        grouped.setdefault(kind, []).append(card_id)
    print(f"  action cards ({len(cards)}):", flush=True)
    for kind in GROUP_ORDER:
        ids = grouped.get(kind, [])
        print(f"    {kind} ({len(ids)}):", flush=True)
        counts: dict[int, int] = {}
        for card_id in ids:
            counts[card_id] = counts.get(card_id, 0) + 1
        for card_id in sorted(counts):
            suffix = ""
            item = entries.get(card_id, {})
            if kind == "talent" and item.get("relatedCharacterId"):
                related = int(item["relatedCharacterId"])
                suffix = f"  ({entries.get(related, {}).get('name', related)} talent)"
            print(
                f"      {_card_label(card_id, entries)}  x{counts[card_id]}{suffix}",
                flush=True,
            )

    composition = deck["composition"]
    coverage = deck["objective_coverage"]
    print(
        "  composition: "
        + " / ".join(
            f"{composition[kind]} {kind}"
            for kind in ("talent", "weapon", "artifact", "support", "event")
        )
        + f" = {composition['total']}",
        flush=True,
    )
    print(
        f"  objective coverage: weapon {coverage['weapon']} "
        f"(wagner {'OK' if coverage['weapon'] >= 1 else 'NO'}), "
        f"artifact {coverage['artifact']} "
        f"(timaeus {'OK' if coverage['artifact'] >= 1 else 'NO'}), "
        f"event {coverage['event']} "
        f"(uncletian {'OK' if coverage['event'] >= 2 else 'NO'})",
        flush=True,
    )

    ok = bool(legality["loads"])
    print(f"  era legality at {ERA_VERSION}: {'OK' if ok else 'FAIL'}", flush=True)
    if legality["engine_message"]:
        print(f"    engine: {legality['engine_message']}", flush=True)
    if legality["illegal_characters"]:
        print("    out-of-era characters:", flush=True)
        for character in legality["illegal_characters"]:
            item = entries.get(character, {})
            print(
                f"      {character} {item.get('name', '?')} "
                f"(sinceVersion {item.get('sinceVersion') or 'unknown'})",
                flush=True,
            )
    if legality["illegal_cards"]:
        print("    out-of-era cards:", flush=True)
        for card_id in legality["illegal_cards"]:
            item = entries.get(card_id, {})
            print(
                f"      {card_id} {item.get('name', '?')} "
                f"(sinceVersion {item.get('sinceVersion') or 'unknown'})",
                flush=True,
            )
    if legality["unobtainable_cards"]:
        print(
            "    engine-legal but unobtainable (not deck-buildable): "
            + ", ".join(
                f"{card_id} {entries.get(card_id, {}).get('name', '?')}"
                for card_id in legality["unobtainable_cards"]
            ),
            flush=True,
        )
    if rule_errors:
        print("  rule check: FAIL", flush=True)
        for error in rule_errors:
            print(f"    - {error}", flush=True)
    else:
        print("  rule check: OK", flush=True)
    print("", flush=True)


def _legality(
    characters: list[int],
    cards: list[int],
    entries: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Per-item engine probes + the whole-deck engine probe."""
    base_characters = (1103, 1301, 1401)
    base_cards = (311101,) * 30

    illegal_characters: list[int] = []
    for character in characters:
        probe_chars = [character] + [
            candidate for candidate in base_characters if candidate != character
        ][:2]
        ok, _ = _probe_load(probe_chars, base_cards, version=ERA_VERSION)
        if not ok:
            illegal_characters.append(character)

    illegal_cards: list[int] = []
    for card_id in sorted(set(cards)):
        ok, _ = _probe_load(base_characters, [card_id] * 30, version=ERA_VERSION)
        if not ok:
            illegal_cards.append(card_id)

    unobtainable = [
        card_id
        for card_id in sorted(set(cards))
        if card_id in entries and entries[card_id].get("obtainable") is False
    ]
    whole_ok, message = _probe_load(characters, cards, version=ERA_VERSION)
    loads = whole_ok and not illegal_characters and not illegal_cards and not unobtainable
    return {
        "loads": loads,
        "engine_message": message,
        "illegal_characters": illegal_characters,
        "illegal_cards": illegal_cards,
        "unobtainable_cards": unobtainable,
    }


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": ERA_VERSION, "decks": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _decode_entry(
    entry: Entry, share_to_id: dict[int, int]
) -> tuple[list[int], list[int]] | None:
    try:
        share_ids = decode_share_code_raw(entry.code)
    except Exception as exc:
        print(f"[import] {entry.name}: cannot decode share code: {exc}", flush=True)
        return None
    missing = [share_id for share_id in share_ids if share_id not in share_to_id]
    if missing:
        print(
            f"[import] {entry.name}: share ids {missing} are not in the asset catalog "
            "(regenerate data/assets/... or use an era code)",
            flush=True,
        )
        return None
    if len(share_ids) != 33:
        print(
            f"[import] {entry.name}: decoded {len(share_ids)} ids, expected 33 "
            "(3 characters + 30 cards)",
            flush=True,
        )
        return None
    try:
        characters, cards = decode_share_code(entry.code, share_to_id)
    except KeyError as exc:
        print(
            f"[import] {entry.name}: share id {exc.args[0]} has no definition id "
            "in the asset catalog",
            flush=True,
        )
        return None
    return list(characters), list(cards)


def _record_deck(
    entry: Entry,
    characters: list[int],
    cards: list[int],
    *,
    entries: dict[int, dict[str, Any]],
    config: dict[str, Any],
    write: bool,
    source: str,
    notes: str,
    share_code: str,
    source_share_code: str = "",
) -> bool:
    """Describe, engine-validate, rule-check and optionally write one deck."""
    deck = _describe(characters, cards, entries)
    legality = _legality(characters, cards, entries)
    rule_errors = validate_deck_rules(deck, entries)
    _print_deck(entry, deck, entries, legality, rule_errors)

    valid = bool(legality["loads"]) and not rule_errors
    if not valid:
        print(f"[import] {entry.name}: NOT written (invalid)\n", flush=True)
        return False

    deck_record: dict[str, Any] = {
        **deck,
        "name": entry.name,
        "source": source,
        "notes": notes,
        "share_code": share_code,
    }
    if source_share_code:
        deck_record["source_share_code"] = source_share_code
    if write:
        decks = config.setdefault("decks", {})
        replaced = entry.name in decks
        decks[entry.name] = deck_record
        config["version"] = ERA_VERSION
        print(
            f"[import] {entry.name}: queued for configs/era_decks.json "
            f"({'replaced' if replaced else 'new'})\n",
            flush=True,
        )
    else:
        print(f"[import] {entry.name}: dry-run, not written\n", flush=True)
    return True


def import_one(
    entry: Entry,
    *,
    entries: dict[int, dict[str, Any]],
    share_to_id: dict[int, int],
    config: dict[str, Any],
    write: bool,
) -> bool:
    """Decode/validate/print one entry; optionally write it. True == valid."""
    decoded = _decode_entry(entry, share_to_id)
    if decoded is None:
        return False
    characters, cards = decoded
    return _record_deck(
        entry,
        characters,
        cards,
        entries=entries,
        config=config,
        write=write,
        source="share_code",
        notes=f"imported from {entry.source}; share_code={entry.code}",
        share_code=entry.code,
    )


def import_variant(
    name: str,
    source_name: str,
    replacements: list[tuple[int, int]],
    *,
    rows: list[Entry],
    entries: dict[int, dict[str, Any]],
    share_to_id: dict[int, int],
    config: dict[str, Any],
    write: bool,
) -> bool:
    """Create a documented era variant of an existing share-code deck.

    ``replacements`` is a list of ``(old_card, new_card)``; every copy of the old
    card is replaced.  The source is decoded from the input file if present, else
    taken from the config.  The variant stores the card ids directly (no share
    code is invented); ``source_share_code`` and the notes keep provenance.
    """
    source_entry = next((entry for entry in rows if entry.name == source_name), None)
    if source_entry is not None:
        decoded = _decode_entry(source_entry, share_to_id)
        if decoded is None:
            return False
        characters, cards = decoded
        source_code = source_entry.code
        source_label = f"{source_name} ({source_code})"
    else:
        source_deck = (config.get("decks") or {}).get(source_name)
        if not source_deck:
            print(
                f"[import] {name}: source {source_name!r} is neither in the input "
                "file nor in the config",
                flush=True,
            )
            return False
        characters = [int(c) for c in source_deck["characters"]]
        cards = [int(c) for c in source_deck["cards"]]
        source_code = str(
            source_deck.get("share_code")
            or source_deck.get("source_share_code")
            or ""
        )
        source_label = f"{source_name} (config)"

    changes: list[str] = []
    for old_card, new_card in replacements:
        count = sum(1 for card in cards if card == old_card)
        if count == 0:
            print(
                f"[import] {name}: source deck has no copy of {old_card}; "
                "nothing to replace",
                flush=True,
            )
            return False
        cards = [new_card if card == old_card else card for card in cards]
        old_name = entries.get(old_card, {}).get("name", "?")
        new_name = entries.get(new_card, {}).get("name", "?")
        changes.append(
            f"{count}x {old_card} {old_name} -> {count}x {new_card} {new_name}"
        )
    notes = (
        f"era variant of {source_name}; source share_code={source_code}; "
        + "; ".join(changes)
        + "; other cards unchanged"
    )
    print(f"[import] variant {name} of {source_name}: " + "; ".join(changes), flush=True)
    variant_entry = Entry(
        name=name, code=source_code or "variant", source=f"variant of {source_label}"
    )
    return _record_deck(
        variant_entry,
        characters,
        cards,
        entries=entries,
        config=config,
        write=write,
        source="share_code_variant",
        notes=notes,
        share_code="",
        source_share_code=source_code,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", default=str(DEFAULT_INPUT), help="input list path")
    parser.add_argument("--code", default=None, help="import a single share code")
    parser.add_argument("--name", default="imported_deck", help="deck id for --code")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="config to update")
    parser.add_argument("--assets", default=str(DEFAULT_ASSETS))
    parser.add_argument(
        "--write",
        action="store_true",
        help="write valid decks to configs/era_decks.json (default: dry-run)",
    )
    parser.add_argument("--dry-run", action="store_true", help="print only, never write")
    parser.add_argument(
        "--variant",
        default=None,
        metavar="NEW=SOURCE",
        help="create an era variant of an input/config deck, e.g. "
        "nvpu_shuangyan_era=nvpu_shuangyan",
    )
    parser.add_argument(
        "--replace",
        action="append",
        default=[],
        metavar="OLD=NEW",
        help="card substitution for --variant; every copy of OLD becomes NEW "
        "(repeatable)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.write and args.dry_run:
        raise SystemExit("--write and --dry-run are mutually exclusive")
    write = bool(args.write) and not args.dry_run
    entries = load_asset_entries(Path(args.assets))
    share_to_id = share_to_id_map(entries)
    config_path = Path(args.config)
    config = _load_config(config_path)

    if args.code:
        if not NAME_RE.match(args.name):
            raise SystemExit(f"--name {args.name!r} must match {NAME_RE.pattern}")
        rows = [Entry(name=args.name, code=args.code, source="--code")]
    else:
        input_path = Path(args.file)
        if not input_path.exists():
            print(f"[import] waiting for codes: {input_path} does not exist.", flush=True)
            return 0
        rows = parse_entries(input_path)
        if not rows:
            print(
                f"[import] waiting for codes: {input_path} has no deck entries.",
                flush=True,
            )
            return 0

    print(f"[import] {len(rows)} entr{'y' if len(rows) == 1 else 'ies'}", flush=True)
    variant_spec: tuple[str, str, list[tuple[int, int]]] | None = None
    if args.variant:
        if "=" not in args.variant:
            raise SystemExit("--variant must be NEW=SOURCE")
        new_name, source_name = args.variant.split("=", 1)
        if not NAME_RE.match(new_name):
            raise SystemExit(
                f"--variant name {new_name!r} must match {NAME_RE.pattern}"
            )
        if not args.replace:
            raise SystemExit("--variant requires at least one --replace OLD=NEW")
        replacements: list[tuple[int, int]] = []
        for specification in args.replace:
            if "=" not in specification:
                raise SystemExit("--replace must be OLD=NEW")
            old_text, new_text = specification.split("=", 1)
            try:
                replacements.append((int(old_text), int(new_text)))
            except ValueError:
                raise SystemExit(f"--replace ids must be integers: {specification!r}")
        variant_spec = (new_name, source_name, replacements)

    valid = 0
    written: list[str] = []
    for entry in rows:
        if import_one(
            entry,
            entries=entries,
            share_to_id=share_to_id,
            config=config,
            write=write,
        ):
            valid += 1
            if write:
                written.append(entry.name)
        if write:
            # Persist after each entry so a later malformed entry does not discard
            # the earlier successes.
            _write_config(config_path, config)

    variant_ok: bool | None = None
    if variant_spec:
        new_name, source_name, replacements = variant_spec
        variant_ok = import_variant(
            new_name,
            source_name,
            replacements,
            rows=rows,
            entries=entries,
            share_to_id=share_to_id,
            config=config,
            write=write,
        )
        if variant_ok and write:
            written.append(new_name)
        if write:
            _write_config(config_path, config)

    summary = f"{valid}/{len(rows)} share codes valid, {len(rows) - valid} invalid"
    if variant_spec and variant_ok is not None:
        summary += f"; variant {variant_spec[0]}: {'OK' if variant_ok else 'FAIL'}"
    print(f"[import] summary: {summary}", flush=True)
    if write:
        print(
            f"[import] config: {config_path} now has "
            f"{len(config.get('decks', {}))} deck(s)"
            + (f"; written: {', '.join(written)}" if written else ""),
            flush=True,
        )
        print(
            '[import] next: "$PY" -m scripts.era_decks --all --workers 8',
            flush=True,
        )
    else:
        print("[import] dry-run: nothing written (pass --write to save)", flush=True)
    return 0 if valid == len(rows) and variant_ok is not False else 1


if __name__ == "__main__":
    sys.exit(main())
