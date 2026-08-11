# 事件可信度

> 状态：Phase 3 已实现
>
> 策略版本：`event-credibility-v1`

## 目标与非目标

可信度回答“当前证据有多可靠”。v1 使用最强证据、独立来源佐证和冲突/否认证据的可解释规则，
不建立概率模型。可信度与重要性、热度、生命周期分别存储。

`Source.is_official` 只是账号属性，不自动证明该 Source 对当前事件有权责；官方账号转发他人爆料
也不是官方确认。20 个账号搬运同一爆料可以增加热度，但不能算 20 个独立可信来源。

## 输入、输出与枚举

每条事件证据使用：

```text
relation: reports | supports | confirms | denies | corrects | mentions
source_role:
  responsible_official | direct_subject | first_party_participant |
  independent_media | known_leaker | ordinary_account | republisher | unknown
independence_group: string | null
source_reliability_snapshot: 0.0 .. 1.0
content_form, upstream relation, evidence_excerpt
```

输出：`credibility_score`（数据库 0–1，展示 0–100）、独立支持/冲突计数、breakdown，以及：

```text
unverified | plausible | corroborated | officially_confirmed | disputed | denied
```

生命周期继续使用 `unconfirmed | developing | confirmed | disputed | denied | resolved | stale` 等独立
枚举。可信度等级变化可以触发生命周期转换，但两个字段不能合并。

## 来源角色校验

模型提出 `source_role`，代码按以下上限验证：

- `responsible_official`：当前 Source 为官方、不是纯转发，并且正文是其权责范围内的直接表述。
- `direct_subject`：当事人本人、相关战队、联赛、开发者等直接主体，不要求全局官方。
- `first_party_participant`：事件参与方的一手信息，但不一定负责最终决定。
- `independent_media` / `known_leaker`：必须是当前原创报道或明确独立调查，不是转载链。
- `republisher`：纯转发、搬运或只复述同一上游；证据强度为 0。
- 无法从结构化 Source、URL 或内容角色验证时降为 `unknown`，不能采纳更强角色。

`confirms` 和 `denies` 只有 `responsible_official` 或适用的 `direct_subject` 才能产生权威覆盖；其他
来源的同类措辞按 `supports` 或冲突证据处理。

## independence_group

确定性优先级：

1. RawItem 同源修订链统一为 `raw-origin:{root_raw_item_id}`。
2. 纯转载/引用无新增事实时，使用规范化上游 URL 或上游 external id：`upstream:{key}`。
3. 有原创新增事实时使用 `source:{source_id}`；同一账号在同一事件中始终只算一个独立来源。
4. 多家账号转载同一已知上游，全部落入同一 upstream group。
5. 无法识别上游的转载使用 `null`，可进入消息列表和热度，但不参与独立佐证。

同一事件、同一 group、同一正反方向只取最强一条证据；Source 的重复发布不会重复增加。

## 计算公式

先给每个独立正向 group 计算证据强度（0–100）：

| source_role | strength |
| --- | ---: |
| responsible_official | 100 |
| direct_subject | 85 |
| first_party_participant | 80 |
| independent_media | `35 + 55 × reliability` |
| known_leaker | `25 + 55 × reliability` |
| ordinary_account | `10 + 50 × reliability` |
| republisher | 0 |
| unknown | 10 |

普通路径：

```text
best = max(independent positive strengths, default=0)
corroboration = 8 * min(additional independent positive groups, 3)
conflict_penalty = 15 * min(independent conflicting groups, 2)
score = clamp(best + corroboration - conflict_penalty, 0, 95)
```

覆盖规则优先：

- 负责主体直接 `confirms`：100，`officially_confirmed`。
- 负责主体直接 `denies`：0，`denied`。
- 同时有权威确认与权威否认，或正反双方都有强证据：50，`disputed`。
- 无覆盖时，至少两个独立正向 group 且分数 >= 70：`corroborated`。
- 分数 >= 40：`plausible`；否则 `unverified`。

`corrects` 对被修正旧事实记冲突，对修正后的新事实记支持；结构化 fact changes 保存两者，避免
把“有修正”简单等同于整件事被否认。

## 示例

- 一个可靠爆料账号原创爆料，可靠性 0.8：强度 69，等级 `plausible`。
- 20 个账号纯转载该爆料：仍只有同一 upstream group，可信度不变；热度增加。
- 第二个真正独立媒体（可靠性 0.8）确认：69 + 8 = 77，`corroborated`。
- 负责该版本的 Riot 官方原帖确认：100，`officially_confirmed`，事件生命周期可转 `confirmed`。
- 官方账号只转发爆料：角色是 `republisher`，不能官方确认。
- 负责方明确否认核心事实：0，`denied`；相关证据和原始爆料都保留用于审计。

## 边界情况

- Source 可靠性调整不回写历史证据；证据保存当时快照，显式重评才更新。
- 两个 Source 使用同一通讯社稿或同一截图且无独立事实时应共享 group。
- `mentions`、`context_only` 和 excerpt 不支持核心事实的记录不参与可信度。
- 一个官方综合公告可以分别确认 A/B、首次报告 C；三个 mention 独立判定关系与权责。
- 官方修正局部数值不一定否认整个事件，必须基于 `structured_fact_changes` 重算当前事实。
- denied 是可信度结论，不等于删除事件；事件页面仍展示否认证据和时间线。

## 实现对应、可调参数与未解决问题

实现位于 `domain/event_credibility.py` 和 `services/event_metrics.py`，上游关系解析复用
`services/classification_source.py` 的结构化 URL 能力，聚合服务负责持久化快照和生命周期转换。

可调参数包括角色基准、独立加成、冲突扣分与等级阈值。尚需用真实 fixture 验证“同稿转载”的
fingerprint；v1 不新增 Source 职责表，若 `responsible_official` 误判率偏高，再评估按产品/赛区配置
权责范围。
