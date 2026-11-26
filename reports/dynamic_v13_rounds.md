# dynamic 组重跑（v13 口径）：第 1 轮有效，第 2 轮退化

> 口径：训练 seeds 1–5、评测 seeds 6–10，16 关 × 5 seeds = 80 局/组，配对比较；
> 全部在 **队伍元素重掷 + 无 rule 层 + 对局历史 + 量化复盘** 下跑（`v13` 代码）。
> 数据 `data/runs/v13_*`（未入库）。

## 1. 协议

| 项 | 值 |
|---|---|
| 训练块 | seeds 1–5（16 关 × 5 = 80 局），只用于抽取 |
| 评测块 | seeds 6–10（80 局），与基线同 seeds 配对 |
| 轮次 | 2 轮；每轮训练后抽一次，再在评测块上评估 |
| 抽取 | `scripts.extract_skills`，每关 ≤2 候选，`effort=high`，`max_tokens=32768`；复盘 prompt 带骰子/手牌利用率、反应、对方技能频次 |
| loadout | 每轮按新库重规划（`reports/loadouts/v4/vN`，16 关，0 fallback） |
| 并发 | 4 workers，串行执行（同一时刻不超过 4） |

库演化：

| 轮 | 版本 | 条数 | 构成 | 变化 |
|---|---|---|---|---|
| round 0 | v5 | 38 | 10 strategy + 28 ss | 上一轮遗留的起点 |
| round 1 | v6 | 40 | 10 strategy + 30 ss | 用 v13_static 1–5 抽取；全局 strategy 未动 |
| round 2 | v7 | 42 | 10 strategy + 32 ss | 用 v6 自己的 1–5 训练；20 条原地改写 + 2 条新增 |

## 2. 评测块结果（seeds 6–10，n=80/组，0 错误）

| 组 | 三星 | 三星率 | 平均星 | 成本 |
|---|---|---|---|---|
| `scripted` | 41/80 | 51.25% | 2.013 | $0 |
| `static` | 40/80 | 50.00% | 2.163 | $0.317 |
| `llm_empty` | 50/80 | 62.50% | 2.337 | $0.334 |
| **`dynamic` r1（v6）** | **58/80** | **72.50%** | 2.587 | $0.316 |
| `dynamic` r2（v7） | 51/80 | 63.75% | 2.400 | $0.312 |

配对（同 stage/seed）：

| 对比 | 仅前者 | 仅后者 | 净差 | McNemar p |
|---|---|---|---|---|
| `dynamic r1` vs `static` | 22 | 4 | **+18** | **0.0005** |
| `dynamic r2` vs `static` | 16 | 5 | +11 | 0.027 |
| `dynamic r2` vs `dynamic r1` | 5 | 12 | **−7** | 0.143 |
| `dynamic r2` vs `llm_empty` | 9 | 8 | +1 | 1.00 |
| `llm_empty` vs `static` | 14 | 4 | +10 | 0.031 |

逐关（scr / emp / static / v6 / v7）：

```
blanche         1  2  2  2  1      buyun      0  3  1  4  4
chefmao         2  1  0  2  1      ellin      1  4  3  4  5
irontonguetian  3  4  4  4  4      jifang     5  5  4  4  4
lan             3  3  4  2  3      liusu      4  4  4  5  4
marjorie        1  4  0  3  3      patton     3  5  5  5  5
shitou          1  0  1  3  3      swan       5  4  4  4  4
timaeus         0  3  2  3  3      timmie     4  0  0  3  1
uncletian       5  5  3  5  3      wagner     3  3  3  5  3
```

## 3. 结果说明

1. **第一轮是真实收益**：v6 在样本外 72.5%，对 static 22 胜 4 负（p=0.0005），
   对 llm_empty +8 局。训练块上 v6 是 61/80 = 76.25%（static 50/80），
   样本内外差距 3.75pp，没有明显的样本内虚高。
   增益集中在 `buyun 1→4`、`shitou 1→3`、`marjorie 0→3`、`timmie 0→3`、`wagner 3→5`。
2. **第二轮退化**：v7 相对 v6 净 −7（12 负 5 胜，p=0.14），把 v6 拉回 `llm_empty` 水平
   （净 +1，p=1.00）。掉的关正是 v6 涨起来的关：`uncletian 5→3`、`timmie 3→1`、
   `wagner 5→3`、`blanche 2→1`、`chefmao 2→1`。
3. **原因（结构性）**：第二轮对 20 条已有 stage_specific 做了**原地改写**，没有门禁。
   改写文本来自「v6 自己 1–5 的失败」，而 v6 在 1–5 上已经 76.25%，
   失败样本变少、也更偏；新文本覆盖掉了第一轮已经有效的具体打法。
   这与 `docs/failures/004` 同源：**技能内容一改就整库接受，没有逐条证据。**
4. `static`（每关 5 条手写策略）仍然低于 `llm_empty`（−10，p=0.031），
   与 004 的结论一致：手写策略在这个环境里继续拖后腿。

## 4. 成本

| 项 | 花费 |
|---|---|
| 评测块基线：scripted（免费）+ static + llm_empty | $0.651 |
| round 1：抽取 + loadout + v6 评测 + v6 训练（1–5） | ≈ $0.55 |
| round 2：抽取 + loadout + v7 评测 | ≈ $0.30 |
| **合计** | **≈ $1.50** |

## 5. 复现

```sh
REPO=~/projects/gitcg-skills; PY=/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python
cd "$REPO"; export OMP_NUM_THREADS=1
COMMON="--stages all --workers 4 --stage-rules --stage-decks configs/stage_decks.json --trace"

"$PY" -m skillloop.run --group scripted  --seeds 6,7,8,9,10 $COMMON --out data/runs/v13_scripted_s6_10.jsonl
"$PY" -m skillloop.run --group static    --seeds 6,7,8,9,10 $COMMON --library data/skills/static_v1 \
  --loadout reports/loadouts/v2 --out data/runs/v13_static_s6_10.jsonl
"$PY" -m skillloop.run --group llm_empty --seeds 6,7,8,9,10 $COMMON --out data/runs/v13_llm_empty_s6_10.jsonl

# round 1
"$PY" -m scripts.extract_skills --rows data/runs/v13_static_s1_5.jsonl --base data/skills/dyn_v4 --out data/skills/dyn_v4
"$PY" -m scripts.plan_loadout --library data/skills/dyn_v4 --out reports/loadouts/v4 --stages all --client real
"$PY" -m skillloop.run --group dynamic --seeds 6,7,8,9,10 $COMMON --library data/skills/dyn_v4 \
  --loadout reports/loadouts/v4 --out data/runs/v13_dynamic_v6_s6_10.jsonl
"$PY" -m skillloop.run --group dynamic --seeds 1,2,3,4,5 $COMMON --library data/skills/dyn_v4 \
  --loadout reports/loadouts/v4 --out data/runs/v13_dynamic_v6_s1_5.jsonl

# round 2
"$PY" -m scripts.extract_skills --rows data/runs/v13_dynamic_v6_s1_5.jsonl --base data/skills/dyn_v4 --out data/skills/dyn_v4
"$PY" -m scripts.plan_loadout --library data/skills/dyn_v4 --out reports/loadouts/v4 --stages all --client real
"$PY" -m skillloop.run --group dynamic --seeds 6,7,8,9,10 $COMMON --library data/skills/dyn_v4 \
  --loadout reports/loadouts/v4 --out data/runs/v13_dynamic_v7_s6_10.jsonl
```

## 6. 局限与下一步

- n=80 下 ±7 局仍不显著（p=0.14）；「第二轮退化」方向明确但需要 50 seeds 才能确证。
- 抽取输入只有 80 局、每关 ≤2 候选；第一轮的 22 胜 4 负已是这个样本量能给出的强信号。
- **下一步应先做逐技能门禁**：对每条候选在评测块上做配对 A/B，`CI_low > 0` 才保留；
  否则每一轮都会出现「改掉好的、写下坏的」，v7 就是例子。
- 训练块可扩到 seeds 1–10、评测 11–20，减少「第二轮没失败可学」的问题。
