# 黄金数据填写模板

`case.json` 是未标注输入模板，复制后填写稳定 case_id、真实冻结证据、接收时间和故事 group_id。
`labels.json` 只是**合成的标签结构示例**，不是任何消息的黄金答案；选择实验 target 对应的对象放入
case.labels.values。未经人工确认保持 source=unlabeled、values={}；模型预填为 model_prefill，人工确认后才改为 human_confirmed。
人工确认时填写 evidence_basis 与标签 schema_version，数据变动升级 manifest.version 并重新导出指纹。

- 不存在的标签字段不评价。评分值示例 0.8 不代表编辑政策推荐值。
- products/topics 为集合；content_form/message_type/各重要性维度单选。
- summary_facts 和 unsupported_summary_facts 只做字面短语诊断；语义评审可记录在 semantic_review，当前不自动评分。
- 事件 labels.steps 按 step_id 对齐，mentions 使用当前消息的 mention_index，event_key 是跨步骤的人工逻辑身份。
  同一条消息可以有多个 mention；不要把实验数据库自增 ID 填入 event_key。
- event_key 关系指标比较成对成员关系；孤立事件没有成对关系时关系分母为空，不推断语义身份正确。
  当前候选召回率只评价此前步骤已出现的同一黄金事件，初始种子尚无完整逻辑成员历史时不进入召回分母。
- 迟到消息的 published_at 可以早于前一步，但 received_at 必须递增。标签、tags、评审备注不要放入 input 或 initial_state。
- 同一故事必须在一个 split 内。训练与验收场景不能拆开同一故事。

数据导出、实验命令与计量说明见 [实验指南](../../../../docs/V3_EXPERIMENTS.md)。
