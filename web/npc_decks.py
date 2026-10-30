"""C3（WS8/D18）：把 NPC 敌人阵容变成**人可以拿去打**的牌组。

D18 说「人可持 NPC 敌人卡组」。NPC「冒险挑战」的阵容有三个真实特征，
本模块逐条对应处理（不做「凑 3 人」这种想当然的假设）：

1. **人数不固定**（关卡数据里 2/3/4/5/6 人都有，含同一敌人的多份）→ **原样保留、
   不跳过、不去重、不凑数**。引擎的 `initPlayerState` 只按牌组里的角色列表建局、
   不校验人数（PvE 线已经用 2–4 人的 NPC 阵容跑通门禁）。
2. **支援区（预备）角色**与 **3. 多轮挑战**：游戏内是两种独立机制，**本接口与游戏
   内并不一致**——压扁成「后台角色 + 单场」。差异逐条写在 `SEMANTICS`（前端会原样
   展示），TODO 见文件末尾「探索替代方式来描述这两种机制」。

**牌组不带行动牌**（`cards=[]`）：游戏内 NPC 没有手牌，而引擎 `initialHandsCount`
是**全局**配置、没有 per-player 版本，只能靠**空牌堆**让这一侧开局没有手牌
（`drawCardsPlain` 遇空堆直接跳过）。人持 NPC 阵容时也一样——只能用角色技能和骰子打。
**骰子**按游戏内 NPC 规则给：持 NPC 阵容的座位在前端置 `alwaysOmni`，每轮初始
掷骰＝8 个万能骰（详见 `SEMANTICS["dice"]`）。

浏览器侧拿到 `{characters, cards}` 后**直接当牌组对象传给引擎**，不走分享码：
NPC 角色 id 不在官方 `share_id.json` 里，`DEFAULT_ASSETS_MANAGER.encode` 会抛错。
"""

from __future__ import annotations

from typing import Any

from common.paths import REPO_ROOT

DEFAULT_STAGES_PATH = REPO_ROOT / "configs" / "npc" / "npc_stages_mond_liyue.json"

# 给人用的 NPC 牌组要说清「游戏内机制 / 本接口近似 / 差异」三段（前端展示 + 测试断言）。
# 注意：**支援区**与**多轮挑战**两项与游戏内并不一致，下面如实记录差异，不冒充原版。
SEMANTICS: dict[str, str] = {
    "lineup_size": (
        "出战人数按关卡原样（含同一敌人的多份），不凑 3 人；引擎按牌组角色列表"
        "建局、不校验人数，PvE 线已用 2–4 人阵容跑通。"
    ),
    "support": (
        "【游戏内】支援区角色是场外待命：只有在场角色被击败后才依次上场，"
        "不能被主动切换。"
        "【本接口】没有支援区机制，把预备角色接在出战角色之后一起进牌组＝后台角色"
        "（开局就可见、可被主动切换），靠「出战没死就不主动切」来近似。"
        "【差异】上场时机与可见性都不一致：本接口里支援角色可以提前上场。"
    ),
    "waves": (
        "【游戏内】多轮挑战：先击败当前战场的在场角色与支援角色，然后玩家角色血量和"
        "卡牌状态保持不变、进入新战场，面对下一轮 NPC 阵容，直到全部轮次打完。"
        "【本接口】一场一局，打完即结束；关卡数据 schema 也没有 waves 字段，"
        "只能表达单场。"
        "【差异】没有轮次接续、没有「新战场」，也无法表达玩家状态跨轮保留。"
    ),
    "cards": (
        "【游戏内】NPC 没有手牌、不打行动牌（PvE 近似：NPC 永不出行动牌）。"
        "【本接口】牌组 `cards=[]`，靠空牌堆让这一侧开局没有手牌——引擎的"
        " `initialHandsCount` 是全局配置、没有 per-player 版本，空堆时"
        "`drawCardsPlain` 直接跳过。"
        "【差异】人持 NPC 阵容时同样拿不到行动牌（只能用角色技能与骰子）。"
    ),
    "dice": (
        "【游戏内】NPC 每轮拿 8 个万能元素骰。"
        "【本接口】持 NPC 阵容的座位在引擎里置 `alwaysOmni`：每轮初始掷骰全为万能骰，"
        "张数＝全局 `initialDiceCount`（默认 8）。"
        "【差异】骰子仍可主动重投——重投出来的按全局规则随机、不再是万能骰"
        "（游戏内 NPC 不重投，NPC 侧不受影响）。"
    ),
    "share_code": (
        "NPC 角色 id 不在官方 share_id 表里，编不出分享码——前端按牌组对象直传。"
    ),
}

# TODO（探索）：找**替代方式**来表达「支援区」与「多轮挑战」这两种游戏内机制——
# 现状是把它们压扁成「后台角色 + 单场」，与游戏不一致（见 SEMANTICS 的【差异】）。
# 候选方向（都还没做，需要先定口径）：
#   1. 关卡 spec 增加字段：reserve 的「上场时机」（在场全灭后自动上场）与
#      `waves`（轮次阵容列表），由引擎或外层 runner 实现，而不是塞进牌组；
#   2. 引擎侧支持 reserve/多轮（击败后自动入场、局间接续玩家血量与卡组），
#      这属于 PvE 线（PLAN §4.4）的范围，Web 侧只做展示；
#   3. 纯展示方案：牌组只放出战角色，支援/轮次信息在 UI 上作为关卡说明呈现，
#      不伪装成牌组的一部分（需要前端配合）。


def npc_decks(stages_path: str | None = None) -> dict[str, Any]:
    """返回**全部** NPC 关卡阵容（出战 + 预备）当玩家牌组用。

    **牌组不带行动牌**（`cards=[]`）：游戏内 NPC 没有手牌，而引擎的
    `initialHandsCount` 是**全局**配置、没有 per-player 版本（见
    `reports/explore/npc_pve_doc.md` §4），只能靠**空牌堆**让这一侧开局没有手牌
    ——`drawCardsPlain` 遇到空堆直接 `continue`，所以空牌组是合法的。

    形状：
    ``{"decks": [{slug, display, region, enemies, characters,
                  n_on_field, n_support, total, cards}],
       "semantics": {...}}``
    ``characters`` = 出战（按 count 展开，保留重复）+ 预备（同样展开），顺序即上场
    优先级。
    """
    from npc.spec import load_stages

    stages = load_stages(stages_path)

    decks: list[dict[str, Any]] = []
    for slug in sorted(stages):
        stage = stages[slug]
        on_field = [int(c) for c in stage.deck_characters]
        support = [int(c) for c in stage.reserve_characters]
        characters = on_field + support
        decks.append(
            {
                "slug": slug,
                "display": stage.display,
                "region": stage.region,
                "enemies": [entry.name for entry in stage.lineup],
                "reserve_enemies": [entry.name for entry in stage.reserve],
                "characters": characters,
                "n_on_field": len(on_field),
                "n_support": len(support),
                "total": len(characters),
                "cards": [],  # NPC 无手牌 → 空牌堆（见 docstring）
            }
        )
    return {
        "decks": decks,
        "semantics": dict(SEMANTICS),
    }
