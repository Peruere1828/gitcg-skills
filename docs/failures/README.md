# failures — 负结果索引

> 每条失败必须回答：**为什么失败**、**什么条件下才重开**。
> 成功方法见 [`../PLAYBOOK.md`](../PLAYBOOK.md)；一次性证据（JSON/表格）放 `reports/`。

| # | 主题 | 结论 | 状态 | 重开条件 |
|---|---|---|---|---|
| — | （暂无本项目负结果） | — | — | — |

## 沿用的上游教训（只作设计约束，不作为本项目结论）

来自环境层上游 `genshin-GITCG` 的 `docs/failures/`（详见 `docs/PROVENANCE.md`）：

| 主题 | 对我们的约束 |
|---|---|
| matchup 重加权 / 胜局筛选无效（−0.14） | 技能筛选不能只看整局胜负，必须同种子配对 |
| LLM 自评/搜索 teacher 无增益 | 技能去留不交给 LLM 判断，交给配对证据 |
| compact fp16 破坏 definition id | 轨迹日志必须存 exact id + 名字 |
| 20-deck 单模型退化 | 不作为技能库的组织方式（本项目按关卡/目标分层，不是按 deck 混训） |

## 模板

```markdown
## NNN — <主题>
- status: negative | invalid | limited
- 证据: reports/<file>.json（口径：stage/seeds/n/group/library_version/model/date）
- 为什么失败: ...
- 重开条件: ...
```
