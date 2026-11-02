"""Build the engine payload for NPC stage special rules.

``configs/npc/special_rules.json`` is the authoritative source for the adventure
challenge rules (initial supports + start-of-battle / action-phase effects). The
engine consumes them through the string ``CreateParam`` attribute
``ATTR_CREATEPARAM_STAGE_RULES = 2002`` as JSON::

    {"player": 0,
     "supports": [{"card_id": 322007, "removable": true}],
     "effects": [{"kind": "initial_energy", "value": 2,
                  "target": "friendly_all"},
                 {"kind": "element_application", "element": "HYDRO",
                  "target": "both_active"}]}

The payload is always written from the **contestant's** point of view
(``player=0``): the stage data says "我方支援区" / "我方所有角色", and the NPC is
player 1 in every PvE match in this repo.

Two effect kinds are in the engine contract:

* ``initial_energy`` — ``value`` + ``target``;
* ``element_application`` — ``element`` (upper-case) + ``target``.

The data's ``weapon_free_equip`` (Wagner) and ``artifact_free_equip`` (Timaeus)
are **not** stage effects: they come from the injected support card's own text
(``via_support``), so the real card definition implements them. They are kept in
:attr:`StageRule.via_support_effects` for auditing but are not sent to the
engine. ``timing`` is implicit in the effect kind and is not sent either.

CLI::

    python -m npc.stage_rules --stage chefmao      # one payload
    python -m npc.stage_rules --stage all          # every stage (null = no rule)
    python -m npc.stage_rules --verify             # coverage + contract check
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from common.paths import REPO_ROOT

from .spec import stage_names

DEFAULT_RULES_PATH = REPO_ROOT / "configs" / "npc" / "special_rules.json"

# Effect kinds the engine contract accepts, and the data-only kinds that are
# implemented by the injected support card itself.
CONTRACT_EFFECT_KINDS = ("initial_energy", "element_application")
VIA_SUPPORT_EFFECT_KINDS = ("artifact_free_equip", "weapon_free_equip")
# Targets the engine accepts per kind (see packages/cbinding/js/main.ts).
INITIAL_ENERGY_TARGETS = ("friendly_all",)
ELEMENT_APPLICATION_TARGETS = ("enemy_active", "both_active", "friendly_active")


@dataclass(frozen=True)
class StageSupport:
    """One card placed in the contestant's support zone at game start."""

    card_id: int
    removable: bool = True
    name: str = ""


@dataclass(frozen=True)
class StageEffect:
    """One contract effect; ``value``/``element`` depends on ``kind``."""

    kind: str
    target: str
    value: int | None = None
    element: str | None = None

    def as_payload(self) -> dict[str, Any]:
        if self.kind == "initial_energy":
            return {"kind": self.kind, "value": int(self.value or 0), "target": self.target}
        return {"kind": self.kind, "element": str(self.element or "").upper(), "target": self.target}


@dataclass(frozen=True)
class StageRule:
    """Normalized rule data for one stage."""

    stage: str
    supports: tuple[StageSupport, ...] = ()
    effects: tuple[StageEffect, ...] = ()
    # Effect kinds dropped because the support card's own text implements them.
    via_support_effects: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.supports and not self.effects

    def as_payload(self, *, player: int = 0) -> dict[str, Any] | None:
        """The JSON-able engine payload, or ``None`` when the stage has no rule."""
        if self.is_empty:
            return None
        return {
            "player": int(player),
            "supports": [
                {"card_id": int(support.card_id), "removable": bool(support.removable)}
                for support in self.supports
            ],
            "effects": [effect.as_payload() for effect in self.effects],
        }


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _parse_support(entry: dict[str, Any], *, stage: str) -> StageSupport:
    raw_id = entry.get("card_id")
    if raw_id is None:
        raise ValueError(
            f"stage {stage!r}: initial support {entry.get('name')!r} has no card_id; "
            "resolve it in configs/npc/special_rules.json"
        )
    card_id = int(raw_id)
    if card_id <= 0:
        raise ValueError(f"stage {stage!r}: invalid support card_id {card_id}")
    return StageSupport(
        card_id=card_id,
        removable=bool(entry.get("removable", True)),
        name=str(entry.get("name") or ""),
    )


def _parse_effect(entry: dict[str, Any], *, stage: str) -> StageEffect | None:
    kind = str(entry.get("kind") or "")
    if kind in VIA_SUPPORT_EFFECT_KINDS:
        # The injected support card (Wagner/Timaeus) owns this effect.
        return None
    if kind not in CONTRACT_EFFECT_KINDS:
        raise ValueError(f"stage {stage!r}: unsupported effect kind {kind!r}")

    target = str(entry.get("target") or "")
    allowed_targets = (
        INITIAL_ENERGY_TARGETS if kind == "initial_energy" else ELEMENT_APPLICATION_TARGETS
    )
    if target not in allowed_targets:
        raise ValueError(
            f"stage {stage!r}: effect {kind!r} has unsupported target {target!r}; "
            f"expected one of {allowed_targets}"
        )
    if kind == "initial_energy":
        raw_value = entry.get("value")
        if raw_value is None:
            raise ValueError(f"stage {stage!r}: initial_energy has no value")
        return StageEffect(kind=kind, target=target, value=int(raw_value))
    element = str(entry.get("element") or "").upper()
    if not element:
        raise ValueError(f"stage {stage!r}: element_application has no element")
    return StageEffect(kind=kind, target=target, element=element)


@lru_cache(maxsize=4)
def load_rules(path: str | Path | None = None) -> dict[str, StageRule]:
    """Load and normalize ``special_rules.json`` keyed by stage name."""
    payload = _read_json(Path(path) if path is not None else DEFAULT_RULES_PATH)
    rules: dict[str, StageRule] = {}
    for entry in payload.get("stages", ()):
        name = str(entry.get("stage") or "")
        if not name:
            raise ValueError("special_rules.json: stage entry without a name")
        if name in rules:
            raise ValueError(f"special_rules.json: duplicate stage {name!r}")
        effects: list[StageEffect] = []
        via_support: list[str] = []
        for raw_effect in entry.get("effects", ()):
            effect = _parse_effect(raw_effect, stage=name)
            if effect is None:
                via_support.append(str(raw_effect.get("kind")))
            else:
                effects.append(effect)
        rules[name] = StageRule(
            stage=name,
            supports=tuple(
                _parse_support(raw, stage=name) for raw in entry.get("initial_supports", ())
            ),
            effects=tuple(effects),
            via_support_effects=tuple(via_support),
        )
    return rules


def build_stage_rules(
    stage: str, *, player: int = 0, path: str | Path | None = None
) -> dict[str, Any] | None:
    """Payload for one stage, or ``None`` when the stage has no special rule."""
    rules = load_rules(path)
    rule = rules.get(stage)
    if rule is None:
        raise KeyError(f"unknown NPC stage: {stage!r}; known: {sorted(rules)}")
    return rule.as_payload(player=player)


def stage_rules_json(
    stage: str, *, player: int = 0, path: str | Path | None = None
) -> str | None:
    """The compact JSON string to pass to ``CreateParam.set_attr(2002, ...)``."""
    payload = build_stage_rules(stage, player=player, path=path)
    if payload is None:
        return None
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def verify_rules(path: str | Path | None = None) -> dict[str, Any]:
    """Coverage/contract check over every stage in the rule file.

    The parser already rejects malformed entries; this checks that the file and
    the stage specs describe the same 16 stages and reports the resulting
    payload coverage.
    """
    rules = load_rules(path)
    expected = set(stage_names())
    actual = set(rules)
    errors: list[str] = []
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        errors.append(f"missing stage rules: {missing}")
    if extra:
        errors.append(f"rule file has unknown stages: {extra}")
    for name in sorted(actual & expected):
        rule = rules[name]
        payload = rule.as_payload()
        if payload is None:
            continue
        keys = set(payload)
        if keys != {"player", "supports", "effects"}:
            errors.append(f"stage {name!r}: payload keys {sorted(keys)}")
        for effect in payload["effects"]:
            kind = effect["kind"]
            targets = (
                INITIAL_ENERGY_TARGETS
                if kind == "initial_energy"
                else ELEMENT_APPLICATION_TARGETS
            )
            if kind not in CONTRACT_EFFECT_KINDS:
                errors.append(f"stage {name!r}: effect kind {kind!r} in payload")
            if effect["target"] not in targets:
                errors.append(f"stage {name!r}: target {effect['target']!r} in payload")
    return {
        "stage_specs": len(expected),
        "rules": len(actual),
        "with_supports": sorted(name for name, rule in rules.items() if rule.supports),
        "with_effects": sorted(name for name, rule in rules.items() if rule.effects),
        "empty": sorted(name for name, rule in rules.items() if rule.is_empty),
        "via_support_effects": {
            name: list(rule.via_support_effects)
            for name, rule in sorted(rules.items())
            if rule.via_support_effects
        },
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", default="all", help="stage name, comma-separated list, or 'all'")
    parser.add_argument("--player", type=int, default=0, help="player the rules apply to (default 0)")
    parser.add_argument(
        "--dump",
        action="store_true",
        help="print payloads (the default action; kept for the documented CLI)",
    )
    parser.add_argument("--verify", action="store_true", help="coverage/contract report instead of a dump")
    args = parser.parse_args(argv)

    if args.verify:
        report = verify_rules()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if not report["errors"] else 1

    rules = load_rules()
    if args.stage == "all":
        names = sorted(rules)
    else:
        names = [item.strip() for item in args.stage.split(",") if item.strip()]
    unknown = [name for name in names if name not in rules]
    if unknown:
        parser.error(f"unknown stage(s) {unknown}; known: {sorted(rules)}")
    payloads = {
        name: build_stage_rules(name, player=args.player) for name in names
    }
    output: Any = payloads[names[0]] if len(names) == 1 else payloads
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
