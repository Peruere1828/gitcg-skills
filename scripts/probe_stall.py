"""Forensic replay of the era-matrix games that hit the 15-round cap.

The era matrix (``scripts.era_decks --matrix``, 5 decks x 16 stages x seeds 1-5,
engine ``v3.3.0``) produced 5 draws out of 400 games: ``winner=None`` with
``rounds=15``. A draw is not a normal GI-TCG outcome, so this script replays one
(stage, deck, seed) and dumps enough state to tell *why* nobody closed:

* per-round HP of every character on both sides (from the public notification
  state captured at each decision),
* every damage mutation (value, target, old/new HP, reaction, defeated flag),
* every ``skill_used`` / ``declare_end`` / ``step_round`` / ``set_winner``
  mutation with its round number,
* the final state: phase, winner, turn, hand sizes, pile (deck) sizes, dice,
  declared-end flags,
* per-player decision counts (including how often each policy chose
  ``action_declare_end``).

How the trace is captured
-------------------------
``envs.match.run_match`` builds its own ``PolicyPlayer``; to record the public
mutation stream (which is where damage values live — ``MatchEvent`` only keeps
skills/reactions) the probe subclasses ``PolicyPlayer`` and swaps it into
``envs.match`` for the duration of one replay. The match semantics are therefore
exactly the ones the matrix used. Policies are wrapped in ``TracePolicy`` only to
snapshot the acting player's view before each decision; the wrapped policy still
makes the choice.

Usage::

    PY=/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python
    "$PY" -m scripts.probe_stall                    # the five flagged draws
    "$PY" -m scripts.probe_stall --stage buyun --deck keqing_jihua --seed 5
    "$PY" -m scripts.probe_stall --json-out data/runs/stall_probe.jsonl
    "$PY" -m scripts.probe_stall --min-rounds 10    # every matrix row with rounds >= 10
    "$PY" -m scripts.probe_stall --check-round-cap  # is the 15 cap configurable?
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common.paths import REPO_ROOT, data_dir
from envs.match import PolicyPlayer, run_match
from npc.policy import npc_policy_for_stage
from npc.spec import get_stage
from reps.schema import DeckSpec
from reps.snapshot import snapshot_state

ERA_VERSION = "v3.3.0"
ERA_CONFIG = REPO_ROOT / "configs" / "era_decks.json"

#: The five (stage, deck, seed) rows in ``data/runs/era_matrix.jsonl`` whose
#: ``winner`` is null at ``rounds == 15``.
FLAGGED: tuple[tuple[str, str, int], ...] = (
    ("buyun", "keqing_jihua", 5),
    ("buyun", "linghua_yongdong", 3),
    ("lan", "keqing_shuanghuo", 3),
    ("uncletian", "keqing_shuanghuo", 3),
    ("uncletian", "linghua_yongdong", 3),
)

MAX_EVENTS = 40


# --------------------------------------------------------------------------- #
# mutation trace
# --------------------------------------------------------------------------- #
_MUTATIONS: list[dict[str, Any]] = []


def _enum_name(message: Any, field_name: str) -> str:
    """Name of an enum-valued proto field (``"SKILL_TYPE_BURST"`` ...)."""
    field = message.DESCRIPTOR.fields_by_name[field_name]
    return field.enum_type.values_by_number[int(getattr(message, field_name))].name


class ProbePlayer(PolicyPlayer):
    """``PolicyPlayer`` that also stores the public mutation stream (P0 only).

    ``envs.match.run_match`` delivers the public notification to both players;
    one sink is enough, and taking player 0 avoids double counting (same
    convention as ``run_match``'s ``event_sink``).
    """

    def on_notify(self, notification: Any) -> None:
        super().on_notify(notification)
        if self.who != 0:
            return
        state = getattr(notification, "state", None)
        round_number = int(getattr(state, "round_number", 0)) if state is not None else 0
        turn = int(getattr(state, "current_turn", 0)) if state is not None else 0
        for mutation in getattr(notification, "mutation", ()):
            which = mutation.WhichOneof("mutation")
            if which is None:
                continue
            record: dict[str, Any] = {
                "round": round_number,
                "turn": turn,
                "kind": which,
            }
            if which == "skill_used":
                skill = mutation.skill_used
                record.update(
                    {
                        "who": int(skill.who),
                        "skill_definition_id": int(skill.skill_definition_id),
                        "skill_type": _enum_name(skill, "skill_type"),
                        "caller_definition_id": int(skill.caller_definition_id),
                    }
                )
            elif which == "damage":
                damage = mutation.damage
                record.update(
                    {
                        "value": int(damage.value),
                        "target_id": int(damage.target_id),
                        "target_definition_id": int(damage.target_definition_id),
                        "source_id": int(damage.source_id),
                        "source_definition_id": int(damage.source_definition_id),
                        "reaction": int(damage.reaction_type),
                        "old_health": int(damage.old_health),
                        "new_health": int(damage.new_health),
                        "cause_defeated": bool(damage.cause_defeated),
                        "heal_kind": int(damage.heal_kind),
                    }
                )
            elif which == "set_player_flag":
                flag = mutation.set_player_flag
                record.update(
                    {
                        "who": int(flag.who),
                        "flag": _enum_name(flag, "flag_name"),
                        "value": bool(flag.flag_value),
                    }
                )
            elif which == "player_status_change":
                status = mutation.player_status_change
                record.update({"who": int(status.who), "status": int(status.status)})
            elif which == "change_phase":
                phase = mutation.change_phase
                record.update(
                    {
                        "phase": _enum_name(phase, "new_phase"),
                        "has_change": bool(phase.has_change),
                    }
                )
            elif which == "set_winner":
                record.update({"winner": int(mutation.set_winner.winner)})
            elif which == "switch_active":
                switch = mutation.switch_active
                record.update(
                    {
                        "who": int(switch.who),
                        "from": int(switch.from_),
                        "to": int(switch.to),
                    }
                    if hasattr(switch, "from_")
                    else {"who": int(switch.who)}
                )
            _MUTATIONS.append(record)


# --------------------------------------------------------------------------- #
# policy wrapper: snapshot the acting player's public view at every decision
# --------------------------------------------------------------------------- #
@dataclass
class TracePolicy:
    inner: Any
    who: int
    steps: list[dict[str, Any]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return getattr(self.inner, "name", "policy")

    def choose(self, built: Any) -> int:
        view = built.context.player_view
        self.steps.append(
            {
                "who": int(self.who),
                "request_type": str(built.context.request_type.value),
                "round": int(view.round_number) if view is not None else 0,
                "turn": int(view.current_turn) if view is not None else 0,
                "phase": str(view.phase) if view is not None else "",
                "players": [_player_view(view, who) for who in (0, 1)]
                if view is not None
                else [],
                "legal": [
                    str(getattr(spec, "label", ""))
                    for spec in built.context.legal_low_level_specs
                ],
            }
        )
        return int(self.inner.choose(built))


def _player_view(view: Any, who: int) -> dict[str, Any]:
    player = view.players[int(who)]
    return {
        "characters": [
            {
                "definition_id": int(character.definition_id),
                "health": int(character.health),
                "max_health": int(character.max_health),
                "energy": int(character.energy),
                "defeated": bool(character.defeated),
                "active": bool(character.is_active),
                "aura": int(character.aura),
                "entities": [int(entity.definition_id) for entity in character.entities],
            }
            for character in player.characters
        ],
        "dice": [int(die) for die in player.dice],
        "hand": [int(card.definition_id) for card in player.hand_cards],
        "pile": [int(card.definition_id) for card in player.pile_cards],
        "declared_end": bool(player.declared_end),
    }


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def matrix_games(min_rounds: int) -> list[tuple[str, str, int]]:
    """All ``(stage, deck, seed)`` rows of the era matrix with ``rounds >= n``."""
    path = data_dir("runs", "era_matrix.jsonl")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [
        (str(row["stage"]), str(row["deck"]), int(row["seed"]))
        for row in rows
        if int(row["rounds"]) >= int(min_rounds)
    ]


def load_deck(deck_name: str) -> dict[str, Any]:
    payload = json.loads(ERA_CONFIG.read_text(encoding="utf-8"))
    try:
        return payload["decks"][deck_name]
    except KeyError as exc:  # pragma: no cover - CLI validation
        raise SystemExit(f"unknown era deck {deck_name!r}") from exc


def _name(definition_id: int, *, kind: str = "card") -> str:
    from agents.scripted.assets import load_assets

    assets = load_assets()
    catalog = {
        "character": assets.characters,
        "skill": assets.skills,
    }.get(kind, assets.cards)
    info = catalog.get(int(definition_id))
    return str(getattr(info, "name", "")) or f"#{int(definition_id)}"


def _owner_map(final: Any) -> dict[int, int]:
    """entity id -> owner, from the final snapshot (character ids are stable)."""
    owner: dict[int, int] = {}
    if final is None:
        return owner
    for who, player in enumerate(final.players):
        for character in player.characters:
            owner[int(character.id)] = who
            for entity in character.entities:
                owner[int(entity.id)] = who
    return owner


def _final_summary(final: Any) -> dict[str, Any]:
    if final is None:
        return {}
    return {
        "phase": final.phase,
        "round": int(final.round_number),
        "turn": int(final.current_turn),
        "winner": final.winner,
        "players": [
            {
                "active_character_id": player.active_character_id,
                "characters": [
                    {
                        "definition_id": int(character.definition_id),
                        "name": _name(int(character.definition_id), kind="character"),
                        "health": int(character.health),
                        "max_health": int(character.max_health),
                        "energy": int(character.energy),
                        "max_energy": int(character.max_energy),
                        "defeated": bool(character.defeated),
                        "active": bool(character.is_active),
                        "aura": int(character.aura),
                        "entities": [
                            {
                                "definition_id": int(entity.definition_id),
                                "name": _name(int(entity.definition_id)),
                                "variables": dict(entity.variables),
                            }
                            for entity in character.entities
                        ],
                    }
                    for character in player.characters
                ],
                "hand_size": len(player.hand_cards),
                "hand": [int(card.definition_id) for card in player.hand_cards],
                "pile_size": len(player.pile_cards),
                "dice": [int(die) for die in player.dice],
                "declared_end": bool(player.declared_end),
                "supports": [int(entity.definition_id) for entity in player.supports],
                "summons": [int(entity.definition_id) for entity in player.summons],
            }
            for player in final.players
        ],
    }


def _round_table(steps: list[dict[str, Any]], final: Any) -> list[dict[str, Any]]:
    """HP / damage / skill use aggregated by round."""
    by_round: dict[int, dict[str, Any]] = defaultdict(
        lambda: {
            "first": None,
            "last": None,
            # ``attempted`` sums DamageEM.value (a ward/immunity still reports
            # the incoming number); ``effective`` is the real HP loss.
            "attempted": {0: 0, 1: 0},
            "effective": {0: 0, 1: 0},
            "absorbed": {0: 0, 1: 0},
            "unattributed": 0,
            "heal": {0: 0, 1: 0},
            "skills": {0: [], 1: []},
            "ends": {0: 0, 1: 0},
        }
    )
    for step in steps:
        bucket = by_round[int(step["round"])]
        if bucket["first"] is None and step["players"]:
            bucket["first"] = step["players"]
        if step["players"]:
            bucket["last"] = step["players"]
    # Final snapshot closes round 15 (the last decision view predates the end
    # phase: end-phase draws / summon ticks / step_round happen after it).
    owner = _owner_map(final)
    for mutation in _MUTATIONS:
        bucket = by_round[int(mutation["round"])]
        if mutation["kind"] == "damage":
            target = owner.get(int(mutation["target_id"]))
            if target is None:
                target = _owner_by_definition(mutation["target_definition_id"], final)
            value = int(mutation["value"])
            lost = max(0, int(mutation["old_health"]) - int(mutation["new_health"]))
            if mutation.get("heal_kind"):
                if target is not None:
                    bucket["heal"][target] += value
            elif target is not None:
                bucket["attempted"][target] += value
                bucket["effective"][target] += lost
                bucket["absorbed"][target] += max(0, value - lost)
            else:
                bucket["unattributed"] += value
        elif mutation["kind"] == "skill_used":
            who = int(mutation["who"])
            name = _name(int(mutation["skill_definition_id"]), kind="skill")
            bucket["skills"][who].append(name)
        elif mutation["kind"] == "set_player_flag":
            if mutation.get("flag", "").endswith("DECLARED_END") and mutation.get("value"):
                bucket["ends"][int(mutation["who"])] += 1
    table: list[dict[str, Any]] = []
    for round_number in sorted(by_round):
        bucket = by_round[round_number]
        first = bucket["first"] or []
        last = bucket["last"] or []
        entry = {
            "round": int(round_number),
            "skills": {who: bucket["skills"][who] for who in (0, 1)},
            "ends": bucket["ends"],
            "attempted": bucket["attempted"],
            "effective": bucket["effective"],
            "absorbed": bucket["absorbed"],
            "unattributed": bucket["unattributed"],
            "heal": bucket["heal"],
            "hp_start": {
                who: [character["health"] for character in player["characters"]]
                for who, player in enumerate(first)
            },
            "hp_last": {
                who: [character["health"] for character in player["characters"]]
                for who, player in enumerate(last)
            },
            "hand_last": {
                who: len(player["hand"]) for who, player in enumerate(last)
            },
            "pile_last": {
                who: len(player["pile"]) for who, player in enumerate(last)
            },
        }
        table.append(entry)
    # A round's last *decision* precedes its end phase (end-phase draws, summon
    # ticks, stepRound). The authoritative "after round r" board is therefore
    # round r+1's first decision view; the last round is closed by the final
    # snapshot.
    for index, entry in enumerate(table):
        next_board = table[index + 1]["hp_start"] if index + 1 < len(table) else {}
        if next_board:
            entry["hp_end"] = next_board
            next_first = by_round[table[index + 1]["round"]]["first"] or []
            entry["hand_end"] = {
                who: len(player["hand"]) for who, player in enumerate(next_first)
            }
            entry["pile_end"] = {
                who: len(player["pile"]) for who, player in enumerate(next_first)
            }
        elif index + 1 < len(table):
            entry["hp_end"] = entry["hp_last"]
        elif final is not None:
            entry["hp_end"] = {
                who: [int(character.health) for character in player.characters]
                for who, player in enumerate(final.players)
            }
            entry["hand_end"] = {who: len(player.hand_cards) for who, player in enumerate(final.players)}
            entry["pile_end"] = {who: len(player.pile_cards) for who, player in enumerate(final.players)}
    return table


def _owner_by_definition(definition_id: int, final: Any) -> int | None:
    if final is None:
        return None
    for who, player in enumerate(final.players):
        for character in player.characters:
            if int(character.definition_id) == int(definition_id):
                return who
    return None


def _features(table: list[dict[str, Any]]) -> dict[str, Any]:
    """Coarse per-game signals used to classify a long game.

    ``dealt`` is damage *inflicted by* a player (i.e. HP lost by the opponent);
    ``absorbed_by_self`` is damage the opponent attempted that this player's
    shields/wards/immunities cancelled. Keeping the two directions explicit
    avoids the classic "who is zero?" mix-up.
    """
    features: dict[str, Any] = {"rounds": len(table)}
    # The cap leaves a trailing round with no decisions (the engine steps to
    # round 15 and ends); "last two rounds" means the last two *played* rounds.
    played = [entry for entry in table if entry["skills"][0] or entry["skills"][1]] or table
    for who in (0, 1):
        dealt = [int(entry["effective"][1 - who]) for entry in played]
        taken = [int(entry["effective"][who]) for entry in table]
        absorbed_by_self = [int(entry["absorbed"][who]) for entry in table]
        attack_rounds = sum(1 for entry in table if entry["skills"][who])
        features[f"profile_{who}"] = {
            "dealt_total": sum(dealt),
            "taken_total": sum(taken),
            "absorbed_by_self_total": sum(absorbed_by_self),
            "zero_damage_rounds": sum(1 for value in dealt if value == 0),
            "attack_rounds": attack_rounds,
            "last_two_rounds_damage_dealt": dealt[-2:],
            "max_zero_streak": _max_streak(dealt),
        }
    return features


def _max_streak(values: list[int]) -> int:
    best = current = 0
    for value in values:
        current = current + 1 if int(value) == 0 else 0
        best = max(best, current)
    return best


def _event_lines(limit: int = MAX_EVENTS) -> list[str]:
    lines: list[str] = []
    for mutation in _MUTATIONS[-limit:]:
        kind = mutation["kind"]
        prefix = f"r{mutation['round']:>2} "
        if kind == "skill_used":
            lines.append(
                f"{prefix}P{mutation['who']} skill {_name(mutation['skill_definition_id'], kind='skill')} "
                f"({mutation['skill_type']})"
            )
        elif kind == "damage":
            lines.append(
                f"{prefix}damage {mutation['value']} -> def {mutation['target_definition_id']} "
                f"({mutation['old_health']}->{mutation['new_health']}, reaction={mutation['reaction']}, "
                f"defeated={mutation['cause_defeated']})"
            )
        elif kind == "set_player_flag" and "DECLARED_END" in str(mutation.get("flag", "")):
            lines.append(f"{prefix}P{mutation['who']} declared_end={mutation['value']}")
        elif kind == "step_round":
            lines.append(f"{prefix}-- round ended --")
        elif kind == "set_winner":
            lines.append(f"{prefix}winner={mutation['winner']}")
        elif kind == "change_phase":
            lines.append(f"{prefix}phase -> {mutation.get('phase')}")
        elif kind == "switch_active":
            lines.append(f"{prefix}P{mutation.get('who')} switch active")
    return lines


def _decision_summary(steps: list[dict[str, Any]], record: Any) -> dict[str, Any]:
    counts = {0: Counter(), 1: Counter()}
    for step in steps:
        counts[int(step["who"])][step["request_type"]] += 1
    ends = {0: 0, 1: 0}
    for decision in record.decisions:
        if decision.chosen_kind == "action_declare_end":
            ends[int(decision.player)] += 1
    return {
        "requests": {who: dict(counter) for who, counter in counts.items()},
        "declare_end_decisions": ends,
    }


def analyse(stage_name: str, deck_name: str, seed: int, *, keep_mutations: bool = True) -> dict[str, Any]:
    from scripts.era_decks import _scripted_policy

    deck_payload = load_deck(deck_name)
    deck = DeckSpec(
        name=str(deck_payload["name"]),
        characters=tuple(int(c) for c in deck_payload["characters"]),
        cards=tuple(int(c) for c in deck_payload["cards"]),
    )
    stage = get_stage(stage_name)
    npc_deck = DeckSpec(
        name=f"npc_{stage.name}",
        characters=stage.deck_characters,
        cards=deck.cards,
    )

    del _MUTATIONS[:]
    p0 = TracePolicy(_scripted_policy(deck_payload), who=0)
    p1 = TracePolicy(npc_policy_for_stage(stage), who=1)

    import envs.match as match_mod

    original = match_mod.PolicyPlayer
    match_mod.PolicyPlayer = ProbePlayer
    try:
        record = run_match(
            deck,
            npc_deck,
            p0,
            p1,
            seed=seed,
            version=ERA_VERSION,
            record_decisions=True,
            record_events=True,
            record_final_state=True,
        )
    finally:
        match_mod.PolicyPlayer = original

    final = None
    if record.final_state_json:
        try:
            final = snapshot_state(None, record.final_state_json)
        except Exception:
            final = None
    table = _round_table(p0.steps + p1.steps, final)
    summary = {
        "stage": stage_name,
        "deck": deck_name,
        "seed": int(seed),
        "version": ERA_VERSION,
        "winner": record.winner,
        "rounds": int(record.rounds),
        "truncated": bool(record.truncated),
        "error": record.error,
        "decisions0": int(record.decisions0),
        "decisions1": int(record.decisions1),
        "fallbacks0": int(record.fallbacks0),
        "fallbacks1": int(record.fallbacks1),
        "final": _final_summary(final),
        "rounds_table": table,
        "features": _features(table),
        "decisions": _decision_summary(p0.steps + p1.steps, record),
        "events_tail": _event_lines(),
        "damage_events": sum(1 for m in _MUTATIONS if m["kind"] == "damage"),
        "skill_events": sum(1 for m in _MUTATIONS if m["kind"] == "skill_used"),
        "mutations": list(_MUTATIONS) if keep_mutations else [],
    }
    _print_game(summary)
    return summary


def _fmt_hp(player: dict[str, Any]) -> str:
    return " ".join(
        f"{character['name']}={character['health']}/{character['max_health']}"
        f"{'†' if character['defeated'] else ('*' if character['active'] else '')}"
        for character in player["characters"]
    )


def _print_game(summary: dict[str, Any]) -> None:
    print("=" * 100)
    print(
        f"{summary['stage']} / {summary['deck']} / seed {summary['seed']} -> "
        f"winner={summary['winner']} rounds={summary['rounds']} "
        f"error={summary['error']} truncated={summary['truncated']}"
    )
    final = summary["final"]
    for who, player in enumerate(final.get("players", [])):
        print(
            f"  P{who}: {_fmt_hp(player)}\n"
            f"       hand={player['hand_size']} pile={player['pile_size']} "
            f"dice={player['dice']} declared_end={player['declared_end']} "
            f"supports={player['supports']}"
        )
    print(f"  turn={final.get('turn')} phase={final.get('phase')}")
    print(f"  decisions={summary['decisions']}")
    print(
        "  per-round: round | effective dmg->P0/P1 (absorbed) | skills P0/P1 | "
        "ends P0/P1 | HP at end of round (P0 / P1)"
    )
    for entry in summary["rounds_table"]:
        hp0_source = entry.get("hp_end", entry["hp_last"]).get(0, [])
        hp1_source = entry.get("hp_end", entry["hp_last"]).get(1, [])
        hp0 = "/".join(str(value) for value in hp0_source)
        hp1 = "/".join(str(value) for value in hp1_source)
        print(
            f"    r{entry['round']:>2} | {entry['effective'][0]:>2}/{entry['effective'][1]:<2} "
            f"({entry['absorbed'][0]}/{entry['absorbed'][1]}) | "
            f"{len(entry['skills'][0])}/{len(entry['skills'][1])} | "
            f"{entry['ends'][0]}/{entry['ends'][1]} | "
            f"[{hp0}] / [{hp1}]"
        )
    print(f"  last {len(summary['events_tail'])} events:")
    for line in summary["events_tail"]:
        print(f"    {line}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Replay era-matrix games with a forensic trace: per-round damage/HP, "
            "skills, declare-end counts, final hands/piles/dice and the last events."
        )
    )
    parser.add_argument("--stage")
    parser.add_argument("--deck")
    parser.add_argument("--seed", type=int)
    parser.add_argument(
        "--json-out",
        type=Path,
        default=data_dir("runs", "stall_probe.jsonl"),
        help="raw trace output (default: data/runs/stall_probe.jsonl, gitignored)",
    )
    parser.add_argument(
        "--no-mutations",
        action="store_true",
        help="omit the full mutation list from the JSON output",
    )
    parser.add_argument(
        "--check-round-cap",
        action="store_true",
        help="print the engine default maxRoundsCount and the attribute id",
    )
    parser.add_argument(
        "--min-rounds",
        type=int,
        help="replay every data/runs/era_matrix.jsonl row with rounds >= N instead of the 5 draws",
    )
    args = parser.parse_args(argv)

    if args.check_round_cap:
        import gitcg
        from gitcg import CreateParam, Game, low_level

        deck_payload = load_deck(FLAGGED[0][1])
        param = CreateParam(version=ERA_VERSION)
        param.set_characters(0, list(deck_payload["characters"]))
        param.set_cards(0, list(deck_payload["cards"]))
        param.set_characters(1, list(deck_payload["characters"]))
        param.set_cards(1, list(deck_payload["cards"]))
        state_json = Game(create_param=param).state().json()
        config = json.loads(state_json)["store"][0]
        param2 = CreateParam(version=ERA_VERSION)
        param2.set_characters(0, list(deck_payload["characters"]))
        param2.set_cards(0, list(deck_payload["cards"]))
        param2.set_characters(1, list(deck_payload["characters"]))
        param2.set_cards(1, list(deck_payload["cards"]))
        param2.set_attr(low_level.ATTR_STATE_CONFIG_MAX_ROUNDS_COUNT, 21)
        state_json2 = Game(create_param=param2).state().json()
        config2 = json.loads(state_json2)["store"][0]
        print(
            json.dumps(
                {
                    "gitcg": gitcg.__version__ if hasattr(gitcg, "__version__") else "?",
                    "attribute": int(low_level.ATTR_STATE_CONFIG_MAX_ROUNDS_COUNT),
                    "default_config": config,
                    "with_attr_21": config2,
                },
                indent=2,
            )
        )
        return 0

    games: list[tuple[str, str, int]]
    if args.stage and args.deck and args.seed is not None:
        games = [(args.stage, args.deck, int(args.seed))]
    elif args.stage or args.deck or args.seed is not None:
        parser.error("--stage, --deck and --seed must be given together")
    elif args.min_rounds is not None:
        games = matrix_games(int(args.min_rounds))
        if not games:
            raise SystemExit(f"no era_matrix rows with rounds >= {args.min_rounds}")
        print(f"replaying {len(games)} matrix games with rounds >= {args.min_rounds}")
    else:
        games = list(FLAGGED)

    rows = [
        analyse(stage, deck, seed, keep_mutations=not args.no_mutations)
        for stage, deck, seed in games
    ]
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        with args.json_out.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"\nwrote {len(rows)} trace(s) to {args.json_out}")

    # Compact table for the report.
    print("\nCOMPACT: stage/deck/seed rounds winner | dmg0/dmg1 last2(0/1) | "
          "hand0/pile0 hand1/pile1 | ends0/ends1")
    for row in rows:
        features = row["features"]
        final = row["final"]["players"]
        print(
            f"  {row['stage']}/{row['deck']}/{row['seed']} r{row['rounds']} w={row['winner']} | "
            f"{features['profile_0']['dealt_total']}/{features['profile_1']['dealt_total']} "
            f"{features['profile_0']['last_two_rounds_damage_dealt']}/"
            f"{features['profile_1']['last_two_rounds_damage_dealt']} | "
            f"{final[0]['hand_size']}/{final[0]['pile_size']} {final[1]['hand_size']}/{final[1]['pile_size']} | "
            f"{row['decisions']['declare_end_decisions'].get('0', '-')}/"
            f"{row['decisions']['declare_end_decisions'].get('1', '-')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
