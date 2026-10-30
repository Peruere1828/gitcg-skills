"""In-game LLM-assisted policy (PLAN.md WS4, D5).

This is the **LLM-assisted mode**: a base policy proposes, and an LLM re-ranks /
validates among a small candidate set at selected decision points, with a hard
per-game call budget and a forced fallback to the base policy. Strength
acceptance (G1, M4) is measured in **pure-policy mode**; this module must never be
used there (PLAN.md §6.3).

Information safety: only ``context.player_view`` (public + own private info) is
rendered into the prompt; hidden opponent state is never sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from reps.action_adapter import BuiltDecisionContext
from reps.schema import DecisionContext, OptionKind

from envs.policy import Policy


@dataclass
class AssistConfig:
    budget_per_game: int = 8
    max_candidates: int = 12
    min_legal_options: int = 3
    request_types: tuple[str, ...] = ("action",)
    temperature: float = 0.0
    # In-game decisions want low latency, so default to a non-reasoning model.
    # Deep offline review (coach) can use a reasoning model instead.
    model: str | None = "deepseek-chat"
    # Reasoning models (e.g. deepseek-flash) spend tokens on hidden reasoning
    # before emitting `content`; too small a budget yields empty content.
    max_tokens: int = 512


@dataclass
class Intervention:
    """One recorded LLM intervention (for logs / distillation / evaluation)."""

    step_index: int
    request_type: str
    called: bool
    n_candidates: int
    base_code: int
    llm_code: int | None
    final_code: int
    changed: bool
    reason: str | None = None
    raw: str | None = None
    latency_seconds: float = 0.0
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_index": self.step_index,
            "request_type": self.request_type,
            "called": self.called,
            "n_candidates": self.n_candidates,
            "base_code": self.base_code,
            "llm_code": self.llm_code,
            "final_code": self.final_code,
            "changed": self.changed,
            "reason": self.reason,
            "error": self.error,
            "latency_seconds": round(self.latency_seconds, 3),
        }


def _kind_label(kind: OptionKind) -> str:
    return kind.value.replace("action_", "").replace("_", " ")


def _character_by_slot(state: Any, owner: str, index: int) -> Any | None:
    if index < 0:
        return None
    seq = state.characters
    return seq[index] if index < len(seq) else None


class ContextDescriber:
    """Render a decision context and its candidates as compact text via assets."""

    def __init__(self, assets: Any, opponent_name: str = "unknown") -> None:
        self.assets = assets
        self.opponent_name = opponent_name

    def _char_name(self, definition_id: int) -> str:
        try:
            return self.assets.character(int(definition_id)).name
        except Exception:
            return f"char#{definition_id}"

    def _card_name(self, definition_id: int) -> str:
        try:
            return self.assets.card(int(definition_id)).name
        except Exception:
            return f"card#{definition_id}"

    def describe_state(self, context: DecisionContext) -> str:
        state = context.player_view
        assert state is not None
        me = state.players[context.acting_player]
        opp = state.players[1 - context.acting_player]
        lines = [
            f"round={state.round_number} phase={state.phase} turn={state.current_turn}",
            "my characters: "
            + ", ".join(
                f"{self._char_name(c.definition_id)} hp={c.health}/{c.max_health} "
                f"energy={c.energy}/{c.max_energy} aura={c.aura}"
                + (" ACTIVE" if c.is_active else "")
                + (" DEFEATED" if c.defeated else "")
                for c in me.characters
            ),
            "my dice: "
            + ", ".join(str(d) for d in me.dice)
            + f" (n={len(me.dice)})",
            "my hand: "
            + ", ".join(self._card_name(c.definition_id) for c in me.hand_cards),
            f"opponent({self.opponent_name}) characters: "
            + ", ".join(
                f"{self._char_name(c.definition_id)} hp={c.health}/{c.max_health} "
                f"energy={c.energy}/{c.max_energy} aura={c.aura}"
                + (" ACTIVE" if c.is_active else "")
                + (" DEFEATED" if c.defeated else "")
                for c in opp.characters
            ),
            f"opponent hand size={len(opp.hand_cards)} dice≈{len(opp.dice)}",
            f"my declared_end={me.declared_end}",
        ]
        return "\n".join(lines)

    def describe_candidate(self, context: DecisionContext, spec: Any, index: int) -> str:
        kind = spec.kind
        if kind == OptionKind.ACTION_USE_SKILL:
            try:
                skill = self.assets.skill(int(spec.subject_definition_id))
                subject = f"{skill.character_name or ''} skill {skill.name}".strip()
            except Exception:
                subject = f"skill#{spec.subject_definition_id}"
        elif kind == OptionKind.ACTION_PLAY_CARD:
            subject = f"card {self._card_name(spec.subject_definition_id)}"
        elif kind == OptionKind.ACTION_SWITCH_ACTIVE:
            subject = f"switch to {self._char_name(spec.subject_definition_id)}"
        elif kind == OptionKind.ACTION_ELEMENTAL_TUNING:
            subject = f"tune {self._card_name(spec.subject_definition_id)}"
        else:
            subject = "declare end"
        targets = ", ".join(
            f"{t.owner}:{t.zone}[{t.index}]" for t in spec.target_slots
        )
        dice = len(spec.used_dice)
        target_part = f" targets={targets}" if targets else ""
        return f"[{index}] {_kind_label(kind)}: {subject}{target_part} (dice={dice})"

    def build_prompt(
        self, context: DecisionContext, candidates: Sequence[tuple[int, Any]]
    ) -> list[dict[str, str]]:
        system = (
            "You are a Genius Invokation TCG (Genshin card game) decision assistant. "
            "You are given the public game state and a numbered list of legal actions. "
            "Choose the single best action index. Reply with ONLY minified JSON "
            '{"choice": <int>, "reason": "<=20 words"}.'
        )
        action_lines = [
            self.describe_candidate(context, spec, i)
            for i, (_code, spec) in enumerate(candidates)
        ]
        user = (
            f"{self.describe_state(context)}\n\n"
            f"request={context.request_type.value}\n"
            "legal actions:\n" + "\n".join(action_lines)
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]


class LLMAssistPolicy:
    """Wrap a base policy with optional LLM re-ranking at selected decisions."""

    def __init__(
        self,
        base: Policy,
        client: Any,
        *,
        assets: Any,
        opponent_name: str = "unknown",
        config: AssistConfig | None = None,
        on_intervention: Callable[[Intervention], None] | None = None,
    ) -> None:
        self.base = base
        self.client = client
        self.config = config or AssistConfig()
        self.describer = ContextDescriber(assets, opponent_name=opponent_name)
        self.interventions: list[Intervention] = []
        self.on_intervention = on_intervention
        self.calls = 0
        self.name = f"llm+{getattr(base, 'name', 'base')}"

    # -- candidate selection ----------------------------------------------------
    def _candidates(
        self, built: BuiltDecisionContext, base_code: int
    ) -> list[tuple[int, Any]]:
        codes = list(built.context.legal_low_level_codes)
        specs = list(built.context.legal_low_level_specs)
        pairs = list(zip((int(c) for c in codes), specs))
        # Keep the base choice plus a diverse spread of kinds/subjects.
        selected: list[tuple[int, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for code, spec in pairs:
            key = (spec.kind, spec.subject_definition_id, spec.target_slots)
            if key in seen:
                continue
            seen.add(key)
            selected.append((code, spec))
            if len(selected) >= self.config.max_candidates:
                break
        if all(code != base_code for code, _ in selected):
            for code, spec in pairs:
                if code == base_code:
                    selected.append((code, spec))
                    break
        # Base choice first, so index 0 is always the fallback the LLM can echo.
        selected.sort(key=lambda item: item[0] != base_code)
        return selected

    def _should_call(self, built: BuiltDecisionContext) -> bool:
        if self.calls >= self.config.budget_per_game:
            return False
        if built.context.request_type.value not in self.config.request_types:
            return False
        if len(built.context.legal_low_level_codes) < self.config.min_legal_options:
            return False
        return True

    def choose(self, built: BuiltDecisionContext) -> int:
        base_code = int(self.base.choose(built))
        if not self._should_call(built):
            return base_code

        candidates = self._candidates(built, base_code)
        if len(candidates) < 2:
            return base_code
        prompt = self.describer.build_prompt(built.context, candidates)
        import time

        started = time.perf_counter()
        raw = self.client.chat(
            prompt,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            response_format_json=True,
            model=self.config.model,
        )
        latency = time.perf_counter() - started
        self.calls += 1

        error: str | None = None
        llm_code: int | None = None
        reason: str | None = None
        from common.llm import extract_json_object  # local import avoids cycle

        parsed = extract_json_object(raw)
        if parsed is None:
            error = "unparseable"
        else:
            try:
                index = int(parsed.get("choice"))
            except (TypeError, ValueError):
                index = -1
            reason = str(parsed.get("reason") or "")[:200]
            if 0 <= index < len(candidates):
                llm_code = int(candidates[index][0])
            else:
                error = "choice out of range"

        final_code = llm_code if llm_code is not None else base_code
        intervention = Intervention(
            step_index=built.context.step_index,
            request_type=built.context.request_type.value,
            called=True,
            n_candidates=len(candidates),
            base_code=base_code,
            llm_code=llm_code,
            final_code=final_code,
            changed=final_code != base_code,
            reason=reason,
            raw=raw if raw is not None else None,
            latency_seconds=latency,
            error=error,
        )
        self.interventions.append(intervention)
        if self.on_intervention is not None:
            self.on_intervention(intervention)
        return final_code


def build_llm_assist_policy(
    deck_slug: str,
    *,
    client: Any,
    base: Policy | None = None,
    config: AssistConfig | None = None,
    seed: int | None = None,
    opponent_name: str | None = None,
) -> LLMAssistPolicy:
    """Build an LLM-assisted policy for one scripted deck slug.

    The base (pure-policy) proposer defaults to that deck's scripted expert, which
    keeps the LLM in a re-ranking role rather than a generator (PLAN.md WS4).

    ``deck_slug`` is *our* deck (the base proposer). Pass ``opponent_name`` with the
    actual adversary's name so prompts label the opponent correctly; it defaults to
    ``deck_slug`` only for the single-pairing case where the two coincide.
    """
    from envs.policy import _asset_catalog, expert_policy

    base_policy = base or expert_policy(deck_slug, seed=seed)
    return LLMAssistPolicy(
        base_policy,
        client,
        assets=_asset_catalog(),
        opponent_name=opponent_name or deck_slug,
        config=config,
    )
