# Ported from Rebel_base_RL research/world_model (AGPL-3.0) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
"""Public-belief rooting for fork search (PLAN.md M3-CPU S-A, gaps G1+G3).

The practical resolver in ``envs.fork_search`` forks the *live* boundary
snapshot, whose hidden state (opponent hand/pile/dice) is the single true
determinization. That violates the GT-CFR charter invariant §5.2 ("no hidden
truth in the online root"). This module replaces the root with **public belief +
sampled determinization**:

1. mask the boundary snapshot to the acting player's ``perspective`` view
   (``reps.public_state.mask_state_for_player``) -- the same information the
   encoder is allowed to see;
2. build a ``PublicBeliefState`` whose opponent range is sampled only from
   public information (known decklist minus revealed cards, public hand/dice
   counts, optional learned belief histograms/group marginals);
3. reconstruct a *full, engine-loadable* state JSON by patching a structural
   scaffold's hidden zones (hands/pile/dice) with a sampled particle, assigning
   **synthetic entity ids** to hidden zones so no ordering/identity of the true
   hidden cards survives.

The reconstruction works on the engine's ``store``/``$`` JSON (the same format
``reps.snapshot`` decodes and ``envs.snapshot.fork_game`` loads), so a sampled
root can be handed straight to the fork bridge. Determinism comes from a seed
derived only from public fields + the particle, so two live games that share a
public state but differ in hidden truth produce identical roots (the S-A
acceptance: the search no longer reads hidden truth).
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter
from typing import Any, Sequence

from .belief_groups import groups_for_card
from .public_state import (
    ExplicitRange,
    ExplicitRangeHypothesis,
    PublicBeliefState,
    PublicStateTracker,
    mask_state_for_player,
)
from .schema import StateSnapshot
from .snapshot import snapshot_state

_DICE_TYPES = (1, 2, 3, 4, 5, 6, 7, 8)


# --------------------------------------------------------------------------- #
# small helpers (ported)
# --------------------------------------------------------------------------- #
def _json_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_copy(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_copy(item) for item in value]
    return value


def _raw_definition_id(*, store: Sequence[Any], definition_ref: Any) -> int:
    if not isinstance(definition_ref, dict) or "$" not in definition_ref:
        return 0
    try:
        definition_node = store[int(definition_ref["$"])]
    except Exception:
        return 0
    if not isinstance(definition_node, dict):
        return 0
    return int(definition_node.get("id", 0) or 0)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _stable_public_seed(*, prefix: str = "belief", **payload: Any) -> int:
    digest = hashlib.blake2b(
        json.dumps(
            {"prefix": str(prefix), **{key: _jsonable(value) for key, value in payload.items()}},
            ensure_ascii=True,
            sort_keys=True,
            default=str,
        ).encode("utf-8"),
        digest_size=8,
    ).digest()
    return max(1, int.from_bytes(digest, byteorder="big", signed=False) & 0x7FFFFFFF)


def _sample_hidden_dice(*, sample_index: int, count: int) -> tuple[int, ...]:
    """I.i.d. dice sample for one public-belief particle.

    The historical implementation was a deterministic rotation
    ``(sample_index + offset) % 8``, which forces every particle to contain eight
    *distinct* dice. Real dice pools are drawn with replacement, so the rotation
    systematically misrepresented the opponent's legal actions (a hidden dice
    type is often duplicated). Seed from the particle index only: the sample must
    stay a function of public fields, never of the true hidden state.
    """
    if count <= 0:
        return ()
    rng = random.Random(_stable_public_seed(prefix="hidden_dice", sample_index=int(sample_index)))
    return tuple(int(rng.choice(_DICE_TYPES)) for _ in range(int(count)))


def _belief_score_lookup(*, vocabulary: Sequence[int], scores: Sequence[float]) -> dict[int, float]:
    lookup = {int(card_id): 0.0 for card_id in vocabulary}
    for card_id, score in zip(vocabulary, scores, strict=False):
        lookup[int(card_id)] = float(score)
    return lookup


def _group_probability_lookup(*, probabilities: Sequence[float]) -> dict[str, float]:
    from .belief_groups import belief_groups

    groups = belief_groups()
    lookup: dict[str, float] = {}
    for group_name, probability in zip(groups, probabilities, strict=False):
        lookup[str(group_name)] = max(1.0e-4, min(1.0 - 1.0e-4, float(probability)))
    return lookup


def _card_group_log_bonus(*, card_id: int, probabilities: dict[str, float]) -> float:
    groups = groups_for_card(int(card_id))
    if not groups or not probabilities:
        return 0.0
    return float(sum(math.log(max(1.0e-4, probabilities.get(str(group), 0.5))) for group in groups))


def _group_presence_likelihood(*, cards: Sequence[int], probabilities: dict[str, float]) -> float:
    if not probabilities:
        return 1.0
    present_groups = {
        str(group) for card_id in cards for group in groups_for_card(int(card_id))
    }
    log_weight = 0.0
    for group_name, probability in probabilities.items():
        if str(group_name) in present_groups:
            log_weight += math.log(max(1.0e-4, float(probability)))
        else:
            log_weight += math.log(max(1.0e-4, 1.0 - float(probability)))
    return float(math.exp(max(-20.0, min(5.0, log_weight))))


def _sample_weighted_hand_particle(
    *,
    remaining_cards: Counter[int],
    hand_count: int,
    belief_scores: dict[int, float],
    deck_scores: dict[int, float],
    hand_group_probs: dict[str, float],
    rng: random.Random,
) -> tuple[tuple[int, ...], float]:
    bag = [
        int(card_id)
        for card_id, count in remaining_cards.items()
        for _ in range(max(0, int(count)))
    ]
    if not bag or hand_count <= 0:
        return (), 1.0
    chosen: list[int] = []
    weight = 1.0
    local_bag = list(bag)
    for _ in range(min(int(hand_count), len(local_bag))):
        scores = [
            math.exp(
                max(
                    -12.0,
                    min(
                        12.0,
                        float(
                            belief_scores.get(card_id, 0.0)
                            + (0.35 * deck_scores.get(card_id, 0.0))
                            + _card_group_log_bonus(card_id=card_id, probabilities=hand_group_probs)
                        ),
                    ),
                )
            )
            for card_id in local_bag
        ]
        total = sum(scores)
        if total <= 1.0e-8:
            selected_index = int(rng.randrange(len(local_bag)))
            probability = 1.0 / float(len(local_bag))
        else:
            threshold = float(rng.random()) * float(total)
            running = 0.0
            selected_index = len(local_bag) - 1
            probability = 1.0 / float(max(1, len(local_bag)))
            for index, score in enumerate(scores):
                running += float(score)
                if threshold <= running:
                    selected_index = index
                    probability = float(score) / float(total)
                    break
        chosen_card = int(local_bag.pop(selected_index))
        chosen.append(chosen_card)
        weight *= max(1.0e-8, float(probability))
    return tuple(sorted(chosen)), max(1.0e-8, float(weight))


# --------------------------------------------------------------------------- #
# belief-state construction (root ranges)
# --------------------------------------------------------------------------- #
def _build_self_range_from_state(state: StateSnapshot, *, player_id: int) -> ExplicitRange:
    player = state.players[player_id]
    hand_definition_ids = tuple(int(card.definition_id) for card in player.hand_cards)
    visible_pile_definition_ids = tuple(
        int(card.definition_id) for card in player.pile_cards if int(card.definition_id) > 0
    )
    return ExplicitRange(
        hypotheses=(
            ExplicitRangeHypothesis(
                hand_definition_ids=hand_definition_ids,
                deck_definition_ids=visible_pile_definition_ids,
                hidden_dice=tuple(int(die) for die in player.dice),
                weight=1.0,
            ),
        )
    )


def _build_opponent_range(
    *,
    state: StateSnapshot,
    root_player: int,
    opponent_deck_cards: Sequence[int],
    tracker: PublicStateTracker | None,
    belief_samples: int,
    opponent_dice_count: int | None = None,
    belief_histogram: Sequence[float] = (),
    belief_remaining_deck_histogram: Sequence[float] = (),
    belief_group_hand_probs: Sequence[float] = (),
    belief_group_deck_probs: Sequence[float] = (),
) -> ExplicitRange:
    opponent_player = 1 - int(root_player)
    opponent = state.players[opponent_player]
    opponent_hand_count = (
        int(tracker.opponent_public_hand_count_from_state(state))
        if tracker is not None
        else int(len(opponent.hand_cards))
    )
    opponent_dice_count = (
        int(opponent_dice_count)
        if opponent_dice_count is not None
        else (
            int(round(tracker.opponent_public_dice_count_from_state(state)))
            if tracker is not None
            else int(len(opponent.dice))
        )
    )
    revealed_counts = Counter(
        tracker.revealed_opponent_card_definition_ids() if tracker is not None else ()
    )
    deck_cards = tuple(int(card_id) for card_id in opponent_deck_cards)
    remaining_cards = Counter(deck_cards)
    for definition_id, count in revealed_counts.items():
        if definition_id in remaining_cards:
            remaining_cards[definition_id] = max(0, remaining_cards[definition_id] - int(count))
    vocabulary = deck_cards
    belief_scores = _belief_score_lookup(vocabulary=vocabulary, scores=belief_histogram)
    deck_scores = _belief_score_lookup(vocabulary=vocabulary, scores=belief_remaining_deck_histogram)
    hand_group_lookup = _group_probability_lookup(probabilities=belief_group_hand_probs)
    deck_group_lookup = _group_probability_lookup(probabilities=belief_group_deck_probs)
    sample_count = max(1, int(belief_samples))
    public_seed = _stable_public_seed(
        prefix="belief_range",
        phase=str(state.phase),
        round_number=int(state.round_number),
        acting_player=int(state.current_turn),
        iterator_id=int(state.iterator_id),
        hand_count=int(opponent_hand_count),
        dice_count=int(opponent_dice_count),
        revealed=tuple(sorted(int(value) for value in revealed_counts.elements())),
    )
    hypotheses: list[ExplicitRangeHypothesis] = []
    seen: set[tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]] = set()
    for sample_index in range(sample_count * 3):
        rng = random.Random(public_seed + (sample_index * 7919))
        hand_cards, unnormalized_weight = _sample_weighted_hand_particle(
            remaining_cards=remaining_cards,
            hand_count=opponent_hand_count,
            belief_scores=belief_scores,
            deck_scores=deck_scores,
            hand_group_probs=hand_group_lookup,
            rng=rng,
        )
        deck_after_hand = Counter(remaining_cards)
        for definition_id, count in Counter(hand_cards).items():
            if definition_id in deck_after_hand:
                deck_after_hand[definition_id] = max(0, deck_after_hand[definition_id] - int(count))
        deck_cards_after = tuple(
            card_id
            for card_id, count in sorted(deck_after_hand.items())
            for _ in range(max(0, int(count)))
        )
        hidden_dice = _sample_hidden_dice(sample_index=sample_index, count=opponent_dice_count)
        particle_key = (tuple(sorted(hand_cards)), deck_cards_after, hidden_dice)
        if particle_key in seen:
            continue
        seen.add(particle_key)
        group_weight = _group_presence_likelihood(
            cards=hand_cards, probabilities=hand_group_lookup
        ) * _group_presence_likelihood(cards=deck_cards_after, probabilities=deck_group_lookup)
        hypotheses.append(
            ExplicitRangeHypothesis(
                hand_definition_ids=tuple(hand_cards),
                deck_definition_ids=deck_cards_after,
                hidden_dice=hidden_dice,
                weight=float(unnormalized_weight * group_weight),
            )
        )
        if len(hypotheses) >= sample_count:
            break
    if not hypotheses:
        fallback_hand = tuple(
            card_id
            for card_id, count in sorted(remaining_cards.items())
            for _ in range(max(0, int(count)))
        )[:opponent_hand_count]
        fallback_deck = Counter(remaining_cards)
        for definition_id, count in Counter(fallback_hand).items():
            if definition_id in fallback_deck:
                fallback_deck[definition_id] = max(0, fallback_deck[definition_id] - int(count))
        hypotheses.append(
            ExplicitRangeHypothesis(
                hand_definition_ids=tuple(fallback_hand),
                deck_definition_ids=tuple(
                    card_id
                    for card_id, count in sorted(fallback_deck.items())
                    for _ in range(max(0, int(count)))
                ),
                hidden_dice=_sample_hidden_dice(sample_index=0, count=opponent_dice_count),
                weight=1.0,
            )
        )
    return ExplicitRange(hypotheses=tuple(hypotheses))


def build_public_belief_state(
    *,
    public_state: StateSnapshot,
    root_player: int,
    opponent_deck_cards: Sequence[int],
    tracker: PublicStateTracker | None = None,
    belief_samples: int = 4,
    opponent_dice_count: int | None = None,
    belief_histogram: Sequence[float] = (),
    belief_remaining_deck_histogram: Sequence[float] = (),
    belief_group_hand_probs: Sequence[float] = (),
    belief_group_deck_probs: Sequence[float] = (),
) -> PublicBeliefState:
    """Build a ``PublicBeliefState`` from a player-masked state.

    ``public_state`` must already be masked to ``root_player`` (e.g. via
    ``mask_state_for_player``); the opponent range is then sampled from public
    information only, so the result never depends on hidden truth.
    """
    root_player = int(root_player)
    opponent_player = 1 - root_player
    self_range = _build_self_range_from_state(public_state, player_id=root_player)
    opponent_range = _build_opponent_range(
        state=public_state,
        root_player=root_player,
        opponent_deck_cards=opponent_deck_cards,
        tracker=tracker,
        belief_samples=belief_samples,
        opponent_dice_count=opponent_dice_count,
        belief_histogram=belief_histogram,
        belief_remaining_deck_histogram=belief_remaining_deck_histogram,
        belief_group_hand_probs=belief_group_hand_probs,
        belief_group_deck_probs=belief_group_deck_probs,
    )
    opponent = public_state.players[opponent_player]
    opponent_public_dice_count = (
        float(int(opponent_dice_count))
        if opponent_dice_count is not None
        else (
            tracker.opponent_public_dice_count_from_state(public_state)
            if tracker is not None
            else float(len(opponent.dice))
        )
    )
    opponent_public_hand_count = (
        tracker.opponent_public_hand_count_from_state(public_state)
        if tracker is not None
        else float(len(opponent.hand_cards))
    )
    revealed = tracker.revealed_opponent_card_definition_ids() if tracker is not None else ()
    return PublicBeliefState(
        root_player=root_player,
        acting_player=root_player,
        public_state=public_state,
        round_number=int(public_state.round_number),
        phase=str(public_state.phase),
        opponent_public_dice_count=float(opponent_public_dice_count),
        opponent_public_hand_count=float(opponent_public_hand_count),
        self_range=self_range,
        opponent_range=opponent_range,
        public_history=tracker.public_history() if tracker is not None else (),
        revealed_opponent_card_definition_ids=tuple(int(v) for v in revealed),
    )


# --------------------------------------------------------------------------- #
# state reconstruction (public belief + particle -> engine state JSON)
# --------------------------------------------------------------------------- #
class _StatePatcher:
    """Patches hidden zones of a scaffold state JSON in place (store/ref format)."""

    def __init__(self, store: list[Any], *, synthetic_hidden_ids: bool) -> None:
        self.store = store
        self.synthetic_hidden_ids = bool(synthetic_hidden_ids)
        self._definition_cache: dict[tuple[str, int], int] = {}
        self._next_synthetic_id = -900000

    def synthetic_id(self) -> int:
        value = self._next_synthetic_id
        self._next_synthetic_id -= 1
        return value

    def ref_list(self, owner: dict[str, Any], field_name: str) -> list[Any]:
        field_ref = owner.get(field_name)
        if not isinstance(field_ref, dict) or "$" not in field_ref:
            raise ValueError(f"scaffold node is missing '{field_name}'")
        value = self.store[int(field_ref["$"])]
        if not isinstance(value, list):
            raise ValueError(f"scaffold field '{field_name}' is not a list")
        return value

    def resize(self, refs: list[Any], target_count: int) -> None:
        while len(refs) > int(target_count):
            refs.pop()
        while len(refs) < int(target_count):
            self.store.append({})
            refs.append({"$": len(self.store) - 1})

    def node(self, ref: Any) -> dict[str, Any]:
        if not isinstance(ref, dict) or "$" not in ref:
            raise ValueError("invalid store ref")
        index = int(ref["$"])
        while index >= len(self.store):
            self.store.append({})
        node = self.store[index]
        if not isinstance(node, dict):
            node = {}
            self.store[index] = node
        return node

    def definition_ref(self, kind: str, definition_id: int) -> dict[str, int]:
        key = (str(kind), int(definition_id))
        index = self._definition_cache.get(key)
        if index is None:
            store_index = None
            for i, candidate in enumerate(self.store):
                if (
                    isinstance(candidate, dict)
                    and candidate.get("$$") == str(kind)
                    and int(candidate.get("id", 0)) == int(definition_id)
                ):
                    store_index = i
                    break
            if store_index is None:
                self.store.append({"$$": str(kind), "id": int(definition_id)})
                store_index = len(self.store) - 1
            index = store_index
            self._definition_cache[key] = index
        return {"$": index}

    def zone_definition_ids(self, owner: dict[str, Any], field_name: str) -> tuple[int, ...]:
        zone_ref = owner.get(field_name)
        if not isinstance(zone_ref, dict) or "$" not in zone_ref:
            return ()
        try:
            zone_items = self.store[int(zone_ref["$"])]
        except Exception:
            return ()
        if not isinstance(zone_items, list):
            return ()
        definition_ids: list[int] = []
        for item in zone_items:
            if isinstance(item, dict) and "$" in item:
                try:
                    raw_card = self.store[int(item["$"])]
                except Exception:
                    continue
                if isinstance(raw_card, dict):
                    definition_ids.append(
                        _raw_definition_id(store=self.store, definition_ref=raw_card.get("definition"))
                    )
            else:
                definition_ids.append(
                    _raw_definition_id(store=self.store, definition_ref=item)
                )
        return tuple(int(value) for value in definition_ids if int(value) > 0)

    def align_definition_ids(
        self,
        owner: dict[str, Any],
        field_name: str,
        public_cards: Sequence[Any],
        definition_ids: Sequence[int],
    ) -> tuple[int, ...]:
        target_count = len(public_cards) if public_cards else len(definition_ids)
        desired = [int(value) for value in definition_ids]
        if target_count <= 0:
            return ()
        if len(desired) >= target_count:
            return tuple(desired[:target_count])
        template_defs = list(self.zone_definition_ids(owner, field_name))
        filler = [value for value in template_defs if int(value) > 0] or desired
        if not filler:
            filler = [1]
        while len(desired) < target_count:
            desired.append(int(filler[(len(desired) - len(definition_ids)) % len(filler)]))
        return tuple(desired[:target_count])

    def patch_card_zone(
        self,
        owner: dict[str, Any],
        field_name: str,
        public_cards: Sequence[Any],
        definition_ids: Sequence[int],
        *,
        hidden_zone: bool,
    ) -> None:
        refs = self.ref_list(owner, field_name)
        target_defs = self.align_definition_ids(owner, field_name, public_cards, definition_ids)
        self.resize(refs, len(target_defs))
        for index, definition_id in enumerate(target_defs):
            card_node = self.node(refs[index])
            public_card = public_cards[index] if index < len(public_cards) else None
            visible_match = (
                public_card is not None
                and not hidden_zone
                and int(public_card.definition_id) == int(definition_id)
                and int(definition_id) > 0
            )
            if visible_match:
                card_node["id"] = int(public_card.id)
            else:
                card_node["id"] = self.synthetic_id()
            card_node["definition"] = self.definition_ref("entities", int(definition_id))
            card_node["variables"] = {}
            card_node["attachments"] = []

    def patch_primitive_zone(
        self, owner: dict[str, Any], field_name: str, values: Sequence[int]
    ) -> None:
        field_ref = owner.get(field_name)
        # gitcg 0.21.0 keeps ``dice`` inline (a plain list) on the player node;
        # other engine builds store it behind a store ref. Support both.
        if isinstance(field_ref, dict) and "$" in field_ref:
            self.store[int(field_ref["$"])] = [int(value) for value in values]
        else:
            owner[field_name] = [int(value) for value in values]

    def patch_player(
        self,
        player_node: dict[str, Any],
        *,
        hand_definition_ids: Sequence[int],
        deck_definition_ids: Sequence[int],
        hidden_dice: Sequence[int],
        public_player,
        hidden_zone: bool,
    ) -> None:
        self.patch_card_zone(
            player_node,
            "hands",
            public_player.hand_cards,
            hand_definition_ids,
            hidden_zone=hidden_zone,
        )
        self.patch_card_zone(
            player_node,
            "pile",
            public_player.pile_cards,
            deck_definition_ids,
            hidden_zone=hidden_zone,
        )
        self.patch_primitive_zone(player_node, "dice", hidden_dice)


def _sample_branch_random(
    *,
    public_belief: PublicBeliefState,
    self_hypothesis: ExplicitRangeHypothesis,
    opponent_hypothesis: ExplicitRangeHypothesis,
    reconstruction_counter: int,
) -> int:
    return _stable_public_seed(
        prefix="branch_random",
        phase=str(public_belief.phase),
        round_number=int(public_belief.round_number),
        iterator_id=int(public_belief.public_state.iterator_id),
        self_hand=tuple(sorted(int(v) for v in self_hypothesis.hand_definition_ids)),
        self_dice=tuple(int(v) for v in self_hypothesis.hidden_dice),
        opp_hand=tuple(sorted(int(v) for v in opponent_hypothesis.hand_definition_ids)),
        opp_deck=tuple(sorted(int(v) for v in opponent_hypothesis.deck_definition_ids)),
        opp_dice=tuple(int(v) for v in opponent_hypothesis.hidden_dice),
        counter=int(reconstruction_counter),
    )


def reconstruct_state_json(
    scaffold_state_json: str,
    *,
    public_belief: PublicBeliefState,
    self_hypothesis: ExplicitRangeHypothesis,
    opponent_hypothesis: ExplicitRangeHypothesis,
    branch_random: int,
    synthetic_hidden_ids: bool = True,
) -> str:
    """Patch ``scaffold_state_json``'s hidden zones with a sampled particle.

    The returned JSON is an engine-loadable full state whose hidden zones
    (opponent hand/pile/dice, and the self pile ordering) come from the particle
    and the public state, never from the scaffold's hidden truth. Public fields
    (characters, supports, summons, public counters/logs, decklists) are kept
    from the scaffold.
    """
    payload = json.loads(scaffold_state_json)
    store = payload["store"]
    root = store[-1]
    root_player = int(public_belief.root_player)
    opponent_player = 1 - root_player
    public_state = public_belief.public_state

    players_ref = root.get("players")
    if not isinstance(players_ref, dict) or "$" not in players_ref:
        raise ValueError("scaffold root is missing players")
    players = store[int(players_ref["$"])]
    if not isinstance(players, list) or len(players) < 2:
        raise ValueError("scaffold root players are invalid")

    patcher = _StatePatcher(store, synthetic_hidden_ids=synthetic_hidden_ids)
    patcher.patch_player(
        patcher.node(players[root_player]),
        hand_definition_ids=self_hypothesis.hand_definition_ids,
        deck_definition_ids=self_hypothesis.deck_definition_ids,
        hidden_dice=self_hypothesis.hidden_dice,
        public_player=public_state.players[root_player],
        hidden_zone=False,
    )
    patcher.patch_player(
        patcher.node(players[opponent_player]),
        hand_definition_ids=opponent_hypothesis.hand_definition_ids,
        deck_definition_ids=opponent_hypothesis.deck_definition_ids,
        hidden_dice=opponent_hypothesis.hidden_dice,
        public_player=public_state.players[opponent_player],
        hidden_zone=True,
    )

    iterators_ref = root.get("iterators")
    if isinstance(iterators_ref, dict) and "$" in iterators_ref:
        iterators = store[int(iterators_ref["$"])]
        if isinstance(iterators, dict):
            iterators["random"] = int(branch_random)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def sample_belief_roots(
    scaffold_state_json: str,
    *,
    root_player: int,
    opponent_deck_cards: Sequence[int],
    samples: int = 1,
    tracker: PublicStateTracker | None = None,
    synthetic_hidden_ids: bool = True,
) -> list[str]:
    """Return ``samples`` determinized, engine-loadable root states.

    Determinization is a pure function of the *masked* boundary state and the
    opponent decklist/particle scheme, so results are identical for two live
    games that differ only in hidden truth.
    """
    state = snapshot_state(None, scaffold_state_json)
    public_state = mask_state_for_player(state, perspective_player=int(root_player))
    opponent_dice_count = len(state.players[1 - int(root_player)].dice)
    belief = build_public_belief_state(
        public_state=public_state,
        root_player=int(root_player),
        opponent_deck_cards=opponent_deck_cards,
        tracker=tracker,
        belief_samples=max(1, int(samples)),
        opponent_dice_count=opponent_dice_count,
    )
    self_hypothesis = belief.self_range.hypotheses[0]
    opponent_hypotheses = belief.opponent_range.hypotheses or (
        ExplicitRangeHypothesis(hand_definition_ids=(), deck_definition_ids=(), hidden_dice=()),
    )
    roots: list[str] = []
    for index in range(max(1, int(samples))):
        opponent_hypothesis = opponent_hypotheses[index % len(opponent_hypotheses)]
        branch_random = _sample_branch_random(
            public_belief=belief,
            self_hypothesis=self_hypothesis,
            opponent_hypothesis=opponent_hypothesis,
            reconstruction_counter=index,
        )
        roots.append(
            reconstruct_state_json(
                scaffold_state_json,
                public_belief=belief,
                self_hypothesis=self_hypothesis,
                opponent_hypothesis=opponent_hypothesis,
                branch_random=branch_random,
                synthetic_hidden_ids=synthetic_hidden_ids,
            )
        )
    return roots


def belief_root_summary(
    scaffold_state_json: str,
    *,
    root_player: int,
    opponent_deck_cards: Sequence[int],
    samples: int = 4,
) -> dict[str, Any]:
    """Lightweight diagnostics for reports/tests (hypothesis weights + dims)."""
    state = snapshot_state(None, scaffold_state_json)
    public_state = mask_state_for_player(state, perspective_player=int(root_player))
    belief = build_public_belief_state(
        public_state=public_state,
        root_player=int(root_player),
        opponent_deck_cards=opponent_deck_cards,
        belief_samples=max(1, int(samples)),
        opponent_dice_count=len(state.players[1 - int(root_player)].dice),
    )
    return {
        "root_player": int(root_player),
        "phase": str(belief.phase),
        "round_number": int(belief.round_number),
        "opponent_hand_count": float(belief.opponent_public_hand_count),
        "opponent_dice_count": float(belief.opponent_public_dice_count),
        "n_opponent_hypotheses": int(belief.opponent_range.count),
        "opponent_hypothesis_weights": [
            float(hyp.weight) for hyp in belief.opponent_range.hypotheses
        ],
        "n_self_hypotheses": int(belief.self_range.count),
    }
