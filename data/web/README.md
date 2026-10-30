# data/web/ — Web 调试接口的逐决策日志（C4）

`web/ai_rpc_server.py` 的落盘根目录（`--log-dir`，默认本目录；`--no-log` 关闭）。
本目录内容不入库（根 `.gitignore` 忽略 `data/**`，只留本 README），只提交 schema。

每局一个目录：`<UTC时间戳>_<session前24字符>/`

| 文件 | 内容 |
|---|---|
| `session.json` | 开局元数据：引擎版本、`ai_seat`、自对弈开关、两个座位的牌组（definition id） |
| `decisions.jsonl` | 一行一条记录（`type` 区分）：`decision`（每次 RPC 决策）+ 一行 `game_end` |
| `summary.json` | 关账汇总：胜负、回合数、各座位决策数/回退数、过期通知数、日志目录 |
| `game_log.json` | 浏览器上传的引擎日志（`serializeGameStateLog`，含人类这边的决策） |

`decision` 行字段：

```
t iso type session who method step state_seq notify_seq latency_ms policy fallback error response
```

- `step`：该座位的第几步（按座位独立计数）；
- `state_seq` / `notify_seq`：这次决策看到的是该座位第几条通知、浏览器发的第几号 `seq`；
- `fallback`：模型桥是否回退到占位策略（`error` 是失败原因）；
- `response`：实际回给引擎的动作（camelCase）。

`game_end` 行额外带 `winner / rounds / source（game-end|notify）/ decisions / notify /
fallbacks / stale_drops`。

语义与格式细节见 `web/README.md`。
