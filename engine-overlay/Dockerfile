# engine-overlay/Dockerfile — D20 引擎构建镜像
#
# 基线 ubuntu:22.04：与上游 genius-invokation CI 的 Build-C job（ubuntu-22.04）
# 同 glibc 口径（glibc 2.35），产物 libgitcg.so 可在宿主 Ubuntu 24.04 的 conda
# env gitcg 下 dlopen。
#
# 镜像内含：
#   - build-essential（g++ / C++20）、ninja-build、git、python3、ca-certificates
#   - cmake >= 3.25（22.04 apt 只有 3.22，用 pip wheel 装 3.31.x）
#   - node 24 + pnpm 12（供 TS/数据构建产出 generated/js_code.cpp）
#
# V8 不在此镜像构建；镜像只提供工具链。V8 一个 workspace 缓存在宿主
# build/v8-workspace（挂载进容器），见 scripts/build-inner.sh。

FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive
ARG NODE_MAJOR=24
ARG PNPM_VERSION=12.8.1
ARG CMAKE_PIP_SPEC=cmake>=3.25,<4

# 基础工具链。g++-12 作为 C++20 兜底（默认仍是 22.04 的 gcc-11，与上游 CI 一致）。
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential \
      g++-12 \
      ninja-build \
      git \
      python3 \
      python3-pip \
      python3-venv \
      ca-certificates \
      curl \
      xz-utils \
      unzip \
      file \
      pkg-config \
      rsync \
      libatomic1 \
 && rm -rf /var/lib/apt/lists/*

# cmake >= 3.25（apt 的 3.22 不满足 packages/cbinding/CMakeLists.txt 的 3.25 下限）
RUN python3 -m pip install --no-cache-dir "${CMAKE_PIP_SPEC}"

# node >= 24（22.04 apt 的 nodejs 是 12.x）；V8 构建本身不需要 node，
# node 仅用于 pnpm + TS/数据构建。
RUN curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

# pnpm（根 package.json 声明 packageManager pnpm@12.0.0；用具名版本，不依赖 corepack 签名）
RUN npm install -g "pnpm@${PNPM_VERSION}" \
 && npm cache clean --force

RUN set -eux; \
    cmake --version | head -1; \
    ninja --version; \
    g++ --version | head -1; \
    python3 --version; \
    node --version; \
    pnpm --version
