#!/usr/bin/env bash
# engine-overlay/scripts/materialize-v8.sh
#
# 预取 V8 构建 wrapper（piovium/v8-cmake @ versions.env:V8CMAKE_COMMIT，内含 vendor 的
# V8 源码）到 build/v8-cmake-src，供 FetchContent 用 -DFETCHCONTENT_SOURCE_DIR_V8-CMAKE
# 直接引用，从而跳过 FetchContent 的非浅克隆（本机 GitHub 吞吐会卡在 ~120K）。
#
# 幂等：源目录已 pin 到该 commit 且 V8 版本匹配则跳过。

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OVERLAY_DIR="$(cd "$HERE/.." && pwd)"
REPO_ROOT="$(cd "$OVERLAY_DIR/.." && pwd)"
# shellcheck source=../versions.env
. "$OVERLAY_DIR/versions.env"

DST="$REPO_ROOT/build/v8-cmake-src"
VERFILE="$DST/v8/include/v8-version.h"

check_version() {
  python3 - "$VERFILE" "$V8_VERSION" <<'PY'
import re, sys
path, want = sys.argv[1], sys.argv[2]
txt = open(path).read()
def g(k):
    m = re.search(rf"#define {k}\s+(\d+)", txt)
    return m.group(1) if m else "?"
got = f"{g('V8_MAJOR_VERSION')}.{g('V8_MINOR_VERSION')}.{g('V8_BUILD_NUMBER')}.{g('V8_PATCH_LEVEL')}"
print(got, file=sys.stderr)
sys.exit(0 if got == want else 1)
PY
}

if [ -f "$VERFILE" ]; then
  if check_version 2>/dev/null; then
    echo "[materialize-v8] 已就绪：$DST (V8 $V8_VERSION)"
    exit 0
  fi
fi

echo "[materialize-v8] 浅取 $V8CMAKE_REPO @ $V8CMAKE_COMMIT"
rm -rf "$DST"
mkdir -p "$DST"
git init -q "$DST"
git -C "$DST" remote add origin "$V8CMAKE_REPO"
git -C "$DST" fetch --depth 1 origin "$V8CMAKE_COMMIT"
git -C "$DST" checkout -q FETCH_HEAD

GOT="$(python3 -c "
import re; t=open('$VERFILE').read()
g=lambda k: re.search(r'#define '+k+r'\s+(\d+)', t).group(1)
print(f\"{g('V8_MAJOR_VERSION')}.{g('V8_MINOR_VERSION')}.{g('V8_BUILD_NUMBER')}.{g('V8_PATCH_LEVEL')}\")
")"
echo "[materialize-v8] wrapper HEAD=$(git -C "$DST" rev-parse HEAD) V8=$GOT"
[ "$GOT" = "$V8_VERSION" ] || { echo "ERR: V8 版本 $GOT != 期望 $V8_VERSION" >&2; exit 1; }
echo "[materialize-v8] OK"
