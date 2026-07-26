# LoL Daily Intel Project Brief

## 项目定位

做一个“英雄联盟垂直领域多信源 AI 情报聚合系统”。用户有真实需求：不想每天分别打开 X/Twitter、微博、腾讯 LOL 官网、贴吧、赛事平台等多个渠道找资讯，希望由系统统一采集、理解、去重、分级，并生成每日资讯网站。

这不是单纯 RAG，也不是纯 Agent。更准确的定位是：

- AI workflow 为主：采集、清洗、分类、去重、聚合、生成日报。
- RAG 为辅：后续用于补充英雄、版本、赛事、选手、战队、历史公告等背景知识。
- Agent 为辅：后续用于事实核查、事件追踪、转会传闻时间线、版本影响分析等复杂任务。

简历包装建议：

> LoL Daily Intel：面向英雄联盟资讯的多源 AI 情报聚合系统。基于 FastAPI、PostgreSQL/pgvector、Next.js 和 LLM workflow，接入官方公告、社交媒体、社区爆料与赛事资讯，对非结构化内容进行实体抽取、语义去重、可信度评估、事件聚合和中文日报生成，支持来源追踪、人工导入和网站展示。

## 为什么是真的 AI 应用

这个项目不是强行套 AI，因为核心问题包含大量非结构化信息理解：

- 判断一条公告、微博、X、贴吧帖子在说什么。
- 抽取英雄、皮肤、版本号、战队、选手、赛事、活动等实体。
- 判断多条消息是否指向同一事件。
- 区分官方确认、高可信爆料、社区传闻、普通讨论。
- 结合多个来源生成一条可读、可追溯的日报资讯。
- 根据用户偏好和领域知识判断重要性。

传统爬虫只能拿内容，AI 负责“读懂、合并、判断、解释”。

## MVP 范围

第一版目标：做一个本地可运行的网站，可以查看“英雄联盟每日资讯”。资讯可以先来自手动导入和少量稳定信源，系统完成 AI 分类、摘要、重要性/可信度标注，并展示在网页上。

第一版不要一开始被 X/微博/贴吧卡住。优先做完整闭环：

1. 前端网站首页展示今日资讯。
2. 后端 API 提供资讯、信源、导入接口。
3. 数据库存储 raw items、normalized items、news events、daily digests。
4. 支持手动粘贴文本/链接导入。
5. LLM 对内容做结构化分析：分类、摘要、实体、重要性、可信度。
6. 后续逐步接入自动信源。

建议第一版页面：

- `/`：今日日报
- `/events`：事件列表
- `/sources`：信源管理
- `/items`：原始内容/导入内容

## 推荐技术栈

建议一开始按完整项目准备，但实现先保持小步推进。

- Frontend: Next.js + React + TypeScript + Tailwind CSS + lucide-react
- Backend: Python + FastAPI + Pydantic + SQLAlchemy
- Database: PostgreSQL through Docker Compose
- Vector/RAG later: pgvector
- Scheduler later: APScheduler first, Celery later if needed
- Crawling: httpx/requests + BeautifulSoup, Playwright only when necessary
- LLM: OpenAI-compatible SDK wrapper, allow OpenAI/DeepSeek/通义/其他兼容模型
- Package tools: pnpm for frontend, uv for backend
- Deploy/dev infra: Docker Compose

## 当前本机环境检查结果

已检测到：

- Git
- Node.js v22.23.1
- npm 10.9.8
- pnpm 11.9.0
- Python 3.13.9 from Anaconda
- uv 0.10.9
- Docker CLI 29.6.1
- Docker Compose v5.2.0

注意：

- Docker Desktop/daemon 当前未运行。`docker info` 报错：无法连接 `npipe:////./pipe/docker_engine`。
- Docker 命令读取 `C:\Users\Administrator\.docker\config.json` 时有 Access denied 警告。先启动 Docker Desktop 后再看是否仍影响实际使用。
- 未检测到本机 `psql`。这不是硬性问题，可以通过 Docker 容器运行 PostgreSQL 和 psql。
- `E:\leagueNews` 当前为空目录，不是 git 仓库。

建议下一步先启动 Docker Desktop，然后验证：

```powershell
docker info
docker compose version
docker run hello-world
```

如果 `hello-world` 能跑，环境就足够开始项目。

## 建议架构

核心数据流：

```text
Source Connector
  -> Raw Item
  -> Normalize
  -> AI Analysis
  -> Event Aggregation
  -> Daily Digest
  -> Website
```

关键原则：

- 采集层和 AI 分析层解耦。
- 每个平台做成可插拔 connector。
- 原始内容永远先入库，避免 AI 处理失败后丢数据。
- 自动采集和手动导入都走同一套 pipeline。
- 日报基于聚合后的 news events，而不是直接总结 raw items。

建议数据表：

- `sources`: 信源配置
- `raw_items`: 原始抓取/手动导入内容
- `normalized_items`: 清洗和结构化后的内容
- `news_events`: 聚合后的新闻事件
- `daily_digests`: 每日生成的日报

## 第一阶段信源策略

优先：

- 手动导入：粘贴文本或链接，保证系统先能用。
- Riot 官方新闻/patch notes。
- 腾讯 LOL 官网公告。
- LPL/赛事官方公告。

后续：

- X/Twitter: SkinSpotlights、Spideraxe、设计师账号等。
- 微博：战队官博、爆料博主、赛事相关账号。
- 百度贴吧半价吧：先抓帖子列表标题和链接，再筛选资讯价值。

注意：X、微博、贴吧都可能有 API 限制、反爬、登录态、页面结构变化。第一版不要把项目成败押在这些平台上。

## 下一轮开工提示词

可以在新对话里直接粘贴：

```text
我想开始实现一个项目：LoL Daily Intel，英雄联盟垂直领域多信源 AI 情报聚合网站。当前工作区是 E:\leagueNews，目录基本为空。请先检查环境和目录，然后帮我初始化项目骨架。

项目目标：
- 做一个本地可运行的网站，展示英雄联盟每日资讯。
- 第一版支持手动导入资讯文本/链接，并用 LLM 做分类、摘要、实体抽取、重要性评分、可信度标注。
- 架构上要支持后续添加多种信源 connector，比如 Riot/腾讯 LOL 官网、X/Twitter、微博、百度贴吧半价吧等。
- 先不要被复杂爬虫卡住，优先做完整闭环：导入 -> 入库 -> AI 分析 -> 事件展示 -> 日报页面。

技术栈倾向：
- 前端：Next.js + React + TypeScript + Tailwind CSS + lucide-react，使用 pnpm。
- 后端：FastAPI + Pydantic + SQLAlchemy，使用 uv。
- 数据库：PostgreSQL through Docker Compose，后续加 pgvector。
- LLM：封装 OpenAI-compatible client，环境变量配置 OPENAI_API_KEY、OPENAI_BASE_URL、MODEL_NAME。

请先做：
1. 检查 Docker 是否可用，若 Docker daemon 未运行请提示我启动 Docker Desktop。
2. 设计一个简洁的项目目录结构。
3. 初始化 git 仓库、前端、后端、docker-compose、env 示例文件。
4. 建立最小数据库模型和 API 草案，先支持 sources、raw_items、news_events。
5. 做一个基础网页，能显示样例日报/事件列表。

要求：
- 不要一上来做复杂 Agent；先用稳定 workflow。
- 采集层、AI 分析层、展示层要解耦。
- 每个平台信源以后通过 connector 插件方式加入。
- 每次改动前说明要做什么，完成后给出运行命令和验证结果。
```

## 面试叙述要点

可以这样解释项目动机：

> 我自己长期关注英雄联盟版本、皮肤、赛事和转会资讯，但这些信息分散在 X、微博、官网、贴吧、赛事平台等不同渠道。普通 RSS/爬虫只能聚合内容，不能判断重要性、去重、区分可信度，也不能把中英文和公告/爆料/讨论统一整理成日报。所以我做了一个垂直领域 AI 情报系统，用 LLM 做分类、实体抽取、语义去重、可信度评估和日报生成。

重点强调：

- 多源数据接入
- 非结构化文本理解
- AI workflow
- 来源追踪
- 可信度建模
- 事件聚合
- 可插拔 connector 架构
- 真实个人需求驱动，不是模板项目
