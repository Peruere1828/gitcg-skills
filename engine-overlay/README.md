# engine-overlay — 引擎构建脚手架（PvE-B0，D20）

本目录是 **WS7 NPC PvE 调试系统 / WS8 Web 界面** 的引擎构建脚手架：在 Docker 内从
upstream 源码重建带我们数据的 `libgitcg.so`，接入 conda env `gitcg`。

> 纪律：`refs/genius-invokation` **只读**（PLAN §6.5），我们的改动只进
> `engine-overlay/`（入库）；构建工作区全在 `build/`（已 gitignore）。

```
engine-overlay/
├── Dockerfile                 # ubuntu:22.04 + cmake>=3.25 + node24 + pnpm12 + ninja + g++ C++20
├── versions.env               # 所有 pin（引擎 tag/commit、V8 wrapper/版本、node/pnpm/cmake、镜像 tag）
├── overlay/                   # 我们的引擎改动（引擎仓根目录镜像；B0 为空）
│   └── README.md              # B1 起敌人 .gts 的放置路径对照
└── scripts/
    ├── materialize.sh         # 物化引擎源码到 build/engine，精确 pin 到 v0.21.0
    ├── materialize-v8.sh      # 预取 V8 wrapper（piovium/v8-cmake，含 vendor 的 V8 源码）
    ├── apply-overlay.sh       # overlay/ -> build/engine/，并记 overlay hash
    ├── build-in-docker.sh     # 宿主入口：docker build + materialize + run 容器内构建
    ├── build-inner.sh         # 容器内：pnpm build -> cmake 配置 -> 编译 V8 -> 链接 .so
    └── install-lib.sh         # 备份原 .so + 安装新 .so 到 conda env gitcg（可 --restore）
```

## 一、为什么 pin 到 tag `v0.21.0` 而不是 refs 的 HEAD

已确认事实：

- PyPI `gitcg==0.21.0` 的 `libgitcg.so` 由 tag `v0.21.0`
  （commit `c94826b4611a19f0057d667f7db3b0f9aaf68c7e`，2026-09-24）构建。
- `refs/genius-invokation` 是 **shallow（grafted）克隆**：`HEAD=6f01d5c1`
  （2026-10-05）比该 tag **新 11 天**，且本地没有 tag 对象（`git tag` 只到 v0.17.3）。
- 该 tag 与 refs HEAD 的 C 公共头 `packages/cbinding/include/gitcg/gitcg.h`
  **逐字节相同**（`diff` 无输出），但 **CMakeLists 不同**：tag 用
  `piovium/v8-cmake`（vendored V8），HEAD 用 `v8-cmake-barbara`（depot_tools 拉
  V8 14.8）。用 HEAD 会 ABI/行为漂移且构建方式完全不同。

因此 `materialize.sh` 从 upstream **精确取 tag 单 commit**（不改 refs）。

> ABI 佐证：wheel 内 `libgitcg.so` 的字符串里含 **`11.6.189.4`**，正是 tag 所用
> `piovium/v8-cmake` wrapper 的 `v8/include/v8-version.h`，二者一致。

## 二、V8 怎么构建（重要，和直觉不同）

tag `v0.21.0` 的 `packages/cbinding/CMakeLists.txt` 用 FetchContent 引入
**`piovium/v8-cmake`@`14d0f727`** —— 这是 `bnoordhuis/v8-cmake` 的 fork，
**把 V8 源码（含 third_party）vendor 进仓库**，用纯 CMake+Ninja 直接编译
（目标：`v8_base_without_compiler` / `v8_compiler` / `v8_snapshot` /
`v8_inspector` / `v8_libplatform`）。

- **没有 depot_tools / gclient / `V8_WORKSPACE` / `V8_OFFLINE` / `V8_BUILD_JOBS`**。
- 不需要数 GB 的 GN/LLVM 下载；V8 版本 `11.6.189.4`。
- FetchContent 默认做**非浅克隆**，本机对 `piovium/v8-cmake`（~52MB 全历史）
  的克隆会卡在 ~120K；故 `materialize-v8.sh` 先浅取到 `build/v8-cmake-src`，
  构建时用 `-DFETCHCONTENT_SOURCE_DIR_V8-CMAKE=build/v8-cmake-src` 直接引用。

## 三、构建产物与接入

产物 `build/artifacts/libgitcg.so`（linux x64，glibc 2.35 口径，与上游 CI 一致）。
接入：

```sh
bash engine-overlay/scripts/install-lib.sh            # 备份 .so.orig + 安装 + import 冒烟
bash engine-overlay/scripts/install-lib.sh --restore  # 回滚到 wheel 原始 .so
```

安装目标：`site-packages/gitcg/libgitcg.so`（`gitcg/low_level.py` 用 `ffi.dlopen`
从自身目录加载）。首次运行会留一份 `libgitcg.so.orig`。

## 四、完整构建流程

前置：Docker、网络可达（github.com / registry.npmjs.org）、磁盘 ≥ 20 GB、
内存 ≥ 16 GB（30 G 用 `BUILD_JOBS=8`）。

```sh
mkdir -p build
# 长构建挂 setsid 跑，输出流式落盘（C8）
setsid bash engine-overlay/scripts/build-in-docker.sh 2>&1 | tee build/engine-build.log &
tail -f build/engine-build.log
```

三段：① 镜像构建；② `materialize.sh`（tag 取源）+ `materialize-v8.sh`（预取 V8
wrapper）+ `apply-overlay.sh`；③ 容器内 `pnpm install` →
`pnpm --recursive --filter={packages/cbinding}... --filter=!{packages/assets-manager} build`
（产出 `packages/cbinding/generated/js_code.cpp`）→
`cmake -S packages/cbinding -B /work/build/engine-build -G Ninja
-DCMAKE_BUILD_TYPE=Release -DFETCHCONTENT_SOURCE_DIR_V8-CMAKE=/work/build/v8-cmake-src`
→ `cmake --build /work/build/engine-build -j8`。

### 增量重建（改敌人数据用它）

```sh
SKIP_IMAGE=1 SKIP_MATERIALIZE=1 bash engine-overlay/scripts/build-in-docker.sh
```

- `build/engine-build` 持久在宿主：V8 的 `.o`/静态库命中 Ninja 增量，**不再重编 V8**，
  只重编 `js_code.cpp` + 重链 `libgitcg.so`（分钟级）。
- `build/v8-cmake-src`、`build/engine` 同样持久。
- 只跳过 pnpm（js_code.cpp 已生成）可再加 `SKIP_PNPM=1`。

## 五、已知网络阻断：`static-data.piovium.org`

上游 `pnpm build "cbinding...,standalone..."` 的 filter 会连带构建
**`@gi-tcg/assets-manager`**（`@gi-tcg/data` 的 devDep），而它的 `build:data` 会去
`https://static-data.piovium.org` 拉静态数据。该主机在本机网络 **不可达**
（Railway 主机，IPv4/IPv6 均 connect timeout）。

- `assets-manager` **不是** cbinding 构建所需（cbinding 的 `js_code.cpp` 只依赖
  `@gi-tcg/core`/`@gi-tcg/data`/`@gi-tcg/typings`/`@gi-tcg/utils`）。
- 故 `build-inner.sh` 用 `--filter=!{packages/assets-manager}` **显式排除**它，
  其余按上游口径构建。
- **影响**：WS8/C 线的 `standalone` 前端真依赖 assets-manager，需该网络或缓存才能构建
  （`BUILD_STANDALONE=1` 时会尽力构建但允许失败）。B0 不受影响。

## 六、provenance（D4 engine.lock 变体）

| 项 | 值 |
|---|---|
| 引擎源码 | `v0.21.0` / `c94826b4611a19f0057d667f7db3b0f9aaf68c7e` |
| pybinding 版本 | `0.21.0`（与 wheel 一致） |
| overlay hash | `build/overlay-manifest.json` 的 `overlay_tree_sha256` |
| V8 | `11.6.189.4`（wrapper `piovium/v8-cmake@14d0f727`，vendor V8 源码，CMake 直编） |
| 构建镜像 | `gitcg-engine-builder:22.04`（ubuntu:22.04 + cmake 3.31.10 / node 24.21.0 / pnpm 12.8.1 / g++ 11.4 / ninja 1.10.1） |

## 七、验收（§4.4 PvE-B0）

```sh
conda run -n gitcg python -m pytest -m "not slow" -q
```

## 八、失败 fallback（D20）

V8 构建失败时，NPC 环境可走 **Node 子进程 + TS core + `custom-data-loader`**
（免构建读取敌人数据）驱动；Web 线（WS8）不受影响。失败时**保留
`build/v8-cmake-src` 与 `build/engine-build` 与日志**，只做增量重试（重建只重编失败/改动部分）。

## 九、WS8 standalone 前端（D18）

WS8（Web 人机调试界面）不需要 V8 的 `.so`，直接复用引擎仓的 TS 前端
`packages/standalone/`（浏览器跑 `@gi-tcg/core` + `web-ui-core` + `deck-builder` +
`assets-manager`）。改动放 `overlay/packages/standalone/**`（含一个
`assets-manager/src/constants.ts` 的 base URL 兜底改动），工具链入口
`scripts/standalone.sh`：

```sh
bash engine-overlay/scripts/standalone.sh build   # 构建 standalone 全部 workspace 依赖
bash engine-overlay/scripts/standalone.sh dev     # http://localhost:5173/
```

静态数据/素材走本机可达镜像 `https://gi-tcg-assets-api-hf.guyutongxue.site/`
（上游默认 `static-data.piovium.org` 在本网络不可达），`GI_TCG_ASSETS_BASE_URL` 可覆盖。
另有 HTTP 桥原型（浏览器引擎 ↔ Python）见 `web/ai_rpc_server.py` 与
`overlay/packages/standalone/README.md`；协议/状态见 `web/README.md`。

## 构建记录（PvE-B0，2026-10-12）

首建（本机 12 核 / 30G，`BUILD_JOBS=8`，Docker 内）：

| 阶段 | 起始 → 结束 | 耗时 |
|---|---|---|
| TS/数据构建（`js_code.cpp`） | 08:52:40 → 08:53:04 | 24 s |
| cmake 配置（预取 wrapper，无下载） | 08:53:04 → 08:53:05 | 1 s |
| **V8 编译 + 链接 `libgitcg.so`（1232 个 TU）** | 08:53:05 → 09:12:21 | **19 min 16 s** |

- 产物：`build/artifacts/libgitcg.so`，**36,583,216 bytes**，
  `sha256=7e8b61cca0d6143fa9f1e68b89cf243b32c8b56677de6d3ff5197b2d626c061f`
  （wheel 原始 .so 36,591,416 bytes，`sha256=45ba6ddd…`）。
- 导出动态符号数：新产物 **48582**，与 wheel 原始 .so **逐数相同**（ABI 一致佐证）。
- `js_code.cpp`：`sha256=910a0a03fd45d8725976f07371016b4e8e0bf5c6ceaad5878831622cecbf4c43`
- overlay：空（`overlay_tree_sha256=e3b0c442…`，即空树 sha256）。
- 镜像 `gitcg-engine-builder:22.04`：cmake 3.31.10 / g++ 11.4.0 / node 24.21.0 /
  pnpm 12.8.1 / ninja 1.10.1。

验收（§4.4 PvE-B0，全部在新 `.so` 上通过）：

| 项 | 命令 | 结果 |
|---|---|---|
| 单测 | `python -m pytest -m "not slow"` | **162 passed, 30 deselected**（47.3 s） |
| 冒烟 + 重放一致 | `pytest tests/test_env_smoke.py tests/test_replay_consistency.py` | **4 passed**（34.6 s；同种子重放一致） |
| 跑通一局 | `envs.match.run_match`（superconduct_aggro vs unyielding_geo, seed=11） | winner=1, rounds=7, decisions=(42,48), fallbacks=(0,0), truncated=False, error=None |

安装：`site-packages/gitcg/libgitcg.so`（原文件备份为 `libgitcg.so.orig`）。

**增量重建验证**（首建后，`SKIP_IMAGE=1 SKIP_MATERIALIZE=1`）：全流程 **29 s**
（pnpm 重新生成 `js_code.cpp` + 重编 4 个 cbinding TU + 重链），**V8 未重编**；
产物与首建 **sha256 完全一致**（`7e8b61cc…`）、BuildID 一致 → 确定性可复现，
印证 D20「改敌人数据只做增量（rolldown + 重链，分钟级）」。

## 构建记录（PvE-B1 敌人移植，2026-10-12）

- overlay：**17 个 NPC 敌人 `.gts`**（`overlay/packages/data/src/characters/**`），
  `overlay_tree_sha256=47ed2afa7a944ad8c79f12872de84df92be7bbc18cefc16301bd58b3d764ab3d`。
  导入表由新增的 `scripts/register-data-imports.py` 自动重生成（`apply-overlay.sh` 调用）。
- 增量重建（`SKIP_IMAGE=1 SKIP_MATERIALIZE=1`）：全流程 **~30 s**，V8 未重编。
- 产物 `build/artifacts/libgitcg.so`，**36,620,080 bytes**，
  `sha256=82630a6e0d1f6a5e09306c41c131100e73b404a09c67deb1957c2feeade7e9ce`。
- 验收：`tests/test_npc_enemies.py`（**27 passed**：蒂玛乌斯三只行为抽检 +
  19 类敌人各跑满一局无错 + 3 深渊法师护罩挂载）；`pytest -m "not slow"` 全绿。
