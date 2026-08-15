# 事件展示投影、API 与前端

> 状态：Phase 4 已实现
>
> 策略版本：`event-presentation-v1`

## 目标与非目标

公开展示以事件的当前结论为主体，并提供关键事实、未决问题、实质时间线和全部相关消息的追溯。
卡片不能把任意成员消息的标题/摘要当作事件当前状态，也不能用一个
`representative_message_id` 同时承担起源、权威来源、最新进展和媒体选择。

本阶段不删除现有消息 API 或消息页面，不创建脱离 Next.js/FastAPI 技术栈的新前端，也不把所有
转载堆成事件时间线。

## 当前输入与目标输出

展示读取 `Event` 当前投影、`EventRevision`、active `EventMention`、`NormalizedItem` 公开投影、
Source 和已发布媒体。核心 DTO：

```text
EventCard
  id, title, current_summary, products, event_family, category
  lifecycle_status
  importance_score, importance_level
  credibility_score, credibility_level
  heat_score, heat_level
  source_count, message_count
  message_count_24h, unique_sources_24h, message_count_total
  last_material_update_at
  primary_source
  best_media

EventDetail
  EventCard fields
  latest_development, key_facts[]
  timeline[]
  evidence_groups{reports, supports, confirms, denies, corrects, mentions}
  related_messages[]
  references{}
```

所有公开消息内容继续通过 `published_item_payload()` 等价投影过滤媒体路径，不能泄漏 storage 内部
路径、Source 配置或 provenance 私有数据。

## 四类消息引用

| 字段 | 选择规则 |
| --- | --- |
| `origin_message_id` | 最早创建该事件的 material mention；修订链取当前可发布版本 |
| `primary_source_message_id` | 角色强度最高、直接性最高的支持/确认消息；同档取更早的一手来源 |
| `latest_update_message_id` | 最新 `material_update`；普通转载和 duplicate 不覆盖 |
| `best_media_message_id` | 有公开可用媒体的候选中按角色、相关性、清晰度和时间确定性评分 |

四者可以相同，也可以不同。选择函数必须确定性、可测试，并在引用目标撤回时重新计算。

## 摘要与当前状态

- `title/current_summary/key_facts/latest_development` 只在创建或
  `material_update` 时应用同一次模型响应中的 proposed changes。
- `corroboration_only` 可以改变可信度与证据列表；`duplicate/context_only` 可以改变热度和相关
  消息列表；三者都不能重写摘要。
- 任何变更都写入 `EventRevision` 快照，包含触发 mention、旧值和新值。
- `current_summary` 描述当前结论，不保留已被否认的断言；历史断言仍可在 timeline/evidence 审计。

## 时间线

时间线只收录：

- 创建事件的首个 `material_update`；
- 新事实、官方确认、否认、修正；
- 生命周期的实质转换；
- 确实改变 current projection 的后续进展。

纯转载、完全重复、普通讨论和无新增事实的媒体报道只进入 related messages/evidence，不产生时间线
节点。节点字段包括发生时间、relation、标题、说明、structured fact changes、Source、消息链接和
revision id。

## API

当前提供并保持消息 API 不变：

```text
GET /api/v1/events
  filters: category, products, event_family, lifecycle, credibility_level,
           importance_level, heat_level, search
  sort: latest | importance | heat
  pagination: limit/offset

GET /api/v1/events/{id}
```

当前详情响应内嵌 `timeline`、mention 级 `evidence` 和按消息去重的 `related_messages`，避免首版增加
三个必须同步分页的端点。数据量增长后可以在不改变字段语义的前提下拆出分页子资源。

列表按 `last_material_update_at DESC, id DESC`。热度排序前执行
受 TTL 控制的刷新。详情返回支持、确认、否认和修正的分组证据，并明确原始/引用/转发内容形式。

内部管理 DTO 额外返回 aggregation key、anchors、完整 breakdown、run id 和 revision 快照；公开
DTO 不暴露模型原始响应或内部匹配分数。

## 前端

事件列表的一级分类由 API 计算并过滤，前端提供：`全部 | 电竞 | LOL PC | 云顶 | 其他产品 | 生态`。
`category` 不是 Event 持久化字段，而是由现有 product taxonomy 与 event family 确定性映射：电竞 family
或 `lol_esports` 优先为 `esports`；其余按 `riot_ecosystem`/`lol_universe`、`lol_pc`、`tft`、
`other_lol_product` 映射为 `ecosystem`、`lol_pc`、`tft`、`other_products`。

沿用 `apps/web`：

```text
/events                 事件列表
/events/{id}            事件详情
/messages/{id}          保留现有消息详情
/admin/events           后续管理视图
```

事件卡片至少显示标题、当前摘要、三个独立指标、`source_count` 家信源报道和 `message_count` 条消息、
最后实质更新、主要来源和代表图片。两个展示数按已发布消息的 distinct `normalized_item_id` 与
distinct Source 计算；同一消息在一个事件中有多个 mention 只计一次。详情仍保留热度字段和相关消息列表，
但卡片不再使用“24h 0/0 来源”作为报道规模展示。

初版保留 `/` 的消息流并增加事件导航，避免在 Phase 4 同时改变现有公开契约。将事件流设为首页是
单独产品选择，不影响后端 DTO。

## 示例

官网综合版本公告确认 A/B 并创建 C 后：

- A 的卡片继续使用系统维护的平衡事件标题，可信度变为官方确认，最新更新指向官网公告。
- B 同理，但使用活动自己的摘要、重要性和关键事实。
- C 的 origin、primary source、latest update 可以都指向官网公告，重要性按皮肤事件自身影响计算。
- 官网公告作为一条消息可出现在三个事件详情中；消息详情也可反向列出三个 mention。
- 20 个转载增加 A 的 related messages 和热度统计，不产生 20 个时间线节点。

## 边界情况

- 最佳媒体消息撤回或媒体不可公开时，确定性回退到下一候选或无图卡片。
- 事件 denied 后仍公开当前否认结论和历史证据，不隐藏原事件。
- 同一消息在事件中有多个 mention 时，相关消息列表按消息去重，证据区保留 mention 级语义。
- 063 替换迁移不会把旧事件行带入新页面；公开列表只读取 v1 事件表。
- 统计 cache 过期时可显示最后计算时间；不能把 stale heat 当作可信度警告。
- API schema 新增字段时保持旧消息 API 不变，前端构建必须通过严格 TypeScript 校验。

## 实现对应、可调参数与未解决问题

实现路径为 `schemas/event.py`、`api/routes/events.py`、`services/event_presentation.py`、
`apps/web/lib/types.ts`、`apps/web/lib/api.ts`、`apps/web/app/events` 和事件组件。选择函数各有单元测试，
API 与前端有契约测试/构建验证。

可调参数包括列表默认排序、分页大小、timeline 收录关系、媒体评分和热度刷新 TTL。尚待产品确认
首页是否事件优先，以及 denied/stale 事件默认保留多久；v1 先保留现有消息页面与完整事件审计。
