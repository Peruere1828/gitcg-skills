# web/ — 网页调试端协议

本文件描述**浏览器调试端（前端）**应实现的协议。Python 侧
`web/ai_rpc_server.py` 是桥，前端在
`engine-overlay/overlay/packages/standalone/`。读者只看这一份即可跑起来。

## 1. 定位与边界

- **人类固定执 NPC 牌组**（seat1）；**AI 执用户选定的牌组**（seat0），seat0 先手。
- **游戏版本固定**：由所选关卡的 `eval.card_version` 决定（例如 `v3.3.0`）；
  前端不提供版本选择。
- **前端不修改 LLM 策略；任何策略/库/提示词变更都在后端 `skillloop.run`
  协议内完成。** 桥构造策略时直接调用 `skillloop.run.build_policy`，与离线评测
  同源；前端只提供可视化界面、对局日志与决策信息展示。
- 一局对应一个 `session` id。主流程只有四步：选 NPC 阵容 → 确认 AI 牌组 →
  开局 → 看棋盘与 AI 决策。

## 2. 主流程（前端）

1. **选 NPC 阵容**：人类执该阵容（seat1）。`GET /npc-decks` 返回全部关卡，
   每条的 `eval` 带 AI 参赛者牌组、卡池版本、关卡特殊规则与 loadout。
2. **AI 牌组输入框**：可编辑；默认用 `eval.deck`（`eval.characters/cards`）
   自动回填。
3. **开始对局**：AI 固定 seat0 先手，人类 seat1 后手；没有版本、先手或反选选择。
4. **对局中**：棋盘详情与导出日志保留；下方 AI 决策面板轮询
   `GET /ai-decisions`，显示 AI 引用的技能、技能库版本与耗时；面板顶部可
   一键导出本局完整 prompt / 模型回复 / 思考（Markdown）。终局上报
   `POST /game-end`，引擎日志上报 `POST /game-log`。

## 3. 前端协议（需要的接口）

浏览器访问前端端口，`/bridge/*` 由前端反代到桥（默认 `127.0.0.1:8770`）。

| 方法 / 路径 | 用途 |
|---|---|
| `GET /npc-decks` | NPC 阵容列表；每条含 `eval`（`stage`/`card_version`/`deck`/`characters`/`cards`/`rules`/`loadout`）与 `assets`（CustomData，喂 `AssetsManager`） |
| `POST /session` | 开局。`{session, meta:{stage, version, ai_seat:0, human_seat:1, decks:{"0":{…},"1":{…}}}, resume?}`；返回 `seq_base` 等 |
| `POST /rpc` | 浏览器 → 桥的引擎 RPC：`{session, who, request}` → `RpcResponse`（AI 座位决策） |
| `POST /notify` | 通知上行：`{session, who, seq, notification:{mutation,state}}`；座位内单调 `seq`，过期通知被丢弃 |
| `GET /ai` | 只读状态：`protocol`/`stage`/`parity`/`group`/`library`/`card_version`/`eval_deck` 等 |
| `GET /ai-decisions?session=&who=0&limit=20` | 只读决策行：`used_skills`/`used_skill_names`/`library`/`latency_ms`/`fallback`/`llm_error`/`finish_reason`/`reasoning` |
| `GET /ai-prompt-log?session=&who=0&format=md` | 导出本局完整 prompt / 模型回复 / 思考（`md` 或 `jsonl`）；数据来自 `LLM_TRACE` |
| `POST /game-end` | 终局：`{session, winner, rounds, aborted?, abort_reason?}` |
| `POST /game-log` | 上传引擎日志：`{session, log}` |

前端主流程**不调用** `POST /choose-deck`、`GET /ai-deck`、`POST /ai-library`；
这些接口仅为手动调试保留。

## 4. 暂不提供 / 尚不完善

以下能力代码可能保留，但**不属于当前调试主流程**，不要依赖：

- **AI 反选**（`POST /choose-deck`）：评价协议下 AI 牌组由关卡 `eval.deck` 固定。
- **取 AI 牌组**（`GET /ai-deck`）：牌组已由 `eval` 回填，主流程不需要。
- **游戏版本选择**：版本跟随关卡 `eval.card_version`。
- **多人对战**：固定 1v1。
- **导入日志**。
- **摆盘器**。
- **双方 AI 自对弈**：仅自动检查时使用，人类手动调试不勾选。
- **AI 技能库下拉切换**（`POST /ai-library`）：库由后端 `POLICY` 决定。

## 5. 运行

```sh
# 一次起好前端 + 桥，前端反代 /bridge → 本机 8770
bash scripts/serve_web_demo.sh
```

默认值对齐 `v2_dynamic`：前端 7001、桥 8770、
`configs/stage_decks.json` + `reports/loadouts/v4` + `v3.3.0`。
后台常驻：

```sh
setsid nohup bash scripts/serve_web_demo.sh >/tmp/gitcg-web.log 2>&1 < /dev/null &
```

浏览器打开 `http://<本机IP>:7001/`，然后：

1. 选 NPC 阵容（人类执该阵容）；
2. 确认 AI 牌组已由 `eval` 自动回填（可手动改）；
3. 开始对局；
4. 看下方 AI 决策面板，检查技能引用是否生效。

必要环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` | `7001` | 前端端口 |
| `BRIDGE_PORT` | `8770` | Python 桥端口（仅本机，经前端反代） |
| `POLICY` | `llm:dynamic` | AI 座位策略 spec（也可 `llm:none`/`llm:llm_empty`/`llm:static`） |
| `SEED` | `20261112` | 策略种子 |
| `CONDA_ENV` | `gitcg-skill` | Python 环境 |
| `STAGE_DECKS` | `configs/stage_decks.json` | 关卡 → 牌组映射 |
| `LOADOUT_ROOT` | `reports/loadouts/v4` | 冻结 loadout 根 |
| `CARD_VERSION` | `v3.3.0` | 卡池版本 |
| `REASONING` | `none` | AI 决策的 reasoning effort（`none`/`low`/`medium`/`high`；非 `none` 时回复预算 8192 token，仅调试用） |
| `LLM_TRACE` | `data/web/llm_trace.jsonl` | 模型调用 JSONL 路径（完整 prompt + content + reasoning_content；不含 key；设 `none` 关闭） |

## 6. 代码位置

- `engine-overlay/overlay/packages/standalone/src/App.tsx`：配置页与主流程
- `engine-overlay/overlay/packages/standalone/src/StandaloneParent.tsx`：棋盘、会话、日志
- `engine-overlay/overlay/packages/standalone/src/AiDecisionPanel.tsx`：AI 决策监控
- `engine-overlay/overlay/packages/standalone/src/stage-rules.ts`：关卡规则映射
- `web/ai_rpc_server.py`：后端桥（策略由 `skillloop.run.build_policy` 决定）

## 7. LLM 策略与技能库

四个库选择与离线评测的四组严格对应：

| `llm:<选择>` | 实际库 | 含义 |
|---|---|---|
| `none` / `llm_empty` | `data/skills/rules_v1`（17 rule） | **仅游戏规则**，不含 strategy/stage_specific，也不用 loadout |
| `static` | `data/skills/static_v1`（17 rule + 10 strategy） | 游戏规则 + 静态策略；可用 loadout |
| `dynamic` | `data/skills/dyn_v4`（17 rule + 23 strategy） | 游戏规则 + 动态策略；可用 loadout |

主流程不暴露库选择；`GET /ai` 的 `library` 会报告实际解析到的库（`llm_empty`
报告 `rules_v1`，17 条规则）。

桥的 `--scratchpad` 开启**局内记录板**（默认关，仅用于和 `skillloop.run --scratchpad`
对齐的 A/B 调试）；`GET /ai` 的 `scratchpad` 字段报告该状态。详见
`docs/EVAL_PROTOCOL.md` §7。
