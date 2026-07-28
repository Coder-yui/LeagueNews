# Google Cloud 第一次部署：无域名安全预发布

这份说明适用于当前配置：

- Google Cloud Compute Engine
- Ubuntu 24.04
- 2 vCPU、4 GB 内存、40 GB 磁盘
- 暂时没有域名
- 网站只供自己验收，不直接暴露到公网

最终访问路径：

```text
你的浏览器 http://localhost:8080
  -> 本机 SSH 加密隧道
  -> Google Cloud 实例 127.0.0.1:8080
  -> Caddy -> Web / API
```

服务器只需要对公网开放 SSH。不要为这个预发布模式开放 80、443 或 8080。

## 第一阶段：先在 GitHub 生成镜像

在开发电脑将当前提交推送到 GitHub：

```powershell
git push origin main
```

然后打开 GitHub 仓库：

1. 点击 `Actions`。
2. 打开 `Build and publish production images`。
3. 等待任务显示绿色对勾。
4. 回到仓库首页，在右侧 `Packages` 应看到：
   - `leaguenews-api`
   - `leaguenews-web`

工作流会同时发布：

- `latest`：最新 main 版本。
- `sha-<完整提交号>`：可用于精确回滚。

如果镜像是公开的，服务器可以直接下载。如果镜像是私有的，需要在服务器登录 GHCR：

1. GitHub 头像 → Settings。
2. Developer settings → Personal access tokens → Tokens (classic)。
3. 创建只带 `read:packages` 权限的 Token。
4. 在服务器执行：

```bash
read -s GHCR_TOKEN
echo "$GHCR_TOKEN" | docker login ghcr.io -u Coder-yui --password-stdin
unset GHCR_TOKEN
```

不要把 Token 发到聊天、写进 `.env.production` 或提交到 Git。

## 第二阶段：Google Cloud 控制台设置

### 1. 设置预算告警

进入 Billing → Budgets & alerts，创建一个小额预算，例如 10 美元，并设置
50%、80%、100% 告警。预算告警不会自动停机，但能防止忘记资源持续计费。

### 2. 保留当前外部 IP

进入 VPC network → IP addresses，找到实例当前的临时外部 IPv4，将它提升为静态地址。
静态 IP 必须保持绑定实例；不要创建一个长期闲置、未绑定的静态 IP。

### 3. 检查防火墙

预发布阶段只保留 SSH 访问：

- 22/TCP：最好限制为你当前公网 IP。
- 不开放 80、443、8080、3000、8000、5432。
- 不勾选实例的 `Allow HTTP traffic` 和 `Allow HTTPS traffic`。

Google Cloud 网页里的 SSH 按钮可以用于安装服务器，但后面建立本机隧道需要电脑上的
OpenSSH 或 Google Cloud CLI。

## 第三阶段：初始化服务器

通过 Google Cloud 的 SSH 进入实例，先安装 Git：

```bash
sudo apt-get update
sudo apt-get install -y git
```

如果仓库是私有的，在服务器生成一个只读 Deploy Key：

```bash
ssh-keygen -t ed25519 -C "league-news-google-cloud" \
  -f ~/.ssh/league-news-deploy -N ""
cat ~/.ssh/league-news-deploy.pub
```

复制屏幕输出的整行公钥，然后：

1. 打开 GitHub 的 `Coder-yui/LeagueNews` 仓库。
2. Settings → Deploy keys → Add deploy key。
3. Title 填 `Google Cloud preview server`。
4. Key 粘贴刚才的整行公钥。
5. 不要勾选 `Allow write access`。
6. 点击 Add key。

回到服务器，固定这个仓库使用该密钥：

```bash
cat >> ~/.ssh/config <<'EOF'
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/league-news-deploy
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config ~/.ssh/league-news-deploy
ssh -T git@github.com
```

第一次连接会询问是否信任 GitHub 主机，核对显示的是 `github.com` 后输入 `yes`。
成功时 GitHub 会提示认证成功但不提供 shell，这是正常现象。

克隆并初始化服务器：

```bash
git clone git@github.com:Coder-yui/LeagueNews.git
cd LeagueNews
sudo ./deploy/scripts/bootstrap-ubuntu.sh
```

脚本会：

- 安装 Docker Engine 和 Compose Plugin。
- 启用 Docker 开机启动。
- 把当前 SSH 用户加入 docker 组。
- 创建并永久启用 4 GB Swap。

脚本完成后退出 SSH，再重新连接：

```bash
exit
```

重新连接后验证：

```bash
docker version
docker compose version
free -h
```

`free -h` 应显示约 4 GB 内存和 4 GB Swap。

## 第四阶段：创建预发布配置

进入项目目录：

```bash
cd ~/LeagueNews
cp .env.preview.example .env.production
mkdir -p .secrets/weibo-browser-profile
printf '{}\n' > .secrets/x-cookies.json
printf '[]\n' > .secrets/weibo-cookies.json
chmod 700 .secrets .secrets/weibo-browser-profile
chmod 600 .env.production
chmod 644 .secrets/x-cookies.json .secrets/weibo-cookies.json
```

生成数据库密码：

```bash
openssl rand -hex 24
```

保存输出，随后填入 `.env.production` 的 `POSTGRES_PASSWORD`。

生成管理台密码哈希：

```bash
docker run --rm caddy:2.10.2-alpine \
  caddy hash-password --plaintext '在这里填写你的管理台密码'
```

编辑配置：

```bash
nano .env.production
```

至少修改：

- `ADMIN_PASSWORD_HASH`
- `POSTGRES_PASSWORD`
- `OPENAI_API_KEY`
- `MODEL_NAME`
- `OPENAI_BASE_URL`

`ADMIN_PASSWORD_HASH` 中的每个 `$` 都要写成 `$$`，否则 Docker Compose 会把它当作变量。

预发布相关配置必须保持：

```dotenv
SITE_ADDRESS=:80
PUBLIC_ORIGIN=http://localhost:8080
BIND_ADDRESS=127.0.0.1
HTTP_PORT=8080
HTTPS_PORT=8443
DEPLOY_BUILD_LOCAL=false
```

保存 nano：按 `Ctrl+O`、回车，再按 `Ctrl+X`。

## 第五阶段：第一次启动

```bash
cd ~/LeagueNews
chmod +x deploy/scripts/*.sh
./deploy/scripts/deploy.sh
```

脚本会：

1. 校验 `.env.production`。
2. 从 GHCR 下载已经构建好的 API 和 Web 镜像。
3. 启动 PostgreSQL。
4. 初始化或迁移数据库。
5. 启动 API、Web、管线 Worker、采集调度器和 Caddy。

检查状态：

```bash
docker compose --env-file .env.production \
  -f deploy/docker-compose.prod.yml ps
```

除一次性运行的 `migrate` 显示 `Exited (0)` 外，其余服务应该为 `Up` 或 `healthy`。

如果失败：

```bash
docker compose --env-file .env.production \
  -f deploy/docker-compose.prod.yml logs --tail=200
```

不要执行 `docker compose down -v`，`-v` 会删除数据库和媒体数据卷。

## 第六阶段：从自己的电脑打开网站

不要使用 Google Cloud 网页 SSH 做这一步。打开自己电脑的 PowerShell：

```powershell
ssh -L 8080:127.0.0.1:8080 你的服务器用户名@服务器外部IP
```

保持这个 SSH 窗口不要关闭，然后浏览器访问：

```text
http://localhost:8080
http://localhost:8080/admin
```

进入 `/admin` 时，浏览器会要求输入 `.env.production` 中的 `ADMIN_USERNAME` 和你生成
哈希时使用的原始管理台密码。

关闭 SSH 窗口后，本地访问会立即中断，但服务器上的采集和自动管线仍会继续运行。

## 第七阶段：迁移现有数据

先完成空环境启动和访问验证，再迁移现有数据库、媒体和平台登录状态。需要迁移：

- `backups/*.dump`
- `apps/web/public/media`
- `.secrets/x-cookies.json`
- `.secrets/weibo-cookies.json`
- `.secrets/weibo-browser-profile`

数据库导出使用开发电脑上的：

```powershell
.\scripts\export-production-database.ps1
```

上传和恢复步骤见 `docs/PRODUCTION_DEPLOYMENT.md`。恢复前应先暂停
`pipeline-worker` 和 `collection-scheduler`，避免恢复期间产生新任务。

## 第八阶段：以后购买域名

有域名后：

1. 把域名 A 记录指向静态外部 IP。
2. 开放 80 和 443。
3. 从 `.env.production.example` 重新生成正式配置。
4. 设置 `SITE_ADDRESS=你的域名`。
5. 设置 `PUBLIC_ORIGIN=https://你的域名`。
6. 设置 `BIND_ADDRESS=0.0.0.0`。
7. 再运行 `./deploy/scripts/deploy.sh`。

Caddy 会自动申请和续期 HTTPS 证书，SSH 隧道不再是普通访客访问网站的必要条件。
