# LoL Daily Intel API

FastAPI 后端，负责：

- 多平台 Connector 与不可变 RawItem ingestion；
- 媒体落盘、版本图片 OCR、翻译与消息分析；
- 人工审核、知识与术语管理；
- 持久化自动管线、checkpoint、失败恢复与分阶段撤回；
- Source 周期采集调度；
- NormalizedItem 发布与公开读取 API。

运行和架构以仓库文档为准：

- [`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md)
- [`../../docs/README.md`](../../docs/README.md)
- [`../../docs/LOCAL_RUNBOOK.md`](../../docs/LOCAL_RUNBOOK.md)
- [`../../docs/CONNECTOR_OPERATIONS_GUIDE.md`](../../docs/CONNECTOR_OPERATIONS_GUIDE.md)
- [`../../docs/REVIEWED_AI_WORKFLOW.md`](../../docs/REVIEWED_AI_WORKFLOW.md)
- [`../../docs/MESSAGE_CLASSIFICATION.md`](../../docs/MESSAGE_CLASSIFICATION.md)
- [`../../docs/IMPORTANCE_SCORING_POLICY.md`](../../docs/IMPORTANCE_SCORING_POLICY.md)
- [`../../docs/OCR_CONFIGURATION.md`](../../docs/OCR_CONFIGURATION.md)
