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
// --- 只读监控：AI 座位的最近决策 / 引用技能 --------------------------------
// 每 2s 拉 `GET {baseUrl}/ai-decisions?session=<sid>&who=<who>&limit=20`，
// 展示模型选中的候选、引用了哪些技能、技能库版本、fallback/error、耗时。
// **纯观测**：不参与 rpc/notify，也不改变对局协议；端点失败只显示「监控不可用」，
// 绝不拦对局。后端尚未重启（404/连接失败）时同样安全降级。

import { For, Show, createSignal, onCleanup, onMount } from "solid-js";

export interface AiDecisionPanelProps {
  /** 桥地址（例如 `/bridge` 或 `http://127.0.0.1:8770`） */
  baseUrl: string;
  /** 本局 session id（与 /session 一致） */
  sessionId: string;
  /** AI 座位（0 | 1）；人类执 NPC 时为 OTHER_WHO */
  who: 0 | 1;
}

/** 面板内部的收窄后记录；外部 JSON 全部按 unknown 解析 */
interface DecisionRow {
  step: number | null;
  method: string;
  modelChoice: number | null;
  nCandidates: number | null;
  usedSkills: string[];
  libraryResolved: string | null;
  fallback: boolean;
  /** null = 旧日志没有该字段 */
  llmCalled: boolean | null;
  /** 本次模型调用的解析错误（与桥级 fallback 分开） */
  llmError: string | null;
  /** 供应商隐藏思考的摘录（协议无关，仅供观察） */
  reasoning: string;
  finishReason: string | null;
  error: string | null;
  latencyMs: number | null;
}

const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 5000;
const MAX_ROWS = 20;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function readNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function readString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function readBoolean(value: unknown): boolean {
  return value === true;
}

/** 技能名列表：后端优先 `used_skill_names`，旧记录是 `used_skills` */
function readSkillNames(value: unknown): string[] | null {
  if (!Array.isArray(value)) {
    return null;
  }
  const out: string[] = [];
  for (const item of value) {
    if (typeof item === "string") {
      out.push(item);
    } else if (isRecord(item)) {
      const name =
        readString(item.name) ??
        readString(item.skill) ??
        readString(item.skill_name);
      if (name) {
        out.push(name);
      }
    }
  }
  return out;
}

/**
 * 端点返回 `[...]` 或 `{decisions|items|records|rows|data: [...]}` 都接受。
 * 已知键都没有时返回 null：旧版/未挂载端点会回落到服务描述 JSON
 * （`{service, endpoints}`），这种 200 响应仍应按「监控不可用」处理。
 */
function extractRecords(raw: unknown): unknown[] | null {
  if (Array.isArray(raw)) {
    return raw;
  }
  if (!isRecord(raw)) {
    return null;
  }
  for (const key of ["decisions", "items", "records", "rows", "data"]) {
    const value = raw[key];
    if (Array.isArray(value)) {
      return value;
    }
  }
  if (Object.keys(raw).length === 0) {
    return [];
  }
  return null;
}

function readLibraryResolved(record: Record<string, unknown>): string | null {
  const library = record.library;
  if (isRecord(library)) {
    return readString(library.resolved);
  }
  return readString(record.library_resolved) ?? readString(library);
}

function parseRow(raw: unknown): DecisionRow | null {
  if (!isRecord(raw)) {
    return null;
  }
  const usedSkills =
    readSkillNames(raw.used_skill_names) ??
    readSkillNames(raw.used_skills) ??
    [];
  return {
    step:
      readNumber(raw.step) ??
      readNumber(raw.step_index) ??
      readNumber(raw.index) ??
      readNumber(raw.seq),
    method:
      readString(raw.method) ?? readString(raw.request) ?? readString(raw.rpc) ?? "?",
    modelChoice: readNumber(raw.model_choice),
    nCandidates: readNumber(raw.n_candidates),
    usedSkills,
    libraryResolved: readLibraryResolved(raw),
    fallback: readBoolean(raw.fallback),
    llmCalled: readBoolean(raw.llm_called),
    llmError: readString(raw.llm_error),
    reasoning: readString(raw.reasoning) ?? "",
    finishReason: readString(raw.finish_reason),
    error: readString(raw.error),
    latencyMs: readNumber(raw.latency_ms) ?? readNumber(raw.latency),
  };
}

function parseDecisions(raw: unknown): DecisionRow[] | null {
  const records = extractRecords(raw);
  if (records === null) {
    return null;
  }
  const rows: DecisionRow[] = [];
  for (const item of records) {
    const row = parseRow(item);
    if (row) {
      rows.push(row);
    }
  }
  return rows.slice(-MAX_ROWS);
}

const cellStyle = {
  padding: "0.15rem 0.4rem",
  "border-bottom": "1px solid rgba(128, 128, 128, 0.25)",
  "text-align": "left",
  "vertical-align": "top",
  "white-space": "nowrap",
} as const;

const skillsCellStyle = {
  ...cellStyle,
  "white-space": "normal",
  "min-width": "10rem",
} as const;

export function AiDecisionPanel(props: AiDecisionPanelProps) {
  const [rows, setRows] = createSignal<DecisionRow[]>([]);
  const [error, setError] = createSignal<string | null>(null);
  let disposed = false;
  let busy = false;
  let timer: number | undefined;
  let controller: AbortController | null = null;

  const endpoint = () => {
    const base = props.baseUrl.replace(/\/+$/, "");
    const query = new URLSearchParams({
      session: props.sessionId,
      who: String(props.who),
      limit: String(MAX_ROWS),
    });
    return `${base}/ai-decisions?${query.toString()}`;
  };

  const poll = async () => {
    if (disposed || busy) {
      return;
    }
    busy = true;
    controller = new AbortController();
    const timeout = window.setTimeout(() => controller?.abort(), POLL_TIMEOUT_MS);
    try {
      const resp = await fetch(endpoint(), { signal: controller.signal });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const raw: unknown = await resp.json();
      if (disposed) {
        return;
      }
      const parsed = parseDecisions(raw);
      if (parsed === null) {
        throw new Error("返回格式异常（无 decisions 字段）");
      }
      setRows(parsed);
      setError(null);
    } catch (e) {
      // 端点未挂载/超时/网络失败都只降级成「监控不可用」，不影响对局。
      if (!disposed) {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      window.clearTimeout(timeout);
      controller = null;
      busy = false;
    }
  };

  onMount(() => {
    void poll();
    timer = window.setInterval(() => void poll(), POLL_INTERVAL_MS);
  });
  onCleanup(() => {
    disposed = true;
    controller?.abort();
    if (timer !== undefined) {
      window.clearInterval(timer);
    }
  });

  return (
    <details open style={{ "margin-top": "0.75rem" }}>
      <summary style={{ cursor: "pointer" }}>
        AI 决策 / 技能引用（最近 {MAX_ROWS} 条，只读监控）
      </summary>
      <Show when={error()}>
        <div style={{ color: "#b00020", padding: "0.25rem 0.4rem" }}>
          监控不可用：{error()}
        </div>
      </Show>
      <Show when={!error() && rows().length === 0}>
        <div style={{ opacity: 0.7, padding: "0.25rem 0.4rem" }}>
          暂无决策记录。
        </div>
      </Show>
      <Show when={rows().length > 0}>
        <div style={{ "max-height": "16rem", "overflow-y": "auto" }}>
          <table
            style={{
              "border-collapse": "collapse",
              "font-size": "0.8rem",
              width: "100%",
            }}
          >
            <thead>
              <tr>
                <th style={cellStyle}>决策</th>
                <th style={cellStyle}>候选（选中/总数）</th>
                <th style={skillsCellStyle}>引用技能</th>
                <th style={cellStyle}>技能库</th>
                <th style={skillsCellStyle}>状态</th>
                <th style={cellStyle}>耗时</th>
              </tr>
            </thead>
            <tbody>
              <For each={rows()}>
                {(row) => (
                  <tr>
                    <td style={cellStyle}>
                      第 {row.step ?? "?"} 步 · {row.method}
                    </td>
                    <td style={cellStyle}>
                      {row.llmCalled === false
                        ? "expert"
                        : row.llmError
                          ? "解析失败"
                          : `${row.modelChoice ?? "?"} / ${row.nCandidates ?? "?"}`}
                    </td>
                    <td style={skillsCellStyle}>
                      {row.usedSkills.length > 0
                        ? row.usedSkills.join("、")
                        : "（无）"}
                    </td>
                    <td style={cellStyle}>{row.libraryResolved ?? "?"}</td>
                    <td style={skillsCellStyle}>
                      <Show when={row.llmError}>
                        <span style={{ color: "#b00020" }}>解析失败：{row.llmError}</span>
                        <span style={{ opacity: 0.7 }}>（已回退 expert）</span>
                      </Show>
                      <Show when={!row.llmError && row.fallback}>
                        <span style={{ color: "#b00020" }}>fallback</span>
                        <Show when={row.error}>
                          <span>：{row.error}</span>
                        </Show>
                      </Show>
                      <Show when={!row.llmError && !row.fallback && row.error}>
                        <span style={{ color: "#b00020" }}>{row.error}</span>
                      </Show>
                      <Show when={!row.llmError && !row.fallback && !row.error}>
                        <span style={{ opacity: 0.7 }}>
                          {row.llmCalled === false ? "expert" : "ok"}
                        </span>
                      </Show>
                      <Show when={row.finishReason === "length"}>
                        <div style={{ color: "#b00020" }}>回复被 max_tokens 截断</div>
                      </Show>
                      <Show when={row.reasoning}>
                        <details style={{ "margin-top": "0.2rem", "max-width": "34rem" }}>
                          <summary style={{ cursor: "pointer", opacity: 0.75 }}>
                            思考（{row.reasoning.length} 字）
                          </summary>
                          <pre
                            style={{
                              "white-space": "pre-wrap",
                              "max-height": "12rem",
                              "overflow-y": "auto",
                              "font-size": "0.7rem",
                              margin: "0.2rem 0 0",
                            }}
                          >
                            {row.reasoning}
                          </pre>
                        </details>
                      </Show>
                    </td>
                    <td style={cellStyle}>
                      {row.latencyMs === null ? "?" : `${row.latencyMs} ms`}
                    </td>
                  </tr>
                )}
              </For>
            </tbody>
          </table>
        </div>
      </Show>
    </details>
  );
}
