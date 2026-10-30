#!/usr/bin/env bash
# engine-overlay/scripts/standalone.sh
#
# WS8/D18：构建/运行升级版 standalone 前端（浏览器跑 TS 引擎）。
#
# 前置：build/engine 已 materialize（materialize.sh）；assets-manager 的静态数据
# 走本机可达的 v5 镜像（默认 https://gi-tcg-assets-api-hf.guyutongxue.site/），
# 可用 GI_TCG_ASSETS_BASE_URL 覆盖。
#
# 用法：
#   bash engine-overlay/scripts/standalone.sh apply     # overlay -> build/engine
#   bash engine-overlay/scripts/standalone.sh build     # 构建 standalone 的全部 workspace 依赖
#   bash engine-overlay/scripts/standalone.sh dev       # 起 vite dev server（前台，先 apply overlay）
#   bash engine-overlay/scripts/standalone.sh preview   # 构建后本地预览产物（先 apply overlay）
#
# 注：dev/preview 都会先跑 apply-overlay.sh，避免改完 overlay 源码后仍服务
# build/engine/ 里的陈旧副本（改了 ai-http-io.ts 却看不到效果）。
#
# 起局：浏览器打开 http://localhost:5173/，可选「AI 对手（HTTP 桥）」；
# Python 服务见 web/ai_rpc_server.py。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OVERLAY_DIR="$(cd "$HERE/.." && pwd)"
REPO_ROOT="$(cd "$OVERLAY_DIR/.." && pwd)"
ENGINE="$REPO_ROOT/build/engine"

ASSETS_BASE_URL="${GI_TCG_ASSETS_BASE_URL:-https://gi-tcg-assets-api-hf.guyutongxue.site/}"

cmd="${1:-dev}"

[ -d "$ENGINE" ] || { echo "ERR: 缺 $ENGINE，先跑 engine-overlay/scripts/materialize.sh" >&2; exit 1; }

case "$cmd" in
  apply)
    bash "$HERE/apply-overlay.sh"
    ;;
  build)
    bash "$HERE/apply-overlay.sh"
    cd "$ENGINE"
    echo "[standalone] build workspace deps (assets base URL = $ASSETS_BASE_URL)"
    GI_TCG_ASSETS_BASE_URL="$ASSETS_BASE_URL" \
      DEFAULT_STATIC_DATA_API_BASE_URL="$ASSETS_BASE_URL" \
      pnpm --filter "@gi-tcg/standalone^..." build
    echo "[standalone] deps built"
    ;;
  dev)
    bash "$HERE/apply-overlay.sh"
    cd "$ENGINE/packages/standalone"
    echo "[standalone] vite dev (assets base URL = $ASSETS_BASE_URL)"
    GI_TCG_ASSETS_BASE_URL="$ASSETS_BASE_URL" pnpm dev
    ;;
  preview)
    bash "$HERE/apply-overlay.sh"
    cd "$ENGINE/packages/standalone"
    GI_TCG_ASSETS_BASE_URL="$ASSETS_BASE_URL" pnpm build
    GI_TCG_ASSETS_BASE_URL="$ASSETS_BASE_URL" pnpm preview
    ;;
  *)
    echo "usage: $0 {apply|build|dev|preview}" >&2
    exit 2
    ;;
esac
