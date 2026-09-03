# 消息处理架构

> 历史设计：本文是消息管线重构阶段的过渡方案。当前消息处理流程见
> [`../REVIEWED_AI_WORKFLOW.md`](../REVIEWED_AI_WORKFLOW.md)，当前总体边界见 [`../ARCHITECTURE.md`](../ARCHITECTURE.md)。

当前消息管线只有五个受控阶段：

```text
relevance -> optional image_ocr -> translation -> message_analysis -> importance
```

对需要完整处理的非中文消息，四次 LLM 调用依次是：相关性、整条翻译、产品与内容分析、
消息类型/主题与重要性特征。OCR 是指定类型消息的条件分支；中文消息无需翻译，纯媒体或纯链接
无需进入 importance，因此实际调用数可以少于四次。

`message_analysis` 在一次调用中生成标题、摘要、实体和前两个分类轴：

```text
products[] + content_form
```

分类枚举、名称、定义、候选矩阵和边界规则统一维护在
[`MESSAGE_CLASSIFICATION.md`](../MESSAGE_CLASSIFICATION.md)，运行时实现位于
`services/api/app/domain/message_taxonomy.py`。`importance` 调用根据已批准的产品和信源只接收适用的
`message_type`、`topics` 候选，在同一次响应中返回这两个分类轴和重要性特征。

`importance` 沿用既有算法，由
`services/api/app/domain/importance.py::derive_importance_inputs` 将新 taxonomy 投影成评分函数的
内部输入。该适配只服务评分，不是第二套消息分类，也不会把旧字段写入 NormalizedItem。

消息处理的输出是一个完整 `NormalizedItem`。当前到此停止，不生成 Claim、不执行事件候选
召回、不创建或更新事件。事件聚合将在单独完成设计后作为 NormalizedItem 之上的新层实现。
