# tests — 关键测试（全部进 CI）

信息泄漏测试（两份只差对手隐藏信息的 state，编码必须一致）、action_adapter 往返、同种子重放一致性、非法动作回退。

> 当前测试数量以 `python -m pytest --collect-only -q` 为准（本文件不写死数字）。
> 当前状态/门禁见 [`docs/STATUS.md`](../docs/STATUS.md)；评测口径见 [`docs/EVAL_PROTOCOL.md`](../docs/EVAL_PROTOCOL.md)。

## 运行

`python -m pytest`（从仓库根跑）；`python -m pytest -m "not slow"` 跳过慢测。

## P0（2026-10-27）新增

- `test_feature_parity.py`：compact exact definition id、`repair_observation_payload`、`compare_observations`（definition 列按 int 比）、store builder 记录 `definition_ids_present`、`include_training_targets` 不改变策略输入。
- `test_eval_seed.py`：`--seed-start` 只加一次；`match_seed` 落盘。
- `test_neural_policy_seed.py`：`#t>0` 采样同 seed 可复现。
- `test_belief_dice.py`：hidden dice 是 i.i.d. 采样，不再是确定性轮转。
- `test_cfr_probe_value.py`：identity 价值头→概率；acting-player→player-0。

## 主要测试（历史清单，持续更新）

- `test_info_hiding.py`：`public_state.mask_state_for_player` 隐藏对手手牌/骰子/牌堆、保留己方与公开信息、幂等。
- `test_info_leak.py`（slow，**WS1 硬门禁**）：只改对手隐藏信息的两份 context，编码器**输入**（token/option/mask/opponent id）
  必须逐字段一致；改公开信息（对手角色血量）必须可见；belief target 必须随 `full_state` 变化（标签由上帝视角算）。
- `test_action_adapter.py`：每个合法具体动作都有 code + kind-appropriate 载荷；动作空间上限护栏；抽象动作 100% 覆盖合法具体动作（往返语义 key 可恢复）；无非法回退。
- `test_replay_consistency.py`（slow）：同种子两次对局结果一致。
- `test_arena.py`（slow）：arena 顺序与多进程结果一致（验证 codebook reset + 预洗牌确定性）。
- `test_env_smoke.py`：脚本对手对局跑通、无 IO 错误 / 无回退。
- `test_stats.py` / `test_ladder.py`：Wilson CI、Elo。
- `test_llm.py`：LLM 客户端 JSON 解析/脱敏、LLM 辅助策略「非法响应回退 + 预算 + prompt 不泄密」、教练结构化输出（mock 客户端，无网络）。
- `test_decklab.py`：换卡保持 30 张、候选池排除、换卡评测小样本（slow）。
- `test_engine_bridge.py`：L5.4/D12——快照往返无损、续跑克隆确定、`InjectionPolicy` 位置/夹取逻辑（快测）；
  replay-branch 注入 base 选择时逐局复现 base、同注入两次一致、`evaluate_decision` 候选覆盖（slow）。
- `test_llm_matrix.py`（L5.2）：LLM 矩阵任务网格确定性、纯策略/LLM 行结构、干预与用量记录（mock 客户端，无网络）、
  客户端失败标 `degraded` + 连续失败中止、聚合报分/delta/`llm_quality`、流式续跑跳过已完成 + spec 指纹护栏 + 撕裂行容错。
- `test_learning_curve.py`（L5.3）：曲线数据量过滤/去重/排序、固定验证留出集（各点同 holdout）、微型端到端扫描、`--eval-opponents` 缺 `--eval-seeds` 报错。
- `test_fork_bridge.py`（L5.4）：fork 桥单元（位置重放 / 注入 / 前缀-rollout 策略、`ForkTask` JSON 往返、候选选取、搜索策略选优/平局取基座/预算开关）；慢测——纯重放 fork 进程内与**子进程桥**均逐字节复现活体终局、`run_search_match` 冒烟、在线搜索拒绝 `workers==0`。
- `test_belief_root.py`（M3-CPU S-A）：公共信念根化——`reps.belief` 补丁只改写隐藏区（合成隐藏 id、内联骰子）、
  belief 范围只依公共视图、确定化根对隐藏真值不变（可 fork 打完整局 + belief-root 搜索冒烟，slow）。
- `test_cfr_solver.py`（M3-CPU S-B）：外采 MCCFR 脚手架——regret matching/先验回退与不删动作、分布归一、
  bandit 收敛最优臂、深度截断走 frontier 且同输入确定复现、玩具 2×2 矩阵博弈收敛到已知混合 Nash（值 6/5）、
  `belief_root_particles`/`public_infoset_key` 对隐藏真值不变（engine-free）。
- `test_belief_head.py`（M3-CPU S-C，G7）：belief 头形状（`belief_groups` 边缘 + 对手手牌卡后验 + 标量）、
  proper-scoring belief 损失在优化下降、全退化标签批次损失为 0、CVPN/Transformer 的 `forward_all` 形状与
  `forward` 二元组兼容、belief 头 checkpoint 往返、旧 config（无 belief 字段）后向兼容（engine-free）。
- `test_distill.py`（M3-CPU S-C，G6）：teacher 根策略按语义 key 重索引/归一/不删动作、样本 JSON 往返
  （含 `policy_target`/`action_values`/`search_value`/`search_meta`）与旧格式缺字段容错、蒸馏损失数值 sanity
  （soft 单热 == hard CE、value 目标 soft/hard 选择、总损失 == 系数加权分量和）、蒸馏训练损失/一致率/belief 损失下降、
  冷启动 BC 退化、无 belief 头时 belief 损失为 0；慢测——`train.pipeline.run_distill_round` 端到端（真引擎评测）。
- `test_collect.py`（L5.6 + S-C）：集群数据平面——观测/样本 JSON 往返无损、`CollectSpec` 任务网格与指纹稳定、
  撕裂行容错、replay 目录展平；S-C 搜索 teacher 的 `SearchSpec` 往返 + 进 spec 指纹；慢测——并行采集 + 续跑不重复、
  `load_samples` 计数一致、`collect_search_samples` 产出带 solver 目标（根策略归一、根价值、belief 标签）的样本。
- `test_deck_corpus.py`（探索任务 1）：公开卡组语料——每条分享码经引擎编解码解出 3 角色 + 30 牌、
  `DeckSpec` 唯一且形状正确、loader 元数据/查找、分享码与存储 id 一致（需资产缓存，否则 skip）；
  语料 JSON 缺失（gitignore）时整个文件 skip，无网络。
- `test_npc_spec.py`（PvE-B3）：16 关 `NpcSpec` 加载/校验、中文名→引擎角色 id 映射经 `appears_in` 交叉校验、
  A/E/Q 技能 definition id + 伤害表与 `.gts` 源逐一对照、关卡阵容按 count 展开、未知目标类型报错。
- `test_npc_policy.py`（PvE-B2）：`NpcPolicy` greedy 取最大伤害 / 平局固定顺序 / 绝不打出行动牌 / 绝不主动切人 /
  reroll 保持骰子 / choose_active 取首槽 / rotation 优先其循环并在缺项时回退；slow——真实对局同种子（含 rotation）逐决策复现。
- `test_npc_objectives.py`（PvE-B3）：7 类挑战目标合成轨迹判定（win / 回合数 / 被击倒数（终局快照）/
  元素爆发 / 元素反应 / 装备牌 / 事件牌）+ 三星计数。
- `test_npc_eval.py`（PvE-B3）：聚合（总览/逐关/逐目标）、spec 指纹稳定与敏感、流式续跑指纹护栏 + 撕裂行容错、
  任务网格；slow——`run_npc_eval` 端到端 + 重复运行续跑不重复 + 产物落盘。
- `test_npc_contestant.py`（PvE-v1，B5）：门禁 contestant 策略——合成 profile 无 deck 专属规则、
  目标注入（装备/事件牌）与不可用时的回退、`inject_objectives=False` 不注入；slow——同种子逐决策复现。
- `test_decklab_builder.py`（PvE-v1，B5，D19）：规则组牌——池 C 牌组合法（30 张 / ≤2 张 / 全在池内 /
  角色在池内）、确定性、武器+圣遗物齐备（目标可达）、越界报错、kind 统计。

依赖：需要资产缓存（`data/assets/`）；首次运行会联网拉取，CI 可预置缓存并设 `GITCG_ASSETS_OFFLINE=1`。
