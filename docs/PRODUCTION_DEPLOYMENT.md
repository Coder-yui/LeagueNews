# 生产部署手册

本项目的第一版生产架构面向一台 Linux 主机：

- Caddy 负责公网 80/443、HTTPS、压缩、安全响应头和管理端 Basic Auth。
- Next.js、FastAPI、自动管线 Worker、采集调度器分别运行。
- PostgreSQL、API 和 Web 不直接暴露宿主机端口。
- PostgreSQL 数据、媒体文件和 Caddy 证书使用 Docker Volume 持久化。
- X Cookie 和微博浏览器 Profile 从服务器 `.secrets` 目录挂载，不进入 Git。

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

如果暂时没有域名，先使用
[`GOOGLE_CLOUD_FIRST_DEPLOY.md`](GOOGLE_CLOUD_FIRST_DEPLOY.md) 中的 SSH 隧道预发布方案，
不要把管理台通过纯 HTTP 暴露到公网。

## 2. 创建生产配置

```bash
git clone <your-github-repository-url> league-news
cd league-news
cp .env.production.example .env.production
mkdir -p .secrets/weibo-browser-profile
touch .secrets/x-cookies.json
printf '[]\n' > .secrets/weibo-cookies.json
chmod 700 .secrets .secrets/weibo-browser-profile
chmod 600 .env.production .secrets/x-cookies.json .secrets/weibo-cookies.json
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
- 除公开健康检查、已发布消息和事件以外的全部 `/api` 请求。

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
- `.secrets/weibo-browser-profile`。

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

如果不恢复 dump，迁移容器会用当前 SQLAlchemy 模型创建一套空数据库、登记历史迁移，
并建立生产 OCR Profile；信源和采集周期需要重新在管理台配置。

## 4. 微博与 X 会话

Linux 生产镜像使用 Playwright Chromium，而不是本地 Edge。直接复制浏览器 Profile
有可能因为平台安全策略失效，上线前必须实际运行一次微博采集验证。

如果微博要求重新登录，建议先在受控的 Linux 图形环境中完成 Profile 登录，再复制到
服务器 `.secrets/weibo-browser-profile`。不要把 Profile、Cookie 或截图提交到 Git。

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

1. 首页、消息详情、事件列表和事件详情匿名可访问。
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

## 8. 回滚

应用回滚通过恢复上一个 `IMAGE_TAG` 完成。若新版本包含数据库迁移，不要直接回滚数据库；
先确认迁移是否向后兼容。发生数据问题时停止 Worker 和调度器，保存现场备份，再使用经过
验证的 dump 恢复。
