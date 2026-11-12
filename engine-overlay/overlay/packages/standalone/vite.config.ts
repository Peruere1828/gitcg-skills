// Copyright (C) 2024-2025 Guyutongxue
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as
// published by the Free Software Foundation, either version 3 of the
// License, or (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <http://www.gnu.org/licenses/>.

import { defineConfig } from "vite";
import gts from "@gi-tcg/unplugin-gts/rolldown";
import solid from "vite-plugin-solid";
import babel from "@rollup/plugin-babel";
import { viteStaticCopy } from "vite-plugin-static-copy";

// --- WS8/D18 overlay 改动 ---------------------------------------------------
// 把 assets-manager 读取的 `DEFAULT_STATIC_DATA_API_BASE_URL` 暴露给浏览器
// （`@gi-tcg/assets-manager` 以 `development` 条件解析到 src/，在浏览器里读
// `import.meta.env?.DEFAULT_STATIC_DATA_API_BASE_URL`）。
// 通过 envPrefix + define 双重注入，命令行即可覆盖：
//   GI_TCG_ASSETS_BASE_URL=https://... pnpm --filter @gi-tcg/standalone dev
const ASSETS_BASE_URL =
  process.env.GI_TCG_ASSETS_BASE_URL ??
  process.env.DEFAULT_STATIC_DATA_API_BASE_URL ??
  "https://gi-tcg-assets-api-hf.guyutongxue.site/";

// --- WS8/D18 overlay 改动：同源反代 Python 桥 --------------------------------
// 前端默认桥地址是 `/bridge`（见 App.tsx），这里把 `/bridge/*` 去掉前缀后转给
// 本机 `web/ai_rpc_server.py`。`BRIDGE_URL` 可覆盖（例如桥起在别的端口）。
const BRIDGE_URL = process.env.BRIDGE_URL ?? "http://127.0.0.1:8770";
const bridgeProxy = {
  "/bridge": {
    target: BRIDGE_URL,
    changeOrigin: false,
    rewrite: (path: string) => path.replace(/^\/bridge/, ""),
  },
};

export default defineConfig({
  esbuild: {
    target: "ES2020",
  },
  envPrefix: ["VITE_", "DEFAULT_STATIC_DATA_API_BASE_URL"],
  define: {
    "import.meta.env.DEFAULT_STATIC_DATA_API_BASE_URL":
      JSON.stringify(ASSETS_BASE_URL),
    "globalThis.__GI_TCG_ASSETS_BASE_URL__": JSON.stringify(ASSETS_BASE_URL),
  },
  server: {
    // WS8/D18 overlay：默认绑 0.0.0.0，便于在 WSL 里跑、Windows 侧浏览器访问；
    // 如需仅本机可 `HOST=127.0.0.1 pnpm dev`。注意：真正的敏感面是
    // web/ai_rpc_server.py 的 HTTP 桥（能替 AI 座位决策），其 CORS 默认已
    // 收敛为 localhost/127.0.0.1，故这里暴露 dev server 不额外放大风险。
    host: process.env.HOST ?? true,
    port: Number(process.env.PORT ?? 5173),
    // WS8/D18 overlay：把 Python 桥（web/ai_rpc_server.py）挂在同源 `/bridge`
    // 下，单端口部署——浏览器只访问本页面端口，桥的请求由 dev server 反代到
    // 127.0.0.1:8770。这样局域网访问只需放行本页面端口，也不必配 CORS。
    proxy: bridgeProxy,
  },
  // `standalone.sh preview`（构建产物）同样走同源反代。
  preview: {
    host: process.env.HOST ?? true,
    port: Number(process.env.PORT ?? 4173),
    proxy: bridgeProxy,
  },
  plugins: [
    gts(),
    solid(),
    babel({
      babelHelpers: "bundled",
    }),
    viteStaticCopy({
      watch: null,
      silent: true,
      targets: [
        {
          src: "../data-code-analyzer/src/result.json",
          rename: "data-code-analyze-result.json",
          dest: "."
        }
      ]
    })
  ],
});
