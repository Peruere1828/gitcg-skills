"""PvE NPC enemy port (PLAN.md §4.4, PvE-B1).

The Timaeus three enemies — Cryo Hilichurl Shooter (3102), Ice Shieldwall
Mitachurl (3101) and Blazing Axe Mitachurl (3302) — only exist after the
engine overlay has been built and installed
(``engine-overlay/scripts/build-in-docker.sh`` + ``install-lib.sh``).  When the
stock PyPI ``libgitcg.so`` is loaded, constructing a deck with those character
ids raises, so every test here is skipped.

The behavioural checks drive the engine with tiny purpose-built policies (not
``NpcPolicy``, which is B2): player 0 exercises the *player* side, player 1
uses the *enemy* lineup.
"""

from __future__ import annotations

import pytest

from gitcg import Game
from reps.action_hierarchy import reset_default_hierarchical_action_codebook
from reps.schema import DeckSpec, OptionKind
from reps.snapshot import snapshot_state

# AuraType (packages/typings/src/gen/enums.ts)
AURA_CRYO = 1

# Official GCG enemy card / status ids (see configs/npc/npc_enemies_all.json).
CRYO_HILICHURL_SHOOTER = 3102
ICE_SHIELDWALL_MITACHURL = 3101
BLAZING_AXE_MITACHURL = 3302
HOARFROST_GREATSHIELD = 131011
FLOWFIRE_EDGE = 133021

SHOOTER_HP = 3
MITACHURL_HP = 8


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _base_spec() -> DeckSpec:
    from envs.decks import expert_deck_specs

    base = expert_deck_specs()[0]
    return DeckSpec(name=base.name, characters=base.characters, cards=base.cards)


def _enemy_spec(order: tuple[int, ...]) -> DeckSpec:
    return DeckSpec(name="npc_timaeus", characters=order, cards=_base_spec().cards)


def _build_game(p0: DeckSpec, p1: DeckSpec, seed: int = 3, p0_fn=None, p1_fn=None) -> Game:
    from envs.match import PolicyPlayer, _build_create_param
    from envs.policy import CallablePolicy

    p0_fn = p0_fn or _end_first
    p1_fn = p1_fn or _skill_first
    reset_default_hierarchical_action_codebook()
    game = Game(create_param=_build_create_param(p0, p1, seed=seed))
    game.set_player(0, PolicyPlayer(0, CallablePolicy(p0_fn, name="p0")))
    game.set_player(1, PolicyPlayer(1, CallablePolicy(p1_fn, name="p1")))
    return game


def _overlay_available() -> bool:
    from envs.match import _build_create_param

    try:
        # `Game(...)` resolves every deck id against the engine data; the stock
        # wheel raises here for the NPC-only character ids.
        Game(
            create_param=_build_create_param(
                _base_spec(),
                _enemy_spec(
                    (CRYO_HILICHURL_SHOOTER, ICE_SHIELDWALL_MITACHURL, BLAZING_AXE_MITACHURL)
                ),
                seed=3,
            )
        )
        return True
    except Exception:  # noqa: BLE001 - stock .so aborts the data lookup
        return False


AVAILABLE = _overlay_available()
pytestmark = pytest.mark.skipif(
    not AVAILABLE, reason="engine overlay (PvE-B1 NPC enemies) not installed"
)


def _pick(built, prefer: OptionKind) -> int:
    codes = list(built.context.legal_low_level_codes)
    specs = built.context.legal_low_level_specs
    for code, spec in zip(codes, specs):
        if spec.kind == prefer:
            return int(code)
    for code, spec in zip(codes, specs):
        if spec.kind == OptionKind.ACTION_DECLARE_END:
            return int(code)
    return int(codes[0])


def _skill_first(built) -> int:
    return _pick(built, OptionKind.ACTION_USE_SKILL)


def _end_first(built) -> int:
    return _pick(built, OptionKind.ACTION_DECLARE_END)


def _chars(snap, who: int) -> dict[int, object]:
    return {c.definition_id: c for c in snap.players[who].characters}


def _active_def(snap, who: int) -> int | None:
    player = snap.players[who]
    for char in player.characters:
        if char.id == player.active_character_id:
            return char.definition_id
    return None


def _status_usage(snap, who: int, def_id: int, status_id: int) -> int | None:
    char = _chars(snap, who).get(def_id)
    if char is None:
        return None
    for entity in char.entities:
        if entity.definition_id == status_id:
            return int(entity.variables.get("usage", 0))
    return None


def _play(p0: DeckSpec, p1: DeckSpec, seed: int, max_steps: int = 60, p0_fn=None, p1_fn=None):
    """Start a game and return (game, list-of-snapshots after each step)."""
    game = _build_game(p0, p1, seed=seed, p0_fn=p0_fn, p1_fn=p1_fn)
    game.start()
    snaps = []
    for _ in range(max_steps):
        if not game.is_running():
            break
        game.step()
        snaps.append(snapshot_state(None, game.state().json()))
    return game, snaps


# --------------------------------------------------------------------------- #
# fast: the cards resolve in the engine data
# --------------------------------------------------------------------------- #
def test_timaeus_characters_present_with_authoritative_stats():
    game, _ = _play(
        _base_spec(),
        _enemy_spec((CRYO_HILICHURL_SHOOTER, ICE_SHIELDWALL_MITACHURL, BLAZING_AXE_MITACHURL)),
        seed=3,
        max_steps=1,
    )
    chars = _chars(snapshot_state(None, game.state().json()), 1)
    assert set(chars) == {
        CRYO_HILICHURL_SHOOTER,
        ICE_SHIELDWALL_MITACHURL,
        BLAZING_AXE_MITACHURL,
    }
    assert chars[CRYO_HILICHURL_SHOOTER].max_health == SHOOTER_HP
    assert chars[ICE_SHIELDWALL_MITACHURL].max_health == MITACHURL_HP
    assert chars[BLAZING_AXE_MITACHURL].max_health == MITACHURL_HP
    for char in chars.values():
        assert char.max_energy == 2


@pytest.mark.slow
def test_timaeus_battle_begin_passive_statuses():
    """Hoarfrost Greatshield / Flowfire Edge are attached when the battle begins."""
    _, snaps = _play(
        _base_spec(),
        _enemy_spec((CRYO_HILICHURL_SHOOTER, ICE_SHIELDWALL_MITACHURL, BLAZING_AXE_MITACHURL)),
        seed=3,
    )
    action_snap = next(s for s in snaps if s.phase == "action")
    assert _status_usage(action_snap, 1, ICE_SHIELDWALL_MITACHURL, HOARFROST_GREATSHIELD) == 2
    assert _status_usage(action_snap, 1, BLAZING_AXE_MITACHURL, FLOWFIRE_EDGE) == 3
    # The shield also leaves the character Cryo-applied.
    assert _chars(action_snap, 1)[ICE_SHIELDWALL_MITACHURL].aura == AURA_CRYO


@pytest.mark.slow
def test_hide_switches_after_shooter_acts():
    """Cryo Hilichurl Shooter's Hide auto-switches to the next character."""
    _, snaps = _play(
        _base_spec(),
        _enemy_spec((CRYO_HILICHURL_SHOOTER, ICE_SHIELDWALL_MITACHURL, BLAZING_AXE_MITACHURL)),
        seed=3,
    )
    actives = [(_active_def(s, 1), _chars(s, 1).get(CRYO_HILICHURL_SHOOTER)) for s in snaps]
    live = [(act, shooter) for act, shooter in actives if act is not None]
    assert live[0][0] == CRYO_HILICHURL_SHOOTER
    switched = next(
        (act for act, shooter in live
         if act != CRYO_HILICHURL_SHOOTER and shooter is not None and not shooter.defeated),
        None,
    )
    assert switched is not None, "shooter never switched (Hide did not fire)"


@pytest.mark.slow
def test_flowfire_edge_converts_physical_and_shield_reduces():
    """Blazing Axe Mitachurl attacks the shielded Ice Shieldwall Mitachurl.

    * Flowfire Edge turns the Mitachurl's Physical DMG into Pyro, so it reacts
      with the shield's Cryo Application (Melt).  A Pyro attack reacting for
      ``2 * 2 = 4`` then reduced by the shield's ``-2`` deals **2** per hit.
    * Hoarfrost Greatshield provides 2 Cryo Applications: the first reaction
      drops its usage 2 -> 1, the second removes it; afterwards the full 4 lands.
    """
    p0 = _enemy_spec((BLAZING_AXE_MITACHURL, CRYO_HILICHURL_SHOOTER, ICE_SHIELDWALL_MITACHURL))
    p1 = _enemy_spec((ICE_SHIELDWALL_MITACHURL, CRYO_HILICHURL_SHOOTER, BLAZING_AXE_MITACHURL))
    _, snaps = _play(p0, p1, seed=3, p0_fn=_skill_first, p1_fn=_end_first)

    # Per-step trace of the shielded target: (health, shield usage).
    trace = []
    for snap in snaps:
        if snap.phase in ("init_hands", "init_actives"):
            continue
        char = _chars(snap, 1)[ICE_SHIELDWALL_MITACHURL]
        trace.append((char.health, _status_usage(snap, 1, ICE_SHIELDWALL_MITACHURL, HOARFROST_GREATSHIELD)))

    usages = [u for _, u in trace]
    assert 2 in usages, "shield should start with 2 Cryo Application instances"
    assert 1 in usages, "a reaction must consume one Cryo Application (usage 2 -> 1)"
    assert None in usages, "the second reaction must remove the shield"

    # Damage-per-hit while the shield is up is 4 (Melt) - 2 (shield) = 2.
    drops = []
    for (hp0, u0), (hp1, _u1) in zip(trace, trace[1:]):
        if hp1 < hp0:
            drops.append((hp0 - hp1, u0))
    assert drops, "shielded target took no damage at all"
    shielded_hits = [d for d, u in drops if u is not None]
    assert shielded_hits and all(d == 2 for d in shielded_hits), drops
    # Once the shield is gone a Melt hit lands its full 4.
    assert 4 in [d for d, u in drops if u is None], drops


@pytest.mark.slow
def test_timaeus_npc_lineup_full_match_is_clean():
    """The enemy lineup plays a full game against a normal deck, no errors."""
    from envs.match import run_match
    from envs.policy import legal_random_policy

    p0 = _base_spec()
    p1 = _enemy_spec((CRYO_HILICHURL_SHOOTER, ICE_SHIELDWALL_MITACHURL, BLAZING_AXE_MITACHURL))
    for seed in (1, 3, 7):
        record = run_match(
            p0,
            p1,
            legal_random_policy(seed),
            legal_random_policy(seed + 100),
            seed=seed,
        )
        assert record.error is None, record.error
        assert not record.truncated
        assert not record.fallbacks0 and not record.fallbacks1
        assert not record.io_errors0 and not record.io_errors1
        assert record.rounds >= 1


# --------------------------------------------------------------------------- #
# full Mondstadt/Liyue NPC enemy roster (PLAN §4.4)
# --------------------------------------------------------------------------- #
# Character ids are the official GCG enemy-card ids; the two Fatui entries
# already exist in the upstream data package (engine HP 11/10 vs the NPC data
# 9/10 for Fatui Pyro Agent) — a documented deviation.
ALL_ENEMIES: dict[str, int] = {
    "hydroslime": 3204,
    "electroslime": 3406,
    "cryoslime": 3107,
    "cryo_hilichurl_shooter": 3102,
    "electro_hilichurl_shooter": 3402,
    "hilichurl_shooter": 3002,
    "hilichurl_fighter": 3001,
    "blazing_axe_mitachurl": 3302,
    "ice_shieldwall_mitachurl": 3101,
    "crackling_axe_mitachurl": 3403,
    "anemo_samachurl": 3501,
    "dendro_samachurl": 3703,
    "cryo_abyss_mage": 3103,
    "pyro_abyss_mage": 3301,
    "hydro_abyss_mage": 3201,
    "fatui_anemoboxer_vanguard": 3502,
    "fatui_hydrogunner_legionnaire": 3203,
    "fatui_pyro_agent": 2301,
    "mirror_maiden": 2202,
}

# Abyss Mage wards: status id attached at battle begin, with 2 element instances.
ABYSS_MAGE_WARDS = {
    3103: 131031,  # Cryo Abyss Mage -> Cryo Ward
    3301: 133011,  # Pyro Abyss Mage -> Pyro Ward
    3201: 132012,  # Hydro Abyss Mage -> Hydro Ward
}


@pytest.mark.slow
@pytest.mark.parametrize("name,cid", sorted(ALL_ENEMIES.items()))
def test_each_enemy_type_plays_a_clean_match(name: str, cid: int):
    """Every NPC enemy type must be deck-legal and play a full game without error."""
    from envs.match import run_match
    from envs.policy import legal_random_policy

    p1 = _enemy_spec((cid, CRYO_HILICHURL_SHOOTER, ICE_SHIELDWALL_MITACHURL))
    record = run_match(
        _base_spec(),
        p1,
        legal_random_policy(5),
        legal_random_policy(55),
        seed=5,
    )
    assert record.error is None, f"{name}: {record.error}"
    assert not record.truncated, name
    assert not record.fallbacks0 and not record.fallbacks1, name
    assert not record.io_errors0 and not record.io_errors1, name


@pytest.mark.slow
@pytest.mark.parametrize("cid,status", sorted(ABYSS_MAGE_WARDS.items()))
def test_abyss_mage_ward_applied_at_battle_begin(cid: int, status: int):
    _, snaps = _play(
        _base_spec(),
        _enemy_spec((cid, CRYO_HILICHURL_SHOOTER, ICE_SHIELDWALL_MITACHURL)),
        seed=3,
    )
    action_snap = next(s for s in snaps if s.phase == "action")
    assert _status_usage(action_snap, 1, cid, status) == 2


@pytest.mark.slow
def test_cryo_ward_blocks_all_damage_until_depleted():
    """Blazing Axe Mitachurl (Pyro-converted) attacks the shielded Cryo Abyss Mage.

    The ward makes the mage immune to all DMG while it holds 2 Cryo Applications;
    each Melt consumes one, and only after the ward shatters does HP drop.
    """
    ward = ABYSS_MAGE_WARDS[3103]
    p0 = _enemy_spec((BLAZING_AXE_MITACHURL, CRYO_HILICHURL_SHOOTER, ICE_SHIELDWALL_MITACHURL))
    p1 = _enemy_spec((3103, CRYO_HILICHURL_SHOOTER, ICE_SHIELDWALL_MITACHURL))
    _, snaps = _play(p0, p1, seed=3, p0_fn=_skill_first, p1_fn=_end_first)

    trace = []
    for snap in snaps:
        if snap.phase in ("init_hands", "init_actives"):
            continue
        char = _chars(snap, 1)[3103]
        trace.append((char.health, _status_usage(snap, 1, 3103, ward)))

    usages = [u for _, u in trace]
    assert 2 in usages and 1 in usages and None in usages
    assert all(hp == 4 for hp, u in trace if u is not None), trace
    assert any(hp < 4 for hp, u in trace if u is None), trace
