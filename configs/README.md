# configs — run 配置与版本锁定（PLAN.md §9、D4）

```
configs/
├── engine.lock          # 现行版本锁定（每个 run 强制引用其拷贝）
├── engine.lock.example  # 新 run 的模板
├── npc/                 # PvE 关卡规格 + 敌人数据（见 npc/README.md）
└── pools/               # AI 侧卡池 id 清单（见 pools/README.md）
```

## engine.lock（D4）

记引擎 commit + `pybinding_version` + 卡池 `data_version` + 运行时（Python/平台/洗牌口径）。
每个训练 run **复制本文件**并随 run 元数据保存（`common/lock.py:runtime_metadata`），checkpoint/数据/报告都可用它复现。

- 新 run：复制 `engine.lock.example` 填值，或从现行 `engine.lock` 拷贝后按需改。
- **卡池升级 = 显式迁移**：复制为新 lock（`engine.lock.vN`）并开新 run；旧 run 用旧 lock 可重放。

## 约定

- 本目录只放 **id 清单 / 规格 / 锁定**，不放卡面数据与抓取物（抓取物在 gitignore 的 `data/`）。
- 历史文件**只追加不覆盖**：卡池升级新增文件，不就地改写。
