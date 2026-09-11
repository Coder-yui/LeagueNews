# V3 领域方法实验

本手册对应 2026-09-11 方法与评测底座收敛后的实现。此前生成的 phase3/phase4 报告已经删除，数据集与计划仍保留，可重新运行。
示例都是合成数据，证明执行机制，不证明聚合、精选或日报的实际质量提升。

## 方法和执行边界

- `app/methods/contracts.py`：输入、配置、选择记录与输出契约。
- `app/methods/assembly.py`：选择、注册、调用；线上与实验使用同一个入口。
- `app/methods/baseline.py`：方法入口、精选和日报策略。
- `app/methods/llm_tasks.py`：默认领域 prompt、输入构造、业务校验；LLMClient 的同名方法保留为已有消费者的委托入口。
- `app/methods/retrieval.py`：异步检索 query/result/Protocol；默认实现位于 services/event_candidates.py。
- `app/methods/examples.py`：可运行的替代方法示例，不是推荐生产策略。
- `app/domain/message_scoring.py`：模型维度到最终分数、优先级的共同计算。
- `app/domain/event_recall.py`：候选池上的纯召回策略。
- `app/orchestration`：图、审核、恢复和实验执行器。
- `app/services`：读取快照、审核反馈、事务、发布和通知。

新增方法是在方法目录实现函数，并在 assembly 的注册处添加映射。可在测试/实验中注入自定义 assembly；
不需要修改 Worker、审核交付、发布事务。精选和日报同样按注册表调用，不再由组装器硬编码实现分支。
日报方法收到该日全部当前已发布候选，再自行筛选、去重、分区、排序。
事件获取依赖通过 `MethodAssembly(..., event_retriever=...)` 注入；默认获取器用 SQL 预筛选候选池，
再调用既有 `event_recall` 排序方法，`event_aggregation` 独立完成语义判断。

## 配置实际生效

线上使用 `PROCESSING_METHOD_CONFIG`（JSON），消息/事件入队时保存配置快照。完整配置字段为：

```json
{
  "message_analysis": "baseline",
  "importance_scoring": "baseline",
  "importance_calculation": "baseline",
  "event_recall": "baseline",
  "event_aggregation": "baseline",
  "featured_selection": "threshold",
  "daily_report": "baseline",
  "prompt_refs": {"message_analysis": "analysis-experiment-2"},
  "prompt_contents": {"analysis-experiment-2": "这里放完整的领域提示词"},
  "model_parameters": {"message_analysis": {"model": "你的模型标识", "temperature": 0.1, "max_tokens": 1600}},
  "strategy_parameters": {}
}
```

prompt ref 必须能在同一配置的 `prompt_contents` 中找到非空正文。模型参数目前支持 `model`、`temperature`、
`max_tokens`，不支持的字段报错。配置只改变本次调用视图，不修改共享客户端或全局 settings。
调用的实际模型、prompt 内容哈希、温度、token 上限记录在 LLM execution metadata 中。

精选 `top_n` 参数是 `max_items`、`min_importance_score`；threshold 参数是 `min_importance_score`。
线上按上海自然日形成完整候选集合，公开查询和通知入队使用同一方法。查询条件和分页不会改变当天的竞争集合。
推送在消息发布时判断，已经入队/发出的历史通知不会因为后续排名变化而撤回。
日报 baseline/balanced 接受 `section_limits`。召回 baseline 接受 `total_limit`（1～24）和 `window_days`。
这些策略参数应在开发集上调整，保留测试集独立验收。

## 三种实验粒度

1. **组件**：message_analysis、importance_scoring、event_importance、featured_selection、daily_report。
   固定上游输入，比较目标方法。重要性实验使用真实评分公式；event_importance 使用事件重要性计算，
   不再调用聚合模型冒充评分。
2. **完整消息**：item_processing 输入 `raw_item` 快照，运行实际 V3 消息图。相关性、媒体、翻译、分析、评分、
   证据门均执行，发布被禁止。需要人工审核的样本不能标为成功，需先整理输入再实验。
3. **连续事件 / 端到端**：事件场景按顺序在独立内存 SQLite 库推进，复用线上 admission、召回、校验、
   membership、projection 和指标计算。创建的事件会进入后续召回。每个 case/candidate 的库分别创建和销毁。
   END_TO_END 的每条消息都有 raw_item 时，完整消息结果继续进入事件、精选、日报；只有组件 evidence 时，
   明确记录 `entry_stage=message_analysis`、`complete_flow=false`。

RawItem 输入至少包括 `title`、`content_blocks`、`language`、`published_at`，可提供 `received_at`、source、
knowledge_rules 和 glossary。媒体输入放在 `input.media_artifacts`，每项包括 `uri`（绝对本地路径）、sha256、
block_index、mime_type。执行前核对文件内容哈希；不会下载在线媒体。OCR 结果写入本次隔离库，scope=experiment。

事件步骤应提供 published_at（发生/发布时间）和 received_at（系统可见时间）；迟到消息应保留两者差异。
初始 candidates 是冻结快照，不应包含未来事件证据。提供 visible_until/首步 received_at 时会拒绝超出边界的初始状态。
初始候选快照不包含完整成员历史；评价热度/可信度的连续变化，宜从完整历史消息预热，不能把仅含候选摘要的种子当成完整历史。

## 运行

在仓库根目录，使用已安装的 Python 环境；不需要连接业务数据库：

```bash
PYTHONPATH=services/api services/api/.venv/bin/python services/api/scripts/run_experiment.py \
  --provider fixture \
  --plan services/api/evals/phase4_fixture/raw_to_daily/plan.json \
  --artifact-root artifacts/experiments
```

fixture 仅替换远端模型客户端响应，真实规则仍执行。该命令不发送模型请求。

准备好实际模型配置后，将 `--provider` 改为 `configured`。这会发送真实模型请求；不读取样本里的 fixture_outputs 作为结果。
CLI 固定默认模型配置，并将 provider 身份与应用源文件哈希加入缓存身份，防止跨提供方或修改代码后复用旧结果。
调用密钥留在配置环境中，不写入计划或报告。

`--no-cache` 禁用结果缓存，`--no-resume` 禁用已完成 case 续跑，`--overwrite` 覆盖同名输出目录。
计划可设置并发和单 case 超时；配置 provider 的 `max_total_calls` 在实际请求前计数，包括应用层重试。
SDK 的连接重试仍由 `LLM_MAX_RETRIES` 管理。尚未接入提供方价格，因此 configured 模式拒绝 `max_cost_usd`，不会把费用写成已受控。
Runner 在 case 边界检查方法调用预算，复合 case 可能在结束时超出该预算并标为失败；这不是逐次请求的硬上限。configured CLI 另在每次模型请求前执行硬上限检查。
报告 `call_count` 是已完成执行器返回的方法调用数（包括纯评分公式、召回等）；失败时以
`metadata.method_timings` 中已记录的方法调用为证据，不能假设失败前没有执行。
`run_metadata.actual_provider_requests` 是 configured CLI 发出的应用层模型调用数；fixture 为 0。
逐次请求见 `metadata.attempts`，其中 synthetic_fixture 是合成响应/usage，不是实测模型消耗。

## 数据集与迭代

保留 manifest + cases.jsonl，修改证据后升级 dataset version 并重新导出指纹。
标签存于 labels，执行器不会收到 labels/expected；human_confirmed、model_prefill、synthetic_fixture、unlabeled 必须区分。
同一事件/故事放入同一 group，不跨 train/validation/test；连续事件用时间顺序场景，精选和日报用完整日窗口。

建议先人工整理一小批真实失败样本：同实体不同事件、同事件多平台转述、迟到进展、传闻转官宣、跨产品消息。
每次只改变一个方法或一个关键参数，先做组件对比，再重放连续场景，最后运行 raw_to_daily 验证下游影响。
合并准确率、错误拆分、漏召回与编辑评价应分别看；不要只看总平均分，也不要用合成样例的通过率宣布领域效果改善。

## 最短迭代路径

1. **黄金集**：复制 [case 模板](../services/api/evals/templates/case.json)，按
   [标签说明](../services/api/evals/templates/README.md) 填写输入及人工标签。
   未标注使用 unlabeled + 空 values；模型预填使用 model_prefill。labels/tags 不进入执行器，输入内嵌
   expected/labels/gold_labels/tags 会报错。导出 manifest+cases.jsonl，升级 dataset/label 版本，运行原有 CLI。
2. **只改 prompt/模型**：在候选 `parameters.method_config` 中填写 prompt_refs/prompt_contents/model_parameters；
   默认 prompt 在 `methods/llm_tasks.py`，不改 Worker 或通用请求重试代码。先跑组件，再跑 raw_to_daily。
3. **换消息方法**：在 methods 中实现相同输入/结果契约，在 Assembly 注册；候选配置选择实现名称。
   现有 raw_to_daily 计划演示 baseline 与 heuristic/rule_v2 的切换，属于合成机制示例。
4. **外部检索**：实现 `async retrieve(EventRetrievalQuery) -> tuple[RetrievedEvent, ...]`，组装时注入，
   自己获取候选，不接受 ORM Session；结果必须带当前业务 revision。参考
   [异步检索测试](../services/api/tests/test_async_event_retrieval.py)，其等待期间无占用连接，并走真实 membership 应用。
   未注册实现和不支持的配置显式报错；本次不实现 vector 或 embedding。

推荐入口始终是 `scripts/run_experiment.py`。`app/evaluation/runner.py` 尚有
`reprocess_all_raw_items.py` 的 load_jsonl 消费者及历史快照比较测试，因此保留兼容，不作为新方法实验入口。

## 质量、计量、延迟与缓存

- gold_metrics 仅使用 human_confirmed；没有人工标签为 null，并提供 gold_unavailable_reason。
  合成样例的执行成功、fixture projection 检查均不是质量正确率。
- products/topics 使用集合的 precision/recall/F1 与 exact match；单选字段给出准确率、分母和错误分布。
  重要性维度、确定性公式回归、最终分数 MAE 分开；MAE 是有数值输出时的条件误差，必须同时查看缺失数。
- summary_facts 是字面短语诊断，不是事实正确率；semantic_review 允许人工记录但尚不自动评分。
- 连续事件按 step_id 展开；mention_index 保留多事件能力，event_key 作为黄金逻辑分组。
  关系指标使用同事件成员对，避免实验自增 ID 干扰。mention presence、关系指标和 action/family/product
  准确率是不同指标；孤立事件没有成员对时关系指标不可用。召回只计算此前已出现的黄金事件，报告分母；
  初始种子无完整逻辑成员历史时不纳入召回率。错误拆分/合并按错误成员对计数，不冒充事件总数。
- 每 case 的 metadata 保存 expected、label_source、attempts、method_timings；事件指标有逐 step 诊断。
  比较 HTML/JSON 同时保留预测、标签来源、失败类别和计量细节。invalid、failed、manual_review、budget_stopped 分开。
- attempt 在响应到达后、JSON/schema/业务校验前保存 usage；最终失败、超时和取消保留此前已知消耗。
  application_attempt_count 与 logical_call_count 不等于全部 HTTP 请求数。SDK 内部重试未知。
- fixture 每次固定提供 **11 输入 / 7 输出合成 token**，仅测试聚合路径，measurement_kind=synthetic_fixture；
  不代表 tokenizer 估算或真实模型账单。configured 使用提供方 usage；缺失为 unknown/partial，不补零。
  total token 只加输入输出，不重复加缓存/推理子项。
- cost_usd 无价格时 null；known_cost_usd 是已知部分之和，须同时看 unknown_cost_attempts/cases。
  合成价格只存在确定性测试中；任何 `max_cost_usd` 均显式拒绝。实际模型需要 `--provider configured`，
  `max_total_calls` 是逐次模型请求的硬门槛，另有 Runner 的 case/方法预算计数，不能混作计费请求数。
- case_latency_ms 是本次离线 case 执行耗时分布；automatic_wall_ms 是 Runner 墙钟时间。
  方法阶段耗时、请求耗时与 case 时延不能相加冒称端到端时间。生产排队、人工等待未纳入离线时延。
- 缓存/续跑复用预测，duration_ms=null、call/token/cost 本次增量为 0；历史记录放在 historical_measurement。
  本次 attempts 与 method_timings 为空，不进入当前请求分布。CLI 的有效源码哈希/模型配置/prompt/数据指纹
  参与缓存身份，evaluator_version 记录当前评测器身份。
- CLI 在 artifact-root/.attempts 持久保存逐次账本，即使 no-cache/no-resume 也保存。只有 started 或
  response_received 的未终结记录表示不完整，不能据此推断调用没消耗。生成的账本和报告不提交。

## 离线演示与验证

```bash
PYTHONPATH=services/api services/api/.venv/bin/python services/api/scripts/run_experiment.py \
  --provider fixture --plan services/api/evals/phase4_fixture/raw_to_daily/plan.json \
  --artifact-root artifacts/experiments --overwrite

PYTHONPATH=services/api services/api/.venv/bin/python services/api/scripts/run_experiment.py \
  --provider fixture --plan services/api/evals/phase3_fixture/plans/events.json \
  --artifact-root artifacts/experiments --overwrite

services/api/.venv/bin/python -m pytest services/api/tests/test_async_event_retrieval.py \
  services/api/tests/test_call_metering.py services/api/tests/test_frozen_evaluators.py \
  services/api/tests/test_experiment_measurement_boundaries.py -q
```

重复第一条命令验证缓存；加 --no-cache --no-resume 强制新执行。events 计划含一个故意失败场景，
其后续 step 必须 invalid，不能算成方法答错或成功样本。所有样例均为 synthetic_fixture。

PostgreSQL 锁与版本竞争仍需独立可销毁 PostgreSQL 验证；SQLite 只验证方法和事务入口的控制流。
准备空的隔离数据库，设置仅指向该数据库的 DATABASE_URL 运行 `scripts/migrate_database.py`，随后设置
同一地址为 PIPELINE_TEST_DATABASE_URL，运行 test_pipeline_postgres.py 与 test_v3_postgres_acceptance.py。
不要使用现有业务数据库。2026-09-11 本机未提供 PostgreSQL 工具，Docker daemon 未启动，四项 opt-in 测试未运行。

本次验收记录：起点 `a2efffe`，Ruff 通过；最终后端 **404 passed / 4 skipped**（基线 380/4）。
raw_to_daily 的 baseline/candidate 均完成，分别记录 8/2 次合成调用；真实模型请求为 0，gold_metrics=null。
重复运行命中缓存，本次新增 attempt/call/token/cost 均为 0，账本行数不增长。
events 两个候选各完成一个正常场景，并正确标记一个故意失败的场景及其后继步骤。
本次未改公开 API、前端、ORM 或 SQL 历史迁移，因此未运行前端 lint/build 或数据库初始化/升级验证；
新增事件锁/版本检查的 PostgreSQL 并发语义仍属于上述未验证项。
