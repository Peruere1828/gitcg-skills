"""C3 配套：`web/npc_assets.py`（把 NPC `.gts` 元数据转成 assets-manager 的 CustomData）。

镜像里没有 NPC 自定义角色的 datum，前端 `getData(NPC id)` 会拿到 404 的 JSON、各字段
全 undefined —— 前端因此崩过两次（`undefined.matchAll`、`e.playCost is not iterable`），
技能也没有名字与描述。上游 `AssetsManager` 的 `customData` 选项就是为此准备的
（`setupCustomData` 把完整 datum 灌进 `dataCacheSync`），本模块产出它的输入。
"""

from __future__ import annotations

from web.npc_assets import (
    DICE_TYPES,
    ENTITY_TYPES,
    npc_custom_data,
    npc_custom_data_summary,
)


def test_custom_data_has_all_npc_characters_and_skills():
    data = npc_custom_data()
    characters = data["characters"]
    assert len(characters) >= 17, "蒙德+璃月 NPC 敌人角色应全部解析出来"
    ids = [ch["id"] for ch in characters]
    assert len(set(ids)) == len(ids), "角色 id 不该重复"
    for ch in characters:
        assert isinstance(ch["id"], int) and ch["id"] > 0
        assert ch["name"], f"{ch['id']} 缺名字（.gts 的 @name）"
        assert ch["rawDescription"], f"{ch['id']} 缺描述（.gts 的 @description）"
        assert ch["cardFaceUrl"].startswith("data:image/svg+xml;base64,")
        assert isinstance(ch["hp"], int) and ch["hp"] > 0
        assert isinstance(ch["maxEnergy"], int) and ch["maxEnergy"] >= 0
        assert ch["tags"], f"{ch['id']} 缺 tags"
        assert len(ch["skills"]) >= 3, f"{ch['name']} 技能少于 3 个"


def test_skills_have_names_descriptions_and_dice_costs():
    """技能的名字/描述/骰子费用是这次要修的东西——逐条钉住。"""
    data = npc_custom_data()
    skills = data["skills"]
    assert len(skills) >= 50
    for skill in skills:
        assert isinstance(skill["id"], int)
        assert skill["name"], f"技能 {skill['id']} 缺名字"
        assert skill["rawDescription"], f"技能 {skill['name']} 缺描述"
        assert skill["type"] in {"normal", "elemental", "burst", "technique", "passive"}
        for cost in skill["playCost"]:
            assert set(cost) == {"type", "count"}
            assert cost["type"] in set(DICE_TYPES.values())
            # 充能走角色 maxEnergy，不该混进技能骰子费用
            assert cost["type"] != DICE_TYPES["Energy"]
            assert cost["count"] > 0
    # 顶层技能与角色自带技能一致（setupCustomData 两处都会灌缓存）
    own = {s["id"] for ch in data["characters"] for s in ch["skills"]}
    assert own <= {s["id"] for s in skills}


def test_entities_cover_combat_statuses_and_summons():
    """冰史莱姆的「元素生命·冰」这类战斗状态/召唤物也要有 datum，否则点开就崩。"""
    data = npc_custom_data()
    kinds = {e["type"] for e in data["entities"]}
    assert kinds <= set(ENTITY_TYPES.values())
    assert "status" in kinds and ("summon" in kinds or "combatStatus" in kinds)
    ids = [e["id"] for e in data["entities"]]
    assert len(set(ids)) == len(ids)
    char_ids = {ch["id"] for ch in data["characters"]}
    assert not set(ids) & char_ids, "实体 id 不该和角色 id 撞车"
    for entity in data["entities"]:
        assert entity["name"]
        assert isinstance(entity["playCost"], list)


def test_stage_lineups_are_fully_covered():
    """关卡阵容里的**自定义**角色都必须有 CustomData（官方角色本来就走镜像数据）。"""
    import json

    import common.paths as paths
    from web.npc_decks import npc_decks

    share_path = (
        paths.REPO_ROOT / "build/engine/packages/assets-manager/src/data/share_id.json"
    )
    if not share_path.exists():
        import pytest

        pytest.skip("engine workspace not materialized (no share_id.json)")
    official = set(json.loads(share_path.read_text(encoding="utf-8")).values())
    have = {ch["id"] for ch in npc_custom_data()["characters"]}
    used = {c for deck in npc_decks()["decks"] for c in deck["characters"]}
    missing = used - have - official
    assert not missing, f"缺 CustomData 的自定义角色：{sorted(missing)}"


def test_summary_is_json_safe():
    import json

    summary = npc_custom_data_summary()
    assert summary["characters"] == len(npc_custom_data()["characters"])
    assert json.loads(json.dumps(summary))["skills"] >= 50
