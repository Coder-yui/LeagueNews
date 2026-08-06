# 消息与事件处理架构

## 消息管线

最终处理顺序：

```text
relevance
→ optional image_ocr
→ translation
→ fact_classify
→ importance
→ claim_gen
→ event_decision
```

`fact_classify` 在后台顺序执行事实抽取和分类，但只形成一个人工检查点，同时展示事实标题、
摘要、实体、内容类型、主题、实体角色与时间属性。`claim_gen` 审核通过后直接生成
`NormalizedItem` 并进入事件归属审核，不再增加重复的“消息定稿”检查点。

消息层不计算综合可信度。`NormalizedItem` 保存事实、分类、带 `core|context|affected`
角色的实体、`facets.product_scope`、编辑基准重要性、来源与模型处理元数据。重要性由模型
提取内容子类型与编辑特征后，按版本化基准分、修正项和类型区间确定性计算，用于内容排序；
Claim 保存可追溯的原子事实主张，不包含概率模型。
Claim 不是把摘要逐句拆开，而是记录以后可能被确认、更新、推翻或取代的事实，用于事件时间线
和跨消息修订关系。只有名单、没有逐项数值或机制的版本预览，按
`champion_buff|champion_nerf|system_buff|system_nerf` 分组保存，目标列表放在同一条
Claim 中；出现具体数值或机制时才拆为单独 Claim。

`Source.is_official` 与 `Source.reliability_score` 是数据库中的显式、可编辑配置。官方身份
不再由运行时账号名单推断，也不会由 `reliability_score == 1` 推断。

## 原始消息修订

同一信源、同一 `external_id` 只与当前最新版本做语义比较。正文的行尾空白以及贴吧图片
URL 的临时 `tbpicau` 查询参数不参与语义哈希，点赞数等 provenance 变化也不会产生修订；
正文、图片路径等真实证据变化才会创建下一版 RawItem。内容恢复成更早版本时仍会创建新的
revision，以准确记录来源当前状态。

新修订入库时，前一版的排队/运行任务、审核和 checkpoint 会被取消或失效；已发布
NormalizedItem、Claim 和事件成员转为 superseded/withdrawn 历史，新版本重新走完整消息
处理和事件判断。公开网页、管理台、事件成员列表与待处理任务查询只使用修订链最新版本，
历史记录继续保留用于追溯。

## 事件证据与可信度

`EventMessage` 持久化 `membership_role`、`evidence_stance`、`independence_key`、
`source_reliability_snapshot`、`is_official_evidence`、`timeline_note` 与 `update_kind`。

官方证据必须同时满足：来源标记为官方、消息不是引用/转载、立场为 supports 或
contradicts、节点不是 context。优先级如下：

1. 官方支持与官方反对同时存在：`disputed`, 0.5。
2. 官方支持：`official_confirmed`, 1.0；适用生命周期推进到 `confirmed`。
3. 官方反对：`officially_refuted`, 0.0；生命周期为 `officially_refuted`。

非官方活动证据按 `independence_key` 去重，同一键只取最高可靠性快照。原创键为
`source:{source_id}`；转载键为规范化后的 `upstream:{host}{path}`；无法识别上游的转载
不参与加成。只有支持证据时：

```text
base = max(independent_support.reliability_score)
boost = 0.1 × min(independent_support_count - 1, 3)
score = min(0.9, base + boost)
```

零、一个、多个支持信源分别对应 `unverified`、`single_source`、
`multi_source_supported`。非官方支持与反对同时存在时为 `disputed`, 0.5。API 同时返回
支持、反对和全部独立信源数量，并另行返回官方信源数量。

## 事件重要性与排序

事件重要性与可信度完全独立。先在非 context、非 duplicate_evidence 的活动成员中取
`is_significant_update=true` 的最高消息重要性；没有重大更新时回退到其余合格活动成员的
最高值。官方身份和信源数量不产生重要性加成。

事件列表、公开 Feed 与摘要输入默认按 `importance_score DESC`，同分再按
`last_published_at DESC`。未确认但重要的事件可以展示，并由事件可信度状态提供标签。

## 路由、时间线与维护

- 国服神话商城使用 `mythic_shop:cn:{ISO_YEAR}-W{ISO_WEEK}`，跨年不会碰撞；非国服内容
  不进入该事件策略。
- 消息时间线按原消息发布时间从旧到新展示，`timeline_note` 为节点主文案，原始消息为证据。
- `get_event_timeline` 的 Claim 视图返回 active、superseded、withdrawn 等完整历史，并带
  supersession、归因、证据、时间和事件关系。
- 人工事件修正必须通过 `EventDecisionDraft` 和候选/聚合键业务校验，并同步写入
  `run.decision_draft` 后才能批准。
- 事件纠错保存所有 `original_event_ids`，重新聚合时全部强制召回。
- pipeline worker 每日调用幂等维护入口；也可单独运行
  `python -m scripts.run_event_maintenance`。超过阈值且无新证据的未确认时间线仅产生一次
  `expired_unconfirmed` 修订，后续官方证据仍可恢复为 confirmed。
