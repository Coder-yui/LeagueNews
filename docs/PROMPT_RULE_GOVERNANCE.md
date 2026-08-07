# Prompt、规则与离线评测治理

## 任务边界

LLM Prompt 的名称、版本和 Schema 标识由 `app/prompts/registry.py` 登记。生产单条分析把
事实抽取、分类、重要性特征提取和 Claim 生成分成独立调用；最终重要性由程序确定性计算，
可信度由 Source 权威度和事件成员证据等确定性策略计算。Prompt 不把历史规则当作当前消息
事实来源。

每次成功调用把 prompt 名称/版本/hash、Schema、模型、provider host、temperature、
max tokens、输入 hash、响应、usage、延迟、重试、finish reason 和可用的 commit SHA
写入阶段 proposal，批准时进入不可变 checkpoint。Checkpoint 同时记录本次实际选择的
KnowledgeRule 与 GlossaryTerm ID/version，不记录 API Key、Header 或 Cookie。
所有生产 LLM operation 必须在 Registry 中显式注册；未注册名称会立即失败。实验性 operation
仍可通过显式 opt-in 使用 `unregistered-v1`，生产调用不得静默降级。

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

消息处理规则按 knowledge type 以及 global/source/connector scope 选择；事件聚合规则按
global/topic/subtopic scope 选择。翻译术语只有当
`source_term` 实际命中当前原文时才注入，不再无条件加载 500 条。后续若引入 alias，应把
alias 作为显式、版本化字段并使用同一确定性匹配和冲突检查，不交给模型猜优先级。

## 离线评测

版本化 JSONL 位于 `services/api/evaluation`：

- `regression-v1.jsonl` 是早期合成的工程冒烟样本。
- `raw-items-ontology-v2.jsonl` 从本地 737 条 RawItem 中的 706 个修订链头部人工选取并标注，
  当前包含 52 条自包含样本。31 条被后续修订取代的记录只作为历史证据，不进入独立评测。
- `raw-items-event-chains-v1.jsonl` 包含 23 个真实时间线节点和 7 个事件组，覆盖 26.15
  版本从设计师预览到正式发布与转发、Flandre 转会、经典模式通行证、NIP 对 WBG
  从预告到赛果与赛后讨论、经典模式发布，以及两组防误合并案例。两个真实数据集的
  RawItem 并集为 68 条，全部是修订链头部。

真实样本覆盖证据门禁、端游和云顶版本、PBE、商城与活动、赛事与转会、纪律与服务、
实体周边、社区内容、转发、Wild Rift/2XKO 排除边界、图片依赖和链接空壳。每条输入保留
RawItem ID、来源、真实标题/文本证据片段、媒体数量和修订头标记；期望输出使用受控分类轴、
重要性区间和聚合键前缀。评估器额外拒绝非法受控标签、含 `unknown` 的路由键和越界分数。

候选文件每行使用相同 `case_id`，并提供 `actual` 对象；允许附加解释字段，但期望字段必须
匹配，`importance_score` 必须落入 `importance_band`，`route_keys` 必须匹配标注前缀。比较命令：

```bash
cd services/api
.venv/bin/python -m app.evaluation.runner \
  evaluation/regression-v1.jsonl \
  evaluation/example-candidate.jsonl \
  --json-output /tmp/evaluation.json \
  --markdown-output /tmp/evaluation.md
```

链级数据集通过 `chain_id`、`chain_stage`、`chain_order` 和 `event_group` 检查同组是否
共享最终事件身份，并检查不同组之间是否发生误合并。批处理可重复传入 `--dataset`，按多个
数据集引用的 RawItem 并集执行一次全流程。

机器输出包含总体/分任务匹配率、错误案例、本体不变量错误计数和覆盖标签统计；Markdown
供人工审阅。`regression-v1` 只验证 runner，两个 RawItem 数据集用于本体、Prompt 和事件链
回归，但仍应随着新来源和新失败案例持续扩充。管理员可以对明确
选择的 Review ID 调用受保护的 `/api/v1/knowledge/evaluation-export`，得到原输入、原模型
输出、纠正值和 Review provenance，并在离线完成真实标签。导出不会包含 Source payload、
Cookie、Token 或请求 Header。规则或 Prompt 晋升前应保存 candidate 标识并运行回归比较。
