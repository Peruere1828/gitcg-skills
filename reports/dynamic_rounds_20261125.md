# dynamic 组两轮进化结果（2026-11-25）

> **2026-11-25 superseded**：此后 prompt 增加了「对局历史」段，`rule` 层已从库中删除，
> `static` 的 loadout 重规划为每关 5 条 strategy（旧版只有 1–3 条生效）。
> 本文数字属于旧 prompt / 旧库口径，只能作历史记录；新结果见 `data/runs/v12_*`。

> 口径：16 关 × 5 seeds = 80 局/组，配对比较；训练与评测**不同 seed 块**
> （训练 seeds 1–5，评测 seeds 6–10）。数据在 `data/runs/v11_*`（未入库）。
> 基线口径见 [`baseline_20261125_pve_prompt_v1.md`](./baseline_20261125_pve_prompt_v1.md)；
> 方法见 `docs/EVAL_PROTOCOL.md`。

## 1. 协议

| 项 | 值 |
|---|---|
| 训练集 | seeds 1–5（16 关 × 5 = 80 局），只用于抽取失败 |
| 评测集 | seeds 6–10（80 局），与 static / llm_empty / scripted 同 seeds 配对 |
| 轮次 | 2 轮（用户选定） |
| 抽取 | `scripts.extract_skills`，每关 ≤2 候选，effort=high，`max_tokens=32768` |
| loadout | 每轮按新库重规划（`reports/loadouts/v4/vN`，16 关，0 fallback） |
| 模型 | `deepseek-v4.1-flash`，temperature=0，推理 `none`（抽取 `high`） |
| 并发 | 3–4 workers，`OMP_NUM_THREADS=1` |

库演化：

| 轮 | 库版本 | 条数 | 构成 | 变化 |
|---|---|---|---|---|
| round 0 | `dyn_v4` v2 | 27 | 17 rule + 10 strategy | = `static_v1` v2 的精确副本 |
| round 1 | v3 | 51 | +24 stage_specific | 首次抽取（静态库 1–5 的失败） |
| round 2 | v4 | 55 | +28 stage_specific | 18 条同关原地改写、4 条新增 |

两轮抽取都**没有**改动 10 条全局 strategy（`replaces` 拒绝策略层改写，见
`docs/failures/extract-overwrote-global-strategy.md`）。

## 2. 评测结果（seeds 6–10，n=80/组，全部 0 错误/0 截断）

| 组 | 三星 | 三星率 | 平均星 | LLM calls | 成本 |
|---|---|---|---|---|---|
| `scripted` | 41/80 | 51.25% | 2.013 | 0 | $0 |
| `llm_empty` | 36/80 | 45.00% | 1.950 | 1,688 | $0.303 |
| `static` | 41/80 | 51.25% | 2.112 | 1,643 | $0.294 |
| `dynamic` r1（v3） | 39/80 | 48.75% | 2.087 | 1,723 | $0.297 |
| `dynamic` r2（v4） | 39/80 | 48.75% | 2.050 | 1,638 | $0.300 |

配对（同 stage/seed）：

| 对比 | 仅前者三星 | 仅后者三星 | 净差 | McNemar p |
|---|---|---|---|---|
| `dynamic r2` vs `static` | 8 | 10 | −2（−2.5pp） | 0.81 |
| `dynamic r2` vs `dynamic r1` | 10 | 10 | 0 | 1.00 |
| `dynamic r1` vs `static` | 10 | 12 | −2（−2.5pp） | 0.83 |
| `static` vs `llm_empty` | 12 | 7 | +5（+6.3pp） | 0.36 |
| `dynamic r2` vs `scripted` | 12 | 14 | −2（−2.5pp） | 0.85 |

### 样本内 vs 样本外

`dynamic r1` 在**训练块**（1–5）上是 51/80 = 63.75%，同块 `static` 是 47/80 = 58.75%
（配对 +10/−6，净 +4，p=0.45）；换到评测块（6–10）变成 39 vs 41（净 −2）。

同一份库、同一批候选，样本内 +5pp、样本外 −2.5pp。这正是「单轮抽取只记住训练局」
的典型形态，也是为什么评测必须换 seed 块。

## 3. 逐关三星数（seeds 6–10，各关 n=5）

| 关卡 | scripted | llm_empty | static | dyn r1 | dyn r2 |
|---|---|---|---|---|---|
| blanche | 1 | 1 | 0 | 2 | 1 |
| buyun | 0 | 0 | 0 | 1 | 0 |
| chefmao | 2 | 0 | 0 | 1 | 0 |
| ellin | 1 | 4 | 3 | 5 | 5 |
| irontonguetian | 3 | 2 | 4 | 3 | 2 |
| jifang | 5 | 4 | 5 | 4 | 4 |
| lan | 3 | 2 | 2 | 1 | 2 |
| liusu | 4 | 3 | 4 | 2 | 3 |
| marjorie | 1 | 2 | 2 | 2 | 3 |
| patton | 3 | 4 | 5 | 4 | 5 |
| shitou | 1 | 0 | 0 | 2 | 0 |
| swan | 5 | 4 | 5 | 5 | 5 |
| timaeus | 0 | 3 | 4 | 2 | 3 |
| timmie | 4 | 0 | 1 | 0 | 1 |
| uncletian | 5 | 3 | 3 | 3 | 2 |
| wagner | 3 | 4 | 3 | 2 | 3 |

两轮的逐关模式几乎相同（只有 `blanche/buyun/chefmao/shitou/uncletian` 各差 1 局），
配对上是 10 对 10 完全打平。

## 4. 结论

1. **两轮动态进化都没有产生可测收益**。r2 相对 static 净 −2，r2 相对 r1 净 0；
   在 n=80 下所有差异都不显著（p ≥ 0.8）。
2. **样本内乐观是真实存在的**：r1 在训练块 +4 局，在评测块 −2 局。以后只看训练块
   会得出「进化有效」的错误结论。
3. **第二轮是空转**：18 条原地改写 + 4 条新增，逐关结果几乎逐格相同。
   说明改写多发生在已经「打得对」的局里，没有触及真正的失败模式；
   也可能因为同关多候选竞争同一 id 时后者覆盖前者（本轮出现
   `skill_shitou` ↔ `skill_shitou_2` 交叉改写）。
4. **seed 块难度差异极大**：同为 scripted，seeds 1–5 是 63.75%，6–10 是 51.25%
   （`timmie` 1→4、`timaeus` 4→0、`blanche` 0→1）。跨块数字不可直接比较。

## 5. 成本

| 项 | 花费 |
|---|---|
| 基线 `llm_empty` + `static`（6–10） | $0.597 |
| `dynamic r1` 评测 + 训练（2×80 局） | $0.582 |
| `dynamic r2` 评测（80 局） | $0.300 |
| 两轮抽取（12 + 11 次调用） | ≈ $0.09 |
| 两轮 loadout 规划（16 次/轮） | ≈ $0.06 |
| **合计** | **≈ $1.63** |

## 6. 复现

```sh
REPO=~/projects/gitcg-skills; PY=/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python
cd "$REPO"; export OMP_NUM_THREADS=1
COMMON="--stages all --workers 4 --stage-rules --stage-decks configs/stage_decks.json"

# 评测块基线
"$PY" -m skillloop.run --group scripted   --seeds 6,7,8,9,10 $COMMON --out data/runs/v11_scripted_s6_10.jsonl
"$PY" -m skillloop.run --group llm_empty  --seeds 6,7,8,9,10 $COMMON --trace --out data/runs/v11_llm_empty_s6_10.jsonl
"$PY" -m skillloop.run --group static     --seeds 6,7,8,9,10 $COMMON --trace \
  --library data/skills/static_v1 --loadout reports/loadouts/v2 --out data/runs/v11_static_s6_10.jsonl

# round 1：用静态库的训练块失败抽取
"$PY" -m scripts.extract_skills --rows data/runs/v10_static_s1_5.jsonl \
  --base data/skills/dyn_v4 --out data/skills/dyn_v4
"$PY" -m scripts.plan_loadout --library data/skills/dyn_v4 --out reports/loadouts/v4 --stages all --client real
"$PY" -m skillloop.run --group dynamic --seeds 6,7,8,9,10 $COMMON --trace \
  --library data/skills/dyn_v4 --loadout reports/loadouts/v4 --out data/runs/v11_dynamic_v3_s6_10.jsonl

# round 2：用 v3 自己的训练块失败再抽取
"$PY" -m skillloop.run --group dynamic --seeds 1,2,3,4,5 $COMMON --trace \
  --library data/skills/dyn_v4 --loadout reports/loadouts/v4 --out data/runs/v11_dynamic_v3_s1_5.jsonl
"$PY" -m scripts.extract_skills --rows data/runs/v11_dynamic_v3_s1_5.jsonl \
  --base data/skills/dyn_v4 --out data/skills/dyn_v4
"$PY" -m scripts.plan_loadout --library data/skills/dyn_v4 --out reports/loadouts/v4 --stages all --client real
"$PY" -m skillloop.run --group dynamic --seeds 6,7,8,9,10 $COMMON --trace \
  --library data/skills/dyn_v4 --loadout reports/loadouts/v4 --out data/runs/v11_dynamic_v4_s6_10.jsonl
```

## 7. 局限与下一步

- **功效不足**：n=80 时 McNemar 需要约 ±15 的净差才接近显著；要判断 ±3pp 级差异，
  需要 50 seeds/关（约 5 倍成本，单轮动态线 ≈ $7）。
- **没有逐技能门禁**：本轮把抽取结果整库接受，18 条改写里若有一半有害、一半有益，
  净效应就是 0——正好是观测到的形态。下一步应先在**同一评测块**做每候选 / 每组的
  paired gate（`skillloop.gate.paired_delta`），只保留 CI_low>0 的改动。
- **同关多候选竞争同一 id**：应加冲突检测（同一 `replaces` 在一轮内出现两次就拒绝后者
  或改成独立 stage_specific），避免「后写覆盖前写」和 id 交叉互换。
- **训练块选择**：r1/r2 都只用 1–5 的失败；若要更多训练信号，可扩到 seeds 1–10 训练、
  11–20 评测（成本翻倍，但评测块也翻倍）。
