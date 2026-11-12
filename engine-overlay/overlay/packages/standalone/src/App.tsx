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

import {
  For,
  Match,
  Show,
  Switch,
  createMemo,
  createSignal,
  onMount,
} from "solid-js";
import getData from "@gi-tcg/data";
import {
  CURRENT_VERSION,
  GameStateLogEntry,
  Version,
  deserializeGameStateLog,
  VERSIONS,
  Deck,
  Game,
  GameState,
} from "@gi-tcg/core";
import { StandaloneChild } from "./StandaloneChild";
import { StandaloneParent } from "./StandaloneParent";
import { DeckPreview } from "./DeckPreview";
import { IS_BETA, SERVER_HOST, WEB_CLIENT_BASE_PATH } from "@gi-tcg/config";
import { DeckBuilder } from "@gi-tcg/deck-builder";
import "@gi-tcg/deck-builder/style.css";
import "@gi-tcg/state-editor/style.css";
import {
  AssetsManager,
  DEFAULT_ASSETS_MANAGER,
  type CustomData,
} from "@gi-tcg/assets-manager";
import { GameStateEditor } from "@gi-tcg/state-editor";
import type { StageRulesPayload } from "./stage-rules";

enum GameMode {
  NotStarted = 0,
  Standalone = 1,
  Editor = 2,
}

const INIT_DECK0 =
  "FZDByRUNGRCB0WoNFlGgWpEPE0AB9TAPFGCB9kgWGIERCoEQDLFADcQQDPFgacYWDJAA";
const INIT_DECK1 =
  "FdHxNj8TAWDQxFkMFkAhyWIYCYDA45wOCUDQ5J0PGEAh9IIPGWDh9p4YC6FgirYRCxAA";

const EMPTY_DECK: Deck = { characters: [], cards: [] };

/**
 * 评价协议：`GET /npc-decks` 每条阵容附带的评测口径（后端契约）。
 * 旧版桥没有 eval 字段 → 保持 undefined，前端所有回填/警告安全跳过。
 */
interface NpcDeckEval {
  stage: string;
  card_version: string | null;
  deck: string | null;
  characters: number[] | null;
  cards: number[] | null;
  rules: StageRulesPayload | null;
  /** 冻结的 loadout 文件（static/dynamic 才有；仅展示） */
  loadout?: string | null;
}

/** C3：`GET /npc-decks` 的一条 NPC 阵容（人数不限 3，出战+预备原样） */
interface NpcDeck {
  slug: string;
  display: string;
  region: string;
  enemies: string[];
  reserve_enemies: string[];
  characters: number[];
  n_on_field: number;
  n_support: number;
  total: number;
  cards: number[];
  /** 评价协议：关卡/卡池版本/评测牌组/特殊规则（后端新增，可缺省） */
  eval?: NpcDeckEval | null;
}

interface NpcPayload {
  decks: NpcDeck[];
  cards_source?: string;
  semantics: Record<string, string>;
  /** C3：NPC 的 CustomData（名字/技能描述/骰子费用），喂给 AssetsManager */
  assets?: CustomData;
}

/** `GET /ai` 返回的技能库摘要（selection 为选项值，resolved 为实际加载的库） */
interface AiLibraryInfo {
  selection: string;
  resolved: string;
  path: string;
  version: number;
  skills: number;
  built_at: string;
  kind: string;
}

/** `GET /ai` 的技能库下拉选项 */
interface AiLibraryOption {
  value: string;
  label: string;
}

/** `GET /ai` 的 AI 引擎/技能库状态；外部数据解析后收窄成这个形状 */
interface AiStatus {
  mode: string;
  spec: string;
  llm_available: boolean;
  llm_error: string | null;
  model: string;
  library: AiLibraryInfo | null;
  options: AiLibraryOption[];
  /** 评价协议状态（后端新增；旧版桥没有这些字段） */
  stage?: string | null;
  parity?: string | boolean | null;
  protocol?: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function readString(
  source: Record<string, unknown>,
  key: string,
  fallback = "",
): string {
  const value = source[key];
  return typeof value === "string" ? value : fallback;
}

function parseAiLibrary(raw: unknown): AiLibraryInfo | null {
  if (!isRecord(raw) || typeof raw.selection !== "string") {
    return null;
  }
  return {
    selection: raw.selection,
    resolved: readString(raw, "resolved"),
    path: readString(raw, "path"),
    version: typeof raw.version === "number" ? raw.version : 0,
    skills: typeof raw.skills === "number" ? raw.skills : 0,
    built_at: readString(raw, "built_at"),
    kind: readString(raw, "kind"),
  };
}

function parseAiOptions(raw: unknown): AiLibraryOption[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw.flatMap((item) => {
    if (
      !isRecord(item) ||
      typeof item.value !== "string" ||
      typeof item.label !== "string"
    ) {
      return [];
    }
    return [{ value: item.value, label: item.label }];
  });
}

function parseAiStatus(raw: unknown): AiStatus | null {
  if (!isRecord(raw)) {
    return null;
  }
  return {
    mode: readString(raw, "mode"),
    spec: readString(raw, "spec"),
    llm_available:
      typeof raw.llm_available === "boolean" ? raw.llm_available : true,
    llm_error: typeof raw.llm_error === "string" ? raw.llm_error : null,
    model: readString(raw, "model"),
    library: parseAiLibrary(raw.library),
    options: parseAiOptions(raw.options),
    stage: typeof raw.stage === "string" ? raw.stage : null,
    parity:
      typeof raw.parity === "boolean" || typeof raw.parity === "string"
        ? raw.parity
        : null,
    protocol: typeof raw.protocol === "string" ? raw.protocol : null,
  };
}

/** 当前选项值：优先 library.selection，其次从 `llm:dynamic` 这类 spec 里取 */
function aiSelectionOf(status: AiStatus): string {
  if (status.library?.selection) {
    return status.library.selection;
  }
  // `/ai?session=` 的 spec 可能带内部编码 `llm:none#deck=<slug>`，丢掉 # 片段。
  return status.spec.startsWith("llm:")
    ? status.spec.slice(4).split("#", 1)[0]
    : status.spec;
}

/** C1：`POST /choose-deck` 的反选结果 */
interface CounterPick {
  slug: string;
  characters: number[];
  cards: number[];
  policy: string;
  method: string;
  proxy: string | null;
  similarity: number | null;
  win_rate: number | null;
  reason: string;
  policy_applied?: boolean;
  base_policy?: string | null;
}

/** 开局时解析好的配置（牌组已解成对象、先手位已定） */
interface GameConfig {
  sessionId: string;
  myDeck: Deck;
  otherDeck: Deck;
  humanSeat: 0 | 1;
}

/** 先手三选：人类 / AI（对面）/ 随机 —— 引擎 seat0 恒为先手 */
type FirstTurn = "human" | "other" | "random";

function newSessionId(): string {
  return typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

if (import.meta.env.DEV) {
  // Debug here!
}

export function App() {
  if (window.opener !== null) {
    // eslint-disable-next-line solid/components-return-once
    return <StandaloneChild />;
  }
  const [mode, setMode] = createSignal<GameMode>(GameMode.NotStarted);
  const [logs, setLogs] = createSignal<GameStateLogEntry[]>();
  const [version, setVersion] = createSignal<Version>(CURRENT_VERSION);

  // 牌组：两个输入框存分享码；NPC 阵容（C3）编不出分享码，走对象覆盖。
  const [myDeckCode, setMyDeckCode] = createSignal(INIT_DECK1);
  const [otherDeckCode, setOtherDeckCode] = createSignal(INIT_DECK0);
  // 用户手改过对面牌组 → 开局时不再自动反选（尊重人的选择）
  const [otherDeckEdited, setOtherDeckEdited] = createSignal(false);
  // 评价协议：AI 牌组由所选 NPC 阵容的 `eval` 回填（不是用户手改）。
  // 与 `otherDeckEdited` 分开：切到另一个关卡时还能继续回填，但手改优先。
  const [otherDeckFromEval, setOtherDeckFromEval] = createSignal(false);
  const [npcPick, setNpcPick] = createSignal<NpcDeck | null>(null);
  const [npcList, setNpcList] = createSignal<NpcPayload | null>(null);
  const [npcError, setNpcError] = createSignal<string | null>(null);
  // C1 反选结果（只做展示；真正生效在开局时的 /session）
  const [counterPick, setCounterPick] = createSignal<CounterPick | null>(null);
  // 先手三选：人类 / AI / 随机
  const [firstTurn, setFirstTurn] = createSignal<FirstTurn>("random");
  // WS8/D18 overlay：AI 对手（HTTP 桥）开关与地址。
  // 默认走**同源** `/bridge`（vite 反代到本机 Python 桥）：局域网另一台机器访问
  // 本页面时，`127.0.0.1` 指向的是那台机器；同源相对路径才能落到这个前端所在的
  // 主机上，也省掉 CORS。
  const [aiOpponent, setAiOpponent] = createSignal(false);
  const [aiServerUrl, setAiServerUrl] = createSignal(
    `${window.location.origin}/bridge`,
  );
  const [aiSelfPlay, setAiSelfPlay] = createSignal(false);
  // T3：桥声明的 AI 座位牌组 slug（拿到后回填到「AI 牌组」）
  const [aiDeckSlug, setAiDeckSlug] = createSignal<string | null>(null);
  // 桥的 AI 引擎模式与技能库状态（GET /ai；桥未起时不可用，不拦开局）
  const [aiStatus, setAiStatus] = createSignal<AiStatus | null>(null);
  const [aiStatusError, setAiStatusError] = createSignal<string | null>(null);
  const [aiLibrarySelection, setAiLibrarySelection] = createSignal("");
  const [aiLibraryBusy, setAiLibraryBusy] = createSignal(false);
  const [aiLibraryNote, setAiLibraryNote] = createSignal<string | null>(null);
  const [aiLibraryError, setAiLibraryError] = createSignal<string | null>(null);
  // 开局解析完的配置；切到 Standalone 前必须有值
  const [gameCfg, setGameCfg] = createSignal<GameConfig | null>(null);

  const bridgeBase = () => aiServerUrl().replace(/\/+$/, "");

  // C3/D18：NPC 各阵容的角色列表（原样，含重复）。座位牌组的角色与之完全一致
  // （且无行动牌）就按 NPC 牌组处理 —— StandaloneParent 据此给该座位 8 个万能骰。
  // 人持 NPC 阵容（下面的「NPC 阵容」下拉 → `npcPick`）走的就是这条路。
  const npcLineups = createMemo(
    () => (npcList()?.decks ?? []).map((d) => d.characters ?? []),
  );

  // C3/P5：NPC/引擎数据的 AssetsManager。组牌器和牌组预览原本回退官方镜像
  // （2301 静态 hp=11），换成它以后静态详情也显示引擎 .gts 的 13。
  const npcAssetsManager = createMemo(() => {
    const assets = npcList()?.assets;
    if (!assets?.characters?.length) {
      return DEFAULT_ASSETS_MANAGER;
    }
    return new AssetsManager({ language: "CHS", customData: [assets] });
  });

  const deckOrEmpty = (code: string): Deck => {
    try {
      return DEFAULT_ASSETS_MANAGER.decode(code);
    } catch {
      return EMPTY_DECK;
    }
  };
  /** 开局用：解析失败要拦住人（日志导入/摆盘器路径用 deckOrEmpty 静默兜底） */
  const myDeck = (): Deck | null => {
    if (npcPick()) {
      return npcPick() as Deck;
    }
    try {
      return DEFAULT_ASSETS_MANAGER.decode(myDeckCode());
    } catch (e) {
      console.error(e);
      alert(`我的牌组分享码无法解析：${e instanceof Error ? e.message : e}`);
      return null;
    }
  };
  const otherDeck = (): Deck | null => {
    try {
      return DEFAULT_ASSETS_MANAGER.decode(otherDeckCode());
    } catch (e) {
      console.error(e);
      alert(`对面牌组分享码无法解析：${e instanceof Error ? e.message : e}`);
      return null;
    }
  };
  const encodeDeck = (deck: Deck): string | null => {
    try {
      return DEFAULT_ASSETS_MANAGER.encode(deck);
    } catch (e) {
      // NPC 角色 id 不在官方 share_id 表里 → 编不出分享码，交由对象直传。
      console.warn("[deck] encode 失败（NPC 角色编不出分享码是预期的）", e);
      return null;
    }
  };
  const deckKey = (values: readonly number[]): string =>
    [...values].sort((a, b) => a - b).join(",");
  /** 牌组内容是否与评价牌组一致（角色/行动牌各自按多重集比较） */
  const sameDeckContents = (
    deck: Deck,
    evaluation: { characters: number[]; cards?: number[] },
  ): boolean =>
    deckKey(deck.characters) === deckKey(evaluation.characters ?? []) &&
    deckKey(deck.cards ?? []) === deckKey(evaluation.cards ?? []);

  /**
   * 评价协议：选了 NPC 阵容就按它的 `eval` 回填 AI 牌组分享码与卡池版本。
   * 旧版桥没有 `eval` → 全部安全跳过；用户手改过 AI 牌组 → 不覆盖。
   */
  const applyNpcEval = (deck: NpcDeck | null) => {
    const evaluation = deck?.eval;
    if (!evaluation || otherDeckEdited()) {
      // 缺 eval（旧版桥）或用户手改过 AI 牌组 → 不动版本/牌组。
      return;
    }
    if (evaluation.characters?.length) {
      const code = encodeDeck({
        characters: evaluation.characters,
        cards: evaluation.cards ?? [],
      });
      if (code) {
        setOtherDeckCode(code);
        // 评测牌组是固定口径：标记来源，开局时不让 /choose-deck 反选覆盖。
        setOtherDeckFromEval(true);
      } else {
        console.warn(
          `[npc-eval] 评价牌组 ${evaluation.deck} 编不出分享码，保留当前 AI 牌组`,
        );
      }
    }
    const cardVersion = evaluation.card_version;
    if (!cardVersion) {
      return;
    }
    if ((VERSIONS as readonly string[]).includes(cardVersion)) {
      setVersion(cardVersion as Version);
    } else {
      console.warn(`[npc-eval] 未知卡池版本 ${cardVersion}，保持当前版本`);
    }
  };
  /** seat0 恒为先手：先手三选在这里落成具体座位 */
  const resolveHumanSeat = (): 0 | 1 => {
    const choice = firstTurn();
    if (choice === "human") return 0;
    if (choice === "other") return 1;
    return Math.random() < 0.5 ? 0 : 1;
  };

  const fetchAiDeck = async () => {
    try {
      const resp = await fetch(`${bridgeBase()}/ai-deck`);
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as {
        slug: string | null;
        characters: number[];
        cards: number[];
      };
      if (!data?.slug) {
        setAiDeckSlug(null);
        return;
      }
      if (npcPick()?.eval && otherDeckFromEval()) {
        // 评价协议下 AI 牌组由关卡 `eval` 固定：只记录桥声明的 slug（用于
        // 不一致警告），不拿桥的默认牌组覆盖评测牌组。
        setAiDeckSlug(data.slug);
        return;
      }
      const code = encodeDeck({ characters: data.characters, cards: data.cards });
      if (code) {
        setOtherDeckCode(code);
        // 桥声明的牌组是**显式选择**：标记手改过，开局时不再被自动反选覆盖
        // （否则 expert:<slug> 的牌组会被反选成别的池内套，脚本专家被改派）。
        setOtherDeckEdited(true);
        setOtherDeckFromEval(false);
      }
      setAiDeckSlug(data.slug);
    } catch (e) {
      // 取不到不拦开局：只是没做牌组对齐（桥侧 /session 还会再校验一次）。
      console.warn("[ai-deck] 取 AI 牌组失败", e);
      setAiDeckSlug(null);
    }
  };

  /** C1 反选：把「我的牌组」发给桥，按对位矩阵挑对面牌组并回填 */
  const chooseCounterPick = async (
    session?: string,
  ): Promise<CounterPick | null> => {
    const mine = myDeck();
    if (!mine) {
      return null;
    }
    try {
      const resp = await fetch(`${bridgeBase()}/choose-deck`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          session: session ?? "-",
          deck: { characters: mine.characters, cards: mine.cards },
        }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const pick = (await resp.json()) as CounterPick;
      if (!pick?.characters) {
        throw new Error(`反选返回异常：${JSON.stringify(pick)}`);
      }
      const code = encodeDeck({ characters: pick.characters, cards: pick.cards });
      if (code) {
        setOtherDeckCode(code);
        setOtherDeckEdited(false);
        setOtherDeckFromEval(false);
      }
      setCounterPick(pick);
      return pick;
    } catch (e) {
      console.warn("[choose-deck] 反选失败", e);
      setCounterPick(null);
      return null;
    }
  };

  /** C3：拉全部 NPC 阵容（人数不限 3，出战+预备原样；桥未启动会失败但不拦开局） */
  const loadNpcDecks = async () => {
    try {
      const resp = await fetch(`${bridgeBase()}/npc-decks`);
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const payload = (await resp.json()) as NpcPayload;
      if (!payload?.decks?.length) {
        throw new Error(`返回异常：${JSON.stringify(payload).slice(0, 200)}`);
      }
      setNpcList(payload);
      setNpcError(null);
    } catch (e) {
      console.warn("[npc-decks] 拉取 NPC 阵容失败", e);
      setNpcError(e instanceof Error ? e.message : String(e));
    }
  };

  /** `GET /ai`：拉桥的 AI 引擎与技能库状态；取不到只提示，不拦开局 */
  const loadAiStatus = async () => {
    try {
      const resp = await fetch(`${bridgeBase()}/ai`);
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const raw: unknown = await resp.json();
      const status = parseAiStatus(raw);
      if (!status) {
        throw new Error(`返回异常：${JSON.stringify(raw).slice(0, 200)}`);
      }
      setAiStatus(status);
      setAiStatusError(null);
      setAiLibrarySelection(aiSelectionOf(status));
      setAiLibraryNote(null);
      setAiLibraryError(null);
    } catch (e) {
      console.warn("[ai] 拉取 AI 引擎状态失败", e);
      setAiStatusError(e instanceof Error ? e.message : String(e));
    }
  };

  /** AI 技能库下拉选项（桥未返回时为空） */
  const aiOptions = () => aiStatus()?.options ?? [];

  /** 一句话状态：LLM 模式展示 model 与技能库摘要，脚本模式技能库不适用 */
  const aiEngineText = createMemo(() => {
    const status = aiStatus();
    if (!status) {
      return aiStatusError() ? `AI 引擎：不可用（${aiStatusError()}）` : "";
    }
    if (status.mode !== "llm") {
      return `AI 引擎：脚本（${status.spec}） · 技能库：不适用`;
    }
    const library = status.library
      ? `${status.library.selection}（${status.library.resolved}，v${status.library.version}，${status.library.skills} 条，built_at ${status.library.built_at}）`
      : "未知";
    const unavailable = status.llm_available
      ? ""
      : `（不可用：${status.llm_error ?? "原因未知"}）`;
    return `AI 引擎：LLM（model=${status.model || "?"}）${unavailable} · 技能库：${library}`;
  });

  /**
   * 评价协议状态行：以「是否选了 NPC 阵容」为准；`/ai` 如果返回
   * `parity` / `protocol` / `stage` 就附在后面（旧版桥没有这些字段）。
   */
  const evalProtocolText = createMemo(() => {
    const pick = npcPick();
    if (!pick) {
      return "非评价协议（未选 NPC 阵容）";
    }
    const status = aiStatus();
    const evaluation = pick.eval;
    const details: string[] = [];
    if (evaluation) {
      // 评价协议的静态口径：关卡牌组 / 卡池 / 冻结 loadout。
      if (evaluation.deck) {
        details.push(`牌组 ${evaluation.deck}`);
      }
      if (evaluation.card_version) {
        details.push(`卡池 ${evaluation.card_version}`);
      }
      if (evaluation.loadout) {
        details.push(`loadout ${evaluation.loadout}`);
      }
    }
    // `/ai` 是全局状态（配置页还没有 session），parity 此时必然 false；
    // 只有显式 true 才展示，避免误导。
    if (status?.parity === true) {
      details.push("parity=true");
    }
    if (status?.protocol) {
      details.push(`protocol=${status.protocol}`);
    }
    if (!evaluation && status?.stage && status.stage !== pick.slug) {
      details.push(`/ai stage=${status.stage}`);
    }
    return `评价协议：${pick.slug}${
      details.length > 0 ? `（${details.join("，")}）` : ""
    }`;
  });

  /**
   * 评价牌组一致性（非阻塞警告）：优先比对桥声明的 AI 牌组 slug；桥没声明
   * 就比对当前 AI 牌组内容。缺 `eval` 的旧版桥什么都不提示。
   */
  const evalDeckWarning = createMemo(() => {
    const evaluation = npcPick()?.eval;
    if (!evaluation?.deck) {
      return null;
    }
    const declared = aiDeckSlug();
    if (declared) {
      return declared === evaluation.deck
        ? null
        : `桥的 AI 牌组是 ${declared}，评价要求 ${evaluation.deck}`;
    }
    const characters = evaluation.characters;
    if (!characters?.length) {
      return null;
    }
    return sameDeckContents(deckOrEmpty(otherDeckCode()), {
      characters,
      cards: evaluation.cards ?? [],
    })
      ? null
      : `当前 AI 牌组与评价牌组 ${evaluation.deck} 不一致`;
  });

  /** `POST /ai-library`：切换技能库；失败回滚下拉并提示错误 */
  const changeAiLibrary = async (next: string) => {
    const previous = aiLibrarySelection();
    if (!next || next === previous) {
      return;
    }
    setAiLibrarySelection(next);
    setAiLibraryBusy(true);
    setAiLibraryError(null);
    setAiLibraryNote(null);
    try {
      const resp = await fetch(`${bridgeBase()}/ai-library`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ library: next }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const raw: unknown = await resp.json();
      if (!isRecord(raw) || raw.ok !== true) {
        throw new Error(`返回异常：${JSON.stringify(raw).slice(0, 200)}`);
      }
      const status = aiStatus();
      if (status) {
        const parsed = parseAiLibrary(raw.library);
        setAiStatus({
          ...status,
          library:
            parsed ??
            (status.library
              ? { ...status.library, selection: next }
              : null),
        });
      }
      setAiLibrarySelection(next);
      setAiLibraryNote(readString(raw, "note", "下一局生效"));
    } catch (e) {
      setAiLibrarySelection(previous);
      setAiLibraryError(e instanceof Error ? e.message : String(e));
    } finally {
      setAiLibraryBusy(false);
    }
  };

  onMount(() => {
    void loadNpcDecks();
    void loadAiStatus();
  });

  const importLog = async () => {
    return new Promise<GameStateLogEntry[]>((resolve, reject) => {
      const input = document.createElement("input");
      input.type = "file";
      input.accept = "application/json";
      input.onchange = (e) => {
        const file = (e.target as HTMLInputElement)?.files?.[0];
        if (!file) {
          reject(`Failed to read uploaded file`);
          return;
        }
        const reader = new FileReader();
        reader.onload = (event) => {
          const contents = event.target?.result as string;
          try {
            const logs = JSON.parse(contents);
            const version = logs.gv;
            resolve(deserializeGameStateLog(getData(version), logs));
          } catch (error) {
            reject(error);
          }
        };
        reader.readAsText(file);
      };
      input.oncancel = () => {
        reject(`File upload canceled`);
      };
      input.click();
    });
  };

  let deckBuilderDialog!: HTMLDialogElement;
  const [loadDeckBuilder, setLoadDeckBuilder] = createSignal(false);
  const [deckBuilderValue, setDeckBuilderValue] = createSignal<Deck>({
    characters: [],
    cards: [],
  });
  const openDeckBuilder = () => {
    setLoadDeckBuilder(true);
    deckBuilderDialog.showModal();
  };
  const closeDeckBuilder = () => {
    deckBuilderDialog.close();
  };
  const loadDeckBuilderValue = async () => {
    const code = prompt(`Input share code:`);
    if (code === null) {
      return;
    }
    try {
      const deck = DEFAULT_ASSETS_MANAGER.decode(code);
      setDeckBuilderValue(deck);
    } catch (e) {
      if (e instanceof Error) {
        alert(e.message);
      }
      console.error(e);
    }
  };
  const saveDeckBuilderValue = async () => {
    const deck = deckBuilderValue();
    try {
      const code = DEFAULT_ASSETS_MANAGER.encode(deck);
      await navigator.clipboard.writeText(code);
      alert(`Deck code copied to clipboard: ${code}`);
    } catch (e) {
      if (e instanceof Error) {
        alert(e.message);
      }
      console.error(e);
    }
  };

  const seatDecks = (cfg: GameConfig): [Deck, Deck] =>
    cfg.humanSeat === 0
      ? [cfg.myDeck, cfg.otherDeck]
      : [cfg.otherDeck, cfg.myDeck];

  const editorInitialState = () => {
    const cfg = gameCfg();
    if (!cfg) {
      return undefined;
    }
    try {
      return Game.createInitialState({
        decks: seatDecks(cfg),
        data: getData(version()),
      });
    } catch (e) {
      if (e instanceof Error) {
        alert(e.message);
      }
      console.error(e);
      return undefined;
    }
  };
  const onEditorSubmit = (state: GameState) => {
    console.log(state);
    setLogs([{ state, canResume: true }]);
    setMode(GameMode.Standalone);
  };

  /** 摆盘器：先把本局配置（牌组/座位/会话）定下来，摆完直接用它开局 */
  const openEditor = () => {
    setGameCfg({
      sessionId: newSessionId(),
      myDeck: npcPick() ?? deckOrEmpty(myDeckCode()),
      otherDeck: deckOrEmpty(otherDeckCode()),
      humanSeat: resolveHumanSeat(),
    });
    setMode(GameMode.Editor);
  };

  /** 导入日志：不需要反选，直接定配置进对局 */
  const enterGame = (logEntries?: GameStateLogEntry[]) => {
    if (logEntries) {
      setLogs(logEntries);
    }
    setGameCfg({
      sessionId: newSessionId(),
      myDeck: npcPick() ?? deckOrEmpty(myDeckCode()),
      otherDeck: deckOrEmpty(otherDeckCode()),
      humanSeat: resolveHumanSeat(),
    });
    setMode(GameMode.Standalone);
  };

  /** 开局（D18 流程：人选牌 → AI 看牌反选 → 定先手） */
  const startGame = async () => {
    const mine = myDeck();
    if (!mine) {
      return;
    }
    let theirs = otherDeck();
    if (!theirs) {
      return;
    }
    const sessionId = newSessionId();
    if (aiOpponent() && !otherDeckEdited() && !otherDeckFromEval()) {
      // 没手改过对面牌组 → 让桥按我的牌组反选一次（结果回填并用于本局）。
      // 评价牌组由关卡 `eval` 固定，不参与反选。
      const pick = await chooseCounterPick(sessionId);
      if (pick) {
        theirs = { characters: pick.characters, cards: pick.cards };
      }
    }
    setGameCfg({
      sessionId,
      myDeck: mine,
      otherDeck: theirs,
      humanSeat: resolveHumanSeat(),
    });
    setMode(GameMode.Standalone);
  };

  return (
    <div>
      <Switch>
        <Match when={mode() === GameMode.NotStarted}>
          <div class="tabs">
            <input
              class="tab__input"
              type="radio"
              name="gameModeTab"
              id="standaloneInput"
              checked
            />
            <label class="tab__header" for="standaloneInput">
              本地模拟
            </label>
            <div class="tab__content config-panel">
              <div class="config-panel__title">牌组配置</div>
              <div class="config-panel__deck">
                <label>我的牌组</label>
                <input
                  type="text"
                  disabled={!!npcPick()}
                  value={
                    npcPick()
                      ? `NPC·${npcPick()!.display}（出战 ${npcPick()!.n_on_field}／预备 ${npcPick()!.n_support}）`
                      : myDeckCode()
                  }
                  onInput={(e) => setMyDeckCode(e.currentTarget.value)}
                />
              </div>
              <div class="config-panel__deck">
                <label>{aiOpponent() ? "AI 牌组" : "对面牌组"}</label>
                <input
                  type="text"
                  value={otherDeckCode()}
                  onInput={(e) => {
                    setOtherDeckCode(e.currentTarget.value);
                    setOtherDeckEdited(true);
                    setOtherDeckFromEval(false);
                    setCounterPick(null);
                  }}
                />
                <button
                  type="button"
                  disabled={!aiOpponent()}
                  title="按我的牌组用对位矩阵挑一套（D19）"
                  onClick={() => void chooseCounterPick()}
                >
                  AI 反选
                </button>
                <button
                  type="button"
                  disabled={!aiOpponent()}
                  title="用桥 --ai-deck / --policy expert:<slug> 声明的牌组"
                  onClick={() => void fetchAiDeck()}
                >
                  取 AI 牌组
                </button>
              </div>
              <div class="config-panel__deck">
                <label>NPC 阵容</label>
                <select
                  value={npcPick()?.slug ?? ""}
                  onChange={(e) => {
                    const slug = e.target.value;
                    if (!slug) {
                      setNpcPick(null);
                      setOtherDeckFromEval(false);
                      return;
                    }
                    const found = npcList()?.decks.find(
                      (d) => d.slug === slug,
                    );
                    setNpcPick(found ?? null);
                    // 评价协议：按该关卡的 eval 回填 AI 牌组与卡池版本
                    applyNpcEval(found ?? null);
                  }}
                >
                  <option value="">（不用 NPC 阵容，用上面的分享码）</option>
                  <For each={npcList()?.decks ?? []}>
                    {(d) => (
                      <option value={d.slug}>
                        {d.display}（出战 {d.n_on_field}
                        {d.n_support > 0 ? `／预备 ${d.n_support}` : ""}）
                      </option>
                    )}
                  </For>
                </select>
                <button type="button" onClick={() => void loadNpcDecks()}>
                  刷新
                </button>
                <Show when={npcError()}>
                  <span>（拉取失败：{npcError()}，需先启动桥）</span>
                </Show>
              </div>
              <div class="config-panel__deck">
                <label>先手</label>
                <select
                  value={firstTurn()}
                  onChange={(e) =>
                    setFirstTurn(e.target.value as FirstTurn)
                  }
                >
                  <option value="random">随机</option>
                  <option value="human">
                    {aiOpponent() ? "人类先手" : "我先手"}
                  </option>
                  <option value="other">
                    {aiOpponent() ? "AI 先手" : "对面先手"}
                  </option>
                </select>
                <span>（引擎 seat0 恒先手；选「随机」则开局时掷一次）</span>
              </div>
              <div class="config-panel__deck">
                <label>游戏版本</label>
                <select
                  value={version()}
                  onChange={(e) => setVersion(e.target.value as Version)}
                >
                  <For each={VERSIONS}>
                    {(ver) => <option value={ver}>{ver}</option>}
                  </For>
                </select>
              </div>
              <div class="config-panel__deck">
                <label>
                  <input
                    type="checkbox"
                    checked={aiOpponent()}
                    onChange={(e) => {
                      const on = e.currentTarget.checked;
                      setAiOpponent(on);
                      if (on) {
                        void fetchAiDeck();
                        void loadAiStatus();
                      } else {
                        setAiDeckSlug(null);
                      }
                    }}
                  />{" "}
                  AI 对手（HTTP 桥）
                </label>
                <input
                  type="text"
                  disabled={!aiOpponent()}
                  value={aiServerUrl()}
                  onInput={(e) => setAiServerUrl(e.currentTarget.value)}
                  placeholder={`${window.location.origin}/bridge`}
                />
                <label>
                  <input
                    type="checkbox"
                    disabled={!aiOpponent()}
                    checked={aiSelfPlay()}
                    onChange={(e) => setAiSelfPlay(e.currentTarget.checked)}
                  />{" "}
                  双方 AI 自对弈
                </label>
              </div>
              <div
                class="config-panel__deck"
                style={{ "align-items": "center" }}
              >
                <label>AI 技能库</label>
                <select
                  disabled={
                    aiLibraryBusy() ||
                    (aiStatus() !== null && aiStatus()!.mode !== "llm") ||
                    aiStatus()?.llm_available === false
                  }
                  // options 是异步拉到的：value 之外再给 option 标 selected，
                  // 否则浏览器会在选项出现前把 value 重置成第一项（显示成 none）。
                  value={aiLibrarySelection()}
                  onChange={(e) =>
                    void changeAiLibrary(e.currentTarget.value)
                  }
                >
                  <Show when={aiOptions().length === 0}>
                    <option value="">（桥未连接）</option>
                  </Show>
                  <For each={aiOptions()}>
                    {(opt) => (
                      <option
                        value={opt.value}
                        selected={opt.value === aiLibrarySelection()}
                      >
                        {opt.label}
                      </option>
                    )}
                  </For>
                </select>
                <Show when={aiLibraryNote()}>
                  <span style={{ color: "#2e7d32" }}>（{aiLibraryNote()}）</span>
                </Show>
                <Show when={aiLibraryError()}>
                  <span style={{ color: "#b00020" }}>
                    （切换失败：{aiLibraryError()}）
                  </span>
                </Show>
              </div>
              <div
                class="config-panel__deck"
                style={{ "flex-wrap": "wrap" }}
              >
                <Show when={aiEngineText()}>
                  <span>{aiEngineText()}</span>
                </Show>
                <span>{evalProtocolText()}</span>
                <Show when={evalDeckWarning()}>
                  <span style={{ color: "#b00020" }}>
                    牌组警告：{evalDeckWarning()}
                  </span>
                </Show>
              </div>
              <div
                style={{
                  display: "flex",
                  gap: "0.75rem",
                  "align-items": "flex-start",
                  "margin-bottom": "1rem",
                }}
              >
                <div style={{ flex: "1 1 0", "min-width": "0" }}>
                  <DeckPreview
                    title="我的牌组"
                    deck={npcPick() ?? deckOrEmpty(myDeckCode())}
                    npcAssets={npcList()?.assets}
                    assetsManager={npcAssetsManager()}
                  />
                </div>
                <div style={{ flex: "1 1 0", "min-width": "0" }}>
                  <DeckPreview
                    title={aiOpponent() ? "AI 牌组" : "对面牌组"}
                    deck={deckOrEmpty(otherDeckCode())}
                    npcAssets={npcList()?.assets}
                    assetsManager={npcAssetsManager()}
                  />
                </div>
              </div>
              <div class="config-panel__description">
                点击下方按钮开始对局：本页面永远是**我**的棋盘，对面座位按「先手」与
                AI 开关决定（AI 模式不弹窗、对面交给 Python 桥；双人模式弹窗显示对面）。
                <br />
                （若弹窗不显示为浏览器阻止，请允许本页面使用弹出式窗口。）
                <Show when={aiOpponent()}>
                  <br />
                  <strong>
                    AI 对手模式：对面座位由 {aiServerUrl()} 的 Python 服务托管。
                  </strong>
                  <Show when={aiDeckSlug()}>
                    <br />
                    AI 卡组：<code>{aiDeckSlug()}</code>（可「取 AI 牌组」回填到
                    「AI 牌组」，与桥的 <code>--policy</code>/<code>--ai-deck</code>{" "}
                    对齐）。
                  </Show>
                </Show>
                <Show when={counterPick()}>
                  <br />
                  反选：<code>{counterPick()!.slug}</code>（
                  {counterPick()!.method}）— {counterPick()!.reason}
                  <Show when={counterPick()!.policy_applied === false}>
                    <br />
                    注：桥的 <code>--policy</code> 不是 <code>expert:*</code>
                    （deck-specific），所以只换牌组、不换策略
                    （{counterPick()!.base_policy ?? "占位"}）。
                  </Show>
                </Show>
                <Show when={npcPick() && npcList()}>
                  <br />
                  <strong>NPC 阵容近似说明：</strong>
                  <For
                    each={Object.values(npcList()?.semantics ?? {})}
                  >
                    {(line) => (
                      <>
                        <br />· {line}
                      </>
                    )}
                  </For>
                </Show>
                <Show when={IS_BETA}>
                  <br />
                  <strong>
                    请注意：本页面包含未发布的测试版卡牌；包含这些卡牌的分享码仅限在本页面内使用，它们可能是无效的正式版分享码。
                  </strong>
                </Show>
              </div>
              <div class="config-panel__button-group">
                <button onClick={() => void startGame()}>开始对局</button>
                <button
                  onClick={async () => {
                    const imported = await importLog().catch(alert);
                    if (imported) {
                      enterGame(imported);
                    }
                  }}
                >
                  导入日志
                </button>
                <button onClick={openEditor}>摆盘器</button>
              </div>
            </div>
            <input
              class="tab__input"
              type="radio"
              name="gameModeTab"
              id="multiplayerInput"
            />
            <label class="tab__header" for="multiplayerInput">
              <a href={`${SERVER_HOST}${WEB_CLIENT_BASE_PATH}`} target="_blank">
                多人对战
              </a>
            </label>
            <div class="tab__content config-panel" />
            <div class="tab__spacer" />
          </div>
          <h3>友情链接</h3>
          <ul>
            <li>
              获取牌组码：
              <a
                href="https://webstatic.mihoyo.com/ys/event/bbs-lineup-qskp/index.html"
                target="_blank"
              >
                米游社「七圣召唤」卡牌广场
              </a>
            </li>
            <li>
              获取牌组码：
              <a href="https://www.summoners.top/teams" target="_blank">
                召唤之巅：原神赛事数据统计平台
              </a>
            </li>
            <li>
              获取牌组码：<button onClick={openDeckBuilder}>启动组牌器</button>
            </li>
            <li>
              此项目{" "}
              <a
                href="https://github.com/Guyutongxue/genius-invokation"
                target="_blank"
              >
                GitHub
              </a>
            </li>
            <li>
              <a
                href="https://gi-tcg.taim.site"
                target="_blank"
              >
                Taimevraiment 七圣召唤模拟器
              </a>
              （
              <a
                href="https://github.com/Taimevraiment/genius-invokation"
                target="_blank"
              >
                GitHub
              </a>
              ）
            </li>
            <li>
              <a
                href="https://jarvis-yu.github.io/Dottore-Genius-Invokation-TCG-PWA/"
                target="_blank"
              >
                Dottore 七圣召唤模拟器
              </a>
              （
              <a
                href="https://github.com/Jarvis-Yu/Dottore-Genius-Invokation-TCG-Simulator"
                target="_blank"
              >
                GitHub
              </a>
              ）
            </li>
            <li>
              <a href="https://lpsim.zyr17.cn/" target="_blank">
                七圣召唤水皇模拟器 "LPSim"
              </a>
              （
              <a href="https://github.com/LPSim/backend" target="_blank">
                GitHub
              </a>
              ）
            </li>
            <li>
              <a
                href="https://flick-ai.github.io/Genius-Invokation"
                target="_blank"
              >
                flick-ai 七圣召唤模拟器
              </a>
              （
              <a
                href="https://github.com/flick-ai/Genius-Invokation"
                target="_blank"
              >
                GitHub
              </a>
              ）
            </li>
          </ul>
        </Match>
        <Match when={mode() === GameMode.Standalone}>
          <Show when={gameCfg()}>
            <StandaloneParent
              logs={logs()}
              sessionId={gameCfg()!.sessionId}
              myDeck={gameCfg()!.myDeck}
              otherDeck={gameCfg()!.otherDeck}
              humanSeat={gameCfg()!.humanSeat}
              version={version()}
              npcCustomData={npcList()?.assets}
              npcLineups={npcLineups()}
              aiOpponent={aiOpponent() ? bridgeBase() : undefined}
              aiSelfPlay={aiSelfPlay()}
              stage={npcPick()?.slug}
              stageRules={npcPick()?.eval?.rules}
            />
          </Show>
        </Match>
        <Match when={mode() === GameMode.Editor}>
          <GameStateEditor
            initialValue={editorInitialState()}
            onSubmit={onEditorSubmit}
          >
            <button
              class="editor-back-button"
              type="button"
              onClick={() => setMode(GameMode.NotStarted)}
            >
              返回首页
            </button>
          </GameStateEditor>
        </Match>
      </Switch>
      <dialog ref={deckBuilderDialog!} class="deck-builder-dialog">
        <Show when={loadDeckBuilder()}>
          <DeckBuilder
            class="deck-builder"
            deck={deckBuilderValue()}
            assetsManager={npcAssetsManager()}
            onChangeDeck={setDeckBuilderValue}
          />
        </Show>
        <div class="deck-builder-actions">
          <button onClick={closeDeckBuilder}>Close</button>
          <button onClick={saveDeckBuilderValue}>Save</button>
          <button onClick={loadDeckBuilderValue}>Load from code</button>
        </div>
      </dialog>
    </div>
  );
}
