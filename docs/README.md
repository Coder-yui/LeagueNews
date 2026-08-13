# LeagueNews 文档导航

本目录根层只放当前实现仍在使用的架构、规则和运维文档。已经退出运行时的方案、阶段性
设计和一次性审计产物统一放在 [`history/`](history/README.md)，不得作为当前实现依据。

阅读规则：先看本文和 [`ARCHITECTURE.md`](ARCHITECTURE.md)，再进入对应模块的专题文档；代码、
追加式 SQL migration 和当前配置是最终事实来源，文档不能覆盖它们。`history/` 只用于追溯决策，
`design/` 用于未来产品与视觉设计，不是运行时规则或部署手册。

消息处理当前流程见 [`REVIEWED_AI_WORKFLOW.md`](REVIEWED_AI_WORKFLOW.md)，系统总边界见
[`ARCHITECTURE.md`](ARCHITECTURE.md)。历史里程碑快照统一见 [`history/`](history/README.md)。

## 架构与数据

- [`ARCHITECTURE.md`](ARCHITECTURE.md)：当前系统边界、目录、API 与数据表职责。
- [`RAW_ITEM_CONTENT_MODEL.md`](RAW_ITEM_CONTENT_MODEL.md)：不可变 RawItem 与 ContentBlock v2。
- [`CONNECTOR_ARCHITECTURE.md`](CONNECTOR_ARCHITECTURE.md)：Connector、Source 与 ingestion 边界。

## 采集与消息处理

- [`CONNECTOR_OPERATIONS_GUIDE.md`](CONNECTOR_OPERATIONS_GUIDE.md)：Connector 配置、运行与排障。
- [`REVIEWED_AI_WORKFLOW.md`](REVIEWED_AI_WORKFLOW.md)：RawItem 到 NormalizedItem 的唯一处理流程。
- [`MESSAGE_CLASSIFICATION.md`](MESSAGE_CLASSIFICATION.md)：当前消息分类枚举、候选矩阵和边界规则。
- [`IMPORTANCE_SCORING_POLICY.md`](IMPORTANCE_SCORING_POLICY.md)：当前重要性与排序优先级算法。
- [`OCR_CONFIGURATION.md`](OCR_CONFIGURATION.md)：OCR Profile、参数、启用方式与保留边界。
- [`PROMPT_RULE_GOVERNANCE.md`](PROMPT_RULE_GOVERNANCE.md)：Prompt、KnowledgeRule、术语与评测治理。

## 事件聚合

- [`EVENT_AGGREGATION.md`](EVENT_AGGREGATION.md)：当前 Event V2 membership 流程和不变量。
- [`EVENT_AGGREGATION_V2.md`](EVENT_AGGREGATION_V2.md)：当前 Event membership contract、验证边界和评测边界。
- [`EVENT_ADMISSION_AND_GRANULARITY.md`](EVENT_ADMISSION_AND_GRANULARITY.md)：零调用准入、事件粒度、候选召回和单次结构化调用。
- [`EVENT_IMPORTANCE.md`](EVENT_IMPORTANCE.md)：事件自身影响的确定性评分。
- [`EVENT_CREDIBILITY.md`](EVENT_CREDIBILITY.md)：来源角色、独立证据和确认/否认规则。
- [`EVENT_HEAT.md`](EVENT_HEAT.md)：消息传播、时间衰减、去重和按需刷新。
- [`EVENT_PRESENTATION.md`](EVENT_PRESENTATION.md)：当前事件投影、时间线、API 和前端展示。
- [`DAILY_REPORT.md`](DAILY_REPORT.md)：日报窗口、筛选、去重、分区和 API。

上述文档是当前事件运行时的规则来源。历史事件方案仍只在 [`history/`](history/README.md) 中作为
审计材料，不能替代当前 V2 文档。

## 运行与部署

- [`LOCAL_RUNBOOK.md`](LOCAL_RUNBOOK.md)：本地启动、停止、验证与排障。
- [`PRODUCTION_DEPLOYMENT.md`](PRODUCTION_DEPLOYMENT.md)：当前生产部署、更新、备份与恢复。

## 设计资料

- [`design/leaguenews-visual-redesign-plan.md`](design/leaguenews-visual-redesign-plan.md)：后续视觉改版计划，
  不代表已经实现的页面行为。
- [`design/universe-visual-study.md`](design/universe-visual-study.md)：视觉研究记录，不能作为产品或运行时契约。

## 历史资料

已退出当前运行时的旧方案、交接快照和一次性评测统一见
[`history/README.md`](history/README.md)。历史文档中的表名、字段、命令和页面可能已经失效，
不得直接复制到当前环境执行。
