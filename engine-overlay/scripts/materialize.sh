#!/usr/bin/env bash
# engine-overlay/scripts/materialize.sh
#
# 物化引擎源码到 build/engine（gitignored），精确 pin 到 versions.env 的 ENGINE_TAG。
#
# 为什么不用 refs/ 的工作树直接 rsync：
#   refs/genius-invokation 是 **shallow（grafted）** 克隆，HEAD=6f01d5c1（2026-10-05）
#   比 v0.21.0 tag（c94826b，2026-09-24）**新 11 天**，且本地没有 tag 对象。
#   直接 rsync 会拿到 0.21.0 之后的 main，pybinding C 头可能与我们 wheel 0.21.0 的
#   cffi ABI（site-packages/gitcg/_gitcg_cffi.py）不一致。
#   因此这里从 upstream 精确取 tag 的树（浅取单 commit），refs/ 保持只读不修改。
#
# 离线/无网络时可改用 --from-refs 走 refs 工作树（**浮动 main，ABI 未验证**，仅排障用）。

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OVERLAY_DIR="$(cd "$HERE/.." && pwd)"
REPO_ROOT="$(cd "$OVERLAY_DIR/.." && pwd)"
# shellcheck source=../versions.env
. "$OVERLAY_DIR/versions.env"

DST="$REPO_ROOT/build/engine"
FROM_REFS=0
[ "${1:-}" = "--from-refs" ] && FROM_REFS=1

mkdir -p "$REPO_ROOT/build"

if [ "$FROM_REFS" = "1" ]; then
  SRC="$REPO_ROOT/refs/genius-invokation"
  [ -d "$SRC" ] || { echo "ERR: refs/genius-invokation 不存在" >&2; exit 1; }
  echo "[materialize] 从 refs 工作树 rsync（浮动 main，仅供排障，ABI 未验证）"
  command -v rsync >/dev/null || { echo "ERR: 需要 rsync" >&2; exit 1; }
  mkdir -p "$DST"
  rsync -a --delete \
    --exclude node_modules \
    --exclude .git \
    --exclude 'packages/*/dist' \
    --exclude 'packages/*/generated' \
    "$SRC/" "$DST/"
  echo "[materialize] refs HEAD=$(git -C "$SRC" rev-parse HEAD 2>/dev/null || echo unknown)"
else
  echo "[materialize] 精确取 tag $ENGINE_TAG ($ENGINE_COMMIT)"
  rm -rf "$DST"
  git init -q "$DST"
  git -C "$DST" remote add origin "$ENGINE_URL"
  git -C "$DST" fetch -q --depth 1 origin "refs/tags/$ENGINE_TAG:refs/tags/$ENGINE_TAG"
  git -C "$DST" checkout -q --detach "$ENGINE_TAG"
fi

HEAD_SHA="$(git -C "$DST" rev-parse HEAD)"
VER="$(sed -nE 's/^version[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' \
        "$DST/packages/pybinding/pyproject.toml" | head -1)"
echo "[materialize] HEAD=$HEAD_SHA pybinding_version=$VER -> $DST"

if [ "$FROM_REFS" = "0" ]; then
  if [ "$HEAD_SHA" != "$ENGINE_COMMIT" ]; then
    echo "ERR: HEAD $HEAD_SHA != 期望 $ENGINE_COMMIT" >&2; exit 1
  fi
fi
if [ "$VER" != "$ENGINE_PYBINDING_VERSION" ]; then
  echo "ERR: pybinding 版本 $VER != 目标 $ENGINE_PYBINDING_VERSION" >&2; exit 1
fi
echo "[materialize] OK"
