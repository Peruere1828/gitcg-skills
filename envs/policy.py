"""Policy abstraction used by the environment layer.

A ``Policy`` maps a ``BuiltDecisionContext`` to a concrete low-level action code.
Keeping the interface at the action-code level means the same env can drive
scripted experts, legal-random/heuristic baselines, CVPN search agents, and
LLM-assisted agents without knowing their internals (PLAN.md WS0/WS4).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Protocol

from reps.action_adapter import BuiltDecisionContext
from reps.agents import HeuristicAgent, LegalRandomAgent
from reps.schema import ActionChoice


class Policy(Protocol):
    """Callable policy: pick one legal action code for the given context."""

    name: str

    def choose(self, built: BuiltDecisionContext) -> int:  # pragma: no cover - protocol
        ...


@dataclass
class ScriptedPolicy:
    """Adapter around the ported ``ExpertRuleAgent`` (one of the 22 scripted decks)."""

    agent: Any
    name: str = "scripted"

    def choose(self, built: BuiltDecisionContext) -> int:
        return int(self.agent.choose(built).action_code)


@dataclass
class BaselinePolicy:
    """Adapter around ``reps.agents`` baselines (legal-random / heuristic)."""

    agent: Any
    name: str = "baseline"

    def choose(self, built: BuiltDecisionContext) -> int:
        choice: ActionChoice = self.agent.choose_action(built.context)
        return int(choice.action_code)


@dataclass
class RandomPolicy:
    """Uniform random over the legal action codes of the current context."""

    seed: int | None = None
    name: str = "random"
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def choose(self, built: BuiltDecisionContext) -> int:
        codes = list(built.context.legal_low_level_codes)
        if not codes:
            raise RuntimeError("no legal actions available")
        return int(self._rng.choice(codes))


@dataclass
class CallablePolicy:
    """Wrap a plain ``fn(built) -> int`` (used for LLM-assisted agents, tests)."""

    fn: Callable[[BuiltDecisionContext], int]
    name: str = "callable"

    def choose(self, built: BuiltDecisionContext) -> int:
        return int(self.fn(built))


def heuristic_policy() -> BaselinePolicy:
    return BaselinePolicy(HeuristicAgent(), name="heuristic")


def legal_random_policy(seed: int | None = None) -> BaselinePolicy:
    return BaselinePolicy(LegalRandomAgent(seed=seed), name="legal_random")


@lru_cache(maxsize=1)
def _asset_catalog() -> Any:
    from agents.scripted.assets import load_assets

    return load_assets()


@lru_cache(maxsize=1)
def _profiles_by_slug() -> dict[str, Any]:
    from agents.scripted.profiles import load_deck_profiles_by_slug

    return load_deck_profiles_by_slug()


def expert_profile(slug: str) -> Any:
    profiles = _profiles_by_slug()
    if slug not in profiles:
        raise KeyError(f"unknown expert deck slug: {slug!r}")
    return profiles[slug]


def expert_policy(slug: str, *, seed: int | None = None) -> ScriptedPolicy:
    """Build a scripted expert policy for one of the ported deck rules."""
    from agents.scripted.agent import ExpertRuleAgent

    profile = expert_profile(slug)
    agent = ExpertRuleAgent(assets=_asset_catalog(), profile=profile, seed=seed)
    return ScriptedPolicy(agent, name=f"expert:{slug}")


def _neural_policy(spec: str, *, seed: int) -> Policy:
    """Neural checkpoints are not part of this repository (deliberate).

    The upstream project (`genshin-GITCG`) trains CVPN policies; this repository is
    the course project *Skill self-evolution for LLM agents* and keeps the runtime
    **torch-free** on purpose: every agent here is either scripted, heuristic, or an
    LLM. Keeping the branch explicit (instead of silently falling back) makes an
    accidental dependency on a trained checkpoint loud rather than subtle.
    """
    raise KeyError(
        "neural:<checkpoint> policies are not available in gitcg-skills "
        f"(requested {spec!r}); use expert:<slug> / heuristic / legal_random / llm:<name>"
    )


def build_policy(spec: str, *, seed: int, role: str) -> Policy:
    """Build a policy from a serializable spec string (used by multiprocess rollout).

    ``seed``/``role`` are folded into the policy RNG seed so the same task always
    reproduces the same agents (M0 replay consistency).

    Specs: ``expert:<slug>`` | ``neural:<checkpoint>[#t=<temp>]`` | ``heuristic``
    | ``legal_random`` | ``random``. The ``neural`` spec loads the CVPN from a
    checkpoint (with the encoder config saved beside it) so a trained checkpoint
    can run through rollout / arena / collectors like any scripted opponent.
    """
    from common.seeding import derive_seed

    spec = spec.strip()
    if spec.startswith("expert:"):
        slug = spec.split(":", 1)[1]
        return expert_policy(slug, seed=derive_seed(seed, role, "expert", slug))
    if spec.startswith("neural:"):
        return _neural_policy(spec.split(":", 1)[1], seed=derive_seed(seed, role, "neural"))
    if spec == "heuristic":
        return heuristic_policy()
    if spec == "legal_random":
        return BaselinePolicy(
            LegalRandomAgent(seed=derive_seed(seed, role, "legal_random")),
            name="legal_random",
        )
    if spec == "random":
        return RandomPolicy(seed=derive_seed(seed, role, "random"))
    raise KeyError(f"unknown policy spec: {spec!r}")

