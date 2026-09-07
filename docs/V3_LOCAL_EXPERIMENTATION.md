# V3 本地实验入口

当前方法、数据集格式和命令以 [V3_EXPERIMENTS.md](V3_EXPERIMENTS.md) 为准。

- 组件比较：`services/api/evals/phase4_fixture/plans/`。
- 消息分析到日报的组合：`services/api/evals/phase4_fixture/end_to_end/plan.json`，明确属于组件上游输入。
- 原始消息到日报：`services/api/evals/phase4_fixture/raw_to_daily/plan.json`，运行实际消息图。
- 连续事件：`services/api/evals/phase3_fixture/events/`，运行间创建独立 SQLite 内存库。

默认 `--provider fixture`，只替换远端客户端响应；`--provider configured` 才会使用实际模型。
输出放在 `artifacts/experiments`，不提交生成的 HTML/JSON 报告与缓存。
本地实验不需要清空业务数据库，不能修改或覆盖 RawItem 原始证据。

回归检查：

```bash
services/api/.venv/bin/python -m ruff check services/api/app services/api/scripts services/api/tests
services/api/.venv/bin/python -m pytest services/api/tests -q
pnpm lint:web
pnpm build:web
```

PostgreSQL opt-in 测试只设置为明确可销毁的测试数据库，不使用实际业务数据库。
