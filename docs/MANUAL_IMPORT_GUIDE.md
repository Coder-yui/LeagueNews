# 手工导入 RawItem

手工导入是一个 Connector 入口，只负责生成与其他平台一致的不可变 RawItem。它不会调用
AI，不会创建 `NormalizedItem`，也不存在导入时创建事件的逻辑。

```text
手工请求
  -> manual Connector 映射
  -> shared ingestion
  -> raw_items + media_assets
  -> 等待人工触发受审核处理流程
```

## 1. 准备 Source 和图片

在 Swagger 查询或创建信源：

```text
GET  /api/v1/sources
POST /api/v1/sources
```

图片二进制目前不由手工导入接口上传。先将图片放入
`E:\leagueNews\apps\web\public\media\`，请求中使用 `/media/...` 浏览器路径。不要引用
系统临时目录。

## 2. 提交

接口：

```text
POST /api/v1/imports/manual
```

推荐使用有序 `content_blocks`：

```json
{
  "source_id": 3,
  "external_id": "source-platform-article-id",
  "title": "文章标题",
  "author": "作者或账号名",
  "language": "zh-CN",
  "url": "https://example.com/article",
  "published_at": "2026-07-12T10:00:00+08:00",
  "content_blocks": [
    {"type": "paragraph", "text": "第一段正文。"},
    {
      "type": "image",
      "storage_path": "/media/example.png",
      "alt_text": "图片说明",
      "mime_type": "image/png"
    },
    {"type": "paragraph", "text": "图片后的正文。"}
  ],
  "raw_payload": {
    "import_method": "manual",
    "note": "人工从原始页面整理"
  }
}
```

文字和图片必须按原文顺序排列。旧的 `title/url/content` 纯文本请求仍兼容，但不能表达
图片插入位置。

成功响应为 `201`。此时只应看到 RawItem 和对应媒体：

```text
GET /api/v1/raw-items
GET /api/v1/media-assets?raw_item_id=<ID>
```

## 3. 进入受审核处理

在管理台 http://localhost:3000/admin 操作，或调用：

```text
POST /api/v1/raw-items/{id}/process
```

实际顺序是：

```text
相关性 AI -> 人工审核
  -> 可选版本图片 OCR / 人工修正
  -> 翻译 / 术语审核
  -> 基于已批准中文内容的分析与摘要 / 人工审核
  -> NormalizedItem
```

每个审核阶段的 API、状态和知识沉淀规则见
[`REVIEWED_AI_WORKFLOW.md`](REVIEWED_AI_WORKFLOW.md)。

处理失败或审核驳回不会修改 RawItem。重试创建新的 `ProcessingRun` 并关联旧运行；
知识规则、术语和审核历史保留。

## 4. 验收

处理前：

- `raw_items.content_blocks` 能按顺序复现原文文字与图片。
- 原文链接、作者、语言和 `published_at` 正确。
- 不存在 `NormalizedItem`。

全部审核批准后：

- `GET /api/v1/normalized-items/published` 出现该消息。
- 首页按原始 `published_at` 从新到旧展示。
- 详情页可以切换中文和原文。
- 设计师版本消息的 OCR 中英对照表只在中文页显示，原文页只展示原图。

当前没有事件验收步骤。事件聚合属于下一阶段独立管线。
