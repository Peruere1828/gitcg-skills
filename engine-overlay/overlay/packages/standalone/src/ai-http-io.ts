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
// along with this program.  If not, see <https://www.gnu.org/licenses/>.
//
// --- WS8/D18 overlay 新增：浏览器引擎 ↔ Python 的 HTTP 桥 ---------------------
// 把某个座位的 `CancellablePlayerIO` 实现为对本地 Python 服务的 HTTP 调用：
//   rpc(request)      -> POST {base}/rpc      body {session, who, request}   -> RpcResponse
//   notify(note)      -> POST {base}/notify   body {session, who, seq, notification}
//   cancelRpc() -> AbortController.abort()（回合被取消/终止时）
//
// 两个隔离要求（T1/T2）：
//   * **sessionId**：桥按 (session, who) 各存一份掩码状态、策略实例与决策日志，
//     自对弈与多开标签页都不会互相覆盖；
//   * **顺序**：notify 按座位串成队列、逐条带单调 `seq`，`rpc` 必须等队列排空
//     才发出去 —— 否则服务端可能先处理 rpc 再处理 notify，决策就吃到陈旧状态。
//   * **续局基线**：同一 session 上「从此处继续」时，新 io 的 `seq` 从 `/session`
//     返回的 `seq_base` 接着数（见 AiHttpIoOptions.seqBase），否则桥会把新局的
//     通知全判成过期。

import type {
  CancellablePlayerIO,
  Notification,
  RpcRequest,
  RpcResponse,
} from "@gi-tcg/core";

export interface AiHttpIoOptions {
  /** 例如 "http://127.0.0.1:8770"（不要带结尾斜杠） */
  baseUrl: string;
  /** 该 io 服务的座位（0 = 先手弹窗位，1 = 主窗口位） */
  who: 0 | 1;
  /** 一局一个的会话 id；不传则落到桥的默认会话 "-"（只建议调试时这样） */
  sessionId?: string;
  /**
   * 续局（「从此处继续」）的 seq 基线：本座位新一局的第一个 notify 用
   * `seqBase + 1`。同一个 session 上开新的一局时，浏览器侧的计数会从 1 重新数，
   * 而桥那边上一局的 `applied_seq` 还停在高位——不接着数的话新局通知全会被判
   * 「过期」丢掉，决策就只能读到被终止那一局的残局状态。基线由 `/session`
   * （`resume:true`）的 `seq_base` 给出；不传＝全新一局（从 0 起算）。
   */
  seqBase?: number;
  /** 是否把引擎通知（对局事件/状态）也 POST 给 Python，默认 true（AI 需要看盘） */
  forwardNotifications?: boolean;
  /** 调试日志回调 */
  onLog?: (direction: "→" | "←" | "!", method: string, payload?: unknown) => void;
}

const trimSlash = (s: string) => s.replace(/\/+$/, "");

// notify 单条超时（毫秒）：服务端卡住时靠 abort 收尾，保证 rpc 的等待队列不会
// 被一条挂死的通知永久堵住。
const NOTIFY_TIMEOUT_MS = 10_000;

export function createAiHttpIo(options: AiHttpIoOptions): CancellablePlayerIO {
  const base = trimSlash(options.baseUrl);
  const { who, forwardNotifications = true, onLog } = options;
  const session = options.sessionId;
  let controller: AbortController | null = null;

  // T2：同一座位的 notify 按序送达，且 rpc 发出前要等它们全部落地。
  // 链上每一步都吞掉错误（通知本来就是尽力而为），链条永不 reject。
  // 起始值 = 续局基线（见 AiHttpIoOptions.seqBase）：新局的第一个 notify 用
  // `seqBase + 1`，与桥侧上一局的 `applied_seq` 接上，不会被判过期。
  let notifySeq = Math.max(0, options.seqBase ?? 0);
  let pendingNotify: Promise<void> = Promise.resolve();

  const notify: CancellablePlayerIO["notify"] = (notification: Notification) => {
    if (!forwardNotifications) {
      return;
    }
    const seq = ++notifySeq;
    pendingNotify = pendingNotify.then(async () => {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), NOTIFY_TIMEOUT_MS);
      try {
        // 注意：不要加 `keepalive: true`——按 Fetch 规范，带 keepalive 的请求体
        // 上限被压到 64 KiB，大 notification（完整 GameState）会静默失败。
        const resp = await fetch(`${base}/notify`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ session, who, seq, notification }),
          signal: ctrl.signal,
        });
        if (!resp.ok) {
          const text = await resp.text().catch(() => "");
          onLog?.("!", "notify", `HTTP ${resp.status}: ${text}`);
        }
      } catch (e) {
        // 通知是尽力而为、不阻塞对局，但错误不能静默吞掉：交给上层日志/提示。
        onLog?.("!", "notify", e);
      } finally {
        window.clearTimeout(timer);
      }
    });
  };

  const rpc = async (request: RpcRequest): Promise<RpcResponse> => {
    // T2：等前面所有 notify 都拿到响应（即已在服务端落地）再问决策，
    // 否则 latest_state 可能还是上一步的。
    await pendingNotify;
    controller?.abort();
    const ctrl = new AbortController();
    controller = ctrl;
    const method = request.request?.$case ?? "unknown";
    onLog?.("→", method, request);
    let resp: Response;
    try {
      resp = await fetch(`${base}/rpc`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ session, who, request }),
        signal: ctrl.signal,
      });
    } catch (e) {
      onLog?.("!", method, e);
      throw e;
    }
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new Error(`AI HTTP ${resp.status} ${resp.statusText}: ${text}`);
    }
    const json = (await resp.json()) as RpcResponse;
    if (!json || typeof json !== "object" || !("response" in json)) {
      throw new Error(`AI HTTP invalid response: ${JSON.stringify(json)}`);
    }
    onLog?.("←", method, json);
    return json;
  };

  return {
    notify,
    rpc,
    cancelRpc: () => {
      controller?.abort();
      controller = null;
    },
  };
}
