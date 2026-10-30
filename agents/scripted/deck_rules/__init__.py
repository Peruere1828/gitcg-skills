# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

from . import aywolfskirk
from . import beidou_skirk_ayaka
from . import double_geo_navia
from . import dual_mualani_stacks
from . import dvalin_bonk
from . import electro_manifestation_nuke
from . import electrocharged_moon
from . import ice_water_battleship
from . import klee_mavuika_mualani
from . import mualani_double_pyro
from . import nahida_kokomi_raiden
from . import natlan_battleship
from . import shenhe_double_swirl
from . import skirk_ayaka_navia
from . import skirk_chasca_freeze
from . import superconduct_aggro
from . import superconduct_mika_attack
from . import trial_skirk_ayaka_navia
from . import unyielding_geo
from . import yelan_clorinde_ororon

DECK_RULE_MODULES = (
    natlan_battleship,
    klee_mavuika_mualani,
    superconduct_mika_attack,
    dual_mualani_stacks,
    superconduct_aggro,
    mualani_double_pyro,
    yelan_clorinde_ororon,
    electrocharged_moon,
    aywolfskirk,
    dvalin_bonk,
    shenhe_double_swirl,
    electro_manifestation_nuke,
    double_geo_navia,
    unyielding_geo,
    beidou_skirk_ayaka,
    skirk_chasca_freeze,
    skirk_ayaka_navia,
    ice_water_battleship,
    nahida_kokomi_raiden,
    trial_skirk_ayaka_navia,
)

RULE_CONFIGS = {module.RULE_CONFIG.slug: module.RULE_CONFIG for module in DECK_RULE_MODULES}

ACTION_RULES = {
    module.RULE_CONFIG.slug: rule
    for module in DECK_RULE_MODULES
    if callable(rule := getattr(module, "choose_action", None))
}

ACTIVE_RULES = {
    module.RULE_CONFIG.slug: rule
    for module in DECK_RULE_MODULES
    if callable(rule := getattr(module, "choose_active", None))
}

REROLL_RULES = {
    module.RULE_CONFIG.slug: rule
    for module in DECK_RULE_MODULES
    if callable(rule := getattr(module, "choose_reroll", None))
}

SELECT_RULES = {
    module.RULE_CONFIG.slug: rule
    for module in DECK_RULE_MODULES
    if callable(rule := getattr(module, "choose_select_card", None))
}

CARD_BONUS_RULES = {
    module.RULE_CONFIG.slug: rule
    for module in DECK_RULE_MODULES
    if callable(rule := getattr(module, "card_context_bonus", None))
}
