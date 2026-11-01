# STATUS — 当前状态（2026-11-01）

> 只写「现在有效」的事实；历史进 `reports/`，负结果进 `docs/failures/`。
> 任务与验收见 [`../PLAN.md`](../PLAN.md)，统计口径见 [`EVAL_PROTOCOL.md`](./EVAL_PROTOCOL.md)。

## 1. 仓库与环境

| 项 | 状态 |
|---|---|
| 仓库 | `~/projects/gitcg-skills`，从上游 `genshin-GITCG@98b4dfa` 快照（2026-10-28） |
| 环境层 | `envs/ reps/ npc/ coach/ agents/ eval/ common/ web/ configs/ engine-overlay/` 原样快照 |
| 已删除 | `train/`、`kaggle/`、`scripts/` 训练脚本、`envs/cfr_*`、`envs/fork_*`、`agents/neural.py` |
| conda env | `gitcg-skill`（Python 3.12，`gitcg==0.21.0`，numpy，pytest；**无 torch**） |
| 引擎 | 上游 overlay 版 `libgitcg.so`（快照时的构建）；血量补丁后**待重链** |

## 2. 难度标定（D4）

- 规则：敌人血量 **≤6 → 6，其余 +2**；审计快照 `configs/npc/hp_table.json`（19 类敌人 / 16 关）。
- 已改：17 个 overlay `.gts`；2 个上游敌人（`fatui_pyro_agent` 11→13、`mirror_maiden` 10→12）
  已复制为 overlay 覆盖文件。
- 发现并修正：`fatuipyroagent` 的 `hp_card` 原记 9，引擎实际 11（配置与引擎不一致）。
- 待办：增量重链 `libgitcg.so` → 装进 `gitcg-skill` → 用 `contestant` 跑 24 seeds → 重标
  `win_within_rounds`（P70）→ 记录新基线。

## 3. 基线（10 seeds × 16 关 = 160 局；**阈值统一 5**）

| 组 | 三星率 | 胜率 | 说明 |
|---|---|---|---|
| scripted | **0.3812** | 0.944 | 纯脚本 |
| contestant | **0.4062** | 0.950 | 手写目标注入（仅 irontonguetian/liusu +0.20） |

> 阈值改回 5 后（D7），两组都明显下降，`blanche`/`timaeus`/`uncletian`/`wagner` 归零 ——
> 回合与目标两项都要"主动争取"，正是技能库要解决的问题。旧标定结果（P70=6–9）留档在
> `reports/calibration/thresholds.json`，仅作难度画像。

## 3.0 早期基线（**已废弃**：阈值按 P70 放宽到 6–9 时的数字，留作对照）

| 组 | 策略 | 三星率 | 胜率 | 备注 |
|---|---|---|---|---|
| 脚本 | `expert:superconduct_aggro` | **0.4125** | ~96% | 无目标注入；新血量下 |
| 参照 | `contestant` | 运行中 | — | 上游旧血量 74.7% |
| LLM 空库 | `deepseek-v4.1-flash` | 待跑 | — | 端点已接通（见 §5） |

脚本组逐关（新血量）：`jifang`/`swan` 1.00，`patton`/`lan` 0.90，`timmie`/`buyun` 0.80，
`shitou` 0.50，`marjorie` 0.40，`ellin` 0.20，`chefmao` 0.10，
`wagner`/`timaeus`/`liusu`/`irontonguetian`/`uncletian`/`blanche` **0.00**。
→ 血量补丁只压三星、几乎不影响胜负（胜率仍 ~96%），符合 D4 的预期。

## 4. 已完成 / 进行中

已完成：
- 仓库快照 + 裁剪 + 文档骨架 + 课程要求入库（首个 commit）。
- `gitcg-skill` env（gitcg 0.21.0 / numpy / pytest，无 torch）。
- 血量补丁（19 敌人 / 16 关）+ 审计 `configs/npc/hp_table.json`。
- `skillloop/` 六个模块：`schema`（技能表示）、`library`（分层检索/激活/版本快照）、
  `render`（稳定前缀 + 编号候选，真实对局实测 system 211 字符 / user 506 字符）、
  `agent`（LLM 直选 + 回退 + 逐决策日志）、`gate`（按关配对 t 检验）、`extract`（失败画像 → 候选技能）。
- 34 个 engine-free 单测全绿；`scripts/probe_render.py` 可在真实对局上检查 prompt。

进行中：
- 引擎增量重链（V8 全量首编，`build/engine-build.log`，约 583/1232）。
- DeepSeek/OpenCode Console 接入探针（子任务），产出 base_url / model / 计费字段。

## 3.1 阈值标定（24 seeds × 16 关 = 384 局，手写策略）

按「胜局回合 P70」重标 `win_within_rounds`（明细 `reports/calibration/thresholds.json`）：

| 关卡 | 旧 | 新 | 关卡 | 旧 | 新 |
|---|---|---|---|---|---|
| blanche | 5 | **9** | liusu | 5 | 6 |
| chefmao | 5 | 7 | shitou | **44** | 6 |
| ellin | 5 | 7 | timaeus | 5 | 6 |
| irontonguetian | 5 | 6 | wagner | 5 | **2** |
| jifang | 5 | 3 | marjorie | 5 | 6 |
| lan | 6 | 5 | **swan** | （无） | **+5** |

标定后手写策略总三星率 0.487（标定前 0.4375）。
仍为 0% 的 3 关（`wagner`/`timaeus`/`uncletian`）失分点不是回合数，而是
`play_equipment_card` / `play_event_cards` —— 这正是技能库要补的**策略**缺口（不是阈值问题）。

## 3.2 标定后 10-seed 复测（新任务集）

| 组 | 三星率 | 说明 |
|---|---|---|
| scripted | **0.5625** | 纯脚本（无目标意识） |
| contestant | **0.6250** | 手写目标注入；配对 Δ=+0.062，CI [−0.020,+0.145] |

**剩余失分画像（scripted，10 局/关）**：`play_equipment_card` 10/10 失败（timaeus、wagner）、
`play_event_cards` 10/10（uncletian）、`use_elemental_burst` 3–6/10（blanche、irontonguetian、liusu）、
`trigger_elemental_reactions` 5/10（shitou），其余主要是 `win_within_rounds`（约 30%）。
→ 任务集现在**未饱和**（0.56），且失分集中在可被"指令遵循"解决的具体行为上。

## 3.3 四组 10-seed 结果（阶段性，640 局；effort=none）

> 本节是 t5b 之前的可引用结果（commit `f79868f`）；**当前可引用**见 §3.6。

| 组 | 三星率 | 胜率 | 调用/局 | prompt tokens/局 | 缓存命中 | 成本/局 |
|---|---|---|---|---|---|---|
| scripted | **0.5625** | 0.944 | 0 | 0 | — | 0 |
| contestant（参照） | **0.6250** | 0.950 | 0 | 0 | — | 0 |
| llm_empty | **0.4938** | 0.963 | 27.1 | 13.7k | 2.5% | $0.0042 |
| static（规则库） | **0.4813** | 0.938 | 25.4 | 32.2k | **66.3%** | $0.0038 |

**结论 1：规则注入没有帮助，但被缓存吸收了成本。**
static vs llm_empty 配对 Δ = **−0.012**，CI [−0.103,+0.078]（跨 0）。
prompt tokens 变成 2.4×，但缓存命中率从 2.5% → 66.3%，单 token 成本降 2.6×，
**每局成本基本不变**（$0.0038 vs $0.0042）。→ 「规则层」应当保留（它便宜、可解释），
但**不能指望它提分**；提分要靠策略层。

**结论 2：LLM 的强弱正好互补，指明了技能库的方向。**

| 现象 | 关卡 | Δ(LLM−scripted) |
|---|---|---|
| LLM 更好（会按目标做事） | blanche +0.50、liusu +0.50、irontonguetian +0.30 | 目标型（爆发/回合） |
| LLM 更差（对局质量弱） | patton −0.70、buyun −0.50、jifang −0.50、lan −0.30 | 节奏/骰子/反应 |

**结论 3：3 关对所有组都是 0%**（timaeus / uncletian / wagner，装备/事件目标 + 严回合），
占总局数 19%；`wagner` 的 2 回合上限是标定过紧（P70=2），后续按 P80 复核。

## 3.4 成本标定（2026-10-30/31 实测）

| group | $/局 | calls/局 | completion tokens/次 |
|---|---:|---:|---:|
| smoke2_static (effort=none) | 0.0038 | 25.4 | 15 |
| smoke2_llm_empty (effort=none) | 0.0042 | 27.1 | 15 |
| t5_static (effort=low) | 0.0400 | 25.5 | 1215 |
| t5_llm_empty (effort=low) | 0.0374 | 24.7 | 1167 |

- **当前配置**：对局 `reasoning_effort=none` + `max_tokens=64`（D10）；思考只用于技能提取/反思（`effort=high`, 8192）。
- t5 于 **2026-10-31 因额度耗尽中止**：`static` 82/160 行、`llm_empty` 90/160 行；这两个部分 run **不可引用**（复盘见 [`failures/thinking-in-game-cost-blowup.md`](./failures/thinking-in-game-cost-blowup.md)）。
- 当日日志总花费 **$7.93**，其中 t5 占 **$6.64**；按实测外推，全量 4 组 10 seeds（3 个 LLM 组）约 **$19**，50 seeds/关约 **$96**。
- 最后可引用结果是 §3.6 的 t5b 五组（2026-11-01）；§3.3 的 640 局降为历史对照。

## 3.5 吞吐：parked scheduler（可用，未规模化验证）

`game.step()` 同步调用策略，一进程一局时墙钟主要耗在模型往返。`skillloop/parked.py` 在
`action` 决策点快照引擎状态、丢弃当前 live game，把 prompt 交给线程池，答案到达后从快照恢复；
一个进程可同时保持多局在飞，引擎仍单线程。入口：`scripts/run_parked.py`（行格式与 `skillloop.run` 相同）。

**已离线证明**（可复跑）：

- `PYTHONPATH=. "$PY" scripts/probe_resume.py --sweep` → `parked=30 identical=30/30`：
  PvP 4 seeds × 3 occurrences + 3 关 × 2 seeds × 3 occurrences，parked+resumed 的终局 JSON 与不中断对局字节相同。
- `tests/test_parked.py` 全绿；其中 `test_parked_rows_match_synchronous_runner` 在 2 关 × 2 seeds 上
  用 stub client 对比 `skillloop.run.play_one` 与 `ParkedRunner`，行字段集合、决策/事件序列（按语义字段）一致。

**硬限制**：只有 `action` 请求可 park（`PARKABLE_REQUESTS = ("action",)`）。`switch_hands` 恢复后终局不一致
（`choose_active` 在样本内偶然一致，保守排除；`reroll_dice` / `select_card` 未验证），因此换牌 / 首发角色
仍由 expert 回答。也正因如此，parked run 与普通 `skillloop.run` 的决策流不同，**不能直接按同一口径对比**；
要对比需先把同步侧也限制为 `request_types=("action",)`（测试即如此构造）。

**未验证**：parked 路径在真实 API 下的规模化吞吐与单位时间成本（429 退避继承 `common.llm`，未在该路径压测）；目前只有离线单测/探针证据。

## 3.6 t5b：effort=none 的五组结果（2026-11-01）

**这是当前可引用的主结果**（明细、逐关表、复现命令：[`../reports/t5b_summary.md`](../reports/t5b_summary.md)）。
口径：16 关 × seeds **0–9** = 160 局/组，`top_k=5`，deck `superconduct_aggro`，
engine lock `fa42fdfec70b` / hp_table `0bbae5d8798a`，`deepseek-v4.1-flash`（temperature 0，对局 effort=none）；
一条命令 `bash data/run_t5b.sh` 跑完（16:32–17:04 UTC），`scripted`/`contestant` 复用 t5 链。

| 组 | 三星率 | 胜率 | 成本/局 |
|---|---:|---:|---:|
| scripted | 0.3812 | 0.9437 | 0 |
| contestant（参照） | 0.4062 | 0.9500 | 0 |
| llm_empty | 0.3563 | 0.9625 | $0.0023 |
| static（`static_v1`，v1，27 技能） | 0.3000 | 0.9437 | $0.0021 |
| dynamic（`dyn_v1`，v2，42 技能） | **0.4250** | 0.9437 | $0.0024 |

- **门禁（按关配对 t，6 对）**：只有 **static→dynamic 过线**（+0.1250，CI [+0.0075, +0.2425]）；
  其余 5 对全跨 0 —— llm_empty→dynamic +0.0687 [−0.0507,+0.1882]、scripted→dynamic +0.0437、
  scripted→static −0.0813、scripted→llm_empty −0.0250、scripted→contestant +0.0250 → 均不采纳。
- **逐关**：最大涨幅 `ellin` 0.2→0.8、`chefmao` 0.2→0.7、`irontonguetian` 0.1→0.5、`liusu` 0.1→0.4；
  下降只有 `marjorie` 0.7→0.5、`swan` 1.0→0.9。`blanche`/`timaeus`/`uncletian`/`wagner` 五组全 0。
- **库**：`data/skills/dyn_v1` = v2，42 条（17 rule + 25 strategy），父库 `static_v1`，
  `source_rows=data/runs/t5b_static.jsonl`，本轮新增 15 条全部 `[strategy]`、按关卡命名。
- **成本**：三个 LLM 组 $1.0890；提取一次 14 次调用 $0.0105。

> **泄漏嫌疑（未解决，勿删）**：`dyn_v1` 的 15 条候选技能是从 `data/runs/t5b_static.jsonl` 提取的，
> 而这个 run 用的就是 seeds 0–9 —— 与 dynamic 组随后评测的 10 个种子完全相同。
> `scripts/extract_skills.py` 的设计口径是「在 run A 上提取 → 在未见过的种子上做门禁」，
> 本链路没有留出种子：提取时模型已经看到这 160 局里哪些关卡失败、失败在哪类目标上。
> 因此 static→dynamic 的 **+0.1250（CI [+0.0075, +0.2425]，accept=True）在 seeds 10–19 的
> 留出门禁复现之前一律标为 leakage-suspect**，不能作为「技能库提分」的证据。

**下一步**：用冻结的 `dyn_v1` 在 **seeds 10–19** 上重跑 static 与 dynamic
（`--seeds 10,11,12,13,14,15,16,17,18,19`，约 $0.7），只有那里的 CI 下界 > 0 才能解除上面这条标注。
t5 的 effort=low 部分 run（§3.4）仍**不可引用**。

## 4. LLM 接入（2026-10-30 实测）

**思考档位分工（D10）**：对局决策 `reasoning_effort=none` + `max_tokens=64`（实测约 15 completion tokens/次）；
技能提取/反思 `effort=high` + `max_tokens=8192`（每轮只跑一次，值得用贵档）。
t5 的 `low` 档实测 1215 tokens/次、$0.04/局，成本 10× 且无质量证据，已暂时回退。

| 项 | 值 |
|---|---|
| 端点 | `https://opencode.ai/inference/go/openai/v1`（OpenAI 兼容） |
| 模型 | `deepseek-v4.1-flash`（1M 上下文；本仓库实测 2.6–4.9 s/次） |
| 认证 | `Authorization: Bearer $SKKEY` + **`x-opencode-session`（必填）** + 自定义 `User-Agent` |
| 对局思考 | `reasoning_effort="none"` + `max_tokens=64`（D10；开启思考时小 `max_tokens` 会返回空串） |
| 缓存 | 短 prompt（~170 tokens）实测 **0%** 命中；长前缀（>4k）才命中 |
| 成本 | 对局 `none` 实测 **$0.0038–0.0042/局**；`low` 实测 **$0.0374–0.0400/局**（约 10×）；见 §3.4 |
| 并发 | 128 并发实测全绿；256 起出现 429（有 `Retry-After`） |

`common/llm.py` 已改为：读 `SKKEY`、发 session/UA 头、空内容判失败、429 按 `Retry-After` 重试、
usage 记录 `cached_tokens` 与 `cost_usd`。探针：`python -m scripts.probe_llm`。

## 5. 已知风险

1. **任务集小**：16 关，留出泛化只有 6 关；靠「50 seeds/关 + 按关配对」提升功效。
2. **与上游共享机器**：本机 6C/12T，实验限 6–8 worker，`OMP_NUM_THREADS=1`。
3. **llm 成本口径**：缓存命中价差 50×，prompt 必须「稳定前缀 + 动态尾」。
4. **关卡数据**：`swan` 只有 `win` 一个目标（三星恒真）、`shitou` 的 `win_within_rounds(44)` 疑似错误，待修。
5. **LLM 成本随配置跳变**：对局思考 `low` 实测 $0.04/局（10×），并发还会放大烧钱速率；
   任何调模型的 run 必须走 `EVAL_PROTOCOL.md §6` 的门禁（见 `docs/failures/thinking-in-game-cost-blowup.md`）。
