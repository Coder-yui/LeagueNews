# 事件重要性

> 状态：Phase 3 已实现
>
> 策略版本：`event-importance-v1`

## 目标与非目标

事件重要性回答“事件本身影响有多大”，与来源身份、消息数量、可信度和热度无关。模型只为每个
创建或实质更新的 mention 提取离散影响维度，代码用版本化常量确定性计算并保存 breakdown。

本算法不复制 `NormalizedItem.importance_score`，不因官网发布加分，不因多人讨论加分，也不把
紧急流行误当作长期影响。

## 输入、输出与字段

输入：`event_family`、`products` 与模型输出的四个受控维度。

```text
scope: individual | group | product_segment | product_wide | ecosystem
magnitude: minor | moderate | major | transformative
duration: transient | short_term | cycle_or_season | long_term
urgency: none | timely | immediate
```

输出：

```text
importance_score: 0.0 .. 1.0
importance_level: low | medium | high | critical
importance_breakdown:
  policy_version, event_family_base, dimensions{}, raw_points, capped_points, evidence[]
```

数据库沿用 0–1 分数约束，API 展示时乘 100。等级阈值为 `<0.30 low`、`<0.55 medium`、
`<0.80 high`、其余 `critical`。

## 计算公式

```text
points = clamp(
  event_family_base
  + scope_points
  + magnitude_points
  + duration_points
  + urgency_points,
  0,
  100
)
importance_score = points / 100
```

family 基准：

| family | base |
| --- | ---: |
| gameplay_balance | 18 |
| gameplay_release | 25 |
| cosmetic_release | 10 |
| player_activity | 10 |
| commercial_offer | 8 |
| service_incident | 20 |
| security_enforcement | 22 |
| esports_match | 8 |
| esports_schedule | 10 |
| roster_change | 15 |
| esports_rules | 20 |
| universe_release | 15 |
| media_release | 15 |
| corporate_change | 25 |
| platform_service | 22 |
| other_named_development | 10 |

维度分：

| 维度 | 枚举到分值 |
| --- | --- |
| scope | individual 0；group 8；product_segment 14；product_wide 22；ecosystem 28 |
| magnitude | minor 0；moderate 10；major 20；transformative 30 |
| duration | transient 0；short_term 4；cycle_or_season 10；long_term 16 |
| urgency | none 0；timely 4；immediate 8 |

所有常量进入代码映射并随策略版本持久化。Prompt 只解释枚举，不包含另一套隐藏分值。

## 更新规则

- 创建事件或 `materiality=material_update` 时计算新 breakdown。
- `corroboration_only`、`duplicate`、`context_only` 不计算也不改变重要性。
- 官方确认若没有改变影响事实，只改变可信度；不重算重要性。
- 修正若确实改变 scope/magnitude/duration/urgency，可以升高或降低事件当前分数；旧值保存在
  `EventRevision` 快照。
- 同一综合公告拆出的 A、B、C 分别使用各自 impact，不读取整篇消息的重要性作为共同分数。

## 示例

### 高重要性、低热度

一条尚未广泛传播的跨产品账户安全变更：`platform_service(22) + ecosystem(28) + major(20) +
long_term(16) + timely(4) = 90`，重要性 0.90；只有一条消息仍可保持低热度。

### 低重要性、高热度

大量账号讨论一款普通皮肤：`cosmetic_release(10) + individual(0) + minor(0) +
cycle_or_season(10) + none(0) = 20`，重要性 0.20；热度可很高。

### 官网综合公告

同一官网版本公告中的平衡事件可能为 0.62，活动为 0.28，皮肤发布为 0.20。官方身份不会改变
这三个分数，只可能改变各自可信度。

## 边界情况

- 事件跨多个产品时按事实实际 scope 判断，不自动使用 ecosystem。
- `immediate` 表示需要马上应对，不等于重大；服务小故障可以紧急但总体仍不高。
- 持续很久但影响人数少的事项只在 duration 加分，不能双重抬高 scope。
- 模型证据与枚举矛盾或缺失时拒绝应用，不以消息重要性兜底。
- 事件影响已经确定后，纯转发、复述和新增来源不能刷新 breakdown。

## 实现对应、可调参数与未解决问题

实现位于 `services/api/app/domain/event_importance.py`，schema 在
`schemas/event_aggregation.py`，持久化与 revision 在 `services/events.py`。单元测试覆盖每个枚举、
封顶、指标正交、确认不加分、修正可降分和综合公告分项。

可调参数是 family base、四维分值和等级阈值。需要通过固定回归集校准电竞决赛、跨产品平台事故
和大型版本平衡的相对排序；在评估完成前不增加消息分数映射或复杂乘法模型。
