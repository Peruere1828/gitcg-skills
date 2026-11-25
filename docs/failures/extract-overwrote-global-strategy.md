# 003 — 抽取候选覆盖全局 strategy：单关文本污染所有关卡

- status: invalid（2026-11-25 已修复；被污染的 dyn_v4 v3 已废弃，动态根重置为 round 0）
- 日期: 2026-11-25（发现、回滚、修复并补回归测试）
- 证据:
  - `data/skills/dyn_v4/library.json` 的 `meta.history` 记录了被废弃的 v3：
    `"discarded: extraction had rewritten global strategies with single-stage text"`
    （`at: 2026-11-25T11:48:23Z`）；同一文件的 `meta.reason` 记录了
    `strat_equipment_timing` 的原文错误与人工修复。
  - 被覆盖前的抽取输入：`data/runs/v10_static_s1_5.jsonl`，16 关 × seeds 1–5 = 80 局，
    `static_v1` 库。候选来自 `blanche` / `irontonguetian` / `liusu` 三关；
    `liusu` 的 5 局里 `win_within_rounds` 失败（其余目标通过）。
  - 覆盖文本点名了 `liusu` 的阵容与血量：`configs/npc/npc_stages_mond_liyue.json`
    的 `liusu` 是 13 HP `fatuipyroagent`（火之债务处理人）+ 12 HP
    `fatuiskirmisheranemoboxervanguard`（风拳前锋军），目标为
    `win` / `win_within_rounds`(n=5) / `use_elemental_burst`。
- 口径: 16 关 × seeds 1–5 = 80 局；`static_v1` v2；`v10_static_s1_5.jsonl`；2026-11-25。
  被污染的 v3 原文在回滚时未保留在磁盘上，以下描述按抽取事件记录引用。
- 回滚后 `data/skills/dyn_v4/library.json` 的 `strat_burst_timing` 现文本为
  「元素爆发伤害最高但需要能量与骰子：能量满后先确认对方角色血量是否进入斩杀线，
  不是斩杀窗口时优先用普通技能创造窗口。」——`blanche`/`irontonguetian`/`liusu`
  的单关文本已不在动态根中；`meta.history` 的 v3 条目保留了废弃原因。

## 现象

- 模型提交了 3 个针对具体关卡的候选（`blanche` / `irontonguetian` / `liusu`），全部声明
  `"layer": "strategy"`、`"replaces": "strat_burst_timing"`。其中 `liusu` 的描述大意是
  「本关要在 5 回合内击杀 13 血火之债务处理人与 12 血风拳前锋军……」，是单关打法，不是跨关规则。
- 回滚前的工作区 `data/skills/dyn_v4/library.json` 里，全局策略 `strat_burst_timing`
  被改写成上述单关文本；`strat_equipment_timing` 同样被 `wagner` 的单关文本覆盖。
- 3 个候选都指向同一个 id，写入器按顺序原地 `update()`：后写覆盖前写，**前两个候选被静默丢弃**，
  `strat_burst_timing` 的 `version` 被连升到 4（3 次 update），但最终只剩最后一条文本。
- 两个被覆盖的 `strat_*` 都没有 `conditions.stages` pin。它们会被检索到**所有关卡**，
  于是 `liusu` 的「13 血火债 + 12 血风拳」和 `wagner` 的单关装备建议进入了每一关的决策提示。

## 为什么失败

- `scripts/extract_skills.py::replacement_target()` 只检查「目标存在 + 同 layer」，
  允许 `strategy → strategy` 原地改写。全局 strategy 是手工维护的跨关规则，
  抽取却只看到**一个关卡**的失败统计与回放，证据粒度不匹配。
- `skillloop/extract.py::parse_skills()` 信任模型声明的 `layer`；模型把单关归纳标成
  `strategy` 后，候选就以跨关规则的身份进入库。
- `strat_*` 规则没有 `conditions.stages`：检索端把它们视为全关卡通用，
  没有 pin 的「全局」文本一旦被污染，污染面是**整个关卡集**，而不只是抽取来源那一关。
- 写入器对同一 `replaces` id 的多次 update 没有冲突检测，也没有「后写覆盖前写」的告警，
  所以 3 条候选竞争同一 id 时，前两条消失得无声无息。

## 影响面

- `data/skills/dyn_v4` v3 中的所有关卡：`strat_burst_timing` / `strat_equipment_timing`
  的文本被换成单关建议，且注入无 stage 过滤。
- 该 v3 的派生产物（gate / 评测 / 报告）如果引用过它，结论不可用；v3 已按 lineage reset 废弃，
  `dyn_v4` 回到 round 0（= `static_v1` v2），`v10_static_s1_5.jsonl` 可作为重新抽取的输入。
- 没有被覆盖的规则层（`rule_*`）和人工维护的其他 `strat_*` 不受影响；
  但任何仍带 `layer=strategy` 的抽取候选都值得回查来源。

## 修复

- `skillloop/extract.py::parse_skills()`：候选声明 `layer == "strategy"` 时**降级为
  `stage_specific`** 并 pin `conditions.stages = (stage,)`，stderr 记录
  `layer strategy is human-maintained; downgrading to stage_specific pinned to this stage`。
  单关失败归纳出的建议不能进全局层。
- `scripts/extract_skills.py::replacement_target()`：**拒绝 `strategy → strategy` 原地改写**，
  即使目标与候选条件都为空；候选按新技能处理，stderr 说明全局策略由人工维护。
  同层同关的 `stage_specific` 改写逻辑保持不变。
- 回归测试 `tests/test_skill_extract.py`：
  - `layer=strategy` + `replaces=strat_x` → 解析后 `layer == "stage_specific"`、
    `conditions.stages` 含该关、`replacement_target(...) == ""`；
  - strategy→strategy 即使条件为空也不改写；
  - 原「strategy 候选保留 strategy / 可原地改写」的断言已按新语义更新。

## 教训

- **抽取证据的粒度决定技能层级的上限**：只看一关的失败，就只能写这一关的技能；
  `layer` 不能交给模型自报，必须由证据范围强制。
- **无 stage pin 的全局文本是高杠杆风险**：一次错误覆盖会改变所有关卡；
  全局层应由人工/门禁维护，不应由单轮抽取写入。
- **同一 `replaces` id 的多次写入需要显式冲突处理**：静默的「后写覆盖前写」
  会让前几条候选消失，也让版本号失真。
- 任何「模型说它通用」的字段都要在写入前用库结构（layer + stages）复核，而不是只信 JSON。

## 重开条件

- 只有出现**跨关**（多关同种子配对）证据、并经过库级门禁的候选，才允许进入全局 strategy 层；
  单关抽取结果永久按 `stage_specific` 处理。当前修复用测试锁住这条规则，无需重开。
