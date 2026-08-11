# Prompt、规则与离线评测治理

## Prompt Registry

生产 LLM operation 必须在 `app/prompts/registry.py` 注册。当前消息处理 operation：

- `relevance`；
- `translation / v3-single-request`；
- `message-content-analysis / v6-title-summarizability`；
- `message-classification-importance / v13-community-promotion`；
- `knowledge-organization`。

每次调用记录 prompt 名称、版本、hash、Schema、模型、输入 hash、响应元数据、usage、延迟和
重试信息；批准后写入 checkpoint。不得记录 API Key、Header、Cookie 或其他凭证。

## KnowledgeRule

知识类型只允许 `analysis` 和 `translation`。生命周期为 `draft -> evaluated -> active -> retired`。
只有 active 规则进入 Prompt。分析规则按 global/source/connector scope 选择；翻译术语仅在原文
实际命中时注入。

分类枚举和边界不是动态知识规则，统一维护在
[`MESSAGE_CLASSIFICATION.md`](MESSAGE_CLASSIFICATION.md) 并通过
`message_taxonomy.py` 生成 Prompt 目录和执行校验。

## 离线评测

`app.evaluation.runner` 只接受当前五个任务：`relevance`、`image_ocr`、`translation`、
`message_analysis`、`importance`。JSONL 每行至少包含 `case_id`、`task` 和 `expected`；
`message_analysis` 样本会验证产品与内容形式约束；`importance` 样本应同时覆盖过滤后的消息类型、
主题和重要性结果。

真实回归集应从管理员明确选择的 Review 导出，并使用当前任务名和受控分类枚举；每次 taxonomy
或 Prompt 升级都要以新版本数据集全量比较。
