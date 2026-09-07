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
- `app/orchestration/*/graph.py` 描述阶段依赖、审核与恢复。日报图只有窗口、候选读取、方案生成、审核与发布，不嵌入筛选策略。
- `app/orchestration/*/backend.py` 准备输入快照并调用共享方法，使用短数据库事务保存检查点和应用结果。
- `app/services/item_publication.py` 写当前投影、媒体引用、修订与下游队列；不能重写 RawItem 原始证据。
- `app/services/event_method_support.py` 验证和应用事件成员；`event_candidates.py` 读取候选池，排序由领域方法执行。
- `app/services/review_actions.py` 保存审核反馈和 OCR 修正；图恢复使用有独立交付状态的 ReviewTask。
- `app/orchestration/experiments` 管理冻结数据、case 调度、隔离仓库、评估与产物，不连接线上业务数据库。

## 执行身份

PipelineJob 固定消息/事件类型、目标修订、方法配置、重试预算与租约；消息和事件有独立完成状态。
ProcessingRun/EventAggregationRun 保存业务执行状态。业务 checkpoint 保留阶段输入输出的审计证据；
LangGraph checkpoint 保存恢复位置。审核决定记录与消费分别记账，避免提交决定后崩溃导致丢失或重新审批。

人工和自动入口共用 V3 流程。创建运行时使用短事务串行检查活动运行；数据库约束为最后一道并发防线。
远端模型调用之前结束持锁事务，结果落库时验证执行所有权。发布投影、修订和下游任务在同一事务内提交。

## 迭代范围

调整 prompt、模型、重要性计算、召回排序、聚合决策、精选或日报策略，修改 methods/domain 并运行实验。
增加方法实现通常只需增加函数和注册项。只有阶段依赖或审核产品需求改变时，才需要修改图。

历史 SQL 迁移与历史记录保留，V2 执行器已经移除。部署与历史运行退役见 [V3_REFACTOR_PROGRESS.md](V3_REFACTOR_PROGRESS.md)，
配置、数据集和实验入口见 [V3_EXPERIMENTS.md](V3_EXPERIMENTS.md)。
