# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RawDeckEntry:
    slug: str
    name: str
    author: str
    tags: tuple[str, ...]
    share_code: str


@dataclass(frozen=True)
class SkillInfo:
    id: int
    character_id: int
    character_name: str
    skill_type: str
    name: str
    play_cost: tuple[tuple[str, int], ...]
    description: str = ""


@dataclass(frozen=True)
class CharacterInfo:
    id: int
    share_id: int | None
    name: str
    tags: tuple[str, ...]
    skill_ids: tuple[int, ...]
    max_health: int
    max_energy: int


@dataclass(frozen=True)
class CardInfo:
    id: int
    share_id: int | None
    name: str
    tags: tuple[str, ...]
    related_character_id: int | None
    play_cost: tuple[tuple[str, int], ...]
    description: str


@dataclass(frozen=True)
class DeckRuleConfig:
    slug: str
    opener: str
    carry: tuple[str, ...]
    bench: tuple[str, ...]
    preferred_elements: tuple[str, ...]
    style: str
    preferred_cards: tuple[str, ...] = ()
    keep_tags: tuple[str, ...] = ("GCG_TAG_TALENT", "GCG_TAG_ALLY", "GCG_TAG_PLACE")
    switch_hp_threshold: int = 4
    setup_rounds: int = 2


@dataclass(frozen=True)
class DeckProfile:
    slug: str
    name: str
    author: str
    tags: tuple[str, ...]
    share_code: str
    characters: tuple[int, ...]
    cards: tuple[int, ...]
    character_names: tuple[str, ...]
    character_elements: tuple[str, ...]
    rule_config: DeckRuleConfig
    talent_cards: tuple[int, ...]
    support_cards: tuple[int, ...]
    equipment_cards: tuple[int, ...]
    event_cards: tuple[int, ...]


@dataclass(frozen=True)
class RuleChoice:
    action_code: int
    rule_id: str
    fallback: bool = False


@dataclass(frozen=True)
class DecisionTrace:
    deck_slug: str
    deck_name: str
    player_index: int
    request_type: str
    rule_id: str
    fallback: bool
    action_code: int
    action_label: str
    round_number: int
    phase: str
    actor_character: str | None = None
    actor_definition_id: int | None = None
    subject_definition_id: int | None = None
    target_definition_ids: tuple[int, ...] = ()
    target_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class MatchResult:
    match_index: int
    deck0: str
    deck1: str
    seed: int
    winner: int | None
    rounds: int
    decisions: int
    traces: tuple[DecisionTrace, ...]
    error: str | None = None
    truncated: bool = False


@dataclass
class RuleStat:
    trigger_count: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    fallback_count: int = 0
    sample_labels: list[str] = field(default_factory=list)

    def record(self, *, outcome: str, fallback: bool, label: str) -> None:
        self.trigger_count += 1
        if outcome == "win":
            self.wins += 1
        elif outcome == "loss":
            self.losses += 1
        else:
            self.draws += 1
        if fallback:
            self.fallback_count += 1
        if label and label not in self.sample_labels and len(self.sample_labels) < 5:
            self.sample_labels.append(label)
