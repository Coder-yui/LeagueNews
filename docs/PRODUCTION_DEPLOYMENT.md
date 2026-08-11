# 生产部署手册

当前生产站点为 `https://leaguenews.me`。本文中的主机、账号和配置示例均不是生产秘密；
真实密码、Cookie、Token、IP 和私有配置不得写入仓库或运行日志。

本项目的第一版生产架构面向一台 Linux 主机：

- Caddy 负责公网 80/443、HTTPS、压缩、安全响应头和管理端 Basic Auth。
- Next.js、FastAPI、自动管线 Worker、采集调度器分别运行。
- PostgreSQL、API 和 Web 不直接暴露宿主机端口。
- PostgreSQL 数据、媒体文件和 Caddy 证书使用 Docker Volume 持久化。
- X Cookie、微博 Cookie 和浏览器运行目录从服务器 `.secrets` 挂载，不进入 Git。

## 1. 服务器和域名

推荐 Ubuntu 24.04 LTS。上线初期可以使用 2 核 CPU、4 GB 内存、40 GB SSD，并配置
4 GB Swap。生产镜像默认由 GitHub Actions 构建，服务器只下载运行；访问量、并发采集
或 OCR 任务增加后再升级到 4 核、8 GB。

安装 Git、Docker Engine 和 Docker Compose Plugin。开放入站端口：

- TCP 22：SSH，最好限制来源 IP。
- TCP 80：Caddy 申请证书和 HTTP 跳转。
- TCP/UDP 443：HTTPS/HTTP3。

不要开放 3000、5432、8000 或 pgAdmin 端口。

为 `SITE_ADDRESS` 配置指向服务器公网 IP 的 A/AAAA 记录。证书签发前 DNS 必须生效。

如果暂时没有域名，只通过 SSH 隧道把 Caddy 的本地监听端口映射到开发电脑；不要把管理台
通过纯 HTTP 暴露到公网。

## 2. 创建生产配置

```bash
git clone <your-github-repository-url> league-news
cd league-news
cp .env.production.example .env.production
mkdir -p .secrets/weibo-browser-profile
touch .secrets/x-cookies.json
printf '[]\n' > .secrets/weibo-cookies.json
chmod 700 .secrets .secrets/weibo-browser-profile
chmod 600 .env.production
chmod 644 .secrets/x-cookies.json .secrets/weibo-cookies.json
```

生成管理端密码哈希：

```bash
docker run --rm caddy:2.10.2-alpine \
  caddy hash-password --plaintext '替换为足够长的随机密码'
```

把结果填入 `ADMIN_PASSWORD_HASH`。Docker Compose 会解释 `$`，因此要把哈希中的每个
`$` 写成 `$$`。同时替换数据库密码、LLM 密钥、域名、邮箱和 User-Agent。

生产环境不会启动 API 文档，并且 Caddy 会保护：

- `/admin` 及其子路径；
- 除公开健康检查和已发布消息以外的全部 `/api` 请求。

这是一层适合单管理员上线初期使用的边界认证。以后需要多用户、操作审计或细粒度权限时，
应升级为应用内账号/RBAC。

## 3. 迁移现有数据

Git 不包含数据库业务数据、媒体文件或平台登录会话。正式上线应优先迁移当前验收数据库，
而不是创建空库。

在当前机器导出数据库：

```powershell
.\scripts\export-production-database.ps1
```

脚本让 `pg_dump` 在 PostgreSQL 容器内直接生成二进制 custom dump，再通过 `docker cp`
取回，避免 Windows PowerShell 重定向原生程序二进制输出时损坏 dump。输出默认位于
`backups/league-news-current-<时间>.dump`。

同时复制：

- `apps/web/public/media` 的全部内容；
- `.secrets/x-cookies.json`；
- 从已登录浏览器导出的 `.secrets/weibo-cookies.json`；
- `.secrets/weibo-browser-profile` 只作为云端 Chromium 的运行目录，不应被视为可移植的
  登录凭据。

在服务器只启动 PostgreSQL：

```bash
docker compose --env-file .env.production \
  -f deploy/docker-compose.prod.yml up -d postgres
```

把 dump 放到服务器，然后恢复：

```bash
CONFIRM_RESTORE=yes ./deploy/scripts/restore.sh /absolute/path/league-news-current.dump
```

首次启动后，把原媒体目录复制进生产 Volume：

```bash
docker compose --env-file .env.production \
  -f deploy/docker-compose.prod.yml run --rm \
  -v /absolute/path/to/exported-media:/import:ro \
  --entrypoint sh api \
  -c 'cp -a /import/. /data/media/'
```

如果不恢复 dump，迁移容器会用当前 SQLAlchemy 模型创建空数据库、登记历史迁移、建立
生产 OCR Profile，并创建当前 15 个内置信源。采集周期仍全部保持关闭，必须在管理台逐项
确认后启用。

## 4. 微博与 X 会话

Linux 生产镜像使用 Playwright Chromium，而不是本地 Edge。Chromium Profile 中的 Cookie
可能受操作系统或容器实例的加密保护，不能依赖直接复制 Profile。

先在本地使用专用 Profile 完成登录，然后关闭所有占用该 Profile 的 Edge 进程并导出：

```powershell
Set-Location E:\leagueNews\services\api
.venv\Scripts\python.exe -m scripts.export_weibo_cookies
```

把生成的 `.secrets/weibo-cookies.json` 上传到服务器同名位置，权限设为 `644`，使容器内
非 root 用户能够通过只读挂载读取；父目录仍保持 `700`。同时把本地登录时的完整
User-Agent 填入生产 `WEIBO_BROWSER_USER_AGENT`。容器每次启动浏览器上下文都会重新注入
Cookie，避免 API 与调度器容器各自的 Profile 加密状态不一致。

Cookie 文件属于账号凭据。不要打印内容、提交 Git 或长期放在 `/tmp`；迁移完成后删除中间
副本。上线前至少对一个微博 Source 执行低 `limit` 实际采集验证。

## 4.1 容器权限边界

- `pipeline-worker` 只接收数据库、媒体、LLM 和自身租约配置，不挂载 X/微博 Cookie 或
  浏览器 Profile。
- `collection-scheduler` 接收数据库、媒体和平台采集凭据，不接收 LLM API Key。
- `migrate` 只接收数据库、媒体路径和迁移目录。
- `api` 暂时同时保留 LLM 与 Connector 凭据，因为当前管理 API 仍同步支持人工 AI 流程和
  手工 Connector 运行。以后把手工采集改为持久化 collection request、由 Scheduler 执行后，
  应从 API 移除平台凭据。

## 5. 第一次启动

```bash
chmod +x deploy/scripts/*.sh
./deploy/scripts/deploy.sh
```

部署脚本默认依次执行配置校验、拉取 GitHub Container Registry 镜像、数据库迁移和服务
启动。只有显式设置 `DEPLOY_BUILD_LOCAL=true` 时才会在服务器本机构建。检查：

```bash
docker compose --env-file .env.production \
  -f deploy/docker-compose.prod.yml ps

docker compose --env-file .env.production \
  -f deploy/docker-compose.prod.yml logs --tail=200 migrate api web

curl -fsS "https://${SITE_ADDRESS}/api/v1/ready"
```

验收顺序：

1. 首页和消息详情匿名可访问。
2. `/admin` 会要求管理端用户名和密码。
3. 未认证的私有 API 返回 401。
4. 管理台能读取原有消息、知识、日志和采集周期。
5. 手动运行一个低风险信源，观察采集日志和自动管线日志。
6. 验证媒体图片、撤回、重新自动处理和人工处理。
7. 最后启用微博等周期采集。

## 6. 更新发布

每次上线前在 `.env.production` 增加 `IMAGE_TAG`，然后：

```bash
git pull --ff-only
./deploy/scripts/backup.sh
./deploy/scripts/deploy.sh
```

`migrate` 服务会在 API、Worker 和调度器之前运行。不要同时启动多个采集调度器或管线
Worker，除非已经针对相应任务做过并发和锁验证。

## 7. 备份和监控

数据库备份：

```bash
./deploy/scripts/backup.sh
```

用 cron 每日执行，并把 `backups` 和媒体文件同步到另一台机器或对象存储。仅在本机保留
备份不能防止整机或磁盘故障。至少每月在独立数据库做一次恢复演练。

上线后还需要接入外部监控，至少告警：

- `/api/v1/ready` 不可用；
- `api`、`pipeline-worker`、`collection-scheduler` 退出或频繁重启；
- 自动采集连续失败、微博/X 登录失效；
- 管线失败任务或积压持续增长；
- LLM 限流、余额和调用费用异常；
- PostgreSQL、Docker Volume 和系统磁盘空间不足。

应用的受保护 `/api/v1/metrics` 提供 Source 最近成功时间、连续失败数、truncated 采集次数、
Pipeline 状态计数、最老 queued 时间和 stale lease 回收数。外部监控还必须覆盖宿主机磁盘、
备份年龄、异地副本、容器重启、证书、公网可用性和定期恢复演练；这些不能由应用内指标
替代。

## 7.1 媒体公开边界与 SSRF

新采集媒体写入 `/data/media/private`，RawItem/管理台通过受 Basic Auth 保护的
`/api/v1/media-assets/files/...` 读取。只有 NormalizedItem 正式发布时才复制到
`/data/media/published` 并获得 `/media/published/...` URL；该 URL 由 API 根据
`MediaAsset.visibility` 校验后返回文件。消息撤回时事务内撤销公开状态，磁盘副本留待安全的
异步垃圾回收，因此即使文件仍存在也不能继续公开读取；private 原始证据始终用于审核和重放。

历史 `/media/...` URL 标记为 `legacy_public` 并继续可用，避免破坏已发布页面。它们是上线前
遗留兼容边界；后续只有在完成数据库到文件逐项核对和 URL 重写迁移后，才能收紧历史目录，
不得直接移动或删除。

远程媒体下载会拒绝 loopback、private、link-local、multicast、reserved 和云元数据地址，
请求前解析并检查域名返回的全部 IPv4/IPv6，关闭自动 redirect，并在每一跳重新解析验证。
HTTP 客户端仍可能在“应用解析检查”和“建立连接时内部再次解析”之间遇到 DNS rebinding
窗口。若运行环境面对不可信用户可控 URL，应进一步为每个 Connector 配置明确媒体域名
allowlist，或使用能把已验证 IP 固定到连接的 transport；不能把当前检查描述为完全消除
DNS rebinding。

## 7.2 管理认证升级条件

Basic Auth 只适合单管理员。出现第二位管理员、开放第三方写入、需要按 Source/审核类型分权、
或需要追责敏感操作时，必须升级为应用内认证、CSRF/Origin 校验、RBAC、会话撤销和不可变
操作审计；在此之前不要把管理 API 暴露给跨站脚本或公共客户端。

## 8. 回滚

应用回滚通过恢复上一个 `IMAGE_TAG` 完成。若新版本包含数据库迁移，不要直接回滚数据库；
先确认迁移是否向后兼容。发生数据问题时停止 Worker 和调度器，保存现场备份，再使用经过
验证的 dump 恢复。
