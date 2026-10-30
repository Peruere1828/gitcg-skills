#!/usr/bin/env bash
# engine-overlay/scripts/install-lib.sh
#
# 把 build/artifacts/libgitcg.so 接入 conda env gitcg：
#   1) 首次运行先把原始 .so 备份为 libgitcg.so.orig（幂等；已存在则不覆盖备份）
#   2) 用新产物替换 site-packages/gitcg/libgitcg.so
#   3) 冒烟：import gitcg + 开一局
#
# 用法：
#   bash engine-overlay/scripts/install-lib.sh           # 安装新 .so
#   bash engine-overlay/scripts/install-lib.sh --restore # 还原备份
#   CONDA_ENV=gitcg bash engine-overlay/scripts/install-lib.sh

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OVERLAY_DIR="$(cd "$HERE/.." && pwd)"
REPO_ROOT="$(cd "$OVERLAY_DIR/.." && pwd)"

CONDA_ENV="${CONDA_ENV:-gitcg}"
NEW_SO="${NEW_SO:-$REPO_ROOT/build/artifacts/libgitcg.so}"

PKG_DIR="$(conda run -n "$CONDA_ENV" python -c \
  "import os,gitcg;print(os.path.dirname(os.path.abspath(gitcg.__file__)))")"
TARGET="$PKG_DIR/libgitcg.so"
BACKUP="$PKG_DIR/libgitcg.so.orig"

if [ "${1:-}" = "--restore" ]; then
  [ -f "$BACKUP" ] || { echo "ERR: 无备份 $BACKUP" >&2; exit 1; }
  cp -f "$BACKUP" "$TARGET"
  echo "[install] 已还原 $TARGET"
  exit 0
fi

[ -f "$NEW_SO" ] || { echo "ERR: 缺产物 $NEW_SO（先构建）" >&2; exit 1; }
[ -f "$TARGET" ] || { echo "ERR: 目标不存在 $TARGET" >&2; exit 1; }

# 首次备份；备份文件与 wheels 里的原始 .so 同名同路径，便于回滚
if [ ! -f "$BACKUP" ]; then
  cp -a "$TARGET" "$BACKUP"
  echo "[install] 备份原 .so -> $BACKUP"
else
  echo "[install] 备份已存在，保留：$BACKUP"
fi

cp -f "$NEW_SO" "$TARGET"
echo "[install] 安装 $(stat -c%s "$TARGET") bytes -> $TARGET"

echo "[install] 冒烟：import gitcg"
conda run -n "$CONDA_ENV" python - <<'PY'
import gitcg
from gitcg import low_level
print("gitcg", gitcg.__version__ if hasattr(gitcg, "__version__") else "?", "loaded OK")
PY
