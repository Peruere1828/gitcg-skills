# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

from .assets import AssetCatalog, load_assets
from .codec import decode_share_code
from .deck_rules import RULE_CONFIGS
from .models import DeckProfile
from .registry import RAW_DECKS


def load_deck_profiles(*, refresh_assets: bool = False) -> tuple[DeckProfile, ...]:
    assets = load_assets(refresh=refresh_assets)
    return tuple(_build_profile(entry.slug, assets=assets) for entry in RAW_DECKS)


def load_deck_profiles_by_slug(*, refresh_assets: bool = False) -> dict[str, DeckProfile]:
    profiles = load_deck_profiles(refresh_assets=refresh_assets)
    return {profile.slug: profile for profile in profiles}


def _build_profile(slug: str, *, assets: AssetCatalog) -> DeckProfile:
    raw_entry = next(entry for entry in RAW_DECKS if entry.slug == slug)
    characters, cards = decode_share_code(raw_entry.share_code, assets.share_to_id)
    character_names = tuple(assets.character(character_id).name for character_id in characters)
    character_elements = tuple(assets.element_for_character(character_id) for character_id in characters)
    support_cards: list[int] = []
    equipment_cards: list[int] = []
    event_cards: list[int] = []
    talent_cards: list[int] = []
    for card_id in cards:
        card = assets.card(card_id)
        tag_set = set(card.tags)
        if "GCG_TAG_TALENT" in tag_set:
            talent_cards.append(card_id)
        if {"GCG_TAG_WEAPON", "GCG_TAG_ARTIFACT", "GCG_TAG_TALENT"} & tag_set:
            equipment_cards.append(card_id)
        elif {"GCG_TAG_ALLY", "GCG_TAG_PLACE", "GCG_TAG_ITEM"} & tag_set:
            support_cards.append(card_id)
        else:
            event_cards.append(card_id)
    return DeckProfile(
        slug=raw_entry.slug,
        name=raw_entry.name,
        author=raw_entry.author,
        tags=raw_entry.tags,
        share_code=raw_entry.share_code,
        characters=characters,
        cards=cards,
        character_names=character_names,
        character_elements=character_elements,
        rule_config=RULE_CONFIGS[raw_entry.slug],
        talent_cards=tuple(talent_cards),
        support_cards=tuple(support_cards),
        equipment_cards=tuple(equipment_cards),
        event_cards=tuple(event_cards),
    )
