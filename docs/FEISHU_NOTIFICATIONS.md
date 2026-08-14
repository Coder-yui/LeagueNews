# 飞书通知系统

## 架构

飞书通知是 LeagueNews 的旁路输出，不经过 MCP，也不改变 MCP 的只读边界：

```text
LeagueNews Core
  ├── Web/API
  ├── MCP（只读）
  └── notification_outbox
        └── collection-scheduler 内的 notification dispatcher
              └── FeishuBotClient
                    ├── 精选消息机器人
                    └── 系统失败告警机器人
```

业务代码只记录通知事实，outbox 负责 durable persistence，dispatcher 负责租约、发送和重试，
Feishu adapter 负责签名、HTTP 和卡片展示。Webhook 和 secret 只注入真正发送通知的
`collection-scheduler`，不会注入 Web、MCP 或 `pipeline-worker`。

## 两个机器人

### 精选消息机器人

精选消息在 `reviewed_pipeline._apply_normalized_item()` 完成 `NormalizedItem` 发布后入队。
判断严格复用 `app.domain.importance.is_featured_message()` 和当前 `FEATURED_MESSAGE_MIN_IMPORTANCE`，
不使用 `priority_score`，不调用 LLM，也不维护第二套阈值。

通知使用 `featured:{normalized_item_id}` 去重。一条消息初次发布为普通消息时不会入队；之后合法
correction 首次达到现有精选规则时可以入队。已经入队或发送过的精选消息不会因为 pipeline retry、
worker 重启或普通 revision 再次刷屏。

卡片只展示标题、摘要、来源、产品、消息类型、重要性、发布时间，以及可选的作者和 topics。完整
消息按钮进入 `{PUBLIC_ORIGIN}/messages/{normalized_item_id}`；原始 JSON、prompt、checkpoint 和
provenance 不会进入精选卡片。

### 系统失败告警机器人

告警只针对真正的失败：

- collection scheduler 将采集计划落为 `failed` 后入队，覆盖 X、微博、百度贴吧及其他 Connector；
  手工触发 Connector 失败也在 API 的统一失败响应边界补充告警。
- Pipeline Worker 将 `PipelineJob.status` 最终持久化为 `failed` 后入队，阶段来自当前
  `job.current_stage`，包括 OCR、翻译、消息分析、重要性和事件聚合。

X/微博的认证、配置、限流和上游拒绝优先使用现有异常类型分类；无法可靠判断时使用
`collection_failed`。告警包含信源、connector、source ID、错误类别、连续失败次数、错误摘要、
运行 ID、RawItem/PipelineJob/ProcessingRun/Checkpoint ID 和时间。

`irrelevant`、`insufficient_evidence`、`awaiting_review`、`cancelled`、正常 event admission reject、
没有新消息都不告警。事件聚合失败只会使下游 PipelineJob 失败，不会回滚已经发布的 `NormalizedItem`。

## Outbox、去重与 cooldown

`notification_outbox` 是追加式迁移 `070_add_notification_outbox.sql` 创建的通用表。它保存
`target`、`kind`、业务 payload、唯一 `dedupe_key`、状态、尝试次数、下次尝试时间、最后错误和发送租约。

- 精选消息使用 `featured:{normalized_item_id}`。
- Pipeline failure 使用 `pipeline_failure:{pipeline_job_id}`。
- collection failure 使用 `source + error_kind + cooldown bucket`，并在入队前检查最近发送/待发
  记录。同一 source、同一 error kind 默认 60 分钟内只产生一条提醒；不同 source 不互相抑制。

dispatcher 使用 PostgreSQL `FOR UPDATE SKIP LOCKED` claim，状态为 `sending` 的租约过期后可被
其他 worker 恢复。成功后标记 `sent`；网络错误、非 2xx、非 JSON 或飞书业务错误码都会进入 `failed`
并按 30 秒、2 分钟、10 分钟、30 分钟的指数退避序列重试。dispatcher 发送失败只更新 outbox 和日志，
不会再产生“飞书发送失败”的飞书告警，因此不会递归。

## 环境变量

```text
FEISHU_FEATURED_PUSH_ENABLED=false
FEISHU_FEATURED_WEBHOOK_URL=
FEISHU_FEATURED_SECRET=

FEISHU_ALERT_PUSH_ENABLED=false
FEISHU_ALERT_WEBHOOK_URL=
FEISHU_ALERT_SECRET=

FEISHU_NOTIFICATION_POLL_SECONDS=5
FEISHU_NOTIFICATION_LEASE_SECONDS=120
FEISHU_ALERT_COOLDOWN_MINUTES=60
PUBLIC_ORIGIN=https://news.example.com
```

`FEISHU_*_PUSH_ENABLED=true` 时必须同时配置对应 webhook URL；secret 在飞书机器人开启签名校验
时填写，否则留空。两个 URL 和 secret 必须完全分开，不能把精选机器人地址配置到告警变量，反之亦然。

API 和 pipeline-worker 只需要 `PUBLIC_ORIGIN` 与两个 enabled flag；只有 collection-scheduler 获得
两个 webhook URL、secret 和 dispatcher 参数。`.env.example`、`.env.preview.example`、
`.env.production.example` 和 `deploy/docker-compose.prod.yml` 已包含配置骨架。

## 创建与部署

1. 在飞书群聊中分别创建两个“自定义机器人”，分别命名为“LeagueNews 精选”和“LeagueNews 告警”。
2. 按飞书安全设置决定是否开启签名校验；开启时保存每个机器人的独立 secret。
3. 将精选机器人 webhook 填入 `FEISHU_FEATURED_WEBHOOK_URL`，将告警机器人 webhook 填入
   `FEISHU_ALERT_WEBHOOK_URL`，不要提交到 Git。
4. 在服务器 `.env` 设置 `PUBLIC_ORIGIN`、两个 enabled flag 和对应 secret，然后执行正常迁移与部署：

   ```bash
   docker compose -f deploy/docker-compose.prod.yml run --rm migrate
   docker compose -f deploy/docker-compose.prod.yml up -d
   ```

迁移是 append-only；不要修改历史 migration。dispatcher 与 collection scheduler 同进程运行，
不需要新增容器、Redis、Celery 或消息队列。

## 手动验证

先在非生产或允许测试通知的群里配置两个机器人：

1. 将两个 enabled flag 设为 `true`，重启 scheduler、pipeline-worker 和 API 使布尔配置生效。
2. 让一条重要性达到当前精选阈值的消息完成发布，确认精选机器人收到卡片并能打开消息详情。
3. 对一个测试 Source 执行手工采集并让 Connector 抛出配置/网络错误，确认告警包含 ConnectorRun ID。
4. 对一条测试 RawItem 触发一个会失败的处理阶段，确认告警阶段显示正确；重复执行同一失败 job
   不应产生第二条 pipeline 告警。
5. 临时断开 webhook 网络，确认 outbox 进入 `failed`、`next_attempt_at` 后重试；恢复网络后应变为
   `sent`。排查时只查看状态、kind、attempts 和 last_error，不要把 webhook URL 或 secret 写入日志。

## 能力边界

这是应用内的 durable notification system，不是主机级 uptime monitoring。整台服务器宕机、
`collection-scheduler` 容器完全没有运行、数据库彻底不可用，或服务器无法访问互联网时，
LeagueNews 自己可能无法发出飞书告警。这些场景属于未来外部 uptime monitoring 的职责，本次不引入
复杂外部监控。
