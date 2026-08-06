# Codex Prompt：管理台重设计实现

## 背景与依据

仓库 `/Users/czh/Projects/LeagueNews`，分支 `codex/architecture-remediation-exploration`。
完整设计方案见 `docs/admin-redesign.md` —— **动手前完整读这份文档**，本任务严格按照文档实现。

技术栈：Next.js 15 App Router、React 19、TypeScript 5.7、Tailwind CSS v4（`@import "tailwindcss"` 方式，无 `tailwind.config.js`）、lucide-react、无其他 UI 库。现有 CSS token 定义在 `apps/web/app/globals.css` 的 `:root` 块。

现有问题：`apps/web/components/admin-console.tsx` 是一个 3100 行的单文件客户端组件，所有数据在挂载时一次性加载 15 个接口，6 个扁平 tab，无法看到每条消息的处理进度。**保留这个文件不删除**，新路由并行上线后期再清理。

---

## 一、路由架构

在 `apps/web/app/admin/` 下新建以下路由（参考 `docs/admin-redesign.md` 第一节）：

```
app/admin/
├── layout.tsx              ← 持久侧边栏 shell
├── page.tsx                ← redirect → /admin/pipeline
├── pipeline/page.tsx       ← 流水线监控（首页）
├── messages/
│   ├── page.tsx
│   └── [id]/page.tsx
├── events/
│   ├── page.tsx
│   └── [id]/page.tsx
├── reviews/page.tsx
├── collection/page.tsx
└── system/
    ├── page.tsx
    ├── ocr/page.tsx
    └── knowledge/page.tsx
```

---

## 二、新增组件

在 `apps/web/components/admin/` 目录下新建以下独立组件。每个组件单独一个文件，可独立测试：

| 文件 | 用途 |
|---|---|
| `AdminLayout.tsx` | 侧边栏 shell，接受 children |
| `SideNav.tsx` | 导航链接列表，激活态、badge、折叠 |
| `PipelineStageBar.tsx` | 8 节点进度条 |
| `StageTooltip.tsx` | 节点 hover tooltip |
| `ExpandableRow.tsx` | 表格行展开容器 |
| `ItemDetailCard.tsx` | 消息阶段详情卡 |
| `ImportanceDimensions.tsx` | 编辑重要性特征展示 |
| `EventTimeline.tsx` | 时间线型事件节点列表 |
| `MultiMembershipView.tsx` | 多归属消息对照表 |
| `ReviewCard.tsx` | 审核队列单条卡片 |
| `SourceStatusRow.tsx` | 数据采集来源状态行 |
| `PipelineJobRow.tsx` | 系统运维 job 列表行 |

---

## 三、CSS token 扩展

在 `apps/web/app/globals.css` 的现有 `:root` 块末尾追加（不改动现有 token，只新增）：

```css
/* 语义 token */
--success: oklch(58% 0.16 145);
--warning: oklch(68% 0.18 75);
--danger:  oklch(55% 0.20 25);
--info:    oklch(58% 0.15 240);

/* Pipeline 阶段节点 */
--stage-done:    var(--success);
--stage-running: var(--blue);
--stage-failed:  var(--danger);
--stage-review:  var(--warning);
--stage-pending: var(--line);
--stage-skipped: var(--muted);
```

深色模式调整（`@media (prefers-color-scheme: dark)` 块末尾追加）：
```css
--success: oklch(65% 0.14 145);
--warning: oklch(72% 0.15 75);
--danger:  oklch(62% 0.17 25);
```

---

## 四、各页面详细要求

### 4.1 layout.tsx —— 侧边栏 shell

- 220px 固定宽侧边栏 + 右侧 `<main>` flex 布局
- 顶部：项目名 "LeagueNews 管理"
- 导航分三组（内容 / 运营 / 系统），参考 `docs/admin-redesign.md` 第二节
- "审核中心"导航项旁边的 badge 从 `GET /workflows/reviews?status=pending` 和 `GET /event-workflows/reviews?status=pending` 两个接口的 count 相加，server component 里 fetch
- 底部健康状态：failed pipeline jobs > 0 时显示红点，否则显示绿勾
- 激活项左边 2px `--accent` 色条
- 移动端：侧边栏折叠为顶部 hamburger，小于 768px 时触发

### 4.2 pipeline/page.tsx —— 流水线监控

这是核心页，参考 `docs/admin-redesign.md` 第三节，需要实现：

**数据接口：**
- `GET /raw-items`（返回 100 条，含 `processing_status` 和关联 `processing_runs`）
- `GET /pipeline/jobs`（取 active/failed jobs 的 current_stage）
- `GET /pipeline/corrections`（用于显示重跑记录）

**顶部统计条：** 总计 / 待处理 / 失败 / [刷新] 按钮

**筛选栏（client 端过滤）：**
- 来源多选下拉（从数据中提取 source 列表）
- 状态单选：全部 / 完成 / 失败 / 待审核 / 进行中 / 未处理
- content_type 多选（`official_fact / official_notice / match_result / insider_rumor / insider_confirmed / data_mine / aggregation / community_noise / null`）
- 搜索框（对 title/summary debounce 300ms 过滤）

**表格列：** 来源 badge | 标题预览（截断 40 字）| 发布时间（相对时间 + hover 绝对时间）| 处理进度条 | 操作按钮

**PipelineStageBar 组件规格：**

管线 8 个阶段（按此顺序；OCR 为可选阶段）：
`relevance → image_ocr → translation → fact_classify → importance → claim_gen → event_decision`

每个阶段一个 16px 圆形节点，节点间 2px 线条，整体宽度自适应。
节点 props：`status: 'pending' | 'running' | 'done' | 'failed' | 'review' | 'skipped'`

状态样式：
- `pending`: 空心圆，`--stage-pending` 色
- `running`: 实心圆，`--stage-running` 色，CSS 脉冲动画（`@keyframes pulse`，scale 1→1.3→1，1.2s 循环）
- `done`: 实心圆 `--stage-done`，圆内白色 ✓（12px）
- `failed`: 实心圆 `--stage-failed`，圆内白色 ✗（12px）
- `review`: 实心圆 `--stage-review`，圆内白色 ⏸（12px）
- `skipped`: 空心圆虚线边框，`--stage-skipped` 色

阶段状态从 `PipelineJob.current_stage` + `ProcessingRun.status` 组合推断：
- 若 job.status='completed' 且 run.status='approved'：全部 done
- 若 job.current_stage='event_decision' 且 run.status='awaiting_review'：该节点 review
- 若 run.status='failed'：对应阶段 failed，后续 pending
- 若 run.outcome='not_relevant'：relevance done，后续全部 skipped

节点 hover 显示 StageTooltip：阶段名（中文）+ 该阶段结果简要（从 ProcessingRun.context 里取）

**行操作按钮：**
- 完成：▶（查看详情）
- 失败：`重试`（调用 `POST /workflows/runs/{id}/retry`）
- 待审核：`审核`（跳转 /admin/reviews）
- 未处理：`处理`（调用 `POST /raw-items/{item_id}/process`）

**ExpandableRow 展开详情（参考 `docs/admin-redesign.md` 3.3 节）：**

点击行（或 ▶ 按钮）展开，下方插入 `ItemDetailCard`，展示：
- 原文摘要 + 来源 + 发布时间
- 各阶段详情列表（阶段名 + 状态图标 + 关键输出）：
  - relevance: `product_scope` + confidence
  - fact_classify: `content_type / topic / facts / entities`
  - 消息层不展示综合可信度；重要性展示编辑类型、规模、适用范围、赛区、知名度和信息增量
  - importance: 分值 + 编辑特征（从 `importance_dimensions` 取）
  - claim_gen: claim 数量 + 主谓摘要
  - event_decision: 归属事件名（link）或失败原因
- 归属事件列表（若有），每条显示 event_type badge + title + link
- 操作区：`[重跑 {stage}]` 下拉选择阶段后调用 `POST /pipeline/{item_id}/correct`；`[查看原始 JSON]` 展开 raw JSON

### 4.3 messages/page.tsx —— 消息管理

数据：`GET /normalized-items`（100 条）

**视图切换**（顶部 toggle）：列表视图 / 审阅视图

**列表视图：**
- 筛选：topic / content_type / 可信度区间滑动条 / 搜索
- 表格列：标题 | content_type badge | topic badge | 可信度（色块 + 数字）| 重要性（色块 + 数字）| 归属事件数 | 发布时间
- 行点击进入 `/admin/messages/[id]`

**审阅视图：**
- 左右两栏，左侧原文（`original_content_blocks` 渲染），右侧 normalized 结果
- 右侧显示：content_type / topic / ImportanceDimensions
- 底部：`[下一条]` `[标记有问题（触发 correction）]`

### 4.4 messages/[id]/page.tsx —— 单条消息详情

server component fetch `GET /normalized-items/{id}/published`，页面分区展示：
1. 标题区：badges（content_type / topic / lifecycle）+ 基本元数据
2. 原文内容块（文字段落 + 图片 + 嵌入）
3. 翻译结果（若有）
4. ImportanceDimensions 组件展开编辑特征
6. Claim 列表（fact_claims 谓词 + attribution）
7. 归属事件列表（含 membership_role badge）
8. 操作区（重跑阶段 / 手动归属事件弹窗）

### 4.5 events/page.tsx —— 事件管理

数据：`GET /events`（100 条）

**视图切换**：列表 / 时间线 / 多归属

**列表视图：** 按 event_type 分组折叠，每组 header 显示类型名 + 数量，每行显示 lifecycle badge + credibility badge + 消息数 + 独立信源数 + link

**时间线视图（EventTimeline 组件）：**
- 顶部筛选覆盖后端全部时间线类型：`transfer_saga / patch_cycle / release_saga / dev_preview / incident / qualification_saga`
- 纵向时间轴，每条消息一个节点，节点显示：时间 | update_kind badge | 来源 | timeline_note；原消息作为证据展开
- 节点图标按 `evidence_stance`：supports（绿 +）/ contradicts（红 -）/ context（灰 ○）
- 轴末尾若 lifecycle=unconfirmed 且 last_published 距今 >3 天，显示"等待确认 · X 天"+ `[标记过期]` 按钮
- 轴底显示事件综合状态行

**多归属视图（MultiMembershipView）：**
- 仅显示有 component 角色 EventMessage 的消息
- 三列对照：消息标题 | 事件 A（primary）| 事件 B（component）
- 每格显示 event_type badge + 事件 title + link
- `[解除关联]` 按钮（调用撤回接口）

### 4.6 events/[id]/page.tsx —— 事件详情

server component fetch `GET /events/{event_id}`，展示：
1. 顶部：event_type + lifecycle + credibility 三个 badge + 标题 + aggregation_key
2. 摘要 + latest_development
3. EventTimeline（复用组件，单事件模式）
4. 成员消息列表（含 membership_role / evidence_stance / source_reliability_snapshot）
5. 修订历史（EventRevision 列表，折叠显示）
6. 可信度分解卡：正向信源列表（strength）/ 负向信源列表 / 合并结果公式展示
7. 操作：修改 lifecycle 下拉 / 更新摘要 textarea / 触发重新聚合按钮

### 4.7 reviews/page.tsx —— 审核中心

数据：`GET /workflows/reviews?status=pending` + `GET /event-workflows/reviews?status=pending`

三队列顶部 tab 切换：消息分析待审 | 事件归属待审 | OCR 待验

**消息分析待审（ReviewCard 组件）：**
- 按 importance_score 倒序
- 每张卡片：原文摘要 + classify 结果 + importance + claims
- 操作：`[批准]`（`POST /workflows/reviews/{id}/approve`）/ `[修正后批准]`（展开编辑表单，字段：content_type / importance_score / 备注）/ `[拒绝]`（`POST /workflows/reviews/{id}/reject`）

**事件归属待审：**
- 展示 LLM 的 `decision_draft.memberships[]` 归属建议
- 并排显示候选事件卡片（event_type / title / lifecycle / 消息数）
- 操作：`[批准建议]` / `[修改归属]`（弹窗选择或新建事件）/ `[拒绝]`

**OCR 待验：**
- 原图 + OCR 文本并排
- inline 可修正文字
- `[确认]` `[修正提交]`

### 4.8 collection/page.tsx —— 数据采集

数据：`GET /sources`（现有路由）+ `GET /connectors/runs`（最近 20 条）+ `GET /collection-schedules`

**来源状态表（SourceStatusRow）：**
- 列：来源名 | 类型 badge | 上次采集（相对时间）| 本周消息数 | 操作
- 操作：`[立即采集]`（`POST /collection-schedules/{source_id}/run`）
- 若上次采集超过 2 小时，时间显示橙色警告色

**采集日志：** 最近 20 条 ConnectorRun，每行显示时间 + 来源 + 状态图标 + 新增条数

### 4.9 system/page.tsx —— 系统运维

数据：`GET /pipeline/jobs` + `GET /pipeline/corrections`

**Pipeline 队列统计卡片行：** 运行中 / 等待 / 完成 / 失败 四个 count 卡片

**失败任务列表（PipelineJobRow）：** 只显示 status=failed 的，列：Job ID | raw_item_id | 失败阶段 | 错误摘要（截断）| 操作（重试 / 查看错误展开）

**Corrections 列表：** restart_from_stage / resume_mode / reason / status / 时间

### 4.10 system/ocr/page.tsx 和 system/knowledge/page.tsx

直接从现有 `admin-console.tsx` 的 `ocr` tab 和 `knowledge` tab 提取对应 JSX，迁移为独立页面，保持现有功能不变。

---

## 五、全局约束

1. **类型安全**：所有 API 响应类型从 `lib/types.ts` 扩展，不用 `any`。新增的字段（如 `content_type`、`aggregation_key`、`membership_role`）加到对应类型里，允许 `| null | undefined`（兼容历史数据）。

2. **错误处理**：每个页面的 fetch 失败时显示带 `[重试]` 按钮的错误态，不阻塞整页；多个 fetch 并行失败时各自独立降级。

3. **加载态**：用 skeleton 替代 spinner，skeleton 形状匹配最终布局（表格行用灰色条，卡片用灰色矩形）。

4. **可访问性**：表格用 `<table>/<th>/<td>`；badge 不能只靠颜色区分，加文字或 icon；操作按钮有 `aria-label`；focus-visible ring 用 `--accent` 颜色，不依赖浏览器默认。

5. **响应式**：768px 以下侧边栏折叠；表格超宽时列可隐藏（优先级：来源 > 状态 > 进度 > 时间 > 操作）。

6. **数字格式**：可信度/重要性均为 0-1 的小数，显示为百分比（如 `62%`）或两位小数（如 `0.62`），全站统一一种，推荐两位小数；所有对齐数字用 `font-variant-numeric: tabular-nums`。

7. **操作反馈**：所有 POST 操作期间按钮显示 loading spinner 并 disabled；成功后局部刷新数据（不刷整页）；失败后在操作处显示内联错误消息（而非 alert）。

8. **旧组件保留**：`components/admin-console.tsx` 和 `app/admin/page.tsx`（旧的）保持不变，原来的管理台仍可通过路由访问（若原路由是 `/admin`，新 layout 接管后原组件在新 layout 里仍渲染，不破坏现有功能）。

9. **不引入新依赖**：所有 UI 用现有 Tailwind v4 + lucide-react + 手写 CSS 实现，不装 shadcn、radix、headlessui 等。

---

## 六、落地顺序（按此顺序实现，每步可独立验收）

1. CSS token 扩展（globals.css）
2. `PipelineStageBar` + `StageTooltip` 组件（含 mock 数据的 storybook 式测试页，可选）
3. `AdminLayout` + `SideNav` + 路由 skeleton（各页面先用 placeholder）
4. 流水线监控页完整功能（`ExpandableRow` + `ItemDetailCard`）
5. 消息管理页列表视图 + 单条详情页（`ImportanceDimensions`）
6. 事件管理页（`EventTimeline` + `MultiMembershipView`）
7. 审核中心页（`ReviewCard`）
8. 数据采集页 + 系统运维页（`SourceStatusRow` + `PipelineJobRow`）
9. OCR 测试台 + 知识库迁移
10. 全局打磨：空态、错误态、移动端折叠、数字格式统一

---

## 七、验收标准

完成后给我：
1. 新增/修改的文件列表
2. 每个页面路由可正常访问的截图或文字确认
3. 流水线监控页：对本地库一条真实 raw_item，能看到其 8 阶段进度条，各节点状态正确（尤其是 failed 节点显示红色 ✗，waiting review 节点显示橙色 ⏸）
4. 事件管理时间线视图：WBG 打野传闻的多条消息能在一条时间线上显示（若已聚合为 transfer_saga）
5. TypeScript 编译无 error（`tsc --noEmit`）
6. 旧 `/admin` 路由仍可访问，原有功能未损坏
