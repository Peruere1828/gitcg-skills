# configs/npc — PvE NPC 规格（入库）

PvE「冒险挑战」NPC 的**关卡规格**与**敌人数据**定义。做法、机制与路线见
[`reports/explore/npc_pve_doc.md`](../../reports/explore/npc_pve_doc.md)。

## 文件

| 文件 | 内容 | 权威性 |
|---|---|---|
| `npc_enemies_all.json` | 蒙德/璃月 NPC 战斗里出现的**全部敌人类型**（19 类，含 3 史莱姆 / 3 深渊法师）：HP / 元素 / 充能 / A-E-Q / 被动 | **权威**：整份取自 genshin-db（游戏数据，见下「来源」）；能与 GameWith 逐条对上的 11 类已标 `cross_check`。与原 `npc_enemies_timaeus.json` 的差异记在 `discrepancies_with_timaeus_file` |
| `npc_stages_mond_liyue.json` | 16 个关卡规格（蒙德 8 + 璃月 8）：出战/预备阵容、特殊规则、挑战目标 | 阵容与目标**权威**（来源见下）；特殊规则多数待核（逐关 `special_rules_verified`） |
| `npc_enemies_timaeus.json` | 蒂玛乌斯三只敌人的 HP / 元素 / 充能 / A-E-Q / 被动 | **权威**（逐条对照公开源，HP 3/8/8 已实测）；保持不变，差异见 all 文件 |
| `npc_spec_example_timaeus.json` | 蒂玛乌斯关卡规格：阵容、特殊规则、挑战目标、参考日志 | 规格参考；其 `objectives` 为占位，**已被** `npc_stages_mond_liyue.json` 更正（见 `discrepancies_with_npc_spec_example`），原文件保留不动 |

## 约定（应该这样做）

- **敌人数据应忠实**：HP / 元素 / 充能 / A-E-Q / 被动 用 `npc_enemies_all.json`（每类自带 `sources` 与 `confidence`）。
- **出牌策略应由我们自己生成**（`NpcPolicy`：greedy + rotation），**不**照抄原作逐回合序列。
- NPC 侧：无手牌 / 无骰子 / 不打行动牌；**玩家恒定先手**（复刻时 NPC = player 1）。
- 充能：**A/E 各 +1，Q 耗净充能，上限 = energy（史莱姆/射手/萨满/暴徒 = 2，深渊法师 = 2–3，水铳重卫士 = 3）**；
  切人由角色被动（如 `Hide`/躲起来）/ 脚本强制切人驱动，**不计行动**。
- 关卡 `objectives` 支持的类型见 `npc_stages_mond_liyue.json` 的 `objective_types`；每条带 `verified`。
- **schema 未覆盖的两种游戏内机制（与实际玩法不一致，勿当成原版语义）**：
  - **支援区（`reserve`）**：游戏内是场外待命、**只有在场角色被击败后才依次上场**、不可主动切换；本 schema 只记录「有哪些预备角色」，**没有上场时机**。
  - **多轮挑战**：游戏内是打完当前战场（在场+支援）后**玩家血量与卡牌状态不变、进入新战场**打下一轮 NPC 阵容；本 schema **没有 `waves` 字段**，只能表达单场阵容。
  现有用法（PvE 关卡跑单场；Web C3 把 `reserve` 当后台角色）都是近似，差异见 `web/README.md` 的 C3 表。
  **TODO（探索）**：找替代方式描述这两种机制——给 reserve 加上场时机、给关卡加 `waves`、或把它们移出牌组/阵容表达（见 `web/npc_decks.py` 顶部 TODO）。

## 来源

- 阵容：GameWith《NPC Challengers Locations & Map》（`article/show/37530`）；
  冒险挑战固定敌方阵容（`reports/explore/npc_lineups_mond_liyue.md`）。
- 敌人 HP/技能/被动：**genshin-db**（`github.com/theBowja/genshin-db`，
  卡数据派生自游戏数据 + Fandom；经 jsDelivr 取用）；蒂玛乌斯三只另经 GameWith 逐条校对。
- 挑战目标：eurogamer《Every Adventure Challenge NPC》奖励表；玛乔丽 / 卯师傅 另经
  中国 BWIKI（`wiki.biligame.com/ys`）逐字复核。
- 特殊规则：卯师傅（BWIKI `卯师傅` 页）；蒂玛乌斯（`npc_pve_doc.md` §6，HoYoWiki entry/2547）。

## 再生成

- 敌人：`python -m scripts.explore.fetch_npc_enemies`（首次联网取 genshin-db，缓存到 gitignore 的 `data/npc_enemy_cache/`）。
- 关卡：`python -m scripts.explore.build_npc_stages`（读 `npc_enemies_all.json` 补 HP；关卡表在脚本内，逐条带来源）。

## 待核清单

- **蒂玛乌斯特殊规则** `artifact_cards_free`：本轮 HoYoWiki 不可直连，沿用 `npc_pve_doc.md` 记录，`special_rules_verified: false`。
- **其余 14 关特殊规则**：未发现/未核实，`special_rules: []`。
- **斯万目标**：来源仅列 1 条（赢得胜利），疑缺失，`objectives_verified: false`。
- **石头第 2 条目标**：来源原文为「44 回合内胜利」，疑似笔误，标 `verified: false`。
- **关卡 display 标题**：仅玛乔丽、卯师傅（BWIKI）与提米（HoYoWiki 英文）已核，其余空缺。
- **目标来源**：除玛乔丽/卯师傅外均为 eurogamer 单一二级源，待游戏内或 HoYoWiki 复核。
- **敌人数值一手性**：来源为 genshin-db（游戏数据/Fandom 派生），非官方一手；11 类已与 GameWith 逐条一致。
