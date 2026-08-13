# LeagueNews 当前架构

更新时间：2026-08-13

## 当前主链路

```text
Source 调度或手工触发
  -> Connector
  -> immutable RawItem + MediaAsset + provenance
  -> Pipeline Job
  -> relevance
  -> optional image_ocr
  -> translation
  -> message_analysis
  -> importance
  -> NormalizedItem 发布
  -> Pipeline Worker durable downstream job
  -> optional single-call EventMention[] aggregation
  -> Event current projection + evidence
```

当前运行时包含 Connector、共享 ingestion、RawItem 修订、媒体落盘、自动任务、人工审核、
OCR、翻译、受控消息分类、实体/摘要提取、消息重要性算法、事件聚合、公开消息/事件页和管理台。
消息处理自身以 NormalizedItem 为输出边界，Pipeline Worker 只消费该发布结果。

事件 V2 已实现：包含确定性准入/召回、单次多 mention 模型接口、原子 membership 应用，以及
相互独立的重要性/可信度/热度投影。Pipeline Worker 在 NormalizedItem 发布后消费 durable
downstream job；事件失败不会回写已经成功的消息处理运行。
事件列表、详情 API 和现有 Next.js 栈内的公开页面已经接入。
总设计与当前字段映射见 [`EVENT_AGGREGATION.md`](EVENT_AGGREGATION.md)；事件继续位于
NormalizedItem 之上，不回写 RawItem，也不重复执行消息处理阶段。

公开消息投影同时返回已批准的 `topics` 和 Source 的 `reliability_score`；消息流与详情页直接
展示这两个字段。可信度只用于说明信源属性，不参与消息重要性加分。

## 消息分类

权威规则：[`MESSAGE_CLASSIFICATION.md`](MESSAGE_CLASSIFICATION.md)。运行时字段：

- `products`：多选，尽量单选，最多 3 个；
- `content_form`：单选；
- `message_type`：单选，受产品与本轮分类信源三态约束；
- `topics`：多选，受产品约束；
- `classification_version`：当前为 `message-taxonomy-v3`。

`original` 和 `quote` 使用当前 Source 的官方性质；`repost` 只在结构化 URL 能与已配置 Source
稳定匹配时使用上游官方性质，否则使用 `unknown` 并披露官方、非官方候选并集。采用的
`current_source_kind/source_kind/basis/upstream_source_url` 保存在消息分析与重要性提案、checkpoint
以及最终 `facets.classification_source` 中。该分类信源只控制 message type 候选，不能证明事件获得
官方确认。

纯媒体或纯链接仍由 LLM 判断内容形式，但其他语义轴强制为 `unknown`，摘要和实体为空，重要性
为 0。LLM 可以为真正无标题的消息返回空标题；发布前由程序确定性补为“仅媒体消息”或
“仅链接消息”。相关性在此之前完成，无关消息不会发布。

## 架构边界

- Connector 是平台能力；Source 是具体账号或站点。
- Connector 只映射 `RawItemCandidate`，共享 ingestion 负责校验、去重、媒体、provenance、
  RawItem 持久化和任务入队。
- `raw_items.content_blocks` 是不可变原始证据，处理和审核不得回写。
- `normalized_items` 是当前消息发布投影，历史保存在 `normalized_item_revisions`。
- 自动与人工路径共享提案、Schema/业务校验和 checkpoint。
- SQL 迁移是追加式历史，不能修改已有编号文件。
- Event 列表和详情 GET 只读取已持久化投影；过滤、排序、分页和聚合计数在 SQL 中完成。
- EventMention 的当前投影必须同时满足已发布且 revision 等于 NormalizedItem.current_revision；旧
  revision 只保留为审计证据，不进入当前事件指标、引用、详情或日报去重。
- Event 热度衰减由 Pipeline Worker 定期刷新，HTTP 请求不承担后台状态更新。

## 页面与 API

```text
/                         已发布消息
/messages/{id}            消息详情
/events                   事件列表
/events/{id}              事件详情
/daily?date=YYYY-MM-DD    日报
/admin                    管理台
/admin/messages           消息列表
/admin/messages/{id}      消息详情与原文
/admin/pipeline           自动任务
/admin/reviews            审核队列
```

```text
/api/v1/sources
/api/v1/connectors
/api/v1/collection-schedules
/api/v1/imports
/api/v1/raw-items
/api/v1/workflows
/api/v1/normalized-items
/api/v1/events
/api/v1/reports
/api/v1/pipeline
/api/v1/knowledge
/api/v1/ocr-lab
```

## 数据表职责

| 表 | 职责 |
| --- | --- |
| `sources` / `source_collection_schedules` | 信源与采集计划 |
| `connector_runs` | 采集运行结果 |
| `raw_items` / `raw_item_source_payloads` | 不可变原文与 provenance |
| `media_assets` / `media_extractions` | 媒体与 OCR 结果 |
| `processing_runs` / `review_tasks` | 消息处理运行、草稿和决定 |
| `processing_checkpoints` | 已接受阶段快照 |
| `pipeline_jobs` / `pipeline_corrections` | 自动任务、恢复和按阶段重跑 |
| `knowledge_rules` / `glossary_terms` | 分析规则与术语 |
| `normalized_items` / `normalized_item_revisions` | 当前发布投影与历史 |
| `events` / `event_mentions` / `event_revisions` | 事件当前投影、mention 证据与历史修订 |
| `event_aggregation_runs` | 准入、候选、调用次数、结构化决定和应用结果审计 |
| `daily_reports` / `daily_report_items` | 日报当前投影与消息排序 |

最新迁移为 `069_fence_event_runs_by_revision.sql`。SQL migration 是生产数据库的
唯一结构来源：新数据库和历史数据库都执行同一条有序 migration 链，不再使用 ORM `create_all()`
初始化生产结构。不得绕过追加迁移直接修改。全新 001→069 初始化与旧 031→069 顺序升级已在
可销毁 PostgreSQL 17 上验证。

## 验证

```bash
services/api/.venv/bin/python -m ruff check services/api/app services/api/scripts services/api/tests
services/api/.venv/bin/python -m pytest services/api/tests -q
pnpm lint:web
pnpm build:web
```
