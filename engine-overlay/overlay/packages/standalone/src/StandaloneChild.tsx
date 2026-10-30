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
//
// --- WS8/D18 overlay 改动：弹窗座位不再写死 seat0 ------------------------------
// 上游这里恒 `createClient(0, …)`，因为「人类恒坐 seat1、弹窗恒管 seat0」。
// 我们加了「先手三选 / 随机先手」（`humanSeat` 可能是 0），于是弹窗有一半的对局
// 会拿到错的座位视角：`exposeState(who, …)` 是按座位掩码的，把 seat1 视角的
// 状态当 seat0 渲染，会让弹窗方看不到自己的手牌/骰子。现在座位由父窗口经
// URL hash（`#opponent=<0|1>`）传过来，标题也跟着变。

import { createClient } from "@gi-tcg/web-ui-core";
import "@gi-tcg/web-ui-core/style.css";
import { onCleanup, onMount } from "solid-js";

/** 父窗口（`StandaloneParent.showPopup`）经 hash 传过来的座位号 */
const OPPONENT_SEAT: 0 | 1 = /(?:^|[#&])opponent=1\b/.test(window.location.hash)
  ? 1
  : 0;

export function StandaloneChild() {
  const [uiIo, Chessboard] = createClient(OPPONENT_SEAT, {
    onGiveUp: () => {
      window.opener?.postMessage({
        giTcg: "1.0",
        method: "giveUp",
      });
    },
  });

  async function messageHandler(e: MessageEvent) {
    const { data } = e;
    if (typeof data !== "object" || data === null) {
      return;
    }
    if (data.giTcg !== "1.0") {
      return;
    }
    if (!("method" in data)) {
      return;
    }
    switch (data.method) {
      case "notify": {
        if (!Array.isArray(data.params)) {
          return;
        }
        uiIo.notify(...(data.params as [any]));
        break;
      }
      case "rpc": {
        if (!Array.isArray(data.params)) {
          return;
        }
        const result = await uiIo.rpc(...(data.params as [any]));
        (e.source as WindowProxy)?.postMessage(
          {
            giTcg: "1.0",
            method: "rpc",
            result: JSON.parse(JSON.stringify(result)),
            id: data.id,
          },
          "*",
        );
        break;
      }
      case "cancelRpc": {
        uiIo.cancelRpc();
        break;
      }
    }
  }

  onMount(() => {
    window.addEventListener("message", messageHandler);
    window.opener?.postMessage({
      giTcg: "1.0",
      method: "initialized",
    });
  });

  onCleanup(() => {
    window.removeEventListener("message", messageHandler);
  });

  return (
    <div class="standalone-board-shell">
      <div class="title-row">
        <span class="title">
          {OPPONENT_SEAT === 0 ? "先手" : "后手"}方棋盘
        </span>
      </div>
      <Chessboard />
    </div>
  );
}
