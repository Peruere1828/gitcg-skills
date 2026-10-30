# reps — 观测/动作/信念表示（PLAN.md WS1 / M1，go/no-go 关卡）

> 接口/测试入口以本文件为准；当前状态见 [`docs/STATUS.md`](../docs/STATUS.md)，
> parity/数据格式见 [`docs/PLAYBOOK.md`](../docs/PLAYBOOK.md) §4。

observation_encoder（token 化，公私信息两段式）、action_hierarchy + action_taxonomy.toml（动作抽象/分层）、
action_adapter（抽象↔具体映射 + 合法化）、belief_groups（public belief）。
验收：抽象动作 100% 覆盖合法具体动作 + adapter 往返测试通过。

## 历史实现（2026-10-07；当前接口以代码为准）

逐模块移植自 `refs/Rebel_base_RL/.../gitcg_world_model`（AGPL-3.0，见 `NOTICE.md`）：

- `schema.py` / `snapshot.py`：`StateSnapshot` 等数据结构；从 `notification.state`（proto）或 `state.json()` 快照。
- `action_hierarchy.py` + `action_semantic_rules.py/.toml` + `non_action_semantic_rules.py/.toml` + `action_taxonomy.toml`：
  动作分层与 `HierarchicalActionCodebook` / `ActionLegalityEngine`（低层动作 code ↔ 高层抽象类别）。
- `action_adapter.py`：把具体请求（action/choose_active/reroll/select_card/switch_hands）枚举为候选
  `LowLevelActionSpec` + 响应载荷（`BuiltDecisionContext`）。
- `public_state.py`：`mask_state_for_player`（对局信息隐藏）+ public belief 起点。
- `semantic_priors.py` / `agents.py` / `replay.py`：先验、legal-random/heuristic 基线、回放序列化。
- `observation_encoder.py` + `belief_groups.py/.toml` + `features.py` + `event_features.py` + `decks.py` + `opponent_identity.py`：
  token 化观测（公共信息 token + `opponent_token_mask`）、动作 option 特征、belief target（由 `full_state` 计算）、
  privileged state。工厂：`envs/observation.py:default_encoder()`（词表覆盖 20 套脚本卡组，152 卡 / 36 角色）。
- 信息泄漏硬门禁：`tests/test_info_leak.py`（只改对手隐藏信息的两个 context，编码**输入**必须一致；改公开信息必须变）。

待办：抽象动作覆盖上限加固（当前观测到单步最大合法动作数远小于护栏 512）、belief_groups 在搜索中的使用。
