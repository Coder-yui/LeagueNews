# 单条消息处理流程

更新时间：2026-08-11

本文描述 RawItem 到 NormalizedItem 的唯一处理流程。分类规则以
[`MESSAGE_CLASSIFICATION.md`](MESSAGE_CLASSIFICATION.md) 为准。

## 流程

```text
RawItem
  -> relevance
  -> optional image_ocr
  -> translation
  -> message_analysis
  -> importance
  -> publish NormalizedItem
  -> stop
```

消息发布即为当前流程终点。

发布成功后由现有 `PipelineJob` durable queue 交给 Pipeline Worker 负责 Event
downstream；审核流程不等待 Event LLM。`PipelineCorrection` 在消息发布提交后完成，
Event downstream 的失败只属于 PipelineJob/EventAggregationRun，不会把已经完成的消息运行回写为失败。

需要完整处理的非中文消息通常依次进行四次 LLM 调用：相关性、整条翻译、产品与内容分析、
消息类型/主题与重要性特征。中文消息无需翻译，OCR 是指定类型消息的条件分支，纯媒体、纯链接
或提前结束的消息调用数更少。

自动与人工模式使用相同的草稿、结构校验、业务校验和 checkpoint。自动模式写入
`decision_source=automatic` 并自动批准无需人工介入的草稿；需要人工判断时停在
`awaiting_review`。审核拒绝保留运行、草稿和反馈，再次处理会创建新的 `ProcessingRun`。

## 阶段职责

### relevance

LLM 输出 `relevant | irrelevant | uncertain`、置信度和理由。`irrelevant` 正常结束且不发布；
`uncertain` 继续处理，避免把证据不完整误判成无关。

### image_ocr

仅对已识别为设计师版本预览等指定图片执行 OCR 和表格结构化。人工修订创建新的
`MediaExtraction`，不覆盖原始提取。RawItem 的 `content_blocks` 始终不可变。

### translation

统一生成中文标题、正文块和已结构化图片内容；需要翻译时整条消息只调用一次 LLM，不按正文
分块追加调用。中文原文记录为 `not_required`，因此不调用翻译模型。批准后的翻译结果固定在
运行上下文中，是后续分析的文本输入。

### message_analysis

一次 LLM 调用输出：

- 标准标题与摘要；
- `products`、`content_form`；
- 最多 8 个实体；
- `classification_version=message-taxonomy-v3`；分类信源三态依据随提案、checkpoint 和最终 facets 保存。

产品尽量单选、最多 3 个。该调用不接收消息类型或主题目录，也不能提前输出这两个字段。
`media_only` 或 `link_only` 固定输出 `products=[unknown]`，摘要和实体为空；流程在批准后直接
补全 `message_type=unknown`、`topics=[unknown]`，不调用 importance 模型。真正没有标题时 LLM
标题可以为空，发布前由程序补充“仅媒体消息”或“仅链接消息”。

### importance

根据已批准的 `products`、`content_form` 和三态分类信源生成 `message_type` 与 `topics` 候选子集。
一次 LLM 调用从子集中选择消息类型和主题，同时提取有限的重要性特征；程序直接通过
`message_type × topic family` 选择重要性档案并确定性算分。纯媒体和纯链接不进入本阶段，
`message_type=unknown`、`topics=[unknown]` 且分数写为 0。

`importance` 根据已批准的 `products` 和分类信源只向 LLM 暴露适用的 `message_type`、
`topics` 候选。评分不再生成 `primary_topic`、`subtopic` 或 `editorial_subtype` 等历史兼容字段；
计算审计只记录从当前分类直接解析出的 `importance_profile`。

批准重要性后原子写入 `NormalizedItem`、媒体关联和 `NormalizedItemRevision`，处理结束。

## 数据职责

- `raw_items`：不可变原始证据。
- `media_extractions`：OCR、结构化数据及人工修订。
- `processing_runs`：一次处理运行、上下文和最终 outcome。
- `review_tasks`：阶段草稿、决定和反馈。
- `processing_checkpoints`：已接受阶段的不可变快照。
- `knowledge_rules`：分析与翻译规则。
- `glossary_terms`：翻译术语。
- `normalized_items`：当前消息发布投影。
- `normalized_item_revisions`：消息发布历史。

## 有效阶段

```text
relevance
image_ocr
translation
message_analysis
importance
```

主要 API：

```text
POST /api/v1/raw-items/{id}/process
GET  /api/v1/workflows/runs
GET  /api/v1/workflows/review-queue
POST /api/v1/workflows/reviews/{id}/approve
POST /api/v1/workflows/reviews/{id}/reject
POST /api/v1/workflows/reviews/{id}/correct-ocr
POST /api/v1/workflows/runs/{id}/retry
POST /api/v1/pipeline/normalized-items/{id}/corrections
```
