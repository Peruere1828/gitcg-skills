from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

import common.llm
import skillloop.run as run_module
from npc.spec import get_stage
from reps.schema import DeckSpec
from skillloop.agent import SkillAgentPolicy
from skillloop.library import SkillLibrary
from skillloop.loadout import Loadout, engine_lock_hash, save_loadout
from skillloop.run import (
    RunSpec,
    _seed_list,
    budget_exhausted,
    budget_from_env,
    run,
    summarize,
)
from skillloop.schema import Skill


class _FakeLLMClient:
    """Offline stand-in for ``common.llm`` clients (no network call)."""

    def __init__(self, reply: str = '{"choice": 0, "used": []}') -> None:
        self.reply = reply
        self.calls: list[list[dict[str, str]]] = []
        self.usage = None

    def chat(self, messages, **kwargs) -> str:
        self.calls.append(messages)
        return self.reply


def _row(stage: str, seed: int, three_star: bool, *, stars: int = 3, n_obj: int = 3, **extra) -> dict:
    return {
        "stage": stage,
        "seed": seed,
        "three_star": three_star,
        "stars": stars,
        "n_objectives": n_obj,
        "llm_calls": extra.pop("llm_calls", 0),
        "error": extra.pop("error", None),
        "truncated": extra.pop("truncated", False),
        **extra,
    }


def test_seed_list_accepts_count_or_list():
    # A bare count N means seeds 1..N (seed 0 is unusable, see the tests below).
    assert _seed_list("4") == (1, 2, 3, 4)
    assert _seed_list("1") == (1,)
    assert _seed_list("3,7,9") == (3, 7, 9)


def test_seed_list_rejects_engine_seed_zero():
    with pytest.raises(SystemExit, match="absorbing"):
        _seed_list("0")
    with pytest.raises(SystemExit, match="absorbing"):
        _seed_list("1,0,2")
    with pytest.raises(SystemExit, match="absorbing"):
        _seed_list("0,1,2")


def test_cli_rejects_engine_seed_zero():
    for seeds in ("0", "1,0,2"):
        with pytest.raises(SystemExit, match="absorbing"):
            run_module.main(["--group", "scripted", "--seeds", seeds])


def test_run_rejects_programmatic_seed_zero(tmp_path, monkeypatch):
    launched: list[int] = []

    def fake_play_one(item):
        launched.append(item["seed"])
        return _row(item["stage"], item["seed"], True)

    monkeypatch.setattr(run_module, "play_one", fake_play_one)
    spec = RunSpec(
        group="scripted",
        stages=("wagner",),
        seeds=(0,),
        deck="superconduct_aggro",
        out=str(tmp_path / "run.jsonl"),
    )
    with pytest.raises(SystemExit, match="absorbing"):
        run(spec, progress=False)
    assert launched == []
    assert not (tmp_path / "run.jsonl").exists()


def test_engine_seed_zero_changes_the_first_roll():
    """Engine-level evidence for refusing seed 0.

    The engine's RNG is a minstd LCG (``A=48271, C=0``), so 0 is an absorbing
    state: a real seed-0 game freezes the first-round dice into a degenerate
    pattern (with ``blanche/keqing_shuanghuo`` all eight dice come out as
    ``DICE_TYPE_CRYO`` = 1), which is why the runner refuses that seed.
    """
    from envs.policy import build_policy as env_build_policy

    def first_action_dice(seed: int) -> tuple[int, ...]:
        stage = get_stage("timmie")
        deck = run_module.resolve_deck("superconduct_aggro")
        npc_deck = type(deck)(
            name=f"npc_{stage.name}", characters=stage.deck_characters, cards=deck.cards
        )
        record = run_module.run_match(
            deck,
            npc_deck,
            env_build_policy("expert:superconduct_aggro", seed=0, role="player"),
            run_module.npc_policy_for_stage(stage),
            seed=seed,
            max_steps=40,          # the first action request happens in round 1
            record_decisions=True,
            record_views=True,
        )
        decision = next(
            d for d in record.decisions
            if d.request_type == "action" and d.player == 0
        )
        return tuple(decision.player_view["players"][0]["dice"])

    zero = first_action_dice(0)
    one = first_action_dice(1)
    assert len(zero) == len(one) == 8
    assert zero != one


def test_summarize_computes_rates_and_ignores_invalid_rows():
    rows = [
        _row("wagner", 1, True),
        _row("wagner", 2, False, stars=2),
        _row("timmie", 1, True),
        _row("timmie", 2, False, error="boom"),
        _row("timmie", 3, False, truncated=True),
    ]
    summary = summarize(rows)
    assert summary["games"] == 5
    assert summary["valid"] == 3
    assert summary["errors"] == 2
    assert summary["three_star_rate"] == round(2 / 3, 4)
    assert set(summary["per_stage"]) == {"wagner", "timmie"}
    assert summary["per_stage"]["wagner"] == {"n": 2, "three_star_rate": 0.5, "mean_stars": 2.5}
    # mean stars normalised by the number of objectives (5/6 here)
    assert summary["mean_stars_per_objective"] == round((1 + 2 / 3 + 1) / 3, 4)


def test_summarize_handles_empty():
    assert summarize([]) == {"games": 0, "valid": 0}


def test_fingerprint_tracks_spec_changes():
    base = dict(group="scripted", stages=("wagner",), seeds=(1, 2), deck="superconduct_aggro")
    a = RunSpec(**base).fingerprint()
    b = RunSpec(**{**base, "deck": "dvalin_bonk"}).fingerprint()
    c = RunSpec(**{**base, "seeds": (1, 2, 3)}).fingerprint()
    assert a != b and a != c
    assert a["group"] == "scripted" and a["library_version"] == 0
    assert "engine_lock" in a and "hp_table" in a


def test_fingerprint_tracks_trace_flag():
    base = dict(group="scripted", stages=("wagner",), seeds=(1,), deck="superconduct_aggro")
    plain = RunSpec(**base).fingerprint()
    traced = RunSpec(**base, trace=True).fingerprint()
    assert plain["trace"] is False and traced["trace"] is True
    assert plain != traced


def test_fingerprint_keeps_stage_list_and_hashes_stage_file():
    fp = RunSpec(
        group="scripted", stages=("wagner", "timmie"), seeds=(1,), deck="superconduct_aggro"
    ).fingerprint()
    assert fp["stages"] == ["wagner", "timmie"]
    assert fp["stages_file_sha1"] != "missing"


# -- four-group policy/library contract --------------------------------------
def test_group_library_defaults():
    assert run_module.DEFAULT_LIBRARY_ROOTS == {
        "static": "data/skills/static_v1",
        "dynamic": "data/skills/dyn_v4",
    }
    assert run_module.LLM_GROUPS == ("llm_empty", "static", "dynamic")
    assert run_module.LIBRARY_GROUPS == ("static", "dynamic")
    assert run_module.LOADOUT_GROUPS == ("static", "dynamic")
    # Frozen libraries pin their version; ``dynamic`` evolves, so compare against
    # the library file instead of hard-coding its current round.
    static = RunSpec(
        group="static", stages=("wagner",), seeds=(1,), deck="superconduct_aggro"
    ).fingerprint()
    assert static["library_root"] == run_module.DEFAULT_LIBRARY_ROOTS["static"]
    assert static["library_version"] == 3
    dynamic = RunSpec(
        group="dynamic", stages=("wagner",), seeds=(1,), deck="superconduct_aggro"
    ).fingerprint()
    assert dynamic["library_root"] == run_module.DEFAULT_LIBRARY_ROOTS["dynamic"]
    assert dynamic["library_version"] == SkillLibrary.load(
        run_module.DEFAULT_LIBRARY_ROOTS["dynamic"]
    ).version
    # llm_empty has no library at all and must resolve to None even with --library.
    for group in ("llm_empty", "scripted"):
        fp = RunSpec(
            group=group, stages=("wagner",), seeds=(1,), deck="superconduct_aggro",
            library_root="data/skills/static_v1",
        ).fingerprint()
        assert fp["library_root"] is None and fp["library_version"] == 0


def test_scripted_group_is_not_a_skill_agent():
    spec = RunSpec(
        group="scripted", stages=("wagner",), seeds=(1,), deck="superconduct_aggro"
    )
    policy = run_module.build_policy(spec, get_stage("wagner"))
    assert not isinstance(policy, SkillAgentPolicy)


@pytest.mark.parametrize(
    ("group", "strategies"),
    [("llm_empty", None), ("static", 10), ("dynamic", None)],
)
def test_group_policies_load_expected_library_layers(tmp_path, group, strategies):
    spec = RunSpec(
        group=group,
        stages=("wagner",),
        seeds=(1,),
        deck="superconduct_aggro",
        # llm_empty must ignore any override (it has no library at all).
        library_root=_library_root(tmp_path) if group == "llm_empty" else None,
    )
    client = _FakeLLMClient()
    policy = run_module.build_policy(spec, get_stage("wagner"), client=client)
    assert isinstance(policy, SkillAgentPolicy)
    system = policy._system_message(SimpleNamespace(player_view=None))["content"]
    if group == "llm_empty":
        # Rules are the system's fixed text; nothing is loaded from a library.
        assert policy.library is None
        assert policy._skills == []
        assert not any("- [strat_" in line for line in system.splitlines())
        return
    assert policy.library is not None
    layers = policy.library.by_layer()
    assert set(layers) == {"strategy", "stage_specific"}
    if group == "dynamic":
        # The dynamic library grows with every evolution round, so its strategy
        # count is not hard-coded; only the floor is.
        assert layers["strategy"] >= 10
        assert layers["stage_specific"] >= 1
        # Proves the default resolves to the evolving root, not static_v1.
        assert policy.library.meta.get("library_root") == "data/skills/dyn_v4"
    else:
        assert layers == {"strategy": strategies, "stage_specific": 0}
    # Rules are one fixed system text, so no rule skill can be a prompt line.
    assert "[rule_" not in system


def test_llm_empty_ignores_library_and_loadout_overrides(tmp_path):
    other = _library_root(tmp_path, version=9)
    loadout_root = tmp_path / "loadouts"
    save_loadout(loadout_root, _loadout(version=9))

    spec = RunSpec(
        group="llm_empty",
        stages=("wagner",),
        seeds=(1,),
        deck="superconduct_aggro",
        library_root=other,
        loadout_root=str(loadout_root),
    )
    client = _FakeLLMClient()
    policy = run_module.build_policy(spec, get_stage("wagner"), client=client)
    assert policy.library is None
    assert policy.config.pinned_skills == ()
    fp = spec.fingerprint()
    assert fp["library_root"] is None
    assert fp["library_version"] == 0
    # Loadout is not part of an llm_empty run at all.
    assert fp["loadout_root"] is None and fp["loadout_hash"] == "none"


def test_pinned_rule_from_loadout_is_dropped_from_the_skill_block(tmp_path):
    # An old loadout may still name ``rule_burst``; the library no longer has that
    # skill (loading strips the rule layer), so the pin resolves to nothing.
    library_root = _legacy_library_root(tmp_path, version=4)
    spec = RunSpec(
        group="static", stages=("wagner",), seeds=(1,), deck="superconduct_aggro",
        library_root=library_root,
    )
    client = _FakeLLMClient('{"choice": 0, "used": ["rule_burst"]}')
    policy = run_module.build_policy(
        spec, get_stage("wagner"), client=client, pinned_skills=("rule_burst",)
    )
    system = policy._system_message(SimpleNamespace(player_view=None))["content"]
    assert "- [rule_burst]" not in system
    assert [skill.skill_id for skill in policy._skills] == []


def test_play_one_records_no_library_for_llm_empty(monkeypatch):
    monkeypatch.setattr(common.llm, "build_llm_client", lambda: _FakeLLMClient())
    monkeypatch.setattr(
        run_module, "run_match",
        lambda *args, **kwargs: SimpleNamespace(
            winner=0, rounds=3, error=None, truncated=False,
        ),
    )
    monkeypatch.setattr(
        run_module, "evaluate_stage",
        lambda stage, record: SimpleNamespace(three_star=True, stars=3, objectives=()),
    )
    spec = RunSpec(
        group="llm_empty",
        stages=("wagner",),
        seeds=(7,),
        deck="superconduct_aggro",
    )
    row = run_module.play_one(
        {"spec": spec.__dict__, "stage": "wagner", "seed": 7, "task_index": 0,
         "stage_decks": {}}
    )
    assert row["library_root"] is None
    assert row["library_version"] == 0
    assert row["pinned_skills"] == []
    assert row["injected_skills"] == []


# -- compact trace rows ------------------------------------------------------
def _fake_match_record():
    decisions = [
        SimpleNamespace(
            player=0, step_index=1, request_type="action",
            chosen_label="use_skill:0:1", chosen_kind="action_use_skill",
            used_dice=(1,), player_view={"round_number": 1},
            subject_definition_id=32031, card_kind=None, decider="llm",
        ),
        SimpleNamespace(
            player=1, step_index=1, request_type="action",
            chosen_label="declare_end:0:none", chosen_kind="action_declare_end",
            used_dice=(), player_view={"round_number": 2},
            subject_definition_id=0, card_kind=None,
        ),
    ]
    events = [
        SimpleNamespace(kind="reaction", player=0, round_number=1,
                        reaction_type="superconduct"),
    ]
    return SimpleNamespace(
        winner=0, rounds=2, error=None, truncated=False,
        decisions=decisions, events=events,
    )


def test_play_one_adds_trace_keys_only_when_enabled(monkeypatch):
    monkeypatch.setattr(run_module, "run_match", lambda *args, **kwargs: _fake_match_record())
    monkeypatch.setattr(
        run_module, "evaluate_stage",
        lambda stage, record: SimpleNamespace(three_star=True, stars=3, objectives=()),
    )

    def play(*, trace: bool) -> dict:
        spec = RunSpec(
            group="scripted", stages=("timmie",), seeds=(1,),
            deck="superconduct_aggro", trace=trace,
        )
        return run_module.play_one(
            {"spec": spec.__dict__, "stage": "timmie", "seed": 1, "task_index": 0}
        )

    plain = play(trace=False)
    assert "trace" not in plain and "trace_meta" not in plain

    traced = play(trace=True)
    assert isinstance(traced["trace"], list) and traced["trace"]
    assert traced["trace_meta"] == {
        "n_decisions": 2,
        "truncated": False,
        # Compact-trace counts of who decided each recorded row (the second fake
        # row has no ``decider`` label, so it counts as script).
        "deciders": {"llm": 1, "script": 1},
    }
    assert any(line.startswith("结局 ") for line in traced["trace"])
    json.dumps(traced, ensure_ascii=False)          # JSON-serialisable


def test_cli_trace_flag():
    args = run_module.build_parser().parse_args(["--group", "scripted", "--trace"])
    assert args.trace is True


def test_play_one_records_decisions_for_card_objectives():
    """Regression: the scored row must see what the contestant played.

    ``play_equipment_card`` / ``play_event_cards`` are judged from
    ``MatchRecord.decisions``; with ``record_decisions=False`` they were always
    false. ``timaeus`` seed 7 is a real expert-vs-NPC game in which the expert
    plays artifacts, so this asserts the objective is *observable*, not that the
    policy is good.
    """
    spec = RunSpec(
        group="scripted",
        stages=("timaeus",),
        seeds=(7,),
        deck="superconduct_aggro",
    )
    row = run_module.play_one(
        {"spec": spec.__dict__, "stage": "timaeus", "seed": 7, "task_index": 0}
    )
    assert row["error"] is None
    assert row["objectives"]["play_equipment_card"] is True


# -- card pool version -------------------------------------------------------
_V33_POOL = Path(__file__).resolve().parents[1] / "configs/pools/mond_liyue_launch_v33.json"
_POST_330_CARD = 213061      # v3.4.0 card ("砰砰礼物"), rejected by CreateParam(version="v3.3.0")


def _v33_deck(*, name: str = "superconduct_aggro", post_330_card: int | None = None) -> DeckSpec:
    """A deck that fits the v3.3 launch pool (the shipped expert decks use later cards).

    ``post_330_card`` replaces the last card with a card released after v3.3.0.
    """
    pool = json.loads(_V33_POOL.read_text(encoding="utf-8"))
    cards = [int(card) for card in pool["cards"][:30]]
    if post_330_card is not None:
        cards[-1] = int(post_330_card)
    return DeckSpec(
        name=name,
        characters=tuple(int(c) for c in pool["characters"][:3]),
        cards=tuple(cards),
    )


def test_card_version_pins_the_pool_and_is_recorded(monkeypatch):
    """A v3.3-only scripted game plays under the pin and the row records it."""
    deck = _v33_deck()
    monkeypatch.setattr(run_module, "resolve_deck", lambda name: deck)
    spec = RunSpec(
        group="scripted",
        stages=("timmie",),
        seeds=(1,),
        deck=deck.name,
        card_version="v3.3.0",
    )
    row = run_module.play_one(
        {"spec": spec.__dict__, "stage": "timmie", "seed": 1, "task_index": 0}
    )

    assert row["error"] is None
    assert row["card_version"] == "v3.3.0"
    assert row["rounds"] > 0
    assert row["truncated"] is False


def test_card_version_rejects_out_of_pool_cards_as_a_row_error(tmp_path, monkeypatch):
    """A post-3.3 card fails the engine's state build; the grid gets error rows."""
    deck = _v33_deck(post_330_card=_POST_330_CARD)
    monkeypatch.setattr(run_module, "resolve_deck", lambda name: deck)
    monkeypatch.delenv("GITCG_LLM_BUDGET_USD", raising=False)
    spec = RunSpec(
        group="scripted",
        stages=("timmie",),
        seeds=(1, 2),
        deck=deck.name,
        card_version="v3.3.0",
        out=str(tmp_path / "run.jsonl"),
    )

    summary = run(spec, progress=False)
    rows = [
        json.loads(line)
        for line in (tmp_path / "run.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    # The out-of-pool card is caught while building the state, before any game:
    # every row carries the engine error instead of aborting the whole grid.
    assert summary["valid"] == 0
    assert len(rows) == 2
    for row in rows:
        assert row["error"].startswith("AssertionError")
        assert "v3.3.0" in row["error"]
        assert row["card_version"] == "v3.3.0"


def test_fingerprint_tracks_card_version():
    base = dict(group="scripted", stages=("timmie",), seeds=(1,), deck="superconduct_aggro")
    default = RunSpec(**base).fingerprint()
    pinned = RunSpec(**base, card_version="v3.3.0").fingerprint()
    assert default["card_version"] is None
    assert pinned["card_version"] == "v3.3.0"
    assert default != pinned


# -- fixed stage decks -------------------------------------------------------
def _write_stage_decks(path, mapping: dict[str, str]) -> str:
    path.write_text(
        json.dumps({"stages": {stage: {"deck": deck, "notes": "test"} for stage, deck in mapping.items()}}),
        encoding="utf-8",
    )
    return str(path)


def _library_root(tmp_path, *, version: int = 4) -> str:
    root = tmp_path / "library"
    SkillLibrary.from_skills(
        [
            Skill(skill_id="alpha", skill_name="甲", description="技能甲"),
            Skill(skill_id="beta", skill_name="乙", description="技能乙"),
        ],
        version=version,
    ).save(root)
    return str(root)


def _legacy_library_root(tmp_path, *, version: int = 4) -> str:
    """A pre-migration library: one ``rule`` entry plus two strategy skills.

    ``Skill`` itself refuses ``layer="rule"`` now, so the legacy shape is written as
    raw JSON — which is exactly what ``history/*.json`` snapshots look like.
    """
    root = tmp_path / "legacy_library"
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "library_version": version,
        "meta": {"history": []},
        "skills": [
            {"skill_id": "rule_burst", "skill_name": "元素爆发",
             "description": "爆发需要能量", "layer": "rule"},
            {"skill_id": "alpha", "skill_name": "甲", "description": "技能甲",
             "layer": "strategy"},
            {"skill_id": "beta", "skill_name": "乙", "description": "技能乙",
             "layer": "strategy"},
        ],
    }
    (root / "library.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return str(root)


def _loadout(*, version: int = 4, deck: str = "superconduct_mika_attack") -> Loadout:
    return Loadout(
        stage="wagner",
        deck=deck,
        skills=("beta", "alpha"),
        rationale="test",
        model="stub-model",
        prompt_hash="abcdef123456",
        fallback=False,
        library_version=version,
        engine_lock=engine_lock_hash(),
    )


@pytest.mark.parametrize("group", ["scripted", "dynamic"])
def test_stage_decks_change_scripted_and_dynamic_identically(tmp_path, group):
    config = _write_stage_decks(
        tmp_path / "stage_decks.json", {"wagner": "superconduct_mika_attack"}
    )
    spec = RunSpec(
        group=group,
        stages=("wagner",),
        seeds=(1,),
        deck="superconduct_aggro",
        library_root=_library_root(tmp_path) if group == "dynamic" else None,
        stage_decks_root=config,
    )
    policy = run_module.build_policy(spec, get_stage("wagner"))

    if group == "scripted":
        assert policy.name == "expert:superconduct_mika_attack"
    else:
        assert policy.task.deck == "superconduct_mika_attack"
        assert policy.base.name == "expert:superconduct_mika_attack"


@pytest.mark.parametrize("group", ["scripted", "dynamic"])
def test_missing_stage_deck_falls_back_to_run_spec_deck(tmp_path, group):
    config = _write_stage_decks(
        tmp_path / "stage_decks.json", {"wagner": "superconduct_mika_attack"}
    )
    spec = RunSpec(
        group=group,
        stages=("timmie",),
        seeds=(1,),
        deck="superconduct_aggro",
        library_root=_library_root(tmp_path) if group == "dynamic" else None,
        stage_decks_root=config,
    )
    policy = run_module.build_policy(spec, get_stage("timmie"))

    if group == "scripted":
        assert policy.name == "expert:superconduct_aggro"
    else:
        assert policy.task.deck == "superconduct_aggro"


def test_fingerprint_tracks_stage_decks_and_loadout(tmp_path):
    config = _write_stage_decks(
        tmp_path / "stage_decks.json", {"wagner": "superconduct_mika_attack"}
    )
    loadout_root = tmp_path / "loadouts"
    save_loadout(loadout_root, _loadout())

    base = dict(group="dynamic", stages=("wagner",), seeds=(1,), deck="superconduct_aggro")
    a = RunSpec(**base).fingerprint()
    b = RunSpec(**base, stage_decks_root=config).fingerprint()
    c = RunSpec(**base, loadout_root=str(loadout_root)).fingerprint()
    assert a != b and a != c
    assert b["stage_decks_hash"] != "none"
    assert c["loadout_hash"] != "none"


def test_run_passes_frozen_loadout_and_stage_deck_to_workers(tmp_path, monkeypatch):
    config = _write_stage_decks(
        tmp_path / "stage_decks.json", {"wagner": "superconduct_mika_attack"}
    )
    library_root = _library_root(tmp_path, version=4)
    loadout_root = tmp_path / "loadouts"
    loadout = _loadout(version=4)
    save_loadout(loadout_root, loadout)

    seen: list[dict] = []

    def fake_play_one(item):
        seen.append(item)
        return _row(item["stage"], item["seed"], True, cost_usd=0.0)

    monkeypatch.setattr(run_module, "play_one", fake_play_one)
    monkeypatch.delenv("GITCG_LLM_BUDGET_USD", raising=False)
    spec = RunSpec(
        group="dynamic",
        stages=("wagner",),
        seeds=(1, 2),
        deck="superconduct_aggro",
        library_root=library_root,
        loadout_root=str(loadout_root),
        stage_decks_root=config,
        out=str(tmp_path / "run.jsonl"),
    )
    run(spec, progress=False)

    assert len(seen) == 2
    for item in seen:
        assert item["loadout"]["deck"] == "superconduct_mika_attack"
        assert item["loadout"]["skills"] == ["beta", "alpha"]
        assert item["stage_decks"] == {"wagner": "superconduct_mika_attack"}
    # the same frozen loadout is reused across seeds
    assert {item["loadout"]["prompt_hash"] for item in seen} == {"abcdef123456"}


def test_missing_stage_loadout_fails_loudly(tmp_path, monkeypatch):
    config = _write_stage_decks(tmp_path / "stage_decks.json", {})
    loadout_root = tmp_path / "loadouts"
    save_loadout(loadout_root, _loadout())
    monkeypatch.setattr(run_module, "play_one", lambda item: _row(item["stage"], item["seed"], True))
    spec = RunSpec(
        group="dynamic",
        stages=("wagner", "timmie"),
        seeds=(1,),
        deck="superconduct_aggro",
        library_root=_library_root(tmp_path),
        loadout_root=str(loadout_root),
        stage_decks_root=config,
        out=str(tmp_path / "run.jsonl"),
    )
    with pytest.raises(SystemExit, match="no loadout for stage"):
        run(spec, progress=False)


def test_stale_loadout_is_rejected(tmp_path, monkeypatch):
    library_root = _library_root(tmp_path, version=5)
    loadout_root = tmp_path / "loadouts"
    save_loadout(loadout_root, _loadout(version=4))
    monkeypatch.setattr(run_module, "play_one", lambda item: _row(item["stage"], item["seed"], True))
    spec = RunSpec(
        group="dynamic",
        stages=("wagner",),
        seeds=(1,),
        deck="superconduct_mika_attack",
        library_root=library_root,
        loadout_root=str(loadout_root),
        out=str(tmp_path / "run.jsonl"),
    )
    with pytest.raises(SystemExit, match="stale loadout"):
        run(spec, progress=False)


def test_play_one_pins_skills_and_records_loadout_hash(tmp_path, monkeypatch):
    captured: dict = {}

    class _DummyPolicy:
        decisions: list = []

    def fake_build_policy(spec, stage, *, client=None, deck=None, pinned_skills=()):
        captured.update(deck=deck, pinned_skills=pinned_skills)
        return _DummyPolicy()

    monkeypatch.setattr(run_module, "build_policy", fake_build_policy)
    monkeypatch.setattr(
        run_module, "run_match",
        lambda *args, **kwargs: SimpleNamespace(winner=0, rounds=3, error=None, truncated=False),
    )
    monkeypatch.setattr(
        run_module, "evaluate_stage",
        lambda stage, record: SimpleNamespace(three_star=True, stars=3, objectives=()),
    )
    monkeypatch.setattr(common.llm, "build_llm_client", lambda: SimpleNamespace(usage=None))

    spec = RunSpec(
        group="dynamic",
        stages=("wagner",),
        seeds=(7,),
        deck="superconduct_aggro",
        library_root=_library_root(tmp_path),
    )
    loadout = _loadout()
    row = run_module.play_one(
        {
            "spec": spec.__dict__,
            "stage": "wagner",
            "seed": 7,
            "task_index": 0,
            "stage_decks": {},
            "loadout": loadout.as_dict(),
        }
    )

    assert captured == {"deck": "superconduct_mika_attack", "pinned_skills": ("beta", "alpha")}
    assert row["deck"] == "superconduct_mika_attack"
    assert row["pinned_skills"] == ["beta", "alpha"]
    # The dummy policy is not a ``SkillAgentPolicy``, so the non-LLM default applies.
    assert row["injected_skills"] == []
    assert row["skill_loadout_hash"] == loadout.fingerprint()
    assert row["library_version"] == 4


def test_play_one_static_injected_skills_drop_the_rule_layer(tmp_path, monkeypatch):
    """``pinned_skills`` is the request; ``injected_skills`` is what reached the prompt."""
    root = Path(_legacy_library_root(tmp_path, version=4))

    def fake_run_match(contestant, npc_deck, policy, npc_policy, **kwargs):
        # Build the real system prefix so ``_skills`` reflects the injection path.
        policy._system_message(SimpleNamespace(player_view=None))
        return SimpleNamespace(winner=0, rounds=3, error=None, truncated=False)

    monkeypatch.setattr(run_module, "run_match", fake_run_match)
    monkeypatch.setattr(common.llm, "build_llm_client", lambda: _FakeLLMClient())
    monkeypatch.setattr(
        run_module, "evaluate_stage",
        lambda stage, record: SimpleNamespace(three_star=True, stars=3, objectives=()),
    )
    spec = RunSpec(
        group="static", stages=("wagner",), seeds=(1,),
        deck="superconduct_aggro", library_root=str(root),
    )
    loadout = Loadout(
        stage="wagner", deck="superconduct_aggro", skills=("rule_burst", "alpha"),
        rationale="test", model="stub-model", prompt_hash="abcdef123456",
        library_version=4, engine_lock=engine_lock_hash(),
    )
    row = run_module.play_one(
        {"spec": spec.__dict__, "stage": "wagner", "seed": 1, "task_index": 0,
         "stage_decks": {}, "loadout": loadout.as_dict()}
    )
    assert row["pinned_skills"] == ["rule_burst", "alpha"]
    assert row["injected_skills"] == ["alpha"]


# -- budget guard ------------------------------------------------------------
def test_budget_from_env_and_exhausted(monkeypatch):
    monkeypatch.delenv("GITCG_LLM_BUDGET_USD", raising=False)
    assert budget_from_env() is None
    monkeypatch.setenv("GITCG_LLM_BUDGET_USD", "0.25")
    assert budget_from_env() == 0.25
    assert not budget_exhausted(0.2499, 0.25)
    assert budget_exhausted(0.25, 0.25)
    assert budget_exhausted(1.0, 0.25)
    assert not budget_exhausted(1.0, None)


def test_budget_guard_stops_runner_and_marks_summary(tmp_path, monkeypatch):
    launched: list[tuple[str, int]] = []

    def fake_play_one(item):
        launched.append((item["stage"], item["seed"]))
        return _row(item["stage"], item["seed"], True, cost_usd=0.03)

    monkeypatch.setattr(run_module, "play_one", fake_play_one)
    monkeypatch.setenv("GITCG_LLM_BUDGET_USD", "0.05")
    spec = RunSpec(
        group="llm_empty",
        stages=("wagner",),
        seeds=(1, 2, 3, 4, 5),
        deck="superconduct_aggro",
        workers=1,
        out=str(tmp_path / "run.jsonl"),
    )
    summary = run(spec, progress=False)

    assert len(launched) == 2                     # stopped before launching game 3
    assert summary["games"] == 2
    assert summary["aborted"] == "budget"
    assert summary["budget_usd"] == 0.05
    assert summary["spent_usd"] == 0.06
    rows = [json.loads(line) for line in
            (tmp_path / "run.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    # The budget-abort path runs the same closing meta write.
    meta = json.loads((tmp_path / "run.jsonl.meta.json").read_text(encoding="utf-8"))
    assert meta["fingerprint"] == spec.fingerprint()
    assert "finished_at" in meta and meta["n_games"] == 2


class _ThreadExecutor:
    """ProcessPoolExecutor stand-in: runs the fake games in local threads."""

    def __init__(self, max_workers=None, mp_context=None, **_kwargs):
        self._pool = ThreadPoolExecutor(max_workers=max_workers)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._pool.shutdown(wait=True)
        return False

    def submit(self, fn, *args, **kwargs):
        return self._pool.submit(fn, *args, **kwargs)


def test_parallel_window_stays_full_without_batch_barrier(tmp_path, monkeypatch):
    lock = threading.Lock()
    active = 0
    max_active = 0
    starts: dict[int, float] = {}
    ends: dict[int, float] = {}

    def fake_play_one(item):
        nonlocal active, max_active
        seed = item["seed"]
        with lock:
            starts[seed] = time.perf_counter()
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05 if seed == 1 else 0.2)      # game 1 frees its slot early
        with lock:
            active -= 1
            ends[seed] = time.perf_counter()
        return _row(item["stage"], seed, True, cost_usd=0.0)

    monkeypatch.setattr(run_module, "ProcessPoolExecutor", _ThreadExecutor)
    monkeypatch.setattr(run_module, "play_one", fake_play_one)
    monkeypatch.delenv("GITCG_LLM_BUDGET_USD", raising=False)
    spec = RunSpec(
        group="llm_empty",
        stages=("wagner",),
        seeds=tuple(range(1, 9)),
        deck="superconduct_aggro",
        workers=3,
        out=str(tmp_path / "run.jsonl"),
    )
    summary = run(spec, progress=False)

    assert summary["games"] == 8 and len(starts) == 8
    assert max_active == 3                         # window stays full
    # A batch/barrier scheduler would wait for games 2 and 3 before starting 4.
    assert starts[4] < ends[2] and starts[4] < ends[3]


def test_parallel_budget_stops_new_submissions(tmp_path, monkeypatch):
    lock = threading.Lock()
    launched: list[int] = []

    def fake_play_one(item):
        with lock:
            launched.append(item["seed"])
        time.sleep(0.05 if item["seed"] == 1 else 0.4)
        return _row(item["stage"], item["seed"], True, cost_usd=0.03)

    monkeypatch.setattr(run_module, "ProcessPoolExecutor", _ThreadExecutor)
    monkeypatch.setattr(run_module, "play_one", fake_play_one)
    monkeypatch.setenv("GITCG_LLM_BUDGET_USD", "0.05")
    spec = RunSpec(
        group="llm_empty",
        stages=("wagner",),
        seeds=tuple(range(1, 9)),
        deck="superconduct_aggro",
        workers=3,
        out=str(tmp_path / "run.jsonl"),
    )
    summary = run(spec, progress=False)

    # 3 start immediately; the first costs $0.03, so one more (game 4) is allowed;
    # the second completion reaches $0.06 and no further games are submitted.
    assert len(launched) == 4
    assert summary["games"] == 4
    assert summary["aborted"] == "budget"
    assert summary["budget_usd"] == 0.05
    assert summary["spent_usd"] == 0.12


# -- optional in-game scratchpad (A/B arm) -----------------------------------
def test_fingerprint_tracks_scratchpad():
    base = dict(group="scripted", stages=("wagner",), seeds=(1,), deck="superconduct_aggro")
    off = RunSpec(**base).fingerprint()
    on = RunSpec(**base, scratchpad=True).fingerprint()
    assert off["scratchpad"] is False and on["scratchpad"] is True
    assert off != on


def test_cli_scratchpad_flag():
    parser = run_module.build_parser()
    assert parser.parse_args(["--group", "scripted"]).scratchpad is False
    assert parser.parse_args(["--group", "scripted", "--scratchpad"]).scratchpad is True


def test_build_policy_sets_scratchpad_and_token_budget():
    client = _FakeLLMClient()
    base = dict(group="llm_empty", stages=("wagner",), seeds=(1,), deck="superconduct_aggro")
    off = run_module.build_policy(RunSpec(**base), get_stage("wagner"), client=client)
    assert off.config.scratchpad is False
    assert off.config.max_tokens == 64
    on = run_module.build_policy(
        RunSpec(**base, scratchpad=True), get_stage("wagner"), client=client
    )
    assert on.config.scratchpad is True
    assert on.config.max_tokens == 160
    assert on.config.note_max_chars == 240 and on.config.scratchpad_max_chars == 600


def test_build_policy_reasoning_raises_the_token_budget():
    client = _FakeLLMClient()
    base = dict(group="llm_empty", stages=("wagner",), seeds=(1,), deck="superconduct_aggro")
    none = run_module.build_policy(RunSpec(**base), get_stage("wagner"), client=client)
    assert none.config.reasoning_effort == "none"
    assert none.config.max_tokens == 64

    low = run_module.build_policy(
        RunSpec(**base, reasoning="low"), get_stage("wagner"), client=client
    )
    assert low.config.reasoning_effort == "low"
    assert low.config.max_tokens == 12288


def test_stage_profiles_apply_to_the_era_base(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({"stages": {"lan": {"setup_rounds": 0}}}), encoding="utf-8")
    base = dict(group="scripted", stages=("lan",), seeds=(1,), deck="keqing_jihua")

    default = run_module.build_policy(RunSpec(**base), get_stage("lan"))
    assert default.agent.profile.rule_config.setup_rounds == 2

    spec = RunSpec(**base, stage_profiles=str(path))
    tuned = run_module.build_policy(spec, get_stage("lan"))
    assert tuned.agent.profile.rule_config.setup_rounds == 0
    assert spec.fingerprint()["stage_profiles"] != "none"


def test_play_one_records_scratchpad_flag(monkeypatch):
    monkeypatch.setattr(
        run_module, "run_match",
        lambda *args, **kwargs: SimpleNamespace(
            winner=0, rounds=3, error=None, truncated=False,
        ),
    )
    monkeypatch.setattr(
        run_module, "evaluate_stage",
        lambda stage, record: SimpleNamespace(three_star=True, stars=3, objectives=()),
    )
    spec = RunSpec(
        group="scripted", stages=("timmie",), seeds=(1,),
        deck="superconduct_aggro", scratchpad=True,
    )
    row = run_module.play_one(
        {"spec": spec.__dict__, "stage": "timmie", "seed": 1, "task_index": 0}
    )
    assert row["scratchpad"] is True


# -- code/prompt provenance --------------------------------------------------
def test_fingerprint_records_code_and_prompt_provenance():
    fp = RunSpec(
        group="scripted", stages=("wagner",), seeds=(1,), deck="superconduct_aggro"
    ).fingerprint()
    for key in ("prompt_version", "code_hash"):
        assert key in fp
    # HEAD sha / dirty flag are meta-only provenance: a commit or ``git add`` must
    # not make the same code look like a different spec on resume.
    assert "code_sha" not in fp and "code_dirty" not in fp
    assert fp["prompt_version"] == run_module.PROMPT_VERSION
    code_hash = fp["code_hash"]
    assert len(code_hash) == 12 and all(char in "0123456789abcdef" for char in code_hash)
    assert run_module._code_hash() == code_hash      # stable within a checkout


def test_fingerprint_ignores_git_head_and_dirty(monkeypatch):
    spec = RunSpec(
        group="scripted", stages=("wagner",), seeds=(1,), deck="superconduct_aggro"
    )
    before = spec.fingerprint()
    monkeypatch.setattr(run_module, "_code_sha", lambda: "f" * 40)
    monkeypatch.setattr(run_module, "_code_dirty", lambda: True)
    assert spec.fingerprint() == before


def test_code_hash_tracks_source_content(tmp_path, monkeypatch):
    """The content hash (unlike HEAD) changes when a run-defining source does."""
    root = tmp_path / "repo"
    source = root / "skillloop" / "run.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setattr(run_module, "REPO_ROOT", root)
    monkeypatch.setattr(run_module, "CODE_TREES", ("skillloop",))

    first = run_module._code_hash()
    assert len(first) == 12
    source.write_text("VALUE = 2\n", encoding="utf-8")
    second = run_module._code_hash()
    assert second != first
    # Files outside ``CODE_TREES`` never move the hash.
    (root / "docs").mkdir()
    (root / "docs" / "note.md").write_text("notes\n", encoding="utf-8")
    assert run_module._code_hash() == second


def test_code_hash_ignores_tests_and_docs():
    """Only the run-defining trees are hashed, so writing docs keeps resume valid."""
    tracked = [Path("skillloop/run.py"), Path("envs/match.py"), Path("npc/spec.py")]
    for path in tracked:
        assert path.parts[0] in run_module.CODE_TREES
    assert "tests" not in run_module.CODE_TREES
    assert "docs" not in run_module.CODE_TREES
    assert "reports" not in run_module.CODE_TREES


def test_play_one_records_prompt_version_and_system_hash(monkeypatch):
    class _DummyPolicy:
        decisions = [
            SimpleNamespace(error=None, latency=0.0, used=(), system_hash="abc123abc123"),
            SimpleNamespace(error=None, latency=0.0, used=(), system_hash="abc123abc123"),
        ]

    monkeypatch.setattr(run_module, "build_policy", lambda *a, **k: _DummyPolicy())
    monkeypatch.setattr(
        run_module, "run_match",
        lambda *args, **kwargs: SimpleNamespace(
            winner=0, rounds=3, error=None, truncated=False,
        ),
    )
    monkeypatch.setattr(
        run_module, "evaluate_stage",
        lambda stage, record: SimpleNamespace(three_star=True, stars=3, objectives=()),
    )
    spec = RunSpec(
        group="scripted", stages=("timmie",), seeds=(1,), deck="superconduct_aggro",
    )
    row = run_module.play_one(
        {"spec": spec.__dict__, "stage": "timmie", "seed": 1, "task_index": 0}
    )
    assert row["prompt_version"] == run_module.PROMPT_VERSION
    assert row["system_hash"] == "abc123abc123"
    json.dumps(row, ensure_ascii=False)


def test_play_one_records_empty_system_hash_without_llm_decisions(monkeypatch):
    monkeypatch.setattr(
        run_module, "run_match",
        lambda *args, **kwargs: SimpleNamespace(
            winner=0, rounds=3, error=None, truncated=False,
        ),
    )
    monkeypatch.setattr(
        run_module, "evaluate_stage",
        lambda stage, record: SimpleNamespace(three_star=True, stars=3, objectives=()),
    )
    spec = RunSpec(
        group="scripted", stages=("timmie",), seeds=(1,), deck="superconduct_aggro",
    )
    row = run_module.play_one(
        {"spec": spec.__dict__, "stage": "timmie", "seed": 1, "task_index": 0}
    )
    assert row["system_hash"] == ""
    assert row["prompt_version"] == run_module.PROMPT_VERSION


def test_run_meta_gets_finished_provenance_and_keeps_fingerprint(tmp_path, monkeypatch):
    def fake_play_one(item):
        return _row(
            item["stage"], item["seed"], True, cost_usd=0.0,
            prompt_version=run_module.PROMPT_VERSION,
            system_hash="deadbeef0001",
        )

    monkeypatch.setattr(run_module, "play_one", fake_play_one)
    monkeypatch.delenv("GITCG_LLM_BUDGET_USD", raising=False)
    out = tmp_path / "run.jsonl"
    spec = RunSpec(
        group="scripted", stages=("wagner",), seeds=(1, 2),
        deck="superconduct_aggro", out=str(out),
    )
    run(spec, progress=False)

    meta_path = tmp_path / "run.jsonl.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["fingerprint"] == spec.fingerprint()
    assert meta["finished_at"].endswith("+00:00")
    assert meta["system_hashes"] == {"wagner": "deadbeef0001"}
    assert meta["n_games"] == 2 and meta["out"] == str(out)
    assert meta["prompt_version"] == run_module.PROMPT_VERSION
    for key in ("code_sha", "code_dirty", "code_hash"):
        assert key in meta

    # Resuming the finished grid must not raise and must keep the provenance.
    run(spec, progress=False)
    again = json.loads(meta_path.read_text(encoding="utf-8"))
    assert again["fingerprint"] == meta["fingerprint"]
    assert again["system_hashes"] == {"wagner": "deadbeef0001"}


def test_resume_repeats_zero_pending_and_rejects_real_spec_changes(tmp_path, monkeypatch, capsys):
    played: list[tuple[str, int]] = []

    def fake_play_one(item):
        played.append((item["stage"], item["seed"]))
        return _row(item["stage"], item["seed"], True)

    monkeypatch.setattr(run_module, "play_one", fake_play_one)
    monkeypatch.delenv("GITCG_LLM_BUDGET_USD", raising=False)
    out = tmp_path / "run.jsonl"
    spec = RunSpec(
        group="scripted", stages=("wagner",), seeds=(1, 2),
        deck="superconduct_aggro", out=str(out),
    )
    run(spec)
    assert "2 pending / 0 done" in capsys.readouterr().out
    # Same spec, same code: nothing replays.
    run(spec)
    assert "0 pending / 2 done" in capsys.readouterr().out
    # A commit / dirty tree only changes provenance, not the fingerprint: still a
    # clean resume.
    monkeypatch.setattr(run_module, "_code_sha", lambda: "0" * 40)
    monkeypatch.setattr(run_module, "_code_dirty", lambda: True)
    run(spec)
    assert "0 pending / 2 done" in capsys.readouterr().out
    assert played == [("wagner", 1), ("wagner", 2)]

    # A real spec change still refuses to reuse the same --out.
    with pytest.raises(SystemExit, match="different spec"):
        run(RunSpec(**{**spec.__dict__, "deck": "dvalin_bonk"}))
