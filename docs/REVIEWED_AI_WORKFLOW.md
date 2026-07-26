# 人工审核 AI 工作流

更新时间：2026-07-25

本文档描述迁移 `011_add_reviewed_ai_workflows` 和
`012_track_approved_media_extractions`、`013_add_ocr_lab` 和
`014_add_patch_table_structure` 引入的新处理方式。旧的
“Raw 一次处理后直接创建 Normalized Item 和 Event”流程已停止使用。

管理页面：

```text
http://localhost:3000/admin
```

API 文档：

```text
http://localhost:8000/docs
```

## 1. 数据原则

- `raw_items` 保存原始平台内容，不因 AI 失败或人工拒绝而修改正文。
- AI 输出先进入 `review_tasks.proposal`，人工批准后才写入正式数据。
- Patch OCR 可以保留多个尝试版本，只有审核批准采用的提取 ID 会进入正式展示。
- 人工拒绝属于业务回滚：正式数据保持不变，错误草稿和反馈保留用于审计。
- 等待人工审核期间不保持数据库事务或连接。
- 只有明确选择“相关性/分析/事件修正”时才成长规则；翻译术语修正只成长术语表。
- OCR 识别错误和“其他问题”只记录审核反馈，不写入规则或术语。

## 2. 单条信息处理

启动：

```http
POST /api/v1/raw-items/{raw_item_id}/process
```

阶段：

1. AI 判断 LoL 相关性。
2. 人工审核相关性。
3. 确认相关后执行英文翻译、RiotPhroxzon patch 图片 OCR、实体/分类/摘要/评分。
4. 人工统一审核翻译和单条分析。
5. 批准后创建正式 `normalized_items`。

保留范围：

- 英雄联盟端游
- LoL 电竞
- 云顶之弈
- LoL 世界观、影视
- Riot 公司新闻
- LoL 周边、音乐和商业合作

排除范围：

- 英雄联盟手游 / Wild Rift
- 2XKO
- 无关私人内容

## 3. 审核

查询待审核任务：

```http
GET /api/v1/workflows/reviews?status=pending
```

批准：

```http
POST /api/v1/workflows/reviews/{review_id}/approve
Content-Type: application/json

{"note": null}
```

拒绝：

```http
POST /api/v1/workflows/reviews/{review_id}/reject
Content-Type: application/json

{
  "feedback_type": "translation_term",
  "reason": "Ability Haste 术语翻译错误",
  "knowledge_rule": "Ability Haste 应使用英雄联盟国服正式译名",
  "knowledge_scope": "global",
  "corrected_values": {},
  "glossary_updates": [
    {
      "source_term": "Ability Haste",
      "preferred_translation": "技能急速",
      "forbidden_translations": ["能力急速"],
      "scope": "lol"
    }
  ]
}
```

`feedback_type` 决定反馈的去向：

- `relevance_correction`：成长相关性规则。
- `analysis_correction`：成长单条分析规则。
- `event_correction`：成长事件聚合规则。
- `translation_term`：只更新术语表。
- `ocr_error`：只保存反馈，不成长任何知识。
- `other`：只保存反馈。

如果错误来自 OCR，不必为了单张例外图片继续修改全局参数。单条分析审核页会显示该图片
的表格编辑器，可直接修正 section、左栏对象和右栏改动，再提交：

```http
POST /api/v1/workflows/reviews/{review_id}/correct-ocr
Content-Type: application/json

{
  "extraction_id": 123,
  "table_data": {
    "preview_kind": "full_preview",
    "divider_x": 148,
    "structure_confidence": 1,
    "sections": [
      {
        "section_type": "champion_buff",
        "label": "CHAMPION BUFFS",
        "records": [
          {
            "target": "Aatrox",
            "raw_changes": ["Health: 12S -> 125"],
            "bbox": [10, 20, 600, 100],
            "ocr_confidence": 1
          }
        ]
      }
    ],
    "warnings": [],
    "boundaries": []
  },
  "note": "将最终生命值从 12S 修正为 125"
}
```

系统不会覆盖原始 OCR 提取，而会创建 `v2-manual` 修订版，以人工文本重新调用 AI
整理，并重新生成翻译、摘要和分析待审稿。旧审核标记为 `superseded`，正式数据仍不
改变。修订说明只进入审计记录，不成长知识规则或术语表。

拒绝后运行状态为 `revision_requested`。需要成长的规则或术语立即生效，然后重试：

```http
POST /api/v1/workflows/runs/{run_id}/retry
```

## 4. 事件处理

单条信息审核通过后，另行手动触发：

```http
POST /api/v1/normalized-items/{normalized_item_id}/process-event
```

处理方式：

1. AI 判断是否为事件。
2. 非事件内容保留为 Normalized Item，不创建 Event。
3. 对事件按状态、类型化时间窗口、实体、分类和标题相似度检索最多 5 个候选。
4. AI 只能在候选中选择已有事件，否则创建新事件。
5. 生成事件变更草稿并等待人工审核。
6. 批准后创建或更新 Event，并写入 `event_revisions`。

候选查询由应用程序生成固定参数化查询，AI 不生成或执行 SQL。

事件来源关系包括早期报告、传闻、社区讨论、官方预览、官方确认、修正、后续和冲突。
直接官方来源优先成为 `primary_item_id`；非官方资料仍作为证据保留。

## 5. 知识和术语维护

相关接口：

```text
GET   /api/v1/knowledge/rules
POST  /api/v1/knowledge/rules
PATCH /api/v1/knowledge/rules/{id}

GET   /api/v1/knowledge/glossary
POST  /api/v1/knowledge/glossary
PATCH /api/v1/knowledge/glossary/{id}
```

每次修改会增加版本号。`is_active=false` 可停用错误知识，不需要删除审计记录。

知识范围支持：

- `global`
- `connector:{connector_type}`
- `source:{source_id}`

## 6. 报告

生成报告草稿：

```http
POST /api/v1/reports/generate
Content-Type: application/json

{
  "report_type": "daily",
  "period_start": "2026-07-25T00:00:00+08:00",
  "period_end": "2026-07-25T23:59:59+08:00",
  "timezone": "Asia/Shanghai"
}
```

报告会区分本期新事件和本期有后续更新的事件，并记录所引用的 Event 和 Event Revision。

人工批准或拒绝：

```text
POST /api/v1/reports/{id}/approve
POST /api/v1/reports/{id}/reject
```

## 7. 主要状态

Raw 粗粒度状态：

```text
pending
processing
relevance_review
irrelevant
item_review
analyzed
processed
```

Processing Run：

```text
running
awaiting_review
revision_requested
failed
completed
```

Normalized Item 事件状态：

```text
pending
processing
event_review
not_event
linked
```

## 8. LLM 输出校验、重试与错误响应

全部结构化 LLM 调用统一执行：

1. JSON 解码。
2. Pydantic 字段、类型、枚举和取值范围校验。
3. 业务约束校验，例如相关性范围与布尔判断必须一致、事件更新 ID 必须来自候选列表、
   翻译块索引必须完整。
4. 第一次失败时将精简校验错误和原输出反馈给模型，要求重新输出完整 JSON。
5. 第二次仍失败则终止本次运行；Raw 和既有正式数据不变，运行可人工重试。

API 错误统一包含兼容字段 `detail` 和结构化字段：

```json
{
  "detail": "相关性判断失败：模型连续两次未通过结构或业务校验……",
  "error": {
    "code": "upstream_processing_error",
    "message": "相关性判断失败：模型连续两次未通过结构或业务校验……",
    "retryable": true
  }
}
```

请求参数错误的 `error.code` 为 `validation_error`，并在 `error.details` 返回字段错误。

## 9. 表格结构与 OCR 测试台

入口：

```text
http://localhost:3000/admin
```

选择“OCR 测试台”。测试台只列出已下载到本地的 `@RiotPhroxzon` 图片，运行本地
表格分割和 RapidOCR，不调用 LLM，也不会修改正式提取结果。

固定版式解析顺序：

1. 从 OCR 文本框的横坐标聚类检测左右列分隔位置。
2. 从左列完整横线检测普通单元格和纵向合并单元格的上下边界。
3. 左侧单元格生成 `target`，同一纵向区间的右侧文本生成 `raw_changes`。
4. 彩色分类行生成 `section_type`。
5. Preview 没有详情列时只输出目标名单，禁止 LLM 猜测改动。
6. Full Preview 结构置信度低于 65% 时停止正式处理，要求先人工校正。

可调参数：

- 图片缩放、灰度化、对比度、锐度。
- `text_score`、`box_thresh`、`unclip_ratio`。
- 文字方向分类 `use_cls`。
- 分隔线位置手动覆盖 `divider_x_ratio`。
- 表格线亮度 `line_brightness` 和横线覆盖率 `line_coverage`。

每次运行会保留参数、逐行文本、坐标、OCR 置信度、结构置信度、原始识别框叠加图、
表格单元格叠加图以及 `target → raw_changes` 配对。确认某组参数后点击“设为生产参数”，
后续正式 Patch OCR 会读取当前激活参数。

当前激活的生产参数为：缩放 `2`、不开启灰度、对比度 `1`、锐度 `1`、检测阈值使用
引擎默认值、检测框扩张 `1.2`、开启文字方向分类、分隔线自动检测、表格线亮度 `105`、
横线覆盖率 `0.82`。

正式管线先生成键值配对，再把配对后的结构交给 LLM 整理和翻译。LLM 不再负责根据
散乱坐标猜测哪个改动属于哪个英雄。API：

```text
GET  /api/v1/ocr-lab/assets
GET  /api/v1/ocr-lab/runs
POST /api/v1/ocr-lab/runs
GET  /api/v1/ocr-lab/profiles
POST /api/v1/ocr-lab/runs/{run_id}/activate
```
