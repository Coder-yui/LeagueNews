# 管理台重设计方案

> 基于当前项目状态：Next.js 15 App Router、Tailwind v4、lucide-react、
> 3100 行单文件 `admin-console.tsx`、14 个 API 路由端点、
> 609 条已处理 raw_items，以及 pipeline-redesign.md 落地后的新管线阶段。
>
> 目标：把"一个巨型 tab 组件 + 首屏加载 15 个接口"改造成
> "侧边栏多路由 + 按需加载 + 每条消息处理进度可视化"。

---

## 一、整体路由架构

```
app/admin/
├── layout.tsx              ← 持久侧边栏 shell（跨页不刷新）
├── page.tsx                ← redirect → /admin/pipeline
├── pipeline/
│   └── page.tsx            ← 流水线监控（默认首页）
├── messages/
│   ├── page.tsx            ← 消息列表
│   └── [id]/page.tsx       ← 单条消息详情
├── events/
│   ├── page.tsx            ← 事件列表
│   └── [id]/page.tsx       ← 单条事件详情（含时间线）
├── reviews/
│   └── page.tsx            ← 审核中心（三队列）
├── collection/
│   └── page.tsx            ← 数据采集（sources / connectors）
└── system/
    ├── page.tsx            ← 系统运维（jobs / corrections）
    ├── ocr/page.tsx        ← OCR 测试台
    └── knowledge/page.tsx  ← 知识库 & 术语
```

每个页面只加载自己需要的接口。`layout.tsx` 只加载顶部健康摘要
（pending 审核数、failed job 数），不加载业务数据。

---

## 二、侧边栏导航设计

```
┌────────────────────┐
│ LeagueNews  ·  管理 │
├────────────────────┤
│ ⬡ 流水线监控        │  /admin/pipeline
│                    │
│ 内容                │
│   📄 消息管理       │  /admin/messages
│   🔗 事件管理       │  /admin/events
│                    │
│ 运营                │
│   ✅ 审核中心  [3]  │  /admin/reviews    ← badge 显示 pending 数
│   📡 数据采集       │  /admin/collection
│                    │
│ 系统                │
│   ⚙️ 系统运维       │  /admin/system
│   🔬 OCR 测试台     │  /admin/system/ocr
│   📚 知识库         │  /admin/system/knowledge
├────────────────────┤
│ ● 健康状态          │  ← 底部：failed job 红点 / 全绿勾
└────────────────────┘
```

侧边栏宽度 220px，激活项左边有 2px accent 条，非激活项 hover 有浅色背景。
移动端侧边栏折叠为顶部 hamburger 菜单。

---

## 三、流水线监控页（核心页）

### 3.1 页面结构

```
┌─────────────────────────────────────────────────────────────┐
│ 流水线监控          总计 609    待处理 12    失败 3   [刷新]  │
├─────────────────────────────────────────────────────────────┤
│ 筛选: [全部来源 ▾] [全部状态 ▾] [content_type ▾] [搜索...]  │
├──────┬──────────────────┬──────────┬─────────────────┬──────┤
│ 来源  │ 标题预览          │ 发布时间  │ 处理进度         │ 操作 │
├──────┼──────────────────┼──────────┼─────────────────┼──────┤
│ 微博  │ 2026LPL第三赛段... │ 8/1 15:00│ ●●●●●●●●○○ 8/10│  ▶   │
│ TW   │ Mythic Shop...    │ 8/1 14:30│ ●●●●●●●●●● 完成  │  ▶   │
│ 微博  │ 关于WBG人员变动... │ 8/1 13:00│ ●●●●●●✗  失败   │ 重试  │
│ 贴吧  │ 8.3神话商城每日...  │ 8/1 12:00│ ●●●●⏸ 待审核    │ 审核  │
└──────┴──────────────────┴──────────┴─────────────────┴──────┘
```

### 3.2 进度条组件（PipelineStageBar）

管线 8 个阶段按 pipeline-redesign.md §9.3 的新顺序排列，
每个阶段一个圆形节点，节点之间用连线连接：

```
rel → ocr → trans → cls → cred → imp → claim → event
 ●     ●      ●      ●     ✗
```

节点状态与颜色（使用 CSS token，支持深色模式）：

| 状态 | 颜色变量 | 图标 | 含义 |
|---|---|---|---|
| pending | `--muted` | ○ 空圆 | 未开始 |
| running | `--blue` 动态脉冲 | ⬤ 实圆 | 进行中 |
| done | `--success`（绿） | ✓ | 完成 |
| failed | `--danger`（红） | ✗ | 失败，hover 显示错误 |
| review | `--warning`（橙） | ⏸ | 等待人工审核 |
| skipped | `--muted` 虚线节点 | — | 跳过（如 relevance=false） |

节点 hover 显示 tooltip：阶段名 + 耗时 + 简短结果摘要。

### 3.3 行展开详情（inline，不跳页）

点击任意行展开，在原行下方插入详情卡：

```
┌─────────────────────────────────────────────────────────────┐
│ ▼ 关于WBG人员变动...                              [收起]     │
├─────────────────────────────────────────────────────────────┤
│ 原文摘要: "昨天没一个人猜对..."                              │
│ 来源: 召唤师Park (weibo) · 2026-08-01 13:00                 │
│                                                             │
│ 阶段详情:                                                   │
│   relevance    ✓  lol_esports  0.95                        │
│   translation  ✓  已翻译 (ZH→ZH)                           │
│   classify     ✓  insider_rumor / roster                   │
│   credibility  ✓  0.62                                     │
│                   来源 0.75 × 措辞 0.80 × 类型 0.65 × 1.0  │
│   importance   ✓  0.45  (scope:3 mag:2 act:2 dur:1 nov:3) │
│   claim_gen    ✓  2 条断言: considered_for(WBG:jungle)      │
│   event        ✗  IntegrityError: membership conflict       │
│                                                             │
│ 归属事件: 传闻·WBG打野转会2026 (transfer_saga)  [查看事件]  │
│                                                             │
│ [重跑 event_decision]  [手动归属]  [查看原始 JSON]           │
└─────────────────────────────────────────────────────────────┘
```

重跑按钮调用 `POST /pipeline/{item_id}/correct`，
传入 `restart_from_stage: "event_decision"`。

### 3.4 筛选与排序

- 来源筛选：多选下拉，每个 source 显示消息数
- 状态筛选：全部 / 完成 / 失败 / 待审核 / 进行中 / 未处理
- content_type 筛选：新管线字段，双轴可见性的入口
- 搜索：对 title / summary 的前端 debounce 过滤
- 排序：发布时间倒序（默认）/ 重要性倒序 / 处理时间

---

## 四、消息管理页

### 4.1 列表视图（默认）

```
筛选: [topic ▾] [content_type ▾] [可信度 0-1] [lifecycle ▾] [搜索]

┌──┬──────────────────────┬─────────────┬──────┬──────┬─────┐
│  │ 标题                  │ content_type │ 可信度│ 重要性│ 事件 │
├──┼──────────────────────┼─────────────┼──────┼──────┼─────┤
│  │ 2026LPL W2D1...      │ match_result │ 1.00 │ 0.55 │  2  │
│  │ 传闻:WBG打野考虑...   │ insider_rumor│ 0.62 │ 0.45 │  1  │
│  │ 格温花仙子皮肤展示     │ official_fact│ 1.00 │ 0.72 │  1  │
└──┴──────────────────────┴─────────────┴──────┴──────┴─────┘
```

行点击进入单条消息详情页。

### 4.2 审阅视图（切换）

两栏布局，左侧原文（含图片），右侧 normalized 结果。
适合批量校验 classify 阶段输出质量：

```
┌──────────────────────┬──────────────────────────┐
│ 原文                  │ 分析结果                  │
│                      │                          │
│ [来源 badge]          │ content_type: insider_rumor│
│ 召唤师Park            │ topic: roster             │
│ 2026-08-01 13:00     │ credibility: 0.62         │
│                      │   来源:   0.75            │
│ "关于WBG人员变动，    │   措辞:   0.80            │
│ 昨天没一个人猜对..."  │   类型:   0.65            │
│                      │ importance: 0.45          │
│                      │ 归属事件: transfer_saga    │
│                      │                          │
│                      │ [下一条] [标记有问题]      │
└──────────────────────┴──────────────────────────┘
```

### 4.3 单条消息详情页 `/admin/messages/[id]`

完整展示：
- 原文内容块（文字 + 图片 + 嵌入）
- 翻译结果
- 双轴分类（content_type + topic）
- 可信度四因子展开
- 重要性五维展开
- Claim 列表（fact_claims + attribution）
- 归属事件列表（含 membership_role）
- 处理历史（ProcessingCheckpoint 时序）
- 操作区：重跑指定阶段 / 修正内容 / 手动归属事件

---

## 五、事件管理页

### 5.1 列表视图（默认）

按 event_type 分组展示，每组可折叠：

```
▼ transfer_saga (4)
  WBG打野转会2026赛季        unconfirmed  cred:0.71  3信源  3消息  [详情]
  Bin回归BLG传闻             unconfirmed  cred:0.55  1信源  2消息  [详情]

▼ patch_cycle (2)
  v26.15版本更新             completed    cred:1.00  4信源  8消息  [详情]
  v26.14版本更新             completed    cred:1.00  2信源  5消息  [详情]

▼ daily_matches (7)
  LPL · 2026-08-01          completed    cred:1.00  5信源  3消息  [详情]
  LCK · 2026-07-31          completed    cred:1.00  3信源  2消息  [详情]

▼ shop_rotation (2)
  神话商城第32周             live         cred:0.60  2信源  5消息  [详情]
```

筛选：event_type / lifecycle_status / credibility_status / 日期范围

### 5.2 时间线视图（切换，transfer_saga / patch_cycle 专用）

```
WBG 打野转会 · 2026赛季
aggregation_key: WBG:jungle:2026off-season

──────────────────────────────────────────────────────────
  8/1 13:00  [insider_rumor]  召唤师Park
             "有变动，下午说细节"
             cred 0.55  considered_for(WBG:jungle)

  8/1 17:00  [insider_rumor]  _尧阿尧y_
             "候选 Beichuan/蔻蔻/RE0/xiaofang"
             cred 0.62  considered_for(WBG:jungle)  ← 独立信源 +1

  8/2 10:00  [insider_confirmed]  召唤师Park
             "TES/WBG互换理论可行"
             cred 0.68  considered_for(WBG:jungle, TES:jungle)

             ⋯⋯ 等待官方确认 · 距今 3 天 ⋯⋯  [标记过期]
──────────────────────────────────────────────────────────
  当前状态: unconfirmed  综合可信度: 0.71  独立信源: 3
```

每个节点可展开查看原文。时间线节点的图标反映 `evidence_stance`：
supports（➕）/ contradicts（➖）/ context（○）。

### 5.3 多归属视图（切换）

显示同时归属多个事件的消息，重点是 membership_role=component 的：

```
┌──────────────────┬─────────────────────────┬────────────────┐
│ 消息              │ 事件 A (primary)         │ 事件 B (component)│
├──────────────────┼─────────────────────────┼────────────────┤
│ 7/31不停机更新    │ v26.14版本更新(patch)    │ 经典模式上线    │
│ 格温皮肤展示      │ 花仙子皮肤系列(release) │ 格温单体发布   │
└──────────────────┴─────────────────────────┴────────────────┘
```

提供"解除关联"按钮，调用撤回 EventMessage 的接口。

### 5.4 单条事件详情页 `/admin/events/[id]`

- 顶部：event_type badge + lifecycle badge + credibility badge
- 摘要与最新动态
- 时间线（同 5.2 但单事件）
- 成员消息列表（含 membership_role / evidence_stance / credibility_components）
- 修订历史（EventRevision 列表）
- 可信度分解卡：positive（支持信源）/ negative（反驳信源）/ 合并结果
- 操作：修改 lifecycle / 更新摘要 / 添加消息 / 触发重新聚合

---

## 六、审核中心页

三个队列，顶部 tab 切换，按重要性倒序排列：

### 队列一：消息分析待审

来源：`GET /workflows/reviews?status=pending`

```
┌──────────────────────────────────────────────────────────┐
│ 消息分析待审 (3)                                          │
├──────────────────────────────────────────────────────────┤
│ 关于WBG人员变动...  insider_rumor/roster  重要性 0.45     │
│                                                          │
│ 原文 ────────────────  分析结果 ───────────────────────── │
│ "昨天没一个人猜对..."  classify: insider_rumor / roster   │
│                       credibility: 0.62                  │
│                       importance: 0.45                   │
│                       claims: considered_for(WBG:jungle) │
│                                                          │
│    [批准]    [修正后批准]    [拒绝]                        │
└──────────────────────────────────────────────────────────┘
```

修正后批准展开内联编辑表单（可修改 content_type / credibility / importance）。

### 队列二：事件归属待审

来源：`GET /event-workflows/reviews?status=pending`

展示 LLM 的 `memberships[]` 决策草案，并排显示候选事件供对比：

```
┌──────────────────────────────────────────────────────────┐
│ 归属决策待审                                              │
├───────────────────────┬──────────────────────────────────┤
│ 消息                   │ LLM 建议归属                     │
│ 格温花仙子皮肤展示      │ → 花仙子皮肤系列 (primary)       │
│ official_fact/skin     │ → 格温英雄发布 (component)       │
│ importance: 0.72       │                                  │
├───────────────────────┴──────────────────────────────────┤
│ 候选事件:                                                 │
│  [1] 花仙子皮肤系列  release_saga  confirmed  3消息       │
│  [2] 格温英雄发布    release_saga  confirmed  1消息       │
│  [3] 新建: 格温皮肤  release  (LLM 无此建议，可手动)       │
├──────────────────────────────────────────────────────────┤
│   [批准建议]  [修改归属]  [拒绝]                           │
└──────────────────────────────────────────────────────────┘
```

### 队列三：OCR 待验

来源：`GET /ocr-lab/runs`（取 pending review 的）

原图 + OCR 文本并排，可内联修正文字。

---

## 七、数据采集页

```
来源状态:
┌────────────────┬────────┬───────────┬──────────┬──────────┐
│ 来源名          │ 类型   │ 上次采集   │ 本周消息 │ 操作      │
├────────────────┼────────┼───────────┼──────────┼──────────┤
│ 召唤师Park      │ weibo  │ 10分钟前  │ 54 条    │ [立即采集]│
│ SkinSpotlights │ twitter│ 8分钟前   │ 49 条    │ [立即采集]│
│ lol半价吧       │ tieba  │ 2小时前   │ 54 条    │ [立即采集]│
└────────────────┴────────┴───────────┴──────────┴──────────┘

采集日志（最近 20 条 ConnectorRun）:
  8/1 15:32  weibo:英雄联盟赛事  ✓ 新增 3 条
  8/1 15:30  x_twitter:SkinSpotlights  ✓ 新增 1 条
  8/1 15:28  weibo:召唤师Park  ✗ 超时
```

---

## 八、系统运维页

### 8.1 Pipeline Jobs

```
Pipeline 队列:
  运行中 (1)  等待 (2)  完成 (95)  失败 (2)

失败任务:
  Job #342  raw_item:489  event_decision  [重试]  [查看错误]
  Job #301  raw_item:441  credibility     [重试]  [查看错误]
```

### 8.2 Pipeline Corrections

显示所有 PipelineCorrection 记录，按时间倒序，
每行显示：restart_from_stage / resume_mode / reason / status。

---

## 九、设计规范

### 9.1 颜色系统（基于项目现有 CSS token 扩展）

项目已有 `--ink / --muted / --paper / --panel / --line / --acid / --blue`，
在此基础上补充语义 token（加在 `:root` 里）：

```css
:root {
  /* 现有 */
  --ink: oklch(15% 0.01 250);
  --muted: oklch(55% 0.01 250);
  --paper: oklch(98% 0.005 250);
  --panel: oklch(94% 0.008 250);
  --line: oklch(87% 0.01 250);
  --accent: oklch(55% 0.18 250);   /* 原 --blue，更名语义化 */
  --acid: oklch(68% 0.22 130);     /* 保留 */

  /* 新增语义 token */
  --success: oklch(58% 0.16 145);
  --warning: oklch(68% 0.18 75);
  --danger:  oklch(55% 0.20 25);
  --info:    oklch(58% 0.15 240);

  /* Pipeline 阶段节点 */
  --stage-done:    var(--success);
  --stage-running: var(--accent);
  --stage-failed:  var(--danger);
  --stage-review:  var(--warning);
  --stage-pending: var(--line);
  --stage-skipped: var(--muted);
}

/* 深色模式调整 */
@media (prefers-color-scheme: dark) {
  :root {
    --ink:   oklch(92% 0.01 250);
    --muted: oklch(55% 0.01 250);
    --paper: oklch(12% 0.01 250);
    --panel: oklch(18% 0.01 250);
    --line:  oklch(28% 0.01 250);
    /* accent/success/warning/danger 稍降 chroma，略升 lightness */
    --success: oklch(65% 0.14 145);
    --warning: oklch(72% 0.15 75);
    --danger:  oklch(62% 0.17 25);
  }
}
```

### 9.2 间距与字型

- 间距单位：4px 基准，常用 4/8/12/16/24/32/48px
- 圆角：卡片 8px，按钮 6px，badge 4px，modal 12px
- 字体：系统字体栈，正文 14px/1.6，代码 13px
- 表格数字：`font-variant-numeric: tabular-nums`

### 9.3 交互状态

每个可交互元素必须有：
- hover：背景色变化，`transition: 120ms ease`
- active：scale(0.98) 或压暗
- focus-visible：2px `--accent` 环，不依赖浏览器默认
- disabled：opacity 0.4，cursor not-allowed
- loading：skeleton 而非 spinner（skeleton 形状匹配最终布局）

### 9.4 空态与错误态

- 列表为空：图标 + 说明文字 + 推荐操作按钮
- 加载失败：说明哪个接口失败 + [重试] 按钮，不阻塞其他面板
- 进行中的操作：按钮内 spinner + 禁用，避免重复提交

---

## 十、组件拆分清单

新建以下独立组件（`components/admin/` 目录）：

| 组件文件 | 用途 |
|---|---|
| `AdminLayout.tsx` | 侧边栏 + 顶部健康栏 shell |
| `SideNav.tsx` | 导航链接列表（含 badge） |
| `PipelineStageBar.tsx` | 8 节点进度条，接受 stages[] prop |
| `StageTooltip.tsx` | 节点 hover 详情 |
| `ExpandableRow.tsx` | 表格行展开/收起容器 |
| `ItemDetailCard.tsx` | 单条消息阶段详情卡（用于展开行和详情页） |
| `CredibilityBreakdown.tsx` | 四因子可信度可视化 |
| `ImportanceDimensions.tsx` | 五维重要性雷达/条形图 |
| `EventTimeline.tsx` | 时间线型事件纵向节点列表 |
| `MultiMembershipView.tsx` | 多归属消息对照表 |
| `ReviewCard.tsx` | 审核队列单条卡片（支持三种队列） |
| `SourceStatusRow.tsx` | 数据采集来源状态行 |
| `PipelineJobRow.tsx` | 系统运维 job 列表行 |

旧 `admin-console.tsx` **保留不删**，旧路由 `/admin`（通过 redirect）仍可访问，
逐步把各 tab 内容迁移到新路由后再统一删除。

---

## 十一、API 变更需求

现有 API 基本够用，但有两处缺口：

1. **`GET /raw-items` 需要返回处理进度**：
   当前返回的 `RawItemRead` 里已有 `processing_status`，
   但缺少当前进行到哪个 stage 的信息。
   建议在 `RawItemRead` 里补充 `current_pipeline_stage: str | None`，
   从关联的 active `PipelineJob` 取 `current_stage`。

2. **`GET /events` 需要支持 `event_type` 筛选**：
   当前无 query param 筛选，前端只能全量拉取再前端过滤（100 条上限也偏少）。
   建议加 `?event_type=transfer_saga&lifecycle_status=unconfirmed&limit=50&offset=0`。

这两个改动量极小，可以在前端开发时同步提 PR。

---

## 十二、落地顺序

1. `AdminLayout.tsx` + `SideNav.tsx` + 路由 skeleton（各页面先 placeholder）
2. `PipelineStageBar.tsx` + `ExpandableRow.tsx`（最高价值组件，单独可测试）
3. 流水线监控页（`/admin/pipeline`）完整功能
4. 消息管理页列表视图 + 单条详情页
5. 事件管理页列表视图 + 时间线视图
6. 审核中心页三队列
7. 数据采集页 + 系统运维页
8. OCR 测试台 + 知识库（从旧 admin-console 直接迁移）
9. 旧 `admin-console.tsx` 下线
