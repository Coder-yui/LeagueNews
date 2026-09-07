# LeagueNews 当前架构

更新时间：2026-09-07

## 当前主链路

```text
Source 调度或手工触发
  -> Connector
  -> immutable RawItem + MediaAsset + provenance
  -> PostgreSQL Pipeline Job + lease/fencing
  -> LangGraph Item Graph
     evidence -> relevance -> media -> translation
     -> message_analysis -> importance -> evidence_gate -> publication
  -> NormalizedItem 发布
  -> LangGraph Event Graph
     load_message -> minimal_filter -> candidate_retrieval
     -> semantic_decision -> apply_membership -> refresh_projection
  -> Event current projection + evidence
  -> Daily report scheduler（只负责窗口、资格与重生成判断）
  -> LangGraph Daily Report Graph
     load_window -> select_candidates -> plan -> publish

Published messages and durable failures also produce records in the notification outbox. The
collection-scheduler process runs the notification dispatcher, which delivers those records to the
separate featured-message and alert Feishu bots. Notification delivery is a side channel and never
changes the success or rollback semantics of the core pipeline.
```

当前 V3 本地运行时包含 Connector、共享 ingestion、RawItem 修订、媒体落盘、自动任务、人工审核、
OCR、翻译、受控消息分类、实体/摘要提取、消息重要性算法、事件聚合、公开消息/事件页和管理台。
消息、事件和日报的在线入口统一通过 `app/orchestration` 的版本化 Graph Registry 与 Runtime；
`app/workflows` 中仍被引用的代码是 V2 领域算法 baseline 和历史运行兼容层，不再是新运行入口。

运行时分层如下：PostgreSQL 保存 RawItem、业务投影、PipelineJob、审计记录和 LangGraph checkpoint；
Pipeline Worker 负责 claim、lease/fencing、重试和下游任务编排；LangGraph 负责有版本的节点状态机、
checkpoint 恢复和人工 interrupt；`app/methods` 只承载可替换的领域方法与策略；`app/services` 负责
入库、查询、队列、通知和业务边界。Connector 不直接调用图或写 NormalizedItem，图也不绕过共享服务
直接实现采集能力。

### 图版本、状态版本与方法配置

`ITEM_PROCESSING_GRAPH_VERSION`、`EVENT_AGGREGATION_GRAPH_VERSION` 和
`DAILY_REPORT_GRAPH_VERSION` 是 LeagueNews 的业务图版本，不是 LangGraph Python 包版本。业务图注册表
严格按 `(GraphName, graph_version)` 查找，只注册已实现的真实图；未实现版本不会以 `PLANNED`、占位图
或 latest/fallback 协商方式进入运行时。LangGraph 包升级不自动改变这些业务版本。

每个运行同时保存 `graph_version`、`state_version` 和方法快照。例如：

```json
{
  "graph_version": "v3.0.0-dev2",
  "state_version": 1,
  "method_config": {
    "message_analysis": "baseline",
  }
}
```

版本不做自动兼容：旧 checkpoint 或旧业务状态必须由显式迁移、重启或新的 graph 入口处理，不能由
Registry 静默选择其他版本，也不在本轮提供跨版本 checkpoint adapter。

### 方法组装与调用边界

当前生产 `MethodAssembly` 由 Runtime、后端或明确的 use-case 入口组装并传递。事件候选召回、精选
选择、日报候选/排序和发布通知都必须收到显式 assembly；低层 service、scheduler 和 backend 不再
偷偷创建 baseline assembly。日报 scheduler 只判断上海自然日窗口、消息资格、既有报告与 late update
重生成条件，然后触发日报图；它不提前执行 selection，避免图外重复选择。

日报图的唯一阶段顺序是 `load_window -> select_candidates -> plan -> publish`，日报没有人工 review
阶段。查询和通知使用与图相同的 assembly/config，方法替换因此在实验、在线读取和入队之间保持一致。

### 媒体与 OCR

`media` 是当前消息图的活动阶段：媒体下载/解析后，patch 图片走
`image -> OCR -> parse -> MediaExtraction`，结果以媒体提取和检查点形式进入后续翻译、分析和发布。
`image_ocr` 仅作为旧运行、旧审核任务和旧通知的历史读取兼容值；新运行、新审核和新 API schema 使用
`media`，不得把 `image_ocr` 当作当前阶段重新写入。

事件 baseline 包含确定性准入/召回、单次多 mention 模型接口、原子 membership 应用，以及
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
- `classification_version`：当前为 `message-taxonomy-v4`。

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
- PipelineJob 的执行身份唯一由 `workflow_name + target_entity_type + target_entity_id + target_revision`
  决定。`raw_item_id` 只用于 provenance、所有权、查询和 RawItem 修订 supersession；执行去重、活动任务
  冲突和失败恢复不得只按 `raw_item_id` 判断。
- 自动与人工路径共享提案、Schema/业务校验和 checkpoint。
- LangGraph PostgreSQL checkpoint 保存节点级技术恢复状态；`processing_checkpoints`、
  `review_tasks` 和 revision 表保存长期业务审计，二者不互相替代。
- 日常自动处理通常串行；实验 Runner 使用有界并发，不依赖 Redis 或 Celery。
- SQL 迁移是追加式历史，不能修改已有编号文件。
- Event 列表和详情 GET 只读取已持久化投影；过滤、排序、分页和聚合计数在 SQL 中完成。
- EventMention 的当前投影必须同时满足已发布且 revision 等于 NormalizedItem.current_revision；旧
  revision 只保留为审计证据，不进入当前事件指标、引用、详情或日报去重。
- Event 热度衰减由 Pipeline Worker 定期刷新，HTTP 请求不承担后台状态更新。
- Media 下载按每一个 redirect hop 重新验证 URL 并选择 direct/proxy client：literal IP 永远要求
  global；direct hostname 的所有 DNS 结果必须 global；proxy hostname 只能是 X/Riot 已知媒体 CDN
  suffix allowlist。fake-IP 环境不以 DNS 放宽该 allowlist，未知 host fail closed。
- 本地 destructive maintenance 脚本 dry-run 默认、要求 `--apply` 和确认（`--yes` 显式跳过），在
  同一事务内完成删除、invariant 验证和 commit；删除 EventMention 后会删除空 Event 或重建保留
  Event 的完整 derived projection。

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
| `checkpoints` / `checkpoint_blobs` / `checkpoint_writes` | LangGraph 技术恢复状态 |
| `pipeline_jobs` / `pipeline_corrections` | 自动任务、恢复和按阶段重跑 |
| `knowledge_rules` / `glossary_terms` | 分析规则与术语 |
| `normalized_items` / `normalized_item_revisions` | 当前发布投影与历史 |
| `events` / `event_mentions` / `event_revisions` | 事件当前投影、mention 证据与历史修订 |
| `event_aggregation_runs` | 准入、候选、调用次数、结构化决定和应用结果审计 |
| `daily_reports` / `daily_report_items` | 日报当前投影与消息排序 |
| `notification_outbox` | 精选消息与系统失败告警的幂等记录、租约和重试状态 |

最新迁移为 `079_finalize_v3_execution_identity.sql`。SQL migration 是数据库结构的
唯一结构来源：新数据库和历史数据库都执行同一条有序 migration 链，不再使用 ORM `create_all()`
初始化正式结构。不得绕过追加迁移直接修改。ORM 模型必须与 079 的最终约束一致，但 079 及此前
迁移都属于不可修改的历史；后续结构变化只能追加新编号迁移。

## 验证

```bash
services/api/.venv/bin/python -m ruff check services/api/app services/api/scripts services/api/tests
services/api/.venv/bin/python -m pytest services/api/tests -q
pnpm lint:web
pnpm build:web
```
