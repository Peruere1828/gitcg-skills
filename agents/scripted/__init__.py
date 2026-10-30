# Scripted (rule-based) opponents ported from Rebel_base_RL `gitcg_expert_system`.
#
# These are the fixed evaluation opponents (PLAN.md WS2). Keep upstream semantics;
# adapt imports only. See NOTICE.md.

from .agent import ExpertRuleAgent
from .assets import AssetCatalog, load_assets
from .models import DeckProfile, RuleChoice
from .profiles import load_deck_profiles, load_deck_profiles_by_slug
from .registry import RAW_DECKS

__all__ = [
    "ExpertRuleAgent",
    "AssetCatalog",
    "load_assets",
    "DeckProfile",
    "RuleChoice",
    "load_deck_profiles",
    "load_deck_profiles_by_slug",
    "RAW_DECKS",
]
