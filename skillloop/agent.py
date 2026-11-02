"""The LLM decision policy: one model call per strategic decision, with a fallback.

Two design choices worth stating because they shape every experiment:

* **Fallback, not replacement** — mechanical decisions (forced picks, card selection,
  dice rerolls) go to the deck's scripted expert. Only strategic request types are
  sent to the model. This keeps the agent legal and cheap, and it makes the
  *marginal* contribution of the skill library measurable instead of being confounded
  by a weak executor.
* **Stable prefix** — the system message (role + task + injected skills) is built
  once per game, so consecutive decisions share a byte-identical prefix and can hit
  the provider's prompt cache.

INNOVATION: per-decision logging with prompt hash + skill citation, so a library
version can be audited after the fact (``skillloop.gate``).
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from common.llm import extract_json_object
from reps.action_adapter import BuiltDecisionContext
from reps.schema import OptionKind
from skillloop.cards import card_kind, skill_kind
from skillloop.library import SkillLibrary
from skillloop.render import (
    ProgressSignals,
    StateRenderer,
    TaskSpec,
    progress_block,
    select_candidates,
)
from skillloop.schema import Skill


@dataclass(frozen=True)
class AgentConfig:
    model: str | None = None            # None -> whatever the client/config says
    # In-game decisions run with ``reasoning_effort="none"``, so the reply is a bare
    # ``{"choice": i, "used": [...]}`` JSON object (~10-20 tokens); 64 leaves margin.
    # Raising this without re-measuring is how hidden reasoning once inflated
    # completion tokens and cost per game ~10x.
    reasoning_effort: str | None = "none"
    max_tokens: int = 64
    temperature: float = 0.0            # determinism is what makes paired seeds valid
    max_calls_per_game: int = 80
    request_types: tuple[str, ...] = ("action", "switch_hands", "choose_active")
    candidate_cap: int = 16
    top_k: int = 5                      # skill retrieval depth (the course's "Top-k" factor)
    # Frozen stage loadout (see ``skillloop.loadout``). When non-empty, these exact
    # skills replace BM25 retrieval for the whole game; order is prompt order.
    pinned_skills: tuple[str, ...] = ()


@dataclass
class Decision:
    step: int
    request: str
    n_candidates: int
    choice: int | None
    used: tuple[str, ...] = ()
    error: str | None = None
    latency: float = 0.0
    system_hash: str = ""
    http_calls: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "request": self.request,
            "n_candidates": self.n_candidates,
            "choice": self.choice,
            "used": list(self.used),
            "error": self.error,
            "latency": round(self.latency, 3),
            "system_hash": self.system_hash,
            "http_calls": self.http_calls,
        }


def parse_choice(raw: str | None, n_candidates: int) -> tuple[int | None, tuple[str, ...], str | None]:
    """Parse ``{"choice": i, "used": [...]}``; returns ``(index, used, error)``."""
    if raw is None:
        return None, (), "empty_response"
    parsed = extract_json_object(raw)
    if parsed is None:
        return None, (), "unparseable"
    try:
        index = int(parsed.get("choice"))
    except (TypeError, ValueError):
        return None, (), "choice_not_int"
    if not 0 <= index < n_candidates:
        return None, (), "choice_out_of_range"
    used = parsed.get("used") or []
    if isinstance(used, str):
        used = [used]
    used_ids = tuple(str(item) for item in used if isinstance(item, (str, int)))[:2]
    return index, used_ids, None


class SkillAgentPolicy:
    """LLM policy over the legal candidates, with the deck expert as fallback."""

    def __init__(
        self,
        base: Any,
        client: Any,
        *,
        renderer: StateRenderer,
        task: TaskSpec,
        library: SkillLibrary | None = None,
        rules_note: str = "",
        config: AgentConfig | None = None,
        on_decision: Callable[[Decision], None] | None = None,
    ) -> None:
        self.base = base
        self.client = client
        self.renderer = renderer
        self.task = task
        self.library = library
        self.rules_note = rules_note
        self.config = config or AgentConfig()
        self.on_decision = on_decision
        self.decisions: list[Decision] = []
        self.calls = 0
        self.name = f"llm+{getattr(base, 'name', 'base')}"
        self._system: dict[str, str] | None = None
        self._system_hash = ""
        self._skills: list[Skill] = []
        self._signals = ProgressSignals()

    # -- prompt ---------------------------------------------------------------
    def _system_message(self, context: Any) -> dict[str, str]:
        """Build the stable prefix once per game (or when the library changes)."""
        if self._system is None:
            if self.library is not None:
                if self.config.pinned_skills:
                    # Frozen loadout: exactly these ids, in order; unknown ids skipped.
                    self._skills = [
                        skill
                        for skill_id in self.config.pinned_skills
                        if (skill := self.library.get(skill_id)) is not None
                    ]
                elif self.config.top_k > 0:
                    self._skills = self.library.retrieve(
                        self.task.describe(), self._context_dict(context), k=self.config.top_k
                    )
            if self.library is not None and not self.rules_note:
                self.rules_note = self.library.rules_text()
            self._system = self.renderer.system_message(
                task=self.task, skills=self._skills, rules_note=self.rules_note
            )
            self._system_hash = hashlib.sha1(self._system["content"].encode("utf-8")).hexdigest()[:12]
        return self._system

    def _context_dict(self, context: Any) -> dict[str, Any]:
        stage = self.task.stage
        # retrieval matches machine types (``win_within_rounds``), not the prompt text
        objectives = tuple(self.task.objective_types) or tuple(self.task.objectives)
        round_number = None
        if context.player_view is not None:
            round_number = int(context.player_view.round_number)
        return {"stage": stage, "objective_types": objectives, "round": round_number}

    # -- progress -------------------------------------------------------------
    def _observe(self, context: Any, code: int) -> None:
        """Count only what we can know from our own final action.

        Called for *every* final choice (model or expert fallback), so forced card
        plays and off-policy requests still count toward objective progress.
        """
        spec = next(
            (
                spec
                for candidate, spec in zip(
                    context.legal_low_level_codes, context.legal_low_level_specs
                )
                if int(candidate) == int(code)
            ),
            None,
        )
        if spec is not None:
            if spec.kind == OptionKind.ACTION_PLAY_CARD:
                kind = card_kind(int(spec.subject_definition_id or 0))
                if kind == "weapon":
                    self._signals.played_weapon += 1
                elif kind == "artifact":
                    self._signals.played_artifact += 1
                elif kind == "event":
                    self._signals.played_event += 1
                elif kind == "support":
                    self._signals.played_support += 1
                else:
                    self._signals.played_other += 1
            elif spec.kind == OptionKind.ACTION_USE_SKILL:
                if skill_kind(int(spec.subject_definition_id or 0)) == "burst":
                    self._signals.bursts += 1
        state = context.player_view
        if state is not None and 0 <= int(context.acting_player) < len(state.players):
            self._signals.defeated = sum(
                1 for character in state.players[int(context.acting_player)].characters
                if character.defeated
            )

    def _finish(self, context: Any, code: int) -> int:
        self._observe(context, int(code))
        return int(code)

    # -- policy ---------------------------------------------------------------
    def choose(self, built: BuiltDecisionContext) -> int:
        context = built.context
        base_code = int(self.base.choose(built))
        if context.request_type.value not in self.config.request_types:
            return self._finish(context, base_code)
        if self.calls >= self.config.max_calls_per_game:
            return self._finish(context, base_code)
        candidates = select_candidates(context, max_candidates=self.config.candidate_cap)
        if len(candidates) < 2:
            return self._finish(context, base_code)  # forced decision: no information for the LLM to add

        system = self._system_message(context)
        progress = progress_block(self.task, context, self._signals)
        user = self.renderer.user_message(context, candidates, progress=progress)
        started = time.perf_counter()
        raw = self.client.chat(
            [system, user],
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            response_format_json=True,
            model=self.config.model,
            reasoning_effort=self.config.reasoning_effort,
        )
        latency = time.perf_counter() - started
        self.calls += 1

        index, used, error = parse_choice(raw, len(candidates))
        if index is None:
            final_code = base_code
        else:
            final_code = int(candidates[index][0])
        retrieved = [skill.skill_id for skill in self._skills]
        used = tuple(skill_id for skill_id in used if skill_id in retrieved)
        decision = Decision(
            step=int(context.step_index),
            request=context.request_type.value,
            n_candidates=len(candidates),
            choice=index,
            used=used,
            error=error,
            latency=latency,
            system_hash=self._system_hash,
            http_calls=1,
        )
        self.decisions.append(decision)
        if self.on_decision is not None:
            self.on_decision(decision)
        return self._finish(context, final_code)
