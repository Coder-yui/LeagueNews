# LeagueNews v3 LangGraph-first 架构

状态：可运行的 V2 兼容基线（本地探索，不替换云端 v2）
基线提交：`b2b60e4`（`v2.0.0`）

## 目标

v3 假设项目从第一天就选择代码优先的 LangGraph 编排，同时继续保持单台自有云服务器、
PostgreSQL、FastAPI、Next.js 和 Docker Compose。它不引入 Dify、Coze、LangChain、
LangSmith Agent Server、Redis 或 Celery。

v3 重写的是 AI 工作流编排，不重写已经验证的 Connector、不可变 RawItem、公开 API 和前端。
旧领域算法先通过明确标注的 `V2Compatibility*Backend` 接入，成为可回归的 baseline；后续实验候选只替换
Backend/Domain Component，不再改 Graph、队列或发布边界。v2 在 `main` 和 `v2.0.0` 标签上继续作为
生产稳定版本。

## 当前迁移矩阵

| 业务能力 | V3 入口 | 阶段 | 当前状态 |
| --- | --- | --- | --- |
| 消息处理与发布 | `item_processing` | evidence、relevance、media、translation、message_analysis、importance、evidence_gate、publication | 已接入 V2 LLM/OCR/规则/发布语义 |
| 事件聚合 | `event_aggregation` | load_message、minimal_filter、candidate_retrieval、semantic_decision、apply_membership、refresh_projection | 已接入 V2 召回、LLM、成员关系和事件指标 |
| 日报生成 | `daily_report_generation` | load_window、select_candidates、deduplicate_events、assign_sections、rank_items、publish | 已接入 V2 时间窗、去重、分栏、排名和持久化 |
| 发布后人工修订 | 事务命令，不进入 Graph | validate、lock、revision、field ownership | 消息和 Event 均已实现 |
| Connector 与 ingestion | 原有共享 ingestion | fetch、map、validate、dedupe、evidence persistence、enqueue | 保留，经验证后无需为了 LangGraph 重写 |

三个 Graph 都注册精确 `graph_version/state_version`，都支持 automatic/manual review，且
production/shadow/experiment 共用同一个图。Item Graph 的兼容适配器将每个业务阶段写入独立
`ProcessingCheckpoint`；Event Graph 还将六个阶段快照写入 `EventAggregationRun`。日报的恢复状态由
LangGraph checkpointer 管理，最终选择仍以 `DailyReport/DailyReportItem` 为唯一业务结果。

## 不变量

- RawItem 和原始媒体是不可变证据。
- LangGraph checkpoint 是技术恢复状态，不是业务审计记录。
- ProcessingCheckpoint、ReviewTask、NormalizedItemRevision 和 EventRevision 继续承担业务审计。
- 每个业务阶段在结构化结果通过自动或人工审核后写一个幂等 ProcessingCheckpoint；发布也写 checkpoint。
- Graph state 只保存 JSON 可序列化的小型状态、ID、版本和结构化提案，不保存 ORM 对象或媒体内容。
- 数据库事务不得跨越 LLM、OCR、Connector 等网络或长耗时调用。
- production、shadow、experiment 使用同一 Graph；只有 production 可以执行发布副作用。
- Event aggregation 是消息发布后的独立 Graph 和独立作业。

## 运行边界

```text
PostgreSQL workflow job
  -> invoke graph(thread_id)
  -> completed: finish job
  -> interrupt: persist ReviewTask, mark awaiting_review, release lease
  -> error: retry top-level run from LangGraph checkpoint
```

开源 LangGraph 不提供生产消息队列。v3 保留当前已经验证的 PostgreSQL `SKIP LOCKED`、lease、
heartbeat 和 fencing 机制，但把它收缩为不了解业务 stage 的通用 Graph launcher。

## 双层 checkpoint 与任意阶段回放

Item Graph 的阶段顺序固定为：

```text
evidence -> relevance -> media -> translation -> message_analysis
         -> importance -> evidence_gate -> publication
```

每个阶段有两类持久化，职责不能混用：

| 层次 | 写入时机 | 用途 | 保留策略 |
| --- | --- | --- | --- |
| LangGraph checkpoint | Graph 节点边界 | 进程崩溃或节点异常后，从失败节点续跑 | 运行时恢复数据，可按保留期清理 |
| ProcessingCheckpoint | 阶段结果审核通过后 | 审计、对比、人工选择历史阶段回放 | 业务历史，随 RawItem 修订失效但不删除 |

失败恢复与主动回放是两种操作：

- 同一次运行失败：继续使用原 `workflow_run_id` 和 `thread_id`，LangGraph 从最近技术 checkpoint
  重试失败节点，已完成的模型调用不重复执行。
- 人工选择任意阶段重做：必须创建新的 ProcessingRun，用 `replay_from_run_id` 指向旧运行，并指定
  `restart_from_stage`。新运行只加载目标阶段之前的已审核 ProcessingCheckpoint，目标阶段及其后续
  全部重新计算。旧运行和旧 checkpoint 不修改。

回放加载时必须校验 RawItem ID、RawItem revision、证据指纹、Graph/State 版本和前置阶段完整性。
发布阶段本身也可重试，但 `publish` 适配器必须以运行和目标 revision 做幂等与 fencing。

## 自动与人工审核

`review_mode=automatic` 是日常默认：策略自动批准正常提案，低置信度或规则命中的提案仍可
`interrupt` 升级人工。`review_mode=manual` 会在每个可审核阶段暂停；人工可以批准、拒绝，或提交
一个经过同一 Pydantic/业务规则验证的替代提案。只有批准后的最终提案会写业务 checkpoint 并进入
下游，自动和人工路径不维护两套处理实现。

## 发布后的人工直接修订

已发布消息和 Event 的编辑不通过 Item Graph，也不重新调用模型；它们是独立的事务命令：

```text
expected_revision + idempotency_key + editor + reason + validated patch
  -> SELECT ... FOR UPDATE
  -> 更新当前 projection
  -> current_revision + 1
  -> 追加完整 Revision 快照（revision_source=manual）
  -> 提交后立即成为公开结果
```

人工修订永远不修改 RawItem 或原始媒体。默认把改过的 projection 字段登记到
`manual_override_fields`，后续自动事件聚合只能更新未锁定字段，避免人工结果被下一条消息静默覆盖。
人工修订消息时，当前 EventMention 会复制到新的消息 revision，使该消息仍是同一事件的有效证据；
若编辑改变了事件语义或归属，则另行执行显式的 Event 编辑/重新归属命令，而不是偷偷运行 AI。

所有写操作使用 `expected_revision` 防止两名编辑互相覆盖，并用 `idempotency_key` 保证请求重试只生成
一个 revision。后续管理端还需要提供“解除字段锁”操作；解除也必须形成可审计 revision。

## Graph 和状态版本

每个运行固定记录 `graph_name`、`graph_version`、`state_version`、Prompt 版本和策略版本。
存在未完成运行时，对应 Graph builder 必须继续保留。Graph 升级通过注册新版本完成，不原地改变
暂停运行的状态契约。

## 生产与实验隔离

生产和实验共享 Graph 定义，但使用不同作业池和并发限制。实验运行必须有 `batch_id`，输出只能写入
evaluation artifact，不能创建 NormalizedItem、Event 或 notification outbox 记录。

建议初始值：production 并发 2，experiment 并发 6，OCR 并发 2。最终值依据模型 RPM/TPM、
数据库连接、CPU 和队列最老等待时间调整。

## 已完成的本地验收

- relevant production 路径能够得到发布结果；
- irrelevant 路径在昂贵阶段前结束；
- shadow/experiment 在类型和运行时两层阻止发布；
- 人工审核可以 interrupt，并以同一个 thread_id 恢复；
- 恢复时不重新执行已经 checkpoint 的 LLM 节点；
- 新运行可以从任意业务阶段回放，且只复用该阶段之前的已审核 checkpoint；
- manual 模式逐阶段暂停，人工替代提案经过与自动路径相同的验证；
- 已发布消息和 Event 可直接人工修订，具备乐观锁、幂等、编辑审计和字段所有权保护；
- Graph state 和业务表之间没有双重业务真相。

当前分支尚未切换 V2 的云端 Worker、Scheduler 或部署配置。这是刻意的发布边界，不是再保留一套新
业务实现：本地通过 V3 Graph/Backend 运行，云端 `main` 继续运行冻结的 V2。待新版算法实验完成后，
再让 PostgreSQL launcher 调用 Graph Registry，并完成影子运行和切换验收。
