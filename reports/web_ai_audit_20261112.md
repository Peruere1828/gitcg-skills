# Web AI 对局审计（2026-11-12 05:30 UTC）

审计对象：`data/web/20261112T053047Z_mwd40iqu-xr44x7ry`
- 人类座位 0：NPC 阵容「布兰琪」`[3302 火斧丘丘暴徒, 3302, 3101 冰盾丘丘暴徒]`，策略 `heuristic`
- AI 座位 1：时代牌组 `keqing_shuanghuo` `[2301 愚人众·火之债务处理人, 1303 班尼特, 1403 刻晴]`
- AI 策略 `llm:dynamic[dyn_v4]`（40 条，built_at 2026-11-02T11:44:13Z），model `deepseek-v4.1-flash`
- 结果：6 回合，人类胜；AI 37 次决策 = 27 次 `action` + 6 次 `rerollDice` + 3 次 `chooseActive` + 1 次 `switchHands`
- 审计时点快照：工作树 HEAD `c82e775`（2026-11-12 03:49）。`web/llm_render.py` 的修改时间 **05:48:48**，晚于本局结束（05:34:55），因此**本局运行时并不存在**该文件里的修复。

复现脚本（临时，未入库）：`/tmp/audit_states.py`（状态时间线）、`/tmp/audit_actions.py`（逐动作差分）、`/tmp/audit_report.py`（27 步动作表与血量/反应汇总）。核心 deref：

```python
def deref(x, depth=0):
    if isinstance(x, dict):
        if "$" in x: return deref(store[x["$"]], depth + 1)
        return {k: deref(v, depth + 1) for k, v in x.items()}
    if isinstance(x, list): return [deref(v, depth + 1) for v in x]
    return x
```

## 1. 血量结论

**结论：2301 的最大血量始终是 13；11 是第 2 回合开始后被打掉的「当前血量」。** 唯一真的把 11 当最大血量的数据源是**官方静态资产镜像**；它没有进入引擎，也不影响本局棋盘。

证据：

1. `game_log.json` 全部 81 个 state 快照中，`players[1].characters[0].variables.maxHealth` 的取值集合 = `{13}`。`health` 时间线：
   - 快照 0–21（R0–R2 开始前）：`13/13`；R1 全程未受伤。
   - 快照 22（R2 T0）：人类 3302 首次攻击 2301（人类消耗 3 骰，2301 `13 -> 11`，同时「潜行」被消耗移除）→ `11/13`。
   - 快照 34（R3 T0）`8/13`；快照 37 `5/13`；快照 50 `2/13`；快照 54 `0/13` 被击倒并强制换上班尼特。
2. 引擎数据：`engine-overlay/overlay/packages/data/src/characters/pyro/fatui_pyro_agent.gts:135` `@hp 13`、`:144` `health 13;`。
3. 补丁审计：`configs/npc/hp_table.json:142-147` 记录 `health_before: 11, health_after: 13, hp_card_before: 13`。注意 `hp_card_before` 来自 `configs/npc/npc_enemies_all.json` 的旧 `hp_card`（已是 13），**不是**官方镜像。
4. Web CustomData：`web/npc_assets.py:179,195` 从 `.gts` 解析 `health`；实测 `npc_custom_data()` 中 `2301 hp=13`，`character_ids` 包含 2301。
5. 官方静态镜像：`data/assets/expert_system_assets_chs_latest.json:13576` 中 `2301` 的 `"hp": 11`（v4 all 缓存；线上 `/api/v5/datum/v7.1.0/CHS/2301` 同源，本机无法联网复核，见「未验证项」）。
6. 前端取值路径：
   - 对战棋盘：`build/engine/packages/web-ui-core/src/components/CharacterArea.tsx:497` 只渲染 `data().health`（当前血），满血时才换底色，**不显示 max** → R2 之后棋盘上就该显示 11。
   - 角色详情：`build/engine/packages/card-data-viewer/src/Entity.tsx:84-89`，有 state 时显示 `${health}/${maxHealth}`（11/13），无 state 时显示静态 `data.hp`。
   - 对战页把 `/npc-decks` 的 `assets` 传给 `AssetsManager`：`build/engine/packages/standalone/src/App.tsx:1050` → `StandaloneParent.tsx:148-155` → `build/engine/packages/assets-manager/src/manager.ts:415` 先查 `dataCacheSync`，因此对战内 2301 走 CustomData = 13。
   - 组牌器没有传 `assetsManager`：`App.tsx:1074` 的 `<DeckBuilder>` 未传，`deck-builder/src/DeckBuilder.tsx:72-74` 回退 `DEFAULT_ASSETS_MANAGER` → 走官方镜像 = 11。

因此：
- 若用户是在**棋盘/对局详情**看到 11，那是 R2 T0 人类首次攻击后的当前血量（13 上限不变）。
- 若用户是在**组牌器/静态角色牌详情**看到 11，那是官方镜像 `hp: 11`，属于展示层数据陈旧；`hp_table` 的 `hp_card_before: 13` 与镜像不一致，审计表本身也误导。

## 2. AI 行为统计

判定规则（写入脚本）：AI 是座位 1；对相邻 state 做差分，**AI 骰子减少/手牌减少/能量或附着变化**的窗口归为 AI 动作；人类骰子减少的窗口归为人类动作，仅用于排除。普通攻击/战技/爆发的区分依据：
- 骰费与伤害：2301 普攻 `1 火 + 2 杂`、2 物理（潜行 +1）；2301 战技 `3 火`、1 火伤 + 潜行；2301 爆发 `3 火 + 2 能量`、5 火伤。
- 刻晴普攻 `1 雷 + 2 杂`、2 物理；战技 `3 雷`、3 雷伤（本局未用）。
- 班尼特普攻 `1 火 + 2 杂`、2 物理；战技 `3 火`、3 火伤（本局用了 1 次）。
- 能量 `+1` 几乎只来自普攻（以及 `星天之兆` 等牌），可交叉验证。

### 2.1 类型占比（27 次 action）

| 类型 | 次数 | 占比 | 步骤 |
|---|---:|---:|---|
| 普通攻击 | 9 | 33.3% | 3,4,10,11,20,22,25,35,36 |
| 元素战技 | 1 | 3.7% | 31（班尼特） |
| 元素爆发 | 1 | 3.7% | 18（2301，消耗 2 能量） |
| 打出行动牌 | 10 | 37.0% | 5,6,13,14,15,19,21,27,28,32 |
| 元素调和 | 1 | 3.7% | 12 |
| 宣布结束回合 | 5 | 18.5% | 7,16,23,29,33 |

战斗技能（战技+爆发）合计 2/27 = **7.4%**；非战斗动作 16/27 = 59.3%。

打出的牌：田铁嘴、交给我吧、运筹帷幄、群玉阁、换班时间、元素共鸣·交织之火、最好的伙伴、星天之兆、莲花酥、蒙德土豆饼。

### 2.2 伤害、反应、换人

- **造成人类方有效伤害合计 19 点**（人类总血 30 → 终局 11）。原始技能伤害 27 点：其中 4 点是对 1 血角色的溢出（R3 2301 爆发 5 点打死 1 血 3302），另外 R6 两次普攻各 2 点被冰盾 `131011` 全额吸收为 0。
- **AI 触发的元素反应次数 = 0**。人类出战角色在 AI 动作窗口内只在 step 31 发生一次附着变化：3302 `aura 0 -> 3`（火），这是因为此前无附着，只附着未反应。R3 的 2301 火伤打在无附着目标上；R6 班尼特普攻是物理，打在带冰附着的 3101 上也不会反应（若用火战技则会融化）。
- **AI 主动切人 = 0 次**。3 次 `chooseActive` 中：step 1 开局选刻晴；step 9 是刻晴阵亡后强制选 2301；step 26 是 2301 阵亡后强制选班尼特。没有任何一次为了反应或规避而主动换人。
- 9 次普通攻击的 `chosenActionIndex` 全部是引擎索引 0（注意：这是引擎合法动作下标，不是模型候选列表下标，见「未验证项」），说明普攻通常排在合法动作第一位，存在顺序偏置的可能性。

### 2.3 典型 step

**step 3（R1，刻晴，明确「有战技却普攻」）**
- 决策：`chosenActionIndex=0, usedDice=[8,1,2]`，引用 `tempo_budget_for_round_cap_shitou`。
- 动作前骰子 `[1,2,3,5,7,8,8,8]`，其中 3 个万能骰；刻晴战技「星斗归位」需 3 雷，**完全可付**。
- 实际效果（快照 #6→#7）：骰子变 `[3,5,7,8,8]`，刻晴能量 `0->1`，人类 3302 `10->8`（2 物理）。即选择了 3 费普攻（1 雷+2 杂），而非 3 雷的 3 伤战技。
- 同回合 step 4 骰子只剩 `[3,5,7,8,8]`（无雷/万能不足），战技不可付，普攻可解释。

**step 31（R5，班尼特，唯一正确战技）**
- `usedDice=[3,3,8]`；快照 #61→#62 骰子 `[1,2,3,3,4,5,7,8] -> [1,2,4,5,7]`，人类 3302 `4->1`，`aura 0->3`。对应「热情过载」3 火伤 + 火附着。这是本局唯一一次元素战技，也是唯一一次给人类附着。

**step 35 / 36（R6，班尼特，两发普攻打进冰盾）**
- 人类出战 3101 带 `131011 寒冰大盾`（受到伤害 -2，usage 2，aura=1 冰）。
- step 35 `[8,2,4]`、step 36 `[8,7,5]`，快照 #74→#75、#78→#79：骰子各 -3，3101 `health 10 -> 10`、`aura 1 -> 1`、盾 usage 仍为 2，**实际伤害 0**。
- 班尼特战技是火伤，打冰附着会触发融化；即使被盾 -2，也应有正收益。两回合内 AI 没有用元素调和/交织之火去凑 3 火，也没有换人，只是重复普攻。

## 3. 提示词 / 检索根因

### 3.1 已确认的系统提示（可复现）

本局 37 条决策的 `system_hash` 全部为 `093df81df140`，说明整局只构建了一次系统消息。用当时仓库代码（`skillloop.render.StateRenderer` + 未过滤的 `dyn_v4`）可以**逐字节复现**该 hash：

```python
from skillloop.library import SkillLibrary
from skillloop.render import StateRenderer, TaskSpec
lib = SkillLibrary.load('data/skills/dyn_v4')
task = TaskSpec(stage='web', display='网页对局', objectives=('赢得胜利',),
                deck='网页牌组', objective_types=('win',))
ctx = {'stage': 'web', 'objective_types': ('win',), 'round': 0}
skills = lib.retrieve(task.describe(), ctx, k=5)
sysmsg = StateRenderer(None).system_message(task=task, skills=skills,
                                             rules_note=lib.rules_text())
# sha1(sysmsg['content'])[:12] == '093df81df140'
```

检索结果（整局固定）：`strat_protect_low_hp, strat_mulligan, skill_timmie, frontload_reaction_into_kill_line_shitou, tempo_budget_for_round_cap_shitou`。模型引用统计：`strat_mulligan 8`、`strat_protect_low_hp 8`、`tempo_budget... 7`、`skill_timmie 1`、`frontload... 0`。

### 3.2 为什么 PvE 关卡技能会进入网页对局

- `web/ai_rpc_server.py` 当时构造 `TaskSpec(stage="web", objectives=("赢得胜利",), objective_types=("win",))`，**补丁前**直接 `load_llm_library(self.selection)`，没有网页过滤。
- `skillloop/library.py:53-75` 的 `match_context`：`objective_types` 只要求**有交集**。`objective_types=("win",)` 因此让所有 `win` / `win_within_rounds` 技能都「适用」。
- 检索发生在**第一决策（回合 0）**，所以 `round_max: 5` 也通过；且系统消息整局不重建，后续回合不会重新过滤。
- `skill_timmie`（`data/skills/dyn_v4/library.json:379-388`）条件为 `objective_types=[win_within_rounds, win], round_max=5`；`tempo_budget_for_round_cap_shitou`（`:588-597`）同构；`frontload_reaction_into_kill_line_shitou`（`:31-41`）同样命中。它们描述的是「五回合窗口 / 起手铺垫」的 PvE 关卡节奏，对普通 PvP 是错误框架。
- `strat_mulligan`（`:532-535`）条件为 `{}`（全局适用），描述却只讲起手换牌；它在整局 top-5 中占一席，被引用 8 次，甚至在 R2/R3 的行动阶段仍被引用。
- `library.rules_text()`（`skillloop/library.py:137-141`）**无条件注入所有 status=active 的 rule 层技能**，包括 `规则_目标_*`（`规则_目标_回合上限`、`规则_目标_元素反应` 等）。这些目标块同样按 PvE 目标写成，和网页对局无关。

### 3.3 候选行与状态行信息不足

- `skillloop/render.py:290-326` 的 `describe_candidate`：技能只渲染「使用技能：<角色>·<技能名>」，**没有技能类型（普攻/战技/爆发）、没有伤害/元素/附着/反应信息**；行动牌也只有名字。模型无法单看「云来剑法」与「星斗归位」判断哪个是 2 物理、哪个是 3 雷。
- `skillloop/render.py:252` 状态行把附着渲染为**裸整数**（如 `刻晴 7/10 能量 1/3 3`），没有映射成「火/雷/冰」；模型即使想反应也无法从 `3` 推断是火。
- 系统提示（`:330-338`）包含 `SYSTEM_ROLE = 单人 PvE 关卡 / 三星目标`，加上 5 条检索技能，但**没有任何网页对局战术**：没有「优先元素战技」「看附着打反应」「为反应切人」的显式指令。`rule_reaction` 只泛泛说「附着后再用另一种元素会触发反应」，`rule_switch` 只说切人花 1 骰。
- 候选上限 `candidate_cap=16`（`skillloop/agent.py:52`），切人/调和候选可能被挤出；本局无法验证（见未验证项）。

### 3.4 根因排序

1. **候选行缺少类型/数值/元素/附着信息**（影响最大，直接导致「不知道战技比普攻强」）。
2. **状态行附着是裸整数 + 无反应提示**（无法做反应规划）。
3. **伪造 `objective_types=("win",)` + 回合 0 检索**，把 PvE 五回合技能注入普通对局；系统角色还自称 PvE。
4. **无网页战术指令**（没有「优先战技/反应/切人」）。
5. **候选 16 条上限 + 去重**可能隐藏切人/调和（未验证）。

## 4. 修复建议（按优先级，尽量限定在 web/）

**P0：先把已存在但未生效的网页层修复落地**
- `web/llm_render.py` 当前是未跟踪文件，且 `web/ai_rpc_server.py` 的改动未提交；它们是在本局之后写的（05:48/05:49）。需要补测试、提交后再用一局验证。
- `WebStateRenderer` 目前只覆盖 `describe_candidate`，**没有覆盖 `describe_state`** → 附着仍是裸整数。应在 web 层覆盖状态行，输出 `附着:火` / `附着:冰` / `无附着`。
- `filter_library_for_web` 仍保留全部 `layer=="rule"`，包括 `规则_目标_*`；也仍保留 `strat_mulligan`（实测过滤后 top-5 第一）。建议：`is_web_skill` 对 `规则_目标_*` 返回 False；`strat_mulligan` 仅在 `switch_hands` 请求时用，或从网页库中剔除（网页系统消息只建一次，无法按请求切换）。

**P1：候选行给出可决策信息（web 层）**
- 技能行加：类型（普通攻击/元素战技/元素爆发）、基础伤害、伤害元素、是否附着、能量消耗。
- 行动牌行加：效果摘要（现有 `WebStateRenderer` 已做）。
- 反应预览：用对方出战角色的 aura + 候选技能元素，计算「将触发融化/超载…（+X 伤）」；对护盾/减伤状态给出提示（本局 R6 的两发 0 伤普攻就会被标出）。
- 切人行标注「切换后出战元素 = X；对当前目标可触发 Y」。

**P2：检索上下文不再伪造关卡目标（web 层）**
- 不要把 `objective_types` 设成 `("win",)`；改为空或网页专属类型，避免 `match_context` 命中 `win/win_within_rounds`。
- 或直接给网页固定 loadout：通用 `rule_*` + `strat_reaction` / `strat_focus_fire` / `strat_switch_cost` / `strat_burst_timing`，排除全部 `skill_*`、`tempo_*`、`frontload_*`、`strat_mulligan`。
- 系统角色文案改为「双方对局」；删除「三星目标」框架。

**P3：候选覆盖与顺序**
- 提高 `candidate_cap`，或给切人/元素调和保留固定槽位；避免普攻永远占据索引 0。

**P4：可审计性（web 层）**
- `decisions.jsonl` 目前没有 `n_candidates`、候选行、模型选中的候选下标、动作预期效果。应至少记录：候选数量、渲染后的候选文本（或 hash）、模型 `choice` 下标（而非仅引擎 `chosenActionIndex`）、动作后的血量/附着差分。否则后续只能像本报告一样反推。

**P5：血量展示一致性（web / 数据）**
- 组牌器的 `AssetsManager` 也带上 `customData`/`overrideData`（例如 `{id: 2301, hp: 13}`），或在生成资产缓存时以引擎 `.gts` 为准刷新；同时修正 `hp_table.json` 中 `hp_card_before: 13` 与官方镜像 `11` 的不一致说明。

## 5. 验证命令与结果

```bash
export PY=/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python
cd /home/ubuntuaaa/projects/gitcg-skills

$PY /tmp/audit_report.py
# 27 步动作表；kind counts = card 10 / normal 9 / end 5 / tuning 1 / burst 1 / elemental 1
# cumulative P0 hp lost by AI actions: 19
# 唯一附着变化: step 31 human char[1] aura 0->3
```

```bash
$PY - <<'PY'
import json
d=json.load(open('data/web/20261112T053047Z_mwd40iqu-xr44x7ry/game_log.json'))
store,log=d['store'],d['log']
def deref(x,n=0):
    if n>80: return None
    if isinstance(x,dict):
        if '$' in x: return deref(store[x['$']],n+1)
        return {k:deref(v,n+1) for k,v in x.items()}
    if isinstance(x,list): return [deref(v,n+1) for v in x]
    return x
vals=[deref(e['s'])['players'][1]['characters'][0]['variables'] for e in log]
print(set(v['maxHealth'] for v in vals))   # {13}
PY
```

```bash
$PY - <<'PY'
from web import npc_assets
print([(c['id'],c['hp']) for c in npc_assets.npc_custom_data()['characters'] if c['id']==2301])
# [(2301, 13)]
PY
```

```bash
# 官方镜像
$PY -c "import json;d=json.load(open('data/assets/expert_system_assets_chs_latest.json'))['data'];print([x['hp'] for x in d if isinstance(x,dict) and x.get('id')==2301])"
# [11]
```

系统提示 hash 复现：见 §3.1，结果 `093df81df140`，与 37 条决策的 `system_hash` 全部一致。

## 6. 未验证项 / 风险

- **未保存实际 prompt 与候选列表**：`decisions.jsonl` 只有 `chosenActionIndex`（引擎合法动作下标）和 `usedDice`，没有模型看到的候选行，也没有模型候选下标。动作类型是从骰子消耗、伤害、能量、附着、手牌变化反推的，存在少量歧义；`chosenActionIndex=0` 只能说明是引擎动作 0，不能直接证明模型选了渲染候选 0。
- `game_log.json` 的 `phaseDamageLog` 被序列化为 `[null]`，无法直接读伤害事件；反应次数是从 aura 差分推断，本局 AI 侧为 0。
- 线上 `/api/v5/datum/v7.1.0/CHS/2301` 无法联网核对，11 的依据来自本地同源资产缓存 `data/assets/expert_system_assets_chs_latest.json:13576`。
- `web/llm_render.py` 与 `web/ai_rpc_server.py` 的修复是在本局之后写入的，未用新对局验证；本次按约束未调用真实 LLM，也未提交。
- R6 两次普攻的 0 伤害是按 3101 的盾（-2）与普攻 2 物理推断；盾的 `usage` 未变化，说明日志没有记录被完全吸收的伤害事件，数值结论依赖规则而非事件日志。

## 6. 修复后验证（2026-11-12 06:10 UTC 实局）

修复内容见 `web/llm_render.py`（网页渲染/战术/库过滤）与 `web/ai_rpc_server.py`
（决策日志字段）。用同一套对局（人类座位布兰琪 NPC 阵容、AI 座位
`keqing_shuanghuo`）跑了一局真实 LLM 对局，session：
`data/web/20261112T061057Z_e8efd18a-115e-4486-a400-`。

- 结果：9 回合，AI（座位 0，`llm:dynamic[dyn_v4]`）胜；46 次 LLM 决策，**0 fallback**。
- 技能使用（从 `game_log.json` 的 `roundSkillLog` 精确统计）：
  - 元素战技 **5**：班尼特「热情过载」×4、火债处理人「伺机而动」×1
  - 普通攻击 **8**：刻晴「云来剑法」×6、班尼特「好运剑」×1、火债「突刺」×1
  - 元素爆发 **1**：火债「焚毁之锋」×1
  - 对比修复前：元素战技 1、普通攻击 9、爆发 1。
- 引用技能：全部是 `strat_*`（`strat_dice_economy` 11、`strat_end_round` 8、
  `strat_card_draw` 2、`strat_equipment_timing` 2、`strat_protect_low_hp` 2）；
  `skill_timmie` / `tempo_budget_*` / `frontload_*` / `strat_mulligan` 不再出现。
- 决策日志新增 `n_candidates` / `model_choice` 可直接核对（例：`model_choice=14,
  n_candidates=16`）。
- 刻晴 6 次普通攻击的骰子复查：R3 为 `[4,4,6,6,1,7]`（1 雷、无万能），R4/R5 为
  `[3,4,5,5,7]`（1 雷、无万能）；元素战技需 3 雷，**当时不可支付**。因此这部分普攻
  是骰子约束，不是模型无视战技。
- 反应：AI 给人类出战角色附着火 2 次；但后续没有用不同元素命中同一目标，AI 侧仍
  未触发反应。界面上的「融化」是人类（3101 冰盾）打 AI 的附着火角色，不是 AI 触发。
  AI 仍 0 次主动切人；本局没有明显可负担的「切人→反应」线路（刻晴始终缺雷骰）。

结论：新的候选描述/反应提示/网页战术/库过滤已把元素战技使用从 1 次提升到 5 次，
并消除了 PvE 关卡特化技能污染；剩余普攻主要是骰子约束。要继续验证「切人打反应」，
需要更多局或一套能稳定凑同色骰的牌组，单局不足以断言。

## 7. 血量显示补充（修复后）

- 对局内：棋盘只渲染**当前血量**（上游 `web-ui-core` 的 `Health` 组件），所以 R2 被
  打 2 点后显示 11；角色详情显示 `11/13`，引擎 `maxHealth` 恒为 13。
- 组牌器与配置页牌组预览现在传入带 `customData` 的 `AssetsManager`（`App.tsx` 的
  `npcAssetsManager`），静态角色详情与引擎 `.gts` 对齐（2301 `hp=13`），不再回退到
  官方镜像的 11。
- 若仍在某个界面看到「11」被当作上限，请指出具体界面（组牌器 / 摆盘器 / 对局详情 /
  棋盘），因为引擎与 CustomData 两处证据都表明上限是 13。
