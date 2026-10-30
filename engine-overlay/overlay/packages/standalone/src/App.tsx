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
import { IS_BETA, SERVER_HOST, WEB_CLIENT_BASE_PATH } from "@gi-tcg/config";
import { DeckBuilder } from "@gi-tcg/deck-builder";
import "@gi-tcg/deck-builder/style.css";
import "@gi-tcg/state-editor/style.css";
import { DEFAULT_ASSETS_MANAGER, type CustomData } from "@gi-tcg/assets-manager";
import { GameStateEditor } from "@gi-tcg/state-editor";

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
}

interface NpcPayload {
  decks: NpcDeck[];
  cards_source?: string;
  semantics: Record<string, string>;
  /** C3：NPC 的 CustomData（名字/技能描述/骰子费用），喂给 AssetsManager */
  assets?: CustomData;
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
  const [npcPick, setNpcPick] = createSignal<NpcDeck | null>(null);
  const [npcList, setNpcList] = createSignal<NpcPayload | null>(null);
  const [npcError, setNpcError] = createSignal<string | null>(null);
  // C1 反选结果（只做展示；真正生效在开局时的 /session）
  const [counterPick, setCounterPick] = createSignal<CounterPick | null>(null);
  // 先手三选：人类 / AI / 随机
  const [firstTurn, setFirstTurn] = createSignal<FirstTurn>("random");
  // WS8/D18 overlay：AI 对手（HTTP 桥）开关与地址
  const [aiOpponent, setAiOpponent] = createSignal(false);
  const [aiServerUrl, setAiServerUrl] = createSignal("http://127.0.0.1:8770");
  const [aiSelfPlay, setAiSelfPlay] = createSignal(false);
  // T3：桥声明的 AI 座位牌组 slug（拿到后回填到「AI 牌组」）
  const [aiDeckSlug, setAiDeckSlug] = createSignal<string | null>(null);
  // 开局解析完的配置；切到 Standalone 前必须有值
  const [gameCfg, setGameCfg] = createSignal<GameConfig | null>(null);

  const bridgeBase = () => aiServerUrl().replace(/\/+$/, "");

  // C3/D18：NPC 各阵容的角色列表（原样，含重复）。座位牌组的角色与之完全一致
  // （且无行动牌）就按 NPC 牌组处理 —— StandaloneParent 据此给该座位 8 个万能骰。
  // 人持 NPC 阵容（下面的「NPC 阵容」下拉 → `npcPick`）走的就是这条路。
  const npcLineups = createMemo(
    () => (npcList()?.decks ?? []).map((d) => d.characters ?? []),
  );

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
      const code = encodeDeck({ characters: data.characters, cards: data.cards });
      if (code) {
        setOtherDeckCode(code);
        setOtherDeckEdited(false);
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

  onMount(() => {
    void loadNpcDecks();
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
    if (aiOpponent() && !otherDeckEdited()) {
      // 没手改过对面牌组 → 让桥按我的牌组反选一次（结果回填并用于本局）
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
                      return;
                    }
                    const found = npcList()?.decks.find(
                      (d) => d.slug === slug,
                    );
                    setNpcPick(found ?? null);
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
                  placeholder="http://127.0.0.1:8770"
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
