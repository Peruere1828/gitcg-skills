"""Replay / coach Agent (PLAN.md WS4).

Consumes arena extraction logs (one JSON record per game, with per-decision
traces) and produces a *structured* review: mistake attributions, a game summary,
and deck-level lessons. Output is JSON so it can feed the training pipeline
directly (curriculum weighting, BC labels).

Client-optional: with no LLM client it degrades to a deterministic rule-based
review (still structured), so the pipeline never blocks on API availability.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Sequence

from common.llm import extract_json_object

_REVIEW_SYSTEM = (
    "You are a Genius Invokation TCG (Genshin card game) coach reviewing a game "
    "between scripted decks. Identify concrete mistakes (wrong action, wasted dice, "
    "bad switch/targeting, missed lethal) with the decision step index. Be specific "
    "and conservative; do not invent facts not in the timeline. Reply with ONLY "
    "minified JSON: {\"summary\": str, \"mistakes\": [{\"step\": int, \"player\": int, "
    "\"severity\": \"low|medium|high\", \"why\": str, \"better\": str}], "
    "\"deck_lessons\": [str]}."
)


def _name(assets: Any, definition_id: int, kind: str = "card") -> str:
    try:
        if kind == "character":
            return assets.character(int(definition_id)).name
        if kind == "skill":
            return assets.skill(int(definition_id)).name
        return assets.card(int(definition_id)).name
    except Exception:
        return f"{kind}#{definition_id}"


@dataclass
class GameTimeline:
    deck0: str
    deck1: str
    winner: int | None
    rounds: int
    lines: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        winner = "draw" if self.winner is None else f"player{self.winner} ({self.deck0 if self.winner == 0 else self.deck1})"
        header = f"decks: p0={self.deck0} vs p1={self.deck1}\nwinner: {winner}\nrounds: {self.rounds}"
        return header + "\n" + "\n".join(self.lines)


def _summarize_view(assets: Any, view: dict[str, Any] | None, acting: int) -> str:
    if not view:
        return "state=<unrecorded>"
    players = view.get("players") or ()
    if len(players) < 2:
        return "state=<malformed>"
    mine = players[acting]
    opp = players[1 - acting]

    def _chars(player: dict[str, Any]) -> str:
        out = []
        for c in player.get("characters", ())[:3]:
            tag = "ACTIVE" if c.get("is_active") else ""
            dead = "DEAD" if c.get("defeated") else ""
            out.append(
                f"{_name(assets, c.get('definition_id', 0), 'character')}"
                f"({c.get('health')}/{c.get('max_health')}hp,{c.get('energy')}en)"
                + (f"[{tag}{dead}]" if tag or dead else "")
            )
        return ", ".join(out)

    dice = mine.get("dice") or ()
    return (
        f"my[{_chars(mine)}] hand={len(mine.get('hand_cards', ()))} dice={list(dice)} "
        f"|| opp[{_chars(opp)}] hand={len(opp.get('hand_cards', ()))}"
    )


def build_timeline(match: dict[str, Any], *, assets: Any, max_decisions: int = 60) -> GameTimeline:
    timeline = GameTimeline(
        deck0=match.get("deck0", "?"),
        deck1=match.get("deck1", "?"),
        winner=match.get("winner"),
        rounds=int(match.get("rounds", 0)),
    )
    decisions = match.get("decisions") or []
    for decision in decisions[:max_decisions]:
        acting = int(decision.get("player", 0))
        opts = decision.get("legal_labels") or []
        line = (
            f"#{decision.get('step_index')} p{acting} {decision.get('request_type')}: "
            f"chose {decision.get('chosen_label')} "
            f"among {len(opts)} options; {_summarize_view(assets, decision.get('player_view'), acting)}"
        )
        if decision.get("fallback"):
            line += " [FALLBACK]"
        if decision.get("policy_error"):
            line += f" [POLICY_ERROR {decision['policy_error'][:60]}]"
        timeline.lines.append(line)
    if len(decisions) > max_decisions:
        timeline.lines.append(f"... ({len(decisions) - max_decisions} more decisions omitted)")
    return timeline


def rule_based_flags(match: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic mistakes detectable without an LLM."""
    flags: list[dict[str, Any]] = []
    for decision in match.get("decisions") or []:
        if decision.get("fallback"):
            flags.append(
                {
                    "step": decision.get("step_index"),
                    "player": decision.get("player"),
                    "severity": "medium",
                    "why": "agent produced an illegal/invalid action and fell back to declare_end",
                    "better": "fix action adapter / policy legality handling",
                }
            )
        if decision.get("policy_error"):
            flags.append(
                {
                    "step": decision.get("step_index"),
                    "player": decision.get("player"),
                    "severity": "high",
                    "why": f"policy raised: {decision['policy_error']}",
                    "better": "harden policy against this context",
                }
            )
    return flags


@dataclass
class ReplayCoach:
    assets: Any
    client: Any | None = None
    model: str | None = "deepseek-flash"  # offline review can afford a reasoning model
    max_tokens: int = 2048
    max_decisions: int = 60

    def review(self, match: dict[str, Any]) -> dict[str, Any]:
        timeline = build_timeline(match, assets=self.assets, max_decisions=self.max_decisions)
        flags = rule_based_flags(match)
        base: dict[str, Any] = {
            "deck0": timeline.deck0,
            "deck1": timeline.deck1,
            "winner": timeline.winner,
            "rounds": timeline.rounds,
            "rule_flags": flags,
            "llm": None,
        }
        if self.client is None:
            base["summary"] = (
                f"{timeline.deck0} vs {timeline.deck1}, winner={timeline.winner}, "
                f"{len(flags)} rule-based flags."
            )
            base["mistakes"] = flags
            base["deck_lessons"] = []
            return base

        prompt = [
            {"role": "system", "content": _REVIEW_SYSTEM},
            {"role": "user", "content": timeline.as_text()},
        ]
        raw = self.client.chat(
            prompt,
            model=self.model,
            max_tokens=self.max_tokens,
            response_format_json=True,
        )
        parsed = extract_json_object(raw)
        base["llm"] = {"raw": raw, "parsed": parsed is not None}
        if parsed is None:
            base["summary"] = "LLM review unavailable/parse-failed; rule-based only."
            base["mistakes"] = flags
            base["deck_lessons"] = []
            return base
        base["summary"] = str(parsed.get("summary", ""))
        llm_mistakes = parsed.get("mistakes") or []
        base["mistakes"] = list(llm_mistakes) + flags
        base["deck_lessons"] = [str(x) for x in (parsed.get("deck_lessons") or [])]
        return base


def load_matches(path: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                matches.append(json.loads(line))
    return matches


def review_games(
    matches: Sequence[dict[str, Any]], *, coach: ReplayCoach
) -> list[dict[str, Any]]:
    return [coach.review(match) for match in matches]
