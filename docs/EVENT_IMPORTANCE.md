# 事件重要性

> 状态：Event Importance v4 已实现
>
> 策略版本：`event-importance-v4-normalized-item-projection`

## 职责边界

Event Importance 是 membership 完成后的 projection。它回答“组成这个 Event 的有效 material
observations 中，当前最强的重要性信号是什么”，但不能决定 observation 属于哪个 Event。

新的事件聚合模型不输出 importance profile、features 或分数，也不会因为 importance 拒绝 attach 或
create。事件 membership 落库后，`refresh_event_metrics()` 从 EventMention 关联的
`NormalizedItem.importance_score` 和 `importance_calculation.importance_profile` 计算 Event
Importance。

## 聚合规则

```text
event_importance_score = max(valid material mention normalized-item scores)
```

- 只有 `materiality=material_update` 的 mention 贡献 importance。
- `duplicate`、`context_only` 和 `corroboration_only` 不贡献。
- 分数必须在 `[0, 1]`，profile 必须属于已有受控 profile 集合。
- 0.80 后出现 0.55 的普通补充，Event 保持 0.80；之后出现 0.86 的重大观察，Event 升至 0.86。
- Breakdown 记录 dominant item/profile、贡献数量、最多 10 条 evidence 以及被忽略原因。

`EventMention.impact_snapshot.domain_importance` 是已有的历史审计格式。服务层仍能读取和校验显式
snapshot，以兼容人工写入和已保存证据；自动 V2 membership 路径不再生成 snapshot。当历史
snapshot 存在时优先使用它，否则读取该 mention 关联的 NormalizedItem importance 结果。

## 实现位置

- `domain/importance.py`：消息重要性 profile 和 scorer。
- `domain/event_importance.py`：Event evidence 聚合。
- `services/event_metrics.py`：membership 完成后的 Event Importance 刷新。
- `services/events.py`：EventMention 持久化及可选历史 snapshot 校验。
