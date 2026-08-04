# LeagueNews 系统治理交付报告

日期：2026-08-03

## 已通过代码和测试验证

本次完成了采集可靠性、可恢复 Pipeline、并发约束、安全边界、CI/可观测性、Prompt/反馈
治理、受控 ontology/评分、Claim、事件召回、日报/周报、RSS 和只读 MCP 八个里程碑。
保留了 Connector/Source、RawItem 不可变、NormalizedItem revision、Event 独立层和
PostgreSQL 持久化队列等原架构边界。

- 采集使用持久化 cursor、重叠窗口和 capped-batch continuation；cursor 仅在 ingestion
  成功后推进，并记录 used/next cursor、truncated 和 candidate count。
- Pipeline Job 增加 worker、token、lease、heartbeat、recovery provenance；数据库 partial
  unique index 约束 active ProcessingRun 和 pending ReviewTask，并发冲突转为幂等结果或明确
  冲突。Event 更新在行锁后重新检查成员关系。
- API/Worker/Scheduler/Migrator 的环境变量和卷按职责拆分；Worker 无平台 Cookie/Profile，
  Scheduler 无 LLM key。媒体下载验证所有 DNS IPv4/IPv6 和每次 redirect；新 raw media 为
  private，发布时才获得 public projection。
- CI 在 PR/main 执行 Ruff、pytest、前端 lint/build、fresh DB 与 031 升级；镜像发布依赖
  quality job。运行元数据保存 prompt/model/schema/input hash/rule/glossary/usage/latency/
  retry/finish/error/commit/decision provenance。
- KnowledgeRule 使用 draft/evaluated/active/retired；拒绝和知识整理只产生 draft。规则晋升
  需要评测摘要；术语按文本命中，规则按上下文筛选，冲突可检测和导出。
- `lol-news-v1` 增加 primary topic、secondary topics、facets 和受控 entity types，旧
  category/entities 保留。模型只输出五个 0–4 维度，程序按
  `importance-v1-five-dimensions` 计算兼容的 importance_score。可信度拆为 source prior、
  来源角色、转载、证据、OCR/翻译组成；转载优先按 upstream URL 去重。
- Claim 保存 subject/predicate/object、before/after、time、stance/type、raw block evidence、
  model/schema/confidence/revision/provenance；EventClaim 支持一个 Claim 关联多个 Event，
  EventMessage 继续作为兼容投影。事件候选先用 key/category/time 索引收窄再混合评分。
- Digest 只读取时间窗内的 EventRevision，保存 cutoff/timezone/input revision/policy，
  重跑幂等，晚到或更正创建 DigestRevision。公开 API、页面与 RSS 只读取 published 数据。
  MCP 使用当前稳定 `2025-11-25` 协议，只有六个只读工具并返回结构化 provenance/source URL。

数据流：

```text
Connector -> RawItemCandidate -> ingestion -> immutable RawItem
  -> NormalizedItem + revision + ontology/score/credibility
  -> Claim(raw block evidence) <-> EventClaim <-> Event + EventRevision
  -> Digest + DigestRevision -> public pages/API + RSS
  -> read-only MCP: events/timeline/search/digests
```

## 新增 migration

1. `032_add_collection_cursors.sql`：cursor、overlap 和 batch provenance。
2. `033_add_pipeline_leases_and_item_constraints.sql`：job lease/recovery 与并发唯一约束。
3. `034_add_knowledge_rule_lifecycle.sql`：规则生命周期、评测与晋升 provenance。
4. `035_add_collection_failure_counter.sql`：连续失败计数。
5. `036_add_media_publication_boundary.sql`：private/published/legacy-public 媒体边界。
6. `037_add_ontology_claims_and_digests.sql`：ontology/评分/可信度字段、Claim/EventClaim、
   Digest/DigestRevision。

迁移 002–031 未修改。037 对旧 category 做保守确定性映射，旧媒体使用 `legacy_public`
兼容，不在 migration 中运行 LLM。历史 Claim 使用显式工具
`python -m scripts.backfill_claims`，默认 dry-run；只有备份和抽样确认后才使用 `--apply`。

## 验证记录

执行并通过：

```bash
services/api/.venv/bin/python -m ruff check services/api/app services/api/scripts services/api/tests
services/api/.venv/bin/python -m pytest services/api/tests -q
# 148 passed, 2 skipped；skip 是需要显式 PostgreSQL URL 的并发测试

EVENT_TEST_DATABASE_URL=<临时 PostgreSQL 17> \
  services/api/.venv/bin/python -m pytest \
  services/api/tests/test_event_aggregation_postgres.py \
  services/api/tests/test_pipeline_postgres.py -q
# 2 passed

pnpm lint:web
pnpm build:web
# ESLint 与 Next.js production build 通过；含 /digests 和 /digests/[id]

docker compose --env-file .env.production.example \
  -f deploy/docker-compose.prod.yml config --quiet
docker run --rm ... caddy:2.8-alpine caddy validate ...
git diff --check
# 均通过
```

另用全新临时 PostgreSQL 17 验证 fresh initialization，共记录 002–037 的 36 个版本；再对
031 fixture 顺序执行 032–037，全部通过。离线 evaluation fixture 为 3/3 exact match；
框架另有 Recall@5、false merge rate、false split rate 测试。X、微博、Riot 网页采集覆盖
cap continuation/边界/重试；SSRF 覆盖 loopback、metadata、IPv6、DNS 私网解析和 redirect；
Pipeline 覆盖 stale reclaim、双 worker、人工/自动竞态和暂时失败；分发测试覆盖 Claim
evidence、多事件关联、Digest 幂等/修订、RSS XML/稳定 GUID 和 MCP initialize/list/call。

只读 Docker 资源基线：本机镜像 1.314 GB、活跃 volume 122.7 MB、build cache 0；
PostgreSQL 17 Alpine 416 MB，Caddy Alpine 约 71–82 MB。本机无应用 image，因此未能测量
API/Chromium/OCR 实际分层；同一 API image 的多容器会共享只读层。

## 只能在生产环境验证

- 真实 X/微博平台分页行为、限流和长时间 scheduler heartbeat。
- 生产 DNS/TLS/Caddy、Basic Auth、CSP 与实际浏览器兼容性。
- 真实媒体域名的 DNS rebinding 风险和可选 allowlist。
- 真实 LLM provider 的 usage/finish reason 差异与长期成本。
- 真实历史数据迁移耗时、Claim backfill 抽样质量和磁盘增长。
- 日报 cutoff 调度、RSS reader discovery、MCP 客户端经反向代理的互操作性。
- 真实标注集上的 Recall@5、错误合并率、错误拆分率和重要性排序一致性。

## 当前风险与暂未自动执行

- 通用 DNS 校验与实际 socket 建连之间仍有 rebinding 时间窗；已明确记录，可对固定媒体平台
  增加 allowlist/固定解析策略。
- 为保持现有同步手工 Connector/OCR 管理能力，API 暂时仍需平台凭据和 LLM key；Worker 与
  Scheduler 已最小化。后续可把手工触发改成数据库采集请求。
- Basic Auth 适合单管理员；多管理员或外部写 API 出现前需应用内认证、Origin/CSRF、RBAC
  和不可变操作审计。
- 037 的历史 ontology 是确定性粗粒度映射；没有伪造 canonical identity。真实 ontology
  精修、Claim backfill 和 evaluation labels 均未自动写入。
- Digest 生成端点已实现，但生产 cutoff scheduler 未擅自配置。MCP Skill 只提供设计和安装
  指引，没有强行写入错误的运行时目录。
- 未加入 pgvector、Celery/Kafka/RabbitMQ、微服务或新云服务；向量召回需真实 Recall@5
  证据后再评估。

## 部署前项目所有者清单

1. 对 PostgreSQL 和媒体卷做可恢复备份，并完成一次隔离环境 restore test。
2. 审核所有 diff，特别是 Compose secret grants、Caddy public matcher 和 migrations 032–037。
3. 在 staging 的生产数据副本上运行 migration，检查锁时间、表/索引大小、旧 URL 和回滚
   预案；不要在 migration 中运行 Claim backfill。
4. 配置 lease/heartbeat、PUBLIC_ORIGIN、站点/Auth、分服务 secret；不要复制真实 secret
   到仓库或测试。
5. 部署后验收 health/ready/metrics、三类 Connector cursor continuation、worker stale
   recovery、人工/自动竞态、private media 404、撤回媒体不可访问、CSP 和 RSS XML。
6. 先 dry-run Claim backfill，抽样核对 raw block evidence；确认备份后分批 `--apply`。
7. 配置每日/每周 cutoff 调度，验证晚到消息产生 revision；接入 RSS reader 与 MCP client。
8. 建立磁盘、volume、备份年龄/恢复、TLS、容器重启、source failures/truncation、queue age、
   stale reclaim、LLM/OCR/media 指标告警。
9. 从真实审核历史安全导出并人工标注 evaluation set，在 Prompt/规则晋升前跑回归。

本次没有连接或修改生产服务器、生产数据库、DNS 或 GitHub Secrets；没有部署、提交、推送
或创建 PR，也没有读取或输出生产 Cookie、Token、密码。
