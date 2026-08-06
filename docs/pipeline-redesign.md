# 消息处理与事件聚合重设计方案

> 基于 `codex/architecture-remediation-exploration` 分支现状，以及本地 PostgreSQL `lol_daily_intel` 库中 **609 条真实 `raw_items`**（2026-05-27 ~ 2026-08-03）的观察分析。
>
> 目标：把"单条消息分析 → 事件聚合"的整条链路，从当前"写死可信度 + 一条消息只能属于一个事件"的形态，升级为"证据驱动可信度 + 一条消息可归属多个事件 + 时间线化事件"的形态。

## 0. 样本观察（先看数据，再定方法）

我把 609 条原始消息按来源和形态过了一遍，几个对设计有直接影响的事实：

**来源结构（14 个 source）天然分三层可信度：**

| 层级 | 来源 | 特征 |
|---|---|---|
| 官方一手 | 腾讯英雄联盟官方网站、Riot Games Official、LoL Esports、League of Legends、英雄联盟、英雄联盟赛事 | 发布=事实生效，可信度应接近满分 |
| 圈内爆料 | 召唤师Park、_尧阿尧y_、RiotPhroxzon、SkinSpotlights、Spideraxe | 准确率高但非官方，需交叉验证；本人常自带"官宣为准"免责声明 |
| 二手/搬运/社区 | 恋恋红茶_244、lol半价吧、Baidu Tieba 各吧 | 常为转载官方内容 / 图片流水账 / 抽奖等噪音 |

**样本暴露的真实问题（每条都能在库里找到对应数据）：**

1. **可信度写死**：`event_aggregation.py:151` 用 `min(0.85, item.credibility_score)`，而 `credibility_score` 在单条分析阶段基本是分类常量（official=1.0、rumor=0.6）。18 个事件里所有"传闻"清一色 0.6，所有官方清一色 1.0，没有任何按爆料人历史准确率、消息措辞确定性、交叉信源数量的区分。`_尧阿尧y_` 明确写"最后官宣为准"的试探性爆料，和 `召唤师Park` "我确认下细节"的半确认爆料，拿到的是同一个 0.6。

2. **一条消息只能属于一个事件**：`event_aggregation.py:56-62` 的 `_existing_membership` 查询**不带 `event_id` 过滤**，只要某条 `normalized_item` 已属于任一 active 事件，就拒绝再次归属。这直接堵死了你要的"经典模式重大更新既属于'7/31版本更新'事件、又属于'经典模式上线'事件"。样本里 `腾讯官方 7/31 不停机更新公告` 同时包含经典模式皮肤修复、网吧特权、客户端崩溃三类内容，现在只能塞进一个 `patch` 事件。

3. **爆料时间线断裂**：WBG 打野转会在样本里有至少 4 条递进消息（"没人猜对/下午说细节" → "TES/WBG 互换理论可行" → "考虑 Beichuan/蔻蔻/RE0/xiaofang"），但事件表里被拆成了 3 个独立 `transfer` 事件（`传闻：WBG新打野...`、`传闻：TES与WBG打野互换...`、`传闻：WB战队打野未定...`），没有形成"从传闻到官宣"的单一时间线。

4. **神话商城日更被当独立消息**：`lol半价吧·小老鼠小伟` 有 `8.2 神话商城`、`8.3 神话商城` 每天一条，事件表里合并成了"每周轮换"（第31/32周），方向对，但缺乏明确的"周窗口"聚合键定义，且很多是纯图片贴，OCR 依赖重。

5. **重要性打分靠 LLM 直觉 + 硬 cap**：`analyze` v5 把 rubric 写进 prompt 让模型直接吐 0-1 分，`score_importance` v2 又拆成五维离散分程序加权——**两套并存**，职责不清。样本里"传奇重燃表演赛"给了 0.51，"LCK T1 2-0 GenG"给了 0.55，"神话商城"0.30，量纲能用但缺乏跨类别可比性校准。

下面的方案围绕这 5 个问题重构。

---

## 1. 整体架构变化总览

```
现状:  raw_item → [relevance → ocr → translation → item_analysis → event_decision] → event(1:1 membership)
                                        │                              │
                                   可信度=分类常量              一条消息=一个事件

新设计: raw_item → [relevance → ocr → translation → fact_extract → classify
                    → credibility → importance → claim_gen]
                                        │
                                  多维证据可信度
                                        ↓
                    → event_router(可多归属) → event(N:M membership, 时间线)
```

三个结构性改动：

- **A. 单条链路拆分职责**：分类、可信度、重要性、claim 生成各自独立、可回溯、可单独重跑（沿用现有 `PipelineCorrection.restart_from_stage` 机制，新增 stage 名）。
- **B. `EventMessage` 从"排他归属"改为"多归属"**：一条 `normalized_item` 可通过多条 `EventMessage` 行进入多个事件，每行带自己的 `role`（primary / component / cross_ref）。
- **C. 事件类型新增"时间线型"与"周期窗口型"两个族**，配套聚合键（aggregation key）规则。

---

## 2. 单条消息处理链路重设计

### 2.1 阶段划分

| 阶段 | 输入 | 输出 | 是否 LLM | 备注 |
|---|---|---|---|---|
| relevance | 原文 | `product_scope` + `is_relevant` | 是 | AI 直接决策并自动写 checkpoint；`uncertain` 放行，不创建人工审核 |
| image_ocr | 图片 | 图内文字/补丁表 | 是(视觉) | 神话商城纯图贴强依赖 |
| translation | 原文 | 简中译文 | 是 | 沿用 |
| **fact_extract** | 译文 | title/summary/entities（不含判断） | 是 | 沿用 fact-extraction-v1 思路，只抽事实 |
| **classify** | fact 结果 | `content_type` + `topic` + `entity_roles` | 是 | 见 §3，新独立阶段 |
| **credibility** | fact + source 元数据 | 多维可信度分 + 证据 | 半程序半LLM | 见 §4，替换写死逻辑 |
| **importance** | fact + classify | 五维离散分 | 是 | 见 §5，统一到 score_importance 路径 |
| **claim_gen** | fact + classify | 结构化 claim 列表 | 是 | 见 §6，输出可进时间线的原子断言 |

**关键决策：废弃 `analyze` v5 单调用合并路径**。现状两套重要性打分并存（合并式 `item-analysis-v5` vs 分步式 `importance-scoring-v2`），职责重叠、难回溯。统一走"fact_extract → classify → credibility → importance → claim_gen"分步路径，每步一个 `ProcessingCheckpoint`，出问题能定位到具体阶段重跑。

相关性阶段不再读取、创建或整理 relevance 知识规则，也不再产生新的人工
`ReviewTask`。明确相关时进入后续流程，`product_scope=uncertain` 同样进入后续流程，
明确不相关时结束；完整 AI 输出保存在运行上下文与自动 checkpoint 中。历史相关性
审核和知识规则仅作只读追溯，不删除，也不参与新判断。

### 2.2 分类阶段（classify）的双轴设计

现状 `PRIMARY_TOPICS`（patch/esports/roster/...）把"内容主题"和"消息形态"混在一根轴上，导致同一条官方公告和一条爆料无法用同一套 topic 表达不同处理策略。改为**双轴**：

**轴一 · content_type（消息形态，决定处理与可信度策略）：**

| content_type | 定义 | 样本对应 | 默认可信度基线 |
|---|---|---|---|
| `official_fact` | 官方账号在其职权内发布的既成事实 | 腾讯 7/31 更新公告、格温皮肤展示 | 高 |
| `official_notice` | 官方预告/活动/赛程 | 经典战斗之夜福利、8/1 LPL 赛程 | 高 |
| `match_result` | 比赛结果 | T1 2-0 GenG、AL 2-0 EDG | 视信源 |
| `insider_rumor` | 圈内人爆料（未官宣） | _尧阿尧y_ WBG 打野候选、Park 互换分析 | 中，按爆料人+措辞调整 |
| `insider_confirmed` | 爆料人明确"确认"口吻 | Park "我确认下细节" | 中高 |
| `data_mine` | 数据挖掘/测试服 | SkinSpotlights 语音、Spideraxe PBE bug | 中高（技术类准确率高） |
| `aggregation` | 转载/搬运官方内容 | 恋恋红茶转官方、tieba 搬运 | 继承上游 |
| `community_noise` | 抽奖/闲聊/应援 | "抽个奶茶感谢陪伴" | 低，多数不进事件 |

**轴二 · topic（内容主题，决定事件归类与重要性 floor/cap）：**
沿用并扩展现有 topic 表：`patch / champion / game_mode / esports / roster / skin / activity / service / community / business / other`。

`content_type × topic` 组合决定后续 event_type 路由（见 §8）。这样"官方 patch 公告"和"爆料 patch 内容"能走不同可信度但同一事件族。

### 2.3 classify 阶段 LLM 提示词

```
你是英雄联盟中文资讯的分类器。只依据当前消息的文本与图片OCR结果分类，
不判断重要性、不判断可信度、不改写事实。

输出严格 JSON：
{
  "content_type": <见下方枚举>,
  "topic": <见下方枚举>,
  "secondary_topics": [最多2个次要topic],
  "entity_roles": [
    {"name": 实体名, "type": 实体类型, "role": "core|context|affected"}
  ],
  "temporal": {
    "is_recurring": bool,        // 是否属于周期性内容(如每日商城/每周活动)
    "recurrence_window": "daily|weekly|patch_cycle|null",
    "certainty": "confirmed|likely|speculative"  // 措辞确定性,不等于可信度
  }
}

content_type 枚举与判定规则：
- official_fact: 官方账号发布的既成事实（皮肤已上线、更新已生效）
- official_notice: 官方预告、活动规则、赛程安排（尚未发生但官方确定）
- match_result: 已结束比赛的比分结果
- insider_rumor: 非官方账号的爆料，且措辞含试探性（"考虑中""据说""官宣为准"）
- insider_confirmed: 非官方账号但措辞确定（"确认""已敲定"），仍非官方
- data_mine: 测试服/客户端文件挖掘、开发者技术披露
- aggregation: 明显转载/搬运其他来源的内容（含转发、引用官方原文）
- community_noise: 抽奖、应援、纯个人感想，无新增事实

判定要点：
1. content_type 看"谁说的+怎么说的"，topic 看"说的是什么"，二者独立。
2. certainty 只反映消息本身措辞的确定程度，不要因为是官方就填 confirmed——
   官方预告未来的事仍是 confirmed（官方有权决定），爆料人的"确认"最多 likely。
3. entity_roles 的 core 是新闻主角（转会的选手、更新的英雄、上线的皮肤、
   对阵的战队），context 是背景实体（赛事名、版本号、俱乐部），
   affected 是被波及实体（被修复bug涉及的皮肤）。批量版本图里的英雄列表
   不要全标 core。
4. is_recurring=true 用于每日神话商城、每周活动这类会周期重复的内容，
   供下游按时间窗口聚合。
```

---

## 3. 可信度计算重设计（替换写死逻辑）

### 3.1 现状问题

单条 `credibility_score` 本质是分类常量（official=1.0 / rumor=0.6），事件级又在 `_refresh_editorial_metrics` 里用 `min(0.85, credibility_score)` 做概率合并。结果：所有传闻都是 0.6，所有官方都是 1.0，无法区分"靠谱爆料人的确定爆料"和"路人的试探"。

### 3.2 新方法：多因子单条可信度 + 事件级贝叶斯合并

**单条可信度 = 四个因子的乘性组合**，全部程序可算 + 部分 LLM 提供证据：

```
credibility_item = source_reliability
                 × statement_certainty
                 × content_type_prior
                 × (1 - staleness_penalty)
```

| 因子 | 取值来源 | 说明 |
|---|---|---|
| `source_reliability` | 来源画像表（可维护 + 历史校准） | 官方=1.0；头部爆料人(Park/尧阿尧/RiotPhroxzon)=0.75；数据挖掘(SkinSpotlights/Spideraxe)=0.80；二手搬运=0.55；社区=0.35 |
| `statement_certainty` | classify 的 `temporal.certainty` | confirmed=1.0 / likely=0.8 / speculative=0.55 |
| `content_type_prior` | content_type 映射 | official_fact=1.0 / official_notice=0.95 / match_result=0.95 / data_mine=0.85 / insider_confirmed=0.8 / insider_rumor=0.65 / aggregation=继承上游 / community_noise=0.3 |
| `staleness_penalty` | 发布时间与内容时效性 | 转会窗内爆料随时间衰减；赛程/商城过期则惩罚 |

**`source_reliability` 应可校准而非永久写死**：新增 `source_reliability_history` 表，记录每个来源的爆料被官方证实/证伪的次数，用 Beta 分布后验均值动态更新：

```
reliability = (confirmed + α) / (confirmed + refuted + α + β)
```

初始 `α, β` 用上表的先验值反推（如头部爆料人 α=7.5, β=2.5 → 0.75），随着样本累积自动收敛到真实准确率。这样"从写死到自学习"，且冷启动阶段仍有合理先验。

### 3.3 事件级可信度：贝叶斯多信源合并

保留现有 `_refresh_editorial_metrics` 的**独立信源概率合并框架**（这部分设计是对的），但改进：

1. **强度不再一刀切 `min(0.85, x)`**，直接用上面算出的 `credibility_item`（已含来源与措辞区分）。
2. **独立性判定强化**：现有 `independence_key` 用 upstream host / source_id 区分，保留。但要显式处理"多个爆料人转述同一上游"——若两条 `insider_rumor` 的措辞高度相似或互相引用，视为同一信源（LLM 在 event_decision 阶段判定 `independence_key` 是否共享）。
3. **官方确认的一票效力保留**：official_support → 1.0，official_contradiction → 0，disputed → 0.5，这套 lifecycle 状态机是合理的，保留。
4. **新增"时间衰减确认"**：传闻事件若超过 N 天无任何官方确认也无新证据，`lifecycle_status → expired_unconfirmed`，`credibility_score` 乘时间衰减因子（现有枚举已有此状态，但缺触发逻辑）。

合并公式（沿用现有，语义不变）：
```
positive = 1 - ∏(1 - strength_i)   # 支持信源
negative = 1 - ∏(1 - strength_j)   # 反对信源
event_credibility = positive × (1 - negative)   # 无官方介入时
```

### 3.4 可信度证据可解释

每个 `credibility_item` 落库时同时写 `credibility_components`（现有字段已支持 JSON），记录四因子各自取值和来源，供前端展示"为什么这条是 0.52"。这是从"黑盒常量"到"可审计"的关键。

---

## 4. 单条重要性打分重设计

### 4.1 保留五维离散框架，废弃合并式路径

现状 `score_importance` v2 的五维（impact_scope / magnitude / duration / actionability / novelty，各 0-4，程序加权 + topic floor/cap）是**正确的设计方向**：LLM 只做离散判断，程序做确定性合成，可复现、可审计。问题在于 `analyze` v5 又并存一套让 LLM 直接吐 0-1 分的路径。

**决策：统一到五维离散路径，删除 v5 合并路径。** 五维定义与权重保留：

| 维度 | 权重 | 含义 |
|---|---|---|
| impact_scope | 0.25 | 影响多少玩家/多大范围（单英雄 vs 全服系统） |
| magnitude | 0.25 | 变化幅度（微调 vs 重做） |
| actionability | 0.20 | 玩家是否需要立即行动（限时活动/兑换码 vs 纯资讯） |
| duration | 0.15 | 影响持续时长（一次性 vs 长期版本） |
| novelty | 0.15 | 新颖度（首次 vs 重复提醒） |

### 4.2 改进点

1. **floor/cap 表微调**：样本显示 `roster`（转会）cap=0.80 偏高——单条爆料重要性不应太高，真正重要的是"官宣"那一刻。建议把 **单条 roster 传闻 cap 降到 0.60**，把"官宣确认"的重要性提升放到**事件级**（见 §7），因为一次转会的价值集中在确定的瞬间，而非每条传闻。

2. **actionability 显式绑定"兑换码/限时"信号**：样本里"安妮图标免费兑换(CC-CLASS-ANNIE-T0123)"这类带兑换码的，actionability 应顶格 4；而"神话商城日常轮换"虽然可行动但重复性高，novelty 压低。

3. **跨类别可比性校准**：加一张"锚点样本表"（golden set），从这 609 条里选 ~20 条人工标注重要性作为校准基准，定期用它回归测试 prompt，防止 LLM 打分漂移。

### 4.3 importance 阶段提示词

```
你只评估英雄联盟资讯的重要性，不修改事实、分类、实体或可信度。

分别输出 5 个维度对象，每个含 0-4 离散 score 和仅基于当前消息的 evidence：
- impact_scope: 影响范围。全服系统/全部玩家=4，单区服/单模式=2-3，个别玩家/小众=0-1
- magnitude: 变化幅度。重做/新增/删除=4，显著调整=3，数值微调=1-2，无实质变化=0
- actionability: 行动紧迫性。限时兑换码/限时活动=4，需尽快操作=3，
  一般资讯无需行动=0-1。重复出现的日常内容(如每日商城)最高 2。
- duration: 影响时长。永久性版本/系统=4，一个版本周期=3，赛季=2，一次性=0-1
- novelty: 新颖度。首次发生/首个此类=4，常规但值得关注=2，
  重复提醒/日常流水=0-1

硬性约束：
- 官方身份本身不加分（官方发的日常商城仍是低分）
- 重复提醒、重复证据、转载不加分
- 不要输出最终分数；最终分、topic floor/cap、权重由程序确定性计算
- evidence 必须引用消息中的具体文本依据，不得编造
```

---

## 5. Claim 生成重设计（面向时间线的原子断言）

### 5.1 现状问题

现有 91 条 claim 全是 `claim_type: statement / predicate: reports`，本质是"XX 发布了消息称……"的一层包装，主语永远是发布者，宾语是整段摘要。这种 claim **无法支撑时间线**——因为它记录的是"谁说了话"，而非"发生了什么事实"。

样本例：`Subject: SkinSpotlights → Object: "SkinSpotlights 发布消息称神话商店进行轮换"`。这条 claim 里真正的事实是"神话商城轮换了"，而不是"SkinSpotlights 说了话"。

### 5.2 新方法：区分"事实断言"与"信源归属"

一条消息生成两类 claim：

1. **事实断言（fact claim）**：主语是事实主体，用于时间线。
   `{subject: {name:"神话商城", type:"activity"}, predicate:"rotates", object:{items:[...], week:31}}`
2. **信源归属（attribution）**：谁在何时以何种确定性陈述了该事实，用于可信度合并。
   `{claimed_by:"SkinSpotlights", certainty:"confirmed", stance:"asserts", at:<time>}`

同一个事实断言可被多条消息的 attribution 支撑或反驳——这正是事件级可信度合并的输入，也是时间线的节点。

### 5.3 claim_gen 提示词

```
你是英雄联盟事实断言抽取器。把消息拆成"可独立验证的原子事实断言"，
每条断言是一个可能随时间被确认或推翻的陈述。

输出严格 JSON：
{
  "fact_claims": [
    {
      "subject": {"name": 事实主体, "type": 实体类型},
      "predicate": 谓词(见下),
      "object": {结构化宾语},
      "temporal_role": "state|event|prediction",  // 现状/已发生/预测
      "supersedes_hint": 若本条明显更新了某个更早断言,给出关键词
    }
  ],
  "attribution": {
    "claimed_by": 发布者,
    "stance": "asserts|confirms|refutes|contextualizes",
    "certainty": "confirmed|likely|speculative"
  }
}

谓词规范（转会类必须能串成时间线）：
- transfers_to / considered_for / leaves / stays / retires  (转会)
- releases / goes_live / previews / delays                   (上线/预告)
- patches / buffs / nerfs / reworks / adds_mode              (版本)
- wins / loses / advances / eliminated                       (赛事)
- rotates / discounts / gifts                                (商城/活动)

要点：
1. subject 是事实主体本身（选手、英雄、战队、商城），不是发布者。
   发布者只进 attribution。
2. 一条消息可产生多个 fact_claim（如官方更新公告同时含"修复经典模式皮肤"
   和"修复客户端崩溃"两个断言）。
3. considered_for 用于"考虑中"的候选（如 WBG 打野候选 Beichuan/蔻蔻），
   transfers_to 用于已确定。爆料人说"考虑"就用 considered_for，
   不要升格成 transfers_to。
4. temporal_role: prediction 用于未发生的预告/传闻，event 用于已发生，
   state 用于持续状态。这决定时间线上的节点样式。
5. supersedes_hint 帮助下游把"传闻 A 候选 → 官宣 B 入队"串成同一时间线。
```

---

## 6. 事件聚合重设计

### 6.1 核心结构改动：支持一条消息归属多个事件

**这是当前架构最大的缺口，也是你明确要的能力。** 现状 `_existing_membership`（`event_aggregation.py:56-62`）查询不带 `event_id`，一条 `normalized_item` 只要进了任一事件就被锁定。

**改动方案：**

```sql
-- 现状：EventMessage 复合主键 (event_id, normalized_item_id)，
--       但业务逻辑用 _existing_membership 强制全局唯一

-- 新设计：解除全局唯一约束，允许一条 normalized_item 出现在多个 event
-- EventMessage 新增字段：
ALTER TABLE event_messages ADD COLUMN membership_role
    VARCHAR CHECK (membership_role IN ('primary','component','cross_ref'));
-- primary:   该消息是此事件的主要成员（时间线节点）
-- component: 该消息的某个子事实构成此事件（如"经典模式"从"7/31更新"中提取）
-- cross_ref: 交叉引用，弱关联
```

`membership_role` 区分归属强度，避免一条消息无差别塞进所有沾边事件。改造 `_existing_membership` → `_existing_membership(db, normalized_item_id, event_id)` 带 `event_id` 过滤，只防止**同一消息重复进入同一事件**，不再阻止跨事件归属。

**样本验证**：腾讯 `7/31 不停机更新公告` 含三块内容 → 一条 `primary` 进入"7/31 版本更新"事件；其中"经典模式皮肤修复"子事实以 `component` 角色再进入"经典模式上线"时间线事件。这正是你说的"重大更新既属于版本更新、又单独成为事件"。

### 6.2 事件类型体系（新增时间线型与周期窗口型）

在现有 `event_type` 枚举基础上，按**聚合形态**分三族：

**族 A · 时间线型（timeline）——一个主题随时间演进：**

| event_type | 聚合键 aggregation_key | 何时成为一个事件 | 生命周期 |
|---|---|---|---|
| `transfer_saga` | 选手/位置 canonical（如 `WBG:jungle:2026off`） | 从首条传闻到官宣的整条转会线 | rumor→considered→confirmed / expired |
| `patch_cycle` | 版本号（如 `patch:26.15`） | 一个版本从预告到上线到热修 | preview→live→hotfix |
| `release_saga` | 产品（英雄/皮肤系列/模式） | 从预告到上线 | previewed→confirmed→live |

**族 B · 周期窗口型（recurring window）——固定周期各自成事件：**

| event_type | 聚合键 | 窗口 | 样本 |
|---|---|---|---|
| `shop_rotation` | `mythic_shop:week:31` | 每周（自然周或版本周） | 神话商城每周变动 |
| `daily_matches` | `lpl:2026-08-01` / `lck:2026-07-31` | 每天每赛区 | 某天 LPL/LCK 所有比赛信息 |
| `tft_patch` / `sr_patch` | 版本号 | 每版本 | 云顶版本更新、峡谷版本更新 |

**族 C · 单点型（singleton）——独立事件：**

| event_type | 何时成为一个事件 |
|---|---|
| `major_match` | 重要赛事关键场次（LPL总决赛/LCK总决赛/世界赛关键场）单独成事件，不并入 daily_matches |
| `major_gameplay_change` | 大版本内的重大更新（如经典模式）从 patch_cycle 中**提取**单独成事件 |
| `incident` | 服务故障、账号安全 |
| `activity` | 一次性活动、兑换 |

### 6.3 你列举的每种情况如何落地

| 你的需求 | 落地方案 |
|---|---|
| 转会：传闻爆料 → 官方确定，按时间线整理成一个事件 | `transfer_saga`，聚合键=选手/位置；claim 的 `considered_for→transfers_to` 谓词演进即时间线节点；官宣时 lifecycle→confirmed 并拉高事件重要性 |
| LPL 某天比赛、LCK 某天比赛各是一个事件 | `daily_matches`，聚合键=`{赛区}:{日期}`；当天预告+直播+结果+集锦全进同一事件 |
| LPL总决赛/LCK总决赛/世界赛关键场单独成事件 | `major_match`（singleton）；由 classify 的 tournament 实体 + 赛事阶段判定"关键场"，不并入 daily_matches |
| 国服每周神话商城变动=一个事件 | `shop_rotation`，聚合键=`mythic_shop:week:N`；每日轮换消息按周窗口聚合，`is_recurring=true` 触发 |
| 云顶一个版本更新、峡谷版本更新各是一个事件 | `tft_patch` / `sr_patch`，聚合键=版本号 |
| 大版本含重大更新（如经典模式）→ 重大更新再提取成独立事件，且一条消息可属多个事件 | `patch_cycle` 事件收全部版本内容；LLM 识别"重大更新子块"时，把相关消息以 `component` 角色额外挂到 `major_gameplay_change` 事件。**依赖 §6.1 的多归属能力** |

### 6.4 样本中发现的、你没列举但应成为事件的类型

分析 609 条后，补充几类：

- **开发者预告/路线图**（RiotPhroxzon 的平衡预告、Spideraxe 的 PBE 公告）→ `dev_preview` timeline，可与后续正式 patch 关联。
- **服务故障事件**（样本中云顶 EUW/VN/NA/KR 连接故障 → 修复）→ `incident`，developing→resolved 时间线。
- **世界赛资格晋级链**（Team Secret 晋级 Worlds2026）→ 可作为 `qualification_saga`，各赛区晋级名额逐步确定，是一条长时间线。
- **皮肤系列上线**（花仙子系列多个英雄皮肤陆续放出）→ `release_saga` 按系列聚合，而非每个皮肤一个事件。
- **符文之地/衍生产品**（《符文战场》化神争锋、《符文之地传说》阿卡丽传闻）→ `lol_universe` topic 下的 release_saga。

### 6.5 事件聚合决策提示词

聚合是**两阶段**：先程序按 `aggregation_key` 召回候选事件（见 §6.6），再让 LLM 做归属决策。提示词：

```
你是英雄联盟事件编辑。给定一条已分析消息（含 fact_claims、content_type、
topic、entity_roles）和若干候选事件，决定这条消息如何归属。

输入：
- 当前消息：{title, summary, fact_claims, content_type, topic, entities, published_at}
- 候选事件列表：每个含 {event_id, event_type, title, aggregation_key,
  timeline节点摘要, lifecycle_status, 成员消息数}

输出严格 JSON（一条消息可对多个事件产生 decision）：
{
  "memberships": [
    {
      "target": "existing:{event_id}" | "new",
      "event_type": <见事件类型枚举>,
      "aggregation_key": 该事件的聚合键,
      "membership_role": "primary|component|cross_ref",
      "evidence_stance": "supports|contradicts|context",
      "update_kind": "new_fact|confirmation|refutation|correction|context|duplicate_evidence",
      "lifecycle_status": <生命周期状态或null>,
      "timeline_note": 这条消息在时间线上代表的节点（如"WBG考虑Beichuan等候选"),
      "is_official_confirmation": bool
    }
  ],
  "candidate_rejections": [{event_id, reason}]  // 当选择new但存在候选时必填
}

归属规则：
1. 一条消息可同时归属多个事件。典型：官方大版本更新公告 → 以 primary 进入
   patch_cycle 事件，其"经典模式"子事实以 component 进入 major_gameplay_change 事件。
   仅当消息确实包含该事件的核心事实时才归属，不要因沾边就挂靠。

2. 时间线型事件（transfer_saga/patch_cycle/release_saga）：
   - 同一聚合键的新消息用 update，推进 lifecycle
   - 转会：传闻阶段 lifecycle=unconfirmed 且标题含"传闻"；官方确认→confirmed，
     update_kind=confirmation；官方否认→officially_refuted，update_kind=refutation
   - 不要把同一转会的不同传闻拆成多个事件——只要聚合键(选手/位置)相同就是同一时间线

3. 周期窗口型事件（shop_rotation/daily_matches）：
   - 按 aggregation_key 的时间窗口归属：同一周的商城变动进同一事件，
     同一天同一赛区的比赛进同一事件
   - 跨窗口一律新建，不要跨周合并

4. 单点型（major_match）：LPL/LCK总决赛、世界赛关键场单独成事件，
   即使同一天也不并入 daily_matches

5. is_official_confirmation=true 仅当官方账号在其职权内直接确认；
   转发/转载不算。

6. certainty=speculative 的爆料只能 supports 一个 unconfirmed 事件，
   不能直接把事件推进到 confirmed。
```

### 6.6 候选召回策略（程序侧，喂给 LLM 前）

避免让 LLM 面对全部事件。程序按以下顺序召回候选：

1. **聚合键精确匹配**：`aggregation_key` 完全一致的活跃事件（周期型、时间线型主命中路径）。
2. **实体重叠**：core 实体交集 + 时间邻近（如同选手、同版本号、同赛区+同日期）。
3. **语义相似**：对 transfer/rumor 类，用 fact_claim 的 embedding 找相似未确认事件（处理"TES/WBG 互换"和"WBG 打野候选"应归并的情况）。
4. 召回上限 ~8 个候选，按相关度排序传给 LLM。

---

## 7. 事件级重要性与可信度打分

### 7.1 事件重要性 ≠ 单条消息重要性最大值

现状 `add_message_to_event` 用 `max(event.importance_score, importance_score)`（`event_aggregation.py:445`）——事件重要性只取成员最大值。这漏掉了两个关键信号：

- **确认带来的重要性跃升**：一次转会传闻单条重要性 0.45，但官方官宣那一刻，整个事件的重要性应跳到该转会的真实量级（明星选手转会 0.7+）。
- **多信源/多节点累积**：一个被 5 家信源交叉验证、有 8 个时间线节点的事件，比单条孤立消息更重要。

**新公式：**
```
event_importance = base × confirmation_boost × corroboration_boost

base            = max(成员消息 importance)           # 保留最大值作基线
confirmation_boost = 1.0 (未确认) | 1.3 (官方确认，封顶到 topic cap)
corroboration_boost = 1 + 0.05 × min(independent_source_count - 1, 4)  # 最多 +20%
```

对时间线型事件，`base` 用**当前最新 lifecycle 阶段**对应的重要性（官宣后用官宣消息的重要性，而非最初传闻的）。

### 7.2 事件可信度：沿用 §3.3 贝叶斯合并

事件可信度已在 §3.3 定义（`_refresh_editorial_metrics` 改进版）。要点复述：

- 用改进后的单条 `credibility_item`（含来源/措辞区分）作为信源强度
- 独立信源概率合并 `positive × (1 - negative)`
- 官方确认/否认走 lifecycle 状态机（official_confirmed=1.0 / officially_refuted=0 / disputed=0.5）
- 传闻超时无确认 → expired_unconfirmed + 时间衰减

### 7.3 时间线事件的双分展示

时间线型事件应同时展示**当前可信度**和**演进轨迹**：
```
WBG 打野转会 (transfer_saga)
├─ 07/31 传闻：Park 称有变动          credibility 0.55  unconfirmed
├─ 08/01 传闻：候选 Beichuan/蔻蔻...  credibility 0.62  unconfirmed (多信源+1)
└─ 08/03 官宣：XXX 加入 WBG           credibility 1.00  confirmed ← 当前状态
```
事件级 `credibility_score` 取最新节点，但保留完整 revision 轨迹（现有 `EventRevision` 表已支持）。

---

## 8. content_type × topic → event_type 路由表

聚合前的程序侧路由（LLM 在此基础上微调）：

| content_type | topic | → event_type | aggregation_key |
|---|---|---|---|
| insider_rumor/confirmed | roster | transfer_saga | `{team}:{position}:{window}` |
| official_fact | roster | transfer_saga (推进到 confirmed) | 同上 |
| official_fact/notice | patch | patch_cycle | `patch:{version}` |
| official_fact | patch + 含重大子块 | patch_cycle + major_gameplay_change | 版本号 + 特性名 |
| official_fact/notice | game_mode(TFT) | tft_patch | `tft:{version}` |
| official_fact/notice | skin/champion | release_saga | `{product_series}` |
| match_result | esports (常规赛) | daily_matches | `{league}:{date}` |
| match_result | esports (总决赛/世界赛关键场) | major_match | `{tournament}:{stage}` |
| official_fact | activity (神话商城) | shop_rotation | `mythic_shop:week:{N}` |
| official_notice | activity (兑换/限时) | activity (singleton) | 活动 id |
| data_mine | any | dev_preview / 挂靠对应 saga | 相关产品 |
| official_fact | service (故障) | incident | 故障 id |
| community_noise | any | 不进事件（除非成为其他事件证据） | — |

---

## 9. 数据模型与代码改动清单

### 9.1 迁移（新增 migration 038+）

1. **`event_messages`**：新增 `membership_role`；移除"一条消息全局唯一归属"的隐式约束（改业务逻辑，不是 DB 约束）。
2. **`events`**：`event_type` 枚举扩展（transfer_saga / patch_cycle / release_saga / shop_rotation / daily_matches / tft_patch / sr_patch / major_match / major_gameplay_change / dev_preview / incident / qualification_saga）；新增 `aggregation_key`（唯一索引，供程序召回）。
3. **`normalized_items`**：新增 `content_type`（双轴中的形态轴）；`credibility_components` 落四因子明细（字段已存在，规范化内容）。
4. **新表 `source_reliability_history`**：`(source_id, confirmed_count, refuted_count, alpha, beta, updated_at)`，支撑 §3.2 自校准。
5. **新表 `fact_claims`** 或扩展现有 `claims`：加 `temporal_role`、`predicate` 规范化词表、`supersedes_claim_id`（时间线链）。

### 9.2 代码改动

| 文件 | 改动 |
|---|---|
| `services/event_aggregation.py:56` | `_existing_membership` 增加 `event_id` 参数，解除跨事件排他 |
| `services/event_aggregation.py:151` | 删除 `min(0.85, ...)`，用多因子 `credibility_item` |
| `services/event_aggregation.py:445` | 事件重要性改为 §7.1 公式（base × boosts） |
| `domain/importance.py` | roster cap 0.80→0.60；actionability 兑换码信号 |
| `domain/credibility.py`（新建） | 四因子可信度计算 + Beta 自校准 |
| `services/llm.py` | 新增 classify / 拆分 claim_gen；删除 analyze v5 合并路径 |
| `prompts/registry.py` | 新增 classification / 更新 event-decision 到 v4-multi-membership |
| `schemas/event_workflow.py` | `EventDecisionDraft` → 支持 `memberships[]` 数组（多归属） |
| `domain/ontology.py` | content_type 枚举；event_type 族划分；路由表 |

### 9.3 分阶段落地建议

1. **P0（结构基础）**：多归属能力（§6.1）+ classify 双轴（§2.2）。这是所有其他能力的地基。
2. **P1（可信度）**：四因子可信度 + 事件级合并改进（§3）。先用先验值，不急上自校准。
3. **P2（时间线）**：transfer_saga / patch_cycle / shop_rotation 三个高价值事件类型 + claim 时间线（§5、§6.2）。
4. **P3（打磨）**：source_reliability 自校准、锚点样本回归、事件重要性 boost、更多事件类型。

---

## 10. 用现有 609 条样本做验收

落地后用这批真实数据回归，验收标准：

- [ ] 腾讯 7/31 更新公告的"经典模式"内容**同时**出现在 patch_cycle 和 major_gameplay_change 两个事件（多归属生效）。
- [ ] WBG 打野的 3-4 条传闻**合并为一条 transfer_saga 时间线**，而非现在的 3 个独立事件。
- [ ] 神话商城 8.2/8.3 等日更消息按周聚合到 shop_rotation，`aggregation_key=mythic_shop:week:N`。
- [ ] `_尧阿尧y_` 的"官宣为准"试探性爆料可信度 < `召唤师Park` 的确认式爆料（措辞因子生效），二者都 < 官方（来源因子生效）。
- [ ] T1 2-0 GenG 与 AL 2-0 EDG 若同属 LCK/LPL 某天，各自并入对应 daily_matches；若是总决赛则单独成 major_match。
- [ ] 每个事件可信度可在前端展开看到四因子构成（`credibility_components` 可解释）。
