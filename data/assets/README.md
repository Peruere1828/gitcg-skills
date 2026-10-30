# data/assets — 卡牌/角色资产目录缓存（gitignore）

`expert_system_assets_chs_latest.json`（~3.4 MB）是七圣召唤 CHS **资产目录**的缓存：
逐条含 `id / shareId / sinceVersion / name / englishName / tags(GCG_TAG_*) / skills / hp / maxEnergy / playCost ...`。

**本目录不入库**（`.gitignore` 的 `data/**`），只提交本 README。仅元数据，不含卡面素材。

## 谁在用

- `agents/scripted/assets.py::load_assets()` — 脚本对手/卡池的资产加载器；**缓存缺失时自动下载**。
- `tests/test_deck_corpus.py` — 读取同一缓存。
- `configs/pools/` 的卡池口径派生（区域 `GCG_TAG_NATION_*` + 版本 `sinceVersion`）。

## 来源与再生成

- 源：`https://gi-tcg-assets-api-hf.guyutongxue.site/api/v4/data/latest/CHS/all`
  （见 `agents/scripted/assets.py` 的 `ASSETS_URL`；请求头 `X-Gi-Tcg-Assets-Manager: 1`）。
- 自动：删除本文件后调用 `load_assets()` 会重新下载；`load_assets(refresh=True)` 可强制刷新。
- 离线：`GITCG_ASSETS_OFFLINE=1` 时缺缓存直接报错（不联网）；缓存路径可用 `GITCG_ASSETS_CACHE` 覆盖。
