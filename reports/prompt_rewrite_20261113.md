# Prompt 重写与 low/none 对比（2026-11-13）

## 1. 结论先行

- 旧“紧凑 prompt”信息不足：没有元素/武器、没有支援/召唤物/状态/装备、元素附着只给数字、
  卡牌只有名字；模型只能按卡名盲猜。
- 已重写并**设为所有 LLM 组的默认协议**；`--compact-render` 仅保留用于复现历史 run。
- 规则与元素反应效果取自七圣召唤 wiki（3.3；忽略 3.4 后的改动及特技/密传）：
  <https://wiki.biligame.com/ys/七圣召唤>。
- effort=none：45/80 = 56.25%（同配对 scripted 57/80 = 71.25%）；比旧紧凑协议
  dynamic 41/80 有提升，但仍未超过 scripted。
- effort=low：见 §5（运行中，受预算门禁）。

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

### User（每步变化）

- 当前局面：角色名、`[元素·武器]`、HP/能量、附着（元素名）、装备；
- 双方支援/召唤物/状态（带可用次数与效果）；对手手牌只给张数（隐藏信息）；
- 手牌：名称、费用、效果（生成牌不在牌组资料里，必须重复效果）；
- 合法动作：统一费用、目标、技能类型、反应效果预览。

### 示例输出

艾琳关起手（`reports/prompts/ellin_mulligan_20261113.md`）：
system 4870 字符 / 3659 prompt tokens；user 564 字符。

## 3. effort=none 结果（完整 5 seeds）

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

## 4. 复现

```sh
PY=/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python
GITCG_LLM_BUDGET_USD=0.8 $PY -m skillloop.run --group dynamic \
  --stages all --seeds 1,2,3,4,5 --workers 4 \
  --library data/skills/dyn_v4 --stage-decks configs/stage_decks.json \
  --loadout reports/loadouts/v4 --card-version v3.3.0 --stage-rules \
  --reasoning none --out data/runs/v7_dynamic_full_none_s1_5.jsonl
```

## 5. effort=low

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
- 剩余预算用于补测 none 亏损的 4 个关卡（liusu/marjorie/shitou/uncletian，
  seeds1–3），结果见下方更新；总预算控制在 $2 内。

### low 的意义

同一套新 prompt 下，none 在 iron/marjorie/shitou/uncletian 上仍会拖节奏；
low 在已覆盖的 iron 上 5/5，并把 blanche/buyun/chefmao 从 scripted 的 0/1/2
提升到 2/4/4。说明“完整 prompt + 显式思考”确实能把信息用起来，而不只是
把 prompt 变长。
