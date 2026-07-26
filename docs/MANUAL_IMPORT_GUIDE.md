# 手动添加资讯指南

本文档说明如何通过 FastAPI Swagger 手动添加一条包含文字、图片和来源信息的资讯。

项目整体启动与退出参见 [`LOCAL_RUNBOOK.md`](LOCAL_RUNBOOK.md)。

## 数据会经过哪些步骤

```text
手动提交
→ source
→ raw_items + media_assets（pending）
→ 手动点击 Process
→ DeepSeek 分析与翻译
→ normalized_items
→ event_items
→ news_events
```

手动导入不会立即调用 AI，也不会直接向 `news_events` 写数据。只有显式触发 Process 后才进入正式 workflow。

## 1. 启动服务

确保以下页面可以打开：

- Swagger：http://localhost:8000/docs
- 前端：http://localhost:3000
- pgAdmin：http://localhost:5050

## 2. 确认或创建信源

先在 Swagger 执行：

```text
GET /api/v1/sources
```

如果目标信源不存在，执行：

```text
POST /api/v1/sources
```

示例：

```json
{
  "name": "腾讯英雄联盟官方网站",
  "connector_type": "manual",
  "base_url": "https://lol.qq.com",
  "is_active": true
}
```

记下响应里的 `id`，导入资讯时作为 `source_id`。

同一个网站或账号只创建一个 source。重复创建相同名称会返回 `409`。

## 3. 准备图片

当前手动 API 接收图片路径，但还不负责上传二进制文件。先把图片复制到：

```text
E:\leagueNews\apps\web\public\media\
```

使用稳定且不重复的英文文件名，例如：

```text
patch-26-14-preview.jpg
haidou-tournament-cover.png
```

在请求中使用浏览器可访问路径：

```text
/media/patch-26-14-preview.jpg
```

不要引用微信或系统 Temp 目录；临时文件可能被自动删除。

## 4. 按原文顺序组织 content_blocks

文章中的文字和图片必须按实际出现顺序排列。

例如：

```text
第一段文字
图片一
第二段文字
图片二
第三段文字
```

请求中写成：

```json
[
  {"type": "paragraph", "text": "第一段文字"},
  {
    "type": "image",
    "storage_path": "/media/image-1.png",
    "alt_text": "图片一说明",
    "caption": "图片一标题",
    "mime_type": "image/png"
  },
  {"type": "paragraph", "text": "第二段文字"},
  {
    "type": "image",
    "storage_path": "/media/image-2.png",
    "alt_text": "图片二说明",
    "caption": "图片二标题",
    "mime_type": "image/png"
  },
  {"type": "paragraph", "text": "第三段文字"}
]
```

不要把图片路径再次写进段落正文。图片位置由 image block 和 `media_assets.block_index` 表达。

## 5. 调用手动导入 API

在 Swagger 展开：

```text
POST /api/v1/imports/manual
```

点击 **Try it out**，填写：

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

使用 `content_blocks` 时可以不填旧字段 `content`。系统会按文字 block 顺序生成 `plain_text`，图片不会混入纯文本。

旧式纯文本导入仍兼容：

```json
{
  "source_id": 1,
  "title": "纯文本消息",
  "content": "消息正文"
}
```

但旧式格式不能表达图片插入位置。

## 6. 判断 raw 入库是否成功

成功返回 `201`，响应是刚保存的 raw item，例如：

```json
{
  "id": 10,
  "source_id": 3,
  "title": "原始标题",
  "status": "pending",
  "plain_text": "原始正文"
}
```

此时只有 `raw_items/media_assets`，还没有 normalized item 和 event，也没有产生 LLM 费用。

## 7. 手动触发 AI 处理

在 Swagger 展开：

```text
POST /api/v1/raw-items/{item_id}/process
```

点击 **Try it out**，把刚才 raw 响应中的 `id` 填入 `item_id`，然后点击 **Execute**。

成功返回 `200` 和生成的 event，同时 raw 状态变成 `processed`。

处理包含：

```text
分析
→ 非中文全文翻译
→ normalized_items
→ news_events/event_items
→ raw.status = processed
```

如果模型配置、代理网络、JSON 校验或翻译失败：

- 接口返回明确的 `502` 或 `503`。
- 当前事务回滚。
- 不创建 normalized/event。
- raw 继续保持 `pending`，网络恢复后可再次点击 Process。

检查完整链路：

1. `GET /api/v1/raw-items`：原始文字、图文 blocks、hash、处理状态。
2. `GET /api/v1/media-assets?raw_item_id=<ID>`：图片路径和插入位置。
3. `GET /api/v1/normalized-items`：AI 单条分析结果。
4. `GET /api/v1/events`：前端事件数据。

## 8. 检查完整链路

### Patch Preview 表格图片

对于设计师发布的 Patch Preview，按正常顺序把正文段落和表格图片写进
`content_blocks`。图片使用本地 `storage_path`（例如
`/media/patch-26-14-preview.jpg`），并把 `mime_type` 设为 `image/jpeg` 或其他
`image/*` 类型。

当标题包含 `Patch` 且资讯带有图片时，处理接口会额外执行：

1. RapidOCR 在本机读取图片中的英文和数值；
2. DeepSeek 根据 OCR 坐标恢复分区、目标和“调整前 → 调整后”；
3. 结果写入 `media_extractions`，不会覆盖 `raw_items` 原文；
4. 前端在对应原图下方显示结构化改动表，并标注 OCR 置信度。

如果 OCR 或 DeepSeek 请求失败，处理事务会回滚，raw 继续保持 `pending`，不使用
猜测数据兜底。网络恢复后再次调用同一个处理接口即可。

已经处理过的历史图片可以单独回填，不需要重新分析整条新闻：

```powershell
cd E:\leagueNews\services\api
uv run python scripts/extract_patch_media.py <media_asset_id>
```

可在 pgAdmin 的 `media_extractions` 表查看原始 OCR 行、结构化 JSON、模型与置信度。

1. `GET /api/v1/raw-items`：导入后立即可见，初始状态 pending。
2. `GET /api/v1/media-assets?raw_item_id=<ID>`：图片路径和插入位置。
3. `GET /api/v1/normalized-items`：Process 成功后出现。
4. `GET /api/v1/events`：Process 成功后出现。

## 9. 常见错误

### 422 Validation Error

请求结构不合法。常见原因：

- `content` 不是字符串。
- text block 没有 `text`。
- image block 没有 `source_url` 或 `storage_path`。
- URL 格式不合法。

### 502 AI 分析或翻译失败

原始数据已经保存在 `raw_items`，状态继续为 `pending`，但不会生成半成品或伪造的 event。

### 503 未配置 LLM

检查根目录 `.env` 中的 `OPENAI_API_KEY`、`OPENAI_BASE_URL` 和 `MODEL_NAME`，然后重启 FastAPI。

### 重复数据

当前尚未启用自动去重。不要连续多次点击 Swagger 的 **Execute**。每次成功请求都会新增一条 raw item。

## 英文信源说明

英文资讯在手动触发 Process 后由 DeepSeek 一次性翻译完整文字 blocks：

- 原文保留在 `raw_items.content_blocks`。
- 中文译文保存在 `normalized_items.translated_content_blocks`。
- 图片 block 不翻译并保持原始位置。
- 前端默认显示中文，可切换“中文 / 原文”。
- 中文信源标记为 `not_required`，不会产生额外翻译调用。

历史英文数据或失败后保持 `pending` 的翻译可以执行：

```text
POST /api/v1/normalized-items/{item_id}/translate
```
