# NPC 冒险挑战特殊规则（16 关）

生成日期：2026-11-02
范围：蒙德 8 + 璃月 8，共 16 个 NPC「冒险挑战」关卡
数据文件：[`configs/npc/special_rules.json`](../configs/npc/special_rules.json)

## 0. 结论

- **16/16 关找到来源并核对**，`verified: true`，没有 `unknown`。
- **10 关**开局在我方支援区放置一张支援牌；**3 关**规则明确为「无」；**3 关**只有效果、没有初始支援牌。
- 旧配置里只有 `timaeus` / `chefmao` 两条，且 `timaeus` 的 `artifact_cards_free` 是近似说法；精确说法见 §4。
- 用户提到的 `blanche`「我方所有角色 +2 充能」**有网页来源**（HoYoWiki 原文），不需要只靠手动对局背书；手动观察与原文一致。
- 这批 16 关**没有**「地脉镇石」或「地脉异常」这类不可移除支援牌；10 张初始支援牌均按标准支援牌推断 `removable: true`（推断依据与风险见 §3）。

## 1. 来源与方法（按尝试顺序）

| 来源 | 结果 | 用途 |
|---|---|---|
| **HoYoWiki / 观测枢** NPC 条目 | **成功** | 主来源。每个 NPC 条目的「挑战目标or特殊规则」块逐字给出冒险挑战的规则原文 |
| BWIKI `wiki.biligame.com/ys/<NPC>` | 部分成功 | 交叉核对。本次抓到的页面里，多数只写「来客挑战」，不是本表对应的「冒险挑战」；只有卯师傅、玛乔丽同时有冒险挑战段 |
| GameWith `article/show/37530` | 只有阵容 | 页面没有任何特殊规则字段 |
| Fandom | 连接超时 | 未采用 |

### 1.1 HoYoWiki 的访问方式

HoYoWiki 的公开条目页是 JS 渲染的：直接 `curl` 只能拿到站点壳（`<title>旅行者创作平台-观测枢-原神wiki</title>`，约 19 KB），拿不到正文。因此改用它的内容 API：

```
https://api-static.mihoyo.com/common/blackboard/ys_obc/v1/content/info?app_sn=ys_obc&content_id=<id>
```

先用搜索 API 按 NPC 名找到条目 id：

```
https://api-static.mihoyo.com/common/blackboard/ys_obc/v1/search/content?app_sn=ys_obc&keyword=<NPC名>
```

每个 stage 的 `source` 是人类可读页，`source_api` 是实际取数的 API。抓取日期 2026-11-02。

### 1.2 配置里存的 HoYoWiki entry id 是错的

`configs/npc/npc_stages_mond_liyue.json` 里：

- `timmie.stage_title.source = "HoYoWiki entry/2492"`，但 entry 2492 实际是「关宏」；
- `timaeus` 的来源写 `HoYoWiki entry/2547`，但 entry 2547 实际是「完美留影」。

这两个 id 都不是对应 NPC。本次通过搜索 API 找到的正确条目是：

| stage | NPC | 正确 content id |
|---|---|---|
| timmie | 提米 | 1335 |
| timaeus | 蒂玛乌斯 | 196 |

其余 14 关的正确 id 见 JSON 每个 stage 的 `source`。

### 1.3 「冒险挑战」vs「来客挑战」

BWIKI 的 `来客挑战` 编辑提示里会出现另一套支援牌，例如：

- 岚姐：`异变之石`
- 玛乔丽：`地脉异常·蔓生`
- 艾琳：`地脉异常·阵雨`

这些**不是**本表 16 个「冒险挑战」的规则，不能拿来填 `special_rules`。HoYoWiki 条目里可能出现【冒险挑战】和【决斗对局】两段，本次只取【冒险挑战】段；JSON 的 `challenge_title` 就是该段标题。岚姐的 HoYoWiki 冒险挑战段写的是「凯瑟琳」，与 BWIKI 来客挑战的「异变之石」不同，正是模式差异。

## 2. 总表（规则原文）

| stage | NPC | 冒险挑战标题 | 规则原文 | 初始支援（card_id） | 额外效果 |
|---|---|---|---|---|---|
| `timmie` | 提米 | 两个丘丘人 | 我方支援区初始存在一张「提米」。 | 提米（322007） | — |
| `swan` | 斯万 | 一切冒险的开始 | **无** | — | — |
| `wagner` | 瓦格纳 | 紧急修理 | 我方支援区初始存在一张「瓦格纳」。 | 瓦格纳（322004） | 武器免费装备（每回合 1 次，耗「锻造原胚」，来自卡面） |
| `marjorie` | 玛乔丽 | 都在射程范围内 | **无** | — | — |
| `blanche` | 布兰琪 | 火与冰的传说 | 战斗开始后，我方所有角色获得2点充能。 | — | 我方全体 +2 充能 |
| `timaeus` | 蒂玛乌斯 | 持盾的危机 | 我方支援区初始存在一张「蒂玛乌斯」。 | 蒂玛乌斯（322003） | 圣遗物免费装备（每回合 1 次，耗「合成材料」，来自卡面） |
| `patton` | 巴顿 | 湖边奇遇 | 行动阶段开始时，对敌方的出战角色附着水元素。 | — | **仅敌方**出战角色附水 |
| `ellin` | 艾琳 | 攀高危险 | 我方支援区初始存在一张「艾琳」。 | 艾琳（322010） | — |
| `jifang` | 纪芳 | 神侠行剑谭 | **无。** | — | — |
| `buyun` | 步云 | 熟能生巧 | 我方支援区初始存在一张「群玉阁」。 | 群玉阁（321003） | — |
| `liusu` | 刘苏 | 岩国周游记 | 我方支援区初始存在一张「刘苏」；行动阶段开始时，对双方的出战角色附着水元素。 | 刘苏（322012） | 双方出战角色附水 |
| `shitou` | 石头 | 古石旧事 | 行动阶段开始时，对双方的出战角色附着水元素。 | — | 双方出战角色附水 |
| `irontonguetian` | 田铁嘴 | 荡邪录 | 我方支援区初始存在一张「田铁嘴」。 | 田铁嘴（322011） | — |
| `chefmao` | 卯师傅 | 烈火烹油 | 我方支援区初始存在一张「卯师傅」；行动阶段开始时，对双方的出战角色附着火元素。 | 卯师傅（322005） | 双方出战角色附火 |
| `uncletian` | 天叔 | 临危受命 | 我方支援区初始存在一张「璃月港口」。 | 璃月港口（321001） | — |
| `lan` | 岚姐 | 七剑往事 | 我方支援区初始存在一张「凯瑟琳」。 | 凯瑟琳（322002） | — |

统计：初始支援 10 关；有额外效果 7 关（含 2 个来自支援牌卡面的装备免费效果）；明确「无」3 关。

## 3. `removable` 判定

JSON 里 10 张初始支援牌全部是：

```json
{"removable": true, "removable_verified": false}
```

依据：

1. 这些牌在 `data/assets/expert_system_assets_chs_latest.json` 里都是 `GCG_CARD_ASSIST`（标准支援牌），不是挑战专用的不可移除卡；
2. HoYoWiki 规则原文只写「初始存在一张」，没有「不可被弃置」「无法移除」等字样；
3. 16 关里没有「地脉镇石」类规则（资产文件里搜 `地脉镇石`、`地脉异常`、`异变之石` 均为 0 命中）。

风险：`removable_verified: false` 是刻意的——本次没有进游戏做「支援区满 4 张后再打第 5 张，看能否弃置初始支援」的实测。如果引擎需要硬结论，建议对 10 张牌各做一次溢出替换测试。按现有证据，`true` 是最合理的默认值。

## 4. 卡片 ID 与名称歧义

`card_id` 全部按 `data/assets/expert_system_assets_chs_latest.json` 的 **exact name** 查得，且都唯一：

| 名称 | card_id | 类型 |
|---|---:|---|
| 提米 | 322007 | GCG_CARD_ASSIST |
| 瓦格纳 | 322004 | GCG_CARD_ASSIST |
| 蒂玛乌斯 | 322003 | GCG_CARD_ASSIST |
| 艾琳 | 322010 | GCG_CARD_ASSIST |
| 群玉阁 | 321003 | GCG_CARD_ASSIST |
| 刘苏 | 322012 | GCG_CARD_ASSIST |
| 田铁嘴 | 322011 | GCG_CARD_ASSIST |
| 卯师傅 | 322005 | GCG_CARD_ASSIST |
| 璃月港口 | 321001 | GCG_CARD_ASSIST |
| 凯瑟琳 | 322002 | GCG_CARD_ASSIST |

没有需要留 `null` 的名称。特别检查了用户举的例子：

- **群玉阁 vs 璃月港口**：是两个不同 id（321003 vs 321001），不是同名歧义；`buyun` 用群玉阁，`uncletian` 用璃月港口。
- **地脉镇石 / 地脉异常 / 异变之石**：资产文件里没有这些名字（0 命中），它们是挑战模式专用支援牌，不在玩家卡池；本批 16 关未使用。
- 蒂玛乌斯 / 瓦格纳 / 卯师傅 / 凯瑟琳 / 刘苏 / 田铁嘴 / 提米 / 艾琳均唯一。

## 5. 差异、歧义与风险

### 5.1 蒂玛乌斯：`artifact_cards_free` 是近似说法

旧配置写 `artifact_cards_free`，容易读成「所有圣遗物无条件 0 费」。实际来源是支援牌「蒂玛乌斯」322003 的卡面：

> 入场时：此牌附带2个「合成材料」。如果我方牌组中初始包含至少6张「圣遗物」，则从牌组中随机抽取1张「圣遗物」牌。
> 结束阶段：此牌补充1个「合成材料」。
> 打出「圣遗物」手牌时：如可能，则支付等同于「圣遗物」总费用数量的「合成材料」，以免费装备此「圣遗物」。（每回合1次）

所以准确表述是：**每回合 1 次，用「合成材料」代替元素骰免费装备圣遗物**；初始只有 2 个材料，之后每结束阶段 +1。HoYoWiki 的规则原文本身只写「初始存在一张蒂玛乌斯」，没有把卡面效果写进特殊规则。JSON 用 `artifact_free_equip` + `via_support` 表达，并保留 `legacy_special_rules: ["artifact_cards_free"]` 供引擎侧对照。

瓦格纳完全同理：`weapon_free_equip`，耗「锻造原胚」，每回合 1 次。旧配置没写，但既然记录了蒂玛乌斯，建议引擎侧对瓦格纳也一视同仁。

### 5.2 布兰琪

HoYoWiki 原文：

> 战斗开始后，我方所有角色获得2点充能。

没有初始支援牌。该条与用户手动对局观察一致，因此 `verified: true`，不需要标成「仅用户实测」。注意这是「战斗开始后」一次性 +2，不是每回合回能。

### 5.3 巴顿：只对敌方

原文是「对**敌方**的出战角色附着水元素」，不是双方。JSON 用 `target: "enemy_active"`。刘苏/石头/卯师傅才是双方（`both_active`）。

### 5.4 岚姐 / 玛乔丽 / 艾琳的 BWIKI 支援牌不要混用

BWIKI 的「来客挑战」编辑提示分别是异变之石 / 地脉异常·蔓生 / 地脉异常·阵雨；这些是另一种模式。HoYoWiki 的冒险挑战：

- 岚姐：凯瑟琳（322002）
- 玛乔丽：无
- 艾琳：艾琳（322010）

### 5.5 关卡目标的顺带差异（不属于本任务，但建议 owner 看）

- `shitou`：HoYoWiki 冒险挑战写「4回合内胜利」，配置写 `n=5` 并注明 eurogamer 的「44」疑似笔误。HoYoWiki 支持 **4**。
- `lan`：HoYoWiki 写「6回合内胜利 / 引发2次元素反应」，且奖励后带「？」，配置写 `n=5`。这条 HoYoWiki 自身存疑，暂不建议直接改。
- `swan`：HoYoWiki 冒险挑战也只有 1 条目标（赢得胜利），配置的 `objectives_verified: false` 可能不是缺数据，而是本来就只有 1 条。
- 16 关的 `challenge_title` 本次都从 HoYoWiki 拿到了，可用来补配置里空缺的 `stage_title`（本任务未改 `npc_stages_mond_liyue.json`）。

### 5.6 时效性

HoYoWiki 这些 NPC 条目的最后编辑时间大多在 2023-10-25，田铁嘴 2023-11-07、卯师傅 2023-11-06。特殊规则从 3.3 起通常稳定，但不能排除后续平衡性补丁改动；本次没有进游戏复测。`verified: true` 的含义是「与 HoYoWiki 冒险挑战段逐字核对」，不是「2026-11-02 游戏内实测」。

## 6. 本次产出

| 文件 | 内容 |
|---|---|
| `configs/npc/special_rules.json` | 16 关规则、初始支援、card_id、效果、来源、`legacy_special_rules` |
| `reports/npc_special_rules.md` | 本报告 |

未修改 `configs/npc/npc_stages_mond_liyue.json`；未改代码；未跑对局；未调用 LLM。
