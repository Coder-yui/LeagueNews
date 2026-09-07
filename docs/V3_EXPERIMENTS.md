# V3 领域方法实验

本手册对应 2026-09-07 收尾后的实现。此前生成的 phase3/phase4 报告已经删除，数据集与计划仍保留，可重新运行。
示例都是合成数据，证明执行机制，不证明聚合、精选或日报的实际质量提升。

## 方法和执行边界

- `app/methods/contracts.py`：输入、配置、选择记录与输出契约。
- `app/methods/assembly.py`：选择、注册、调用；线上与实验使用同一个入口。
- `app/methods/baseline.py`：模型调用、精选和日报策略。
- `app/methods/examples.py`：可运行的替代方法示例，不是推荐生产策略。
- `app/domain/message_scoring.py`：模型维度到最终分数、优先级的共同计算。
- `app/domain/event_recall.py`：候选池上的纯召回策略。
- `app/orchestration`：图、审核、恢复和实验执行器。
- `app/services`：读取快照、审核反馈、事务、发布和通知。

新增方法是在方法目录实现函数，并在 assembly 的注册处添加映射。可在测试/实验中注入自定义 assembly；
不需要修改 Worker、审核交付、发布事务。精选和日报同样按注册表调用，不再由组装器硬编码实现分支。
日报方法收到该日全部当前已发布候选，再自行筛选、去重、分区、排序。事件召回方法收到可用事件池，
`event_recall` 与 `event_aggregation` 可分别替换。

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
报告 `call_count` 是方法调用计数（包括纯评分公式、召回等）；`run_metadata.actual_provider_requests` 是 CLI 记录的实际应用层模型请求数，二者不要混用。

## 数据集与迭代

保留 manifest + cases.jsonl，修改证据后升级 dataset version 并重新导出指纹。
标签存于 labels，执行器不会收到 labels/expected；human_confirmed、model_prefill、synthetic_fixture、unlabeled 必须区分。
同一事件/故事放入同一 group，不跨 train/validation/test；连续事件用时间顺序场景，精选和日报用完整日窗口。

建议先人工整理一小批真实失败样本：同实体不同事件、同事件多平台转述、迟到进展、传闻转官宣、跨产品消息。
每次只改变一个方法或一个关键参数，先做组件对比，再重放连续场景，最后运行 raw_to_daily 验证下游影响。
合并准确率、错误拆分、漏召回与编辑评价应分别看；不要只看总平均分，也不要用合成样例的通过率宣布领域效果改善。
