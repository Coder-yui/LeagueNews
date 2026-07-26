# RawItem 与 ContentBlock v2

## 1. 边界

`raw_items` 是采集层输出的、统一的、不可变的结构化原始内容。后续翻译、分析、审核和事件聚合都只能读取它，不能回写或修正文。

以下数据不属于 `raw_items`：

- `plain_text`：它是针对分析或翻译用途，从 `content_blocks` 临时生成的文本视图。
- 处理状态：保存在 `processing_runs`、`review_tasks` 和 `normalized_items`。
- 经脱敏和裁剪的平台 provenance：保存在 `raw_item_source_payloads`，只用于审计和排障。
- `first_seen_at`、`last_seen_at`、`first_seen_run_id`、`last_seen_run_id`、`source_updated_at`：当前产品不需要，不保存。

## 2. raw_items 字段

| 字段 | 含义 |
| --- | --- |
| `id` | 内部主键 |
| `source_id` | 具体信源账号或站点 |
| `external_id` | 平台内容 ID |
| `native_title` | 平台真实标题；X、微博等无标题平台必须为空 |
| `canonical_url` | 浏览器可打开的原文地址 |
| `content_kind` | `article`、`post`、`thread` 或 `manual` |
| `author_name` | 这条内容采集时的平台显示名称 |
| `language` | 平台提供或 Connector 确定的原文语言 |
| `content_blocks` | 完整、有序的 ContentBlock v2 |
| `content_hash` | 排除本地存储字段后的 source-semantic hash |
| `content_hash_version` | 哈希算法版本 |
| `revision` | 同一平台内容 ID 的不可变版本号 |
| `supersedes_raw_item_id` | 内容发生编辑时指向上一版 RawItem |
| `published_at` | 平台发布时间 |
| `ingested_at` | 本系统完成入库的时间 |

API 的 `display_title` 是展示字段，不是数据库列：有 `native_title` 时直接使用；否则使用“作者名：正文第一行”。这个计算不会从正文中删除第一行。

账号 handle/UID、账号主页和信源名称属于 `sources.external_key`、`sources.base_url`
和 `sources.name`，不在每条 RawItem 中重复保存。ContentBlock 的格式版本属于代码和
迁移契约，也不逐条写入 RawItem。

## 3. ContentBlock v2

所有块都有稳定的顺序和 `id`，如 `b0001`、`b0002`。支持以下类型：

### paragraph

```json
{"id":"b0001","type":"paragraph","text":"完整正文"}
```

### heading

```json
{"id":"b0002","type":"heading","level":2,"text":"第 3 楼 · 2026-07-25 10:00:00"}
```

### list

```json
{
  "id":"b0003",
  "type":"list",
  "ordered":false,
  "items":["第一项","第二项"]
}
```

### quote

引用正文已能获取时保存正文，可附作者和原文 URL：

```json
{
  "id":"b0004",
  "type":"quote",
  "text":"被引用内容",
  "author":"原作者",
  "source_url":"https://example.com/original"
}
```

### image

```json
{
  "id":"b0005",
  "type":"image",
  "source_url":"https://cdn.example.com/a.jpg",
  "storage_path":"/media/provider/a.jpg",
  "mime_type":"image/jpeg",
  "alt_text":"图片说明",
  "caption":"图注"
}
```

### embed

视频、投票、引用帖入口、音频、iframe 和普通外链统一保存为 `embed`。`source_url` 必须是浏览器可打开的 HTTP(S) 地址。

```json
{
  "id":"b0006",
  "type":"embed",
  "embed_kind":"video",
  "text":"比赛视频",
  "source_url":"https://example.com/post/123"
}
```

`embed_kind` 可取：

- `video`
- `poll`
- `quoted_post`
- `external_link`
- `iframe`
- `audio`
- `other`

前端不尝试复刻平台播放器或投票组件，统一展示“媒体内容，请前往原文查看”和可点击链接。

## 4. 各 Connector 的映射

- Riot、腾讯：`content_kind=article`，平台文章标题写入 `native_title`。
- X、微博：`content_kind=post`，`native_title=null`；正文、图片、引用和媒体入口按平台顺序写入 blocks。
- 百度贴吧：`content_kind=thread`，使用主题真实标题；把目标账号在主题内的所有楼层按楼层顺序组成 `heading + 内容块`。
- 手动导入：`content_kind=manual`；旧 `content` 输入会先转为单个 `paragraph`。

## 5. 下游如何读取

- 前端原文展示：直接遍历 `content_blocks`，这是完整内容的唯一事实来源。
- 翻译：只翻译 `heading`、`paragraph`、`list.items` 和 `quote.text`，保持块 ID、顺序、图片和 embed 链接不变。
- 分析：调用 `text_from_content_blocks()` 生成当前文本视图。该规则可以以后独立调整，不需要迁移或改写 raw 数据。
- 转发判断：检查是否存在 `embed_kind=quoted_post`，不读取平台私有 payload。
- 排障：需要核对平台响应时，单独查询 `raw_item_source_payloads`。

## 6. 不可变约束

已入库原文不应被处理层覆盖。平台以相同 ID 返回不同内容时，系统创建新的 revision，
不会修改旧行。修复历史采集错误应使用明确的数据修复迁移，并保留变更原因。
