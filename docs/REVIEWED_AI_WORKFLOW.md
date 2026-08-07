# 人工审核单条处理流程

更新时间：2026-08-07

本文档描述人工审核模式的单条处理设计。自动模式复用相同阶段草稿、校验和接受逻辑，
由 Worker 写入 `decision_source=automatic` 和 checkpoint，不等待人工点击。采集层以
不可变 `RawItem` 为边界；事件聚合消费已发布 `NormalizedItem`，不会反向修改 RawItem。
事件准入、匹配、可信度和重要性规则见
[`EVENT_EDITORIAL_POLICY.md`](EVENT_EDITORIAL_POLICY.md)。

## 状态机

```text
RawItem
  -> AI relevance
       -> clear evidence: direct checkpoint and route
       -> insufficient evidence: automatic checkpoint + completed / insufficient_evidence
       -> relevant:
            -> RiotPhroxzon patch preview with images
                 -> OCR + patch table extraction
                 -> human table correction / OCR review
                 -> deterministic patch structuring
            -> translate title, content blocks and structured patch data
            -> translation + terminology review
                 -> reject: rejected + translation rule or glossary term
            -> extract facts and classify approved Chinese content
            -> fact_classify review
            -> deterministic importance scoring + importance review
            -> atomic Claim generation + claim_gen review
                 -> approve: NormalizedItem + revision + Claims
                 -> reject: rejected + analysis rule
            -> event route decision and aggregation review
```

“审核拒绝”不会删除审核、OCR 和知识记录；它只保证本次运行不产生正式
`NormalizedItem`。再次处理会创建新的 `ProcessingRun`，并用
`supersedes_run_id` 指向旧运行。

AI 判断内容不相关且人工确认不相关属于正常结果，运行状态为 `completed`，
`outcome=irrelevant`，不产生纠错知识。

原文过短、只有外链、通用图片或视频且没有可用正文时，证据门禁会直接写入自动
checkpoint，并以 `completed` / `outcome=insufficient_evidence` 结束。它不同于
`irrelevant`：前者表示现有原始证据不足以判断，后者表示已判断不属于产品范围。两种结果
都不产生 `NormalizedItem` 或事件；后续发现漏处理时，可先撤回再走人工纠正流程。当前不提取
引用正文，也不做视频转写或通用图片理解。

## 阶段

### 相关性

AI 输出产品范围、是否相关、置信度和理由，并在证据充分时直接形成 checkpoint。证据门禁
不足时不会创建人工审核，而是自动以 `insufficient_evidence` 终止；人工审核只处理已经产生
具体阶段草稿、但需要业务纠正的内容。

### 版本图片 OCR

仅带图片的 `RiotPhroxzon` preview、micropatch 或 hotfix 内容进入该分支。

现有顺序固定为：

```text
OCR -> 表格化提取 -> 人工修正表格 -> 最终确定性结构化
```

人工修正会创建新的 `MediaExtraction`，不会覆盖原始提取。OCR 错误不进入通用
AI 知识库；正确结果通过具体修订保存。

### 翻译

翻译阶段先于分析阶段。非中文内容会翻译标题、正文内容块以及已经结构化的版本
改动数据，并形成翻译审核草稿；中文原文生成 `not_required` 结果后自动进入事实分类。
结构化译文保持字段、
数字、运算符和 section/entry 对应关系不变，`changes` 文本允许按中文表达拆行或合行。

翻译审核只负责原文到中文内容的正确性。驳回时可以提交翻译规则、术语修正或两者，
批准后的中文内容会固定到 ProcessingRun 上下文，作为下一阶段的唯一内容输入。

### 事实分类、重要性与 Claim

三个阶段只读取已经批准的上游证据，并各自形成可审核草稿：

- `fact_classify`：中文标准标题、摘要、实体、多轴分类和 `event_mentions`；
- `importance`：模型只提取受控修正特征，程序确定性计算 `importance_score` 和
  `priority_score`；
- `claim_gen`：生成带原始块证据、归因和时间角色的原子 Claim。

消息层不保存综合可信度；事件可信度由 Source 配置和事件成员关系确定性计算。分析阶段加载
`analysis` 类型知识规则，但不再加载术语表，也不承担翻译。只有 `claim_gen` 批准后才写入
正式 `NormalizedItem`、revision 和 Claims，并立即启动相同执行模式的事件聚合。

## 数据职责

- `raw_items`：不可变原始内容。
- `media_extractions`：OCR、表格数据、人工修订和最终版本结构。
- `processing_runs`：一次处理运行及其最终 outcome。
- `review_tasks`：阶段草稿和审核反馈。
- `processing_checkpoints`：每个已接受阶段的不可变输出和决定来源。
- `knowledge_rules`：分析、翻译和事件聚合纠错知识。
- `glossary_terms`：翻译术语知识。
- `normalized_items` / `normalized_item_revisions`：最终批准的当前投影和发布历史。
- `claims`：与原始证据关联、可被后续消息更新或取代的原子事实主张。
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

生产 API 不提供单张图片直接结构化或已批准条目直接重翻译入口。消息批准后会按当前
manual/automatic 模式自动启动事件聚合；`/api/v1/event-workflows` 负责查询、审核、重试和
人工修正事件决策。OCR Lab 仅用于算法参数调试，生产流程只读取当前激活的 OCR profile。

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
insufficient_evidence
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
