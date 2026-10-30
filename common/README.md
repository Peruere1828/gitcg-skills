# common — 共享基础件（横切；PLAN.md §0.1 工程约定、§9 目录）

各线复用、无引擎依赖的最小工具层。所有模块**纯 stdlib**（`llm.py` 只用 `urllib`），可被任意模块安全 import。

## 模块

| 文件 | 职责 |
|---|---|
| `paths.py` | `REPO_ROOT` / `repo_root()` / `data_dir()` / `reports_dir()` / `ensure_dir()`——路径相对 `__file__` 解析（脚本不依赖 cwd）；`GITCG_DATA_DIR` / `GITCG_REPORTS_DIR` 可覆盖 |
| `seeding.py` | `stable_hash()` / `derive_seed()`——BLAKE2b 31-bit，配对种子跨进程、跨机、免疫 Python hash randomization |
| `jsonl.py` | `append_jsonl` / `iter_jsonl` / `read_jsonl` / `dumps` / `ensure_trailing_newline`——一行一记录，dataclass/Enum/bytes/numpy 归一化，撕裂尾行容错（供断点续跑） |
| `lock.py` | `load_engine_lock()` / `runtime_metadata()`——D4 版本锁定，把引擎 commit + 卡池版本 + 运行时戳进每个 run 的元数据 |
| `llm.py` | OpenAI 兼容客户端（`LLMConfig` / `LLMClient` / `MockLLMClient` / `build_llm_client` / `extract_json_object` / `redact`）——配置解析顺序：构造参数 → 环境变量（`GITCG_LLM_*` / `DEEPSEEK_*`）→ 仓库 `.env`；不可用时返回 `None`（不抛异常），供调用方走降级链 |

## 约定

- **密钥永不入库/不进日志**：API key 只在 gitignored 的 `.env`；输出前用 `redact()`。
- **落盘统一走 `jsonl` 助手**：保持"一局/一事件一行"与尾行容错，是所有可续跑作业的前提。
- **路径统一走 `paths`**：禁止硬编码仓库根或相对 cwd 的路径。
- **种子统一走 `seeding`**：跨进程/跨机的配对与分片种子禁止用裸 `random`/`hash()`。
- **版本统一走 `lock`**：新 run 必须从 `configs/engine.lock` 拷贝并随元数据保存（D4）。

## 测试

`tests/test_jsonl.py`、`tests/test_llm.py`；`lock` 的指纹护栏经 `eval/` 与 `npc/` 的 `.meta.json` 用例间接覆盖。
