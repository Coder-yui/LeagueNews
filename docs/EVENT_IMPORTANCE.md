# 事件重要性

> 状态：Event Importance v2 已实现
>
> 策略版本：`event-importance-v2-domain-evidence`

## 目标与职责

Event Importance 回答“这个现实事件目前已知的整体重要性”。它不重新判断 LeagueNews 对什么
内容重要，而是复用消息处理阶段已经生成并持久化的 Domain Importance evidence。

三个概念保持分离：

- Domain Importance：资讯内容本身有多重要，存于 `NormalizedItem.importance_calculation` 的
  `importance_profile` 和 `profile_score`。
- Message Importance：具体消息有多值得展示，可能包含 `repost -0.08` 等消息载体修正。
- Event Importance：一个事件的多条有效领域证据聚合结果。

Event Importance 与 credibility、heat、消息数量和来源数量正交。确认与多来源传播可以改变可信度
或热度，但不表示事件本身变得更重要。

## 输入与算法

EventMention 指向 NormalizedItem。刷新事件投影时，从当前已发布 mentions 读取：

```text
NormalizedItem.importance_calculation.importance_profile
NormalizedItem.importance_calculation.profile_score
EventMention.materiality
```

只有 `materiality=material_update` 且 profile/domain score 合法的 mention 能贡献重要性。
`duplicate`、`context_only` 和 `corroboration_only` 不贡献分数。

```text
event_importance_score = max(valid material domain scores)
```

聚合使用 `profile_score`，不使用最终 `NormalizedItem.importance_score`。因此转载扣分只降低具体消息，
不会降低它所描述事件的重要性。算法不调用 LLM，不重新分析全文，也不重新执行消息分类。

## 更新语义

Event Importance 是整个 Event 的 projection。创建或 evidence 变化后，系统从事件的全部有效 material
mentions 重算，而不是用最新一条 material update 覆盖旧值。

- 0.90 的重大事实后出现 0.55 的普通补充，事件仍为 0.90。
- 0.60 的初始事实后出现 0.72、0.84 的实质进展，事件升级到 0.84。
- duplicate、上下文、佐证、来源数量、可信度和热度变化不改变事件重要性。

## Breakdown 与回退

`importance_breakdown` 保存：

```text
policy_version: event-importance-v2-domain-evidence
method: max_material_domain_score
score, level
dominant_profile
dominant_normalized_item_id
contribution_count
contributing_evidence[]
ignored_evidence_count
ignored_evidence_reasons{}
```

贡献摘要最多保存 10 条，按 domain score 降序排列。每条只包含 normalized item id、profile、domain
score 和 materiality。

缺失、类型异常、越界或缺少 profile 的 evidence 不回退到最终消息分，也不会触发新模型调用；系统
忽略该 evidence，在 breakdown 中记录原因。若没有任何有效 evidence，安全回退为 0.0，并保持流程
可审计。

## Impact 四维兼容性

Event aggregation 不再要求模型输出 `scope / magnitude / duration / urgency`，这些字段也不再参与
Event Importance。现有 `event_mentions.impact_snapshot` 数据库列和服务兼容参数暂时保留，避免为本次
内部重构新增或扩大 migration；新聚合流程不再写入该字段。后续可在独立的兼容性清理中移除。

## 实现位置

- `domain/importance.py`：唯一的 Domain Importance Policy，以及 Message Importance 和 Priority。
- `domain/event_importance.py`：有效 evidence 校验、最大值聚合和 breakdown。
- `services/event_metrics.py`：从 EventMention/NormalizedItem 构造 evidence 并刷新 Event projection。
- `services/events.py`：创建事件或添加 mention 后调用同一刷新入口，不再按最新 impact 覆盖。
