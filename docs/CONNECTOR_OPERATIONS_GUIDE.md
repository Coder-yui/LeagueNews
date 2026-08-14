# Connector 数据采集与入库操作手册

本文说明当前已经实现的全部 Connector 如何配置、调用、验证入库，以及遇到常见问题时
如何定位和恢复。

适用的 Connector：

| `connector_type` | 数据来源 | 调用入口 |
|---|---|---|
| `riot_official` | 英雄联盟 Riot 英文官网 | Connector Run |
| `tencent_lol` | 腾讯英雄联盟中文官网 | Connector Run |
| `x_twitter` | 指定 X 账号 | Connector Run |
| `weibo` | 指定微博账号 | Connector Run |
| `baidu_tieba` | 指定贴吧账号在指定贴吧发布的主题 | Connector Run |
| `manual` | 人工提供的内容或链接 | Manual Import |

## 1. Connector 做了什么

一次 Connector Run 的处理路径如下：

```text
指定 Source
  → Connector 获取平台原始内容
  → fetch 返回平台 record
  → map_record 转换为经过校验的 RawItemCandidate
  → 按 Source + external_id/content_hash 去重
  → 图片尝试下载到本地媒体目录
  → 写入 raw_items 和 media_assets
  → 创建 connector_runs 运行记录
  → 为新增 RawItem 创建持久化 pipeline_job
```

Connector 本身只负责采集和原始入库；默认启用的独立 Pipeline Worker 会消费
`pipeline_jobs`，自动继续执行相关性、可选 OCR、翻译、分析、重要性计算和消息发布。
`raw_items` 仍是不可变原文，不保存可变处理状态。完整字段设计见
[RawItem 与 ContentBlock v2](RAW_ITEM_CONTENT_MODEL.md)。

只有在需要显式进入人工审核链路时，才直接调用：

```http
POST /api/v1/raw-items/{raw_item_id}/process
```

自动和人工处理都需要 `.env` 中配置可用的 `OPENAI_API_KEY`、
`OPENAI_BASE_URL` 和 `MODEL_NAME`。已发布结果有误时，应从管理台选择撤回阶段，再选择
人工或自动模式重跑，不要修改 RawItem。

## 2. 启动与基础检查

### 2.1 推荐启动方式

在项目根目录执行：

```powershell
Set-Location E:\leagueNews
.\start.cmd
```

启动脚本会检查依赖、启动 PostgreSQL/pgAdmin、应用数据库迁移，并启动 FastAPI 和
Web 前端。

常用地址：

- FastAPI Swagger：<http://localhost:8000/docs>
- API 健康检查：<http://localhost:8000/api/v1/health>
- pgAdmin：<http://localhost:5050>
- Web：<http://localhost:3000>

仅检查 API：

```powershell
Invoke-RestMethod http://localhost:8000/api/v1/health
```

预期结果：

```json
{"status":"ok"}
```

停止全部本地服务：

```powershell
.\stop.cmd
```

### 2.2 首次安装或依赖发生变化

```powershell
Set-Location E:\leagueNews\services\api
$env:UV_CACHE_DIR = "E:\leagueNews\.uv-cache"
uv sync --dev
```

微博 Connector 使用 Playwright Python 包，但复用系统安装的 Microsoft Edge，不需要
执行 `playwright install` 下载另一套浏览器。

### 2.3 确认已注册类型和 Source

```powershell
Invoke-RestMethod http://localhost:8000/api/v1/connectors |
    Format-Table connector_type

Invoke-RestMethod http://localhost:8000/api/v1/sources |
    Format-Table id,name,connector_type,external_key,is_active
```

调用时推荐始终显式传 `source_id`。如果某个 `connector_type` 有多个启用的 Source，
省略 `source_id` 会返回 `409 multiple sources match; provide source_id`。

全新数据库会创建以下 26 个内置信源。Source ID 不属于 API 契约；各环境始终以
`/sources` 返回值为准。

| Connector | Source | 稳定身份/配置 |
|---|---|---|
| `manual` | 手动导入 | - |
| `tencent_lol` | 腾讯英雄联盟官方网站 | `target=24` |
| `tencent_lol` | 腾讯英雄联盟赛事官网（LPL） | `target=25` |
| `riot_official` | Riot Games Official | `leagueoflegends.com` |
| `x_twitter` | Matt Leung-Harrison (@RiotPhroxzon) | `riotphroxzon` |
| `x_twitter` | League of Legends Dev Team (@LoLDev) | `loldev` |
| `x_twitter` | Riot Phlox (@RiotPhlox) | `riotphlox` |
| `x_twitter` | LCK (@LCK) | `lck` |
| `x_twitter` | LEC (@LEC) | `lec` |
| `x_twitter` | T1 LoL (@T1LoL) | `t1lol` |
| `x_twitter` | Gen.G Esports (@GenG) | `geng` |
| `x_twitter` | G2 League of Legends (@G2League) | `g2league` |
| `x_twitter` | LoL Esports (@lolesports) | `lolesports` |
| `x_twitter` | Spideraxe (@Spideraxe30) | `spideraxe30` |
| `x_twitter` | SkinSpotlights (@SkinSpotlights) | `skinspotlights` |
| `x_twitter` | League of Legends (@LeagueofLegends) | `leagueoflegends` |
| `weibo` | 英雄联盟赛事 | `5756404150` |
| `weibo` | 英雄联盟 | `5720474518` |
| `weibo` | BLG电子竞技俱乐部 | `5926660141` |
| `weibo` | 滔搏电子竞技俱乐部 | `5449734852` |
| `weibo` | 丶涵艺 | `1992350413` |
| `weibo` | 恋恋红茶_244 | `2266865584` |
| `weibo` | 召唤师Park | `2522098777` |
| `weibo` | _尧阿尧y_ | `2600241232` |
| `baidu_tieba` | lol半价吧 · 小老鼠小伟 | `86124184` |
| `baidu_tieba` | lol半价吧 · 凤舞天_惊鸿恋 | `770437943` |

## 3. 统一 Connector Run 调用

除 `manual` 外，其余 Connector 使用同一个端点：

```http
POST /api/v1/connectors/{connector_type}/run
Content-Type: application/json
```

请求体：

```json
{
  "source_id": 17,
  "limit": 5,
  "since": "2026-07-24T00:00:00+08:00",
  "options": {}
}
```

字段说明：

| 字段 | 必填 | 说明 |
|---|---|---|
| `source_id` | 推荐必填 | 本次采集归属的具体 Source |
| `limit` | 否 | API 范围 1–50；不同 Connector 还会执行自己的上限 |
| `since` | 否 | ISO 8601 时间；早于该时间的已发现内容不入库 |
| `options` | 否 | 单次运行参数，目前主要用于腾讯的 `target` |

`options` 不能包含 `limit`、`since`、`source` 或 `source_id`。

PowerShell 通用调用模板：

```powershell
$body = @{
    source_id = 17
    limit = 5
    since = $null
    options = @{}
} | ConvertTo-Json

Invoke-RestMethod `
    -Uri "http://localhost:8000/api/v1/connectors/weibo/run" `
    -Method Post `
    -ContentType "application/json" `
    -Body $body
```

成功响应示例：

```json
{
  "id": 34,
  "source_id": 21,
  "connector_type": "weibo",
  "status": "completed",
  "discovered_count": 5,
  "created_count": 5,
  "revised_count": 0,
  "skipped_count": 0,
  "error_message": null
}
```

计数含义：

- `discovered_count`：Connector 本次返回的内容数；
- `created_count`：新创建的 RawItem 行数，包含新的 revision；
- `revised_count`：其中因相同平台 ID 内容变化而创建的新 revision 数；
- `skipped_count`：因 `external_id` 或正文哈希重复而跳过的数量；
- `created_count=0` 且 `skipped_count>0` 通常表示内容已入库，不是故障。

## 4. Riot 英文官网

### 调用

```powershell
$body = @{source_id=3; limit=10} | ConvertTo-Json
Invoke-RestMethod `
    -Uri "http://localhost:8000/api/v1/connectors/riot_official/run" `
    -Method Post -ContentType "application/json" -Body $body
```

### 采集行为

- 从 `https://www.leagueoflegends.com/en-us/news/` 发现官网文章；
- 只保留 Riot 官网内部文章，跳过 YouTube、LoL Esports 等外链；
- 获取标题、正文、作者、发布时间和正文图片；
- 单次最多 50 条；
- 语言写为 `en`。

### 常见问题

**`Riot news list structure changed: no articles found`**

Riot 官网列表结构发生变化，或者请求被临时拦截。

1. 在浏览器打开 Riot 新闻页确认页面可访问；
2. 检查 `E:\leagueNews\.run\logs\api.error.log`；
3. 稍后重试，排除临时网络问题；
4. 若持续出现，需要更新 `riot_official.py` 中的 Smart List/DOM 解析规则和 fixture。

**列表能读取，但部分文章没有入库**

文章正文结构为空的单篇内容会被跳过，其他文章仍可继续。检查 API 日志和目标文章页面；
如果官网使用了新的正文组件，需要扩展正文选择器。

## 5. 腾讯英雄联盟官网

### 默认调用

```powershell
$body = @{source_id=2; limit=10} | ConvertTo-Json
Invoke-RestMethod `
    -Uri "http://localhost:8000/api/v1/connectors/tencent_lol/run" `
    -Method Post -ContentType "application/json" -Body $body
```

Source 2 当前默认配置为：

```json
{"target":"24"}
```

### 临时覆盖栏目 target

```powershell
$body = @{
    source_id = 2
    limit = 10
    options = @{target="25"}
} | ConvertTo-Json -Depth 4

Invoke-RestMethod `
    -Uri "http://localhost:8000/api/v1/connectors/tencent_lol/run" `
    -Method Post -ContentType "application/json" -Body $body
```

`target` 是腾讯上游内容接口的栏目编号。切换编号后应检查返回文章是否属于预期栏目，
不要在未验证的情况下永久修改 Source。

### 采集行为

- 调用腾讯公开内容列表和详情接口；
- 获取标题、正文、作者、发布时间和图片；
- 详情标记为腾讯 LOL 站内跳转时，继续抓取 `lol.qq.com` 目标页；
- `gicp/news` 跳转页按文章主体提取，自动识别腾讯旧页面的 GBK 编码；
- 周免活动页不读取会随时间变化的空页面骨架，而是按 URL 中的 `siteId` 读取腾讯 CMS 历史
  期次及官方英雄表，生成期数、日期、版本和英雄列表；
- 站内跳转抓取或解析失败时整次运行失败，不以“查看完整公告”占位块入库；站外跳转仅保留
  `external_link`，不会继续抓取；
- 清理上游正文中的非法控制字符；
- 单次最多 50 条；
- 语言写为 `zh-CN`。

站内跳转成功解析后，RawItem 的 `canonical_url` 是实际目标页。目标 URL、响应长度和提取
类型会写入脱敏 provenance 摘要，完整 HTML 不会写入数据库。

## 8. 一次性历史批量采集（仅 RawItem）

需要回补某个时间点之后的历史消息时，使用可断点续传脚本，不要循环调用 API 端点：

```bash
services/api/.venv/bin/python services/api/scripts/bulk_collect_raw_items.py \
  --since 2026-08-01T00:00:00+08:00 \
  --until 2026-08-14T18:00:00+08:00 \
  --limit 10 \
  --batch-delay 75 --batch-delay-jitter 30 \
  --source-delay 120 --source-delay-jitter 60 \
  --error-delay 180 --error-delay-jitter 60 \
  --max-retries 3
```

脚本只选择 active Source，并排除 `x_twitter` 和 `manual`。`--until` 是可选的、包含边界的
事件发布时间上限；启用它时，缺少发布时间或超出时间窗的候选不会写入 RawItem，报告会单独
统计。如需在代理可用时单独回补 X，
显式加上 `--include-x --connector-type x_twitter`，并使用独立的状态、报告和日志文件。每次批次会先完成平台抓取和
共享 ingestion，再原子更新状态 JSON 和 Markdown 报告；调用 ingestion 时显式设置
`enqueue_downstream=False`，因此本任务不会创建 `pipeline_jobs`，也不会触发下游处理。

默认文件位于 `.run/`：

- `bulk_collect_raw_items_20260801_state.json`：每个 Source 的游标、状态和累计计数；
- `bulk_collect_raw_items_20260801_report.md`：可直接阅读的采集报告；
- `bulk_collect_raw_items_20260801.log`：后台标准输出和错误。

进程中断、代理断开或单批重试耗尽后，直接用相同命令重新运行即可。已完成 Source 会跳过，
未完成 Source 从最近一次成功批次的 cursor 继续。`--*-delay-jitter` 会让每次等待在基准值
上下随机波动，适合长时间的外网采集；网络错误仍会按重试次数递增等待。不要在同一状态文件上
同时启动两个实例。

X 批量示例（与非 X 批次使用同一 `since`，但状态文件独立）：

```bash
services/api/.venv/bin/python services/api/scripts/bulk_collect_raw_items.py \
  --include-x --connector-type x_twitter \
  --since 2026-08-01T00:00:00+08:00 \
  --limit 5 --batch-delay 60 --source-delay 90 --error-delay 120 \
  --state-file .run/bulk_collect_x_20260801_state.json \
  --report-file .run/bulk_collect_x_20260801_report.md
```

### 常见问题

**`Tencent news list returned no article records`**

- 检查 `target` 是否有效；
- 去掉 `options`，使用 Source 默认 `target=24` 重试；
- 在浏览器或 PowerShell 中检查腾讯接口是否仍返回 JSON；
- 若接口字段从 `data.result` 发生变化，需要更新解析器和 fixture。

**`Tencent article body is empty`**

目标详情存在，但 `sContent` 为空或格式变化。用返回的 `docid` 检查详情接口；不要把空正文
强行入库，应先修正解析。

**`Tencent redirect content is missing` / `Tencent redirect article body is empty`**

详情接口声明了 `lol.qq.com` 站内跳转，但 fetch 没有取得目标页，或目标页主体结构已变化。
检查 `sRedirectURL` 是否仍可访问以及正文容器是否仍为 `.article`、`article` 或 `main`；修复
fixture 和解析器后再运行，不要降级为链接占位块。

**`Tencent week-free ...`**

周免 URL 缺少 `siteId`、CMS 中找不到对应历史期次，或英雄 ID 无法在官方英雄表映射时会
失败。分别检查活动页 URL、`ZMSubject_Board_Site.js` 和 `hero_list.js`；不得用当前最新一期
替代缺失的历史期次。

修复采集器后需要重采历史区间时，通过标准 Connector Run 传入早于目标消息的 `since`。
相同 `external_id` 且正文语义哈希变化时，ingestion 会创建新 revision 并保留旧 RawItem，
不会原地改写历史证据。

## 6. X 指定账号

### 6.1 配置登录 Cookie

X Connector 使用专用 X 账号的 Web Cookie，配置文件默认是：

```text
E:\leagueNews\.secrets\x-cookies.json
```

最小对象格式：

```json
{
  "auth_token": "实际值",
  "ct0": "实际值"
}
```

也支持浏览器 Cookie 导出工具生成的数组格式。`.secrets/` 已被 Git 忽略。

建议使用专用采集账号，不要使用个人主账号，不要把 Cookie 写入 `.env`、数据库、日志、
截图或提交记录。

### 6.2 调用

```powershell
$sourceId = (
    Invoke-RestMethod http://localhost:8000/api/v1/sources |
    Where-Object external_key -eq "riotphroxzon"
).id
$body = @{source_id=$sourceId; limit=5} | ConvertTo-Json
Invoke-RestMethod `
    -Uri "http://localhost:8000/api/v1/connectors/x_twitter/run" `
    -Method Post -ContentType "application/json" -Body $body
```

其他账号按它的 `external_key` 查询 Source ID，不要把某个数据库中的数字 ID 写进脚本。

### 采集行为

- 一个 X 账号对应一个 Source；
- 使用 `external_key` 中的用户名查找账号并读取最近推文；
- 正文和图片入库；
- 视频、视频地址和视频缩略图不入库，用户通过原帖 URL 查看；
- Cookie 只导入本次运行的临时 twscrape SQLite；
- 临时数据库在运行结束后删除；
- 单次最多 10 条，且受 `X_FETCH_LIMIT` 限制。

### 常见问题

**`X cookie file is missing`**

确认文件路径和 `.env`：

```dotenv
X_COOKIE_FILE=.secrets/x-cookies.json
X_FETCH_LIMIT=10
```

相对路径以项目根目录解析。

**`must contain non-empty auth_token and ct0`**

JSON 格式正确，但缺少必要 Cookie。重新从已登录的 X 浏览器会话导出
`auth_token` 和 `ct0`。

**`X collection failed; the cookie may be expired, rate-limited, or blocked`**

按顺序处理：

1. 在浏览器确认专用账号仍处于登录状态；
2. 重新导出 Cookie；
3. 降低调用频率和 `limit`；
4. 等待一段时间后重试，排除临时限流；
5. 确认 Source 的 `external_key` 是用户名，不含 URL；
6. 若所有账号同时失败，检查 twscrape 与 X Web API 是否发生兼容性变化。

项目不会自动切换到代理池、多账号池或付费 API，也不会绕过验证码。

## 7. 微博指定账号

微博 Connector 借鉴 WeiboSpider 的账号时间线和长微博接口，通过独立 Edge Profile
维护完整浏览器登录态，不再手工复制 `SUB`、`SUBP` 等 Cookie。

### 7.1 首次登录

关闭其他正在使用微博专用 Profile 的窗口，然后执行：

```powershell
Set-Location E:\leagueNews\services\api
$env:UV_CACHE_DIR = "E:\leagueNews\.uv-cache"
uv run python scripts/setup_weibo_browser.py
```

程序会打开一个独立 Edge 窗口。完成微博登录后保持窗口开启；脚本每 5 秒验证一次
“英雄联盟赛事”账号时间线。验证成功后窗口自动关闭。

登录数据保存在专用 Profile，并在验证成功前自动导出当前 Cookie：

```text
E:\leagueNews\.secrets\weibo-browser-profile
E:\leagueNews\.secrets\weibo-cookies.json
```

默认配置：

```dotenv
WEIBO_BROWSER_PROFILE=.secrets/weibo-browser-profile
WEIBO_COOKIE_FILE=
WEIBO_BROWSER_CHANNEL=msedge
WEIBO_BROWSER_HEADLESS=true
WEIBO_BROWSER_USER_AGENT=
```

`WEIBO_COOKIE_FILE` 留空时，本地自动使用上述默认 Cookie 文件。生产环境不能依赖从
Windows 复制 Chromium Profile 后继续读取加密 Cookie；应把自动导出的
`.secrets/weibo-cookies.json` 安全上传并通过 `WEIBO_COOKIE_FILE` 挂载。云端还要使用建立
该登录态时的 `WEIBO_BROWSER_USER_AGENT`；每个浏览器上下文启动时都会重新注入 Cookie。

关闭占用专用 Profile 的 Edge 后导出：

```powershell
Set-Location E:\leagueNews\services\api
.venv\Scripts\python.exe -m scripts.export_weibo_cookies
```

Cookie JSON 是敏感凭据，只能放在 Git 已忽略的 `.secrets`，上传服务器后删除 `/tmp` 等
中间副本。

### 7.2 调用

```powershell
$sourceId = (
    Invoke-RestMethod http://localhost:8000/api/v1/sources |
    Where-Object external_key -eq "5756404150"
).id
$body = @{source_id=$sourceId; limit=5} | ConvertTo-Json
Invoke-RestMethod `
    -Uri "http://localhost:8000/api/v1/connectors/weibo/run" `
    -Method Post -ContentType "application/json" -Body $body
```

替换数字 UID 可采集其他微博账号。

### 采集行为

- 一个微博账号对应一个 Source，`external_key` 必须是数字 UID；
- 使用浏览器页面上下文请求 `searchProfile`，保留完整浏览器登录态；
- 支持正文、长微博、图片、转发正文和原微博链接；
- 视频、投票等只保存可打开的链接，不下载视频；
- 单次最多 10 条；
- 后台运行使用无头 Edge。

### 常见问题

**`The dedicated Weibo browser profile is not logged in`**

登录已失效。重新运行 `setup_weibo_browser.py`，完成登录后再调用 Connector。

**`Unable to start the dedicated Weibo Edge profile`**

最常见原因是 Profile 被另一个 Edge/Playwright 进程占用：

1. 关闭登录脚本打开的 Edge 窗口；
2. 等待几秒，确认登录脚本已经退出；
3. 不要同时调用两个微博 Connector Run；
4. 再次运行采集。

如果错误提示找不到 `msedge`：

- 确认 Microsoft Edge 已安装；
- 或在 `.env` 中把 `WEIBO_BROWSER_CHANNEL` 改成机器上已经安装且 Playwright 支持的
  浏览器通道，例如 `chrome`；
- 修改 `.env` 后重启 FastAPI。

**登录页面反复出现验证码**

在独立 Edge 窗口中人工完成验证。项目不会自动识别或绕过验证码。避免高频调用，
不要并发启动多个微博任务。

**接口返回 403、`ok=-100` 或突然全部失败**

1. 重新执行登录脚本；
2. 在专用 Edge 窗口确认微博主页可正常浏览；
3. 降低采集频率；
4. 检查微博是否修改了 `searchProfile` 或长微博接口；
5. 若接口变化，对照实时 Network 响应更新 Connector 和 fixture。

## 8. 百度贴吧指定账号

### 调用

小老鼠小伟：

```powershell
$sourceId = (
    Invoke-RestMethod http://localhost:8000/api/v1/sources |
    Where-Object external_key -eq "86124184"
).id
$body = @{source_id=$sourceId; limit=5} | ConvertTo-Json
Invoke-RestMethod `
    -Uri "http://localhost:8000/api/v1/connectors/baidu_tieba/run" `
    -Method Post -ContentType "application/json" -Body $body
```

凤舞天_惊鸿恋：

```powershell
$sourceId = (
    Invoke-RestMethod http://localhost:8000/api/v1/sources |
    Where-Object external_key -eq "770437943"
).id
$body = @{source_id=$sourceId; limit=5} | ConvertTo-Json
Invoke-RestMethod `
    -Uri "http://localhost:8000/api/v1/connectors/baidu_tieba/run" `
    -Method Post -ContentType "application/json" -Body $body
```

### 采集行为

- 不需要百度账号或 BDUSS；
- Source 的 `external_key` 是稳定数字 user ID；
- `connector_config.forum_name` 限定目标贴吧；
- 只发现该账号在目标贴吧发布的主题；
- 对每个主题分页执行“只看楼主”，并再次按 user ID 过滤；
- 同一主题中该账号的首楼和全部后续发言按楼层顺序拼接为一个 RawItem；
- 其他用户回复不会混入；
- 单次最多 10 个主题。

当前配置示例：

```json
{
  "forum_name": "lol半价",
  "max_thread_pages": 5,
  "max_post_pages": 100
}
```

### 常见问题

**运行成功但 `discovered_count=0`**

- 该账号最近的用户主题页中可能没有目标贴吧的主题；
- 检查 `forum_name` 是否为 `lol半价`，不要带“吧”字；
- 确认 Source 的数字 user ID 正确；
- 如果目标主题较旧，可提高 `max_thread_pages`，允许范围为 1–20。

**`exceeded max_post_pages`**

目标主题楼层很多，且楼主发言分页超过当前限制。提高 Source 的
`connector_config.max_post_pages`，允许范围为 1–200，然后重试。

**`returned no posts by user` 或 `thread identity does not match`**

贴吧返回的数据与 Source 身份不一致。先确认 user ID、贴吧名称和主题在网页上仍存在；
不要放宽身份校验，否则可能把其他账号的发言写入该 Source。

**`Baidu Tieba collection failed`**

通常是网络问题、贴吧临时风控或 aiotieba 与上游协议不兼容。稍后重试；若持续失败，
检查 aiotieba 版本和实时响应，不要直接改成未经身份验证的 HTML 拼接。

## 9. 手动导入

`manual` 不走 `/connectors/{type}/run`，使用：

```http
POST /api/v1/imports/manual
```

最小示例：

```powershell
$body = @{
    source_id = 1
    external_id = "manual-demo-001"
    title = "手动导入示例"
    author = "编辑"
    language = "zh-CN"
    url = "https://example.com/article"
    content = "这是手动导入的正文。"
    published_at = "2026-07-25T12:00:00+08:00"
} | ConvertTo-Json

Invoke-RestMethod `
    -Uri "http://localhost:8000/api/v1/imports/manual" `
    -Method Post -ContentType "application/json" -Body $body
```

也可以提交结构化 `content_blocks`：

```json
{
  "source_id": 1,
  "external_id": "manual-demo-002",
  "title": "包含图片的手动内容",
  "content_blocks": [
    {"type": "paragraph", "text": "第一段正文"},
    {
      "type": "image",
      "source_url": "https://example.com/image.jpg",
      "alt_text": "图片说明"
    }
  ]
}
```

至少需要 `content`、`content_blocks` 或 `url` 之一。

导入成功后与自动 Connector 一样：若 `PIPELINE_AUTOMATION_ENABLED=true`，新增 RawItem
会自动进入 `pipeline_jobs` 并跑完整链路；不再需要逐阶段手工点击。只有需要人工控制时才
暂停自动化或通过撤回功能选择人工模式。

**返回 `409 duplicate raw item`**

相同 Source 下已经存在相同 `external_id` 或正文哈希。响应会提供
`existing_raw_item_id`。如果这是同一内容，不需要重复导入；如果确实是不同内容，应使用
真实且唯一的 `external_id`，不要通过随机改正文规避去重。

## 10. 周期自动采集

管理台 `/admin` 的“自动化与撤回 → 自动化采集”按 Source 配置：

- 是否启用；
- 正常采集周期；
- 失败重试间隔；
- 单次抓取上限；
- Connector 专用 `options`。

对应 API：

```text
GET  /api/v1/collection-schedules
PUT  /api/v1/collection-schedules/sources/{source_id}
POST /api/v1/collection-schedules/sources/{source_id}/run-now
```

`collection-scheduler` 使用数据库租约串行领取到期任务并在长任务期间续租。内容水位保存在
持久化 `collection_cursor`，不再使用 `last_success_at` 作为事实水位。正常轮询使用可配置
重叠窗口；达到平台上限时记录 `truncated` 并通过 pending ID 继续向后扫描，完整追到旧水位
后才提升 watermark。网络超时保留原 cursor，记录连续失败数并按 `retry_delay_minutes` 重试。
全新数据库只创建内置信源，不自动启用任何周期，避免部署后立即访问外部平台。

## 11. 新增账号 Source

Connector 与账号 Source 分离。同一个 Connector 可以服务多个账号，但每次 Run 只归属
一个具体 Source。

### 新增 X 账号

```powershell
$body = @{
    name = "Example (@example)"
    connector_type = "x_twitter"
    external_key = "example"
    base_url = "https://x.com/example"
    connector_config = @{}
    is_active = $true
} | ConvertTo-Json

Invoke-RestMethod `
    -Uri "http://localhost:8000/api/v1/sources" `
    -Method Post -ContentType "application/json" -Body $body
```

### 新增微博账号

```powershell
$body = @{
    name = "微博账号名称"
    connector_type = "weibo"
    external_key = "1234567890"
    base_url = "https://weibo.com/u/1234567890"
    connector_config = @{include_reposts=$true}
    is_active = $true
} | ConvertTo-Json -Depth 4

Invoke-RestMethod `
    -Uri "http://localhost:8000/api/v1/sources" `
    -Method Post -ContentType "application/json" -Body $body
```

`external_key` 必须是微博数字 UID，不能填昵称。

### 新增贴吧账号范围

```powershell
$body = @{
    name = "lol半价吧 · 账号名称"
    connector_type = "baidu_tieba"
    external_key = "数字user_id"
    base_url = "贴吧用户主页URL"
    connector_config = @{
        forum_name = "lol半价"
        max_thread_pages = 5
        max_post_pages = 100
    }
    is_active = $true
} | ConvertTo-Json -Depth 4

Invoke-RestMethod `
    -Uri "http://localhost:8000/api/v1/sources" `
    -Method Post -ContentType "application/json" -Body $body
```

Source 名称必须唯一；同一 `connector_type + external_key` 也必须唯一。

## 12. 检查运行记录和入库结果

### API 检查

最近 100 次 Connector Run：

```powershell
Invoke-RestMethod http://localhost:8000/api/v1/connectors/runs |
    Select-Object -First 20 |
    Format-Table id,source_id,connector_type,status,discovered_count,created_count,revised_count,skipped_count,error_message
```

最近 100 条 RawItem：

```powershell
Invoke-RestMethod http://localhost:8000/api/v1/raw-items |
    Select-Object -First 20 |
    Format-Table id,source_id,external_id,author,processing_status,published_at,canonical_url
```

### PostgreSQL 检查

按 Source 统计：

```powershell
docker compose exec -T postgres psql -U lol -d lol_daily_intel -P pager=off -c "
SELECT
    s.id,
    s.name,
    s.connector_type,
    count(r.id) AS raw_count
FROM sources s
LEFT JOIN raw_items r ON r.source_id=s.id
GROUP BY s.id,s.name,s.connector_type
ORDER BY s.id;
"
```

查看最近入库内容：

```powershell
docker compose exec -T postgres psql -U lol -d lol_daily_intel -P pager=off -c "
SELECT id,source_id,external_id,author_name,published_at,canonical_url
FROM raw_items
ORDER BY id DESC
LIMIT 30;
"
```

不要通过直接修改 `raw_items` 来“修复”采集错误。应先修正 Source、登录态或 Connector，
再重新运行，让统一去重与入库逻辑处理数据。

## 13. 通用错误对照

| HTTP/现象 | 含义 | 处理 |
|---|---|---|
| `404 source not found` | `source_id` 不存在 | 重新调用 `/sources` 获取实际 ID |
| `404 no active source` | 没有对应启用 Source | 创建或启用正确 Source |
| `409 multiple sources match` | 同类型有多个 Source，未指定 ID | 显式传 `source_id` |
| `409 source ... uses connector_type=...` | URL 中类型与 Source 不匹配 | 修正 connector type 或 Source ID |
| `409 source is inactive` | Source 已停用 | 使用启用 Source |
| `422` | 请求体不符合 schema | 检查 JSON、`limit` 和 ISO 时间 |
| `502` | Connector 获取或解析失败 | 查看响应 `detail`、运行记录和 API 日志 |
| `completed + created=0 + skipped>0` | 全部重复 | 正常，无需处理 |
| `completed + discovered=0` | 没发现符合条件的内容 | 检查 `since`、账号范围和平台最新内容 |
| RawItem 为 `pending` | 尚未执行 AI 工作流 | 正常；按需调用 `/raw-items/{id}/process` |

### 查看后端日志

由 `start.cmd` 启动时：

```powershell
Get-Content E:\leagueNews\.run\logs\api.error.log -Tail 100
Get-Content E:\leagueNews\.run\logs\api.out.log -Tail 100
```

### 图片没有本地 `storage_path`

图片下载是尽力而为：

- 单张图片连接超时、返回非图片、超过 `MEDIA_MAX_BYTES` 或被源站拒绝时；
- RawItem 仍会保留远程 `source_url` 和正文位置；
- 单张图片失败不会让整批 RawItem 回滚。

默认限制：

```dotenv
MEDIA_ROOT=../../apps/web/public/media
MEDIA_MAX_BYTES=20971520
```

### Connector Run 显示 failed，但 RawItem 数没有增加

这是预期的事务行为。采集或批量入库过程中发生未处理异常时，数据库事务会回滚，本次新建
的本地媒体文件也会被清理。修复原因后重新运行即可。

## 14. 运行建议

- 所有账号类 Connector 都显式传 `source_id`；
- 首次先用 `limit=1` 验证，再提高到 5 或 10；
- 不并发调用微博 Connector；
- X 使用专用低风险账号，并控制频率；
- 使用 `since` 做时间过滤，但不要把它当作完整历史回溯机制；
- 定期查看 `connector_runs` 的失败记录和 `error_message`；
- Cookie 和浏览器 Profile 只放 `.secrets/`；
- 修改 Connector 解析逻辑时先更新 fixture 测试，再进行一次 `limit=1` 的真实验证；
- 视频只保留原帖/播放页链接，不在 Connector 中下载。

## 15. 开发验证

Connector 默认测试只使用 fixture，不访问真实平台：

```powershell
Set-Location E:\leagueNews\services\api
$env:UV_CACHE_DIR = "E:\leagueNews\.uv-cache"
uv run ruff check app tests scripts
uv run pytest -q
```

真实平台验证应单独进行，并从 `limit=1` 开始，确认 Source 身份、正文、时间和原帖链接后
再批量运行。
