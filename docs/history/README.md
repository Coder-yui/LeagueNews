# 历史文档

本目录保存已经退出当前运行时的设计和阶段性审计产物，仅用于追溯决策，不是实现、配置或
运维依据。当前文档入口见 [`../README.md`](../README.md)。

历史文档按来源分组：根目录文件是阶段方案或交接快照；子目录是一次性重构记录、评测快照或
旧 taxonomy 审计。若历史文档与代码、migration 或根目录当前文档冲突，以当前实现为准。

- `EVENT_EDITORIAL_POLICY.md`：旧事件编辑与聚合政策；事件运行时已移除。
- `INTELLIGENCE_DISTRIBUTION.md`：旧 Claim、Digest、Feed 与 MCP 分发设计。
- `pipeline-redesign.md`：消息管线重构期间的过渡设计，内容已并入当前工作流文档。
- `GOOGLE_CLOUD_FIRST_DEPLOY.md`：无域名首次预发布阶段的操作记录。
- `GOOGLE_CLOUD_SECOND_DEPLOY.md`：2026-08-16 大版本替换旧数据并迁入本地验收数据的第二次生产部署记录。
- `DEVELOPMENT_HANDOFF.md`：旧事件聚合、情报分发与 Windows 开发环境交接快照。
- `message-processing-v1/MESSAGE_PROCESSING_V1_MILESTONE.md`：2026-08-11 的消息处理 v1 阶段快照；当前消息处理流程见根目录 `REVIEWED_AI_WORKFLOW.md`。
- `event-aggregation-v2/EVENT_AGGREGATION_V2_REFACTOR.md`：Event V2 重构过程、责任划分和复杂度对比；当前契约见根目录 Event 文档。
- `event-aggregation-v2/EVENT_AGGREGATION_V2_REAL_DATA_EVALUATION.md`：2026-08-13 的一次真实数据评测快照，不是运行时规范。
- `message-taxonomy-audit-v0/`：采用旧分类枚举的 737 条一次性审计产物，包含报告、统计 JSON、分配 JSONL 和只读 HTML 查看器。

历史文档中的表名、字段、枚举、命令和页面可能已经失效，不能直接复制到当前环境执行。
