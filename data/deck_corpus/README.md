# deck_corpus — public 七圣召唤 deck data (gitignored)

Curated public deck data for exploration task 1 (expanded eval pool / deck-lab
input). **This directory is gitignored** (`data/*`); only this README is tracked.
The committed human summary lives at [`reports/explore/deck_corpus.md`](../../reports/explore/deck_corpus.md)
and a compact copy at `reports/explore/deck_corpus.json`.

## Provenance

Primary source — **miHoYo 卡牌广场** (official, no login required):

- SPA: <https://webstatic.mihoyo.com/ys/event/bbs-lineup-qskp/index.html#/pc/home>
  (JS bundle `index_*.js` only; the deck data is loaded from the JSON API below).
- JSON API base: `https://api-takumi.mihoyo.com/event/cardsquare/`
- Endpoints used and saved under `raw/`:
  - `match_pool_list?page_size=50` → `raw/match_pool_list.json` (燃斗无双积分赛 pool, 49 decks)
  - `index_recommend` → `raw/index_recommend.json` (home recommendations, 4 decks)
  - `honor_match_pool` → `raw/honor_match_pool.json` (honour + random video pick)
  - `labels`, `match_pool_filter` → `raw/*.json` (context only)
- `card_code` in each record is the official deck share code; `desc` is the
  author's own guide text.

Secondary source — **web guides** (share codes copied from public pages, each
validated before inclusion; provenance list in `scripts/explore/web_decks.json`):

- `https://www.gamersky.com/handbook/202312/1688434.shtml` (17 codes, 4.3 era)
- `https://www.bilibili.com/video/BV1As3DzbEAM/` (5.7 双水茜)
- `https://www.bilibili.com/video/BV1eo4y1u7s9/` (刻雷甘 PVE)
- `https://www.52pk.net/news/cidh463.html` (那维双火; SEO aggregator — low confidence)

No deck/card list here is fabricated: every share code is decoded with the
ported engine codec and rejected unless it yields exactly **3 characters + 30
action cards** with all definition ids present in the asset catalog.

## Files

- `decks.json` — normalised, validated corpus (full: includes author guides).
- `raw/*.json` — untouched API responses (provenance/audit).
- Regenerate with:
  ```bash
  python -m scripts.explore.fetch_deck_corpus              # fetch + validate codes
  python -m scripts.explore.fetch_deck_corpus --no-fetch --engine --export-reports
  ```

## `decks.json` schema

Top level:

| field | meaning |
| --- | --- |
| `generated_at` | UTC ISO-8601 generation time |
| `generator` | script path |
| `sources` | API base / endpoints / web-source file |
| `counts.total` / `.validated` / `.invalid` / `.engine_ok` | corpus stats |
| `decks` | list of deck entries |

Deck entry:

| field | meaning |
| --- | --- |
| `slug` | stable ASCII id (e.g. `cs-mp-01`, `gs-07`, `bili-wq-1`) |
| `title` | deck title (author's / guide's) |
| `source` / `source_url` / `source_title` | provenance |
| `author`, `created_at`, `tags`, `stats` | metadata (miHoYo stats: like/favour/view/id) |
| `share_code` | official 七圣召唤 share code |
| `characters` | 3 character definition ids (decoded) |
| `cards` | 30 action-card definition ids (decoded) |
| `character_names`, `card_names` | human names |
| `core_cards` | compact package summary (2× equipment/talents + notable supports) |
| `archetype` | advisory heuristic label (see report) |
| `guide` | author's guide `desc` (miHoYo) or curator note (web) |
| `also_seen_in` | other endpoint slugs with the same share code (deduped) |
| `validation` | `share_code_decode`, `deck_shape`, `deck_shape_ok`, `ids_known`, `unknown_ids` |
| `validation.engine_game` | `ok`, `winner`, `rounds`, `error`, `truncated`, `opponent`, `seed`, `wall_seconds` |

## Notes / limitations

- The miHoYo pool is an **archive** (decks dated 2023-07 → 2024-01, versions 3.7–4.3).
  It is the official public gallery but not the current 5.x/6.x meta.
- Current-meta coverage comes from the web-guide entries (few, hand-picked).
- `archetype` labels are heuristic and advisory; `core_cards` is a convenience
  summary, not an exhaustive list.
- Everything validates at the **codec/deck-shape** level; `engine_game` records
  whether one scripted game (`heuristic` vs `legal_random`) played with no error.
