#!/usr/bin/env bash
# engine-overlay/scripts/build-inner.sh
#
# **容器内**构建：TS/数据构建 -> generated/js_code.cpp -> cmake/Ninja 链接 libgitcg.so。
# 由 build-in-docker.sh 以 `-v <repo>:/work -w /work` 拉起；也可直接在本机
# （有 node24+pnpm12+cmake>=3.25+ninja+g++ C++20 时）跑，只跑这一步即可。
#
# V8 构建方式（tag v0.21.0）：cbinding/CMakeLists.txt 用 FetchContent 引入
# piovium/v8-cmake（vendor 了 V8 11.6.189.4 源码），纯 CMake+Ninja 编译，
# **无 depot_tools / gclient / V8_WORKSPACE / V8_OFFLINE**。我们用
# -DFETCHCONTENT_SOURCE_DIR_V8-CMAKE 指向预取的 build/v8-cmake-src（见
# materialize-v8.sh），跳过 FetchContent 的非浅克隆。
#
# 环境变量：
#   BUILD_JOBS      并行度（默认 8；V8 编译峰值内存大，30G 机建议 8）
#   BUILD_STANDALONE=1  额外构建 standalone（WS8/C 线用；失败不影响 .so）
#   SKIP_PNPM=1         跳过 pnpm（js_code.cpp 已生成时，纯增量重链）
set -euo pipefail

ROOT="${ROOT:-/work}"
ENGINE="${ENGINE:-$ROOT/build/engine}"
BUILD_DIR="${BUILD_DIR:-$ROOT/build/engine-build}"
V8SRC="${V8_SRC:-$ROOT/build/v8-cmake-src}"
ART_DIR="${ART_DIR:-$ROOT/build/artifacts}"
JOBS="${BUILD_JOBS:-8}"

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

[ -d "$ENGINE/packages/cbinding" ] || { echo "ERR: 未物化引擎（缺 $ENGINE）；先 materialize.sh" >&2; exit 1; }
mkdir -p "$BUILD_DIR" "$ART_DIR"

CMAKE_ARGS=(
  -S "$ENGINE/packages/cbinding"
  -B "$BUILD_DIR"
  -G Ninja
  -DCMAKE_BUILD_TYPE=Release
)
if [ -d "$V8SRC/v8" ]; then
  CMAKE_ARGS+=("-DFETCHCONTENT_SOURCE_DIR_V8-CMAKE=$V8SRC")
else
  echo "WARN: 未找到预取 V8 wrapper（$V8SRC），FetchContent 将联网克隆（可能很慢）；先跑 materialize-v8.sh" >&2
fi
if [ -n "${CXX_OVERRIDE:-}" ]; then
  CMAKE_ARGS+=("-DCMAKE_CXX_COMPILER=$CXX_OVERRIDE")
fi

log "==== 阶段 1/3：TS/数据构建（js_code.cpp）===="
if [ "${SKIP_PNPM:-0}" != "1" ]; then
  cd "$ENGINE"
  export CI=1
  log "pnpm install --frozen-lockfile"
  pnpm install --frozen-lockfile
  # 注意：不能直接用上游的 `pnpm build "cbinding...,standalone..."`。
  # 那个 filter 会连带构建 @gi-tcg/data 的 devDependency @gi-tcg/assets-manager，
  # 而 assets-manager 的 build:data 会去 https://static-data.piovium.org 拉静态数据
  # （本机网络不可达）。assets-manager 不是 cbinding 构建所需（仅 data 的 devDep，
  # 用于 regenerate_data），故这里显式排除它。
  log "pnpm --recursive --filter={packages/cbinding}... --filter=!{packages/assets-manager} build"
  pnpm --recursive \
    --filter="{packages/cbinding}..." \
    --filter="!{packages/assets-manager}" \
    build
  if [ "${BUILD_STANDALONE:-0}" = "1" ]; then
    # WS8/C 线需要 standalone；standalone 真依赖 assets-manager（需要 static-data 网络），
    # 故这里单独尽力构建，失败不影响 .so（B0 不阻塞）。
    log "pnpm build \"standalone...\"（可选，失败不影响 .so）"
    pnpm build "standalone..." || log "WARN: standalone 构建失败（ws8 线另处理）"
  fi
else
  log "SKIP_PNPM=1，跳过"
fi
[ -f "$ENGINE/packages/cbinding/generated/js_code.cpp" ] \
  || { echo "ERR: 缺 generated/js_code.cpp（pnpm build 未成功）" >&2; exit 1; }
ls -l "$ENGINE/packages/cbinding/generated/js_code.cpp"

log "==== 阶段 2/3：cmake 配置（FetchContent v8-cmake，vendor V8 11.6）===="
cmake "${CMAKE_ARGS[@]}"

log "==== 阶段 3/3：编译 V8（首建：数千 TU，数十分钟）+ 链接 libgitcg.so ===="
log "cmake --build $BUILD_DIR -j$JOBS"
cmake --build "$BUILD_DIR" -j "$JOBS"

log "==== 导出产物 ===="
SO="$(find "$BUILD_DIR" -name 'libgitcg.so' -type f | head -1)"
[ -n "$SO" ] || { echo "ERR: 未找到 libgitcg.so" >&2; exit 1; }
cp -f "$SO" "$ART_DIR/libgitcg.so"
log "artifact: $ART_DIR/libgitcg.so ($(stat -c%s "$ART_DIR/libgitcg.so") bytes)"
file "$ART_DIR/libgitcg.so" || true
log "构建完成"
