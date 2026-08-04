# Prompt、规则与离线评测治理

## 任务边界

LLM Prompt 的名称、版本和 Schema 标识由 `app/prompts/registry.py` 登记。生产单条分析把
事实/实体/分类抽取与重要性评分拆成两个调用；可信度由 Source 权威度和转发状态等确定性
策略计算。Prompt 不把历史规则当作当前消息事实来源。

每次成功调用把 prompt 名称/版本/hash、Schema、模型、provider host、temperature、
max tokens、输入 hash、响应、usage、延迟、重试、finish reason 和可用的 commit SHA
写入阶段 proposal，批准时进入不可变 checkpoint。Checkpoint 同时记录本次实际选择的
KnowledgeRule 与 GlossaryTerm ID/version，不记录 API Key、Header 或 Cookie。

## KnowledgeRule 生命周期

- `draft`：审核拒绝产生的 rule candidate；不进入 Prompt。
- `evaluated`：已完成整理或初步评测，但尚未获准生产使用。
- `active`：管理员明确晋升后才进入检索和 Prompt。
- `retired`：停止使用但保留审计历史。

`is_active` 是旧 API 的兼容投影，并由数据库约束保持与 `lifecycle_status=active` 一致。
`GET /api/v1/knowledge/conflicts` 报告带相同 `constraint_key` 但文本不同的 active 规则，以及
同 scope/source term 却有不同首选译名的 active 术语。管理员必须先消解冲突再晋升。
知识整理只生成仍需回归评测的 `draft` 结果，不停用或替换现有 active 规则。

## 检索

规则按 knowledge type、global/source/connector/category scope 选择。翻译术语只有当
`source_term` 实际命中当前原文时才注入，不再无条件加载 500 条。后续若引入 alias，应把
alias 作为显式、版本化字段并使用同一确定性匹配和冲突检查，不交给模型猜优先级。

## 离线评测

版本化 JSONL 位于 `services/api/evaluation`。当前少量 `regression-v1` 来自已知策略回归，
不是伪造的大规模人工标注。比较命令：

```bash
cd services/api
.venv/bin/python -m app.evaluation.runner \
  evaluation/regression-v1.jsonl \
  evaluation/example-candidate.jsonl \
  --json-output /tmp/evaluation.json \
  --markdown-output /tmp/evaluation.md
```

机器输出包含总体/分任务 exact match 和错误案例；Markdown 供人工审阅。管理员可以对明确
选择的 Review ID 调用受保护的 `/api/v1/knowledge/evaluation-export`，得到原输入、原模型
输出、纠正值和 Review provenance，并在离线完成真实标签。导出不会包含 Source payload、
Cookie、Token 或请求 Header。规则或 Prompt 晋升前应保存 candidate 标识并运行回归比较。
