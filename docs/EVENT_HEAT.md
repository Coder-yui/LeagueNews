# 事件热度

> 状态：Phase 3 计算与写入刷新已实现；Phase 4 接入读取时 TTL 刷新
>
> 策略版本：`event-heat-v1`

## 目标与非目标

热度回答“最近有多少消息和来源在讨论”，完全由代码计算，不调用模型。它允许转载传播增加，
同时对时间、完全重复采集和同一来源刷屏降权。热度不改变事件重要性或可信度。

## 输入与输出

输入是事件的 active mentions、消息发布时间、Source、`content_form`、materiality、上游关系、
RawItem 修订链和内容 fingerprint。

持久化缓存：

```text
message_count_total
message_count_24h
unique_sources_24h
heat_score: 0.0 .. 1.0
heat_calculated_at
heat_breakdown
```

API 同时返回真实统计文本所需数字以及等级：`cold | emerging | active | hot | surging`，阈值依次为
0.15、0.35、0.60、0.80。

## 权重与时间衰减

消息基础权重：

| 类型 | weight |
| --- | ---: |
| 原创报道/公告 | 1.0 |
| 引用且提供实质新增内容 | 0.7 |
| 引用但只提供简短上下文 | 0.4 |
| 纯转发或搬运 | 0.25 |
| 同一 Source 的完全重复采集或 RawItem 旧修订 | 0 |

以发布时间为基准，缺失时使用 ingested_at：

```text
age_hours = max(0, as_of - published_at)
time_decay = 2 ** (-age_hours / 12)
message_heat = base_weight * time_decay * source_repeat_factor
heat_raw = sum(message_heat within 7 days)
heat_score = 1 - exp(-heat_raw / 6)
```

同一 Source 在滚动 6 小时内对同一事件的第 1/2/3/后续条分别乘 `1.0 / 0.5 / 0.25 / 0.1`。
跨 Source 的转载仍各自贡献 0.25，因此 20 个账号搬运会提高热度；它们不会因为共享
independence_group 增加可信度。

## 去重与统计

- `message_count_total`：active mentions 对应的不同 `normalized_item_id` 总数。
- `message_count_24h`：过去 24 小时不同消息数，不按 mention 数重复计数。
- `unique_sources_24h`：过去 24 小时不同当前 Source 数；这是传播统计，不表示独立证据数。
- 同一 RawItem 修订链只保留最新已发布项参与热度，旧修订权重为 0。
- 同一 Source、同一上游、同一规范化内容 fingerprint 的重复采集只计一次。
- 不同 Source 搬运同一上游不做全局 0 权重，而按转载权重计传播。
- withdrawn/superseded 消息不参与当前热度，历史总数的口径在 breakdown 中记录。

## 刷新策略

v1 采用“缓存分数 + 计算时间 + 按需刷新”：

- 写入新 mention 后立即刷新受影响事件。
- 列表/详情读取时，若 `heat_calculated_at` 早于 5 分钟，服务在受控批次中刷新。
- 排行查询只使用刷新后的缓存；维护命令可批量刷新活跃事件，但不是正确性的唯一来源。

选择该方案是因为当前项目没有通用定时任务基础设施，而热度必须在没有新消息时自然下降。
纯查询 SQL 每次重算会让列表成本不可控；只在写入时计算又会永久写死分数。

## 示例

- 一条 1 小时前原创消息约贡献 `1.0 × 0.944 = 0.944`。
- 20 个不同账号在短期内搬运同一爆料，每个可贡献约 0.25；总热度明显上升，但可信度仍按一个
  upstream group。
- 同一账号 6 小时内连续发 10 条，后续只按 0.1 系数，不能靠刷屏主导排行。
- 高重要性的冷门平台规则变更可以是 `critical + cold`；普通皮肤争议可以是 `low + hot`。

## 边界情况

- 未来时间戳按 age=0 计算并记录异常，不获得额外加成。
- 删除/撤回证据后必须刷新统计和热度，不保留幽灵消息数。
- `context_only` 仍可按内容形式影响热度，因为它代表讨论；完全重复采集仍为 0。
- 一个消息有三个事件 mention 时，对三个事件各贡献一次，但每个事件内只计一条消息。
- 热度刷新失败不阻断事件证据事务；保留旧 cache 并记录 stale，读取路径重试。

## 实现对应、可调参数与未解决问题

计算实现为 `domain/event_heat.py`，聚合查询与写入刷新位于 `services/event_metrics.py`；读取时 TTL
刷新将在 Phase 4 API 接入。可选维护入口放在现有 scripts，
不建立新 worker。测试使用固定 `as_of`，覆盖衰减、不同 Source 转载、同 Source 限流、版本链去重和
自然下降。

可调参数为基础权重、12 小时半衰期、7 天窗口、6 小时 Source 窗口、重复系数、归一化常数 6、
缓存 TTL 和等级阈值。需要在真实数据量上校准“20 个转载”的曲线以及批量刷新查询成本。
