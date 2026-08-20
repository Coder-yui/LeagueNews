# LeagueNews v3 本地实验工程

状态：工程底座；暂不部署，不替换云端 v2。

## 当前目标

v3 当前只建立稳定的工程边界，不预先决定新版算法。消息处理、重要性、事件聚合和日报将在固定
数据集上经过多轮候选实验后，才选择新的 Prompt、规则、模型和 Graph 结构。

以下 v2 资产继续复用：Connector、共享 ingestion、不可变 RawItem、媒体证据、领域类型、公开
projection 和 revision 历史。实验不得修改这些事实数据或任何已发布结果。

## 目录职责

```text
app/orchestration/
  catalog.py                 精确版本 Graph 注册表和实现状态
  runtime.py                 Graph、Backend、checkpointer 的唯一应用组装入口
  item_processing/           消息 Graph 与 V2 baseline adapter
  event_aggregation/         事件 Graph 与 V2 baseline adapter
  daily_report/              日报 Graph 与 V2 baseline adapter
  experiments/
    contracts.py             数据集、候选方案、运行上下文和结果契约
    runner.py                有限并发执行、case 隔离、可插拔指标
    artifacts.py             本地结果原子落盘

app/domain/                  可测试的纯规则和评分函数
app/prompts/                 Prompt 名称、版本和 schema 注册
app/services/                数据库、模型、OCR 等适配器
```

Graph 只负责编排；领域规则不写进 Graph 节点；数据库和外部模型访问只能通过 Backend/Port。这样
重要性公式、事件候选算法或日报排序改变时，不需要改 Worker、checkpoint 或实验基础设施。

## 版本单位

一次候选实验必须完整记录：

- 数据集名称、版本、输入 schema 和内容指纹；
- Graph 名称、Graph 版本和 state 版本（若该目标使用 Graph）；
- Prompt、分类、重要性、事件策略、日报策略等 component versions；
- 模型参数、阈值和其他候选参数；
- 每个 case 的实际结果、失败类型和指标。

禁止使用 `latest` 作为实验版本。运行中的 Graph 版本不得原地修改；新方案注册新版本。

## 五类实验目标

| target | 现在的工程形态 | 后续实验重点 |
| --- | --- | --- |
| `item_processing` | Item Graph 已有结构 | 节点拆分、Prompt、人工升级策略、端到端质量 |
| `message_importance` | 独立候选执行器 | profile、分段、修正项、阈值和稳定性 |
| `event_aggregation` | Event Graph 和 V2 baseline 已实现 | 准入、候选召回、关系判断、事件更新 |
| `event_importance` | 独立候选执行器 | 多证据组合、时间因素、来源与实质性权重 |
| `daily_report` | Daily Report Graph 和 V2 baseline 已实现 | 时间窗、去重、分区、排序、容量与人工编辑 |

重要性算法保持为可独立实验的纯组件，不强行拆成单独 Graph。Item 和 Event Graph 在对应阶段调用
指定版本的评分组件，因此可以单独比较评分，也可以做端到端回归。

## 执行和评分分离

`ExperimentExecutor` 只为一个候选产生预测；`ExperimentEvaluator` 只负责计算指标。通用 Runner：

- 候选之间依次运行，避免不同方案争抢本机或模型配额；
- 同一候选的 case 使用 `max_concurrency` 有限并发；
- 单 case 失败转成结构化结果，不中断整批；
- case 输出顺序与数据集顺序一致；
- 执行上下文固定为 `run_mode=experiment`、`publication_allowed=false`。

简单任务可以使用 `ExactMatchEvaluator`，事件归属、分数误差、排序质量等使用目标专属 Evaluator。

## 数据集和产物

小型、脱敏、人工确认的 golden dataset 可以提交到 `services/api/evals/`。批量预测、模型原始响应和
本地运行报告写入 `services/api/.artifacts/<experiment_id>/`，该目录被 Git 忽略。

Artifact Store 默认拒绝覆盖同名实验，写文件时先生成临时文件再原子替换。若确实需要重跑，应使用
新的 experiment ID；`overwrite=True` 只用于明确的本地调试。

## 当前进度与下一轮实验顺序

1. 已完成三个 Graph 的 V2 baseline adapter；experiment 路径均不写发布、事件或日报业务表。
2. 固定第一批 RawItem、NormalizedItem/Event 和日报日期快照，建立人工确认的 golden cases。
3. 分别比较消息分析/重要性候选，不改变 Item Graph 的发布与 checkpoint 边界。
4. 比较事件候选召回与语义决策候选，再做端到端聚合回归。
5. 比较日报去重、分区、排序和容量候选，最后才选择 V3 默认策略版本。

在本地结果稳定之前，不实现生产切换、云端 Worker、部署脚本或 V2 数据迁移。
