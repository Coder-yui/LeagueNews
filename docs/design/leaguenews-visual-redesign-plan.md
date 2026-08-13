# LeagueNews Visual Redesign Plan

> 文档状态：Phase 2 设计方案
>
> 研究与审查日期：2026-08-13
>
> 设计输入：当前 `apps/web` 前端、公开 API 展示契约、事件/日报产品文档，以及 [`universe-visual-study.md`](universe-visual-study.md)
>
> 本文边界：定义视觉、信息架构、交互与实施顺序；本阶段不修改 UI、不改变消息/事件/日报业务规则、不重构后端或数据库

## 1. Executive Summary

LeagueNews 不应成为一个 Universe Clone，也不应继续停留在“浅色技术编辑站 + 数据处理演示”的状态。建议建立独立方向：

> **The Living Record / 活态纪事**  
> 一个持续更新、可追溯、由消息累积成事件、再由日报完成每日策展的 League of Legends 资讯记录。

这套方向从 Universe 学习的是机制，而不是表面资产：

1. 用环境、空间和排版先建立内容语境，再交付信息。
2. 用编辑尺度区分重点内容与普通内容，不靠满屏 Badge。
3. 用低对比线框、暖白文字和稀缺金属色建立“档案感”。
4. 让图片与页面暗部融合，但不让缺图内容失去层级。
5. 让内容之间持续存在可理解的下一步关系。

LeagueNews 必须同时坚持以下产品原则：

1. **Information First**：标题、当前事实、时间、来源和必要指标必须先于气氛。
2. **三种实体、三种视觉语法**：Message 是一次发布，Event 是持续变化的当前结论，Daily Report 是固定日期的精选目录。
3. **语义真实**：最新不能伪装成精选，全量不能标成今日，不能把算法没有生成的摘要、趋势或编辑结论用视觉“演”出来。
4. **可追溯浏览**：筛选状态进入 URL；事件进入消息后能回到原事件；详情页不能成为阅读死端。
5. **公共阅读端与管理台分工**：公共端采用克制的深色编辑体验；管理台保持高密度、长时间可操作的浅色 Operator 模式。
6. **每个增量阶段都可运行**：先统一基础和导航，再逐类替换内容组件，避免一次性推倒重来。

本次审查还确认：前端目前不只是视觉不统一，也存在资讯网站逻辑缺口。公共导航在不同页面不一致，移动端导航被直接隐藏；主页同时承担首页和消息列表，却把全量统计写成“今日概览”；Lead Story 实际只是最新一条；公共列表没有搜索、排序、分页；日报有日期参数却没有日期浏览；事件详情已有相关消息数据但没有展示；消息详情无法保留来自事件或日报的上下文。这些应作为视觉升级的一部分修正，但不能借机更改业务算法。

## 2. Product & Frontend Audit

### 2.1 当前实际路由

#### 公共阅读端

| 路由 | 当前职责 | 主要实现 |
| --- | --- | --- |
| `/` | 品牌首页、全量/精选消息流、最新一条 Lead、处理链和来源数量说明 | `apps/web/app/page.tsx` |
| `/messages/[id]` | 已发布消息详情、译文/原文切换、媒体和结构化图片内容 | `apps/web/app/messages/[id]/page.tsx`、`components/message-detail.tsx` |
| `/events` | 事件分类列表 | `apps/web/app/events/page.tsx` |
| `/events/[id]` | 事件当前状态、指标、关键事实、时间线和证据 | `apps/web/app/events/[id]/page.tsx` |
| `/daily` | 指定上海自然日的 LoL PC / Esports / TFT / 其他精选列表 | `apps/web/app/daily/page.tsx` |

项目文档中曾出现 `/reports/{date}` 的架构描述，但当前实际公共页面是 `/daily?date=YYYY-MM-DD`。后续设计与实现应以当前路由为准，并同步修正文档漂移，而不是额外假设一个不存在的页面。

#### 管理台

| 路由 | 当前职责 |
| --- | --- |
| `/admin` | 重定向到 `/admin/pipeline` |
| `/admin/pipeline` | 流水线状态、筛选、展开详情、处理/重试/审核入口 |
| `/admin/messages` | 已发布消息管理、列表/审核对照视图、筛选与分页 |
| `/admin/messages/[id]` | 单条消息原文、译文、重要性依据和重跑操作 |
| `/admin/reviews` | 待审队列与消息/OCR 审核 Dialog |
| `/admin/collection` | Source、采集计划、运行日志 |
| `/admin/system` | 失败任务、Corrections 和恢复操作 |
| `/admin/system/ocr` | OCR 参数测试、图像对照、历史结果与生产配置 |
| `/admin/system/knowledge` | 规则与术语维护 |

管理台已具有独立的固定侧栏、移动抽屉、表格、筛选、分页、状态条和 Dialog。它是 Operator 产品，不应与公共站采用同一沉浸强度。

### 2.2 核心内容实体与显示语义

#### Message

前端可获得并正在部分使用的信息包括：

- 标题、摘要、原文/译文内容块；
- `products`、`message_type`、`topics`、`entities`；
- `content_form`，当前映射为原创、转发、引用、媒体、链接等形式；
- `importance_score` 与内部维度；
- `priority_score`；
- Source 名称、可靠性分数、原始链接、作者；
- 发布时间、采集/创建时间；
- 媒体与审核后的结构化图片提取。

当前公共列表没有展示 `products`，却会展示所有 topics 和 entities；这使产品归属不够清晰，而低优先级元数据容易膨胀。详情页也没有显示产品归属与实体关系。

#### Event

Event 不是 Message 的大卡版本。它拥有：

- 当前标题、当前摘要、最新实质进展；
- 产品、事件族、一级分类、生命周期；
- 相互独立的重要性、可信度、热度；
- 最后实质更新时间；
- 消息数、来源数、24 小时覆盖；
- 主要来源、最佳媒体；
- 关键事实、时间线、mention 级证据、去重后的相关消息。

当前列表复用 `.message-card`，导致 Event 与 Message 视觉同构；同时漏掉最后实质更新时间。详情页没有使用最佳媒体，也没有展示已经返回的 `related_messages`。这削弱了“持续演进的事件”与“单次发布的消息”之间的区别。

#### Daily Report

日报是现有已发布消息在固定上海自然日内，按既有原创、重要性、事件去重和栏目上限规则生成的精选目录。它不是 AI 写作的日报文章，也不包含趋势分析或新摘要。

当前页面仅把四组 `MessageFeed` 纵向拼接。日期可以通过查询参数传入，但没有日期控件；空状态要求用户调用生成接口，暴露了内部操作逻辑；四个栏目没有形成 Digest 的阅读节奏。

#### Source 与信任信息

Source 是具体账号或站点，可靠性是 Source 属性，不等于某个事件得到确认。Event credibility 是事件证据投影，不能与 Source reliability 合并。视觉上必须保持：

`Source reliability ≠ Message importance ≠ Event credibility ≠ Event heat`

不能把四者做成同一颜色、同一“战力值”组件或一个综合等级。

### 2.3 当前组件盘点

公共端只有两个真正抽出的核心组件：

- `MessageFeed / MessageCard`：被首页和日报复用；
- `MessageDetail`：含内容块、双语切换、图片与 Patch 提取表。

Header、Navigation、Footer、Hero、Section heading、Event Card、Event Detail 都直接写在页面中，造成文案、导航项、active 状态和结构不一致。

管理台组件较完整：`AdminLayout`、`SideNav`、`PaginationControls`、`PipelineJobRow`、`PipelineStageBar`、`ReviewCard`、`CollectionScheduleEditor` 等。已有 Dialog、表格、筛选、移动抽屉与操作反馈，但不同开发阶段的样式同时堆在全局样式表中。

### 2.4 当前 Design System 审计

#### Color

当前根变量只有 `ink / muted / paper / panel / line / acid / blue` 和状态色。公共端是暖纸色背景、钴蓝与酸性绿，气质更像技术编辑 Demo；管理台又在这些变量上叠加大量直接 Hex 和 `color-mix`。品牌色、交互色、状态色和内容分类色没有明确边界。

#### Typography

全站使用 `Arial, Microsoft YaHei, sans-serif`。Hero 依赖极大字号与负字距制造冲击，正文、UI、Metadata、数字没有角色分工。管理台大量使用 9–11px 文本，部分信息在高密度下过小。

#### Spacing & Layout

- 公共主宽度统一为 1180px，但 Hero、长文、卡片、指标没有完整的多层宽度体系；
- 管理台最大宽度约 1380px，固定 220px 侧栏；
- 间距以逐个声明为主，没有可复用 scale；
- 普通公共列表已使用直线分隔而非大圆角卡片，这是值得保留的基础；
- 每个公共页面都使用较大的 Hero，降低高频页面首屏效率。

#### Border / Radius / Shadow

公共内容多数为直角、1px 边界，管理台后期样式大量使用 4–10px 圆角。阴影主要用于 Dialog 和回到顶部。方向本身并不差，但缺少“何时直角、何时圆角、何时无容器”的规则。

#### Responsive

公共端主要断点为 760px 和 470px。最大问题是 760px 以下直接隐藏主导航，却没有公共移动导航替代；用户只能依赖页面内链接。消息卡在移动端仍保留偏大的图片和英雄区。管理台有 767px 抽屉导航，结构相对完整。

#### Motion & Accessibility

当前有平滑滚动、图片轻微缩放、按钮按压、侧栏移动和 Spinner。没有全局 `prefers-reduced-motion` 处理。Focus 样式存在，但部分可点击行、仅图片链接和 9px 标签的可发现性仍不足。公共端的全局 `nav`、`footer` 选择器也容易造成组件间样式泄漏。

#### Maintainability

`globals.css` 已超过 700 行，包含旧公共样式、旧管理台样式和“Admin redesign”追加样式，存在重复规则与过宽的全局选择器。它还不是一个由 tokens 驱动的系统，而是逐页累积的视觉结果。

## 3. LeagueNews vs. Universe

### 3.1 产品任务差异

| Universe | LeagueNews |
| --- | --- |
| 低频、主动探索世界观 | 高频、快速确认新信息 |
| 内容较稳定，路径可以戏剧化 | 内容持续更新，路径必须可预测 |
| 图片和地点关系是主要导航线索 | 标题、时间、来源、事件进展是主要判断线索 |
| 可以用大幅留白建立仪式 | 普通列表需要稳定比较密度 |
| 相关角色/地区驱动发现 | 消息/事件/来源/日期驱动追溯 |
| 长转场与章节感可被接受 | 内容出现不能被动画延迟 |

LeagueNews 的公式应是：

`Information First + Editorial Atmosphere + Traceable Evidence`

而不是：

`Immersion First + Lore Exploration`

### 3.2 页面沉浸感 / 信息密度配比

比例表示设计资源和第一注意力的分配，不是精确像素占比。

| 页面 | 沉浸感 | 信息密度 | 理由 |
| --- | ---: | ---: | --- |
| 首页 | 45% | 55% | 允许一个真正的视觉入口，但首屏后立即回答今天应看什么 |
| 消息列表 | 15% | 85% | 高频扫描、筛选、比较和返回是核心 |
| 消息详情 | 20% | 80% | 标题入口可有气氛，正文与来源追溯优先 |
| 事件列表 | 30% | 70% | 需要比消息更强的“进行中”感，同时保持多事件比较 |
| 事件详情 | 50% | 50% | 可以形成专题叙事，但当前状态、时间线和证据必须直接可见 |
| 日报 | 40% | 60% | 采用编辑部 Digest 节奏，但不能伪造日报总结 |
| 管理台 | 5% | 95% | 状态、操作、错误恢复和长时间使用优先 |

移动端的公共页面默认再减少约 10 个百分点的沉浸资源，把空间还给导航、标题、筛选和内容；事件详情与日报可以通过图像顺序保留气氛，而不是保留桌面大 Hero 的高度。

## 4. Visual Direction — The Living Record

### 4.1 设计命题

“活态纪事”不是古老卷轴，也不是未来战情室。它是一个仍在更新的编辑档案：

- **Living**：事件会变化，热度会衰减，日报按日期归档；
- **Record**：每条结论可以回到消息、来源、证据和修订；
- **Editorial**：重点通过位置、尺度和说明被策展，而不是被算法分数染成满屏警报；
- **League-adjacent, not Riot-official**：通过深色环境、暖白、金属细线与有限类别色建立熟悉气质，但不复刻官方徽记、字体和边框。

### 4.2 品牌气质关键词

- 沉静而非阴沉；
- 权威但不假装官方；
- 编辑性而非仪表盘化；
- 有世界感但不角色扮演；
- 精确、可追溯、仍在发生。

### 4.3 两种表面模式，一个系统

#### Reader Dark（公共阅读端）

以蓝黑 Canvas、暖白正文、低对比黄铜结构线为基础。Hero、图片与 Event 专题可以拥有更深的环境层；消息流大部分区域保持安静、稳定、无重阴影。

#### Operator Light（管理台）

以暖灰纸面、深墨文字和低饱和黄铜/蓝色焦点为基础。沿用同一字体角色、spacing、状态语义和线框纪律，但不使用大图、暗角、Display Hero 或装饰菱形。这样既保持品牌家族感，也保护密集操作效率。

Product Category 只改变局部标记，不改变表面模式；LoL PC、Esports、TFT 不能成为三个主题站。

## 5. Typography

### 5.1 字体方案

优先选择 SIL Open Font License、可稳定自托管并可做子集化的字体：

| 角色 | 建议字体栈 | 视觉规则 |
| --- | --- | --- |
| Display | `Source Serif 4`, `Noto Serif SC`, serif | 仅用于首页主命题、事件专题标题、日报日期/标题；中文不全大写、不强拉字距 |
| Heading | `Source Serif 4`, `Noto Serif SC`, serif | 页面 H1、Section H2、卡片重点标题；字重 600–700，行高紧但不压缩中文 |
| Body | `Inter`, `Noto Sans SC`, system-ui, sans-serif | 摘要、正文说明与一般阅读；正文 16–18px、行高 1.7–1.9 |
| UI | `Inter`, `Noto Sans SC`, system-ui, sans-serif | 导航、按钮、筛选和控件；保持清晰，不使用 Display 字体 |
| Metadata | `Inter`, `Noto Sans SC`, system-ui, sans-serif | 12–13px 为默认下限；英文短标签可大写并增加字距，中文不机械加宽 |
| Number | `Inter` + `font-variant-numeric: tabular-nums` | 时间、分数和统计；只有 ID/修订号可用 `ui-monospace`，避免全站终端感 |

不建议引入近似 Beaufort 的“奇幻字体”。Source Serif 4 与 Noto Serif SC 的作用是建立现代编辑出版感，不是模拟 Riot 字形。字体落地时应控制字重数量，优先加载 Display 600/700 与 Sans 400/500/600，避免性能成本。

### 5.2 层级建议

| Token | 桌面 | 移动 | 用途 |
| --- | --- | --- | --- |
| `display-hero` | 64–80 / 0.98 | 40–48 / 1.05 | 仅首页或专题唯一主标题 |
| `display-page` | 44–56 / 1.08 | 34–40 / 1.12 | Event / Daily 页面主标题 |
| `heading-1` | 38–48 / 1.12 | 30–36 / 1.18 | 消息详情标题、管理页主标题上限 |
| `heading-2` | 26–32 / 1.2 | 24–28 / 1.25 | Section 标题 |
| `heading-3` | 20–24 / 1.3 | 19–22 / 1.35 | 卡片标题 |
| `body-lg` | 18 / 1.75 | 17 / 1.7 | Lead 与详情摘要 |
| `body` | 16 / 1.75 | 16 / 1.7 | 长正文 |
| `body-sm` | 14 / 1.6 | 14 / 1.55 | 列表摘要、辅助说明 |
| `label` | 12 / 1.3 | 12 / 1.3 | 分类与控件标签 |
| `meta` | 12–13 / 1.4 | 12 / 1.4 | 时间、来源、形式、数量 |

### 5.3 对齐规则

- 居中只用于首页唯一入口、日报日期开场和少量章节停顿；
- 消息列表、事件列表、所有详情正文、证据和管理台全部左对齐；
- 普通卡片标题最多 2–3 行，摘要最多 2–3 行；详情页不截断；
- 小标签退后依靠明度和空间，不依靠低于可读阈值的字号；
- 页面只能有一个 Display 级标题，卡片不能与页面标题争抢音量。

## 6. Color System

### 6.1 Reader Dark 功能色

以下为方向性基准值，最终实现需要做 WCAG 对比验证；重点是角色关系而不是机械复制 Hex。

| Token | 基准 | 功能 |
| --- | --- | --- |
| `color.canvas` | `#070B0F` | 全局蓝黑环境，避免纯黑死板 |
| `color.field` | `#0A1118` | Hero 延伸、宽幅背景和图片过渡层 |
| `color.surface` | `#0F1720` | 列表区、阅读区的安静表面 |
| `color.surface-elevated` | `#151F29` | 筛选、Popovers、重点摘要、浮层 |
| `color.surface-hover` | `#1A2732` | 可交互表面的一级明度反馈 |
| `color.text-primary` | `#F1E8D7` | 主标题与正文暖白 |
| `color.text-secondary` | `#BCB5A8` | 摘要与说明 |
| `color.text-muted` | `#818994` | Metadata、非当前项 |
| `color.accent` | `#B89A5A` | 黄铜结构、短标签、选中线 |
| `color.accent-strong` | `#D5BA78` | 当前项、关键链接与焦点；严格限量 |
| `color.link` | `#8FC3D1` | 阅读链接，与品牌金和状态色分离 |
| `color.border-subtle` | 暖金灰 16% alpha | 普通分隔线 |
| `color.border` | 暖金灰 28% alpha | 控件、活动 Section 边界 |
| `color.focus` | `#79C8D8` | 键盘焦点，必须高对比且不只靠金色 |
| `color.scrim` | 黑蓝 35%–72% | 图片文字可读区与边缘融合 |

明度关系必须稳定：

`Canvas < Field < Surface < Elevated < Hover < Muted Text < Secondary Text < Primary Text`

金属色的关系必须稀缺：

`Subtle Border < Structural Accent < Active Accent`

### 6.2 Operator Light 功能色

管理台使用同名语义 token 的浅色值：暖灰 Canvas、近白 Surface、深墨主文字、灰绿次文字。Accent 可保留低饱和黄铜用于品牌结构，主操作与焦点使用可访问的蓝青色。状态色继续独立，不能为了“统一品牌”全部改成金色。

### 6.3 Product Category Accent

类别色只允许出现在 2px 边线、小型类别标签、图像 Overlay 的微弱色温或 Section 标记上；不得铺满卡片或改变整页背景。

| Category | 倾向 | 允许用途 |
| --- | --- | --- |
| LoL PC | 冷钢蓝 | 类别短线、标签文字 |
| Esports | 暗铜红 | 类别短线、关键赛事情境 |
| TFT | 低饱和紫 | 类别短线、标签文字 |
| Other products | 灰青 | 中性分类标记 |
| Ecosystem | 灰绿 | 生态/公司层面分类标记 |

同一卡片最多一个 Category Accent。多产品消息使用既有产品优先级或显示“多产品”，不能同时出现彩虹边框。

### 6.4 产品语义色

- Importance：采用中性数字 + 低/中/高/关键文字等级；高等级可使用铜橙，但不持续发光；
- Credibility：采用蓝青系，表达证据稳定度；
- Heat：采用暗红/琥珀系，表达讨论活跃度；
- Success / Warning / Danger / Info：只服务系统状态，尤其用于管理台；
- Source reliability：默认中性显示，仅在解释区域提供分数，不与 credibility 共用徽章。

所有语义都同时显示文字或数字，颜色永远不是唯一编码。

## 7. Spacing, Layout, Border & Depth

### 7.1 Spacing Scale

采用 4px 基线但减少过细档位：

| Token | 值 | 典型用途 |
| --- | ---: | --- |
| `space-1` | 4 | 图标与短标签内部 |
| `space-2` | 8 | 紧密 Metadata、控件组 |
| `space-3` | 12 | 标签组、紧凑行 |
| `space-4` | 16 | 控件内边距、小组间距 |
| `space-5` | 24 | 卡片内容间距 |
| `space-6` | 32 | 卡片纵向节奏、详情小节 |
| `space-7` | 48 | 内容组间距 |
| `space-8` | 64 | 移动 Section / 桌面紧凑 Section |
| `space-9` | 96 | 桌面主要 Section |
| `space-10` | 128 | 仅首页阶段转换 |

规则：同一组内部间距不大于组间距，组间距不大于 Section 间距。消息列表不能为营造氛围把每条间距扩大到专题卡级别。

### 7.2 Layout Tokens

| Token | 建议值 | 用途 |
| --- | --- | --- |
| `layout-bleed` | 视口宽 | Hero 背景和大图环境层 |
| `layout-wide` | 1360px | 首页大构图、管理台上限 |
| `layout-content` | 1180–1240px | 公共列表与 Section |
| `layout-article` | 760–820px | 消息正文、日报阅读栏 |
| `layout-annotation` | 560–640px | 摘要、说明、空状态 |
| `gutter-desktop` | 32–48px | 宽屏边距 |
| `gutter-tablet` | 24px | 平板边距 |
| `gutter-mobile` | 16px | 手机边距 |

建议断点按内容压力定义：约 1200、960、720、480px。不要为每个组件创建独立断点。

### 7.3 Border / Radius

| Token | 值 | 使用规则 |
| --- | --- | --- |
| `radius-none` | 0 | 公共内容卡、Hero 图窗、Section 框架 |
| `radius-xs` | 2px | 精确的分数标记、图片轻微防锯齿边缘 |
| `radius-sm` | 4px | 输入框、按钮、紧凑 Popover |
| `radius-md` | 8px | 管理台 Dialog、复杂浮层 |
| `radius-full` | 999px | 仅状态点、头像或真正的 Pill 控件 |

普通标签不应全部做成胶囊。公共内容卡默认没有完整容器，只用图像、标题、留白和横线建立边界。管理台可保留 4–8px 的操作性圆角，但减少嵌套面板的重复圆角。

### 7.4 Shadow & Depth

- 普通卡：无阴影；
- Sticky Header / Filter：只使用细线和很浅的环境遮罩；
- Popover：小范围软阴影；
- Dialog：明确的深层阴影和 Scrim；
- 图片：通过暗角、Overlay 和边界融合，不用卡片投影；
- Hover：提高一级表面明度或边线明度，不让内容整体“飞起来”。

## 8. Decorative System

### 8.1 允许的语汇

1. **Record Line**：1px 低对比横线，标记 Section 与内容组边界；
2. **Signal Diamond**：4–6px 小菱形，只用于 Section 起点、当前时间线节点或活动导航；
3. **Index Tick**：短竖线/短横线，用于类别或序号；
4. **Open Frame**：只保留两边或角部的开放式框线，用于 Hero / Event summary；
5. **Eyebrow**：英文短标签可大写、加宽字距，中文提供直接说明；
6. **Evidence Thread**：时间线与证据区的一条连续竖线，表达关系而非装饰。

### 8.2 装饰预算

- 每个 Section 只允许一个主要 ornament；
- 普通 Message Card 不使用菱形角饰；
- Event Timeline 的菱形属于节点语义，可以重复；
- 同一元素不同时使用金边、切角、光晕和纹理；
- 高密度区装饰密度必须下降；
- 如果线框不能帮助分组、定位或表示状态，就删除；
- 不使用剑、盾、徽章、地区纹章、假符文和 RPG 属性框。

## 9. Image System

### 9.1 图像角色与比例

| 类型 | 比例/布局 | 处理 |
| --- | --- | --- |
| Homepage Feature | 16:9 或约 21:9 宽幅；桌面不超过约 560px 高 | 可用方向性 Overlay；没有合适图片时改用排版型 Feature |
| Event Hero | 16:9，桌面图文 58/42 或宽幅背景 | 以最佳媒体建立专题语境，标题落在稳定暗区 |
| Event Card | 固定 16:10 | `cover`，焦点优先人物/关键 UI，不因图尺寸改变卡高 |
| Message Thumbnail | 固定 4:3 或 16:10，建议选择一种后全站一致 | 列表右侧辅助辨识；无图时不显示占位幻想图 |
| Daily Lead | 3:2 或 16:9 | 只给当日优先阅读条目，普通条目降低图像权重 |
| Detail Inline Image | 保持原始比例、`contain` | 在深色 Field 上完整展示，尤其保护版本表与文字截图 |

### 9.2 调色与融合

- 列表图片校准到共同 UI 明度，默认可降低约 10%–20% 亮度；
- 饱和度只做轻量收束，保留来源内容本身的辨识；
- Hero 使用 35%–72% 的方向性/底部 Scrim，保证标题对比；
- 图片边缘可使用蓝黑暗角接入 Canvas；
- 不给全部来源图套统一“黑金滤镜”，不掩盖原始媒体性质；
- 文字叠图只放标题、类别和一句摘要，不叠完整 Metadata 集合；
- 图片加载失败时回到排版层级，不显示虚假的品牌图。

### 9.3 Hover

图片只允许约 1.02–1.035 的轻微放大，180–280ms；容器尺寸不变。移动端没有 Hover，信息与动作不能依赖变焦后才出现。

## 10. Motion & Interaction

### 10.1 Motion Tokens

| Token | 时间 | 用途 |
| --- | ---: | --- |
| `motion-instant` | 80–120ms | 按压、颜色反馈 |
| `motion-fast` | 160–180ms | 导航、边线、按钮状态 |
| `motion-base` | 240–280ms | 图片缩放、Popover |
| `motion-slow` | 400–450ms | 仅 Hero 图像首次显现 |

Easing 使用自然的 `ease-out` 进入与 `ease-in` 离开；不引入弹簧、弹跳或 3D 翻转。

### 10.2 增强体验的动效

- Hover / Focus 时标题、箭头和边线同步确认可进入；
- Filter 应用后保留页面框架，列表区域使用短淡变或 Skeleton；
- 移动导航与 Dialog 清楚显示空间来源；
- 事件时间线当前节点可有一次性的淡入，不持续脉冲；
- 图片加载以轻微淡入避免闪烁。

### 10.3 应避免

- 普通消息逐卡滚动入场；
- 自动轮播首页 Feature；
- 全屏页面转场、环境粒子、视差和视频背景；
- 热度、重要性持续闪烁；
- Hover 隐藏原信息或强制出现操作层；
- 实时更新造成列表位置突然跳动。

必须提供 `prefers-reduced-motion`：禁用平滑滚动、位移、缩放和非必要淡入，Spinner 可保留最小状态反馈。

## 11. Information Architecture & Navigation

### 11.1 目标公共架构

当前 `/` 同时承担品牌首页与消息列表。目标应逐步拆分：

```text
/                         首页：今日入口、优先阅读、事件进展、最新消息、日报入口
/messages                 高密度消息列表
/messages/[id]            消息详情
/events                   事件列表
/events/[id]              事件详情
/daily?date=YYYY-MM-DD     日报
/admin/...                处理台（公共导航中的低优先级工具入口）
```

新增 `/messages` 是前端信息架构调整，不改变 Message 业务定义。过渡阶段可让 `/` 与 `/messages` 同时可用；在首页真正改造前不能先破坏根路径。

不应在本轮规划中凭空创建 `/sources`、`/entities` 或 `/topics` 页面。当前没有对应公共目录与筛选契约。只有在数据契约明确后，Source、Entity、Topic 才能成为内部导航入口。

### 11.2 全局导航

主导航在所有公共页面固定为：

`首页 / 消息 / 事件 / 日报`

- Brand 永远链接 `/`；
- 当前项由真实 pathname 判断；
- “处理台”移到右侧 Utility 区，不与阅读栏目同权；
- “Workflow online”只在真实健康状态可获得且用户需要时显示，否则改为中性产品说明或移除；
- 移动端提供 Menu Button + 抽屉/下拉导航，不能隐藏后不提供替代；
- Header 高度桌面约 64–72px，移动约 54–60px；
- 不在每个页面复制 Header JSX。

### 11.3 可点击性规则

| 元素 | 是否可点 | 行为与表现 |
| --- | --- | --- |
| Brand / 主导航 | 是 | 标准站内导航，当前项有线条与 `aria-current` |
| Message 标题 / 图片 / 明确“阅读”动作 | 是 | 进入消息详情；三者共享一致 Hover/Focus，不把整卡伪装成按钮 |
| Event 标题 / 图片 / 明确“查看事件”动作 | 是 | 进入事件详情 |
| Message Type | 是，现有 API 已支持筛选 | 进入 `/messages?message_type=...`，外观必须像可交互 Filter Link |
| Product / Topic / Entity | 当前先否 | 作为静态 Metadata；没有真实筛选契约前不出现手型和 Hover |
| Source 名称 | 视契约而定 | 当前没有公共 Source 页；有 `source_url` 时提供明确“原始来源”外链，名称本身不伪装成内部链接 |
| Importance / Credibility / Heat | 默认否 | 显示说明 Tooltip/Disclosure，不跳转；移动端可点击 info affordance 查看定义 |
| Original / Repost | 否 | 内容形式标记，不做按钮；需要筛选时再升级为 Filter Link |
| 时间 | 否 | 使用 `<time>`，可在 title/辅助文本显示绝对时间 |
| Filter / Sort | 是 | 改写 URL 查询参数，Back/Forward 可恢复 |
| Timeline / Evidence 中的消息 | 是 | 进入消息详情，并保留“来自 Event #id”的返回上下文 |
| 外部来源 | 是 | 新标签页、外链图标、清晰文字，不只放小图标 |

规则核心：**只有真正改变页面、筛选或展开信息的元素才拥有交互外观。** 不把所有 Badge 都做成按钮，也不依赖 Hover 才解释去向。

### 11.4 URL、返回与页面切换

- 列表的 `q / type / featured / sort / page / category / date` 均进入 URL；
- 筛选时重置页码，浏览器前进/后退恢复原条件；
- 从列表进入详情时保存同源 `returnTo` 上下文，必须限制为站内相对 URL；
- 消息详情的返回文案随来源变化：`返回消息列表`、`返回 8 月 13 日日报`、`返回事件：…`；
- 若没有合法来源上下文，回到 canonical 列表而不是依赖 Referer；
- 事件详情到 Message 的链接附带事件上下文；
- 后端尚未返回 Message → Events 关系时，不伪造“相关事件”模块；上下文返回可以先完成，真正反向关系需要另行确认契约；
- Pagination 后焦点与滚动定位到列表标题，不强制回到整个页面顶部；
- 普通路由切换不做戏剧化 Transition。保留 Header，内容用稳定 Skeleton；完成后把焦点移动到新页面 H1。

### 11.5 搜索、筛选与分页边界

Message API 当前已支持：`message_type`、`featured`、`search`、`sort_by=time|priority|intrinsic`、`sort=asc|desc`、`limit/offset`。第一版公共消息筛选只使用这些已存在能力。

Event API 当前已支持：category、product、event family、lifecycle、credibility level、importance level、heat level、search、sort 和 pagination。UI 可采用：

- 一级：Category Tabs；
- 二级：搜索 + `最新进展 / 重要性 / 热度`排序；
- 高级 Disclosure：产品、事件族、生命周期、可信度等；
- Active Filter Summary：显示并可逐个清除。

公共消息流不再一次性拉取全部数据。建议每页 20–25 条，使用带 URL 状态的上一页/下一页与页码摘要。当前不采用 Infinite Scroll；它会损害返回定位、页尾导航和事件/消息间往返。移动端也优先保持相同分页语义，而不是做另一套不可分享的“加载更多”。

### 11.6 信息层级规则

#### Message 扫描顺序

`产品/消息类型 → 标题 → 一句摘要 → 来源 + 时间 + 内容形式 → 重要性 → 少量 Topic/Entity`

- 只展示最多 2 个 Topic 与 2 个核心 Entity，其余用 `+N` 静态摘要；
- 只有 `role=core` 的实体进入列表优先位；
- ID 放到详情或极低权重位置，不作为卡片第一信息；
- Source reliability 不与 Source 名称每次重复组成长句，可用中性说明或详情 Disclosure；
- `original/repost/quote` 必须可读，但不比标题更醒目。

#### Event 扫描顺序

`Category + Lifecycle → 标题 → 当前摘要 → 最新实质进展/更新时间 → 重要性/可信度/热度 → 覆盖与主要来源`

三个指标保持并列但不等权抢色。最后实质更新比总消息数量更靠前，因为 Event 的核心是“发生了什么变化”。

#### Daily 扫描顺序

`日期与报告状态 → 当日优先阅读 → 栏目 → 有序条目 → 来源与时间`

日报不能凭空增加“今日趋势”“编辑总结”或 AI 观点。可以从已选条目中选择最高重要性项做更大版式，但必须标成“当日优先阅读”，说明它是现有排序的视觉呈现，不是假装人工 Headline。

## 12. Page-level Redesign

### 12.1 `/` 首页

**Current**  
同一页包含超大 Hero、全量统计、最新一条蓝色 Lead、完整消息流、处理链路和来源数量。所谓“今日概览”实际使用全量结果；Lead 是 `items[0]`，没有独立策展语义。

**Problem**  
首页与消息列表职责混淆；四个强视觉块同时竞争；技术处理链比产品内容更突出；“每日简报”“今日概览”“最新消息”“已审核消息”语义混杂；抓取全量消息不可扩展。

**Target**  
首页成为 45/55 的编辑入口：一个真实可解释的“当前优先阅读/最新消息”主模块、2–4 个当前事件进展、最新消息短列表和当日日报入口。移除全量假今日统计；只展示可以由现有数据准确计算的数字。

**Universe Inspiration**  
学习 Feature 的面积、位置、图像与上下文，而不是蓝色大块和超大 TOP 水印。

**LeagueNews Adaptation**  
Hero 高度受控，首屏至少露出下一组内容。若没有合适媒体，采用排版型 Feature。用现有 `priority` 排序时称“当前优先阅读”；若只取时间第一项则明确称“最新消息”。

**Keep**  
品牌命题、消息/事件/日报入口、来源可追溯说明。

**Change**  
拆出 `/messages`；删除公开主页的 Pipeline 流程图或降为页尾 Methodology 一句话；来源计数不再伪装目录；Header 统一；Feature 使用准确语义。

### 12.2 `/messages` 消息列表（目标新增路由）

**Current**  
实际列表位于 `/`。All / Featured 两个筛选；一次加载全部消息；卡片显示大量标签、重复详情 CTA 和 220–320px 图片。

**Problem**  
没有搜索、排序、分页和结果状态；卡片元数据顺序偏系统化；产品分类缺失；Topic/Entity 数量不受控；标题和 CTA 重复；返回详情会丢失筛选和滚动上下文。

**Target**  
15/85 的高密度流。紧凑页头、URL 驱动的搜索/类型/精选/排序、结果数与分页；卡片以标题和摘要为核心，固定图像比例，可快速比较。

**Universe Inspiration**  
学习同层内容稳定、低对比边线、标题/类别/元数据分工。

**LeagueNews Adaptation**  
不使用大画廊与横向 Carousel。每页 20–25 条；无图卡保持完整；类别 Accent 只是一条短线；Metadata 不全部做填充 Badge。

**Keep**  
标题、摘要、时间、来源、内容形式、重要性、Topic/Entity 与结构化媒体提示。

**Change**  
显示 Product；控制 Topic/Entity 数量；降低 ID 权重；移除重复 CTA；实现现有 API 已支持的搜索、排序和分页；保留查询状态。

### 12.3 `/messages/[id]` 消息详情

**Current**  
标题、摘要、来源、可靠性、作者、时间、原始链接、双语切换、完整内容块和结构化 Patch 表较完整。返回始终指向 `/`。

**Problem**  
导航缺日报；来源页/事件关系不清；Product 与 Entity 不可见；68px 标题对长中文过强；内容背景与页面层级单一；来自事件/日报的上下文被丢失。

**Target**  
20/80 的编辑阅读页。Breadcrumb/上下文返回、克制标题区、窄阅读栏、清晰 Provenance Panel、原文/译文切换和媒体结构区。长文可读性优先。

**Universe Inspiration**  
学习“进入一篇内容”的标题仪式、窄阅读宽度、图片可突破正文宽度。

**LeagueNews Adaptation**  
不做章节揭幕或滚动动画。正文 Sans 为主；标题 Serif；原始内容与审核译文的状态必须明确。Patch 表属于证据工具，保持高对比和横向可用性。

**Keep**  
不可变原文的完整呈现、双语切换、外部来源、结构化图片提取、回到顶部。

**Change**  
统一 Header；增加 Product、核心 Entity 和内容形式的清晰层；使用 context-aware return；重新组织 Source reliability 说明；降低标题上限；对空媒体/Embed 提供明确动作。

### 12.4 `/events` 事件列表

**Current**  
Category Tabs + 与 Message 相同的列表卡；显示标题、当前摘要、三个指标、来源/消息数、主要来源和可选图片；固定加载最多 100 条。

**Problem**  
Event 看起来只是“更复杂的 Message”；没有搜索、排序、分页和现有高级筛选；未显示最后实质更新；指标被多个 Badge 平铺，层级不足。

**Target**  
30/70 的“当前事件记录”。Event Card 使用独立结构：状态/类别、标题与当前摘要、最新进展时间、三指标窄 Rail、覆盖与主要来源。支持 URL 驱动筛选和排序。

**Universe Inspiration**  
学习关联内容的策展与图文分栏、不同等级内容采用不同构图。

**LeagueNews Adaptation**  
保持纵向列表与比较能力，不做地图/Carousel。图片比 Message 稍大，但无图事件同样成立。三个指标不做 RPG 仪表。

**Keep**  
既有 Category 映射、三个独立指标、覆盖、主要来源和当前摘要。

**Change**  
建立 EventCard；显示最后实质更新；接入现有 API 的搜索/排序/分页与高级筛选；生命周期使用明确中文标签；减少无语义 Badge。

### 12.5 `/events/[id]` 事件详情

**Current**  
标题、摘要、四格指标、最新进展、关键事实、时间线和证据列表。最佳媒体和 `related_messages` 未使用；返回固定为 `/events`。

**Problem**  
缺少专题入口和锚点导航；空时间线/证据没有完整空状态；关键事实区域的双栏结构只放了一栏；证据与相关消息混名但实际只展示 evidence；消息详情无法回到事件。

**Target**  
50/50 的事件专题：图文 Hero/排版 Hero、当前状态摘要、最新进展和最后更新时间在首屏；指标是解释性 Summary Rail；正文按“关键事实 → 实质时间线 → 证据 → 相关消息”展开。

**Universe Inspiration**  
学习把对象放进关系网络、使用大图建立语境、用章节节奏组织长页面。

**LeagueNews Adaptation**  
图片只占入口，不压住证据。Timeline 是页面叙事骨架；证据始终可核验；移动端线性展开，不藏进复杂 Tabs。

**Keep**  
当前摘要、三个独立指标、关键事实、实质时间线、mention 证据、原始来源。

**Change**  
使用 `best_media_url`；展示 `last_material_update_at`、产品/分类/生命周期、`related_messages`；证据按 relation/role 可扫读分组；Event → Message 链接保留返回上下文；补全空状态与 Section Anchor。

### 12.6 `/daily` 日报

**Current**  
日期 Hero + 四个重复 MessageFeed；URL 可传 date 但没有控件；空状态让用户调用生成接口。

**Problem**  
不像一份日报，只像四组筛选结果；无法浏览前后日期；栏目序号在每组重置；没有阅读完成感；公开文案泄漏内部操作。

**Target**  
40/60 的 Editorial Digest。日期是主索引，提供上一日/下一日/回到今日与可访问日期输入；从既有条目中选择当天最高优先条目做版式 Lead，随后按四个栏目形成稳定编号与紧凑条目。

**Universe Inspiration**  
学习章节开场、编辑尺度和安静的长页面节奏。

**LeagueNews Adaptation**  
不添加不存在的日报综述、趋势或 AI 观点。Lead 只改变展示，不改变选取与排序规则；每条仍回到 Message 证据。

**Keep**  
上海自然日、现有栏目和上限、既有日报选取/去重规则、消息详情链接。

**Change**  
加入日期导航；建立 DailyLead / DigestRow 视觉变体；统一全页编号或按栏目明确编号；空状态改为“当日暂无已发布日报”并提供其他日期/最新消息入口；返回消息时保留日报上下文。

### 12.7 `/admin/pipeline`

**Current**  
指标、状态/来源/类型/搜索筛选、可展开表格、阶段条、处理/重试/审核/查看操作与分页。

**Problem**  
信息完整但 9–11px 文本偏多；整行可展开的反馈不够明确；状态、动作和信息密度受全局旧样式影响。

**Target**  
Operator Light 的任务监控页。把异常、待审和进行中放在视觉优先位；表格保持高密度但正文不低于 12px；展开行为有 Chevron、Hover 和 `aria-expanded`。

**Universe Inspiration**  
只学习清晰分组、稀缺强调和细线秩序。

**LeagueNews Adaptation**  
不使用深色沉浸或装饰框。状态色服务操作，品牌金只用于 Section 或当前导航。

**Keep**  
现有筛选、阶段详情、任务动作、分页和行展开逻辑。

**Change**  
统一控件尺寸、状态标签、空/错/加载状态和行可点击性；压缩无效容器层级。

### 12.8 `/admin/messages`

**Current**  
消息管理有类型、优先级、时间、搜索等筛选，提供列表/审核对照视图与分页。表格展示标题、类型、产品、主题、重要性和时间。

**Problem**  
列表与 Review 视图层级差异不够；筛选区与结果区的主次接近；产品、主题和重要性同时进入表格后在窄屏容易拥挤。

**Target**  
让列表明确服务“定位一条已发布消息”，Review 视图服务“快速比较原始与发布结果”。表格保持高密度，窄屏通过列优先级和行详情渐进披露，而不是把所有列压小。

**Universe Inspiration**  
学习档案索引、稳定标题落点和稀缺强调，不学习 Lore 阅读节奏。

**LeagueNews Adaptation**  
使用浅色高密度表面；原文/结果对照维持等权，数字使用 tabular 样式，ID 局部使用 mono。

**Keep**  
现有字段、筛选、分页、列表/对照切换与详情入口。

**Change**  
统一 Metadata 顺序、表格列优先级、筛选控件和与公共端一致的内容形式/重要性语义。

### 12.9 `/admin/messages/[id]`

**Current**  
以独立管理详情展示原文内容、翻译结果、重要性与依据，以及重跑等操作；返回固定指向消息管理。

**Problem**  
详情与公共消息共享概念但视觉结构完全分离；操作区在长内容后方；重要性证据和内容证据缺少清晰章节关系。

**Target**  
形成审计型详情：Identity / Source / Original / Published Projection / Importance / Actions 六个明确区域。操作摘要可在宽屏侧栏或页首保持可见，危险与重跑动作独立分组。

**Universe Inspiration**  
学习对象档案、章节节奏和证据分层，不增加沉浸背景。

**LeagueNews Adaptation**  
Operator Light 下以结构线和标题层级表达档案感；内容仍按原始证据与发布投影的真实关系组织。

**Keep**  
原文、翻译、重要性依据、返回入口和所有现有业务动作。

**Change**  
增加页内目录/Sticky Action Summary；统一 Metadata 顺序；明确普通、重跑和危险动作层级；不把内部 Breakdown 伪装成公共内容。

### 12.10 `/admin/reviews`

**Current**  
待审队列行显示阶段进度，点击按钮打开全屏级 Dialog，内部按普通消息审核或 OCR 审核切换。

**Problem**  
Dialog 内容复杂、视觉层次依赖大量小字号与嵌套框；关闭与焦点管理需要实现阶段专项验证；进度条容易接近游戏状态条。

**Target**  
以“当前必须做的决定”为第一注意点，Context、Proposal、Evidence、Action 四层明确。Dialog 在桌面保持大工作区，移动端转为完整页面式 Sheet。

**Universe Inspiration**  
学习章节和证据关系的结构化，不增加装饰。

**LeagueNews Adaptation**  
Review 是高风险操作，动效与品牌气氛必须后退。保留清楚的阶段、理由、原文与反馈。

**Keep**  
现有审核路径、OCR 特例、Escape/Backdrop 关闭、原始链接和审批动作。

**Change**  
提高字号、减少框中框、明确主/次/危险动作；验收 Focus Trap、焦点返回、未保存关闭提示和移动可操作性。

### 12.11 `/admin/collection`

**Current**  
Source 与采集日志分 Tab；Source 表格可编辑计划，Schedule Editor 以 Dialog 展示。

**Problem**  
Source 状态、最近运行与计划操作的视觉优先级接近；日志和来源是两种任务却共享较多通用面板样式。

**Target**  
来源视图回答“谁在采集、是否健康、何时再运行”；日志视图回答“刚发生什么、哪里失败”。Schedule Editor 保持操作性浅色 Dialog。

**Universe Inspiration**  
学习归属和索引关系。

**LeagueNews Adaptation**  
Source 可有首字母/平台图标，但不伪造徽记；状态优先于装饰。

**Keep**  
所有采集计划、Source 身份、日志和编辑能力。

**Change**  
统一状态/时间/动作列，强化可点击按钮而非整卡；完善 Dialog 焦点与保存反馈。

### 12.12 `/admin/system`

**Current**  
展示 PipelineJob 运行/等待/完成/失败指标、失败任务表、重试操作与 Corrections 表和分页。

**Problem**  
异常、恢复动作和历史修正的层级接近；页面源码高度压缩，后续视觉修改容易误伤逻辑；失败摘要需要更适合排障的截断与展开模式。

**Target**  
明确形成“当前健康摘要 → 需要立即处理的失败 → 已请求的 Corrections”任务顺序。失败行突出阶段、错误摘要与唯一主动作，历史修正保持次级。

**Universe Inspiration**  
只采用低噪声分隔、单一第一注意点和档案式记录顺序。

**LeagueNews Adaptation**  
异常是操作语义，不是戏剧素材；使用 Operator Light、明确状态色和文本说明，不增加沉浸图像。

**Keep**  
现有指标、失败恢复、Corrections、分页和所有业务行为。

**Change**  
统一异常状态与操作层级；改善错误展开；页面源码可维护性应另开小范围纯重排任务，不能与视觉行为改动混成一次提交。

### 12.13 `/admin/system/ocr`

**Current**  
左侧选择测试图片和参数，右侧展示原图、叠图、历史结果、置信度、结构化 JSON 和激活生产配置操作。

**Problem**  
这是 Desktop-first 的视觉工作台，在平板/手机上信息压力最大；参数、历史、结果和生产激活动作的风险等级没有充分拉开；原图与结构化数据需要不同的观看宽度。

**Target**  
保留双栏实验台：控制/历史为稳定侧栏，证据图像与结构化结果为主工作区；生产激活是明确的高影响动作。窄屏按“选择与运行 → 图像 → 结果 → 激活”线性降级。

**Universe Inspiration**  
只学习图像主体与辅助信息的面积分配。

**LeagueNews Adaptation**  
原图是工作证据，不能套公共端暗角、饱和度或裁剪规则；结构化 JSON 继续使用工具型等宽排版。

**Keep**  
所有 OCR 参数、图片、历史结果、结构化输出和激活行为。

**Change**  
建立风险清楚的 Action 层级、工作区最小宽度、移动降级和更稳定的图像对照；不在视觉阶段调整 OCR 算法或默认参数。

### 12.14 `/admin/system/knowledge`

**Current**  
通过 Rules / Terms Tabs 创建、查看并激活/停用稳定规则与术语，采用表单和记录列表。

**Problem**  
规则、术语、状态和动作视觉相似；长文本可扫描性不足；创建区与已有记录的边界不够明确。

**Target**  
把页面设计成维护型记录表：创建/编辑是明确工作区，现有规则按状态和更新时间可扫读；Active/Inactive 以文字、图标和颜色共同表达。

**Universe Inspiration**  
学习命名、归属和档案索引，不学习装饰框。

**LeagueNews Adaptation**  
规则与术语是文本资产，阅读与操作效率高于品牌气氛；使用 Operator Light 与统一表单模式。

**Keep**  
Rules / Terms 信息结构、创建、激活、停用和全部现有字段。

**Change**  
统一编辑器、记录行、状态和危险动作；改善长文本展开与移动端顺序；不改变规则治理和术语应用逻辑。

## 13. Component-level Current → Target

| 组件/概念 | Current | Target |
| --- | --- | --- |
| Public Header | 每页复制，导航项不一致；移动端直接隐藏 | 共享 SiteHeader；首页/消息/事件/日报固定顺序；Utility Admin；完整移动抽屉 |
| Main Navigation | 全局 `nav` 样式，active 手写 | pathname + query-aware；清晰 `aria-current`；低对比线 + 单一 Diamond/Tick |
| Hero | 所有页面都用 52–104px 大标题和大留白 | 只首页/事件详情/日报有中等沉浸 Hero；列表使用 Compact Page Intro |
| Homepage Feature | 最新一条蓝底大块，暗示 TOP | 根据真实排序准确命名；图像/排版均可；位置、面积和说明共同表达优先 |
| Section Title | Kicker + 38px Sans 标题 | Serif H2 + 短 Eyebrow + Record Line；信息密集区左对齐 |
| Message Card | 三列直线列表，但标签多、CTA 重复 | 标题/摘要主导；Product/Type 清晰；元数据稳定；最多少量 Topic/Entity；固定可选缩略图 |
| Event Card | 复用 Message Card | 独立 Event 结构；当前摘要、最后实质更新、三指标 Rail、覆盖与来源 |
| Daily Report Row | 复用 Message Card | DailyLead + DigestRow；更像有序编辑目录，仍链接同一 Message |
| Message Detail | 内容完整但上下文和产品关系缺失 | Breadcrumb、Provenance、窄阅读栏、产品/实体、上下文返回 |
| Event Detail | 指标 + Section，未用 Hero/related messages | 专题入口、Current State、Timeline、Evidence、Related Messages 完整关系 |
| Tags | Topic/Entity/状态多为填充小块 | 三类分开：可交互 Filter、静态 Metadata、语义 Status；外观不得混用 |
| Importance | 彩色描边 Badge + 数字 | 中性数字/等级，铜橙仅提示高阶；附简短“为何显示”解释入口 |
| Credibility | 普通 Topic Badge | 独立蓝青语义，显示分数 + 等级；解释证据而非表现为防御属性 |
| Heat | 与 Credibility 同形 Badge | 独立琥珀/暗红语义，显示活动度和更新时间；不脉冲 |
| Original / Repost | 小型边框 Badge | 稳定内容形式标签；Original 可稍强，Repost 中性；不暗示真假 |
| Source | 名称 + reliability 长句 | 来源名称、时间、外链分工；可靠性放辅助层；不与事件可信度混淆 |
| Product Category | 公共 Message 缺失 | 每条只显示一个主 Category Accent/标签，多产品明确说明 |
| Filters | Message 仅 All/Featured；Event 仅 Category | URL 驱动；核心筛选常显，高级筛选 Disclosure；显示 Active Filters 和清除 |
| Pagination | 仅管理台有 | 公共列表采用 URL 分页；保留过滤/返回状态；暂不使用 Infinite Scroll |
| Buttons | 全局样式和管理台多种局部样式 | Text Link / Secondary / Primary / Danger 四级；公共 Primary 稀少，避免金色实心泛滥 |
| Modal / Dialog | 只在管理台，视觉可用但规则分散 | 统一 Scrim、Header、Action、Focus Trap、Esc、焦点返回和移动 Sheet 规则 |
| Mobile Navigation | 公共端缺失，管理台已有抽屉 | 公共共享抽屉；管理台保留独立抽屉；触控目标至少 44px |
| Empty / Error / Loading | 各页文本不一，公共日报暴露内部 API | 统一语气与动作；Skeleton 保持布局；公开文案不要求用户执行后台操作 |
| Footer | 每页复制不同标语 | 共享 Footer；简要方法论、非官方说明和关键导航，不占主要阅读空间 |

## 14. Design Tokens Proposal

Token 使用语义命名，不把颜色名、某个页面或 Riot 术语写进基础层。

### 14.1 Token Namespaces

```text
color
  canvas / field / surface / surface-elevated / surface-hover
  text-primary / text-secondary / text-muted / text-inverse
  accent / accent-strong / link / focus
  border-subtle / border / border-strong
  overlay-soft / overlay-strong / scrim
  category-lolpc / category-esports / category-tft / category-other / category-ecosystem
  importance-* / credibility-* / heat-*
  success / warning / danger / info

typography
  family-display / family-body / family-ui / family-mono
  display-hero / display-page
  heading-1 / heading-2 / heading-3
  body-lg / body / body-sm
  label / meta / number
  weight-regular / weight-medium / weight-semibold / weight-bold
  tracking-tight / tracking-normal / tracking-label

spacing
  1 / 2 / 3 / 4 / 5 / 6 / 7 / 8 / 9 / 10

border
  width-hairline / width-active / width-focus
  color-subtle / color-default / color-strong

radius
  none / xs / sm / md / full

shadow
  none / popover / dialog

motion
  instant / fast / base / slow
  ease-enter / ease-exit / ease-standard

layout
  bleed / wide / content / article / annotation
  gutter-mobile / gutter-tablet / gutter-desktop
  header-mobile / header-desktop

z-index
  base(0) / sticky(20) / dropdown(40) / header(60)
  scrim(80) / drawer(90) / modal(100) / toast(120)
```

### 14.2 Token Governance

1. 组件只消费语义 token，不直接使用 Category Hex；
2. Reader Dark 与 Operator Light 覆盖颜色值，不复制组件规则；
3. 状态 token 与品牌 Accent 分离；
4. 新增直接 Hex 必须先证明无法归入现有角色；
5. 页面不定义私有字号和阴影，除非记录为受控例外；
6. Breakpoint、Content width 和 z-index 必须来自统一层；
7. 每次视觉 Phase 都清理本次触及区域的旧规则，不顺手重写未触及的管理台样式。

## 15. Implementation Plan

本计划只定义后续实施；当前阶段不执行。每个 Phase 独立构建、可回滚，并保持站点可运行。

### Phase A — Design Foundation

**范围**  
建立 token、字体角色、基础 Reset、焦点、reduced motion 和 Reader/Operator 表面模式。只替换基础变量，不改页面结构。

**涉及文件**  
`apps/web/app/globals.css`、`apps/web/app/layout.tsx`。若样式继续增长，可新增一个薄的 token 文件，但不引入 CSS-in-JS 或组件框架。

**涉及组件**  
全局 Body、文字、链接、按钮、表单、Focus、Surface。

**风险**  
全局选择器影响公共端与管理台；字体加载造成 CLS/性能下降；新暗色公共端对现有直接 Hex 对比不足。

**验收标准**

- Reader Dark 与 Operator Light 的主文字/背景/焦点通过对比检查；
- 无路由或业务行为变化；
- 字体加载失败时 fallback 不破版；
- `prefers-reduced-motion` 生效；
- `pnpm lint:web` 与 `pnpm build:web` 通过。

### Phase B — Global Shell & Navigation

**范围**  
抽取公共 Header/Footer/Shell，统一品牌名、导航、active 状态和移动导航；保持各页主体内容不变。

**涉及文件**  
当前五个公共页面、`apps/web/app/layout.tsx`、`apps/web/app/globals.css`；后续新增 `components/site-header.tsx`、`site-footer.tsx` 或等价小组件。

**涉及组件**  
SiteHeader、PrimaryNav、UtilityNav、MobileNav、SiteFooter。

**风险**  
Active 状态错误；移动 Drawer 的焦点/滚动锁；详情页旧返回链接与新 Header 重复。

**验收标准**

- 所有公共页导航顺序一致且 Daily 不再缺失；
- 320px 宽度可进入首页/消息/事件/日报/处理台；
- 键盘可打开、关闭并遍历移动导航；
- Brand 一律返回 `/`，Admin 保持独立 SideNav；
- 每个页面仍可单独加载和刷新。

### Phase C — Message Stream & Message Detail

**范围**  
在不改变根首页的前提下新增 `/messages` 作为完整列表；重做 Message Card 信息层级；接入现有公开 API 的搜索、消息类型、精选、排序和分页；升级详情返回上下文与阅读样式。

**涉及文件**  
`apps/web/app/page.tsx`、新增 `apps/web/app/messages/page.tsx`、`apps/web/app/messages/[id]/page.tsx`、`components/message-feed.tsx`、`components/message-detail.tsx`、`lib/api.ts`、`globals.css`。

**涉及组件**  
MessageList、MessageCard、MessageFilters、Pagination、Provenance、LanguageToggle、ContentBlocks。

**风险**  
实施时需要重新核对公开 API 契约；offset/query 映射错误；返回上下文开放重定向风险；Topic/Entity 过长。

**验收标准**

- `/messages` 首屏不抓取全量数据；
- 搜索/筛选/排序/页码可分享并支持 Back/Forward；
- Product、Type、Source、Time、Form、Importance 不混淆；
- 无图、长标题、多 Topic/Entity、中英切换与 Patch 表均通过响应式检查；
- 从消息列表返回时恢复原 URL 条件；
- 不更改消息分类、重要性或发布逻辑。

### Phase D — Homepage

**范围**  
把 `/` 从全量消息页改成编辑入口；组合现有 Message、Event 和当日日报数据；删除误导统计和公开 Pipeline 主模块。

**涉及文件**  
`apps/web/app/page.tsx`、`lib/api.ts`、公共 Feature/Section 组件、`globals.css`。

**涉及组件**  
HomepageHero、PriorityFeature、EventUpdateStrip/Grid、LatestMessages、DailyEntry、MethodologyNote。

**风险**  
把算法排序误写成人工编辑；多个 API 请求影响首屏；没有媒体时构图失效；日报不存在时入口语义不准确。

**验收标准**

- 页面所有“今日/最新/优先”文案与真实查询一致；
- 没有媒体、没有日报、没有事件时仍形成完整首页；
- 首屏能看到主命题与下一组内容线索；
- 首页不是消息列表的重复版本；
- 原 `/` 书签仍有效，不出现断路。

### Phase E — Event Experience

**范围**  
建立独立 EventCard、完整列表筛选/排序/分页和事件专题详情；展示已存在但当前遗漏的字段与 `related_messages`。

**涉及文件**  
`apps/web/app/events/page.tsx`、`apps/web/app/events/[id]/page.tsx`、`lib/api.ts`、`lib/types.ts`（仅在核对契约确有需要时）、新增事件展示组件、`globals.css`。

**涉及组件**  
EventFilters、EventCard、MetricRail、EventHero、CurrentState、Timeline、EvidenceGroup、RelatedMessages。

**风险**  
三个指标被视觉合并；Evidence 数据量过大；图片来源差异；筛选组合为空；Event → Message 返回上下文丢失。

**验收标准**

- Event 与 Message 不再同构；
- 最后实质更新、主要来源、三个指标和覆盖语义清楚；
- 现有 Event API 所支持的主要筛选可通过 URL 使用；
- 详情展示 Timeline、Evidence 和 Related Messages，并有独立空状态；
- denied/corrected 等状态仍展示而非被视觉隐藏；
- 不更改事件聚合、可信度、热度或重要性算法。

### Phase F — Daily Report

**范围**  
建立日期导航、DailyLead 与 DigestRow，改善空状态与从日报到消息再返回的路径。

**涉及文件**  
`apps/web/app/daily/page.tsx`、`components/message-feed.tsx` 或新增日报专用展示组件、`lib/api.ts`、`globals.css`。

**涉及组件**  
DateNavigator、DailyHeader、DailyLead、DigestSection、DigestRow、DailyEmptyState。

**风险**  
视觉 Lead 被误解为新增选取算法；未来日期/时区边界；同一 MessageCard 变体条件过多。

**验收标准**

- 日期一律按 Asia/Shanghai 表达；
- 上一日/下一日/今日和直接日期输入可用；
- 空报告不暴露生成 API；
- Lead 只来自已选日报条目，不改变栏目与选取规则；
- 消息详情能返回原日报日期。

### Phase G — Admin Alignment

**范围**  
将管理台映射到 Operator Light token，统一页面头、表单、表格、状态、分页、Dialog 和空/错/加载状态；按页面小批替换，不改变操作逻辑。

**涉及文件**  
`apps/web/app/admin/**`、`components/admin/**`、`globals.css` 中 Admin 区域。

**涉及组件**  
AdminLayout、SideNav、Filters、Table、PaginationControls、PipelineStageBar、ReviewDialog、ScheduleEditor、各类 Status Badge。

**风险**  
管理台功能面广；现有 CSS 新旧规则重叠；小屏 OCR 工作台；Review Dialog 焦点与未保存状态。

**验收标准**

- 每次只改一个管理模块并保持其他模块可运行；
- 关键正文/控件文字原则上不低于 12px；
- 表格、筛选、展开、审核、重试、采集计划、OCR 和知识维护行为不变；
- Dialog 具备 Focus Trap、Esc、焦点返回和清晰的主/危险动作；
- 状态色不依赖颜色单独表达。

### Phase H — Responsive, Accessibility & Polish

**范围**  
跨页视觉 QA、键盘/屏幕阅读器、对比、图片焦点、Reduced Motion、Skeleton、性能和样式清理。此 Phase 修正系统性问题，不增加新功能。

**涉及文件**  
全部已触及公共/管理组件和 `globals.css`；必要时拆分样式文件，但不引入新框架。

**涉及组件**  
全站。

**风险**  
把 Polish 变成无限重构；清理旧 CSS 时误删管理台依赖；截图样本不足。

**验收标准**

- 320 / 375 / 768 / 1024 / 1440px 关键页面通过视觉检查；
- 公共移动导航、筛选、长标题、无图、错误/空状态完整；
- 键盘焦点顺序、`aria-current`、Dialog、表单标签和动态状态可理解；
- 图片不会造成布局跳动；核心页面满足性能预算；
- `rg` 确认本次触及组件没有遗留无用 class，`git diff` 不覆盖无关用户改动；
- `pnpm lint:web` 与 `pnpm build:web` 通过。

## 16. Scope Guardrails

### 16.1 本视觉计划明确不改变

- Message 分类、Product/Topic/Entity 业务定义；
- Message importance 与 priority 计算；
- Source reliability 计算；
- Event 准入、聚合、粒度、生命周期、可信度、热度和重要性算法；
- Daily Report 的日期窗口、原创限制、重要性阈值、事件去重、栏目和条目上限；
- RawItem 不可变证据、发布投影与修订历史；
- 数据库模型与迁移；
- 管理台现有审核、恢复、采集、OCR、规则/术语操作。

### 16.2 需要明确数据契约后才能做

- Message 详情真正的反向 Related Events；
- Source 目录和站内 Source 详情；
- Entity / Topic 的公共聚合页和可靠筛选；
- 人工编辑的首页 Headline；
- 日报总结、趋势、观点或跨日分析；
- 个性化、收藏、已读状态、通知与账户功能。

在这些契约不存在时，设计必须使用诚实的静态标签、现有排序和外部来源链接，不做假入口。

### 16.3 不应复制的 Universe 表现

- Riot 字体、角色/地区徽记、官方边框和地图资产；
- 每页 60–80vh Lore Hero；
- 全大写奇幻标题、复杂切角、假符文纹理；
- 自动轮播、环境视频、滚动揭幕和长页面转场；
- 用黑金滤镜把来源媒体伪装成 Riot 官方视觉；
- 把 Event 指标做成 RPG 属性面板。

## 17. Definition of Success

视觉改造完成后，用户应能在数秒内回答：

1. 我现在位于首页、消息、事件还是某一天的日报？
2. 这条内容是什么产品/类型，何时发布，来源是谁？
3. 它为什么值得先看，但这个“值得”是最新、重要性还是编辑位置？
4. 对事件而言，当前结论、最新实质进展、可信度和热度分别是什么？
5. 我可以在哪里核验原始来源与证据？
6. 点击后会去哪，如何回到原来的筛选、日期或事件上下文？
7. 在没有高质量图片时，页面是否仍然清晰、有品牌感？
8. 在手机上是否仍能导航、筛选、阅读和返回？

如果答案依赖 Hover、猜测 Badge 是否可点、把最新误认成精选、或者必须先理解内部 Pipeline，设计就没有完成。

LeagueNews 的品牌感最终不应来自“像 Riot”，而应来自一组持续重复的决策：**把当前事实放在前面，把证据放在可追溯的位置，把装饰留给结构转折，把图片校准为环境而非壁纸，并让 Message、Event、Daily 各自拥有清楚的阅读承诺。**
