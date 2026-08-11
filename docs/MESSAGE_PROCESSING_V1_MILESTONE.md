# 消息处理 v1 里程碑

- 状态：当前稳定基线
- 日期：2026-08-11
- 分支：`codex/architecture-remediation-exploration`

## 版本定位

本节点确认 LeagueNews 已完成从“消息处理后继续生成 Claim、Event 和 Digest”的实验架构，迁移为
以单条消息为边界的稳定处理链路：

```text
Source / Connector
  -> immutable RawItem + source payload + original media
  -> relevance
  -> optional image OCR
  -> translation
  -> message analysis and controlled classification
  -> deterministic importance
  -> NormalizedItem publication
```

RawItem 及其原始证据保持不可变。当前运行时不生成 Claim、事件成员关系、事件聚合或日报；这些能力
只有在重新设计并通过独立审查后才能重新进入主链路。

## 固定版本

| 能力 | 当前版本 |
| --- | --- |
| 消息处理投影 | `message-processing-v1.1` |
| 消息分类 | `message-taxonomy-v3` |
| 重要性与排序 | `importance-v11-repost-weekly-rotation` |
| 内容分析 Prompt | `message-content-analysis / v7-empty-title-content-form` |
| 分类与重要性特征 Prompt | `message-classification-importance / v14-semantic-source-kind` |
| 相关性 Prompt | `relevance / v3-lol-scope` |
| 翻译 Prompt | `translation / v4-optional-source-title` |
| 内容分析 Schema | `MessageContentAnalysisResult:v2` |
| 翻译 Schema | `TranslationResult:v2` |
| 数据库迁移头 | `062_update_message_taxonomy_v3` |

## 已确认的处理边界

- 产品、内容形式、消息类型和主题使用受控目录，官方与非官方信源只能选择各自合法的消息类型。
- 非官方推广互动使用 `game_community_promotion_interaction`，不再越权选择官方推广类型。
- 重要性由 `message_type × topics` 选择确定性评分档案；LLM 只提取有限特征，不直接输出分数。
- `repost` 在档案分确定后扣 8 分，排序阶段不重复扣分。
- 周免英雄仍属于 `game_announcement`，但使用 44–56 分的低关注轮换档案。
- Source 可信度与消息重要性分离；公开消息流和详情页同时展示 topics 与信源可信度。
- `original` 与 `quote` 使用当前账号性质；`repost` 使用可验证上游性质，上游不明时使用三态候选并集。采用依据进入提案、checkpoint 和最终 facets，但不构成事件官方确认。
- 纯媒体/纯链接可在 LLM 阶段保留空标题，发布前由程序确定性补为“仅媒体消息”或“仅链接消息”。
- 本地实验回退不保留 correction 历史，但不得删除或改写 Source、RawItem、source payload 或原始媒体。

## 本节点包含的结构整理

- 删除已经退出运行时的 Claim、Event、Digest、事件工作流、聚合服务、API、页面和对应测试。
- 将过时设计、交接文档和一次性分类审计材料迁入 `docs/history/`。
- 保留并更新当前仍使用的 Connector、OCR、消息分类、重要性、Prompt 治理和运维文档。
- 精简自动管线、人工审核、管理台和测试，使代码结构与当前单消息架构一致。
- 公开消息页改为连续浏览，便于本地审查处理结果。

## 数据库与迁移

迁移 `056` 至 `062` 建立受控消息分类、恢复兼容默认值，并依次引入分类原生重要性、非官方推广
类型、v11 重要性政策及三态分类信源语义对应的 taxonomy v3 默认值。已有迁移仍是不可改写历史；
后续数据库变化必须追加新编号迁移。

本地验收库为 `localhost:5432/lol_daily_intel`。本节点验收时保留 737 个 RawItem 及全部原始证据，
仅重建或更新下游消息处理投影。

## 验收结果

```text
Ruff: passed
Backend: 151 passed, 1 skipped
Web lint: passed
Web production build: passed
Local public feed and message detail visual QA: passed
```

## 后续演进规则

此文档记录 v1 基线，不替代各专项权威文档。分类、重要性或 Prompt 改动仍须同步代码、测试、迁移
和对应专项文档。若未来引入事件层，应建立新的架构里程碑，并将本文整体迁入 `docs/history/`，不得
在本文件中持续叠加已不属于 v1 的设计。
