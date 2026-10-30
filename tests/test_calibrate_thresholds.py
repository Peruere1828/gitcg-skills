from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from calibrate_thresholds import apply, proposals  # noqa: E402


def _rows(stage: str, rounds: list[int], *, limit_obj=True) -> list[dict]:
    return [
        {"stage": stage, "seed": i, "winner": 0, "rounds": r, "three_star": r <= 4}
        for i, r in enumerate(rounds)
    ]


def _stages(tmp_path: Path, objectives: list[dict]) -> Path:
    path = tmp_path / "stages.json"
    path.write_text(json.dumps({"stages": [{"name": "wagner", "objectives": objectives}]}), encoding="utf-8")
    return path


def test_proposals_use_p70_of_winning_rounds(tmp_path):
    stages = _stages(tmp_path, [{"type": "win_within_rounds", "n": 5, "text": "5回合内胜利"}])
    rows = _rows("wagner", [3, 4, 5, 6, 7, 8, 9, 10, 11, 12])
    proposed = proposals(rows, stages_path=stages)["wagner"]
    assert proposed["n"] == 10
    assert proposed["proposed_limit"] == 10        # ceil(P70 of 3..12) = ceil(9.3) = 10
    assert proposed["current_limit"] == 5


def test_apply_updates_existing_objective(tmp_path):
    stages = _stages(tmp_path, [{"type": "win_within_rounds", "n": 5, "text": "5回合内胜利"}])
    proposed = {"wagner": {"proposed_limit": 6}}
    changed = apply(proposed, path=stages)
    assert changed == ["wagner: win_within_rounds 5 -> 6"]
    saved = json.loads(stages.read_text(encoding="utf-8"))["stages"][0]["objectives"][0]
    assert saved["n"] == 6 and saved["text"] == "6回合内胜利"


def test_apply_adds_missing_objective(tmp_path):
    stages = _stages(tmp_path, [{"type": "win", "text": "赢得胜利"}])
    proposed = {"wagner": {"proposed_limit": 5}}
    changed = apply(proposed, path=stages)
    assert changed == ["wagner: +win_within_rounds(5)"]
    objectives = json.loads(stages.read_text(encoding="utf-8"))["stages"][0]["objectives"]
    assert [o["type"] for o in objectives] == ["win", "win_within_rounds"]
