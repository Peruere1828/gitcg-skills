#!/usr/bin/env bash
# scripts/serve_web_demo.sh
#
# 单端口可视化观战：standalone 前端（Vite）+ Python AI 桥。
#
# 浏览器只访问 `http://<本机 IP>:$PORT/`；前端把 `/bridge/*` 反代到本机
# `web/ai_rpc_server.py`（见 engine-overlay/overlay/packages/standalone/vite.config.ts），
# 所以局域网只需要放行一个端口。
#
# 用法（前台，Ctrl-C 一起退出）：
#   bash scripts/serve_web_demo.sh
#
# 后台常驻：
#   setsid nohup bash scripts/serve_web_demo.sh >/tmp/gitcg-web.log 2>&1 < /dev/null &
#
# 环境变量：
#   PORT          前端端口（默认 7001）
#   BRIDGE_PORT   Python 桥端口（默认 8770，仅本机监听，经前端反代）
#   POLICY        AI 座位的策略 spec（默认 llm:dynamic：LLM 技能智能体 +
#                 最新进化技能库；也可 llm:none / llm:llm_empty / llm:static，
#                 或脚本 expert:<slug> / heuristic）
#   AI_DECK       仅非评价调试用：桥声明的 AI 牌组 slug（默认空；评价协议下
#                 由关卡 loadout/stage-decks 自动决定，不要设置）
#   SEED          策略种子（默认 20261112）
#   CONDA_ENV     Python 环境（默认 gitcg-skill）
#   STAGE_DECKS   关卡->牌组映射（默认 configs/stage_decks.json）
#   LOADOUT_ROOT  冻结 loadout 根（默认 reports/loadouts/v4，匹配 v2_dynamic run）
#   CARD_VERSION  卡池版本（默认 v3.3.0，匹配时代基准）
#   REASONING     AI 决策的 reasoning effort（默认 none；可选 low/medium/high，
#                 开启后回复预算提高到 8192 token，仅用于调试观察）
#   LLM_TRACE     模型调用 JSONL 落盘路径（完整 prompt + content +
#                 reasoning_content；默认 data/web/llm_trace.jsonl，
#                 设 none 关闭）。对应 Python 变量 GITCG_LLM_TRACE。
#
# 网页是调试接口：**人类执 NPC 座位，AI 执参赛者**，按关卡评测协议跑。
#   1. 「NPC 阵容」选一个关卡（这是人类要扮演的 NPC）；
#   2. 桥据 eval 自动回填 AI 的参赛者牌组、卡池版本和关卡特殊规则；
#   3. 勾「AI 对手（HTTP 桥）」，「AI 技能库」选实际评测的组
#      （none/llm_empty/static/dynamic）；「双方 AI 自对弈」只在自动检查时用，
#      此时 NPC 座位走真实 `npc:<stage>` 策略；人类手动打时关掉它。
#   4. 开局后下方「AI 决策」面板实时显示 AI 引用的技能（检查自进化经验）。
# 严格对齐：LLM 策略、提示词、库、基础策略、候选顺序/上限均由
# `skillloop.run.build_policy` 决定（见 web/README.md）。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
cd "$REPO_ROOT"

PORT="${PORT:-7001}"
BRIDGE_PORT="${BRIDGE_PORT:-8770}"
POLICY="${POLICY:-llm:dynamic}"
AI_DECK="${AI_DECK:-}"
SEED="${SEED:-20261112}"
CONDA_ENV="${CONDA_ENV:-gitcg-skill}"
# 严格对齐实际评测：默认值与 data/runs/v2_dynamic.jsonl.meta.json 一致。
# 跑别的 run 时用环境变量覆盖（例如 LOADOUT_ROOT=reports/loadouts/v1）。
STAGE_DECKS="${STAGE_DECKS:-configs/stage_decks.json}"
LOADOUT_ROOT="${LOADOUT_ROOT:-reports/loadouts/v4}"
CARD_VERSION="${CARD_VERSION:-v3.3.0}"
REASONING="${REASONING:-none}"
# 可选：把每次模型调用的完整 prompt / content / reasoning_content 落成 JSONL
# （默认关；文件在 gitignore 的 data/ 或 /tmp 下，不含 key）。
LLM_TRACE="${LLM_TRACE:-data/web/llm_trace.jsonl}"
case "$LLM_TRACE" in
  ""|none|off|0) unset GITCG_LLM_TRACE 2>/dev/null || true ;;
  *) export GITCG_LLM_TRACE="$LLM_TRACE" ;;
esac

BRIDGE_PID=""
VITE_PID=""

cleanup() {
  trap - EXIT INT TERM
  # 两个子进程各自 `setsid` 成独立进程组：负 PID 才能连 conda→python、
  # bash→pnpm→vite 的整棵子树一起收掉（只 kill 顶层会留下孤儿占端口）。
  [ -n "$VITE_PID" ] && kill -- -"$VITE_PID" 2>/dev/null || true
  [ -n "$BRIDGE_PID" ] && kill -- -"$BRIDGE_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

AI_DECK_ARG=()
if [ -n "$AI_DECK" ]; then AI_DECK_ARG=(--ai-deck "$AI_DECK"); fi
echo "[web-demo] AI bridge → 127.0.0.1:${BRIDGE_PORT} (env=${CONDA_ENV}, policy=${POLICY}, ai_deck=${AI_DECK:-<评价协议自动>}, stage_decks=${STAGE_DECKS}, loadout=${LOADOUT_ROOT}, card_version=${CARD_VERSION}, reasoning=${REASONING})"
setsid conda run --no-capture-output -n "$CONDA_ENV" \
  python web/ai_rpc_server.py \
  --host 127.0.0.1 --port "$BRIDGE_PORT" \
  --policy "$POLICY" "${AI_DECK_ARG[@]}" --seed "$SEED" \
  --stage-decks "$STAGE_DECKS" --loadout-root "$LOADOUT_ROOT" \
  --card-version "$CARD_VERSION" --reasoning "$REASONING" &
BRIDGE_PID=$!

echo "[web-demo] standalone frontend → 0.0.0.0:${PORT} (反代 /bridge → 127.0.0.1:${BRIDGE_PORT})"
setsid env PORT="$PORT" HOST=0.0.0.0 BRIDGE_URL="http://127.0.0.1:${BRIDGE_PORT}" \
  bash engine-overlay/scripts/standalone.sh dev &
VITE_PID=$!

wait -n "$BRIDGE_PID" "$VITE_PID"
