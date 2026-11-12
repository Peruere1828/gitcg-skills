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

import { For, Show, createMemo, createSignal, onMount } from "solid-js";
import type { Deck } from "@gi-tcg/core";
import {
  AssetsManager,
  DEFAULT_ASSETS_MANAGER,
  type CustomData,
} from "@gi-tcg/assets-manager";

export interface DeckPreviewProps {
  /** 预览标题（例如「我的牌组」「AI 牌组」） */
  title: string;
  /** 牌组对象；空对象（分享码解析失败时的兜底）显示「（未解析）」 */
  deck: Deck;
  /** NPC 的 CustomData：官方数据里没有的 NPC 角色从这找名字 */
  npcAssets?: CustomData;
  /** 带 NPC/引擎数据的 AssetsManager（例如 2301 hp=13）；缺省用官方镜像 */
  assetsManager?: AssetsManager;
}

/** 角色/卡牌缩略图；缺图（含 NPC id）时 onError 隐藏，避免裂图 */
function Thumbnail(props: {
  id: number;
  name: string;
  width: number;
  assetsManager?: AssetsManager;
}) {
  return (
    <img
      src={(props.assetsManager ?? DEFAULT_ASSETS_MANAGER).getImageUrlSync(
        props.id,
        { thumbnail: true },
      )}
      alt={props.name}
      draggable="false"
      style={{
        width: `${props.width}px`,
        "max-height": "56px",
        "object-fit": "cover",
        "border-radius": "3px",
        background: "#eee",
        "flex-shrink": "0",
      }}
      onError={(e) => {
        e.currentTarget.style.display = "none";
      }}
    />
  );
}

export function DeckPreview(props: DeckPreviewProps) {
  // 传了带 NPC/引擎数据的 manager 就优先用它（名字/图/静态血量都以引擎为准）。
  const assetsManager = () => props.assetsManager ?? DEFAULT_ASSETS_MANAGER;
  // prepareForSync 完成后置 true，触发名字/图片重算（同步 API 依赖缓存）
  const [assetsReady, setAssetsReady] = createSignal(false);
  onMount(() => {
    void assetsManager()
      .prepareForSync()
      .catch(() => undefined)
      .finally(() => setAssetsReady(true));
  });

  const npcCharacter = (id: number) =>
    props.npcAssets?.characters.find((c) => c.id === id);

  const isEmpty = () =>
    props.deck.characters.length === 0 && props.deck.cards.length === 0;

  const characters = createMemo(() => {
    assetsReady();
    return props.deck.characters.map((id) => ({
      id,
      name:
        assetsManager().getNameSync(id) ??
        npcCharacter(id)?.name ??
        `角色#${id}`,
    }));
  });

  // 行动牌按 id 去重聚合（数量 × 缩略图 + 名字），保留首次出现顺序
  const cards = createMemo(() => {
    assetsReady();
    const counts = new Map<number, number>();
    for (const id of props.deck.cards) {
      counts.set(id, (counts.get(id) ?? 0) + 1);
    }
    return [...counts.entries()].map(([id, count]) => ({
      id,
      count,
      name: assetsManager().getNameSync(id) ?? `卡牌#${id}`,
    }));
  });

  return (
    <div
      style={{
        border: "1px solid #ccc",
        "border-radius": "4px",
        padding: "0.5rem",
        "min-width": "0",
        "font-size": "0.85rem",
      }}
    >
      <div style={{ "font-weight": "bold", "margin-bottom": "0.35rem" }}>
        {props.title}
      </div>
      <Show when={!isEmpty()} fallback={<div style={{ color: "#888" }}>（未解析）</div>}>
        <Show when={characters().length > 0}>
          <div
            style={{
              display: "flex",
              "flex-wrap": "wrap",
              gap: "0.35rem",
            }}
          >
            <For each={characters()}>
              {(ch) => (
                <div
                  title={ch.name}
                  style={{
                    display: "flex",
                    "align-items": "center",
                    gap: "0.25rem",
                  }}
                >
                  <Thumbnail
                    id={ch.id}
                    name={ch.name}
                    width={36}
                    assetsManager={assetsManager()}
                  />
                  <span>{ch.name}</span>
                </div>
              )}
            </For>
          </div>
        </Show>
        <Show when={cards().length > 0}>
          <div
            style={{
              display: "flex",
              "flex-wrap": "wrap",
              gap: "0.35rem",
              "margin-top": characters().length > 0 ? "0.35rem" : "0",
            }}
          >
            <For each={cards()}>
              {(card) => (
                <div
                  title={card.name}
                  style={{
                    display: "flex",
                    "align-items": "center",
                    gap: "0.25rem",
                  }}
                >
                  <span style={{ color: "#666" }}>×{card.count}</span>
                  <Thumbnail
                    id={card.id}
                    name={card.name}
                    width={30}
                    assetsManager={assetsManager()}
                  />
                  <span>{card.name}</span>
                </div>
              )}
            </For>
          </div>
        </Show>
      </Show>
      <div style={{ "margin-top": "0.35rem", color: "#666" }}>
        角色 {props.deck.characters.length} / 行动牌 {props.deck.cards.length}
      </div>
    </div>
  );
}
