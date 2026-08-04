# Connector 与 RawItem 采集基座

本文是采集层的唯一架构说明。操作、登录和故障排查见
[`CONNECTOR_OPERATIONS_GUIDE.md`](CONNECTOR_OPERATIONS_GUIDE.md)，RawItem 字段细节见
[`RAW_ITEM_CONTENT_MODEL.md`](RAW_ITEM_CONTENT_MODEL.md)。

## 不变量

Connector 和 ingestion 不能同步执行翻译、OCR、LLM 分析、审核或事件聚合。ingestion
只保存不可变原文及 provenance，并可在同一事务写入 queued `pipeline_job` 作为下游交接；
后续 Worker 只能读取 RawItem，不能修改它。

```text
ConnectorRequest
  -> BaseConnector.fetch()
  -> 平台形状的 PlatformRecord
  -> BaseConnector.map_record()
  -> 经过校验的 RawItemCandidate
  -> ingest_connector_items()
  -> raw_items + raw_item_source_payloads + media_assets
  -> queued pipeline_job（启用自动化时）
```

## 四个边界

1. **Source**：一个具体站点或账号。账号标识和平台配置保存在 `sources`。
2. **fetch**：只访问平台并返回平台形状的 record，不构造 RawItem，不访问数据库。
3. **map_record**：纯映射。把一个平台 record 转成 `RawItemCandidate`，不访问网络或数据库。
4. **ingestion**：与平台无关，统一校验、计算语义哈希、识别 revision、下载图片并事务入库。
   启用自动化时只额外创建 queued job，不在请求内执行 AI。

`RawItemCandidate` 构造时会通过同一套 ContentBlock Pydantic 契约校验。Connector 不能
返回旧的 `video` block、非 HTTP embed、空文字块或任意未知字段。

## 去重与平台内容更新

`content_hash` 是 source-semantic hash：

- 参与：块顺序、文字、图片原始 URL、alt、caption、embed 类型和 URL；
- 不参与：本地 `storage_path` 和下载时推断的 `mime_type`；
- 算法版本保存在 `content_hash_version`。

同一 `source_id + external_id`：

- 语义哈希相同：跳过；
- 语义哈希不同：创建新的不可变 RawItem，`revision + 1`，并通过
  `supersedes_raw_item_id` 指向上一版。

没有 `external_id` 的手动内容按 `source_id + content_hash` 去重。

## 采集层持久化范围

采集层保留：

- `sources`
- `connector_runs`
- `raw_items`
- `raw_item_source_payloads`
- `media_assets`
- 新内容对应的 queued `pipeline_jobs`

`pipeline_jobs` 在这里仅是下游工作交接，不承载原文。`raw_item_source_payloads.payload`
是去除凭据和无关大字段后的 provenance/诊断快照，不是平台响应的逐字节归档。provider
和采集时间由表字段保存，不在 JSON 中重复。

其余表不属于采集基座，但并非可随意清理。当前知识、术语、OCR 修订、审核记录和已批准
`NormalizedItem` 都是后续开发的现有数据；除非用户另行明确要求，不得删除或重建。

## 增量 cursor 与批次完整性

调度水位保存在 `source_collection_schedules.collection_cursor`，而不是从
`last_success_at` 推导。cursor 包含已完成 watermark、正在追赶 capped timeline 时的
`pending_ids` 和 pending high watermark。Connector batch 同时报告 `cursor_used`、
`next_cursor`、`truncated` 和候选数，ConnectorRun 持久化这些诊断字段。

- ingestion 成功提交后才允许调度器保存 `next_cursor`；
- 完整批次才提升 watermark，truncated 批次保留旧 watermark；
- capped timeline 用 `pending_ids` 跳过已经成功入库的顶部内容，继续向旧水位扫描；
- 正常轮询从 watermark 向前重叠 `overlap_minutes`，边界消息和延迟返回依赖 RawItem 幂等；
- truncated 批次使用短间隔继续，不等待完整正常周期。

## 增加新平台

新 Connector 必须：

1. 声明唯一 `connector_type`；
2. 定义平台 record；
3. 实现 `fetch(ConnectorRequest)`；
4. 实现无副作用的 `map_record(record)`；
5. 用平台 fixture 单测映射；
6. 用 fake client 单测 fetch；
7. 注册到 `connector_registry`；
8. 通过共享 ingestion 测试，不自行写库或下载媒体。
