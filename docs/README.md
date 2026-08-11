# LeagueNews 文档导航

本目录根层只放当前实现仍在使用的架构、规则和运维文档。已经退出运行时的方案、阶段性
设计和一次性审计产物统一放在 [`history/`](history/README.md)，不得作为当前实现依据。

当前稳定节点记录：[`MESSAGE_PROCESSING_V1_MILESTONE.md`](MESSAGE_PROCESSING_V1_MILESTONE.md)。

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

## 运行与部署

- [`LOCAL_RUNBOOK.md`](LOCAL_RUNBOOK.md)：本地启动、停止、验证与排障。
- [`PRODUCTION_DEPLOYMENT.md`](PRODUCTION_DEPLOYMENT.md)：当前生产部署、更新、备份与恢复。
