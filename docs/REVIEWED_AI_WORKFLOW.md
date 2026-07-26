# 人工审核单条处理流程

更新时间：2026-07-26

本文档是当前处理层的有效设计。采集层以不可变 `RawItem` 为边界；事件聚合和报告
尚未进入当前流程，也没有对外处理入口。

## 状态机

```text
RawItem
  -> AI relevance
  -> relevance review
       -> approve irrelevant: completed / irrelevant
       -> reject AI decision: rejected + relevance knowledge
       -> approve relevant:
            -> RiotPhroxzon patch preview with images
                 -> OCR + patch table extraction
                 -> human table correction / OCR review
                 -> deterministic patch structuring
            -> AI item analysis
            -> translate non-Chinese text and structured patch data
            -> final item review
                 -> approve: NormalizedItem
                 -> reject: rejected + analysis rule or glossary term
```

“审核拒绝”不会删除审核、OCR 和知识记录；它只保证本次运行不产生正式
`NormalizedItem`。再次处理会创建新的 `ProcessingRun`，并用
`supersedes_run_id` 指向旧运行。

AI 判断内容不相关且人工确认不相关属于正常结果，运行状态为 `completed`，
`outcome=irrelevant`，不产生纠错知识。

## 阶段

### 相关性

AI 输出产品范围、是否相关、置信度和理由。人工拒绝 AI 判断时必须使用
`relevance_correction`，系统会写入可编辑的相关性规则。

### 版本图片 OCR

仅带图片的 `RiotPhroxzon` preview、micropatch 或 hotfix 内容进入该分支。

现有顺序固定为：

```text
OCR -> 表格化提取 -> 人工修正表格 -> 最终确定性结构化
```

人工修正会创建新的 `MediaExtraction`，不会覆盖原始提取。OCR 错误不进入通用
AI 知识库；正确结果通过具体修订保存。

### 分析和翻译

AI 生成：

- 中文标准标题和摘要；
- 分类和实体；
- `importance_score`；
- `credibility`、`credibility_score` 和 `credibility_evidence`。

非中文内容会翻译标题、正文内容块、摘要、实体展示名称，以及已经结构化的版本
改动数据。结构化译文保持字段、数字和运算符不变。

分析与翻译组成同一个最终审核任务。人工批准后才写入正式结果；分析驳回产生
`analysis` 知识规则，翻译驳回必须至少提交一个术语修正。

## 数据职责

- `raw_items`：不可变原始内容。
- `media_extractions`：OCR、表格数据、人工修订和最终版本结构。
- `processing_runs`：一次处理运行及其最终 outcome。
- `review_tasks`：阶段草稿和审核反馈。
- `knowledge_rules`：相关性和分析纠错知识。
- `glossary_terms`：翻译术语知识。
- `normalized_items`：最终批准的单条内容结果。
- `normalized_item_media_extractions`：正式结果采用的图片提取、结构化中文译文和
  翻译模型。

核心外键关系不放在 JSON ID 数组中。数据库限制每个 RawItem 同时只能有一个活动
item run，每个 run 同时只能有一个 pending review。

## API

```text
POST /api/v1/raw-items/{id}/process
GET  /api/v1/workflows/runs
GET  /api/v1/workflows/reviews?status=pending
POST /api/v1/workflows/reviews/{id}/approve
POST /api/v1/workflows/reviews/{id}/reject
POST /api/v1/workflows/reviews/{id}/correct-ocr
POST /api/v1/workflows/runs/{id}/retry
```

生产 API 不提供单张图片直接结构化、已批准条目直接重翻译、事件处理或报告生成
入口。OCR Lab 仅用于算法参数调试，生产流程只读取当前激活的 OCR profile。

## 有效状态

Processing run：

```text
running
awaiting_review
completed
rejected
failed
```

Outcome：

```text
approved
irrelevant
review_rejected
system_error
```

Review task：

```text
pending
approved
rejected
superseded
```
