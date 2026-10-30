# WS8 standalone overlay（D18）

本目录是上游 `packages/standalone/` 的 overlay 副本（D20：`overlay/` 是引擎仓根镜像，
由 `engine-overlay/scripts/apply-overlay.sh` 复制进 `build/engine/`）。
`refs/` 只读，我们的改动全部在这里。

## 我们改了什么

| 文件 | 改动 | 目的 |
|---|---|---|
| `vite.config.ts` | 新增 `envPrefix` + `define`，注入 `DEFAULT_STATIC_DATA_API_BASE_URL` / `globalThis.__GI_TCG_ASSETS_BASE_URL__`；`server.host/port` | 让浏览器里的 assets-manager 指向可达镜像；命令行可覆盖 |
| `src/ai-http-io.ts` | **新增**：HTTP 版 `CancellablePlayerIO` | 浏览器引擎 ↔ Python 服务的桥；带 `sessionId`（T1 会话隔离）+ 通知队列与 `seq`（T2 防陈旧状态）+ `seqBase`（续局基线） |
| `src/App.tsx` | 配置页改成 D18 流程：「我的牌组 / AI 牌组」+「AI 反选」（C1）+「取 AI 牌组」（T3）+ NPC 阵容下拉（C3，16 关全可选）+ **先手三选**（人类 / AI / 随机）+ 桥地址 | 人选牌 → AI 看牌反选 → 定先手；开局时把牌组解成对象、生成 `sessionId` |
| `src/StandaloneParent.tsx` | 按 `humanSeat` 派生座位（seat0 恒先手）：本窗口托管真人棋盘、对面座位交 HTTP 桥或弹窗（座位号经 `#opponent=` 传给弹窗）；开局 `POST /session`（真实 `ai_seat`/牌组；续局带 `resume` 并从响应的 `seq_base` 接着数）、终局 `POST /game-log` + `POST /game-end`（桥 IO 故障时 `aborted`）；收到 `npcCustomData` 时给棋盘**与「查看历史」棋盘**都换一个 `new AssetsManager({customData})`；续局前 `cancelRpc` 所有 AI io | 随机/手动先手都成立；C4 日志归档（故障局标 `io_error` 不算胜负）；**NPC 角色的名字/技能描述/骰子费用/卡面占位图**在实时与历史视图里都显示 |
| `src/StandaloneChild.tsx` | **座位不再写死 seat0**：从 `#opponent=<0\|1>` 读座位交给 `createClient`，标题跟着变 | 真人先手（seat0）时弹窗托管 seat1，写死 seat0 会让弹窗把对手的掩码态当自己渲染（看不到自己的手牌/骰子） |
| `../card-data-viewer/src/Description.tsx` | **护栏**：`descriptionToItems` 拒收非字符串 + `Description` 在描述缺失时给可诊断提示 | 静态数据缺 datum 时 `rawDescription` 为 undefined，会 `undefined.matchAll` **把对局打崩** |
| `../card-data-viewer/src/PlayCost.tsx` | **护栏**：`playCost` 改为可选 + `?? []` | 同上，`[...undefined]` 会抛 `e.playCost is not iterable` |

另有一处 **assets-manager** 的 overlay：
`../assets-manager/src/constants.ts` 把 `DEFAULT_STATIC_DATA_API_BASE_URL` 的兜底默认值
从不可达的 `https://static-data.piovium.org/` 改成本机可达的 v5 镜像
`https://gi-tcg-assets-api-hf.guyutongxue.site/`（仍保留 `import.meta.env` /
`process.env` / `GI_TCG_ASSETS_BASE_URL` 三级覆盖）。

**NPC 牌组的骰子**（D18）：App 把 `/npc-decks` 各阵容的角色列表传给
`StandaloneParent`；createGame 时按座位判断——牌组的角色与某个阵容**完全一致**
（顺序无关、保留重复）且**没有行动牌**，就置
`game.players[who].config.alwaysOmni = true`（游戏内 NPC 每轮 8 个万能骰；张数＝
全局 `initialDiceCount`＝8）。重投照常可用（重投出来的按全局规则随机，游戏内 NPC
不重投）。判据是**阵容整体匹配**而不是「命中某个 NPC 角色 id」——官方
`share_id.json` 里也有敌人角色（2202 / 2301），按 id 判会把官方牌组误判成 NPC 牌组。

## 构建 & 起局

前置：`build/engine/` 已 materialize（`engine-overlay/scripts/materialize.sh`）；
宿主 node24 + pnpm12（或 B0 的 Docker 镜像）。

```sh
# 1. 应用 overlay（把上面的文件复制进 build/engine）
bash engine-overlay/scripts/standalone.sh apply

# 2. 构建 standalone 的全部 workspace 依赖（web-ui-core / deck-builder /
#    state-editor / detail-log-viewer / data-code-analyzer / assets-manager）
#    —— 注意要用可达镜像，否则 assets-manager 的 build:data 会卡在 static-data.piovium.org
bash engine-overlay/scripts/standalone.sh build

# 3. 起 dev server
bash engine-overlay/scripts/standalone.sh dev
#   ➜  http://localhost:5173/
```

**为什么先 build 依赖**：standalone 的若干 workspace 依赖（`@gi-tcg/web-ui-core` 等）
package.json 只导出 `dist/`，Vite dev 无法直接解析其源码，因此必须先构建。
只有 `@gi-tcg/data` / `@gi-tcg/assets-manager` 有 `development` 条件会走 `src/`。

**镜像 / base URL**：`assets-manager` 走 `${base}/api/v5/{data,datum,image}/...`。

- 构建期：`pnpm --filter "@gi-tcg/assets-manager" build:data` 拉 `names/characters/
  entities/keywords` 等，写入 `packages/assets-manager/src/data/*.json`（随 dist 打包；
  `src/data/` 已在引擎 `.gitignore`）。
- 运行期：`assets-manager` 由此 base URL 拉卡牌图/数据。镜像返回
  `access-control-allow-origin: *`，浏览器可直连。
- 覆盖：`GI_TCG_ASSETS_BASE_URL=https://... bash engine-overlay/scripts/standalone.sh dev`。

## 起局

1. 浏览器打开 `http://localhost:5173/`。
2. **双人类（上游默认）**：直接点「开始对局」，先手方棋盘在弹窗、后手方在本页。
   （需要浏览器允许弹出窗口。）
3. **AI 对手（HTTP 桥）**：勾选「AI 对手（HTTP 桥）」，地址填
   `http://127.0.0.1:8770`；先起 Python 服务：

   ```sh
   conda run -n gitcg python web/ai_rpc_server.py --port 8770 \
       --policy expert:superconduct_aggro
   ```

   然后按 D18 流程配牌（配完点「开始对局」）：
   - **我的牌组**：贴分享码，或在「NPC 阵容」下拉里选一套 NPC 阵容（C3，16 关全可选，
     人数不为 3 的阵容原样给；选中后输入框显示 `NPC·<关卡名>`，不走分享码）。
     **NPC 阵容不带行动牌（`cards=[]`）**，所以这一侧开局没有手牌、只能用角色技能和
     骰子——这是刻意对齐游戏内「NPC 无手牌」，不是 bug；NPC 的**名字/技能描述/骰子费用**
     由桥的 `/npc-decks` `assets` 字段带过来（解析自 `.gts`，`web/npc_assets.py`），
   - **AI 牌组**：点「AI 反选」（C1，按我的牌组用对位矩阵挑）或「取 AI 牌组」
     （T3，用桥 `--ai-deck`/`--policy expert:<slug>` 声明的那套）；**没手动改过 AI
     牌组时，点「开始对局」会自动再反选一次**；
   - **先手**：人类先手 / AI 先手 / 随机（引擎 seat0 恒先手，这里决定谁坐先手位）；
   - 本页面永远是**我**的棋盘；AI 模式下对面座位交给桥（不弹窗）。
   再勾「双方 AI 自对弈」可让两边都交给 Python，便于零看管跑完整局。
4. 「摆盘器」「导入日志」「查看历史/从此处继续」等上游功能保持可用。

## HTTP 桥协议

浏览器侧 `createAiHttpIo`（`src/ai-http-io.ts`）：

- `rpc(request)` → `POST {base}/rpc`，body `{"session","who","request"}`，期望回
  `RpcResponse`（`{"response":{"$case":..., "value":...}}`）。
- `notify(notification)` → `POST {base}/notify`，body
  `{"session","who","seq","notification":{mutation,state}}`（尽力而为，AI 用来“看盘”）。
- `cancelRpc()` → `AbortController.abort()`。

`session` 是每局一个的 id（`StandaloneParent` 用 `crypto.randomUUID()` 生成），桥
按 `(session, who)` 隔离掩码状态、策略实例与决策日志（T1）；`seq` 是座位内单调
的序号，桥丢弃过期通知，且 `rpc` 会先等前面的 notify 全部落地才发出（T2）。
**「从此处继续」**在同一个 session 上开新的一局：`/session` 带 `resume:true`，
桥回 `seq_base`（每个座位上一局最后一个已应用的 seq），新的 io 从 `seq_base + 1`
接着数——否则新局的通知会被桥全判成过期，AI 决策吃的是被终止那一局的残局状态。

开局前 `StandaloneParent` 还会 `POST /session`（牌组/版本/座位），终局
`POST /game-log`（引擎日志，含人类这边的决策）+ `POST /game-end`（胜负；
若这一局是桥 IO 故障被引擎判负的，带 `aborted:true` → 桥标成 `io_error`，
不记胜负），落进桥的 `data/web/<时间戳>_<session>/`（C4，详见 `web/README.md`）。

双人模式（不勾 AI 桥）时弹窗由 `showPopup` 打开，URL 带 `#opponent=<0|1>`：
弹窗托管的是**对面**座位（真人随机先手时可能在 seat0 或 seat1），`StandaloneChild`
按这个座位 `createClient`；写死 seat0 会让「真人先手」那半数的对局在弹窗里看到
错误的掩码视角（自己的手牌变暗牌、骰子全 UNSPECIFIED）。

`RpcRequest/Response` 定义见 `packages/typings/src/gen/rpc.ts`；座位 `action` 请求里带
合法动作列表（`validity===0` 为合法）、每个动作的 `autoSelectedDice`（引擎自动选骰），
所以 `action` 回复 = `{chosenActionIndex, usedDice: action[i].autoSelectedDice}` 即可。

## 已知坑

- **`static-data.piovium.org` 不可达**（本机 connect timeout）：所以必须用镜像 +
  上面的 `constants.ts` 兜底；否则卡牌图/`build:data` 全挂。
- **必须先 build 依赖**（见上），否则 dev 起不来。
- **上游第三方 async-context**：游戏结束时会抛一次非致命
  `ReferenceError: contextStorage is not defined`，来自 esm.sh 的
  `@beaker73/async-context`（其 `eval` 生成的 `finally` 引用了未定义的
  `contextStorage`），**不影响对局结果**，仅控制台噪声。
- 宿主 node24 可用（上游 `engines` 写 node ^26，实测 node v24.15 正常）。

## 已完成 / 下一步（WS8 路线）

**已完成**：人选牌（分享码 + **NPC 阵容**，16 关全可选）、**AI 看对手牌反选**
（`POST /choose-deck`，对位矩阵 + decklab 兜底，未手改时开局自动反选）、
**先手三选**（人类 / AI / 随机，`humanSeat` 派生座位；弹窗按 `#opponent=` 拿座位，
不再写死 seat0）、模型桥 `--policy` 全 spec、逐决策日志与战绩归档（C4：续局
`seq` 基线、故障局 `io_error` 归档、`deck_mismatch`/`seat_specs` 落盘）。
协议与近似语义见 `web/README.md`。

**下一步**：

1. **组牌器直通**：在 `@gi-tcg/deck-builder` 里选完直接回填「我的牌组」
   （现在要手动贴分享码）。
2. **LLM 辅助档**：把 `agents/llm_assist.build_llm_assist_policy` 接成
   `--policy llm:<slug>`，干预记进决策日志（分开报分、不进口径验收）。
3. **`/notify` 降流量**：每步一次全量状态（一局数千条）→ seq/节流/合并。
4. **e2e 固化**：把无头 Chromium 冒烟（NPC 阵容 × 先手 × 反选）落成仓库脚本。
