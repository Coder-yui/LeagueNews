# 日报 V1

日报是从已经完成处理的消息中生成的一份精选列表，不重新调用 LLM 做日报总结、重要性判断或产品分类。

## 规则

- 候选消息使用 `RawItem.published_at` 按 `Asia/Shanghai` 自然日划分，查询使用 UTC 时区感知的半开区间 `[00:00, 次日 00:00)`。
- 只保留 `NormalizedItem.publication_status = published`、`content_form = original` 且 `importance_score >= 0.60` 的消息。
- 通过已有 `EventMention` 的 `event_id` 去重。候选按重要性降序后，每个事件只保留先遇到的消息；没有事件关联的消息各自独立，不互相去重。
- 去重发生在栏目截取之前，随后按产品归属、重要性降序、发布时间降序和消息 ID 进行稳定排序。

栏目和上限是：`lolpc` 5 条、`esports` 3 条、`tft` 3 条、`other` 3 条。多产品消息按固定优先级只进入一个栏目：电竞、LoL PC、TFT、其他。未知产品和其他英雄联盟产品进入 `other`。

## 数据与接口

`daily_reports` 按 `report_date` 唯一保存日报主记录，`daily_report_items` 保存已有 `normalized_item_id`、栏目和栏目内位置，不复制消息正文。同一天再次生成会删除该日报的条目并重建，因此是覆盖式幂等行为。

- `GET /api/v1/reports/daily/{date}`：读取已保存日报。
- `POST /api/v1/reports/daily/{date}/generate`：按指定日期生成或覆盖日报。
- `POST /api/v1/reports/daily/{date}/withdraw`：退回日报，公开读取返回 404，但保留日报条目和历史状态。
- `GET /api/v1/reports/daily`：管理端读取最近日报及状态、栏目计数。

## 自动生成

Scheduler 进程按 `Asia/Shanghai` 时区每天 12:00 生成当天日报。调度状态由
`daily_reports.updated_at` 持久化判断；进程在 12:00 后启动时会补生成，重复实例通过同一日期的
PostgreSQL advisory lock 和 `report_date` 唯一约束串行化。当天尚无已发布消息时不创建空日报；
12:00 后一旦有消息完成发布，会在下一轮检查时补生成。同一天仍可通过生成 API 手工覆盖。
人工退回的日报不会被自动任务重新发布，只有管理台“生成 / 重新生成”操作会恢复为 `published`。

相关配置：

- `DAILY_REPORT_AUTOMATION_ENABLED`：默认 `true`；
- `DAILY_REPORT_GENERATION_HOUR`：北京时间小时，默认 `12`；
- `DAILY_REPORT_SCHEDULER_POLL_SECONDS`：检查间隔，默认 `30` 秒。

前端页面为 `/daily`，消息卡片复用消息流组件，仍可进入原消息详情。

## 明确不做

V1 不包含 AI 日报总结、事件摘要或趋势分析、个性化推荐、推送、周报或新评分体系。自动生成复用
现有 Scheduler 进程，不引入第二套后台任务基础设施。

后续版本可以在不改变本 V1 精选规则的前提下增加定时生成、事件视图或摘要能力。
