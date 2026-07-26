# Web Connector 使用说明

> 历史操作说明，平台数量和字段名可能过期。当前操作见
> [`CONNECTOR_OPERATIONS_GUIDE.md`](CONNECTOR_OPERATIONS_GUIDE.md)，架构见
> [`CONNECTOR_ARCHITECTURE.md`](CONNECTOR_ARCHITECTURE.md)。

三个 connector 只采集原文并写入 `raw_items` / `media_assets`，不会自动翻译、分析或创建事件。

## 配置

Riot 和腾讯无需密钥。X 使用本地专用账号的 Web cookie：

```dotenv
X_COOKIE_FILE=.secrets/x-cookies.json
X_FETCH_LIMIT=10
```

`x-cookies.json` 支持对象或浏览器导出的 cookie 数组，至少需要 `auth_token` 和 `ct0`：

```json
{"auth_token": "...", "ct0": "..."}
```

不要使用个人主账号。`.secrets/` 已被 Git 忽略；connector 只把 cookie 导入本次运行的
临时 twscrape SQLite，运行结束后立即删除，不会写入 PostgreSQL、connector run 错误或
raw payload。此方式依赖 X 的内部 Web GraphQL，可能因页面变更、限流、账号风控或服务
条款变化而失效；项目不会自动切换到付费 API、代理池或多账号池。

## 手动运行

启动服务并应用 `008_seed_web_connector_sources.sql` 后，在 Swagger 或 HTTP 中调用：

```http
POST /api/v1/connectors/riot_official/run
Content-Type: application/json

{"limit": 10}
```

腾讯与 X 分别使用 `tencent_lol`、`x_twitter`。腾讯默认栏目 target 为 `24`，需要切换时可传：

```json
{"limit": 10, "options": {"target": "25"}}
```

X 的一个账号对应一条独立 `sources` 记录。新增跟踪账号时先创建 Source：

```json
{
  "name": "League of Legends (@LeagueOfLegends)",
  "connector_type": "x_twitter",
  "external_key": "LeagueOfLegends",
  "base_url": "https://x.com/LeagueOfLegends"
}
```

然后运行时传这个 Source 的 ID：

```json
{"source_id": 7, "limit": 10}
```

同一个 `x_twitter` connector 可以服务任意多个账号，但每次 run 只采集一个具体 Source，
因此不同账号的 raw 不会混入同一信源。

X 帖子只保存文字和图片；视频、视频下载地址及视频缩略图不会进入
`content_blocks` 或 `raw_payload`，用户仍可通过原帖 URL 查看视频。

网页图片会尝试缓存到本地媒体目录。同一篇文章的图片采用有限并发下载；单张图片在
12 秒内无法获取时保留原始 `source_url` 和正文位置，不会让整批资讯回滚。

Riot connector 从官网页面内嵌的 Smart List 元数据发现按时间排序的内部文章，跳过
YouTube、LoL Esports 等外链，再获取文章正文。

缺少或失效 cookie 时 connector run 会明确失败。此时仍可使用手动导入作为回退。

## 验证

默认测试只使用 fixtures，不访问实时网站：

```powershell
Set-Location E:\leagueNews\services\api
uv run ruff check app tests
uv run pytest -q
```

## 微博与百度贴吧账号 Connector

`010_add_weibo_tieba_sources.sql` 创建五个微博账号 Source，以及两个限定在
`lol半价吧` 的贴吧账号 Source。每次运行仍必须指定一个具体的 `source_id`：

```http
POST /api/v1/connectors/weibo/run
Content-Type: application/json

{"source_id": 10, "limit": 10}
```

```http
POST /api/v1/connectors/baidu_tieba/run
Content-Type: application/json

{"source_id": 15, "limit": 10}
```

微博 Source 的 `external_key` 是数字 UID。Connector 借鉴 WeiboSpider 的
`weibo.com/ajax/statuses/searchProfile` 与长微博接口，但使用独立 Edge Profile
维护完整登录态，不需要手工复制 Cookie。首次使用执行：

```powershell
Set-Location E:\leagueNews\services\api
uv run python scripts/setup_weibo_browser.py
```

在打开的 Edge 窗口中完成一次微博登录；检测到账号时间线可访问后窗口会自动关闭。
登录信息只保存在 Git 忽略的 `.secrets/weibo-browser-profile`，不会进入数据库、日志
或 `raw_payload`。运行 Connector 时不能同时打开登录脚本，否则 Edge Profile 会被锁定。
可通过 `WEIBO_BROWSER_PROFILE`、`WEIBO_BROWSER_CHANNEL` 和
`WEIBO_BROWSER_HEADLESS` 覆盖 Profile 路径、浏览器通道与运行模式。

微博图片会进入正常媒体落盘流程；视频、投票和其他附加内容只保存用户可打开的页面
链接，不下载媒体文件。转发微博保存当前账号的转发语、原微博摘要和原微博链接，不会
递归创建另一条资讯。

贴吧 Source 的 `external_key` 是稳定数字 user ID，目标贴吧由
`connector_config.forum_name` 指定。Connector 匿名调用 `aiotieba`，发现该用户在
目标贴吧发布的主题后，对每个主题分页读取全部“只看楼主”楼层，并再次按 user ID
过滤。一个主题最终对应一条 raw item，楼主的首楼和后续发言按楼层顺序拼接；其他用户
回复不会混入。贴吧不需要也不读取 BDUSS。
