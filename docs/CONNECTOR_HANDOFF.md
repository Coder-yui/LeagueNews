# Connector 专项开发 Handoff

> 历史文档：其中的 `ConnectorItem`、`plain_text`、`raw_payload` 和“三个 provider”
> 描述已经过期。当前架构只以
> [`CONNECTOR_ARCHITECTURE.md`](CONNECTOR_ARCHITECTURE.md) 为准。

本文档交给一个只负责 provider/connector 的独立开发对话。该对话完成 Riot 官网、腾讯
英雄联盟官网和 X 三个 provider 后结束；不要顺手重写 AI、事件聚类或前端。

## 1. 当前项目与边界

项目根目录：`E:\leagueNews`

当前稳定闭环：

```text
source-specific provider
  -> CanonicalRawItem
  -> shared ingestion
  -> raw_items + media_assets (pending)
  -> 人工触发 Process
  -> OCR / 翻译 / AI 分析
  -> normalized_items
  -> news_events
  -> Next.js 展示
```

这里统一采用以下术语：

- **Provider**：只处理“怎样从某个来源取回并解析信息”，例如 Manual、Riot Web、
  Tencent Web、X Web。
- **CanonicalRawItem**：所有 provider 输出的同一种无损结构。
- **Ingestion**：与来源无关的校验、哈希、去重、媒体落盘和数据库事务。
- **Connector run**：调用某个 provider 再交给 ingestion 的薄编排入口。

Provider 层只负责：

1. 发现新内容；
2. 获取信源原始响应；
3. 将平台字段整理为统一 `CanonicalRawItem`；
4. 保留原文图文顺序。

共享 ingestion 层负责：

1. 校验 canonical item；
2. 计算 `plain_text` 和 `content_hash`；
3. external ID 幂等去重；
4. 下载需要长期展示的媒体文件；
5. 在同一个事务写入 `raw_items` 和 `media_assets`；
6. 新 raw 一律保持 `pending`。

Provider/connector run 禁止：

- 调用 DeepSeek 或其他 LLM；
- 翻译、摘要、分类、评分；
- 创建 `normalized_items` 或 `news_events`；
- 自动调用 raw Process；
- 修改现有 AI workflow 和首页样式；
- 引入复杂 Agent；
- 把图片二进制直接写进 PostgreSQL。

## 2. 开始前必须阅读

- `services/api/app/connectors/base.py`
- `services/api/app/connectors/manual.py`
- `services/api/app/connectors/registry.py`
- `services/api/app/api/routes/imports.py`
- `services/api/app/api/routes/connectors.py`
- `services/api/app/services/ingestion.py`
- `services/api/app/services/media_storage.py`
- `services/api/app/services/connector_http.py`
- `services/api/app/services/connector_runner.py`
- `services/api/app/schemas/raw_item.py`
- `services/api/app/models/source.py`
- `services/api/app/models/raw_item.py`
- `services/api/app/models/media_asset.py`
- `docs/MANUAL_IMPORT_GUIDE.md`

共享基础设施已经完成。代码继续使用 `ConnectorItem` / `BaseConnector` 命名，但它们的
语义就是本文所说的 canonical item / provider；不要再做纯命名重构。

已经具备：

- `ManualConnector` 只获取并映射输入；
- `ingest_connector_items()` 统一生成 plain text、去重、媒体落盘并写库；
- 手动导入已经走共享 ingestion；
- `MediaStorage` 自动下载只有 `source_url` 的远程图片；
- `ConnectorHTTPClient` 提供统一 UA、超时和有限重试；
- `connector_registry`；
- `run_connector()`；
- `GET /api/v1/connectors`；
- `GET /api/v1/connectors/runs`；
- `POST /api/v1/connectors/{connector_type}/run`；
- `connector_runs` model；
- `007_add_connector_ingestion.sql` 迁移；
- source/external ID 幂等约束。

另一个对话只实现三个 `BaseConnector.collect()`，注册它们并添加对应 fixture 测试。

## 3. 统一输出契约

每条平台内容最终必须转换为：

```python
ConnectorItem(
    external_id="平台内稳定 ID",
    title="原始标题",
    url="原始永久链接",
    author="原始作者",
    language="en 或 zh-CN",
    published_at=datetime(..., tzinfo=...),
    content="正文纯文本；有 blocks 时可由 blocks 派生",
    content_blocks=[
        {"type": "paragraph", "text": "第一段"},
        {
            "type": "image",
            "source_url": "原始图片 URL",
            "mime_type": "image/jpeg",
            "alt_text": "原始 alt",
            "caption": "原始 caption",
        },
        {"type": "paragraph", "text": "图片后的段落"},
    ],
    raw_payload={
        "provider": "riot_web",
        "fetched_at": "...",
        "source_response": {},
    },
)
```

关键要求：

- `external_id` 优先使用平台 ID；没有 ID 时使用 canonical URL 的稳定哈希。
- `source_url` 必须是用户能打开的原页面，不是内部 JSON 接口。
- `content_blocks` 必须保留文字与图片的原始顺序。
- `plain_text` 由共享 ingestion service 从文字 blocks 生成；三个自动 provider 必须提供
  正文，不能只交 URL。
- `raw_payload` 保留足够的原始字段用于调试，但严禁保存 cookie、token、密码。
- 图片文件由 ingestion 保存到 `apps/web/public/media/<provider_type>/...`。
- `media_assets` 保存路径、原始 URL、位置和 MIME，不保存图片二进制。

## 4. 建议的代码结构

```text
services/api/app/
├── connectors/
│   ├── base.py
│   ├── manual.py
│   ├── riot_official.py
│   ├── tencent_lol.py
│   ├── x_twitter.py
│   └── registry.py
├── services/
│   ├── ingestion.py
│   ├── media_storage.py
│   ├── connector_http.py
│   └── connector_runner.py
└── api/routes/
    └── connectors.py

services/api/tests/
├── fixtures/connectors/
│   ├── riot_news_list.html
│   ├── riot_article.html
│   ├── tencent_news_list.json
│   ├── tencent_article.json
│   └── x_user_tweets.json
├── test_ingestion.py
├── test_riot_provider.py
├── test_tencent_provider.py
└── test_x_provider.py
```

不要让 provider 直接操作 SQLAlchemy model、文件系统媒体目录或 AI。建议接口：

```python
items = await connector.collect(limit=10, since=...)
result = await ingest_connector_items(db, source=source, items=items)
```

`ingest_connector_items` 统一负责：

- `plain_text`；
- `content_hash`；
- source/external ID 去重；
- raw 和 media 的一个数据库事务；
- 返回 `created/skipped` 统计；异常由 runner 记录为 failed run。

## 5. 幂等性与运行记录

provider 会被反复运行，幂等与运行记录已经由共享层解决：

1. `007` 为 `raw_items(source_id, external_id)` 增加条件唯一约束；
2. `connector_runs` 记录开始/结束时间、状态、发现/新增/跳过/失败数量和错误；
3. ingestion 先按 source/external ID，再按 source/content hash 查询重复项。

第一版不需要 cursor 表：每次只取最近 10 条，依靠 ingestion 去重。需要深度翻页时再新增。

同一条内容重复运行：

- 不新增第二条 raw；
- 不新增第二组 media；
- 返回 `skipped=1`；
- 不改变已经 `processed` 的记录。

当前是小规模跑通阶段：第一版只允许用户手动触发，每次默认最多获取 10 条，不加定时器、
消息队列、代理池或大规模分页：

```http
POST /api/v1/connectors/riot_official/run
POST /api/v1/connectors/tencent_lol/run
POST /api/v1/connectors/x_twitter/run
```

请求参数至少支持 `limit`。接口只采集入库，不触发 AI。

## 6. Riot 官网 provider

目标：

- 列表：`https://www.leagueoflegends.com/en-us/news/`
- 第一版分类可先覆盖 `/news/game-updates/` 和 `/news/dev/`；
- 文章语言 `en`；
- source name 建议 `Riot Games Official`；
- `connector_type = "riot_official"`。

实现策略：

1. 直接 HTTP 获取列表 HTML；
2. 提取文章 canonical URL、标题、类别、发布时间和摘要；
3. 获取文章 HTML；
4. 使用站点 CSS selector 定位正文容器；
5. 按 DOM 顺序将 heading、p、ul/ol、blockquote、img 转为 blocks；
6. 站点 selector 失败时，可以用 Trafilatura 提取元数据和正文作为显式 fallback；
7. fallback 也失败时返回错误，不把导航栏全文当正文。

注意：Riot Developer API 主要是比赛、玩家和游戏数据，不是新闻内容 API，不能把
Riot API key 当作新闻 provider 的解决方案。

## 7. 腾讯英雄联盟官网 provider

目标：

- 列表/文章域名：`https://lol.qq.com/`
- 示例永久链接：
  `https://lol.qq.com/news/detail.shtml?docid=1566318436975419583`
- `external_id` 使用 `docid`；
- 语言 `zh-CN`；
- source name 建议 `腾讯英雄联盟官网`；
- `connector_type = "tencent_lol"`。

实现策略：

1. 用浏览器 Network 或页面源代码确认当前列表使用的 JSON/HTML 数据源；
2. 优先消费返回稳定结构化字段的同源 JSON；
3. 保留文章页面 URL 作为 `source_url`；
4. 正文若是 HTML 字符串，用 selectolax 按 DOM 顺序生成 blocks；
5. 正确处理 UTF-8、HTML entity、异常控制字符和懒加载图片属性；
6. 遇到腾讯视频时生成 `video`/`embed` block，不在第一版下载视频；
7. 保存原 JSON 到 `raw_payload`，但控制大小并删除无关追踪字段。

可参考 yt-dlp 的腾讯 extractor 思路和相关 issue 中对
`apps.game.qq.com/cmc/zmMcnContentInfo` 返回结构的处理，但不要复制整个视频下载框架；
该端点属于腾讯内部 Web 接口，必须设计 HTML fallback，并用 fixture 测试防止页面变化。

## 8. X 免费 provider

本阶段明确不使用 X 官方付费 API、Apify、商业代理或其他付费数据服务，也不以 Tweepy
作为默认实现。

第一版只跟踪少量账号白名单、每次最多读取最近 10 条，并且仅由用户手动触发：

```dotenv
X_COOKIE_FILE=.secrets/x-cookies.json
X_FETCH_LIMIT=10
```

每个账号建立独立 Source，`connector_type = "x_twitter"`，`external_key` 保存规范化用户名。

推荐默认实现：`twscrape` 的单账号 cookie 模式。

- 免费、本地运行、Python async；
- 不使用官方 API key；
- 只配置一个专用的低风险 X 账号；
- 每次只读取当前 Source 对应公开账号的最近帖子；
- 禁止启用账号池、批量注册、自动登录邮箱、商业代理和无限重试；
- `raise_when_no_account=True` 且设置有限等待时间；
- cookie 文件必须加入 `.gitignore`，不得进入 `.env.example` 的真实值、数据库、日志或
  `raw_payload`；
- 不建议使用个人主账号，因为内部 GraphQL 抓取可能失效或触发账号限制；
- 必须在使用文档里说明该方式依赖 X Web 内部接口，稳定性和服务条款风险高于官方 API。

`Twikit` 可以作为对比研究或备用 adapter，但第一版只实现一个 X provider，避免维护两套
易变的内部接口。`RSSHub` 可借鉴 route/provider 设计，不需要为三条小规模信源额外部署
完整 RSSHub 服务。

目标字段：

- tweet ID -> `external_id`；
- tweet URL -> `source_url`；
- `created_at` -> `published_at`；
- `lang`；
- 用户 display name/username；
- 完整文本；
- attachments/media；
- 图片 alt text；
- quoted/referenced tweet ID 保存在 `raw_payload`。

图片必须由 ingestion 下载到本地 media 目录，并生成与 tweet 文本顺序一致的 blocks。普通 tweet
通常为一个 paragraph 后跟 media；quoted tweet 第一版只保留引用关系和 URL，不递归无限采集。

没有 cookie、cookie 失效或 X 阻断时必须返回明确配置/采集错误，不能切换付费 API，不能
伪造数据。手动 provider 始终作为可用的最终回退：用户仍可粘贴帖子文本和图片。

## 9. 推荐借鉴的 GitHub 项目

### 优先采用

1. Trafilatura  
   https://github.com/adbar/trafilatura  
   Apache-2.0。借鉴/使用正文、元数据、feed 和 sitemap 提取；适合 Riot/腾讯的通用
   fallback。不要依赖它替代平台特定的有序图文 selector。

2. selectolax  
   https://github.com/rushter/selectolax  
   MIT。适合把 Riot 和腾讯正文 DOM 精确转换为 `content_blocks`，保留图片位置。

3. twscrape  
   https://github.com/vladkens/twscrape  
   MIT。作为 X 免费 provider 的主要参考：使用单账号 cookie、异步读取公开账号最近帖子。
   不照搬多账号池、代理、邮箱自动登录和批量采集功能。

4. Twikit  
   https://github.com/d60/twikit  
   MIT。无需官方 API key 的 X 内部接口封装，可用于对比字段映射和 cookie 保存方式；
   当前第一版不同时实现 twscrape 与 Twikit。

### 只借鉴设计，不建议现在整体引入

5. Scrapy  
   https://github.com/scrapy/scrapy  
   BSD-3-Clause。借鉴 retry、AutoThrottle、middleware、item pipeline 和 fixture 测试
   思路。当前只有三个小 connector，整体引入 Scrapy 会让 FastAPI/asyncio 集成变重。

6. yt-dlp  
   https://github.com/yt-dlp/yt-dlp  
   Unlicense。只借鉴其“每个平台一个 extractor、原响应保留、明确 expected error”的
   结构，以及腾讯视频/内容接口的适配经验；本项目第一版不需要视频下载器。

7. RSSHub  
   https://github.com/DIYgod/RSSHub  
   AGPL-3.0。只借鉴“大量来源 route -> 统一 feed item”的 provider 设计。当前项目不部署
   RSSHub，也不复制其整体运行时。

本阶段不采用 Tweepy/X 官方 API，因为目标明确要求零付费采集；如果未来获得免费的合规
API 权限，可再增加官方 provider，但不得改变 canonical/ingestion 接口。

## 10. 网络与合规要求

- 使用明确 User-Agent；
- 每域名限制并发，第一版建议 `1`；
- 请求超时，建议连接 10 秒、总请求 30 秒；
- 对 429/5xx 做有限次数指数退避；
- 尊重 robots、站点条款和 X API 权限；
- 不绕过验证码、付费墙或访问控制；
- 不使用付费 API、商业采集服务、代理池、多账号池或隐蔽指纹；
- HTTP 错误必须记录 connector run 失败，不能写入残缺 raw；
- 日志不得出现 API token、cookie、数据库密码。

## 11. 测试要求

自动测试禁止依赖实时网站。保存脱敏 fixture，覆盖：

- 列表发现；
- 标题、作者、发布时间、语言；
- 正文段落；
- 图片夹在两个段落之间时的顺序；
- 无 alt 图片；
- 空正文拒绝；
- 同一 external ID 重跑时跳过；
- 网络超时、429 和页面结构变化；
- raw 状态始终为 `pending`；
- connector 不生成 normalized/event；
- X provider 缺少/失效 cookie 时给出明确配置错误。

另提供 opt-in smoke script，用环境变量明确开启后才能访问实时网站；不要让它进入默认
pytest。

## 12. 完成定义

Connector 专项对话结束前必须交付：

- `RiotOfficialConnector.collect()`；
- `TencentLolConnector.collect()`；
- `XTwitterConnector.collect()`；
- 将三个实现注册到现有 `connector_registry`；
- 新依赖和 `.env.example` 配置；
- 三个 source 的初始化迁移或幂等初始化脚本；
- fixture 单测；
- provider/connector run 使用文档；
- 实际运行一次 Riot、腾讯和 X（X 若没有可用 cookie 则演示明确配置错误）；
- 数据库证明新 raw 为 `pending` 且重复运行不重复入库；
- `ruff`、`pytest` 全部通过。

最终 handoff 回主对话时只报告 provider/connector 相关改动、迁移版本、配置项、测试结果和仍存在的
平台限制。
