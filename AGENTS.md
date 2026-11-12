# AGENTS.md

> 权威地图：任务/验收 → [`PLAN.md`](./PLAN.md)；当前状态 → [`docs/STATUS.md`](./docs/STATUS.md)；
> 有效方法 → [`docs/PLAYBOOK.md`](./docs/PLAYBOOK.md)；负结果 → [`docs/failures/`](./docs/failures/)；
> 评测口径 → [`docs/EVAL_PROTOCOL.md`](./docs/EVAL_PROTOCOL.md)；来源与创新点 → [`docs/PROVENANCE.md`](./docs/PROVENANCE.md)。

课程项目：**面向大模型智能体的 Skill 自进化**（Py 3.12，卡牌 PvE 环境，LLM 逐决策选动作，不训练模型）。

## 0. 硬约束

1. **密钥不入库**：`.env` 已 gitignore；日志/prompt/报告里不得出现 key（只允许出现模型名与 base_url）。
2. **观测只取 `notification.state`**：不得用全量 `GameState` 造观测（信息泄漏测试是门禁）。
3. **可复现**：每局记录 `stage/seed/deck/group/library_version/model/prompt_hash`；
   LLM 用 `temperature=0`；同种子配对是统计推断的前提。
4. **无 torch**：本仓库不训练模型；`neural:` 策略分支已移除（显式报错）。
5. **上游目录不加业务逻辑**：`envs/ reps/ npc/ coach/ agents/ eval/ common/ web/` 是从上游快照来的环境层，
   我们的功能写进 `skillloop/`；必须改环境层时，在 `docs/PROVENANCE.md` §3 登记。
6. **技能入库要有证据**：由 `skillloop/gate.py` 的配对 A/B 决定，不接受「LLM 觉得好」。

## 0.1 分工（2026-11-01 起）

- 主 agent 只做协调：拆任务、派 subagent、验收、汇总汇报；不直接写实现代码、不做探索性排查。
- 所有具体编码 / 探索 / 调试任务一律派给 subagent；主 agent 负责读 diff、跑验收测试、提交。
- subagent 不得做联网 / LLM 调用，不得 `git commit`，除非主 agent 明确授权。
- 汇报格式固定：**变更文件 / 验证命令与结果 / 未验证项 / 风险**。

## 1. 先读什么

| 任务 | 必读 |
|---|---|
| 接需求/验收 | `PLAN.md` → `docs/STATUS.md` |
| 跑实验 | `docs/EVAL_PROTOCOL.md` → `docs/PLAYBOOK.md` |
| 查负结果 | `docs/failures/README.md` |
| 改引擎/关卡 | `docs/PROVENANCE.md` §3 + `engine-overlay/README.md` |
| 写报告 | `docs/课程要求.md` + `docs/PROVENANCE.md` |

## 2. 环境与常用命令

```bash
export PY=/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python   # 无 torch
export REPO=~/projects/gitcg-skills
cd "$REPO"

"$PY" -m pytest -q -m "not slow"                       # 快速门禁
"$PY" -m npc.eval --stages timmie --deck superconduct_aggro \
      --policy expert:superconduct_aggro --seeds 1,2 --workers 2 --tag smoke
"$PY" -m scripts.run_npc_gate --seeds 3 --workers 8    # PvE 门禁
```

- 长任务挂 `setsid` + 流式落盘；并发走子进程协议（`envs.rollout_worker`）。
- 本机算力：6C/12T、~30GB；实验限 6–8 worker，`OMP_NUM_THREADS=1`（与上游强度主线共享机器）。

## 3. 对局与复现

- `envs/match.py` 在 Python 侧按种子预洗牌并设 `NO_SHUFFLE=1`；每局开始 `reset_default_hierarchical_action_codebook()`。
- policy spec：脚本走 `envs.policy.build_policy`（`expert:<slug>` / `heuristic` / `legal_random` / `random`）；
  LLM 技能智能体走 web 桥的 `--policy llm:<none|llm_empty|static|dynamic>`（`dynamic` 取 `built_at` 最新的进化库）。
- 关卡/敌人数据权威在 `configs/npc/`；血量补丁用 `scripts/patch_npc_hp.py`（规则与审计见 `configs/npc/hp_table.json`）。
- `gitcg 0.21.0`：`ActionRequest.action` 是候选列表；`ActionResponse(chosen_action_index=..., used_dice=[...])`；
  `game.status()` 是方法；`State.query` 跨版本不兼容，用前小样本验证。

## 4. 数据与版本

- 引擎/卡池/血量版本写进每个 run 的元数据：`configs/engine.lock` + `configs/npc/hp_table.json`。
- 原始逐局 JSONL / prompt 落 `data/`（gitignore）；聚合摘要落 `reports/`（入库）。
- 报告必须带口径：stage/seeds/n/group/library_version/model/date。

## 5. 文档维护

- `STATUS` 只写现在；历史进 `reports/`；失败进 `docs/failures/`；成功方法进 `docs/PLAYBOOK.md`。
- 重大决策在 `PLAN.md §0` 追加 D 编号，不改写历史。
- 生成物（表格/报告）标注生成命令。

## 6. 索引

- 任务与验收：[`PLAN.md`](./PLAN.md)
- 当前状态：[`docs/STATUS.md`](./docs/STATUS.md)
- 有效方法：[`docs/PLAYBOOK.md`](./docs/PLAYBOOK.md)
- 负结果：[`docs/failures/README.md`](./docs/failures/README.md)
- 评测协议：[`docs/EVAL_PROTOCOL.md`](./docs/EVAL_PROTOCOL.md)
- 来源/创新点：[`docs/PROVENANCE.md`](./docs/PROVENANCE.md)
- 课程要求：[`docs/课程要求.md`](./docs/课程要求.md)
