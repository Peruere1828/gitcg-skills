"""C1（WS8/D18、D19）：Web 端「AI 看对手牌反选」v1。

口径（PLAN D19）：Web 对局＝人自选 + AI 反选，**v1 用对位矩阵 + decklab 固定规则**。

策略是两级的：

1. 把人的牌组映射到评测池 20 套里最像的一套（**代理**，角色重合权重更高），
   再用 `reports/arena/` 里已经跑好的 **20×20 对位矩阵**择优——矩阵是双向跑的
   （`swap=true`），这里把同一对的两个方向**合并**成「D 对 E 的胜率」，因为
   随机先手下正反手各占一半。
2. 代理相似度过低（人牌组和池内任何一套都不像）→ 退到**矩阵总强度**（contestants
   的总体胜率）；连矩阵都没有 → 退到 **decklab 固定规则牌组**（池 C + `heuristic`）。

返回的 `policy` 是**应该给这套牌配的策略 spec**：池内 20 套都有对应脚本规则
（`expert:<slug>`），decklab 兜底牌组没有脚本，只能配 deck-agnostic 的 `heuristic`。
桥侧只在 `--policy expert:*`（deck-specific）时才据此改派座位策略，绝不覆盖
`neural:` 等用户显式选的模型（见 `web/ai_rpc_server.py`）。

无网络、无重型依赖：`envs`/`decklab` 只在真正调用时延迟导入。
"""

from __future__ import annotations

import glob
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from common.paths import REPO_ROOT

ARENA_DIR = REPO_ROOT / "reports" / "arena"

# 角色重合是「同原型」的强信号，牌组重合次之（30 张牌里泛用牌占比高）。
_CHAR_WEIGHT = 0.6
_CARD_WEIGHT = 0.4
# 代理相似度低于该阈值就说明「池内没有像它的牌组」，改按总强度挑。
MIN_PROXY_SIMILARITY = 0.2


@dataclass(frozen=True)
class Matrix:
    """20×20 对位矩阵：总胜率 + 合并双向后的两两胜率。"""

    path: str
    n_matches: int
    contestants: dict[str, float]                 # slug -> 总胜率
    win_rates: dict[tuple[str, str], float]       # (D, E) -> D 对 E 胜率
    games: dict[tuple[str, str], int]             # (D, E) -> 局数

    def rate(self, deck: str, opponent: str) -> float | None:
        return self.win_rates.get((deck, opponent))


@dataclass(frozen=True)
class CounterPick:
    slug: str
    characters: tuple[int, ...]
    cards: tuple[int, ...]
    policy: str                       # 给这套牌配的策略 spec
    method: str                       # matrix-proxy | matrix-strength | decklab-rule
    proxy: str | None                 # 用哪套池内牌组当人的原型
    similarity: float | None          # 人牌组与原型的相似度
    win_rate: float | None            # 反选牌对该原型的（合并双向）胜率
    reason: str                       # 给人看的中文说明

    def as_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "characters": list(self.characters),
            "cards": list(self.cards),
            "policy": self.policy,
            "method": self.method,
            "proxy": self.proxy,
            "similarity": self.similarity,
            "win_rate": self.win_rate,
            "reason": self.reason,
        }


# --------------------------------------------------------------------------- #
# 相似度
# --------------------------------------------------------------------------- #
def _multiset_jaccard(a: Sequence[int], b: Sequence[int]) -> float:
    """多重集 Jaccard：牌组有重复卡（每卡 ≤2 张），按张数算交并比。"""
    ca, cb = Counter(int(x) for x in a), Counter(int(x) for x in b)
    if not ca and not cb:
        return 1.0
    inter = sum((ca & cb).values())
    union = sum((ca | cb).values())
    return inter / union if union else 0.0


def _set_jaccard(a: Sequence[int], b: Sequence[int]) -> float:
    sa, sb = {int(x) for x in a}, {int(x) for x in b}
    if not sa and not sb:
        return 1.0
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


@dataclass(frozen=True)
class DeckView:
    """最小牌组视图：反选只关心角色与牌（不校验合法性，那是引擎的事）。"""

    name: str = "web_human"
    characters: tuple[int, ...] = ()
    cards: tuple[int, ...] = ()


def deck_view_from_json(payload: Any, *, name: str = "web_human") -> DeckView:
    """把浏览器上报的 `{characters, cards}` 解成 `DeckView`；形状不对就 raise。"""
    if not isinstance(payload, dict):
        raise ValueError("deck 必须是 {characters:[...], cards:[...]} 对象")
    characters = payload.get("characters")
    cards = payload.get("cards") or []
    if not isinstance(characters, (list, tuple)) or not characters:
        raise ValueError("deck.characters 必须是非空整数列表")
    if not isinstance(cards, (list, tuple)):
        raise ValueError("deck.cards 必须是整数列表")
    try:
        return DeckView(
            name=name,
            characters=tuple(int(c) for c in characters),
            cards=tuple(int(c) for c in cards),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"deck 里的 id 必须是整数：{exc}") from exc


def deck_similarity(a: Any, b: Any) -> float:
    """两套牌组的相似度 ∈ [0,1]：0.6×角色 + 0.4×牌（多重集）。"""
    chars = _set_jaccard(a.characters, b.characters)
    cards = _multiset_jaccard(a.cards, b.cards)
    return _CHAR_WEIGHT * chars + _CARD_WEIGHT * cards


# --------------------------------------------------------------------------- #
# 对位矩阵
# --------------------------------------------------------------------------- #
def _candidate_paths(arena_dir: Path) -> list[Path]:
    paths = [Path(p) for p in sorted(glob.glob(str(arena_dir / "*.json")))]
    good: list[Path] = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and "pairings" in payload and "contestants" in payload:
            good.append(path)
    # 优先名字里带 matrix 的；同为矩阵就取局数多的那份（下面再比 n_matches）。
    good.sort(key=lambda p: ("matrix" not in p.name, p.name))
    return good


def load_matrix(path: str | Path | None = None, *, arena_dir: str | Path | None = None) -> Matrix | None:
    """读 `reports/arena/` 的对位矩阵；读不到返回 None（交给上层兜底）。"""
    if path is None:
        paths = _candidate_paths(Path(arena_dir) if arena_dir else ARENA_DIR)
        if not paths:
            return None
        best, best_matches = None, -1
        for candidate in paths:
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            matches = int(payload.get("n_matches") or 0)
            if matches > best_matches:
                best, best_matches = candidate, matches
        if best is None:
            return None
        path = best
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    contestants = {
        str(entry["deck"]): float(entry["rate"])
        for entry in payload.get("contestants", ())
        if isinstance(entry, dict) and "deck" in entry and "rate" in entry
    }
    # 同一对的两个方向合并：D 作为 deck0 的胜场 + D 作为 deck1 的胜场。
    wins: Counter[tuple[str, str]] = Counter()
    games: Counter[tuple[str, str]] = Counter()
    for pairing in payload.get("pairings", ()):
        if not isinstance(pairing, dict):
            continue
        d0, d1 = str(pairing.get("deck0")), str(pairing.get("deck1"))
        n = int(pairing.get("games") or 0)
        if n <= 0 or d0 == d1:
            continue
        w0 = float(pairing.get("wins0") or 0)
        w1 = float(pairing.get("wins1") or 0)
        wins[(d0, d1)] += w0
        wins[(d1, d0)] += w1
        games[(d0, d1)] += n
        games[(d1, d0)] += n
    rates = {key: wins[key] / games[key] for key in games}
    return Matrix(
        path=str(path),
        n_matches=int(payload.get("n_matches") or 0),
        contestants=contestants,
        win_rates=rates,
        games=dict(games),
    )


# --------------------------------------------------------------------------- #
# 反选
# --------------------------------------------------------------------------- #
def _pool_specs() -> dict[str, Any]:
    """评测池 20 套牌组（`envs.decks`，含 gitcg 依赖 → 延迟导入）。"""
    from envs.decks import expert_deck_specs

    return {spec.name: spec for spec in expert_deck_specs()}


def _decklab_fallback() -> Any:
    """没有对位矩阵时的兜底：decklab 池 C 固定规则牌组（D19 的“固定规则起步”）。"""
    from decklab.builder import build_pool_c_superconduct

    return build_pool_c_superconduct(name="decklab_poolc_rule")


def counter_pick(
    human: Any,
    *,
    pool: dict[str, Any] | None = None,
    matrix: Matrix | None = None,
    load_matrix_if_missing: bool = True,
) -> CounterPick:
    """给 `human` 牌组挑一套应对牌组（`human` 是 `reps.schema.DeckSpec` 形状的对象）。"""
    pool = dict(pool) if pool is not None else _pool_specs()
    if matrix is None and load_matrix_if_missing:
        matrix = load_matrix()

    if matrix is None:
        fallback = _decklab_fallback()
        return CounterPick(
            slug=fallback.name,
            characters=tuple(int(c) for c in fallback.characters),
            cards=tuple(int(c) for c in fallback.cards),
            policy="heuristic",
            method="decklab-rule",
            proxy=None,
            similarity=None,
            win_rate=None,
            reason="没有可用的对位矩阵，退到 decklab 池 C 固定规则牌组（配 heuristic 策略）",
        )

    proxy = max(pool.items(), key=lambda kv: deck_similarity(human, kv[1]))
    proxy_slug, proxy_deck = proxy
    similarity = deck_similarity(human, proxy_deck)

    if similarity < MIN_PROXY_SIMILARITY:
        # 人的牌组和池内哪套都不像 → 原理上没有可信的对位数据，改按总强度挑。
        best_slug = max(matrix.contestants, key=lambda s: matrix.contestants[s])
        deck = pool[best_slug]
        strength = matrix.contestants.get(best_slug)
        return CounterPick(
            slug=best_slug,
            characters=tuple(int(c) for c in deck.characters),
            cards=tuple(int(c) for c in deck.cards),
            policy=f"expert:{best_slug}",
            method="matrix-strength",
            proxy=None,
            similarity=round(similarity, 4),
            win_rate=None,
            reason=(
                f"你的牌组与池内任何一套都不像（最高相似度 {similarity:.2f}，"
                f"代理 {proxy_slug}），改按总胜率 {strength:.3f} 挑最强的一套"
            ),
        )

    def score(item: tuple[str, Any]) -> tuple[float, float]:
        slug = item[0]
        rate = matrix.rate(slug, proxy_slug)
        if rate is None:
            # 矩阵里没有这一对：用总胜率垫底，仍参与排序。
            return (-1.0, matrix.contestants.get(slug, 0.0))
        return (rate, matrix.contestants.get(slug, 0.0))

    # 不和人打镜像：代理本身没有对位数据（矩阵对角线被跳过），显式排除。
    candidates = [item for item in pool.items() if item[0] != proxy_slug]
    if not candidates:
        candidates = list(pool.items())
    best_slug, best_deck = max(candidates, key=score)
    rate = matrix.rate(best_slug, proxy_slug)
    return CounterPick(
        slug=best_slug,
        characters=tuple(int(c) for c in best_deck.characters),
        cards=tuple(int(c) for c in best_deck.cards),
        policy=f"expert:{best_slug}",
        method="matrix-proxy",
        proxy=proxy_slug,
        similarity=round(similarity, 4),
        win_rate=None if rate is None else round(rate, 4),
        reason=(
            f"你的牌组最像池内 {proxy_slug}（相似度 {similarity:.2f}）；"
            f"对位矩阵里 {best_slug} 对它胜率 "
            f"{'n/a' if rate is None else format(round(rate, 3), '.3f')}"
            f"（{matrix.games.get((best_slug, proxy_slug), 0)} 局，正反手合并）"
        ),
    )


def pool_slug_for_deck(deck: dict[str, Any], pool: dict[str, Any] | None = None) -> str | None:
    """牌组（`{"characters": [...], "cards": [...]}`）是否就是池内某套 → 返回 slug。"""
    pool = dict(pool) if pool is not None else _pool_specs()
    characters = [int(c) for c in (deck.get("characters") or [])]
    cards = sorted(int(c) for c in (deck.get("cards") or []))
    if not characters:
        return None
    for slug, spec in pool.items():
        if list(spec.characters) == characters and sorted(spec.cards) == cards:
            return slug
    return None
