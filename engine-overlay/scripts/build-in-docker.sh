#!/usr/bin/env bash
# engine-overlay/scripts/build-in-docker.sh
#
# 宿主入口（D20）：build image -> materialize 引擎 + V8 wrapper -> apply overlay ->
# 容器内构建 -> 导出 build/artifacts/libgitcg.so。
#
# 用法：
#   bash engine-overlay/scripts/build-in-docker.sh            # 全流程
#   SKIP_IMAGE=1 bash .../build-in-docker.sh                  # 复用已建镜像
#   SKIP_MATERIALIZE=1 bash .../build-in-docker.sh            # 复用 build/engine + v8-cmake-src
#   SKIP_PNPM=1 SKIP_MATERIALIZE=1 bash .../build-in-docker.sh  # 纯增量重链（改 .gts 后仍要 SKIP_PNPM=0）
#
# 长构建建议挂 tmux/setsid 跑并把输出 tee 到 build/engine-build.log：
#   setsid bash engine-overlay/scripts/build-in-docker.sh 2>&1 | tee build/engine-build.log &

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OVERLAY_DIR="$(cd "$HERE/.." && pwd)"
REPO_ROOT="$(cd "$OVERLAY_DIR/.." && pwd)"
# shellcheck source=../versions.env
. "$OVERLAY_DIR/versions.env"

mkdir -p "$REPO_ROOT/build/artifacts"

if [ "${SKIP_IMAGE:-0}" != "1" ]; then
  echo "[build-in-docker] docker build $IMAGE_TAG"
  PULL_FLAG=()
  [ "${DOCKER_PULL:-0}" = "1" ] && PULL_FLAG=(--pull)
  docker build "${PULL_FLAG[@]}" \
    --build-arg "NODE_MAJOR=$NODE_MAJOR" \
    --build-arg "PNPM_VERSION=$PNPM_VERSION" \
    --build-arg "CMAKE_PIP_SPEC=$CMAKE_PIP_SPEC" \
    -t "$IMAGE_TAG" "$OVERLAY_DIR"
fi

if [ "${SKIP_MATERIALIZE:-0}" != "1" ]; then
  bash "$HERE/materialize.sh"
  bash "$HERE/materialize-v8.sh"
fi
bash "$HERE/apply-overlay.sh"

echo "[build-in-docker] docker run（BUILD_JOBS=$BUILD_JOBS）"
docker run --rm -i \
  --user "$(id -u):$(id -g)" \
  -e HOME=/work/build/home \
  -e BUILD_JOBS="$BUILD_JOBS" \
  -e BUILD_STANDALONE="${BUILD_STANDALONE:-0}" \
  -e SKIP_PNPM="${SKIP_PNPM:-0}" \
  -e CXX_OVERRIDE="${CXX_OVERRIDE:-}" \
  -v "$REPO_ROOT:/work" \
  -w /work \
  "$IMAGE_TAG" \
  bash engine-overlay/scripts/build-inner.sh

echo "[build-in-docker] 产物：$REPO_ROOT/build/artifacts/libgitcg.so"
