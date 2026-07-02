# 后端模块地图

这份文档用于新人接手时快速定位后端代码边界。当前目标是保持功能和接口效果不变，在原有框架内继续做模块化优化和新增功能。

## 入口与装配

- `personal_news_agent/app.py`
  - 只负责创建 FastAPI 应用、挂载静态资源、注册路由、执行 startup 初始化。
  - startup 会加载 `sources.yaml`、初始化 SQLite/MySQL URL store、同步 source 配置、初始化搜索索引、写入 demo 数据、预置系统主题。
- `personal_news_agent/services/factory.py`
  - 负责集中装配后端服务实例。
  - 新增服务时优先在这里注入依赖，避免让 `app.py` 重新膨胀。

## API 层

- `personal_news_agent/api/schemas.py`
  - 所有 HTTP 请求模型放在这里。
  - 修改接口入参时先确认测试里的兼容性要求，尤其是默认值、范围约束和字段名。
- `personal_news_agent/api/routes.py`
  - 注册现有页面路由和 `/api/*` 路由。
  - 路由层只做请求参数转换、错误码映射和调用 service，不承载业务判断。

## 爬虫与数据能力

- `personal_news_agent/services/source_registry.py`
  - 读取并校验 `sources.yaml`，提供 source/category 选择能力。
- `personal_news_agent/services/source_adapter.py`
  - 列表页解析、文章链接抽取、正文规范化。
- `personal_news_agent/services/crawl.py`
  - 抓取调度入口：按分类抓取、按 due 计划抓取。
  - 共同的 section 抓取流程在 `_crawl_section`，后续加重试、限速、指标优先改这里。
- `personal_news_agent/services/url_store.py`
  - URL 管理与 MySQL fallback 边界。
- `personal_news_agent/services/store.py`
  - SQLite 主存储，目前仍偏大。下一步适合继续拆 schema、用户/认证、文章/搜索、任务/通知、topic 方法组。
- `personal_news_agent/services/search.py` 与 `search_index.py`
  - 本地库、Elasticsearch、外部搜索 provider 的统一召回边界。

## 对话与 Agent Runtime

- `personal_news_agent/services/chat.py`
  - 对话 runtime 编排器：topic agent 检测、序号追问、普通新闻召回、研究流水线、SSE trace。
  - 这里应继续保持“编排优先”，不要把消息理解、搜索实现、数据写入细节继续塞回去。
- `personal_news_agent/services/chat_understanding.py`
  - 从用户消息中抽取序号、分类、主题 query 和时间范围。
  - 后续如果要升级为 LLM 意图识别，可以先保留这个确定性 fallback。
- `personal_news_agent/services/topic_agent.py`
  - 负责从聊天中识别长期主题、创建 topic 和持续跟踪任务。
- `personal_news_agent/services/topic_views.py`
  - 负责专题视图：事件线、关系图、证据视图。
- `personal_news_agent/services/llm.py`
  - 当前是 Chat Completions 兼容客户端。涉及 OpenAI/Responses/工具调用改动时必须先查 OpenAI developer docs MCP。

## 任务、报告与个性化

- `personal_news_agent/services/tasks.py`
  - 定时任务创建、cron 解析、due 运行、通知生成。
- `personal_news_agent/services/reports.py`
  - 专题报告生成和保存。
- `personal_news_agent/services/personalization.py`
  - 用户画像驱动的信息流排序、覆盖与推荐理由。
- `personal_news_agent/services/onboarding.py`、`auth.py`、`realname.py`
  - 注册、初始化、实名 provider 边界。

## 回归命令

```bash
python3 -m py_compile $(rg --files personal_news_agent -g '*.py' -g '!._*' -g '!.__*')
pytest -q tests/test_api.py tests/test_services.py
pytest -q
```

外置盘可能产生 `._*.py` AppleDouble 文件，直接跑 `compileall personal_news_agent` 会误报 null bytes。编译检查请用上面的 `rg --files` 过滤命令。
