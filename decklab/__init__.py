"""WS4 deck-building layer (template flex-card sensitivity, v1)."""

from .sensitivity import candidate_cards, evaluate_swaps, mutate_deck

__all__ = ["candidate_cards", "evaluate_swaps", "mutate_deck"]
