# Prompt 重写与 low/none 对比（2026-11-13）

## 1. 结论先行

- 旧“紧凑 prompt”信息不足：没有元素/武器、没有支援/召唤物/状态/装备、元素附着只给数字、
  卡牌只有名字；模型只能按卡名盲猜。
- 已重写并**设为所有 LLM 组的默认协议**；`--compact-render` 仅保留用于复现历史 run。
- 规则与元素反应效果取自七圣召唤 wiki（3.3；忽略 3.4 后的改动及特技/密传）：
  <https://wiki.biligame.com/ys/七圣召唤>。
- effort=none（最终 prompt，16 关 × seeds1–5）：
  scripted 57/80 = 71.25%；llm_empty **51/80 = 63.75%**（旧紧凑 42/80）；
  static 44/80 = 55.00%（旧紧凑 46/80）。llm_empty 已接近 scripted（Δ −0.075，
  CI 跨 0），static 反而显著下降。
- effort=low（中间版 prompt）：39 局实测 low 32/39 vs scripted 24/39；
  见 §6。

## 2. 新 prompt 结构

### System（整局固定，可缓存）

1. 角色、关卡三星目标；
2. **规则要点（3.3）**：回合流程、快速/战斗行动、费用记法、充能、区域上限、击倒、
   穿透/护盾、元素附着；
3. **元素反应表**（含具体效果，而不只是名字）：
   - 融化/蒸发：本次伤害+2；
   - 超载：+2 并强制切换；超导/感电：+1 并对其他敌方各 1 穿透；
   - 冻结：+1 并冻结；扩散：其他敌方各 1 点同元素伤害；
   - 结晶：+1 并获得 1 护盾；燃烧：+1 并生成燃烧烈焰；
   - 绽放：+1 并生成草原核；原激化：+1 并生成激化领域；
4. **对局资料**：我方三名角色全部技能（类型/费用/效果）、对方 NPC 全部技能
   （来自 `configs/npc`）、我方整套 30 张牌（费用/效果/张数）；
5. 检索到的历史技能、输出契约。

费用统一为中文：`［1雷+2任意］`、`［3草］`、`［1同色］`；NPC 的
`Cryo1+Unaligned2` 归一为 `1冰+2任意`。

### 被动技能与规则修正（2026-11-14）

- 被动技能原先完全缺失。现从引擎 GTS（`build/engine/.../characters/**/*.gts`）
  解析 `skillType passive`，按角色的 `skills` 列表挂到整局资料；NPC 变体也覆盖
  （例如冰箭丘丘人的“躲起来：出招后自动切换”）。
- 修正“装备牌算一次行动”的错误：装备/支援/事件都是**打出手牌**，默认是
  **快速行动，不结束行动权**（卡面标“战斗行动”的除外）；规则技能
  `rule_equipment` 与 `rule_draw` 同步修正。
- 候选里的打牌动作标注“快速行动，不结束行动权”。

### User（每步变化）

- 当前局面：角色名、`[元素·武器]`、HP/能量、附着（元素名）、装备；
- 双方支援/召唤物/状态（带可用次数与效果）；对手手牌只给张数（隐藏信息）；
- 手牌：名称、费用、效果（生成牌不在牌组资料里，必须重复效果）；
- 合法动作：统一费用、目标、技能类型、反应效果预览。

### 示例输出

艾琳关起手（`reports/prompts/ellin_mulligan_20261113.md`）：
system 4870 字符 / 3659 prompt tokens；user 564 字符。

## 3. effort=none：scripted / llm_empty / static（最终 prompt，完整 5 seeds）

口径：16 关 × seeds1–5，80 局/组，`reasoning=none`，完整 prompt（含被动、装备快速行动）。

| 组 | 三星 | 三星率 | 旧紧凑 prompt | 配对 Δ vs scripted |
|---|---:|---:|---:|---:|
| scripted | 57/80 | **71.25%** | 71.25% | — |
| llm_empty（rules_v1） | 51/80 | **63.75%** | 52.50%（42/80） | −0.075，CI [−0.184,+0.034] |
| static（static_v1+loadouts/v1） | 44/80 | **55.00%** | 57.50%（46/80） | −0.163，CI [−0.267,−0.058] |

- 完整 prompt 让 `llm_empty` 从 42/80 提升到 **51/80（+9 局）**，已接近 scripted
  （配对差 −0.075，CI 跨 0）；说明“只给规则”的组确实在用完整资料。
- `static` 反而降到 44/80（配对差显著为负）：`static_v1` 的 10 条策略与
  loadouts/v1 在当前 prompt 下拖累节奏（marjorie 1/5、shitou 2/5、liusu 3/5）。
- 旧版 dynamic none（45/80）是补被动/改装备规则之前的 prompt，不再作为最终口径。

逐关（三星数 / 5）：

| 关卡 | scripted | llm_empty | static |
|---|---:|---:|---:|
| blanche | 0 | 0 | 0 |
| buyun | 1 | 1 | 0 |
| chefmao | 2 | **4** | 3 |
| ellin | 4 | 4 | 3 |
| irontonguetian | 4 | 2 | 3 |
| jifang | 5 | 5 | 5 |
| lan | 4 | 4 | 3 |
| liusu | 5 | 4 | 3 |
| marjorie | 3 | 1 | 1 |
| patton | 5 | 5 | 5 |
| shitou | 5 | 4 | 2 |
| swan | 5 | 5 | 4 |
| timaeus | 0 | 0 | 0 |
| timmie | 5 | 5 | 4 |
| uncletian | 5 | 3 | 4 |
| wagner | 4 | 4 | 4 |

llm_empty 的剩余亏损集中在 iron/marjorie/uncletian/liusu/shitou——与 low 的
增益关卡正好互补；说明仅靠 prompt 还不够，需要显式思考或更强的节奏规则。

## 4. 旧版 dynamic none（补被动/改装备规则之前）

口径：16 关 × seeds 1–5，80 局，`dyn_v4` + `loadouts/v4` + `v3.3.0`，4 workers，
新 prompt 默认，`reasoning=none`。

| 组 | 三星 | 三星率 | 成本 |
|---|---:|---:|---:|
| dynamic（新 prompt, none） | 45/80 | 56.25% | $0.263 |
| dynamic（旧紧凑 prompt, none） | 41/80 | 51.25% | $0.173 |
| scripted（同配对） | 57/80 | 71.25% | $0 |

逐关 Δ（新 none − scripted）：chefmao +2、irontonguetian −3、liusu −2、
marjorie −3、shitou −3、uncletian −2、timmie −1，其余为 0。
新 prompt 修复了部分关卡（chefmao），但 iron/marjorie/shitou 仍显著低于 expert。

## 5. 复现

```sh
PY=/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python
GITCG_LLM_BUDGET_USD=0.8 $PY -m skillloop.run --group dynamic \
  --stages all --seeds 1,2,3,4,5 --workers 4 \
  --library data/skills/dyn_v4 --stage-decks configs/stage_decks.json \
  --loadout reports/loadouts/v4 --card-version v3.3.0 --stage-rules \
  --reasoning none --out data/runs/v7_dynamic_full_none_s1_5.jsonl
```

## 6. effort=low（中间 prompt）

低思考单局约 $0.045、10–20 分钟，$0.8 门禁触发后停止启动新局；已完成 27 局
（覆盖 blanche/buyun/chefmao/ellin/iron/jifang），成本 $1.111。

| 关卡 | low | scripted 同配对 | Δ |
|---|---:|---:|---:|
| blanche | 2/5 | 0/5 | +2 |
| buyun | 4/5 | 1/5 | +3 |
| chefmao | 4/5 | 2/5 | +2 |
| ellin | 3/5 | 4/5 | −1 |
| irontonguetian | 5/5 | 4/5 | +1 |
| jifang | 2/2 | 2/2 | 0 |
| **合计** | **20/27 = 74.1%** | **13/27 = 48.1%** | **+7** |

- 配对门控（three_star）：Δ = **+0.233**，95% CI [−0.076, +0.542]；
  方向为正但样本不足以过门。
- 合并（同一批 27 对）：scripted 66.25%，low **74.07%**；平均回合 5.56 → 4.63。

补测 none 亏损的 4 个关卡（liusu/marjorie/shitou/uncletian × seeds1–3，
12 局，$0.513）：low **12/12**，scripted 同配对 11/12（唯一差异 marjorie seed1）。

两批合并（39 局、10 关）：

| | low | scripted 同配对 | 差 |
|---|---:|---:|---:|
| 39 局合计 | **32/39 = 82.05%** | **24/39 = 61.54%** | **+8 局** |
| three-star 配对门控 | — | — | Δ=+0.173，95% CI [−0.007, +0.353] |

- CI 已贴近 0 但尚未过门；未测的 6 关（lan/patton/swan/timaeus/timmie/wagner）
  共 30 局，scripted 为 23/30。若 low 与 scripted 持平，总投影为
  low 55/69 ≈ 79.7% vs scripted 47/69 ≈ 68.1%；即使再丢 3 局仍高于 scripted。
- 预算：none $0.263 + low 第一批 $1.111 + low 补测 $0.513 = **$1.887**（上限 $2）。

### low 的意义

同一套新 prompt 下，none 在 iron/marjorie/shitou/uncletian 上仍会拖节奏；
low 在已覆盖的 iron 上 5/5，并把 blanche/buyun/chefmao 从 scripted 的 0/1/2
提升到 2/4/4。说明“完整 prompt + 显式思考”确实能把信息用起来，而不只是
把 prompt 变长。
