// Copyright (C) 2026 Piovium Labs
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

import { IS_BETA } from "@gi-tcg/config";
import type { Language } from "./manager";

// --- WS8/D18 overlay 改动 ---------------------------------------------------
// 上游默认 `https://static-data.piovium.org/` 在本机网络不可达（connect timeout）。
// 这里把**兜底默认值**改成本机可达的 v5 镜像；仍保留两级覆盖：
//   1. 构建期 tsdown `env` / Vite `envPrefix` 注入的 import.meta.env.X
//   2. Node 侧 process.env.DEFAULT_STATIC_DATA_API_BASE_URL（build:data 用）
//     （以及本仓专用的 GI_TCG_ASSETS_BASE_URL，见 standalone/vite.config.ts）
export const DEFAULT_STATIC_DATA_API_BASE_URL: string =
  (globalThis as any).__GI_TCG_ASSETS_BASE_URL__ ||
  import.meta.env?.DEFAULT_STATIC_DATA_API_BASE_URL ||
  // @ts-expect-error Node.js typing
  globalThis.process?.env.GI_TCG_ASSETS_BASE_URL ||
  // @ts-expect-error Node.js typing
  globalThis.process?.env.DEFAULT_STATIC_DATA_API_BASE_URL ||
  "https://gi-tcg-assets-api-hf.guyutongxue.site/";

const preferredLanguage =
  globalThis?.navigator?.languages?.[0] ??
  globalThis?.navigator?.language ??
  globalThis?.Intl?.DateTimeFormat()?.resolvedOptions()?.locale ??
  "en-US";
export const DEFAULT_LANGUAGE: Language = preferredLanguage.startsWith("zh")
  ? "CHS"
  : "EN";
export const DEFAULT_VERSION: typeof IS_BETA extends true ? "beta" : "latest" =
  (IS_BETA ? "beta" : "latest") as any;
