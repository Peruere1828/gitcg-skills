"""C1 反选（web/counterpick.py）与 C3 NPC 阵容（web/npc_decks.py）单测。

C1 的口径是 PLAN D19：**对位矩阵 + decklab 固定规则起步**——先在评测池 20 套里
找人的牌组的代理，再按已跑好的 20×20 矩阵择优；不像就退到总强度；连矩阵都没有
才退到 decklab 池 C 固定规则牌组。

C3 的口径是 PLAN D18「人可持 NPC 敌人卡组」：NPC 阵容**人数不固定**（1/2/4/6 都有，
含同一敌人的多份）→ 全部 16 关原样返回、不跳过、不凑 3 人。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from web.counterpick import (
    MIN_PROXY_SIMILARITY,
    counter_pick,
    deck_similarity,
    deck_view_from_json,
    load_matrix,
    pool_slug_for_deck,
)
from web.npc_decks import SEMANTICS, npc_decks


@pytest.fixture(scope="module")
def pool() -> dict:
    from envs.decks import expert_deck_specs

    return {spec.name: spec for spec in expert_deck_specs()}


@pytest.fixture(scope="module")
def matrix():
    loaded = load_matrix()
    assert loaded is not None, "reports/arena 里应该有可用的对位矩阵"
    return loaded


# --------------------------------------------------------------------------- #
# 相似度
# --------------------------------------------------------------------------- #
def test_deck_similarity_identical_is_one(pool):
    deck = pool["superconduct_aggro"]
    assert deck_similarity(deck, deck) == pytest.approx(1.0)


def test_deck_similarity_weights_characters_more(pool):
    """角色不同、牌差不多 → 明显低于角色相同但牌有出入。"""
    a = pool["superconduct_aggro"]
    b = pool["natlan_battleship"]
    # 同角色 + 少量换牌：0.6*1 + 0.4*(略小于 1)
    same_chars = type(a)(name="x", characters=a.characters, cards=a.cards[:-2] + b.cards[:2])
    assert deck_similarity(a, same_chars) > 0.9
    # 角色全不同
    assert deck_similarity(a, b) < 0.6


def test_pool_slug_for_deck_matches_only_exactly(pool):
    deck = pool["unyielding_geo"]
    assert (
        pool_slug_for_deck(
            {"characters": list(deck.characters), "cards": list(deck.cards)}, pool=pool
        )
        == "unyielding_geo"
    )
    # 改一张牌就不再是那一套
    changed = list(deck.cards)
    changed[-1] = changed[-1] + 1 if changed[-1] != 1 else 2
    assert (
        pool_slug_for_deck(
            {"characters": list(deck.characters), "cards": changed}, pool=pool
        )
        is None
    )
    # 角色为空 → 不是任何一套
    assert pool_slug_for_deck({"characters": [], "cards": []}, pool=pool) is None


def test_pool_slug_for_deck_ignores_npc_deck(pool):
    """NPC 阵容（不在池内）不该被误认成池内 20 套。"""
    payload = npc_decks()
    for deck in payload["decks"]:
        assert (
            pool_slug_for_deck(
                {"characters": deck["characters"], "cards": deck["cards"]},
                pool=pool,
            )
            is None
        )


# --------------------------------------------------------------------------- #
# 反选
# --------------------------------------------------------------------------- #
def test_counter_pick_uses_matrix_against_proxy(pool, matrix):
    """人的牌组就是池内某套 → 代理＝它自己，选矩阵里对它胜率最高且不是它自己的。"""
    human = pool["natlan_battleship"]
    pick = counter_pick(human, pool=pool, matrix=matrix)
    assert pick.method == "matrix-proxy"
    assert pick.proxy == "natlan_battleship"
    assert pick.similarity == pytest.approx(1.0)
    assert pick.slug != "natlan_battleship", "不能和人打镜像"
    assert pick.slug in pool
    assert pick.policy == f"expert:{pick.slug}"
    assert pick.win_rate is not None and 0.0 < pick.win_rate < 1.0
    # 对位矩阵确实支持这个结论：胜率应等于矩阵里的合并值
    assert pick.win_rate == pytest.approx(
        matrix.rate(pick.slug, "natlan_battleship"), abs=1e-6
    )


def test_counter_pick_variant_maps_to_proxy(pool, matrix):
    """只换 2 张牌的变体仍应映射回原原型。"""
    base = pool["natlan_battleship"]
    other = pool["superconduct_aggro"]
    variant = type(base)(
        name="variant",
        characters=base.characters,
        cards=base.cards[:-2] + other.cards[:2],
    )
    pick = counter_pick(variant, pool=pool, matrix=matrix)
    assert pick.method == "matrix-proxy"
    assert pick.proxy == "natlan_battleship"
    assert pick.similarity and pick.similarity > 0.9


def test_counter_pick_falls_back_to_strength_when_dissimilar(pool, matrix):
    """角色+牌都和池内对不上（相似度低于阈值）→ 按总强度挑，不硬套对位。"""
    odd = type(next(iter(pool.values())))(
        name="odd",
        characters=(1101, 1405, 1102),
        cards=tuple(range(390001, 390031)),
    )
    pick = counter_pick(odd, pool=pool, matrix=matrix)
    assert pick.method == "matrix-strength"
    assert pick.proxy is None
    assert pick.similarity is not None and pick.similarity < MIN_PROXY_SIMILARITY
    best = max(matrix.contestants, key=lambda s: matrix.contestants[s])
    assert pick.slug == best
    assert pick.policy == f"expert:{best}"


def test_counter_pick_without_matrix_uses_decklab_rule(pool):
    pick = counter_pick(
        pool["unyielding_geo"], pool=pool, matrix=None, load_matrix_if_missing=False
    )
    assert pick.method == "decklab-rule"
    assert pick.policy == "heuristic"  # decklab 牌组没有配套脚本
    assert len(pick.cards) == 30
    assert len(pick.characters) == 3


def test_counter_pick_result_is_json_serialisable(pool, matrix):
    payload = counter_pick(pool["unyielding_geo"], pool=pool, matrix=matrix).as_dict()
    assert json.loads(json.dumps(payload))["slug"] == payload["slug"]
    assert set(payload) >= {"slug", "characters", "cards", "policy", "method", "reason"}


# --------------------------------------------------------------------------- #
# deck_view_from_json（/choose-deck 入参校验）
# --------------------------------------------------------------------------- #
def test_deck_view_from_json_accepts_plain_deck():
    view = deck_view_from_json({"characters": [1101, 1403, 1405], "cards": [1, 2]})
    assert view.characters == (1101, 1403, 1405)
    assert view.cards == (1, 2)


@pytest.mark.parametrize(
    "payload",
    [None, [], {}, {"characters": [], "cards": []}, {"characters": [1], "cards": "x"},
     {"characters": ["a"], "cards": []}],
)
def test_deck_view_from_json_rejects_bad_shapes(payload):
    with pytest.raises(ValueError):
        deck_view_from_json(payload)


# --------------------------------------------------------------------------- #
# C3：NPC 阵容（人数不固定，16 关全给）
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def npc_payload() -> dict:
    return npc_decks()


def test_npc_decks_returns_every_stage(npc_payload):
    from npc.spec import load_stages

    stages = load_stages()
    assert len(npc_payload["decks"]) == len(stages) == 16
    assert {d["slug"] for d in npc_payload["decks"]} == set(stages)
    assert set(npc_payload) >= {"decks", "semantics"}


def test_npc_decks_keep_lineup_size_as_is(npc_payload):
    """人数不为 3 是常态：2/3/4/5/6 人都要原样保留，不跳过、不凑数。"""
    sizes = Counter(d["total"] for d in npc_payload["decks"])
    assert sizes[2] >= 1 and sizes[3] >= 1 and sizes[4] >= 1
    assert max(sizes) >= 5, "关卡数据里有带预备区的多人大阵容"
    for deck in npc_payload["decks"]:
        assert deck["total"] == deck["n_on_field"] + deck["n_support"]
        assert deck["total"] >= 1
        # 顺序：出战在前、预备在后
        assert len(deck["characters"]) == deck["total"]


def test_npc_decks_expand_counts_and_keep_duplicates(npc_payload):
    """同一敌人的多份（如提米 2×火斧丘丘暴徒）要保留重复，不能去重。"""
    timmie = next(d for d in npc_payload["decks"] if d["slug"] == "timmie")
    assert timmie["total"] == 2
    assert len(set(timmie["characters"])) == 1, "2 份同一角色，去重就错了"

    jifang = next(d for d in npc_payload["decks"] if d["slug"] == "jifang")
    assert jifang["n_on_field"] == 3 and jifang["n_support"] == 3
    assert jifang["total"] == 6


def test_npc_decks_have_no_hand_cards(npc_payload):
    """游戏内 NPC 没有手牌 → 牌组必须是空牌堆（引擎 initialHandsCount 无 per-player）。"""
    from decklab.builder import load_pool

    pool_cards = set(load_pool().cards)
    for deck in npc_payload["decks"]:
        assert deck["cards"] == [], f"{deck['slug']} 不该带行动牌"
    # 说明文案要把「为什么空牌堆能成立」写清楚
    assert "空牌堆" in npc_payload["semantics"]["cards"]
    assert "initialHandsCount" in npc_payload["semantics"]["cards"]
    assert "per-player" in npc_payload["semantics"]["cards"]
    # 顺带确认：曾经填的池 C 牌仍在池内（若哪天要恢复填充，池子没变）
    assert pool_cards, "池 C 牌表不该为空"


def payload_text(payload: dict, key: str) -> str:
    return payload["semantics"][key]


def test_npc_decks_documents_the_three_approximations(npc_payload):
    """三件事的说明必须**如实记录差异**（支援区/多轮与游戏内不一致，不能冒充原版）。"""
    assert set(SEMANTICS) == set(npc_payload["semantics"])
    assert set(SEMANTICS) >= {
        "lineup_size",
        "support",
        "waves",
        "cards",
        "dice",
        "share_code",
    }
    for key in ("support", "waves", "dice"):
        text = payload_text(npc_payload, key)
        assert "【游戏内】" in text and "【本接口】" in text and "【差异】" in text, key
    assert "后台" in payload_text(npc_payload, "support")
    assert "被击败后" in payload_text(npc_payload, "support")
    assert "多轮" in payload_text(npc_payload, "waves")
    assert "血量和卡牌状态" in payload_text(npc_payload, "waves")
    assert "share_id" in payload_text(npc_payload, "share_code")


def test_npc_decks_document_omni_dice(npc_payload):
    """D18：持 NPC 牌组的座位拿 8 个万能骰（引擎 alwaysOmni，重投照常可用）。"""
    dice = payload_text(npc_payload, "dice")
    assert "8" in dice and "万能" in dice
    assert "alwaysOmni" in dice and "initialDiceCount" in dice
    # 重投照常可选（重投出来的按全局规则随机）——这个差异要如实写出来。
    assert "重投" in dice and "仍可主动重投" in dice
    # 旧的「骰子仍按全局规则发放」说明已被替换掉
    assert "骰子仍按全局规则发" not in payload_text(npc_payload, "cards")


def test_npc_decks_todo_is_tracked_in_module():
    """TODO：探索替代方式描述支援区/多轮——不能只写在文档里，代码里也要能找到。"""
    import web.npc_decks as npc_module

    source = Path(npc_module.__file__).read_text(encoding="utf-8")
    assert "TODO" in source and "探索" in source
    assert "支援区" in source and "waves" in source


def test_npc_deck_ids_are_absent_from_share_id_table(npc_payload):
    """NPC 角色编不出分享码 → 前端必须走对象直传（这里把该事实钉住）。"""
    import common.paths as paths

    share_path = (
        paths.REPO_ROOT / "build/engine/packages/assets-manager/src/data/share_id.json"
    )
    if not share_path.exists():
        pytest.skip("engine workspace not materialized (no share_id.json)")
    share_ids = set(json.loads(share_path.read_text(encoding="utf-8")).values())
    npc_ids = {c for d in npc_payload["decks"] for c in d["characters"]}
    assert npc_ids - share_ids, "NPC 角色 id 应该都不在官方分享码表里"
    # 池内 20 套的角色则必须在表里（否则那些牌组也编不出分享码）
    from decklab.builder import load_pool

    assert set(load_pool().characters) <= share_ids
