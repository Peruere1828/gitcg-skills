# web/ — 浏览器 ↔ Python 桥（WS8/D18/C2 + T1/T2/T3/C4）

本目录放 **Web 人机调试界面** 的 Python 侧最小服务，配合
`engine-overlay/overlay/packages/standalone/` 的浏览器端改动使用。
**LLM 座位严格对齐 `skillloop.run` 的实际评测协议**（策略、提示词、技能库、基础
策略、候选顺序/上限、关卡 TaskSpec、牌组、卡池版本、特殊规则、NPC policy）。

## `ai_rpc_server.py`

纯标准库（`http.server`）实现，把引擎的 `PlayerIO` RPC 用 HTTP 暴露给浏览器：

| 端点 | 请求体 | 返回 |
|---|---|---|
| `POST /rpc` | `{"session","who":0\|1,"request":RpcRequest}` | `RpcResponse` |
| `POST /notify` | `{"session","who","seq","notification":{mutation,state}}` | `{"applied":bool,"seq","state_seq"}` |
| `POST /session` | `{"session","meta":{version,stage,ai_seat,human_seat,ai_self_play,decks},"resume?":bool}` | 会话记录 + `stage` + `deck_mismatch` + `protocol_mismatch` + `seat_specs` + `runs` + `seq_base`（`resume:true`＝续局，给 seq 基线） |
| `POST /choose-deck` | `{"session","deck":{characters,cards}}` | C1 反选结果（见下） |
| `POST /game-end` | `{"session","winner","rounds","aborted?":bool,"abort_reason?":str}` | `{"ok","closed","aborted"}`（`aborted:true`＝故障局，归档成 `io_error`、不记胜负） |
| `POST /game-log` | `{"session","log"}` | `{"ok","dir"}`（引擎日志归档） |
| `GET /health` | – | `{"ok":true,"policy","rpc","notify","sessions","stale_notify","ai_deck","log_dir"}` |
| `GET /state?session=&who=` | – | 该座位最近一次掩码 GameState（不带参数＝老行为，返回最近一条） |
| `GET /ai-deck` | – | `{"slug","characters","cards"}`（T3 牌组对齐） |
| `GET /ai-decisions?session=&who=0\|1&limit=20` | – | 只读调试：最近 N 条该座位的 `decision` 行（技能 id → 名称） |
| `GET /npc-decks` | – | C3：全部 NPC 关卡阵容（人数不固定）+ `eval`（评价协议）+ `assets`（CustomData，喂给 AssetsManager） |
| `GET /results` | – | `{"log_dir","sessions":[…]}` |

`session` 缺省落到 `"-"`（老客户端 / 直接 curl 调试）。

### 三个隔离维度（T1/T2）

- **座位**：引擎 `exposeState(who,…)` 按座位掩码（自己手牌/骰子可见、对手隐 id），
  所以每个 `(session, who)` 各存一份 `latest_state` 与策略实例——双方 AI 自对弈时
  不再互相覆盖（否则某座位会拿到对手视角的掩码态：自家手牌反被隐藏）。
- **会话**：一局一个 id（浏览器每局 `crypto.randomUUID()`），状态 / 策略 / 决策
  计数 / 日志目录都按 session 隔离，多开标签页不串局。策略 role = `web{who}`
  （非默认会话再叠 `@{sid}`），seed 从 `--seed` 派生 → 同一 session 内可复现。
  一个 session 上「从此处继续」开新局时 `runs` +1（同一个日志目录里接着记，
  落一条 `run_start`）。
- **通知序号**：`/notify` 带座位内单调 `seq`，过期通知直接丢弃（计数进
  `/health.stale_notify`）；浏览器侧把 notify 串成队列、发 rpc 前先等队列排空，
  杜绝「决策吃到上一步状态」的竞态。不带 `seq` 的请求照常应用（向后兼容）。
- **续局（「从此处继续」）**：同一个 session 上开新的一局，浏览器侧的计数从 1 重新
  数，所以 `POST /session` 要带 `resume:true`：桥把每个座位的 seq 顶到上一局末尾之后
  （`_RESUME_SEQ_GAP`）、清掉上一局的 `latest_state`，并把基线放进响应的 `seq_base`；
  浏览器的 io 从 `基线 + 1` 接着数（`AiHttpIoOptions.seqBase`）。不做这一步的话，新局
  的通知会全被判成过期，决策吃的是被终止那一局的残局状态（决策日志里
  `state_seq`/`notify_seq` 也会失真——所以决策行带 `run` 标明是第几局）。

### 决策档位

| 档位 | 触发 | 行为 |
|---|---|---|
| 占位策略（默认） | 不给 `--policy` | 纯标准库；`--strategy greedy\|random` + `--seed` |
| 脚本桥 | `--policy SPEC` | `envs.policy.build_policy` 的脚本 spec（`heuristic` / `legal_random` / `expert:<slug>` / `random`；`neural:` 在本仓库显式报错）；**每个 (会话, 座位) 一个独立实例** |
| LLM 技能智能体 | `--policy llm:<library>` | 由 `skillloop.run.build_policy` 构造的 `SkillAgentPolicy`：机械决策交给 era/expert 牌组，战略决策问模型；**只有 `meta.ai_seat` 那侧用 LLM**。`meta.ai_self_play=true` 且带 `stage` 时，非 AI 座位派 `npc:<stage>` 的真实 `NpcPolicy`（`--rotation` 同 runner）；缺 stage 时仍用 `heuristic` |

`llm:` 的 `<library>`（页面「AI 技能库」下拉可切，`POST /ai-library`，**下一局生效**）：

| 选择 | 库 | 说明 |
|---|---|---|
| `none` | 无 | 不注入任何技能，也不注入规则层 |
| `llm_empty` | 空库 v0 | ②′ 空库组；行为上与 none 相同，只是语义不同 |
| `static` | `data/skills/static_v1`（27 条） | 规则层；缺失时退回 `kind=rules` 中 `built_at` 最新的库 |
| `dynamic` | `kind=evolved` 中 **`built_at` 最新**者（当前 `dyn_v4`，40 条） | 注意按 built_at 选，不是目录名/version |

### 严格对齐 `skillloop.run`

**网页桥只是调试接口，LLM 策略与实验评测协议完全一致**：`LLMPolicy` 不再自己拼
`SkillAgentPolicy`/`TaskSpec`/`AgentConfig`，而是构造 `RunSpec` 后调用
`skillloop.run.build_policy`。因此自动得到：

- 关卡 `TaskSpec`（`skillloop.loadout.stage_task`，目标来自 `npc.spec.get_stage`）；
- runner 的 plain `skillloop.render.StateRenderer`；
- era/expert 基础策略（`skillloop.era.resolve_base_policy`）；
- **未过滤**技能库、候选上限 `candidate_cap=16`、引擎原候选顺序。

**已移除的 web-only 协议增强**（历史审计见 `reports/web_ai_*_20261112.md`）：

- `WEB_TACTICS` 网页战术注入；
- `filter_library_for_web` 技能库过滤（`skill_*` / `strat_mulligan` / `规则_目标_*`
  等全部保留，与运行器一致）；
- `WebStateRenderer`（附着元素名、技能/卡牌效果摘要、反应预览、手牌块）；
- `_reorder_for_web_llm` 候选重排；
- `web_llm` 的 `candidate_cap=24`。

`none` 与 `llm_empty` 在协议上都映射到 `group=llm_empty`（LLM + 空库；两者在
runner 里是同一条 `llm_empty` 协议）；`static` → `static` +
`data/skills/static_v1`；`dynamic` → `dynamic` + `built_at` 最新的 `kind=evolved`
库（当前 `dyn_v4`）。

**调试用途**：人类执 **NPC 座位**，AI 执 **参赛者**，页面按关卡评测协议跑；
配置页显示 `评价协议：<stage>（牌组/卡池/loadout/protocol）`，对局中下方
`AI 决策` 面板每 2s 从 `GET /ai-decisions` 拉最近决策，显示 AI 引用了哪些技能
（`used_skill_names`）、技能库版本、候选下标、fallback/耗时——用于检查
自进化得到的经验是否被实际检索/引用。

**牌组优先级**（与 runner 一致）：`--loadout-root` 的 loadout deck >
`--stage-decks` 的关卡 deck > 网页上报的 AI deck。loadout 缺失/过期时 stderr 告警并
退回 `--stage-decks`。默认 `scripts/serve_web_demo.sh` 按
`data/runs/v2_dynamic.jsonl.meta.json` 传 `--stage-decks configs/stage_decks.json
--loadout-root reports/loadouts/v4 --card-version v3.3.0`；跑其他 run 用
`STAGE_DECKS` / `LOADOUT_ROOT` / `CARD_VERSION` 覆盖。

**卡池版本与角色血量**：严格对齐会跟随评测的 `card_version`（时代基准是
`v3.3.0`）。官方角色卡 2301 在 v3.3.0 的卡面血量是 **10**（当前 v7.1.0 才是 11）；
因此时代协议下棋盘显示 10，这是官方时代数据，不是 NPC 难度值。NPC 变体 9301
仍然是 13（独立角色卡，见 `engine-overlay/.../fatui_pyro_agent_npc.gts`）。

**关卡/规则**：`/session` 的 `meta.stage` 是 NPC 阵容 slug；桥用
`npc.spec.get_stage` 解析，`meta.stage` 缺失时 `parity=false`（通用 TaskSpec 回退）
并在 stderr 告警。关卡特殊规则通过 `/npc-decks.eval.rules` 交给前端，桥本身不执行
对局。

**`GET /ai`** 报告策略协议（`/ai?session=` 报该会话）：

```json
{
  "protocol": "skillloop.run", "stage": "blanche", "parity": true,
  "group": "dynamic", "library_root": "data/skills/dyn_v4",
  "library_resolved": "dyn_v4", "card_version": "v3.3.0",
  "eval_deck": "keqing_shuanghuo", "loadout": null,
  "mode": "llm", "spec": "llm:dynamic#deck=...", "library": {...}, "options": [...]
}
```

`library.applied_skills == library.skills`、`library.filtered_out == 0`（严格对齐后
不再过滤；字段保留给前端向后兼容）。

`GET /health` 仍是存活探针。LLM 不可用（`.env` 没 key/base_url/model）时 `/session`
直接把 AI 座位改派回 `expert:<slug>`/`heuristic`，不会每步都走 fallback；决策日志里
LLM 步骤额外带 `used_skills` / `system_hash` / `llm_calls` / `library` /
`request` / `n_candidates` / `model_choice`（仅可观测性，不影响协议）。

### CLI（评测协议）

| 选项 | 默认 | 说明 |
|---|---|---|
| `--policy llm:<library>` | – | `none` / `llm_empty` / `static` / `dynamic`（`/ai.options` 仍列四项） |
| `--stage-decks` | `configs/stage_decks.json` | 每关固定牌组映射 |
| `--loadout-root` | `None` | 冻结 loadout（`reports/loadouts`；static/dynamic 生效） |
| `--library` | `None` | 覆盖 static/dynamic 解析到的库目录 |
| `--card-version` | `None` | 固定卡池（如 `v3.3.0`） |
| `--rotation` | `None` | 自对弈 NPC 座位 rotation（同 `skillloop.run`） |
| `--no-stage-rules` | 规则开启 | 关闭 `/npc-decks.eval.rules` |

组牌器与配置页的两个牌组预览也已改用同一份带 `customData` 的 `AssetsManager`；
官方可玩角色（2301/2202）的 datum 不再由 CustomData 覆盖，静态详情回退官方卡面
（11/10），而 NPC 阵容角色（3302/3101 等）仍显示引擎 `.gts` 的难度值。

reps/torch 只在请求模型策略时延迟导入，默认路径仍是零依赖。任何加载 /
转换 / `choose()` / 非法动作码失败都写 stderr 并回退占位策略，handler 不 500
也不挂死，**且回退会落进决策日志**（`fallback:true` + `error`）。

### T3 牌组对齐

`--ai-deck <slug>`（不给则从 `--policy expert:<slug>` 推断）声明 AI 座位该用的牌组：

- `GET /ai-deck` 把 `{characters,cards}` 给前端，前端用
  `DEFAULT_ASSETS_MANAGER.encode` 回填成分享码（配置页「取 AI 牌组」）；
- 开局 `POST /session` 里浏览器上报实际牌组，与声明不一致就 stderr 告警 + 会话
  记 `deck_mismatch:true`。

原因：脚本专家规则是按自家牌组写的，牌组不符会**静默变蠢**。

若 `meta.stage` 存在，桥还会把 AI 座位上报牌组与 `/npc-decks.eval.deck` 比较：
不一致会 stderr 告警并记 `deck_mismatch:true` / `protocol_mismatch:true`，本局在
`/ai.parity` 里标成非评价协议。

### `/npc-decks.eval`

每个 NPC 阵容（slug = 关卡名）带评价协议，前端据此回填 AI 牌组：

```json
"eval": {
  "stage": "blanche",
  "card_version": "v3.3.0",
  "deck": "keqing_shuanghuo",
  "characters": [1301, 1402, 1503],
  "cards": [312001, 312002, ...],
  "rules": {"player": 0, "supports": [], "effects": [{"kind": "initial_energy", "value": 2, "target": "friendly_all"}]},
  "loadout": null
}
```

- `deck` 来自 `--stage-decks`；配置了 `--loadout-root` 且有匹配 loadout 时用
  loadout deck（保证与 runner 实际使用一致）。
- `characters/cards` 是该 deck 经 `skillloop.era.resolve_deck` 解析后的实际值。
- `card_version` 在关卡 deck 映射到 era deck 时取 `configs/era_decks.json.version`
  （当前 `v3.3.0`），否则 `null`。
- `rules` 是 `npc.stage_rules.stage_rules_json(stage)` 解析后的 JSON；无规则为
  `null`；`--no-stage-rules` 关闭该字段。

### `GET /ai-decisions`（只读调试）

`GET /ai-decisions?session=<sid>&who=<0|1>&limit=<1..100, default 20>` 从该会话
`decisions.jsonl` 读取最后 `limit` 条该座位的 `type=="decision"` 行，返回：

```json
{"session": "...", "who": 0, "decisions": [
  {"step": 12, "method": "action", "policy": "llm:dynamic[dyn_v4]",
   "model_choice": 4, "n_candidates": 16,
   "used_skills": ["strat_reaction"], "used_skill_names": ["先附着再补元素"],
   "library": {"resolved": "dyn_v4", "path": "data/skills/dyn_v4"},
   "fallback": false, "error": null, "latency_ms": 3000,
   "system_hash": "...", "request": "action"}
]}
```

- `used_skill_names` 用该行 `library.path` 加载 `SkillLibrary`（按 path 缓存），未知
  id 或库加载失败时回退 id。
- 无日志目录/文件、会话不存在时返回空列表（200）；`who`/`limit` 非法返回 400。
- **只读**：不改策略、不改对局协议，供 human 扮 NPC 时观察 AI 每步引用了哪些技能。

## C1 反选（D19：对位矩阵 + decklab 固定规则）

`POST /choose-deck` 收**人的牌组** `{characters, cards}`，`web/counterpick.py` 挑一套
应对牌组：

1. 在评测池 20 套里找人的牌组的**代理**（相似度 = 0.6×角色 + 0.4×牌多重集），
2. 用 `reports/arena/` 的 **20×20 对位矩阵**（双向合并后的两两胜率）择优，
3. 相似度 < 0.2（池内没有像它的）→ 退到**总强度**最强的一套；
4. 没有矩阵文件 → 退到 **decklab 池 C 固定规则牌组**（配 `heuristic`）。

返回 `{slug,characters,cards,policy,method,proxy,similarity,win_rate,reason,
base_policy,policy_applied}`，并把结果记进会话（供开局校验）。

**策略改派**：`/session` 时若 base 策略是 deck-specific 的 `expert:*`，就按**各座位
实际牌组**改派——池内 20 套派它自己的 `expert:<slug>`（人的牌组若也是池内套，自对弈
时同样派它自己的脚本）；**非池内牌组（NPC 阵容/自改牌组）改派 deck-agnostic 的
`heuristic`**（硬套别人的脚本每步都会回退占位，日志里会刷一屏 fallback），并 stderr
告警。`neural:` 等用户显式选的策略**绝不覆盖**——那种情况 `policy_applied:false`，
只换牌组不换策略。已在局中的座位不再改（实例已建）。

前端流程（D18）：人选牌 → 点「AI 反选」（或直接开局时自动反选，只要没手改过 AI
牌组）→ 定先手（人类 / AI / 随机）→ 开局。

## C3 NPC 阵容（D18：人可持 NPC 敌人卡组）

`GET /npc-decks` → `web/npc_decks.py`（`assets` 字段由 `web/npc_assets.py` 提供），**全部 16 关原样返回，不跳过、不凑 3 人**：

| 特征 | 游戏内机制 | 本接口（近似） | **差异** |
|---|---|---|---|
| 出战人数不固定（数据里 2/3/4/5/6 都有，含同一敌人的多份） | 阵容几人就是几人 | 原样保留（含重复、含顺序）；引擎 `initPlayerState` 不校验人数，PvE 线已用 2–4 人阵容跑通 | 无（原样） |
| **手牌** | NPC 没有手牌、不打行动牌 | 牌组 `cards=[]` → 空牌堆（`drawCardsPlain` 遇空堆跳过；`initialHandsCount` 是全局配置、没有 per-player 版本，只能靠空堆实现） | 人持 NPC 阵容时**同样拿不到行动牌**，只能用角色技能与骰子 |
| **骰子** | NPC 每轮拿 8 个万能元素骰 | 持 NPC 阵容的座位在前端置引擎 `alwaysOmni`（每轮初始掷骰全 OMNI，张数＝全局 `initialDiceCount`＝8）；`alwaysOmni` 是游戏级开关，续局/导入日志一并生效 | 骰子仍可主动重投，重投出来的按全局规则随机（游戏内 NPC 不重投，NPC 侧不受影响） |
| **支援区（预备）角色** | 场外待命：**只有在场角色被击败后才依次上场**，不能被主动切换 | 没有支援区机制 → 把预备角色接在出战角色之后一起进牌组＝**后台角色**（开局可见、可被主动切换），靠「出战没死就不主动切」近似 | **上场时机与可见性不一致**：本接口里支援角色可以提前上场 |
| **多轮挑战** | 打完当前战场（在场+支援）后**玩家角色血量与卡牌状态保持不变**，进入**新战场**打下一轮 NPC 阵容，直到全部轮次打完 | **一场一局**，打完即结束；关卡数据 schema 也没有 `waves` 字段 | **没有轮次接续、没有「新战场」**，也无法表达玩家状态跨轮保留 |

> **NPC 静态数据已接通**：`/npc-decks` 的 `assets` 字段带上从 `.gts` 解析出的
> `CustomData`（`web/npc_assets.py`），前端 `new AssetsManager({customData})` 传给
> `createClient` —— `setupCustomData` 会把这些自定义角色/技能的 datum 灌进缓存，
> `getData` 因此不再打镜像。**角色名、技能名/描述、骰子费用、卡面占位图都能正常显示**。
> 两处 overlay 护栏（`card-data-viewer` 的 `Description` 与 `PlayCost`）保留作兜底：
> 万一某个 id 没进缓存，面板显示「静态数据里没有该 id 的描述」而不是把对局打崩。
> 技能名沿用官方敌人数据里的 Hilichurl 语名（`Du Plama`/`Upa`/`Oto Tiga`），中文的
> 「普通攻击/元素战技/元素爆发」是技能**类型**标签。

前两条近似之外的说明（行动牌来源、分享码限制、骰子）也在返回的 `semantics` 里，前端会
原样展示。

> **TODO（探索）**：找**替代方式**来表达「支援区」与「多轮挑战」——现状把它们压扁成
> 「后台角色 + 单场」，与游戏内机制不一致。候选方向（都还没做）：① 关卡 spec 增加
> reserve 的「上场时机」与 `waves` 字段，由引擎/外层 runner 实现；② 引擎侧支持
> 击败后自动入场与局间接续玩家状态（属 PvE 线 PLAN §4.4）；③ 纯展示方案——牌组只放
> 出战角色，支援/轮次作为关卡说明在 UI 呈现，不伪装成牌组的一部分。

NPC 角色 id 不在官方 `share_id.json` 里、**编不出分享码**，所以前端按**牌组对象直传**
（不走输入框分享码）。

### C4 逐决策日志

`--log-dir`（默认 `data/web/`，已在 gitignore 内）每局一个目录：

```
data/web/20261015T120000Z_<session前24字符>/
  session.json     # 开局元数据（版本 / 牌组 / ai_seat … + deck_mismatch / seat_specs）
  decisions.jsonl  # 每次决策一行 + run_start（续局）+ 终局 game_end 一行
  summary.json     # 关账汇总（胜负 / 回合数 / 各座位决策数 / 回退数 / 过期通知数 /
                   #            runs / aborted / deck_mismatch / seat_specs）
  game_log.json    # 浏览器上传的引擎日志（含人类这边的决策）
```

决策行字段：`t / iso / type / session / who / method / step / run / state_seq /
notify_seq / latency_ms / policy / fallback / error / response`（`state_seq` 与
`notify_seq` 都是**本局**计数，跨续局看 `run`）。
`--no-log` 关闭；写盘失败只告警、不影响对局。终局优先信浏览器的 `POST
/game-end`，`/notify` 里看到 `winner`（或 phase=gameEnd）也会兜底关账。

**故障局不算胜负**：AI 桥 IO 出错时引擎会按 `gotWinner(flip(who))` 把该方判负，那是
故障不是结果——浏览器上报 `/game-end` 时带 `aborted:true`，桥把会话标成
`close_source=io_error`、`winner=null`；如果 `/notify` 已经先按那个假 winner 自动关过
账，会改判一次（`game_end` 记录里 `amended:true`）。胜率/战绩口径直接过滤
`aborted`（以及 `winner is None`）。

## 用法

```sh
# 占位策略（零依赖）：
conda run -n gitcg-skill python web/ai_rpc_server.py --port 8770 --strategy random --seed 1

# 模型桥：用训练好的 CVPN 驱动 AI 座位（checkpoint 自带 encoder 配置）：
conda run -n gitcg-skill python web/ai_rpc_server.py --port 8770 \
    --policy "neural:data/checkpoints/curve_dfbcsa_e15_d128_n48000.pt#t=0.2" \
    --ai-deck superconduct_aggro

# 脚本专家（牌组自动对齐 + 逐决策日志）：
conda run -n gitcg-skill python web/ai_rpc_server.py --port 8770 \
    --policy expert:superconduct_aggro

# 另开一个终端：
bash engine-overlay/scripts/standalone.sh dev   # 会先 apply overlay 再起 dev server
# 浏览器 http://localhost:5173/ 勾「AI 对手（HTTP 桥）」（自动取 AI 牌组回填）+「双方 AI 自对弈」
```

### 单端口观战部署（AI vs NPC）

`scripts/serve_web_demo.sh` 一次起好前端 + 桥，并把 `/bridge/*` 反代到桥
（vite 的 `proxy`），所以浏览器只访问前端端口：

```sh
bash scripts/serve_web_demo.sh                  # 前台；PORT=7001 是默认值
# 后台常驻：
setsid nohup bash scripts/serve_web_demo.sh >/tmp/gitcg-web.log 2>&1 < /dev/null &
```

浏览器打开 `http://<本机IP>:7001/`（局域网另一台机器也能访问）：

1. 「NPC 阵容」选一个关卡 —— 这成为**我的座位**（NPC 阵容）；`/session` 带
   `meta.stage`，自对弈时桥给这个座位派 `npc:<stage>` 的真实 `NpcPolicy`
   （缺 stage 时才退回 `heuristic`）；
2. 勾「AI 对手（HTTP 桥）」+「双方 AI 自对弈」—— 桥的 `--policy` 驱动对面座位，
   牌组按 `/npc-decks.eval.deck` 自动回填并与 `skillloop.run` 一致；「AI 技能库」
   下拉可切 `none/llm_empty/static/dynamic`，下方状态行显示**实际解析到的库**
   （目录、版本、条数、built_at）与 `parity`；
3. 「开始对局」实时观战（`observeIo` 把通知同时喂给棋盘，否则自对弈时棋盘会停在
   「正在等待…」）。

配置页还会并排预览**我的牌组 / AI 牌组**（角色缩略图 + 行动牌按 id 去重计数；NPC
角色从 `/npc-decks` 的 `assets` 取名字，缺图的角色/卡牌只显示名字）。

可用 `PORT` / `BRIDGE_PORT` / `POLICY` / `AI_DECK` / `SEED` / `CONDA_ENV` 覆盖默认值。
默认 `POLICY=llm:dynamic`（LLM + 最新进化库，需要 `.env` 的 key）；想用脚本 AI：

```sh
POLICY=expert:superconduct_aggro bash scripts/serve_web_demo.sh
POLICY=llm:none bash scripts/serve_web_demo.sh   # LLM，不注入技能库
```

CORS 默认只放行本机 Origin（`localhost`/`127.0.0.1`/`::1`）；如需其他来源
（例如经局域网 IP 访问 dev server）用 `--cors-origin <origin|*>` 显式放行。
预检响应带 `Access-Control-Max-Age`（`_CORS_MAX_AGE_SECONDS`），浏览器缓存
OPTIONS 结果，跨域访问时不再每个请求配一次预检。

无第三方依赖、无 node_modules；缓存只有 `__pycache__/`（根 `.gitignore` 已忽略）。

## 定位

这是**调试接口**，但它驱动 AI 座位的 LLM 策略必须与 `skillloop.run` 的实验口径
严格一致：同一 `stage` / `deck` / 库 / loadout / 卡池 / 规则下，网页里的每一步决策
与运行器的 `build_policy` 输入相同。`parity=false` 的会话（缺 `meta.stage` 或牌组
与 `eval.deck` 不符）只用于手动调试，不能当作评测数据。`--policy neural:...` 在本
仓库显式报错（无 torch）。

## 测试

`tests/test_web_rpc_server.py`：占位路径、snake→camel 映射、proto-JSON→
StateSnapshot、真实对局抓取的端到端决策、oneof 摊平、回退、子进程直跑、HTTP
路由，以及 T1 座位/会话隔离、T2 过期通知护栏与**续局 seq 基线**、T3 牌组校验
（`deck_mismatch`/`seat_specs` 落盘）、C4 日志生命周期与**故障局归档**；另有严格
对齐用例：`build_policy` 产物（plain `StateRenderer`、关卡 TaskSpec、未过滤库、
cap 16）、`/npc-decks.eval`、`/ai.parity`、自对弈 `npc:<stage>` 座位。

## 待办

- `/notify` 目前是**每步一次的全量状态上报**（一局数千条）。可改增量/节流/合并
  以降请求量；未做（当前不是瓶颈）。
