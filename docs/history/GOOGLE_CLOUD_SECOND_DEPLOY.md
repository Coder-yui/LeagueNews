# Google Cloud 第二次部署记录

> 历史部署记录：用于追溯 2026-08-16 大版本上线前后的实际运行版本和部署边界。
> 当前生产操作仍以 [`../PRODUCTION_DEPLOYMENT.md`](../PRODUCTION_DEPLOYMENT.md) 为准。

## 1. 部署目标

- Google Cloud 项目：`project-5f162905-6b28-4d14-8bf`
- Compute Engine 实例：`instance-20260727-160248`
- 区域：`asia-east1-a`
- 机器类型：`e2-medium`
- Docker Compose 项目：`league-news`
- 服务器部署目录：`/home/czh69423821/LeagueNews`

本次是大版本后的替换部署，不迁移第一次部署产生的 PostgreSQL 业务数据或媒体文件。
生产环境文件、平台 Cookie 文件和 Caddy 证书卷保留；数据库和媒体卷重建后，以开发机当前
验收数据库和媒体目录作为新生产环境的初始数据。

## 2. 替换前实际运行版本

盘点时间：2026-08-16（Asia/Shanghai）。

服务器 Git 工作区和实际运行镜像不是同一个提交：

- 服务器 Git 工作区：`3d8df23803a58da55783b1e6de0b83b7ecd500e5`
  (`fix: inject Weibo cookies across production containers`)
- API/Web 容器镜像源码：`b1e6d684d87ef96927d03bf29ef6ccc481367c1f`
  (`chore: audit project and refresh handoff`)

因此，第一次部署结束时真正执行的应用版本应以镜像标签中的 OCI revision `b1e6d68` 为准，
而 `3d8df23` 只表示服务器磁盘上的仓库版本。

替换前容器镜像：

- API、Pipeline Worker、Collection Scheduler、Migrate：
  `ghcr.io/coder-yui/leaguenews-api:latest`
  - 本地镜像 ID：`sha256:5da0c506f3b10d637a5c9b03fa2015381659bf3573e0afa4fb9b108a6e196f26`
  - OCI revision：`b1e6d684d87ef96927d03bf29ef6ccc481367c1f`
- Web：
  `ghcr.io/coder-yui/leaguenews-web:latest`
  - 本地镜像 ID：`sha256:d3c258c886ab05cfd60a9ab95162d51038a10f0d5a22277e2c89c01ed4b9ed2f`
  - OCI revision：`b1e6d684d87ef96927d03bf29ef6ccc481367c1f`
- PostgreSQL：`postgres:17-alpine`
- Caddy：`caddy:2.10.2-alpine`

旧版 SHA 固定镜像仍存在于 GHCR：

- API：`ghcr.io/coder-yui/leaguenews-api:sha-b1e6d684d87ef96927d03bf29ef6ccc481367c1f`
- Web：`ghcr.io/coder-yui/leaguenews-web:sha-b1e6d684d87ef96927d03bf29ef6ccc481367c1f`

## 3. 替换前数据边界

经容器挂载和 Compose label 双重核对，本次替换的旧业务数据卷只有：

- `league-news_postgres_data`：约 103 MB，挂载到 PostgreSQL 的
  `/var/lib/postgresql/data`
- `league-news_media_data`：约 731 MB，挂载到 API、Worker、Scheduler 和 Caddy 的媒体目录

以下卷保留：

- `league-news_caddy_data`：HTTPS 证书和 Caddy 状态
- `league-news_caddy_config`：Caddy 运行配置状态

旧部署的微博 Chromium Profile 使用 `.secrets` 下的宿主机 bind mount；新版本改为 API 和
Scheduler 各自独立的 Docker managed volume。Cookie JSON 凭据文件保留且不写入本记录。

## 4. 本地初始数据

为保证数据库和媒体的一致性，导出前停止了本地 API、Web、Pipeline Worker 和 Collection
Scheduler，只保留 PostgreSQL。导出基线：

- PostgreSQL 17.10 custom dump：23,877,455 bytes
- 数据库逻辑大小：约 133 MB
- 已应用迁移：72 个；当前第 73 个迁移由生产部署迁移容器在恢复后应用
- Source：27
- RawItem：2,918
- NormalizedItem：2,797
- MediaAsset：4,518
- Event：563
- DailyReport：39
- 媒体目录：约 2.1 GB，6,857 个普通文件
- 数据库 dump SHA-256：
  `3beb06a9a7d2d30adf77465175d8edf662157d1d727201f516ed602c1c0aeeee`
- 媒体 tar SHA-256：
  `6bd1f0f89daf5ab28392fba6223e6e8c84132548ac5a3182dc376158080e8852`

## 5. 第二次部署版本

- Git 提交：`76f8b878f16067e3c56c390750128c39bcbda993`
  (`fix(deploy): harden production deployment`)
- 部署镜像标签：`sha-76f8b878f16067e3c56c390750128c39bcbda993`
- API amd64 manifest：`sha256:ea7d887c4407fc788cd4b64a036bd79ba6cf5afea0d7836fa38cd40d4f69d84d`
- Web amd64 manifest：`sha256:680a38d9797738c74c0a1631e503759c9b4efae4c4c18f2b7976ff159e7db7fa`
- 服务器 API 镜像 ID：
  `sha256:9a051b1a5f36f54bcd5733d7f914e57a7a6cb87b5c262322f8e6f48821551f38`
- 服务器 Web 镜像 ID：
  `sha256:8a4301e496e72e1363b92139e20b511abf69f295696f06b75819412be66aa94d`

## 6. 部署结果

部署完成日期：2026-08-16（Asia/Shanghai）。

- 删除并重建了 `league-news_postgres_data` 和 `league-news_media_data`。
- 保留了 Caddy 证书卷、生产环境文件和平台 Cookie 文件。
- 数据库 dump 恢复后由 Migrate 应用 `073_update_message_taxonomy_v4`，迁移总数为 73。
- 恢复后的核心数量与本地基线一致：RawItem 2,918、NormalizedItem 2,797、MediaAsset
  4,518、Event 563。
- 媒体解包后清除了 macOS tar 产生的 6,876 个 `._*` AppleDouble 元数据文件，最终保留
  6,857 个业务媒体文件；抽样的已发布 JPEG 通过公网媒体路由返回 200。
- Scheduler 启动后按当前自动化规则补生成了一份日报，DailyReport 从导入时的 39 变为 40。
- 本地数据没有启用采集计划，部署完成时启用计划数为 0；Pipeline Job 为 2,848 个 completed、
  4 个 failed，没有 queued 或 running job。
- 新版本的生产 MCP 已启用，允许主机为 `leaguenews.me`；服务 Token 在服务器本地随机生成，
  未打印或写入 Git。
- API、Web、PostgreSQL 健康检查通过；API、Web、Worker、Scheduler、PostgreSQL 和 Caddy
  均以 0 次重启运行。
- 公网首页、About、事件页、日报页和旧版 published API 返回 200；管理台和私有 API 未认证
  请求返回 401。
- 上传用媒体 tar 在恢复和校验后从服务器删除，本地忽略目录仍保留同一份归档；服务器保留
  23 MB 的原始本地数据库 dump。
- 部署后数据库备份：`backups/league-news-20260816T035310Z.dump`，23,898,020 bytes，
  SHA-256 `8670059dd87a46fc01811f858aa204272215eb87788fd7431fd0a017c67ebb66`；
  已通过 `pg_restore --list` 验证为 PostgreSQL 17.10 custom archive。
- 清理媒体传输包后，服务器根文件系统约剩余 9.6 GB（使用率约 75%）。

## 7. 上线后采集限速修订

2026-08-16 根据生产采集衔接要求追加部署：

- Git 提交：`2d4981aee6ea215a017c9dec8c5bf59fb61e316d`
  (`fix(collection): enforce configurable catch-up interval`)
- API/Web 镜像标签：`sha-2d4981aee6ea215a017c9dec8c5bf59fb61e316d`
- 服务器 API 镜像 ID：
  `sha256:60f3c35d404e91138c339c817fe2a311021f7ebd4bc550cd2c8c95fbb625b115`
- 服务器 Web 镜像 ID：
  `sha256:9cecd38ea7d5b8ab509d54e3c360d82b0f8d8b9aa7bc6bcff7f4ffca7d35d075`
- 新增 `COLLECTION_TRUNCATED_RETRY_MINUTES`；生产设置为 `60`，确保单批达到平台上限时也不会
  分钟级连续采集同一账号。
- 生产数据基线截止时间按北京时间 2026-08-15 18:00，即 UTC `2026-08-15T10:00:00Z`；
  自动 Source 从该 watermark 继续采集。
- X、腾讯官网、贴吧和 Riot 官网共 17 个 Source 启用，每个 Source 每 120 分钟最多 10 条；
  同 connector 的 Source 错峰运行。8 个微博 Source 因生产 Cookie 登录态失效而暂停，但已预置
  相同 watermark，重新登录后可从同一边界继续。
- 首轮验证中 X、腾讯官网和 Riot 官网成功且 `truncated=false`；贴吧遇到一次百度接口连接超时，
  按 60 分钟策略等待重试。
- 本地 dispatcher 已实际投递的 64 条精选通知和 6 条失败告警，在生产恢复快照中仍为 pending；
  已按部署前边界对账为 sent，避免未来启用云端飞书时重复发送。
- 切换完成后删除了上一版 `76f8b87` 的 API/Web 镜像和退出的旧 migrate 容器；服务器只保留当前
  固定 SHA 的 LeagueNews 镜像，Git 和 GHCR 固定标签仍可用于回滚。

## 8. 回滚说明

Git 可以检出 `b1e6d68`，GHCR 也保留该提交的 SHA 固定镜像，因此应用代码可以回滚。
但 Git 和容器镜像不包含 PostgreSQL 业务数据或媒体文件；第二次部署删除旧数据卷后，代码
回滚不会恢复第一次部署的数据。完全恢复旧站必须同时拥有对应数据库 dump 和媒体备份。
