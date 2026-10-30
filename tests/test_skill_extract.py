from __future__ import annotations

import json

from skillloop.extract import (
    StageProfile,
    build_prompt,
    failure_profiles,
    parse_skills,
    propose_candidates,
)


def _row(stage: str, *, three_star: bool, rounds: int = 5, objectives=None, **extra) -> dict:
    return {
        "stage": stage,
        "seed": extra.pop("seed", 1),
        "three_star": three_star,
        "rounds": rounds,
        "objectives": objectives or {"win": True},
        **extra,
    }


# -- profiles ----------------------------------------------------------------
def test_failure_profiles_aggregate_per_stage():
    rows = [
        _row("wagner", three_star=False, rounds=6, objectives={"win": True, "win_within_rounds": False}),
        _row("wagner", three_star=False, rounds=7, objectives={"win": True, "win_within_rounds": False}),
        _row("wagner", three_star=True, rounds=4, objectives={"win": True, "win_within_rounds": True}),
        _row("timmie", three_star=True, rounds=5, objectives={"win": True}),
        _row("timmie", three_star=True, rounds=5, objectives={"win": True}, error="boom"),
    ]
    profiles = {p.stage: p for p in failure_profiles(rows)}
    assert set(profiles) == {"wagner", "timmie"}          # errored game dropped
    wagner = profiles["wagner"]
    assert wagner.n == 3
    assert wagner.three_star_rate == 1 / 3
    assert wagner.failed_objectives == {"win_within_rounds": 2}
    assert wagner.mean_rounds > 5


def test_profile_text_is_readable():
    profile = StageProfile(
        stage="wagner", n=10, three_star_rate=0.3, mean_rounds=6.2,
        failed_objectives={"win_within_rounds": 7}, objectives=("5回合内胜利",),
    )
    text = profile.as_text()
    assert "wagner" in text and "win_within_rounds" in text and "5回合内胜利" in text


# -- parsing -----------------------------------------------------------------
def test_parse_skills_reads_array():
    raw = json.dumps([
        {"skill_name": "速攻少装备", "description": "只打一张装备", "conditions": {"objective_types": ["win_within_rounds"]}},
        {"skill_name": "", "description": "没有名字应被丢弃"},
    ])
    skills = parse_skills(raw, stage="wagner")
    assert len(skills) == 1
    skill = skills[0]
    assert skill.skill_id.endswith("_wagner")
    assert skill.status == "candidate"
    assert skill.layer == "strategy"
    assert skill.conditions.objective_types == ("win_within_rounds",)


def test_parse_skills_tolerates_fences_and_wrappers():
    fenced = "```json\n[{\"skill_name\": \"A\", \"description\": \"B\"}]\n```"
    assert len(parse_skills(fenced, stage="s")) == 1
    wrapped = json.dumps({"skills": [{"skill_name": "A", "description": "B", "layer": "stage_specific"}]})
    skills = parse_skills(wrapped, stage="s")
    assert len(skills) == 1 and skills[0].layer == "stage_specific"


def test_parse_skills_rejects_garbage():
    assert parse_skills("nope", stage="s") == []
    assert parse_skills(None, stage="s") == []


# -- proposal ----------------------------------------------------------------
class _Client:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[list[dict]] = []

    def chat(self, messages, **_kwargs) -> str | None:
        self.calls.append(messages)
        return self.reply


def test_propose_candidates_skips_perfect_and_rare_failures():
    profiles = [
        StageProfile("perfect", 10, 1.0, 4.0, {}, ()),
        StageProfile("rare", 10, 0.9, 4.0, {"win": 1}, ()),          # 10% < 15% threshold
        StageProfile("bad", 10, 0.3, 6.0, {"win_within_rounds": 7}, ()),
    ]
    client = _Client(json.dumps([{"skill_name": "快", "description": "别拖"}]))
    skills = propose_candidates(client, profiles)
    assert len(client.calls) == 1                     # only the bad stage triggered a call
    assert len(skills) == 1 and skills[0].skill_id.endswith("_bad")


def test_build_prompt_includes_rules_and_profile():
    profile = StageProfile("wagner", 10, 0.3, 6.0, {"win_within_rounds": 7}, ("5回合内胜利",))
    messages = build_prompt(profile, rules_note="rule_dice: 骰子规则")
    assert messages[0]["role"] == "system" and "JSON" in messages[0]["content"]
    assert "rule_dice" in messages[1]["content"]
    assert "wagner" in messages[1]["content"]
