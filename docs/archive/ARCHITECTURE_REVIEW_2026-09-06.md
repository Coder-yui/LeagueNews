**LeagueNews V3 架构审查 · 2026-09-06**

我的结论是：核心业务建模和单机技术路线合理，足以作为日均 100–200 条消息的长期基础；V3 的 LangGraph 引入有实际价值，但迁移尚未收口。当前主要风险在新旧运行机制的接缝、实验配置与真实执行脱节，以及恢复链路中的事务边界。建议保留骨架，集中收敛这些边界，再迭代算法和切换运行环境。

这次审查阅读了主链路、领域规则、ORM 模型、关键迁移、运行脚本、API 和部分前端代码，并进行了隔离验证。没有连接项目现有数据库、调用真实采集平台或模型，也没有修改业务代码及既有迁移。本文中的容量数字是计算示例，不是生产压测结果；模型语义质量、真实消息分布和实际运维成本仍需数据评估。

| 用户关心的问题 | 判断 |
| --- | --- |
| 整体架构是否合理 | 主体合理：采集、证据、消息投影、事件、分发边界值得保留 |
| 数据表设计是否合理 | 总体合理，表数本身不是负担；运行身份和版本约束需要整理 |
| 代码是否臃肿 | 局部存在重复和耦合，集中在 V2 workflow、V3 adapter、Worker 的重叠职责 |
| 分层是否清晰 | 目录层面清晰，依赖和事务所有权尚不完全清晰 |
| 是否便于领域方法迭代 | 已有接口和实验骨架，但版本真正绑定实现、冻结输入、隔离副作用尚未闭合 |
| LangGraph 引入得如何 | 消息流程已有收益；通用运行器、事件审核、日报恢复仍不完整 |
| 日均 100–200 条是否需要当前基础设施 | 单机 PostgreSQL + API + Worker 足够合理，无需增加分布式组件 |
| 是否建议现在直接切换 V3 | 建议先修复本文已复现的恢复问题，再做完整故障验收 |

**从第一性原理看，项目真正需要稳定的是什么。**

LeagueNews 的产出是可追溯、及时、可纠正的资讯与事件。采集平台可能失败，模型可能出错，消息会修订，事件会发展，人会介入。低流量减少了吞吐压力，却没有消除这些正确性要求。

因此，最小但可靠的系统需要六种能力：保存不可变证据；用可替换的方法生成结构化提案；统一验证自动与人工结果；明确发布事务；可恢复地执行工作；基于固定输入比较处理方法。

这些要求支持使用数据库任务、幂等、修订历史和适量 checkpoint。它们不要求微服务、多代理系统或复杂的分布式队列。评判复杂度的尺度应当是“这个机制消除了哪种故障，或者降低了哪种修改成本”，而不是消息量与代码行数的简单比例。

**已有设计中，值得保留的部分很多。**

Connector 表示平台能力，Source 表示具体账号或站点，共享 ingestion 承担去重、证据持久化和入队。这能避免每个平台重复实现一套数据生命周期。[ingestion.py](/Users/czh/Projects/LeagueNews-v3/services/api/app/services/ingestion.py:34)

RawItem 采用追加修订，NormalizedItem 保存当前发布结果，Revision 保存历史；Event 通过 EventMention 位于已发布消息之上。这既允许重做算法，又能保留消息与事件证据的来源。EventMention 的当前读取条件集中处理 publication_status 和消息 revision，方向正确。[raw_item.py](/Users/czh/Projects/LeagueNews-v3/services/api/app/models/raw_item.py:10)、[normalized_item.py](/Users/czh/Projects/LeagueNews-v3/services/api/app/models/normalized_item.py:23)、[events repository](/Users/czh/Projects/LeagueNews-v3/services/api/app/repositories/events.py:46)

消息重要性、事件重要性、可信度和热度采用独立规则，多个评分组件已经能单独测试。人工发布后修改通过 revision 和 expected_revision 管理，通知使用 outbox 与发布事务衔接。这些机制有明确业务价值，应继续保留。[domain](/Users/czh/Projects/LeagueNews-v3/services/api/app/domain/importance.py:598)、[editorial_revisions.py](/Users/czh/Projects/LeagueNews-v3/services/api/app/services/editorial_revisions.py:119)、[publication](/Users/czh/Projects/LeagueNews-v3/services/api/app/workflows/reviewed_pipeline.py:1133)

**最优先的发现：事件任务存在跨会话锁等待，已在 PostgreSQL 17 复现。**

触发条件是 Worker 领取一个 current_stage=event_aggregation 的任务，例如消息人工发布后产生的下游任务，或者事件阶段失败后的重试。

入口先通过 assert_execution_owned 对 PipelineJob 执行 SELECT FOR UPDATE，然后在外层事务尚未结束时等待 Event Graph。Graph 的 save_stage 使用另一个 Session，并再次通过同一个 execution_guard 锁定相同 PipelineJob。第二个会话等待第一个会话释放锁，第一个会话却等待 Graph 返回。

在临时 PostgreSQL 中设置 1 秒 lock_timeout，真实 Worker 入口、Event Graph 和 PostgreSQL checkpointer 组合触发了 LockNotAvailable，SQLSTATE 55P03。仅在调用 Graph 前释放外层事务，同一任务随后得到 completed/applied。模型使用测试替身；锁行为和 checkpoint 使用真实 PostgreSQL。

这是会影响恢复路径的正确性问题，优先级高于代码整理。应明确事务所有权：启动 Graph 的调用方不能持有 Graph 副作用所需的锁；各个短事务在提交自身副作用时检查 lease/fencing。心跳也不能依赖被同一事件循环中的同步数据库等待阻塞的执行路径。

证据：[Worker 入口](/Users/czh/Projects/LeagueNews-v3/services/api/app/services/automatic_pipeline.py:178)、[锁实现](/Users/czh/Projects/LeagueNews-v3/services/api/app/services/pipeline_execution.py:21)、[Graph 阶段落库](/Users/czh/Projects/LeagueNews-v3/services/api/app/orchestration/event_aggregation/backend.py:160)。

**审核决定与 Graph 恢复之间有崩溃窗口，已做故障注入复现。**

approve_review 先提交 ReviewTask=approved、ProcessingRun=running，再执行 Command(resume=...)。如果在提交后、Graph 收到决定前进程中断，业务表认为已经批准，Graph 仍然暂停。

使用 SQLite 和 InMemorySaver 在两步之间注入中断，再走正常 resume，最终出现同一 relevance 阶段同时具有一条 approved 审核和一条新 pending 审核。人工决定没有丢失，但恢复代码没有把已有决定可靠地交付给 Graph，用户需要重复处理。

建议把“决定已经记录”和“决定已经由对应 interrupt 消费”分开记录。审核命令需要稳定身份、对应的运行与暂停点，以及幂等的重新交付路径；可以复用数据库队列，不必增加中间件。恢复必须先检查未交付决定，不能仅依赖当前 HTTP 请求里的 Command。

证据：[批准后再恢复](/Users/czh/Projects/LeagueNews-v3/services/api/app/orchestration/item_processing/service.py:292)、[重新创建待审核记录](/Users/czh/Projects/LeagueNews-v3/services/api/app/orchestration/item_processing/service.py:121)。

**消息与事件拥有独立 Graph，但自动路径还没有独立作业生命周期。**

发布适配器尝试 enqueue_pipeline_job(..., current_stage="event_aggregation")，然而队列按 raw_item_id 查找任意活跃作业，唯一索引也以 raw_item_id 为范围。自动消息作业正在运行时，入队返回原作业，不创建事件作业。

隔离验证中，存在 running/relevance 消息作业时完成消息发布，数据库仍只有这一条 running/relevance 作业。之后 Worker 自己把 current_stage 改为 event_aggregation，并继续调用事件流程。因此消息发布与事件确实已有不同业务运行记录，但自动执行仍共用一个 job 的 attempts、完成状态与恢复入口。

这不会自动回滚已经发布的消息，却会让事件继承消息之前消耗的重试预算，也让 Worker 继续理解消息阶段、审核策略和事件调度。文档所说的“不懂业务 stage 的通用 Graph launcher”尚未实现。

建议让 job 具有明确的 workflow kind、目标实体及目标 revision，按该执行身份去重。消息发布事务应原子写入独立事件作业，然后结束消息作业。Worker 只负责 claim、invoke/resume、pause、retry、finish。

证据：[队列去重](/Users/czh/Projects/LeagueNews-v3/services/api/app/services/pipeline_queue.py:9)、[发布入队](/Users/czh/Projects/LeagueNews-v3/services/api/app/orchestration/item_processing/backend.py:693)、[Worker 续跑事件](/Users/czh/Projects/LeagueNews-v3/services/api/app/services/automatic_pipeline.py:261)。

**领域迭代的接口存在，但实验版本配置还不能可靠表达“实际运行了哪个方法”。**

CandidateSpec 有 component_versions 和 parameters，不过现有 Item/Event/Daily Graph executor 均持有预先注入的单个 backend。执行时没有使用这两个字段组装模型、提示词和策略。因此，仅改变候选配置中的模型、阈值或组件版本，并不会据此切换对应实现；若 graph_version 相同，可能得到两份不同标签、同一种方法的实验报告。

在线 Runtime 也直接构造三个当时命名为 V2Compatibility 的 backend。PromptRegistry 为调用方传入的提示词内容附加版本标签，没有按指定历史版本加载提示词内容的能力。消息恢复入口则直接拒绝不等于当前常量的 graph_version/state_version；即使 Registry 增加旧 builder，这个入口仍需同步修改。第四阶段已将正式实现重命名为 V3 backend，并用 MethodAssembly 固定实际候选。

建议在运行开始时解析并固定一个执行配置：Graph/State 版本、具体领域组件、完整提示词或内容指纹、模型参数、规则和术语快照。在线和实验都用同一个 backend factory 根据该配置组装；实验产物同时记录声明配置与实际执行配置。历史运行恢复应按自身版本解析，不能只接受当前默认版本。

证据：[实验 executor](/Users/czh/Projects/LeagueNews-v3/services/api/app/orchestration/experiments/item_processing.py:28)、[Runtime](/Users/czh/Projects/LeagueNews-v3/services/api/app/orchestration/runtime.py:40)、[PromptRegistry](/Users/czh/Projects/LeagueNews-v3/services/api/app/prompts/registry.py:52)、[恢复版本检查](/Users/czh/Projects/LeagueNews-v3/services/api/app/orchestration/item_processing/service.py:45)。

实验输入也需要真正冻结。现有内置 executor 主要接受 RawItem/NormalizedItem 的 ID、revision 或日报日期，backend 仍读取当前规则、当前事件库和当前日报候选。数据集 JSON 的 fingerprint 只覆盖这些引用，并不覆盖所有被引用内容。事件评估尤其需要固定消息顺序、截至当时的候选事件和时间，否则同一 case 可能随着事件库增长而改变。现有事件召回还有时间下界而无相对消息时间的上界，历史消息实验可能召回后来出现的事件。

**实验模式阻止了最终发布，但媒体路径仍有共享数据副作用。**

Item Graph 的 understand_media 接口只接收 EvidenceSnapshot，没有运行模式。当前 V2 adapter 在需要 OCR 时会更新 MediaAsset 的 OCR/尺寸信息、把旧提取标成 superseded，并提交新的 MediaExtraction。后面的非 production 发布拦截无法阻止这些写入。

这是代码路径确认的隔离缺口，本次没有调用真实 OCR。已有“实验不写业务表”的测试使用普通文本样本，只检查 NormalizedItem、ProcessingCheckpoint 和 PipelineJob，无法覆盖这个分支。

建议将媒体处理结果写入按输入内容、OCR 参数和组件版本寻址的独立 artifact/cache，并由 production 发布动作建立批准引用。实验使用只读输入和单独输出空间。run_mode 标志应作为一层防线，而不应承担全部隔离责任。

证据：[媒体入口及写入](/Users/czh/Projects/LeagueNews-v3/services/api/app/orchestration/item_processing/backend.py:197)、[已有隔离测试](/Users/czh/Projects/LeagueNews-v3/services/api/tests/test_v3_item_backend.py:100)。

**LangGraph 的收益是真实的，但三个流程的工程完成度不同。**

消息 Graph 把模型提案节点、审核节点和业务 checkpoint 节点分开，支持 interrupt 和同一 thread 恢复，也把领域操作放在 Protocol 后面。这比继续在一个巨大函数里增加 if/status 分支更利于维护。尤其是先保存提案、再等待审核，能避免正常审核恢复时重复请求模型。

事件 Graph 已有阶段划分和发布模式控制，但应用层将自动执行中的 interrupt 作为异常；运行表也没有 awaiting_review 状态，Runtime 的事件入口没有接收审核 Command 的参数。日报 Graph 虽有审核节点，在线服务固定 automatic，每次默认生成新的 run ID，Runtime 始终以新输入调用，没有对应的恢复入口。它们具备 Graph 层能力，不等于已经具备可操作、可恢复的产品流程。

日报目前主要是确定性的筛选、去重、分区和排序。独立纯函数便于实验，不代表每个函数都必须是持久化 Graph 节点。当前体量下可以将其收敛成“生成方案 → 可选审核 → 发布”；如果接受失败后从头重算，也可以保留普通应用服务。这里继续细化 Graph 的收益较小。

双层 checkpoint 本身合理：技术 checkpoint 服务进程恢复，业务 checkpoint 服务接受结果的审计和主动重做。关键是定义唯一业务真相、保留策略和两者之间的恢复契约。LangGraph 不会自动把业务数据库提交与技术 checkpoint 变成同一个原子操作；重放可能再次执行尚未可靠记录完成的节点，副作用仍须幂等。官方文档对此也有明确要求。[LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)、[persistence](https://docs.langchain.com/oss/python/langgraph/persistence)

**代码量不算失控，重复维护同一行为才是主要负担。**

审查时统计：后端 app 有 156 个 Python 文件、25,405 行；其中 services 7,601 行、orchestration 4,085 行、workflows 2,856 行、domain 2,163 行。后端测试有 55 个文件、15,365 行；前端 TS/TSX/CSS 共 6,770 行。统计包含注释和空行，不能作为删除代码的依据。

对一个同时包含多个采集平台、媒体/OCR、管理审核、修订重跑、事件、日报和通知的项目，这个总量不离谱。薄 API、集中 schema 和纯评分组件也说明项目已经有结构。

真正需要拆分的是 reviewed_pipeline.py 的 1,472 行、Item V2 adapter 的 780 行，以及仍处理业务阶段的 automatic_pipeline.py。V3 adapter 直接导入 V2 workflow 的多个私有 helper；这些 helper 同时承担内容构造、规则读取、发布和修订职责。运行层改变会牵动领域实现，领域修改也会触及旧 workflow。

建议先把公共能力提取为稳定模块：证据读取、分析组件、提示词与模型适配、统一业务校验、发布事务。V2 和 V3 在过渡期调用这些公共模块，随后只在真正需要恢复旧运行的入口保留 V2 编排。无需给每张表都增加 repository，也无需强行重建一套复杂的领域框架。

另一个分层实例是 ingestion：先取得事务级 advisory lock，再 await 媒体下载，并在整批结束才提交。慢图片会放大事务时长，后续候选失败也可能回滚前面已处理的候选。建议先准备媒体，再在每条消息的短事务里重新检查身份、分配 revision、持久化和入队；对整批原子性的取舍应明确，而不是隐含在函数结构里。[ingestion.py](/Users/czh/Projects/LeagueNews-v3/services/api/app/services/ingestion.py:55)

**数据库最需要整理的是运行契约，不是减少表数。**

目前有 26 张 ORM 业务表，另有迁移记录及 LangGraph 技术表。76 次迁移是迭代历史，并不代表当前存在 76 份业务模型。证据、提取、发布、历史、审核、运行和 outbox 分开存储，大多有合理原因。

| 数据类别 | 当前判断 | 建议 |
| --- | --- | --- |
| Source、RawItem、provenance、原始媒体 | 边界清楚，支持重复采集和源修订 | 保留不可变证据与追加修订 |
| NormalizedItem、Revision、批准媒体关联 | 当前读模型与历史分离合理 | 明确人工字段所有权在所有重处理入口的语义 |
| Event、EventMention、EventRevision | 多消息、多事件和证据关系合理 | 定义算法升级后哪一套 membership 是当前结果 |
| Run、Review、业务 checkpoint、Job | 职责可以区分，但执行身份与状态分散 | 明确谁负责调度、暂停、交付决定、记录结果 |
| JSON 提案、计算过程、内容块 | 适合变化较快的结构 | 保留 schema 版本和校验；常用过滤字段继续使用关系列 |
| LangGraph checkpoint | 适合技术恢复 | 对已结束运行设置保留和清理机制，保护暂停运行 |
| DailyReportItem | 适合动态汇编 | 若要求历史日报原样可追溯，再补消息 revision/快照 |

具体而言，PipelineJob 绑定 RawItem 而非工作流目标，EventAggregationRun 把部分 V3 信息放在 JSON 且仍使用旧阶段集合，日报缺少与调度对应的持久运行身份。此外，ProcessingRun、ReviewTask、ProcessingCheckpoint、PipelineCorrection 的 stage 枚举分别出现在数据库 CHECK 中；新增消息处理阶段会同步牵动多处迁移。应区分稳定生命周期状态与可版本化的内部 stage 名称，避免每次算法节点调整都改变数据库结构。

EventMention 的唯一键包括 aggregation_policy_version，但当前 mention 读取只按发布状态和消息 revision 筛选。将来若同一消息 revision 并存多套聚合策略结果，单纯增加版本字段还不足以选择“当前有效的一套”。应通过显式激活身份或替代关系完成切换，而不是在当前消息上混读不同策略。[EventMention](/Users/czh/Projects/LeagueNews-v3/services/api/app/models/event.py:213)、[当前过滤条件](/Users/czh/Projects/LeagueNews-v3/services/api/app/repositories/events.py:46)

数据体积暂时不构成更换 PostgreSQL 的理由。200 条/天约 73,000 条/年；假设全部走完 8 个消息业务阶段且不重跑，对应约 584,000 条业务 checkpoint/年。技术 checkpoint、媒体及快照的字节量需要实际测量；应优先管理保留期限和重复大字段，而不是提前分库分表。

**规模适配：吞吐可以简单，事件质量和积压恢复需要认真处理。**

若单个 Worker 每条消息连同事件阶段平均占用 60 秒，处理 200 条约需 3.3 小时；120 秒约需 6.7 小时。200 条均匀分布在全天，平均间隔为 7.2 分钟。这些只是容量算术，但足以说明日均量本身不支持引入更重的基础设施。

需要测量的是批量到达：100 条同时到达、每条 60 秒，串行处理末尾消息要等约 100 分钟。应围绕发布时效决定并发，而不是仅看“每天能跑完”。先记录端到端及分阶段 P50/P95、最老等待时间、重试次数、人工积压和模型消耗；在上述边界修复后，再考虑消息处理 1–2 并发。

事件聚合建议先保持一个执行通道，因为它是共享状态的增量整理：两条消息同时召回时，都可能还看不到对方将创建的事件。当前按消息的锁及 mention 幂等可以防同一消息重复写，不能直接防不同消息把同一新事件各建一次。只有测出事件处理成为瓶颈，才值得做提交时重新匹配或更细粒度的协调。

当前候选召回先取全局最近最多 500 个事件，再在 Python 按产品和 family 过滤。事件增长后，某产品较老但相关的事件可能在过滤前就被挤出，影响聚合质量。应优先将产品/family 条件下推 SQL，保留必要的候选多样性，并用实际召回率决定是否需要全文搜索或向量召回；不能仅因使用 AI 就添加向量数据库。[event_candidates.py](/Users/czh/Projects/LeagueNews-v3/services/api/app/services/event_candidates.py:48)

运维已有 metrics 基础，但 V3 checkpoint 使用 execution_metadata 字段，当前 metrics 读取的是 _execution_metadata，会漏掉对应的 V3 模型统计。这类迁移后的可观测性断点应一起修复。[metrics](/Users/czh/Projects/LeagueNews-v3/services/api/app/api/routes/health.py:55)、[V3 checkpoint](/Users/czh/Projects/LeagueNews-v3/services/api/app/orchestration/item_processing/backend.py:573)

**建议按下面的次序收敛，避免再做一次大重写。**

1. 先修可靠性：消除外层 Session 持锁调用 Graph；持久化并幂等交付审核决定；为跨会话失效 worker 的状态回写补齐 fencing；补上真实 PostgreSQL 故障测试。
2. 再明确运行身份：拆开消息与事件作业和重试预算；定义 paused/awaiting_review 的队列语义；落实按运行版本恢复。Worker 收缩为启动和监督工作流的组件。
3. 建立可信的算法迭代闭环：候选配置实际组装组件；冻结输入、规则和时间；隔离 OCR 与其他副作用；记录实际执行配置。事件实验按时间顺序演进隔离事件库，而不只是独立比较每条消息。
4. 收敛代码：将 V2 私有 helper 提取为公共领域与应用组件，保留必要的历史恢复适配；合并日报中收益较低的持久化节点；统一 proposal 校验入口。
5. 根据实测调节：修复 metrics、设定发布时效和终止运行的 checkpoint 保留策略；再决定并发、索引、召回方式和模型调用合并。

收敛后的职责可以保持很小：采集负责证据入库；数据库 Job 负责可靠交付；LangGraph 负责消息/事件的暂停与阶段推进；领域组件负责可替换的方法；发布服务负责校验后原子写入 projection、revision 和下游 job/outbox；实验 Runner 用冻结输入运行相同领域组件，并只写隔离产物。

**本次验证与结论边界。**

- Ruff：通过。
- 常规后端测试：353 passed，3 个 PostgreSQL 测试初次跳过；随后在临时 PostgreSQL 17 中单独执行这 3 项，全部通过。
- 临时 PostgreSQL：全新数据库执行 001→076 共 76 次迁移成功；事件 Graph 实际使用 PostgreSQL checkpointer。
- 额外故障验证：复现事件任务跨 Session 锁等待；释放外层事务后同一任务成功；复现审核提交后中断导致同阶段重复待审核；验证自动消息发布未创建独立事件 job。
- 临时容器采用内存临时数据目录，验证后已停止并自动删除。没有修改业务代码、既有迁移或连接现有业务数据库。
- 本次没有执行真实模型质量评测、负载测试、前端交互验收或历史数据库带数据升级验收。仓库 CI 已配置 fresh/legacy schema 对照验证，这值得保留；但现有三个 PostgreSQL 测试并未覆盖本次发现的 V3 Worker→Graph 完整恢复路径。

LeagueNews 现有资产值得继续演进。下一阶段最有价值的工作，是让“领域方法可以更换，而运行正确性保持稳定”从文档意图变成可验证的执行契约。
