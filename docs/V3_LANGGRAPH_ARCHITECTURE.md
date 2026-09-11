# V3 架构

目标是让领域方法持续迭代，同时由稳定的工程层负责采集、恢复、审核与分发。适用当前每日约 100～200 条消息的体量，保持单体应用与 PostgreSQL。

```text
Connectors → shared ingestion → immutable RawItem
                                      ↓
API / Worker → Runtime / LangGraph → app/methods
                    │                │
                    │                └─ proposals / scores / membership decisions / plans
                    ↓
          审核交付、短事务、发布和独立事件任务
                    ↓
       NormalizedItem / revisions → Events → 精选、日报、分发

Frozen datasets → Experiment executor → 同一 methods / 消息图 / 事件应用服务
                     └─ 独立内存仓库、fixture 或实际模型客户端、输出报告
```

## 代码归属

- `app/methods` 定义可替换方法、输入输出和组装；`app/domain` 包含评分、召回、事件等确定性规则。
- `app/orchestration/*/graph.py` 描述阶段依赖、审核与恢复。日报图严格为
  `load_window -> select_candidates -> plan -> publish`，不包含审核，也不嵌入图外筛选策略。
- `app/orchestration/*/backend.py` 准备输入快照并调用共享方法，使用短数据库事务保存检查点和应用结果。
- `app/services/item_publication.py` 写当前投影、媒体引用、修订与下游队列；不能重写 RawItem 原始证据。
- `app/services/event_method_support.py` 验证和应用事件成员；`event_candidates.py` 实现默认异步检索适配器，排序由领域方法执行。
- `app/services/review_actions.py` 保存审核反馈和 OCR 修正；图恢复使用有独立交付状态的 ReviewTask。
- `app/orchestration/experiments` 管理冻结数据、case 调度、隔离仓库、评估与产物，不连接线上业务数据库。

## 执行身份

PipelineJob 的执行身份固定为 `workflow_name + target_entity_type + target_entity_id + target_revision`，
并保存消息/事件类型、方法配置、重试预算与租约；`raw_item_id` 只是 provenance、所有权、查询和
RawItem 修订 supersession 字段。消息和事件有独立完成状态。
ProcessingRun/EventAggregationRun 保存业务执行状态。业务 checkpoint 保留阶段输入输出的审计证据；
LangGraph checkpoint 保存恢复位置。审核决定记录与消费分别记账，避免提交决定后崩溃导致丢失或重新审批。

人工和自动入口共用 V3 流程。创建运行时使用短事务串行检查活动运行；数据库约束为最后一道并发防线。
远端模型调用之前结束持锁事务，结果落库时验证执行所有权。发布投影、修订和下游任务在同一事务内提交。

日报 scheduler 只检查上海自然日窗口、基础资格和 late update 重生成条件，然后触发 Daily Graph；
它不调用 selection method，也不与 `select_candidates`/`plan` 重复选择。当前 `media` 阶段承载按需
图片 OCR 与结构化解析；`image_ocr` 仅保留为旧运行和旧审核记录的读取兼容名。

## 迭代范围

调整 prompt、模型、重要性计算、召回排序、聚合决策、精选或日报策略，修改 methods/domain 并运行实验。
增加方法实现通常只需增加函数和注册项。只有阶段依赖或审核产品需求改变时，才需要修改图。

历史 SQL 迁移与历史记录保留，V2 执行器已经移除。部署与历史运行退役见 [V3_REFACTOR_PROGRESS.md](V3_REFACTOR_PROGRESS.md)，
配置、数据集和实验入口见 [V3_EXPERIMENTS.md](V3_EXPERIMENTS.md)。

## 2026-09-11 方法与计量边界收敛

本次审查基线为 `a2efffeaf3ba68232bb3bf635e76dca2c2ee0e5e`，开始时工作区干净；
Ruff 通过，后端基线 380 passed / 4 skipped。保留现有图阶段、统一审核与发布、冻结数据和实验存储。
确认并修复了全量事件池依赖、成功后才读取 usage、历史缓存计量混入本次运行、多标签列表比较和场景未展开等问题。
默认消息分析、分类重要性和事件聚合 prompt 的字节哈希与基线一致，没有做算法质量优化。

```text
Worker / API → Runtime（运行身份）→ Graph / Backend（阶段、恢复、审核）
  → MethodAssembly（独立配置快照与注册）
    → methods/llm_tasks.py（领域 prompt、输入、业务解释）
      → LLMClient（请求、每次尝试、校验前 usage、重试）
    → EventRetriever.retrieve(EventRetrievalQuery)
      → RuleEventRetriever（SQL 时间/族候选池）→ 既有召回排序方法
  → 统一校验 / 执行所有权 / 候选版本检查 → 事务应用

冻结数据 → run_experiment.py → 同一 Assembly / 领域任务 / 消息图 / 事件应用
  → 独立 SQLite 事件状态 + 每 case/candidate/step 的 ContextVar 计量
  → evaluators.py → report.json / comparison.json / comparison.html
```

`methods/model_client.py` 和 `methods/retrieval.py` 定义主要运行时端口。
`MethodAssemblyConfig` 只保存可序列化值；组装时深复制，选择时隔离嵌套参数。
检索器、客户端、记录器不进入 checkpoint。`with_config` 保留注册项和运行时检索依赖。
状态型检索器必须按候选/场景分别创建，不能把可变实验索引实例跨场景共享。

检索输入包含语义文本、产品、family/实体线索、发布时间、可见时间、窗口和数量。
输出是带事件 revision 的业务快照，可附分数、来源和理由。远端实现不接收全量事件或 ORM Session。
默认 SQL 候选池按与旧排序相同的时间窗口和 family 预筛选，排序政策不变。
Backend 在结束输入事务后 await 检索；应用时按事件 ID 顺序加锁，先验证版本再写入 mention。
检索的 version/source 元数据不新增到默认模型 prompt。旧同步召回函数仍有测试消费者，作为兼容入口保留。

索引的未来约束：Event 是事实来源，向量索引只是可重建派生数据。索引必须关联事件 revision、
embedding 模型版本和编码内容哈希；更新任务不能让旧版本覆盖新版本；未索引事件要有明确、可评测的
处理策略。实验索引按 candidate/scenario 隔离并限制当时可见范围。本次没有索引表、embedding 服务、
向量供应商、自动 fallback 或额外工作流引擎。

`services/call_metering.py` 在业务事务之外记录 started、response_received 和终态；
同一 attempt/status 重复投递去重，不合并不同尝试。生产默认保存在
`services/api/.artifacts/call-metering.sqlite3`，可用 `CALL_METERING_PATH` 指定持久卷路径；
部署时应将该文件所在目录挂到持久存储，容器替换不会自动迁移容器内文件。
它是独立本地计量账本，不是业务数据库或新的工作流状态。实验 CLI 保存独立 `.attempts` 账本。
记录器故障在发送请求前失败；收到响应后记录器故障也会中止调用，无法保证事故时完整账单核对。
未完成的 started/response_received 记录必须视为 incomplete，不能视为免费。

日志仅保存必要身份、哈希、有效模型参数和 usage，不新增原始 prompt、完整请求或密钥。
保留 SDK 原始 usage 子项，不能把缓存/推理子项再加到总量。
请求计数是应用层 SDK 调用次数；SDK 内部 HTTP 重试不可观测。费用使用显式版本化价格，未配置价格或
不支持的计费维度为 unknown。当前生产没有默认价格，也没有费用硬预算。
日报共享成本按 shared_daily 身份单列，不伪称单消息直接成本。
