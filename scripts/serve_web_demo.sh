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
#   POLICY        AI 座位的策略 spec（默认 expert:superconduct_aggro）
#   SEED          策略种子（默认 20261112）
#   CONDA_ENV     Python 环境（默认 gitcg-skill）
#
# 页面里观战 AI vs NPC：
#   1. 「NPC 阵容」下拉选一个关卡（成为“我”的座位 = NPC 阵容）；
#   2. 勾「AI 对手（HTTP 桥）」+「双方 AI 自对弈」；
#   3. 「开始对局」。对面座位用桥声明的 expert 牌组，NPC 座位自动改派 heuristic。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
cd "$REPO_ROOT"

PORT="${PORT:-7001}"
BRIDGE_PORT="${BRIDGE_PORT:-8770}"
POLICY="${POLICY:-expert:superconduct_aggro}"
SEED="${SEED:-20261112}"
CONDA_ENV="${CONDA_ENV:-gitcg-skill}"

BRIDGE_PID=""
VITE_PID=""

cleanup() {
  trap - EXIT INT TERM
  [ -n "$VITE_PID" ] && kill "$VITE_PID" 2>/dev/null || true
  [ -n "$BRIDGE_PID" ] && kill "$BRIDGE_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[web-demo] AI bridge → 127.0.0.1:${BRIDGE_PORT} (env=${CONDA_ENV}, policy=${POLICY})"
conda run --no-capture-output -n "$CONDA_ENV" \
  python web/ai_rpc_server.py \
  --host 127.0.0.1 --port "$BRIDGE_PORT" \
  --policy "$POLICY" --seed "$SEED" &
BRIDGE_PID=$!

echo "[web-demo] standalone frontend → 0.0.0.0:${PORT} (反代 /bridge → 127.0.0.1:${BRIDGE_PORT})"
PORT="$PORT" HOST=0.0.0.0 BRIDGE_URL="http://127.0.0.1:${BRIDGE_PORT}" \
  bash engine-overlay/scripts/standalone.sh dev &
VITE_PID=$!

wait -n "$BRIDGE_PID" "$VITE_PID"
