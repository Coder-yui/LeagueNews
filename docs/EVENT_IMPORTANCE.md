# 事件重要性

> 状态：Event Importance v3 已实现
>
> 策略版本：`event-importance-v3-mention-snapshot`

## 职责

Event Importance 回答“这个现实事件目前已知的整体重要性”。Message 和 EventMention 共用同一套
LeagueNews Domain Importance Policy，但共享 policy 不等于继承整条 Message 的 domain score：

- Message Domain Importance 针对整条消息，由 `message_type + topics + content` 派生 profile。
- EventMention Domain Importance 针对该独立 Event 本体，由事件聚合的现有一次模型调用选择受控
  profile 和必要的 bounded features，再由服务器端共享 scorer 确定分数。
- Event Importance 聚合多个不可变 EventMention evidence。

因此一篇 `patch_official_notes=0.92` 的综合公告拆出的平衡、活动、皮肤 Event 不会全部继承 0.92；
它们分别使用自身的 gameplay、activity、cosmetic profile。模型永远不输出分数，也没有新增调用。

## EventMention Snapshot

每个 material EventMention 创建时固化：

```text
normalized_item_id + normalized_item_revision + mention_index
impact_snapshot.domain_importance:
  policy_version
  profile
  score
  features{}
  modifiers[]
```

`impact_snapshot` 是旧四维 impact 遗留的现有 JSON 列。本版本复用该列，运行时只使用清晰的
`domain_importance` 子结构，因此不需要 migration，也不同时保留两套 snapshot。服务 API 参数已经改为
`domain_importance_snapshot`；新聚合路径不再产生 scope/magnitude/duration/urgency。

Snapshot 对应 mention 的 NormalizedItem revision，创建后不再读取 NormalizedItem 当前
`importance_calculation`。消息之后重新处理或进入新 revision，不会静默改变旧 evidence；新 revision
若生成 mention，会拥有自己的 snapshot。

## 聚合与过滤

刷新入口从 `EventMention.domain_importance_snapshot` 构造 evidence：

```text
event_importance_score = max(valid material mention domain scores)
```

只有 `materiality=material_update` 且 profile/score 合法的 snapshot 能贡献。`duplicate`、
`context_only`、`corroboration_only` 不贡献；credibility、heat、message/source count 仍然正交。

- 0.80 后出现 0.55 的普通补充，Event 保持 0.80。
- 之后出现 0.86 的重大事实，Event 升级到 0.86。
- repost 只降低 Message final score；EventMention 仍使用事件自身的 domain score。

新 material mention 必须携带经服务器端校验的 snapshot，非 material mention 禁止携带。历史或开发
数据缺失 snapshot 时，刷新会记录 `missing_or_invalid_domain_score` 并按 0 处理，绝不回读当前
NormalizedItem score 作为静默 fallback。

Breakdown 保存 dominant profile/item、贡献数量、最多 10 条简要 evidence，以及被忽略 evidence 的
原因。每条 evidence 同时记录 normalized item revision 和 mention index，便于审计。

## 实现位置

- `domain/importance.py`：profile 路由、唯一共享 profile scorer、Message consumer 和 Priority。
- `schemas/event_aggregation.py`：受控 EventMention importance semantics。
- `workflows/event_aggregation.py`：调用共享 scorer 并生成不可变 snapshot。
- `services/events.py`：校验并持久化 snapshot。
- `domain/event_importance.py`：简单的有效 evidence 最大值聚合。
- `services/event_metrics.py`：只从 mention snapshot 刷新 Event projection。
