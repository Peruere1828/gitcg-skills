# web/ — 浏览器 ↔ Python 桥（WS8/D18/C2 + T1/T2/T3/C4）

本目录放 **Web 人机调试界面** 的 Python 侧最小服务，配合
`engine-overlay/overlay/packages/standalone/` 的浏览器端改动使用。

## `ai_rpc_server.py`

纯标准库（`http.server`）实现，把引擎的 `PlayerIO` RPC 用 HTTP 暴露给浏览器：

| 端点 | 请求体 | 返回 |
|---|---|---|
| `POST /rpc` | `{"session","who":0\|1,"request":RpcRequest}` | `RpcResponse` |
| `POST /notify` | `{"session","who","seq","notification":{mutation,state}}` | `{"applied":bool,"seq","state_seq"}` |
| `POST /session` | `{"session","meta":{version,ai_seat,human_seat,ai_self_play,decks},"resume?":bool}` | 会话记录 + `deck_mismatch` + `seat_specs` + `runs` + `seq_base`（`resume:true`＝续局，给 seq 基线） |
| `POST /choose-deck` | `{"session","deck":{characters,cards}}` | C1 反选结果（见下） |
| `POST /game-end` | `{"session","winner","rounds","aborted?":bool,"abort_reason?":str}` | `{"ok","closed","aborted"}`（`aborted:true`＝故障局，归档成 `io_error`、不记胜负） |
| `POST /game-log` | `{"session","log"}` | `{"ok","dir"}`（引擎日志归档） |
| `GET /health` | – | `{"ok":true,"policy","rpc","notify","sessions","stale_notify","ai_deck","log_dir"}` |
| `GET /state?session=&who=` | – | 该座位最近一次掩码 GameState（不带参数＝老行为，返回最近一条） |
| `GET /ai-deck` | – | `{"slug","characters","cards"}`（T3 牌组对齐） |
| `GET /npc-decks` | – | C3：全部 NPC 关卡阵容（人数不固定）+ `assets`（CustomData，喂给 AssetsManager） |
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
| 模型桥 | `--policy SPEC` | `envs.policy.build_policy` 全 spec（`neural:<ckpt>[#t=]` / `heuristic` / `legal_random` / `expert:<slug>` / `random`）；**每个 (会话, 座位) 一个独立实例** |

reps/train/torch 只在请求模型策略时延迟导入，默认路径仍是零依赖。任何加载 /
转换 / `choose()` / 非法动作码失败都写 stderr 并回退占位策略，handler 不 500
也不挂死，**且回退会落进决策日志**（`fallback:true` + `error`）。

### T3 牌组对齐

`--ai-deck <slug>`（不给则从 `--policy expert:<slug>` 推断）声明 AI 座位该用的牌组：

- `GET /ai-deck` 把 `{characters,cards}` 给前端，前端用
  `DEFAULT_ASSETS_MANAGER.encode` 回填成分享码（配置页「取 AI 牌组」）；
- 开局 `POST /session` 里浏览器上报实际牌组，与声明不一致就 stderr 告警 + 会话
  记 `deck_mismatch:true`。

原因：脚本专家规则是按自家牌组写的，牌组不符会**静默变蠢**。

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
conda run -n gitcg python web/ai_rpc_server.py --port 8770 --strategy random --seed 1

# 模型桥：用训练好的 CVPN 驱动 AI 座位（checkpoint 自带 encoder 配置）：
conda run -n gitcg python web/ai_rpc_server.py --port 8770 \
    --policy "neural:data/checkpoints/curve_dfbcsa_e15_d128_n48000.pt#t=0.2" \
    --ai-deck superconduct_aggro

# 脚本专家（牌组自动对齐 + 逐决策日志）：
conda run -n gitcg python web/ai_rpc_server.py --port 8770 \
    --policy expert:superconduct_aggro

# 另开一个终端：
bash engine-overlay/scripts/standalone.sh dev   # 会先 apply overlay 再起 dev server
# 浏览器 http://localhost:5173/ 勾「AI 对手（HTTP 桥）」（自动取 AI 牌组回填）+「双方 AI 自对弈」
```

CORS 默认只放行本机 Origin（`localhost`/`127.0.0.1`/`::1`）；如需其他来源
（例如经局域网 IP 访问 dev server）用 `--cors-origin <origin|*>` 显式放行。
预检响应带 `Access-Control-Max-Age`（`_CORS_MAX_AGE_SECONDS`），浏览器缓存
OPTIONS 结果，跨域访问时不再每个请求配一次预检。

无第三方依赖、无 node_modules；缓存只有 `__pycache__/`（根 `.gitignore` 已忽略）。

## 定位

这是**调试/训练辅助接口**，不进口径验收（D18）。C2 已把占位策略接到
`reps/` + `train/` 的 CVPN checkpoint（`--policy neural:...`），即
「HTTP 桥 → 本地模型」；`common/llm.py` 侧作为后续可选分支。

## 测试

`tests/test_web_rpc_server.py`：占位路径、snake→camel 映射、proto-JSON→
StateSnapshot、真实对局抓取的端到端决策、oneof 摊平、回退、子进程直跑、HTTP
路由，以及 T1 座位/会话隔离、T2 过期通知护栏与**续局 seq 基线**、T3 牌组校验
（`deck_mismatch`/`seat_specs` 落盘）、C4 日志生命周期与**故障局归档**。

## 待办

- `/notify` 目前是**每步一次的全量状态上报**（一局数千条）。可改增量/节流/合并
  以降请求量；未做（当前不是瓶颈）。
