# LeagueNews 当前架构

更新时间：2026-08-11

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
  -> stop
```

当前运行时包含 Connector、共享 ingestion、RawItem 修订、媒体落盘、自动任务、人工审核、
OCR、翻译、受控消息分类、实体/摘要提取、重要性算法、公开消息页和管理台。消息处理发布
NormalizedItem 后结束；更高层聚合不属于当前运行时。

公开消息投影同时返回已批准的 `topics` 和 Source 的 `reliability_score`；消息流与详情页直接
展示这两个字段。可信度只用于说明信源属性，不参与消息重要性加分。

## 消息分类

权威规则：[`MESSAGE_CLASSIFICATION.md`](MESSAGE_CLASSIFICATION.md)。运行时字段：

- `products`：多选，尽量单选，最多 3 个；
- `content_form`：单选；
- `message_type`：单选，受产品与官方信源状态约束；
- `topics`：多选，受产品约束；
- `classification_version`：当前为 `message-taxonomy-v2`。

纯媒体或纯链接仍由 LLM 判断内容形式，但其他语义轴强制为 `unknown`，摘要和实体为空，重要性
为 0。相关性在此之前完成，无关消息不会发布。

## 架构边界

- Connector 是平台能力；Source 是具体账号或站点。
- Connector 只映射 `RawItemCandidate`，共享 ingestion 负责校验、去重、媒体、provenance、
  RawItem 持久化和任务入队。
- `raw_items.content_blocks` 是不可变原始证据，处理和审核不得回写。
- `normalized_items` 是当前消息发布投影，历史保存在 `normalized_item_revisions`。
- 自动与人工路径共享提案、Schema/业务校验和 checkpoint。
- SQL 迁移是追加式历史，不能修改已有编号文件。

## 页面与 API

```text
/                         已发布消息
/messages/{id}            消息详情
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

最新迁移为 `061_update_importance_policy_v11.sql`。ORM、API 和工作流只访问上表列出的
当前运行时表；升级数据库可能保留迁移兼容对象，但它们不属于运行时模型，也不得绕过追加迁移
直接修改。

## 验证

```bash
services/api/.venv/bin/python -m ruff check services/api/app services/api/scripts services/api/tests
services/api/.venv/bin/python -m pytest services/api/tests -q
pnpm lint:web
pnpm build:web
```
