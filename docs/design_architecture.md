# 设计架构

本文档说明当前代码结构和后续开发边界。原则是：接口稳定、功能不倒退、模块职责清楚。

## 总体分层

```text
页面层
  static/*.html, static/*.js, static/styles.css

API 层
  personal_news_agent/api/schemas.py
  personal_news_agent/api/routes.py

应用装配层
  personal_news_agent/app.py
  personal_news_agent/services/factory.py

业务服务层
  services/chat.py
  services/crawl.py
  services/search.py
  services/tasks.py
  services/reports.py
  services/topic_agent.py
  services/topic_views.py
  ...

数据与外部能力层
  services/store.py
  services/url_store.py
  services/search_index.py
  services/llm.py
  sources.yaml
```

## 入口设计

- `app.py` 是薄入口。
- `factory.py` 负责创建和连接所有 service。
- `routes.py` 负责注册接口。
- `schemas.py` 负责 HTTP 请求模型。

新增功能时不要把业务逻辑写回 `app.py`。

## 后端接口设计

路由层只做四件事：

- 接收请求。
- 做简单参数转换。
- 调用 service。
- 把业务错误映射成 HTTP 状态码。

业务规则应放在 service 层，避免接口函数越来越长。

## 数据能力设计

资讯数据链路：

```text
source 配置
→ section 抓取
→ article 正文抽取
→ SQLite 保存
→ URL store 记录抓取状态
→ 搜索索引写入
→ search/feed/chat/report 使用
```

关键模块：

- `source_registry.py`：读取和校验 source 配置。
- `source_adapter.py`：把网页转成统一文章结构。
- `crawl.py`：调度抓取流程。
- `store.py`：本地数据存储。
- `url_store.py`：URL 抓取状态管理。
- `search.py`：统一搜索入口。
- `search_index.py`：Elasticsearch 适配。

## 对话 Runtime 设计

对话主链路：

```text
用户消息
→ 消息理解
→ topic agent 判断
→ 普通搜索 / 序号追问 / 研究流水线
→ 证据合并
→ 回答和 trace 输出
→ 保存 conversation turn
```

关键模块：

- `chat_understanding.py`：确定性消息理解。
- `chat.py`：对话 runtime 编排。
- `topic_agent.py`：长期主题识别和任务创建。
- `topic_views.py`：专题事件线和关系图。
- `llm.py`：模型调用适配。

后续如果引入更复杂 Agent，不要直接扩大 `chat.py`；优先新增独立 planner/runtime 模块。

## 任务与通知设计

任务链路：

```text
创建任务
→ 计算 next_run_at
→ due 任务扫描
→ 执行报告或主题跟踪
→ 写入通知
```

关键模块：

- `tasks.py`：任务创建、cron 解析、运行。
- `reports.py`：报告生成。
- `store.py`：任务、报告、通知持久化。

## 新功能开发约束

- 不改变已有 API 字段名和默认值，除非同步更新测试和前端。
- 新 service 在 `factory.py` 注入。
- 新接口请求模型放到 `api/schemas.py`。
- 新路由放到 `api/routes.py`，保持薄路由。
- 爬虫增强优先改 `crawl.py` 的公共流程。
- 对话增强优先拆新模块，不继续堆大 `chat.py`。
- 涉及 OpenAI API、Responses API、tool calling 或结构化输出时，先查 OpenAI developer docs MCP。

## 回归要求

改后至少运行：

```bash
python3 -m py_compile $(rg --files personal_news_agent -g '*.py' -g '!._*' -g '!.__*')
pytest -q
```

外置盘可能产生 `._*.py` 文件，不要直接用未过滤的 `compileall personal_news_agent` 判断结果。
