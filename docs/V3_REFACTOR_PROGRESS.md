# V3 重构收尾状态（2026-09-07）

本文件替代此前四阶段的累计完成声明。此前仅凭配置记录和合成结果变化，不能证明真实请求生效、
线上离线一致或有状态事件实验；这些缺口已按实际调用路径修复。

## 已收拢的路径

- 领域方法移至 `app/methods`，与 LangGraph、Job、审核、发布分开；线上和实验共用 assembly。
- prompt/model 配置进入实际请求，未知模型参数及缺失 prompt 正文报错，实际调用元数据同步记录。
- 日报从完整候选集合生成计划，去掉图中的旧预先过滤、去重和分区阶段；日报查询、调度也使用共同方法。
- 精选公开查询和通知入队调用同一方法，按上海自然日比较完整集合。
- 重要性最终分数使用共享 domain 计算，`importance_calculation` 可独立替换公式；事件召回从数据库读取与纯排名策略中拆开。
- 实验有可注入模型客户端与显式 configured CLI；完整消息复用真实 V3 图，连续事件使用隔离仓库推进真实成员状态。
- 删除旧 reviewed_pipeline、event_aggregation、translate_item、understand_media 模块。
  保留的翻译、媒体、审核反馈和发布能力已迁移到各自服务，API/Worker/运维脚本不再调用 V2 执行器。
- 人工与自动启动在短事务内锁定 RawItem 并复用活动运行，避免并发重复创建。事务在模型调用前结束。
- 删除纠错服务遗留的 V2 context 拼装；V3 从有类型的检查点回放，旧运行从原始证据重建。

## 历史数据与部署

新增 `078_retire_legacy_execution.sql` 与 `079_finalize_v3_execution_identity.sql`。部署时先停止旧 Worker，再按顺序迁移并部署新版。
迁移使未完成旧运行退出执行，将相关待审核任务 supersede、相关 Job/correction cancel；保留全部原始证据、
历史提案、检查点、发布投影及修订。旧审核记录仍可查询，不能直接交付给 V3 图。
旧失败运行的 retry 会从原始证据创建新的 V3 运行，以 supersedes_run_id 保留关联。
迁移不自动重新发布或发送通知；需要重新处理的项目通过正常 retry/correction 入口进入新流程。
既有 001～078 迁移未编辑。本次未对现有开发库或生产库执行迁移。

079 将 PipelineJob 的目标身份收敛为 `workflow_name + target_entity_type + target_entity_id + target_revision`，
并使 `target_entity_id` 与事件运行 `thread_id` 在 ORM 中与数据库的非空约束一致。`raw_item_id` 保留为
来源、所有权、查询和 RawItem 修订 supersession 字段；事件失败恢复按完整执行身份和活动状态判断。
Event run 只有在 `graph_name`、`graph_version`、`state_version`、`thread_id` 全部匹配当前请求时才允许恢复；
不匹配会明确失败，不做 fallback 或 checkpoint/state 迁移。事件 membership/projection 结果也继续使用严格的
`extra="forbid"` 图契约。

## 验收方式

- 实际模型 transport spy 验证不同 prompt/model/temperature/token 上限进入请求，记录与请求一致。
- 两条同事件消息验证 balanced 日报在线与离线一致，并用自定义低分方案验证候选未被提前剔除。
- 精选测试同时验证公开查询和通知队列。
- 连续事件验证 create → attach、迟到消息不回退最新投影、重复 case 隔离。
- 完整 raw_to_daily 样例走真实消息图和真实评分公式。
- PostgreSQL 验证并发启动、任务 claim、真实 checkpointer、人工审核与独立事件任务。
- 此前收尾轮次曾在临时 PostgreSQL 库验证 001～078 初始化，以及含旧运行/任务/审核记录的 076 → 078 升级；这不是本轮新执行的结果。
- 保留原有业务测试并迁移到共享服务；删除只针对已删除 V2 私有阶段调度函数的测试。
  阶段顺序/审核/恢复由 V3 graph/service/backend 测试覆盖，没有保留一份 V2 引擎来满足旧测试。

实验使用方法见 [V3_EXPERIMENTS.md](V3_EXPERIMENTS.md)。领域效果仍需要真实标注集验证；示例替代算法仅用于证明替换能力。

## 本轮验证结果

- Ruff、后端完整套件、前端 lint/build 和 `git diff --check` 均在本轮实际执行并通过。
- 后端完整套件：373 passed，4 skipped；4 个 PostgreSQL 条件测试因本轮未配置
  `PIPELINE_TEST_DATABASE_URL` 而 skipped。
- 本轮未执行 PostgreSQL 数据库初始化、migration upgrade 或真实模型请求；上述 PostgreSQL/migration
  结果仅属于此前收尾轮次，不能视为本轮新验证。
- 本轮未修改既有 migration；当前 migration ledger 已到 079。
