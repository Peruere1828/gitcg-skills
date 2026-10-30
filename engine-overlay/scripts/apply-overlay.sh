#!/usr/bin/env bash
# engine-overlay/scripts/apply-overlay.sh
#
# 把我们自己的引擎改动（engine-overlay/overlay/ 下的目录树）覆盖到 build/engine/。
#
# overlay/ 里的路径是相对引擎仓根的镜像路径，例如要改敌人数据：
#   engine-overlay/overlay/packages/data/src/characters/pyro/<enemy>.gts
#   engine-overlay/overlay/packages/data/src/index.ts      （注册新敌人）
#   engine-overlay/overlay/packages/data/src/begin.ts      （如需）
#
# 应用后把 overlay 树的内容 hash 记录到 build/overlay-manifest.json，进 provenance。
# overlay/ 内的 .md/.gitkeep 不参与（只作说明）。

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OVERLAY_DIR="$(cd "$HERE/.." && pwd)"
REPO_ROOT="$(cd "$OVERLAY_DIR/.." && pwd)"
DST="$REPO_ROOT/build/engine"

[ -d "$DST" ] || { echo "ERR: 先跑 materialize.sh（缺 $DST）" >&2; exit 1; }

SRC="$OVERLAY_DIR/overlay"
COUNT=0
if [ -d "$SRC" ]; then
  while IFS= read -r -d '' f; do
    rel="${f#"$SRC"/}"
    case "$rel" in
      *.md|*.gitkeep|README.md) continue ;;
    esac
    mkdir -p "$DST/$(dirname "$rel")"
    cp -a "$f" "$DST/$rel"
    echo "[overlay] $rel"
    COUNT=$((COUNT + 1))
  done < <(find "$SRC" -type f -print0)
fi
echo "[overlay] 应用 $COUNT 个文件"

# overlay 新增的 `packages/data/src` 下 .gts/.ts 需要登记进 `index.ts` 导入表，
# 否则 tsdown 打包时不会 import 它们（见 register-data-imports.py 说明）。
if [ -f "$DST/packages/data/src/index.ts" ]; then
  python3 "$HERE/register-data-imports.py" "$DST"
fi

# provenance：overlay 内容 hash（仅数据文件）
MANIFEST="$REPO_ROOT/build/overlay-manifest.json"
python3 - "$SRC" "$MANIFEST" <<'PY'
import hashlib, json, os, sys
src, out = sys.argv[1], sys.argv[2]
files = {}
if os.path.isdir(src):
    for root, _dirs, names in os.walk(src):
        for n in names:
            if n.endswith((".md", ".gitkeep")):
                continue
            p = os.path.join(root, n)
            rel = os.path.relpath(p, src)
            files[rel] = hashlib.sha256(open(p, "rb").read()).hexdigest()
tree = hashlib.sha256(
    "".join(f"{k}:{v}\n" for k, v in sorted(files.items())).encode()
).hexdigest()
json.dump({"files": files, "overlay_tree_sha256": tree}, open(out, "w"), indent=2)
print(f"[overlay] overlay_tree_sha256={tree}")
PY
