# Skill 体系统一与 `/changed`、`/compare` 设计

> 文档状态：设计已确认，第二阶段实现已于 2026-08-14 完成并通过本地回归。第 2-17 节保留 2026-08-13 的调查与批准设计，第 18 节记录实际实现结果。

### 调查版本快照

- 调查日期：2026-08-13（Asia/Shanghai）。
- 分支：`codex/zdr-cc-runtime-orchestrator`。
- HEAD：`cae1e4c3a89ea4984e12132a214ea39c5833b9e1`（`refactor: redesign personal news assistant UI`）。
- 调查时工作区：相对 HEAD 已有未提交修改；`git status --short --branch` 显示 `.env.example`、`app.py`、`api/routes.py`、`services/*`、`static/*`、`tests/*` 等多项已修改文件，以及事件聚合、修复脚本、SQLite 升级脚本和 `docs/development/` 等未跟踪路径。调查结论基于该 dirty worktree 的实际文件，不代表干净 HEAD 的行为。
- 本文唯一新增/修改目标是 `docs/development/skill-system-upgrade.md`；不回退、不覆盖上述用户改动。

### 实施版本快照

- 实施日期：2026-08-14（Asia/Shanghai）。
- 分支：`codex/zdr-cc-runtime-orchestrator`。
- 实施起点 HEAD：`3637a3b880c16b76f77566e787197c3050d87470`（`feat: aggregate news into canonical events`）。
- 实施工作区继续为 dirty worktree；用户同步修改的 `static/home.html`、`static/home.js`、`static/newsroom.css`、`static/web.js`、相关前端测试和运行态文件均保留。本实现只在公共渲染层 `static/shared.js` 增加菜单/卡片协议，没有回退或覆盖上述页面改动。
- 本次没有数据库迁移、提交、推送或 PR。

## 1. 目标、边界与设计结论

本次升级的目标不是强行让“网页 Skill 数量”和“.claude Agent Skill 数量”相等，而是建立一份可校验的唯一声明目录，使每项能力的角色、入口、处理器、Agent 工作流、联网政策、副作用、输出类型和降级路径都可追踪。推荐方案是：

1. 在 `personal_news_agent/skills/manifest.py` 定义类型，在 `personal_news_agent/skills/catalog.py` 声明所有 Skill；声明使用稳定字符串引用 handler，避免目录与运行时模块循环依赖。
2. `SkillRegistry` 从 catalog 构建命令和别名索引，CC Runtime 从同一 catalog 派生 Agent Skill 白名单，`GET /api/skills` 从同一 catalog 投影公开字段，前端从该接口生成菜单。
3. 在 `personal_news_agent/services/skill_router.py` 建立独立 `SkillRouter`；`chat.py` 只负责调用路由、执行结果和保存对话，不再继续堆意图规则。
4. `/changed` 和 `/compare` 各自拥有薄 Skill handler、独立业务 Service、独立 Agent Skill、小型结果校验模型和专用前端卡片。
5. 保持 `ChatResponse` 和旧斜杠命令兼容，在 `SkillResult`/`ChatResponse` 上增量增加统一信封字段，不创建囊括全部业务字段的巨型 Schema。

不采用以下方案：

- **继续扩充每个 handler 上的 `SkillSpec`**：改动较小，但无法让 Agent-only Skill 进入同一目录，也无法消除 CC Runtime 和前端硬编码。
- **以 YAML 作为运行时唯一来源**：可配置性高，但当前项目是静态 Python 组装；会额外引入解析、导入、类型和部署错误面。V1 用类型化 Python catalog 更容易在启动和测试时失败得清楚。
- **让模型直接决定并执行多个 Skill**：与 V1 单 Skill、安全确认和低置信度回退要求冲突。

## 2. 真实现状调查

### 2.1 完整请求链路

当前真实链路如下：

```text
用户输入
  -> Web/Mobile parseAssistantCommand（若以 / 开头，部分命令先在浏览器改写或直接操作页面）
  -> POST /api/chat 或 POST /api/chat/stream
  -> routes.register_routes().chat/chat_stream
  -> NewsChatService.chat()/chat_events()
  -> _skill_response()（只识别以 / 开头的消息）
  -> SkillRegistry.execute() -> 具体 Python Skill.run()
  -> 独立 Service，部分再调用 CCRuntimeOrchestrator.run(agent_skill)
  -> SkillResult(command/title/message/data)
  -> NewsChatService 将其包装为 ChatResponse(skill_result/evidence/markdown/...)
  -> shared.js 的 chatResponseHtml()
  -> /factcheck、/report 特判卡片，其余 Markdown/Mermaid/通用证据区域
```

具体证据：

- API 入口为 `personal_news_agent/api/routes.py` 的 `register_routes()`、内部 `chat()` 与 `chat_stream()`；它们把 `allow_web_search`、`model_key` 和对话上下文传给 `NewsChatService`。
- `personal_news_agent/services/chat.py` 的 `NewsChatService.chat()` 依次执行输入审核、`_skill_response()`、`_schedule_command_response()`、长期主题创建、序号追问、通用研究/搜索；`chat_events()` 有一套对应流式分支。
- `NewsChatService._skill_response()` 明确要求文本以 `/` 开头，构造 `SkillContext` 后调用 `SkillRegistry.execute()`，再把 `SkillResult.data` 复制到 `ChatResponse`。
- `personal_news_agent/skills/registry.py` 的 `SkillRegistry.execute()` 使用 `shlex.split()`，只按首 token 精确查 `_skills`，当前没有自然语言分类或 Python 侧别名。
- `personal_news_agent/services/factory.py` 的 `build_services()` 创建 registry、search、reports、factcheck、tasks、CC Runtime 和 chat，并将互相依赖的 service 字典注入 Skill。
- `personal_news_agent/services/cc_runtime.py` 的 `CCRuntimeOrchestrator.build_options()` 仅在选择了 Agent Skill 时开放 `Skill` 工具；本地搜索、应用外部搜索与内建 `WebSearch` 又分别受运行上下文控制。
- `personal_news_agent/static/shared.js` 的 `chatResponseHtml()` 负责通用 Markdown、trace、event line 和证据渲染；事实核查与报告下载当前按命令字符串特判。

### 2.2 当前网页可执行命令

“网页可执行命令”包含两类，不能都叫 Python Skill。

#### A. Python Registry Skill（当前 7 个）

| 命令 | Python handler | 主要 Service / Agent Skill | 当前角色 | 当前输出 |
| --- | --- | --- | --- | --- |
| `/report` | `ReportSkill.run()` | `ReportGenerationService`; `news-topic-report` | public | report data + Markdown，前端报告下载特判 |
| `/brief` | `BriefSkill.run()` | `ReportGenerationService`; `news-daily-brief` | public | report data + Markdown |
| `/factcheck` | `FactCheckSkill.run()` | `FactCheckService`; `news-fact-check` | public | factcheck data，前端事实核查卡片特判 |
| `/map` | `HotEventMapSkill.run()` | `UnifiedSearchService`; `hot-event-map` | public | Markdown + Mermaid + evidence |
| `/related` | `RelatedNewsSkill.run()` | `NewsChatService.related_search()`; `news-related-exploration` | 目前 public，建议 context_action | 通用 ChatResponse data + Markdown/mind map |
| `/sources` | `SourcesSkill.run()` | `SourceRegistryService`; `news-source-audit` | 目前 public，建议 admin | 来源清单 + 可选 Markdown |
| `/schedule` | `ScheduleSkill.run()` | `ScheduledTaskService`; `scheduled-news-task` | public 高级入口，write | task data + Markdown |

注册顺序和数量以 `personal_news_agent/skills/registry.py::build_default_registry()` 为准。`personal_news_agent/skills/README.md` 却写着“当前注册六个”，只列 `/report`、`/brief`、`/factcheck`、`/map`、`/related`、`/sources`，漏掉已经注册并在前端展示的 `/schedule`，因此 README、代码和前端数量不一致。根 `README.md` 分散描述了 `/schedule`、`/factcheck`、`/map`，没有一份与 registry 同源的完整公开清单。

#### B. 浏览器命令/别名（不是独立 Python Skill）

`personal_news_agent/static/web.js::handleAssistantInput()` 和 `personal_news_agent/static/mobile.js::handleMobileAssistantInput()` 还识别：

- `/search`、`/s`、`/news`：设置主题后改写成普通新闻研究消息；
- `/deep`、`/dive`、`/deep-dive`：Web 调深挖操作后再发普通总结，移动端直接改写为普通研究；
- `/topic`、`/t`：只切换页面当前主题；
- `/task`、`/track`：直接创建跟踪任务，属于上下文写操作；
- `/ingest`、`/source`：Web 触发原生源更新；移动端没有此分支；
- `/feed`：刷新/筛选信息流；
- `/r` -> `/report`、`/verify` -> `/factcheck`、`/graph` -> `/map`：只在前端转换，直接请求后端时不兼容。

`personal_news_agent/static/shared.js::assistantSlashCommands` 菜单则只硬编码 7 个 canonical Registry Skill。这造成“菜单、浏览器解析器、后端注册器”三套命令语义。V1 Manifest 只统一 Skill 与其 aliases；`/search`、`/topic`、`/feed`、`/deep`、`/ingest`、`/task` 等页面工作台操作要登记为 `context_action`（可经 `/api/skills` 的 context-action 投影提供给对应页面），但不强制伪装成 Python Skill handler。

### 2.3 `.claude/skills` 中的全部 Agent Skill（当前 8 个）

| Agent Skill | 用途 | 当前调用方 | 与用户 Skill 的关系 |
| --- | --- | --- | --- |
| `news-conversation-research` | 普通新闻提问、追问、现状、原因、影响、比较等通用研究 | `NewsChatService._research_chat()` | internal；无专属斜杠命令，是普通研究默认 Agent 工作流 |
| `news-fact-check` | 原子命题、多源证据、保守 verdict | `FactCheckService.run()` | `/factcheck` 的 Agent 增强层 |
| `hot-event-map` | 主体、关系、时间线、影响的 Mermaid 图谱 | `HotEventMapSkill.run()` | `/map` 的 Agent 增强层 |
| `news-related-exploration` | 在当前对话中消歧并延展人物/事件 | `NewsChatService.related_search()` | `/related` 的 Agent 增强层；建议 context_action |
| `news-topic-report` | 生成证据化专题报告 | `_cc_enrich_report_payload()` | `/report` 的 Agent 增强层 |
| `news-daily-brief` | 生成近期简报 | `_cc_enrich_report_payload()` | `/brief` 的 Agent 增强层 |
| `news-source-audit` | 对配置来源做覆盖与可用性审计 | `SourcesSkill.run()` | `/sources` 的 Agent 增强层；建议 admin |
| `scheduled-news-task` | 把自然语言周期要求解析为任务 API 参数 | `ScheduledTaskService` | `/schedule` 的解析层；应用仍负责校验与持久化 |

每个目录都有 `SKILL.md` 和 `agents/openai.yaml`。真正运行白名单不是扫描目录得到的，而是 `personal_news_agent/services/cc_runtime.py::ALLOWED_PROJECT_SKILLS` 手写的 8 个常量；`_validated_skill_names()` 再据此拒绝未知项。`build_options()` 通过 `setting_sources=["project"]` 和 `skills=selected_skills` 加载项目 Skill。

### 2.4 当前角色、硬编码位置与能力成熟度

当前代码没有 `exposure` 概念，以下是按真实行为归类后的建议目标：

| 能力 | 目标 exposure | 原因 |
| --- | --- | --- |
| brief、factcheck、map、report、changed、compare、schedule | `public` | 用户可直接输入；schedule 在菜单中标记高级且具有确认规则 |
| related | `context_action` | 依赖当前文章/事件/对话语境，保留 `/related` 兼容但从通用菜单移到卡片操作 |
| sources | `admin` | 是来源配置与可用性审计，不是一般新闻问答；入口移到设置/来源管理 |
| news-conversation-research | `internal` | 普通研究默认工作流，不应成为用户命令 |
| search/topic/feed/deep/ingest/task 等页面动作 | `context_action` 或 `admin` | 是 UI 工作台动作而非 Registry Skill；写操作仍须保持已有鉴权/确认边界 |

硬编码点：

- Python 注册器：`personal_news_agent/skills/registry.py::build_default_registry()`；
- CC Runtime 白名单：`personal_news_agent/services/cc_runtime.py::ALLOWED_PROJECT_SKILLS` 与多个 `*_SKILL_NAME` 常量；
- 前端菜单：`personal_news_agent/static/shared.js::assistantSlashCommands`；
- Web/移动命令分派：`personal_news_agent/static/web.js::handleAssistantInput()`、`personal_news_agent/static/mobile.js::handleMobileAssistantInput()`；
- 前端输出特判：`shared.js::chatResponseHtml()`、`renderReportDownloads()`；
- README 手写清单：`personal_news_agent/skills/README.md` 与根 `README.md`。

能力成熟度：

- 已有独立业务 Service：factcheck -> `FactCheckService`；report/brief -> `ReportGenerationService`；schedule -> `ScheduledTaskService`；related 的核心仍在超大 `NewsChatService.related_search()`；map handler 自身同时承担编排、清洗和 fallback。
- 主要是通用提示词/Agent 包装：普通 `news-conversation-research`；report/brief 的 CC 部分由共享 `_cc_enrich_report_payload()` 包装；source audit 的 Agent 部分直接写在 handler；当前没有 changed/compare 专用服务或小型结果模型。
- `TopicViewService` 和事件聚合可提供时间线、事件身份和专题证据，但不等于 changed/compare 的业务判定器。

### 2.5 `/schedule` 重复路径

重复路径真实存在，但同步与流式的命中表现不同：

1. `NewsChatService.chat()` 先调用 `_skill_response()`，所以正常情况下 `/schedule` 已由 `ScheduleSkill` 创建任务，随后 `_schedule_command_response()` 不再命中。
2. `NewsChatService.chat_events()` 只要消息以 `/` 开头便执行 registry 并立即 return，同样由 `ScheduleSkill` 创建。
3. `NewsChatService._schedule_command_response()` 仍保留一套直接调用 `ScheduledTaskService.create_from_schedule_message()` 并拼装 `ChatResponse` 的完整创建逻辑，形成不可达或仅在 registry 缺失/变化时触发的第二实现。
4. `personal_news_agent/api/routes.py::create_schedule_task()` 又提供 `/api/tasks/schedule` 的直接 API，这是合理的服务 API，不属于聊天内重复，应该保留并共用同一个 `ScheduledTaskService`。

后续删除的是 `NewsChatService._schedule_command_response()` 及两个调用点，不删除 `/schedule`、`ScheduleSkill` 或 `/api/tasks/schedule`。

### 2.6 `/factcheck` 联网开关缺陷

`FactCheckSkill.run()` 调用 `FactCheckService.run(..., include_remote=True)`，没有使用 `SkillContext.allow_web_search`。因此即使 API 请求和前端开关传入 `False`，FactCheckService 仍会：

- 把 `include_remote=True` 传给 CC Runtime 的 `allow_web_search`；或
- fallback 时在 `FactCheckService.run()` 的 `if include_remote:` 分支调用 `search_service.search_external()`。

现有测试 `tests/test_services.py::test_factcheck_skill_forces_remote_search_when_chat_toggle_is_off`（其断言显示 `search.external_calls` 非空）实际固化了错误行为。实施时必须先把它改成“关闭时无任何外部调用”的契约测试，再让 handler 传 `context.allow_web_search`。离线核查仍可使用本地索引、已存正文和当前对话 evidence，并采用一套无矛盾的状态规则：

- 本地/当前对话证据足以支持一个符合 FactCheck 契约的保守 verdict 时，顶层 `status=success`，`fallback_reason=None`，`skill_result.data.research_mode="local_only"`；文案明确“本轮仅使用本地证据”。关闭联网是用户选择，不自动等于系统降级。
- 本地证据不足以完成核查时，业务 verdict 为 `insufficient`，顶层 `status=degraded`，`fallback_reason=web_search_disabled`，`research_mode="local_only"`；文案说明联网关闭导致无法补齐外部/一手证据。
- 无论本地证据是否充分，`allow_web_search=False` 时都不得调用应用外部 provider、CC 应用 web tool 或内建 `WebSearch`，也不得宣称完成联网核查。

### 2.7 现有结果、证据与前端协议

- `personal_news_agent/skills/base.py::SkillResult`：`command/title/message/data`，无执行 `status`、`output_kind`、统一 `evidence` 或 `fallback_reason`。
- `personal_news_agent/core/models.py::ChatResponse`：已有 `answer/markdown/context_relation/recommendations/research_trace/evidence/event_line/mind_map/skill_result`，但也没有统一执行状态和输出类型。
- evidence 目前是无统一模型的 `dict`。`chat.py::_evidence_payload()`、`factcheck.py::FactCheckService._evidence()`、`skills/report.py::_runtime_evidence()`、`skills/hot_event_map.py::_runtime_evidence()` 各自构造相似但不完全一致的字段。
- `FactCheckResponse` 单独拥有 supporting/contradicting evidence；`factcheck.py::_evidence_refs()` 能把模型返回的 index/URL约束回本轮 evidence，这一思路应复用。
- 但 `factcheck.py::_append_declared_external_evidence()` 当前会在观测到内建 WebSearch 调用后，把 Agent 最终 JSON 中声明的 HTTPS URL追加为 evidence；它验证 URL 格式，却未与工具实际返回 URL ledger 做交集，仍有“模型声明 URL 被当作本轮证据”的缺口。
- `shared.js::chatResponseHtml()` 通过 `skill_result.command === "/factcheck"` 渲染事实核查卡片，`renderReportDownloads()` 通过 `/report` 特判下载；未知 Skill 主要显示 Markdown。没有 `output_kind -> renderer` 注册表。

### 2.8 普通自然语言是否稳定进入专门 Skill

不能。`NewsChatService._skill_response()` 只接受 `/` 开头的输入。普通自然语言依据 `_is_general_conversation()` 和 `use_llm` 进入通用对话、普通新闻研究或本地搜索；`chat_understanding.py` 只做序号、类别、查询和时间范围提取。虽然 `news-conversation-research` 的 description 提到 comparisons，`news-daily-brief` 的 description 提到 what changed today，但普通输入并不会稳定选择这些专门 Agent Skill，更不会产生专门的结构化输出。

## 3. 统一 Skill Manifest

### 3.1 类型与字段

在 `personal_news_agent/skills/manifest.py` 定义冻结 dataclass `SkillDefinition`（外部名称也可称 Skill Manifest），建议字段如下：

```python
@dataclass(frozen=True)
class SkillDefinition:
    id: str
    name: str
    commands: tuple[str, ...]
    aliases: tuple[str, ...]
    exposure: Literal["public", "internal", "admin", "context_action"]
    description: str
    intent_examples: tuple[str, ...]
    handler: str | None
    arguments_model: str | None
    agent_skill: str | None
    required_services: tuple[str, ...]
    network_policy: Literal["local_only", "user_controlled"]
    side_effect: Literal["read_only", "creates_task", "updates_state", "admin_write"]
    confirmation_required: bool
    output_kind: str
    citation_policy: Literal["none", "evidence_if_claims", "evidence_required"]
    fallback_policy: Literal["local_service", "local_evidence_only", "default_markdown", "blocked"]
    enabled: bool = True
```

字段语义：

| 字段 | 用途与约束 |
| --- | --- |
| `id` | 稳定机器 ID，如 `changed`；不带 `/`，不可因显示名称变更而改变 |
| `name` | 用户或管理界面显示名 |
| `commands` | canonical 斜杠入口；可以为空（internal Agent-only），首项为主命令 |
| `aliases` | 兼容别名，如 `/verify`、`/graph`、`/r`；必须带 `/`，不能与任何 command/alias 冲突 |
| `exposure` | `public` 通用菜单；`internal` 仅内部编排；`admin` 仅管理入口；`context_action` 仅上下文卡片/特定页面 |
| `description` | 简短公开说明；不得暴露内部路径、模型或凭据 |
| `intent_examples` | Router 的正例与离线评测样本，不直接当宽松关键词表 |
| `handler` | `module:Class` 字符串或 `None`；Registry 启动时解析并实例化，Agent-only/context-only 可为空 |
| `arguments_model` | `module:Model` 字符串或 `None`；自然语言结构化执行的 Pydantic 参数契约，可路由 Skill 必填 |
| `agent_skill` | `.claude/skills/<name>` 目录名或 `None`；从此字段派生 CC 白名单 |
| `required_services` | handler 执行前必须存在的 `services` key；启动校验而不是运行中才 KeyError |
| `network_policy` | `local_only` 不允许公共 Web Search；`user_controlled` 必须服从 `SkillContext.allow_web_search`。模型服务调用与公共 Web Search 分开管理 |
| `side_effect` | 明确只读、创建任务、更新状态或管理写入；V1 Router 只自动执行 `read_only` |
| `confirmation_required` | 自然语言路由是否先返回确认状态；`schedule=True`。显式旧斜杠入口按兼容规则处理 |
| `output_kind` | 前端 renderer key，如 `default_markdown`、`fact_check`、`event_map`、`change_digest`、`coverage_compare` |
| `citation_policy` | 无引用、事实主张时引用、或必须有本轮 evidence |
| `fallback_policy` | Agent/模型不可用时采用本地服务、本地证据、Markdown 或阻断；具体原因写入结果 |
| `enabled` | 灰度/关闭入口；关闭时不注册命令、不公开、不进入 Agent 白名单或自然语言路由 |

`usage` 与前端参数提示可作为后续非必要公开字段；V1 为兼容当前 `SkillSpec`，可先从 `commands[0]`、`name`、`description` 和现有 handler `spec.usage/examples` 生成旧 `SkillSpec`。不要让旧 `SkillSpec` 继续成为第二声明源。

### 3.2 Catalog 内容与映射

`personal_news_agent/skills/catalog.py` 保存不可变 `SKILL_CATALOG`。建议核心项：

| id | commands/aliases | exposure | handler | agent_skill | side effect | output kind |
| --- | --- | --- | --- | --- | --- | --- |
| `brief` | `/brief` | public | `BriefSkill` | `news-daily-brief` | read_only | `news_brief` |
| `factcheck` | `/factcheck`; `/verify` | public | `FactCheckSkill` | `news-fact-check` | read_only | `fact_check` |
| `map` | `/map`; `/graph` | public | `HotEventMapSkill` | `hot-event-map` | read_only | `event_map` |
| `report` | `/report`; `/r` | public | `ReportSkill` | `news-topic-report` | read_only | `topic_report` |
| `changed` | `/changed` | public | `ChangedSkill` | `news-change-digest` | read_only | `change_digest` |
| `compare` | `/compare` | public | `CompareSkill` | `news-coverage-compare` | read_only | `coverage_compare` |
| `schedule` | `/schedule` | public | `ScheduleSkill` | `scheduled-news-task` | creates_task | `schedule_result` |
| `related` | `/related` | context_action | `RelatedNewsSkill` | `news-related-exploration` | read_only | `related_research` |
| `sources` | `/sources` | admin | `SourcesSkill` | `news-source-audit` | read_only | `source_audit` |
| `conversation_research` | 无 | internal | `None` | `news-conversation-research` | read_only | `default_markdown` |

浏览器 `/search`、`/topic`、`/feed`、`/deep`、`/task`、`/ingest` 可在一个相邻的 `CONTEXT_ACTION_CATALOG` 登记 UI action metadata；它们不是 `SkillRegistry.execute()` 的候选，避免把页面状态操作误建模为后端 Skill。`/task` 和 `/ingest` 属写操作，必须保留现有权限/交互，不进入自然语言 SkillRouter V1。

### 3.3 注册、白名单、接口与前端

#### Python Registry

`build_default_registry()` 改为接收 catalog：过滤 `enabled and handler and commands`，解析 handler，校验 `required_services`（可在 service 完成组装后调用 `registry.bind_services()`），并把 command 与 alias 都映射到同一实例。执行结果始终回填 canonical command 和稳定 `skill_id`，即 `/verify x` 的结果仍是 `skill_id=factcheck, command=/factcheck`。

兼容策略：保留 `SkillRegistry.register(skill)` 和 `SkillSpec` 一个版本周期，内部把 legacy skill 转成临时 `SkillDefinition`；测试和新代码统一使用 catalog。旧第三方调用 `registry.execute(command_text, SkillContext(...))` 不变。若 legacy 注册与 catalog 命令冲突，启动时明确失败。

#### CC Runtime 白名单

提供 `catalog.enabled_agent_skills()`：取所有 `enabled=True` 且 `agent_skill` 非空的唯一值。`cc_runtime.py` 删除手写 `ALLOWED_PROJECT_SKILLS` 内容，`_validated_skill_names()` 使用派生集合。启动校验逐项确认：

1. `.claude/skills/<agent_skill>/SKILL.md` 存在；
2. frontmatter `name` 与目录/manifest 完全一致；
3. 可选 `agents/openai.yaml` 格式有效；
4. 不存在 catalog 未声明却被运行时选择的 Agent Skill；
5. Agent-only Skill（如 `news-conversation-research`）允许没有网页 command/handler。

#### `GET /api/skills`

新增只读接口，默认只返回 `enabled` 且 `exposure=public` 的项目：

```json
{
  "items": [
    {
      "id": "factcheck",
      "name": "事实核查",
      "commands": ["/factcheck"],
      "aliases": ["/verify"],
      "description": "...",
      "exposure": "public",
      "output_kind": "fact_check",
      "side_effect": "read_only",
      "confirmation_required": false,
      "network_policy": "user_controlled"
    }
  ]
}
```

绝不返回 `handler`、`agent_skill`、`required_services`、fallback 内部细节或任何配置。管理/上下文入口需要时使用明确的、受控的 query/独立端点投影，而不是让匿名 public API 返回 internal/admin 项。`/api/skills` 顺序由 catalog 的显式顺序决定，避免前端自行排序漂移。

#### 前端菜单与渲染

`shared.js` 启动时调用 `/api/skills`，将 public items 写入 `assistantSlashCommands`。接口失败时隐藏斜杠菜单并保留输入框的手动命令能力、已存在的卡片按钮和服务端命令解析；页面给出非阻塞提示“技能菜单暂不可用，仍可手动输入命令”。V1 不维护任何静态业务 Skill fallback 清单。Web 和移动共用该加载结果。`related` 从文章/事件/关系卡片触发，`sources` 移到设置或来源管理入口；旧命令文本仍可提交。

建立 `outputRenderers`：

```text
default_markdown -> 现有 Markdown
fact_check -> 事实核查卡片
topic_report -> Markdown + 下载
event_map -> Mermaid + Markdown
change_digest -> 新变化卡片
coverage_compare -> 媒体对比卡片
```

找不到 renderer 时必须调用 `default_markdown`，不能显示空白或抛异常。前端不依据 `/command` 判断业务类型。

### 3.4 一致性校验

新增启动校验和测试，失败信息包含 `skill_id`：

- `id` 唯一；commands/aliases 全局唯一、格式正确；
- 非 internal 且有 command 的 Skill 必须有可导入 handler；
- handler 必须实现 `run(args, context)`；可选 `run_structured(model, context)` 的参数类型必须与 `arguments_model` 一致；其 legacy `spec.command`（过渡期）必须等于 canonical command；
- 所有自然语言 routable Skill 必须有可导入且 `extra="forbid"` 的 `arguments_model`，并能从文本 parser/结构化 adapter 到达同一业务调用；
- `required_services` 都能由 `build_services()` 提供；
- `agent_skill` 目录/frontmatter 存在且一致；
- `output_kind` 必须在后端允许集合和前端 renderer/fallback 契约中登记；
- `side_effect != read_only` 且可被自然语言路由时必须 `confirmation_required=True`；
- `network_policy=user_controlled` 的 handler/Service 测试必须证明 `allow_web_search=False` 时外部 provider 和内建 WebSearch 都不可达；
- 引用型输出必须通过 evidence ledger 校验。

## 4. 独立 SkillRouter

### 4.1 模块与返回模型

新增 `personal_news_agent/services/skill_router.py`：

```python
class SkillRoute(BaseModel):
    skill_id: str | None
    arguments: dict[str, Any] = Field(default_factory=dict)
    confidence: float
    source: Literal["slash_command", "rule", "model", "fallback"]
    confirmation_required: bool = False
```

`skill_id=None, source=fallback` 表示继续当前普通新闻研究，不是错误。路由器只做选择和参数提取，不检索、不持久化、不调用 handler。

调用签名明确接收本次请求的模型选择：

```python
async def route(
    self,
    message: str,
    *,
    context: SkillRoutingContext,
    model_key: str,
    use_llm: bool,
) -> SkillRoute: ...
```

`NewsChatService.chat()` 与 `chat_events()` 把本次 `ChatRequest.model_key` 和 `ChatRequest.use_llm` 原样传入 `SkillRouter.route()`；规则和显式斜杠分支不使用模型。只有 `use_llm=True` 时模型分类分支才可调用 `LLMClient.structured(..., model_key=model_key)`。路由器不得读取全局前端偏好或自行换模型；空值/失效值继续由现有 `ChatRequest` 校验和 `get_model_option()` 处理，未显式提供时使用 `ChatRequest` 的 `DEFAULT_LOGICAL_MODEL` 默认值。

### 4.1.1 路由参数与 handler 执行协议

Router 只返回稳定 `skill_id` 和经过对应 Pydantic 参数模型校验的字典；Registry 负责把结构化参数适配给 handler。正式提供两个执行入口：

```python
async def execute_text(self, text: str, context: SkillContext) -> SkillResult: ...

async def execute_structured(
    self,
    skill_id: str,
    arguments: dict[str, Any],
    context: SkillContext,
) -> SkillResult: ...
```

- `execute_text()` 是旧斜杠兼容入口：沿用 `shlex.split()`、command/alias 解析和现有错误文案；现有 `execute(text, context)` 保留一个版本周期并直接委托 `execute_text()`。因此旧调用方、旧链接和手动命令不需要迁移。
- `execute_structured()` 是自然语言正式入口：按 Manifest 的 `arguments_model` 找到对应 Pydantic 模型，拒绝额外字段和类型错误，再交给参数 adapter；不把结构化字典重新拼成一整条 shell 字符串，也不经过 `shlex.split()`。
- Manifest 增加可选 `arguments_model: str | None`，值为 `module:Model` 字符串。所有可自然语言路由的 Skill 必须配置；internal/context-only 且不可路由项可为空。启动校验要验证模型可导入。
- 过渡期 handler 协议仍是 `run(args: list[str], context)`。每个 `arguments_model` 同模块提供确定性的 `to_legacy_argv(model) -> list[str]` adapter，例如 topic 作为普通 token、category 作为 `--category tech,sports`；adapter 只能序列化已验证字段，不能接受任意 flag。Registry 调用同一个 handler 实例的 `run(argv, context)`。
- 新 `/changed`、`/compare` 也先遵守这一薄 handler 协议；业务 Service 接收 Pydantic 模型，而不是在 Service 内解析 argv。handler 用共享 parser 把 adapter 产生的 argv 转回模型属于不必要往返，因此推荐 handler 同时实现 `run_structured(arguments_model, context)`；Registry 优先调用该方法，旧文本入口则先由专属 parser 得到同一参数模型。没有 `run_structured` 的旧 handler 才使用 `to_legacy_argv()` 兼容 adapter。
- 两条入口最终都必须得到同一参数模型和同一个 handler/Service 调用，不能维护“斜杠算法”和“自然语言算法”两套业务实现。

V1 新 Skill 参数模型：

```python
class ChangedArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str | None = Field(default=None, max_length=240)
    baseline_expression: str | None = Field(default=None, max_length=80)
    category_scope: list[str] = Field(default_factory=list, max_length=8)

class CompareArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str | None = Field(default=None, max_length=240)
    category_scope: list[str] = Field(default_factory=list, max_length=8)
    source_scope: list[str] = Field(default_factory=list, max_length=16)

class ScheduleArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw_request: str = Field(min_length=1, max_length=2000)
```

`ChangedArguments.topic`/`CompareArguments.topic` 为空时由 `SkillContext.topic` 补足；仍为空则返回参数错误。`baseline_expression` 只保存“昨天/过去一周/自 8 月 1 日”等用户表达，由 ChangeDetectionService 按应用时区解析，Router 不自行计算日期。Compare 的 source/category scope 只能来自用户明示或当前上下文。Schedule Router 只传原始用户请求，不直接生成 cron、preview 或 owner；`ScheduledTaskService.prepare_schedule_preview()` 负责权威解析与校验，避免把 Router 模型输出当作可执行任务参数。

同步与流式入口共用：

```python
async def _route_and_execute_skill(..., emit_trace=None) -> ChatResponse | None: ...
```

`chat()` 以 `emit_trace=None` 调用并等待结果；`chat_events()` 传入 queue emitter，把同一函数产生的 trace 转为 SSE，最终包装完全相同的 `ChatResponse`。该函数统一执行 moderation 之后的 route、fallback、confirmation gate、`execute_text/execute_structured`、结果包装和保存前净化；流式入口不得再用 `message.startswith("/")` 维护第二条 Skill 分支。

### 4.2 固定路由顺序

1. **明确斜杠命令**：Registry command/alias 精确匹配；`confidence=1.0`、`source=slash_command`。未知斜杠保持现有“未知 Skill”错误，不交给模型猜。
2. **高精度规则**：要求动作词 + 新闻域锚点/上下文，排除非新闻文本操作；仅规则置信度 `>=0.90` 才自动选择。
3. **模型结构化分类**：只有规则无结果、文本确实像新闻任务且 `use_llm=True` 时调用现有 `LLMClient.structured(..., model_key=model_key)`；schema 限制为 enabled routable skill IDs 或 `none`。`model_key` 由本次 `ChatRequest` 经 `NewsChatService` 显式传入，默认走 `DEFAULT_LOGICAL_MODEL` 对应的现有 OpenAI-compatible `/chat/completions` 入口，不新增供应商。
4. **低置信度回退**：模型 `confidence >=0.82` 才路由；低于阈值、schema 无效、超时或模型未配置都返回普通新闻研究。V1 不因低置信度频繁追问。

显式斜杠优先级最高，模型不得覆盖。V1 每次只返回一个 Skill，不自动串联 brief -> compare 或 changed -> factcheck。

`use_llm=False` 是硬门禁：仍执行明确斜杠命令和 `confidence>=0.90` 的确定性规则，但绝不调用模型分类。规则不能确定时返回 `skill_id=None, source=fallback`，由 `NewsChatService` 继续原有 `use_llm=False` 的非模型搜索/序号追问/上下文处理路径，而不是转入 `_research_chat()` 或任何模型 fallback。

### 4.3 中文触发表与防误触规则

| Skill | 高精度正例 | 必要约束/反例 |
| --- | --- | --- |
| brief | “给我一份今天的 AI 新闻简报”“做个科技早报”“汇总今日值得关注的新闻” | 需要“简报/早报/晚报/今日新闻汇总”等产物词；“简单说说”不触发 |
| factcheck | “核实这条消息是否属实”“这个说法是真是假”“帮我查证某数字” | 必须存在可核查 claim 或清楚指向上一条 claim；观点、预测、写作润色不触发 |
| map | “画出这次事件的关系图”“生成热点事件图谱”“整理成事件时间线图” | 需要图谱/关系图/可视化动作；只问“时间线是什么”可留普通研究 |
| report | “把这件事整理成专题报告”“做一份完整事件复盘报告” | 需要报告/专题/档案等产物词；“报道了什么”不是 report |
| changed | “这件事从昨天到现在有什么新进展”“OpenAI 这件事后来有变化吗”“只告诉我新增内容，不要重讲背景” | 需要已知/具名新闻主题 + 变化/后来/新增/自上次等时间比较语义；泛问“今天有什么新闻”优先 brief/普通研究 |
| compare | “不同媒体怎么报道这件事”“国内外媒体的说法有什么区别”“哪些事实共同确认、哪里冲突” | 必须有新闻主题/当前新闻上下文，并出现媒体/来源/报道/共同确认/冲突等锚点；“帮我比较这两句话”“比较两个产品参数”“这两个 Python 实现有什么区别”明确排除 |
| schedule | “每天早上九点给我推送 AI 新闻”“每周汇总一次这个主题”“定时生成专题报告” | 需要周期/时间 + 推送/汇总/提醒/定时意图；自然语言命中只产生确认，不创建任务 |

冲突优先级：明确“核实真假”优先 factcheck；明确“媒体/来源异同”优先 compare；明确“只看新增/后来变化”优先 changed；“今日多主题简报”优先 brief；其余回普通研究。规则使用短语组合和排除项，不用单个“比较”“变化”“报告”宽泛命中。

### 4.4 执行与确认

- `read_only` Skill 可在自然语言路由成功后自动执行。
- `schedule` 自然语言命中时返回 `ChatResponse(status="blocked", output_kind="schedule_confirmation", context_relation="skill_confirmation_required")`；此状态含义是“等待用户确认”，不能调用 `create_task()` 或 `create_from_schedule_message()`。
- 用户明确输入 `/schedule ...` 时，为保持旧链接和已有行为，V1 继续直接创建任务；响应可增加 `confirmation_bypassed_by=explicit_slash_command`。这是一项有意兼容，不适用于自然语言。
- 模型不可用、分类失败或低置信度时不报错、不反复询问，直接沿用普通研究。

`NewsChatService.chat()` 和 `chat_events()` 都应调用同一个 `_route_and_execute_skill()`，避免同步/流式再出现分支漂移。Router 放在输入审核之后、普通 topic-agent/ordinal/general-research 之前。

#### 4.4.1 预览与服务拆分

将现有 `ScheduledTaskService.create_from_schedule_message()` 拆成可复用但职责分离的三个步骤：

1. `prepare_schedule_preview(user_id, message)`：复用 `extract_schedule_task_api_params()`，只解析、规范化和校验，不调用 `create_task()`，不创建 Scheduled Push 对话或 turn。
2. `create_from_schedule_preview(preview, user_id, original_message)`：再次做 owner、cron、task type、delivery 和字段白名单校验，然后直接使用 preview 创建任务；不得再次调用 CC Runtime/LLM 解析。
3. 现有显式 `/schedule` 和 `/api/tasks/schedule` 兼容入口可组合上述两个步骤后立即创建；自然语言入口在两步之间进入确认门禁。

公开给前端的 `SchedulePreview` 使用固定结构，不包含内部 prompt、handler 或 Agent Skill 名称：

```json
{
  "task_type": "scheduled_push",
  "schedule": "0 9 * * *",
  "timezone": "Asia/Shanghai",
  "next_run_at": "2026-08-14T09:00:00+08:00",
  "topics": ["AI Agent"],
  "category_scope": ["tech"],
  "source_scope": [],
  "output_style": "通用专题早报",
  "delivery_channel": "in_app",
  "intent_summary": "每天 9 点汇总 AI Agent 新闻",
  "report_sections": ["摘要", "核心事件", "时间线", "影响", "后续观察", "来源"],
  "raw_task_description": "每天早上9点给我推送 AI Agent 新闻"
}
```

确认卡片的数据结构为：

```json
{
  "preview": {"...": "SchedulePreview"},
  "confirmation": {
    "confirmation_id": "scf_...",
    "token": "仅本次响应返回的一次性 bearer token",
    "status": "pending",
    "expires_at": "2026-08-13T15:04:13+08:00",
    "confirm_endpoint": "/api/tasks/schedule/confirm",
    "cancel_endpoint": "/api/tasks/schedule/cancel"
  }
}
```

`preview` 是确认后唯一允许执行的数据快照。token 记录同时保存 preview 的 SHA-256 digest；确认时重新计算并比对，避免前端篡改。确认请求不接收可修改的 preview，只接收 token 和绑定字段。

#### 4.4.2 应用级时区

V1 只支持一个应用级时区，不支持用户级或任务级独立时区。`personal_news_agent/config.py::Settings` 新增 `app_timezone`，环境变量为 `PNA_APP_TIMEZONE`，默认 `Asia/Shanghai`；启动时用 `zoneinfo.ZoneInfo` 校验，非法 IANA 名称直接启动失败。`build_services()` 将同一个 `ZoneInfo(settings.app_timezone)` 注入 `ScheduledTaskService`、`SkillRouter` 的相对时间解析、`ChangeDetectionService` 和 API 展示辅助函数。

当前 `tasks.py::LOCAL_TZ = datetime.now().astimezone().tzinfo` 依赖服务器本地时区，必须移除。统一规则：

- “今天、昨天、过去24小时、每周一上午8点”等相对表达只按应用时区解释；Router 仅保留原始表达，权威解析由对应 Service 完成。
- 五字段 cron 的小时/星期语义固定属于应用时区。任务记录继续只保存现有 `schedule_cron`，不新增 timezone 列；所有任务都隐式使用当前应用级时区。
- `next_run_at` 继续以带 offset 的 UTC ISO 8601 写入现有字段，计算时先把 base 转入应用时区匹配 cron，再转回 UTC。
- due runner 继续用 UTC 比较 `next_run_at`；每次执行后用同一个应用时区计算下一次运行，不能读取服务器本地时区。
- schedule preview 和确认卡片返回 `timezone=settings.app_timezone`，`next_run_at` 同时可返回 UTC 存储值和按应用时区格式化的展示值；前端直接使用服务端提供的 `timezone` 与 ISO 时间，不用浏览器本地时区重新解释 cron。
- changed 的 `baseline_at` 内部为带 offset 时间，默认/用户相对范围按应用时区计算；compare evidence 日期展示也经同一时区格式化。
- V1 不在单个 task payload 接受 `timezone` 输入；preview 的 timezone 是只读应用配置。若用户要求“纽约时间”，返回暂不支持任务级时区的明确提示，不能静默按服务器时区执行。

不做数据库迁移的代价是：修改 `PNA_APP_TIMEZONE` 会改变全部现有 cron 任务的墙上时间语义。因此它属于部署级配置，生产环境首次上线固定为 `Asia/Shanghai`；之后若要变更必须作为运维变更单独评估所有已有任务，不能在普通请求中动态切换。

#### 4.4.3 确认与取消接口

V1 采用显式 HTTP action，不把用户自由文本“确认”“好的”解释为授权，也不把它拼成 `/schedule`：

```text
POST /api/tasks/schedule/confirm
POST /api/tasks/schedule/cancel
```

`personal_news_agent/api/schemas.py` 新增：

```python
class ScheduleConfirmationActionRequest(BaseModel):
    user_id: str = "default"
    conversation_id: str = Field(min_length=1, max_length=128)
    confirmation_id: str = Field(min_length=8, max_length=96)
    confirmation_token: str = Field(min_length=32, max_length=256)
```

确认成功返回 `ScheduleConfirmationActionResponse`：`confirmation_status="confirmed"`、`task`、`conversation`、`message`、`replayed=False` 和可直接交给 `chatResponseHtml()` 的兼容 `chat_response`；取消返回 `confirmation_status="cancelled"`、`message`、`replayed=False`，不含 task。重复点击返回同一 task/cancel 结果并把 `replayed=True`，不再次执行副作用。

路由必须从当前认证/请求上下文取得有效 user；在现有代码仍以 body `user_id` 为身份边界的阶段，至少严格比较请求 `user_id`、token record 的 `user_id` 和 conversation owner，不能只凭 token 创建任务。绑定不匹配返回 403；未知 token 返回 404；过期返回 410；正在被另一请求消费返回 409。确认或取消结果要更新原 blocked turn 的现有 `response_json`，使历史卡片显示 confirmed/cancelled，但不得把原始 token写回持久化响应。

#### 4.4.4 token、TTL、绑定与无迁移存储

新增 `personal_news_agent/services/schedule_confirmation.py::ScheduleConfirmationService`，使用受 `asyncio.Lock` 保护、容量有上限的进程内 TTL registry：

- token 使用 `secrets.token_urlsafe(32)` 生成，至少 256 bit 随机性；`confirmation_id` 是非秘密展示 ID。
- registry 只保存 token 的 SHA-256 digest，不保存明文 token；record 绑定 `confirmation_id`、`user_id`、`conversation_id`、preview、preview digest、创建时间、绝对过期时间和 source turn ID。
- 有效期固定 15 分钟；校验同时使用 UTC 绝对时间与单调时钟，过期后不可恢复。
- 状态机固定为 `pending -> consuming -> confirmed` 或 `pending -> cancelled`，清理可转为 `expired`。状态转换与幂等结果读写在同一把锁内完成。
- raw token 只出现在首次 API/聊天响应中；`NewsChatService._save_response_turn()` 持久化前必须经过 confirmation sanitizer，只保存 `confirmation_id/status/expires_at/preview`，不保存 token。应用日志、trace 和错误信息也不得记录 token。
- 待确认权威状态在进程内 registry；原 blocked turn 的 `response_json` 仅保存可展示镜像。确认后任务进入现有 `scheduled_tasks`，卡片最终状态继续写现有 turn `response_json`。整个闭环不新增表或字段。
- 服务重启会清空全部 pending/consuming token；历史卡片应显示“确认已失效，请重新发起”，不得从持久化 preview 自动执行。confirmed task 已经在现有表中，不受影响。
- V1 明确以当前单 API 进程部署为一致性边界。多 worker/多实例不能共享进程内 token 与防重放 tombstone，若未来启用必须先引入现有基础设施中的共享原子存储或重新评审数据库方案，不能假装支持。

registry 保留 confirmed/cancelled tombstone 直到原过期时间再加 15 分钟（且受容量上限约束），用于处理重复点击。容量满时只清理 expired/已消费的最旧项；不能静默淘汰仍有效 pending 项，无法接纳时返回可重试错误而不是创建无门禁任务。

#### 4.4.5 防重放与原子创建

确认流程必须是：锁内验证 token digest、TTL、user、conversation、confirmation ID、preview digest 和状态；把 `pending` 原子改为 `consuming`；锁外用已存 preview 创建任务；锁内写入 `confirmed` tombstone 和同一 task result。并发/重复行为：

- 第一次确认只创建一个 task；
- `consuming` 时第二个请求返回 409，不并行创建；
- 已 `confirmed` 的同 token 再次确认返回第一次的 task，`replayed=True`；
- 已 `cancelled` 的 token 不能确认，返回 409；重复取消返回 cancelled，`replayed=True`；
- 已 `confirmed` 的 token 不能取消，返回 409；
- 过期/重启失效 token 在任何 action 下都不创建任务。

若创建任务在副作用前失败，record 可安全退回 `pending` 并返回可重试错误；若任务已经创建，则必须先以创建结果完成 confirmed tombstone，再做对话展示更新。现有 `NewsStore.create_task()` 尚无 confirmation idempotency key，因此实现时要保证“唯一调用点在 consuming owner 中”；如果无法证明故障点在任务落库前，必须暂停并重新评审，不能通过盲目重试冒险创建重复任务。

#### 4.4.6 前端确认卡片

`output_kind=schedule_confirmation` 对应统一 renderer，Web 和移动端显示主题、周期/时区、下次执行时间、范围、报告章节、推送位置，以及“确认创建”“取消”按钮：

- 点击任一按钮立即同时禁用两个按钮并显示进行中状态；确认调用 confirm endpoint，取消调用 cancel endpoint。
- 成功确认后原卡片切换为“已创建”，展示 task ID/下次执行时间，不再保留可点击确认按钮；重复响应保持同一 task。
- 取消后切换为“已取消”，不自动发送聊天文本，不创建任务。
- 403/404/409/410 使用明确文案；过期或服务重启失效时提供“重新发起”按钮，把原始自然语言重新填入输入框但不自动提交。
- 页面刷新后的历史响应不含 token，因此即使 `expires_at` 尚未到也把按钮置为失效并提示重新发起；不从 DOM、本地存储或历史 JSON 恢复 bearer token。
- 未识别 `schedule_confirmation` renderer 时仍显示 preview Markdown，但不得显示一个能够绕过确认的创建入口。

## 5. `/changed` 设计

### 5.1 职责与输出

新增：

- `personal_news_agent/skills/changed.py::ChangedSkill`：解析命令参数、调用 Service、包装统一结果；
- `personal_news_agent/services/change_detection.py::ChangeDetectionService`：基线解析、证据检索、去重、确定性差分、Agent 调用和结果校验；
- `personal_news_agent/skills/result_models.py::ChangeDigestData`：小型业务模型；
- `.claude/skills/news-change-digest/SKILL.md`：只基于提供的 baseline/current evidence 分类变化；
- 前端 `change_digest` renderer。

业务 payload：

```json
{
  "topic": "",
  "baseline_label": "",
  "baseline_at": "",
  "change_status": "changed|no_material_change|insufficient",
  "new_facts": [],
  "status_changes": [],
  "number_changes": [],
  "corrections": [],
  "repeated_reports": [],
  "watch_next": [],
  "evidence": []
}
```

`change_status` 只表达变化业务结论；统一信封的 `SkillResult.status` 只表达 `success|degraded|blocked|failed` 执行状态，两者不重名。前端卡片分别读取二者。

### 5.2 基线规则

按以下优先级只选一个 baseline：

1. 用户明确给出时间范围（昨天、过去一周、某日起）时，以该范围起点为 `baseline_at`，`baseline_label` 原样规范化为“用户指定：昨天 00:00（Asia/Shanghai）”等；当前对话旧结果可作为辅助证据，但不能覆盖用户范围。
2. 未指定范围时，从 `NewsStore.list_turns(conversation_id, user_id)` 逆序查找当前消息之前、与 topic/focus 匹配的最近研究结果。候选必须有已保存 `response.evidence`、`recommendations` 或 `skill_result.data.evidence/sources`；topic 的规范化实体/关键词需达到明确相关阈值。标签写“当前对话：<时间> 的研究结果”。
3. 当前对话没有相关 baseline 时默认过去 24 小时，`baseline_label="默认：过去24小时"`，显示绝对 `baseline_at`。绝不能写“自上次查看以来”。

时间统一按 `Settings.app_timezone` 解释相对表达，V1 默认且部署固定为 `Asia/Shanghai`，内部使用带 offset 的时间。V1 不跨对话猜“上次查看”，除非未来有明确用户选择；这样避免把其他主题的 recent turn 当 baseline。

### 5.3 检索、差分与实质变化

1. 从 baseline evidence、当前对话 evidence、专题/事件数据和 `UnifiedSearchService.search()` 构造两组只读证据；联网只在 `allow_web_search=True` 时启用。
2. 先按 canonical URL、article ID、`content_hash`、已确认 event fingerprint 去重；再用规范化标题、主体/动作/对象/时间和正文相似度识别转载、同稿分发和高度重复报道。现有 `content_hash`、事件 fingerprint 与 canonical event 数据可复用，但不能把“同一事件”误当“同一稿件”。
3. 建立原子事实 ledger（主体、动作、对象、状态、数值、单位、时间）；Agent 只能返回 evidence index 和分类，不得直接创造引用。
4. 分类定义：
   - `new_facts`：baseline 中没有、当前证据首次支持的实质事实；
   - `status_changes`：proposed/announced/accepted/approved/started/completed/cancelled 等程序状态改变，严禁合并法律/流程阶段；
   - `number_changes`：同一指标、单位和可比口径下的新旧数值；
   - `corrections`：后续权威信息纠正、撤回或反转旧说法；
   - `repeated_reports`：标题变化、新 URL、转载或旧事实再次传播，没有新的原子事实；
   - `watch_next`：可观察的下一信号，不得写成已发生事实。
5. 新标题、新文章、新来源、热度上升本身都不等于实质变化；如果所有 current items 都只落入 `repeated_reports`，返回 `data.change_status=no_material_change`。
6. 没有足够 baseline 或 current evidence 做可靠比较时返回 `data.change_status=insufficient`，明确缺什么。

### 5.4 Agent 与安全降级

Agent 可用时，`news-change-digest` 接收裁剪后的 baseline/current evidence ledger，输出严格 JSON、每项引用 index。Service 验证 index、去重和时间逻辑，再产生卡片数据。

Agent 不可用或结果无效时：

- 只用确定性时间/去重规则列出“基线后新增报道”；
- 不把新增报道自动宣判为新事实，不生成 status/number/correction 推断；
- 顶层 `status=degraded`，`fallback_reason=agent_unavailable|agent_output_invalid`；
- `data.change_status=insufficient`，新报道放入 `repeated_reports` 或带 `classification="unclassified_new_report"` 的安全列表；
- 明确说明“只能确认出现了新报道，尚不能确认事实发生实质变化”。

V1 只读现有 `conversation_turns.response_json`、文章、专题和事件数据，不新增 baseline 表或数据库迁移。

### 5.5 `change_digest` 卡片

卡片显示：topic、baseline 标签/绝对时间、业务状态 badge、四类变化、重复报道折叠区、watch next、证据索引。`no_material_change` 用清晰空状态，不渲染伪造条目；`degraded` 显示降级说明。每个条目携带 `evidence_indices`，点击跳到同轮 evidence。移动端改为单列，不依赖 hover。

## 6. `/compare` 设计

### 6.1 职责与输出

新增：

- `personal_news_agent/skills/compare.py::CompareSkill`；
- `personal_news_agent/services/coverage_comparison.py::CoverageComparisonService`；
- `personal_news_agent/skills/result_models.py::CoverageCompareData`；
- `.claude/skills/news-coverage-compare/SKILL.md`；
- 前端 `coverage_compare` renderer。

业务 payload 至少为：

```json
{
  "topic": "",
  "comparison_status": "sufficient|insufficient",
  "common_facts": [],
  "unique_claims": [],
  "conflicts": [],
  "framing_differences": [],
  "missing_questions": [],
  "source_groups": [],
  "evidence": []
}
```

`comparison_status` 用于表达来源是否足够；统一信封 `status` 仍只使用 `success|degraded|blocked|failed`。

### 6.2 来源聚类与独立性

按层级合并：

1. canonical URL/同 article ID；
2. 完全相同 `content_hash`；
3. 同一稿件的标题/正文高相似度、相同段落序列、相同署名/发布时间；
4. 同一通讯社/机构稿的跨域转载和同稿分发；
5. 同一事件但独立采写保留为独立 source group。

现有入库层能按 `content_hash` 去重完全相同正文，事件层能按 subject/action/object/time fingerprint 合并同一事件；Compare Service 仍需独立的 coverage grouping，因为“同一事件”不等于“同一稿件”。每个 `source_group` 包含 group id、代表来源、成员 evidence indices、`distribution_kind=original|syndicated|near_duplicate|independent` 和判定依据。

### 6.3 四类结论

- `common_facts`：至少两个独立 source group 对同一原子事实的主体、动作、对象、时间/数值等关键槽位一致；转载数不增加独立确认数。
- `unique_claims`：仅一个独立 group 报道，且其他来源未确认；“未提到”不等于反驳。
- `conflicts`：来源对同一可核查原子事实的相同槽位给出互斥值或状态（不同人数、日期、是否发生、程序阶段）。每项必须说明冲突字段、各方值和各自 evidence indices。
- `framing_differences`：选择了不同背景、因果解释、标题重点、评价或语气；措辞、立场和标题差异不得直接升级为事实冲突。

每个结论项只引用本轮 evidence ledger 中的 index。Service 拒绝未知 index、重复 URL 和无法映射的模型 URL。模型只做受约束分类；source group 和 evidence ledger 由应用生成。

### 6.4 来源不足和降级

- 少于两个独立 source group 时，`data.comparison_status=insufficient`；仍显示已有分组和 `missing_questions`，不制造“共同事实”或“冲突”。
- 有多个来源但只有同稿转载时同样 `insufficient`，并在 source_groups 标记 syndicated。
- Agent 不可用时，确定性 fallback 至少返回来源分组、重复/同稿关系、标题或元数据层面的基础差异；顶层 `status=degraded`，`fallback_reason=agent_unavailable`。不得把标题词差异判为事实冲突。
- 联网关闭时只比较本地/当前对话证据；若独立来源不足，明确降级，不偷偷外搜。

### 6.5 `coverage_compare` 卡片

卡片顶部显示独立来源组数和重复稿数量；主体按“共同确认 / 独有说法 / 事实冲突 / 叙事侧重”分区，冲突项采用并列证据而不是红绿真假结论。来源组可展开查看成员链接。每项结论支持 evidence index 跳转；移动端使用纵向分区。未知/降级结果仍回退到 Markdown。

## 7. 整理现有 Skill 的具体方案

1. **factcheck 联网政策**：Manifest 设 `network_policy=user_controlled`；`FactCheckSkill` 传 `context.allow_web_search`；`FactCheckService` 的 CC 和 fallback 外搜都只看该值。离线且本地证据充分时为 `success + research_mode=local_only`；离线且证据不足时为 `degraded + fallback_reason=web_search_disabled + research_mode=local_only`。两种情况都把现有“强制联网”测试改为禁止任何外部请求，并分别验证充分/不足分支。
2. **schedule 单一路径**：只保留 `ScheduleSkill -> ScheduledTaskService` 的聊天路径和 `/api/tasks/schedule -> ScheduledTaskService` API 路径；删除 `NewsChatService._schedule_command_response()`。自然语言 route 先确认，明确 `/schedule` 继续直接执行。
3. **related**：Manifest 设 `context_action`；从公共 slash 菜单移除，但 `/related`、后端 registry、现有卡片动作和链接继续有效。将 `NewsChatService.related_search()` 的业务编排在与本任务相关范围内逐步抽为 `RelatedNewsService`，不重写其全部回答逻辑。
4. **sources**：Manifest 设 `admin`；从公共菜单移到设置/来源管理。保留 `/sources` 和 handler 兼容。公开 `/api/skills` 默认不返回。
5. **schedule 高级入口**：仍是 public，但菜单可展示“高级/会创建任务”和 confirmation metadata；只有自然语言触发需要确认。
6. **旧命令与别名**：不删除 canonical 命令；把前端已有 `/r`、`/verify`、`/graph` 纳入后端 aliases，保证直接 API 和旧链接也可用。页面 context actions 继续兼容。
7. **内部 Agent Skill**：`news-conversation-research` 是普通研究的 internal Agent workflow，不对应网页 Skill；其他 Agent Skill 通过 manifest 的 `agent_skill` 映射到用户 Skill。Agent-only 与网页数量不要求一致。

## 8. 统一结果与证据协议

### 8.1 兼容信封

增量扩展 `SkillResult`：

```python
@dataclass(frozen=True)
class SkillResult:
    skill_id: str
    command: str
    title: str
    message: str
    status: Literal["success", "degraded", "blocked", "failed"] = "success"
    output_kind: str = "default_markdown"
    evidence: tuple[EvidenceRef, ...] = ()
    fallback_reason: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
```

`ChatResponse` 新增同名可选/有默认值字段 `status="success"`、`output_kind="default_markdown"`、`fallback_reason=None`，继续保留 `answer`、`markdown`、`evidence`、`skill_result` 等旧字段。普通非 Skill 对话也可逐步填写统一字段，但本次只要求 Skill 路径完整。旧客户端忽略新增 JSON 字段即可；新客户端优先读顶层，历史对话缺字段时使用默认值。

状态含义：

- `success`：请求按契约完成；业务结果可为 changed/no_material_change/insufficient 或 sufficient/insufficient；关闭联网但本地证据充分的 factcheck 也属于成功完成的 local-only 核查；
- `degraded`：有安全可用结果，但联网、Agent、模型、来源或证据能力下降；
- `blocked`：因确认、策略或权限未执行；不是异常；
- `failed`：handler 执行失败且没有安全结果。

`fallback_reason` 使用稳定代码（如 `web_search_disabled`、`agent_unavailable`、`insufficient_independent_sources`），用户说明仍放 `message/markdown`。

### 8.2 EvidenceRef

在 `personal_news_agent/core/models.py` 定义：

```python
class EvidenceRef(BaseModel):
    index: int
    title: str
    url: str
    source_id: str | None = None
    published_at: datetime | None = None
    origin: Literal["local", "elasticsearch", "native", "external", "builtin_web", "conversation", "topic"]
    claim_role: Literal["supporting", "contradicting", "context", "baseline", "current", "repeated"] = "context"
```

允许各 Skill 在 `data` 中保留 `article_id/summary/content_excerpt/group_id/evidence_indices` 等业务细节，但统一顶层 EvidenceRef 至少包含上述字段。详细业务结果用 `FactCheckData`、`ChangeDigestData`、`CoverageCompareData` 等小模型校验，不向 `ChatResponse` 塞所有业务字段。

### 8.3 本轮证据账本

新增共享 `EvidenceLedger`（建议 `personal_news_agent/services/evidence.py`）：

1. 仅在应用搜索工具/本地存储实际返回结果时登记 URL；
2. canonicalize、去重并分配稳定 1-based index；
3. 传给模型的是 index + 最少必要内容；模型只能返回 index；
4. 结果校验器将 index 映射回 EvidenceRef，拒绝未知 index；
5. 内建 WebSearch 若 SDK 无法提供结构化实际 URL 列表，则不得把 Agent 自报 URL 提升为 EvidenceRef；只能作为未验证文本线索，或在能够从 runtime 工具事件采集 URL 后登记；
6. 每轮 ledger 隔离，不能因为 URL 存在于旧回答或模型常识就当成本轮证据。Changed 的旧 baseline evidence 明确以 `origin=conversation/topic, claim_role=baseline` 复制进本轮 ledger，仍需可追踪来源。

这会替代当前多处 `_runtime_evidence()` 重复实现，并收紧 `_append_declared_external_evidence()` 的信任边界。

## 9. 推荐模块结构与职责

```text
personal_news_agent/
  skills/
    manifest.py              # SkillDefinition/枚举，不导入 handlers
    catalog.py               # 唯一声明目录，使用 handler 字符串
    base.py                  # 兼容 SkillSpec、SkillContext、统一 SkillResult
    registry.py              # 注册、alias、执行、manifest 校验
    result_models.py         # ChangeDigestData/CoverageCompareData 等小模型
    changed.py               # 薄 handler
    compare.py               # 薄 handler
  services/
    skill_router.py          # 斜杠/规则/模型单 Skill 路由
    evidence.py              # 本轮证据 ledger 与引用映射
    schedule_confirmation.py # 15 分钟进程内一次性确认状态机
    time_context.py          # 应用级 ZoneInfo 与统一解析/展示辅助
    change_detection.py      # baseline + 差分业务
    coverage_comparison.py   # 来源聚类 + 报道比较业务
    chat.py                  # 调用 router/registry、保存响应；不承载新业务算法
    cc_runtime.py            # 从 catalog 获取白名单并执行 Agent
.claude/skills/
  news-change-digest/
  news-coverage-compare/
```

现有超大文件只做与本任务直接相关的抽取：

- `chat.py`（当前约 3362 行）：抽出 SkillRouter；删除 schedule 重复路径；changed/compare 不写入该文件。Related 编排只在必要范围抽 Service。
- `shared.js`（约 2326 行）：抽一个小型 output renderer map；不重做 Markdown、聊天或全部 UI。
- `web.js`/`mobile.js`：只把完整 Skill 清单改为 API 数据、保留 context actions；不重构整个命令控制台。
- `cc_runtime.py`（约 701 行）：只替换白名单来源和增加证据事件采集所需的小接口，不改运行时总体架构。
- `reports.py`（约 1231 行）：changed/compare 不塞入 report service；只复用它，不做无关拆分。

## 10. 数据流与错误处理

```text
ChatRequest
 -> moderation
 -> SkillRouter.route(message, context, manifest, model_key, use_llm)
    -> slash: exact manifest
    -> rules: high confidence
    -> model: only when use_llm=True, with request model_key
    -> fallback: original model/non-model path selected by use_llm
 -> confirmation gate (natural-language side effects only; explicit legacy slash follows compatibility path)
    -> natural-language schedule preview only -> in-memory one-time token
    -> explicit confirm/cancel API -> atomic token consumption
 -> slash route: SkillRegistry.execute_text(original_text, context)
 -> rule/model route: SkillRegistry.execute_structured(skill_id, validated_arguments, context)
 -> handler -> independent service
 -> EvidenceLedger records actual results
 -> optional CC Agent receives bounded evidence/context
 -> small result model validates business data and evidence indices
 -> SkillResult envelope
 -> ChatResponse compatible envelope + save_turn(response_json)
 -> frontend output_kind renderer or default_markdown
```

错误策略：

- 输入/参数错误：保持当前友好错误，统一为 `failed`；不保存副作用。
- Router 模型失败：`use_llm=True` 时回普通研究；`use_llm=False` 根本不进入模型分类并回原有非模型处理路径；两者都不显示分类系统错误。
- 外部搜索关闭：严格不调用外部 provider/CC web tool/内建 WebSearch；factcheck 本地证据充分时 `success + local_only`，证据不足时 `degraded + web_search_disabled + local_only`；其他 Skill 按各自证据契约决定 success/degraded。
- 外部搜索失败：保留已取得本地 evidence，degraded + 稳定 reason。
- schedule 确认 token 过期、取消、绑定不匹配、并发消费或服务重启失效：不创建任务，分别返回 410/409/403/409/410；重复确认只返回首次 task。
- Agent 失败/invalid JSON：changed/compare 使用安全 deterministic fallback；不能可靠分类就 insufficient。
- handler 未注册/manifest 不一致：启动测试/校验失败；运行时未知旧命令仍返回明确错误。
- 前端未知 output_kind：`default_markdown`；卡片渲染异常也应保留 Markdown 文本。
- 引用越界/未知 URL：丢弃该结论引用；若关键结论失去全部证据，结果降级或 insufficient，不允许静默保留无证据结论。

## 11. 预计文件变化

### 新增

- `personal_news_agent/skills/manifest.py`
- `personal_news_agent/skills/catalog.py`
- `personal_news_agent/skills/result_models.py`
- `personal_news_agent/skills/changed.py`
- `personal_news_agent/skills/compare.py`
- `personal_news_agent/services/skill_router.py`
- `personal_news_agent/services/evidence.py`
- `personal_news_agent/services/schedule_confirmation.py`
- `personal_news_agent/services/time_context.py`
- `personal_news_agent/services/change_detection.py`
- `personal_news_agent/services/coverage_comparison.py`
- `.claude/skills/news-change-digest/SKILL.md`
- `.claude/skills/news-change-digest/agents/openai.yaml`
- `.claude/skills/news-coverage-compare/SKILL.md`
- `.claude/skills/news-coverage-compare/agents/openai.yaml`
- `tests/test_skill_manifest.py`
- `tests/test_skill_router.py`
- `tests/test_schedule_confirmation.py`
- `tests/test_change_detection.py`
- `tests/test_coverage_comparison.py`

### 修改

- `personal_news_agent/skills/base.py`
- `personal_news_agent/skills/registry.py`
- `personal_news_agent/skills/__init__.py`
- `personal_news_agent/skills/factcheck.py`
- `personal_news_agent/skills/brief.py`
- `personal_news_agent/skills/report.py`
- `personal_news_agent/skills/hot_event_map.py`
- `personal_news_agent/skills/related.py`
- `personal_news_agent/skills/sources.py`
- `personal_news_agent/skills/schedule.py`
- `personal_news_agent/services/factory.py`
- `personal_news_agent/services/chat.py`
- `personal_news_agent/services/cc_runtime.py`
- `personal_news_agent/services/factcheck.py`
- `personal_news_agent/services/tasks.py`
- `personal_news_agent/config.py`
- `.env.example`
- `personal_news_agent/core/models.py`
- `personal_news_agent/api/schemas.py`
- `personal_news_agent/api/routes.py`
- `personal_news_agent/static/shared.js`
- `personal_news_agent/static/web.js`
- `personal_news_agent/static/mobile.js`
- `personal_news_agent/static/home.html`
- `personal_news_agent/static/mobile.html`
- `personal_news_agent/skills/README.md`
- `README.md`
- `tests/test_services.py`
- `tests/test_api.py`
- `tests/test_subpath_frontend.py`

若实施中发现无需触碰某个薄 handler，则从实际修改清单移除，不能为“统一格式”做无价值改写。

### 删除

不删除文件、不删除旧命令。只删除现有文件内部的重复/硬编码：`NewsChatService._schedule_command_response()` 及调用点、`cc_runtime.py` 手写白名单内容、`shared.js` 完整静态 Skill 菜单、前端对 `/r`/`verify`/`graph` 的重复 canonical 映射（在后端 aliases 上线并验证后收敛）。`/api/skills` 失败时隐藏菜单、保留手动输入，不保留完整静态业务清单。

## 12. 分阶段实施与完成标准

### 阶段 1：Manifest、Registry 与一致性校验

内容：建立类型化 catalog；迁移 7 个旧 Skill、8 个 Agent Skill 映射和别名；Registry 从 catalog 构建；CC 白名单派生；保持旧注册接口。

完成标准：

- catalog 校验测试通过，无孤立 handler、重复命令/别名、失效 Agent Skill；
- 7 个旧 canonical 命令行为不变，`/r`、`/verify`、`/graph` 后端可兼容；
- Agent-only `news-conversation-research` 可解释且不出现在网页菜单；
- 现有 registry/CC Runtime 聚焦测试通过。

### 阶段 2：公开 API、前端菜单与统一结果信封

内容：增加 `/api/skills`；前端动态菜单与 output renderer map；引入 EvidenceRef/EvidenceLedger；保持 ChatResponse 默认兼容。

完成标准：

- `/api/skills` 只返回 public enabled 项；related/sources/internal 不泄露；
- Web/移动不再硬编码完整 Skill 清单；API 失败时隐藏菜单并保留手动输入，不出现第二份全量 fallback；
- 未知 output_kind 能显示 default Markdown；
- 历史 ChatResponse 和旧斜杠命令仍可渲染；
- 引用只能映射到本轮 ledger。

### 阶段 3：SkillRouter、schedule 确认与 factcheck 收敛

内容：独立 Router（显式接收请求 `model_key`、`use_llm`）；结构化参数模型与 legacy adapter；同步/流式统一执行入口；自然语言只读路由；schedule preview、15 分钟进程内一次性 token、确认/取消 API 与前端确认卡片；应用级 `PNA_APP_TIMEZONE=Asia/Shanghai` 统一相对时间、cron、`next_run_at`、due runner 和展示；删除聊天内 schedule 重复路径；factcheck 遵守联网开关。

完成标准：

- 主要只读 Skill 正例路由正确，模糊/非新闻比较不误触发；
- `SkillRouter.route(..., model_key=..., use_llm=...)` 显式接收本轮请求参数，把 model key 原样传给 `LLMClient.structured()`，`SkillRoute.arguments` 使用独立 default factory；
- `use_llm=False` 时模型分类调用数为 0；明确斜杠和高精度规则仍可执行，无法确定时回原有非模型路径；
- `execute_text()` 保持旧斜杠行为，`execute_structured()` 使用 Manifest 参数模型；两者经同一 handler/Service，sync/SSE 共用 `_route_and_execute_skill()`；
- 模型不可用/低置信度回普通研究且不频繁追问；
- 自然语言 schedule 在确认前数据库任务数不变；preview 不可由客户端改写；确认/取消接口校验 token、TTL、用户和会话绑定；首次确认只创建一次，重复点击幂等，过期/取消/重启失效不创建；明确 `/schedule` 保持创建行为；
- Web/移动 schedule_confirmation 卡片能确认、取消、禁用重复点击并处理 403/404/409/410；历史卡片不恢复 token；
- `allow_web_search=False` 时应用外部 provider、CC 应用 web tool、内建 WebSearch 均无调用；
- factcheck 离线证据充分为 `success + local_only`，证据不足为 `degraded + web_search_disabled + local_only`；
- 当服务器操作系统时区不是北京时间时，默认应用时区仍为 `Asia/Shanghai`，相对时间、cron、`next_run_at`、due runner、preview 和前端展示保持一致；V1 不接受任务级 timezone；
- chat 与 chat_events 对同一输入路由一致。

### 阶段 4：`/changed`

内容：ChangeDetectionService、handler、Agent Skill、小模型、卡片和降级。

完成标准：

- 当前对话相关 baseline 优先；用户时间范围覆盖；无 baseline 明示过去 24 小时；
- 标题变化/新 URL/转载不构成实质变化；无变化返回 `data.change_status=no_material_change`；
- Agent 不可用只报告新增报道，顶层 degraded；
- 不新增数据库迁移，所有引用通过 ledger；
- Web/移动 change_digest 可读并可回退 Markdown。

### 阶段 5：`/compare`

内容：CoverageComparisonService、handler、Agent Skill、小模型、卡片和降级。

完成标准：

- 能合并完全重复、同稿分发和高度重复报道，同时保留独立采写；
- 共同事实、独有说法、事实冲突、叙事侧重边界正确；
- 措辞/标题/立场差异不被判成事实冲突；
- 来源不足返回 insufficient；Agent 不可用仍显示来源组并标 degraded；
- 每项结论都有本轮 evidence indices。

### 阶段 6：整理入口、文档与回归

内容：related/context action、sources/admin 入口调整；README 同步；完整回归与移动端/Web 验证；在本文追加实际实现状态。

完成标准：

- public/internal/admin/context_action 的接口和页面行为符合 Manifest；
- 旧 `/related`、`/sources` 和所有旧 canonical 命令继续可用；
- README、API 和 catalog 清单一致；
- 相关单元/API/前端契约测试与现有全量回归通过；
- 文档记录实际命令、测试结果、降级边界和未完成项。

每阶段都需先按确认后的实施计划写失败测试，再实现并跑该阶段测试；但这些动作只能在用户明确确认本文后开始。

## 13. 测试与验收矩阵

| # | 测试目标 | 建议层级与断言 |
| --- | --- | --- |
| 1 | Manifest 无孤立 handler、重复命令、失效 Agent Skill | unit：导入 handler，扫描指定 agent dirs/frontmatter，重复即失败 |
| 2 | `/api/skills` 只公开允许项 | API：只有 enabled public；无 related/sources/internal 和内部字段 |
| 3 | 前端不硬编码完整 Skill 清单 | static contract：从 `/api/skills` 加载；接口失败隐藏菜单、保留手动输入；代码中不存在静态业务 Skill fallback 清单 |
| 4 | 自然语言路由主要只读 Skill | parameterized unit：brief/factcheck/map/report/changed/compare 正例和 arguments |
| 5 | 模糊表达不误触发 | “比较两句话”“比较产品参数”“写一份工作报告”“最近怎么样” -> fallback |
| 6 | 自然语言 schedule 未确认不创建 | service/API：route blocked；preview 由 `ScheduleArguments` 和 Service 生成；前后 task count 相等；确认后仅创建一次 |
| 7 | schedule confirmation 闭环 | API/UI：preview 字段完整；token 15 分钟 TTL、user/conversation 绑定、digest 校验；confirm/cancel 明确 action；重复点击幂等；并发消费/过期/重启失效不创建；按钮禁用、历史不恢复 token |
| 8 | factcheck 关闭联网时不外搜且状态一致 | unit/service：外部 provider、CC 应用 web tool、内建 WebSearch 调用数均为 0；本地证据充分为 `success + local_only`，不足为 `degraded + web_search_disabled + local_only` |
| 9 | changed 无 baseline 使用过去24小时 | fixed clock：label、baseline_at、时区与文案精确断言 |
| 10 | changed 不把新标题当实质变化 | 同 content_hash/同稿不同标题 -> repeated_reports；`change_status=no_material_change` |
| 11 | compare 识别转载/同稿 | identical hash + near duplicate fixtures -> 一个 syndicated group，不计独立确认 |
| 12 | compare 不把措辞差异当冲突 | 同原子事实、不同标题/语气 -> framing 或无差异，conflicts 为空；`comparison_status` 不误报冲突 |
| 13 | 两个新 Skill 的 Agent 降级 | fake unavailable/invalid JSON -> top degraded；changed/compare 安全最小输出 |
| 14 | 引用只来自本轮证据 | 模型返回未知 index/虚构 URL -> 丢弃；关键结论 insufficient/degraded |
| 15 | Web、移动和旧命令兼容 | API + static/browser：7 个旧 canonical 命令、aliases、Markdown fallback、移动单列；`/api/skills` 失败隐藏菜单但手动输入仍可用 |
| 16 | 现有回归继续通过 | 先 focused，再 `pytest` 全量；记录命令、通过数和环境限制 |
| 17 | 参数与入口协议一致 | unit：`execute_text()` 保持旧 argv；`execute_structured()` 校验 `arguments_model`；changed/compare/schedule 进入同一 handler/Service；sync/SSE 共用 `_route_and_execute_skill()` |
| 18 | `use_llm` 与模型选择 | `use_llm=False` 时模型分类调用计数为 0；斜杠/高精度规则仍生效；`use_llm=True` 时 `model_key` 原样传给 `LLMClient.structured()` |
| 19 | 应用级时区 | 服务器 TZ 设置为非北京时间时，仍按默认 `Asia/Shanghai` 解析相对时间与 cron、计算/执行 `next_run_at` 并展示；任务级 timezone 被拒绝；非法配置启动失败 |

补充测试：

- sync `/api/chat` 与 SSE `/api/chat/stream` route/result 一致；
- schedule 明确斜杠仍只创建一次；自然语言 preview 不创建任务；confirm/cancel 接口 schema、鉴权绑定、TTL、digest、防重放、并发状态和重启失效均覆盖；重复确认 token 幂等或被拒绝；
- `model_key`/`use_llm` 从 `ChatRequest` 进入 `SkillRouter.route()`；关闭 LLM 时分类模型调用为 0；`SkillRoute.arguments` 每个实例独立；
- 旧斜杠 text parser 与自然语言结构化参数分别经 `execute_text()`/`execute_structured()`，最终调用同一 handler/Service；
- `exposure=admin/context_action/internal` 不进入普通自然语言候选；
- disabled Skill 不注册、不公开、不在 CC 白名单；
- required service 缺失时启动校验给出明确 Skill ID；
- unknown output_kind 与卡片 renderer 异常都保留 default Markdown；
- baseline 主题不相关时不得复用；用户指定范围优先于旧 turn；
- compare 少于两个独立来源与全部转载时均 insufficient；
- changed/compare payload 只出现 `change_status`/`comparison_status`，顶层 `status` 只出现执行状态；
- 在 `TZ=UTC` 等非北京时间进程环境下固定时钟，断言应用默认时区仍是 `Asia/Shanghai`，cron 09:00 对应正确 UTC、due runner 到点执行、changed baseline 与前端展示一致；
- EvidenceRef `index/title/url/source_id/published_at/origin/claim_role` 序列化兼容。

现状基线已运行：

```text
.venv/bin/pytest -q tests/test_services.py -k 'skill or factcheck or schedule' tests/test_subpath_frontend.py -k 'slash_command or skill_command'
5 passed, 170 deselected in 0.39s
```

该结果只是当前聚焦测试基线，不代表新设计已实现，也不代表完整回归通过。

## 14. 兼容、降级与回滚

### 兼容

- 不删除旧命令；旧 `/schedule` 仍直接执行；旧 `/related`、`/sources` 仅改变发现入口，不改变可调用性。
- ChatResponse 只加默认字段；保留 `skill_result.command/title/message/data`、Markdown、evidence 和 report download 数据。
- legacy `SkillRegistry.register()`/`SkillSpec` 至少保留一个版本周期。
- 前端先部署后端 `/api/skills`，再切动态菜单；接口失败时隐藏菜单，输入框、手动斜杠命令和已有上下文按钮继续可操作。
- history 中缺失 `status/output_kind` 的响应按 success/default_markdown 解释。

### 降级

- Router 模型失败 -> 普通研究；
- Agent 失败 -> Skill-specific deterministic fallback；
- 联网关闭 -> 严格只用本地/当前对话 evidence；factcheck 证据充分为 `success + local_only`，证据不足才是 `degraded + web_search_disabled`；其他 Skill 按自身证据契约决定状态。
- 联网请求失败 -> 保留已取得证据并按影响程度 degraded，不伪装成完整联网结果。
- 来源不足 -> 业务 insufficient，不用模型补齐；
- 未知 output -> Markdown；
- 证据引用无效 -> 丢弃引用并降低结果状态。

### 回滚

各阶段以可逆开关/小步交付：

- Router 可通过配置退回 slash-only；
- 新 changed/compare 可 `enabled=False` 从注册、API、菜单和 CC 白名单同时撤下；
- 前端可回滚为隐藏动态菜单并保留手动输入，不能恢复静态业务 Skill 清单；
- 统一字段有默认值，回滚新 renderer 后仍显示 Markdown；
- 不做数据库迁移，因此无需 schema rollback；
- 不删除旧 handlers/命令，Manifest 回滚不会破坏已有链接。

## 15. 风险与需要重点审核的决定

1. **执行与业务状态的边界**：顶层 `status` 只表示执行状态；changed 业务结论只使用 `change_status`，compare 业务结论只使用 `comparison_status`。前端、API schema 和测试必须保持这三者的语义边界。
2. **schedule 显式斜杠兼容例外**：自然语言必须确认，旧 `/schedule` 继续直接创建。需要重点审核这是否符合产品安全预期；若要让显式命令也确认，会构成行为变化，必须再次确认。
3. **内建 WebSearch 引用可观测性**：当前代码能计数调用，却不一定拥有结构化的实际 URL ledger；在补齐工具事件 URL 采集前，不能把 Agent 自报链接当引用。这可能让部分 CC 输出在过渡期降级。
4. **转载/同稿识别误差**：完全 hash 可确定，高相似度只能保守聚类。阈值过低会合并独立报道，过高会把转载算成多源；需用中文转载 fixture 和可解释 group reason 校准。
5. **baseline 相关性**：同一会话可能切换主题。V1 只取明确相关且可追踪 evidence 的历史 turn；宁可默认 24 小时，也不错误声称“自上次查看”。
6. **当前工作区已有未提交改动**：`chat.py`、routes、factory、前端和测试等目标文件已有用户修改。实施时必须基于当时工作区逐文件合并，不能回退或覆盖现有事件聚合/UI 改动。
7. **OpenAI 开发文档 MCP 不可用**：本阶段会话未暴露任何 MCP resource/template 或 OpenAI docs tool，因此未能调用仓库 AGENTS 要求的官方开发文档 MCP。本文关于模型入口只陈述本地 `LLMClient.structured()` 和 CC Runtime 源码事实；后续若 MCP 恢复，应补做结构化输出/Agent Skill 的官方约束核对，但不得借此无确认地改变既定范围。

## 16. 明确不做

- 不新增或修改数据库表、索引、迁移；
- 不接入新模型供应商，不更换现有模型选择机制；
- 不做多 Skill 自动编排；
- 不建设完整可视化 Skill 管理后台；
- 不重构全部 `chat.py`、`reports.py`、前端工作台或事件系统；
- 不移除旧斜杠命令、旧 API 或历史 ChatResponse 字段；
- 不把浏览器所有 context action 强行改造成 Python Skill；
- 不在第一阶段写实现、前端、Agent Skill、测试或迁移；
- 不提交、推送或创建 PR。

## 17. 第一阶段自查结果

- 占位检查：所有设计字段、文件、阶段和测试均已给出具体决策，无未决占位内容。
- 一致性检查：Manifest 是唯一声明源；Registry、CC 白名单、API 和前端均为投影；public/internal/admin/context_action 与旧命令兼容不冲突。
- 真实结构检查：设计基于 `build_services()`、`NewsChatService`、`SkillRegistry`、`CCRuntimeOrchestrator`、当前静态前端和实际 `.claude/skills`，没有假定不存在的框架。
- 职责检查：Router 只分类；Registry 只解析/执行；独立 Service 负责业务；Agent 只做受约束语义分类；EvidenceLedger 负责引用真实性；前端按 output_kind 渲染。
- 异常与降级检查：覆盖模型、Agent、联网、来源、引用、未知 renderer 和 schedule 确认失败路径。
- 范围检查：无数据库迁移、供应商、多 Skill 编排、管理后台或无关大重构；对超大文件只做本任务直接相关抽取。
- 实施门禁：本文完成后立即停止；只有收到“确认开发文档，可以开始开发”才进入 writing-plans、TDD 和分阶段实施。

## 18. 第二阶段实际实现状态（2026-08-14）

### 18.1 阶段结果

1. **Manifest、Registry 与 Runtime 白名单：已完成。** `SkillDefinition` 和 `SKILL_CATALOG` 成为唯一声明源；Registry 提供 `execute_text()`/`execute_structured()`，旧 `execute()` 继续兼容；命令、别名、handler、参数模型、required services 和 Agent Skill 目录可校验；应用完成 service 装配后执行 catalog/service binding 启动校验；`network_policy=local_only` 会在 Registry 边界强制关闭 handler 的联网上下文；CC Runtime 白名单由 enabled catalog 的 `agent_skill` 派生。
2. **统一结果、证据和公开 API：已完成。** `SkillResult`/`ChatResponse` 增量加入 `status/output_kind/evidence/fallback_reason`；`EvidenceLedger` 对本轮 canonical URL、索引、HTTP(S) URL 和模型引用做校验；旧 handler 已取得的 `data.evidence/data.sources` 会在 Registry 边界投影为统一 EvidenceRef，无 URL 或非法 URL 不会进入顶层证据；`ChangeDigestData`/`CoverageCompareData` 分别只使用 `change_status`/`comparison_status`；`GET /api/skills` 只投影 enabled public 字段；前端菜单请求失败时隐藏菜单并保留手动输入，未知输出回退 `default_markdown`。
3. **Router、schedule、时区和 factcheck：已完成。** `SkillRouter` 按 slash -> 高精度规则 -> 可选模型 -> fallback 路由，显式接收 `model_key/use_llm`，V1 单轮只选一个 Skill；sync/SSE 共用 `_route_and_execute_skill()`，SSE 以后台任务和队列在 handler 完成前实时发送 trace。应用时区由 `PNA_APP_TIMEZONE` 控制，默认 `Asia/Shanghai`，V1 拒绝任务级 timezone。自然语言 schedule 返回冻结 preview 和 15 分钟进程内一次性确认票据，确认/取消 API 校验 token digest、用户、会话、confirmation ID、过期、并发和重放；历史 turn 会移除 token；任务落库是确认提交点，之后的 trace、历史写入、降级日志或 warning trace 失败均不会让 token 回到可重试状态；前端对 403/404/409/410 保持终态，对网络和 5xx 失败恢复按钮以允许重试。显式 `/schedule` 仍立即创建，原 `NewsChatService._schedule_command_response()` 重复路径已删除。factcheck 关闭联网时不会请求外部搜索；本地证据充分为 `success + local_only`，不足为 `degraded + web_search_disabled + local_only`。
4. **`/changed`：已完成。** 新增薄 handler、`ChangeDetectionService`、`news-change-digest` Agent Skill 和 `change_digest` renderer；用户时间范围优先，对话相关 evidence 次之，无历史时明确显示“默认：过去24小时”；支持“自 8 月 1 日”等无年份表达并按应用时区解释，检索窗口随 baseline 动态扩展（最多 365 天），无法解析的显式基线会报参数错误而不静默回退；同稿、新标题和 canonical URL 变体不作为实质变化；同一 canonical URL 的正文发生变化时保留 baseline/current 两个观察快照；Agent 不可用时只列安全的未分类新增报道并降级；每项结论可引用合法 baseline 与 current 索引，但至少包含一个本轮 current 索引。
5. **`/compare`：已完成。** 新增薄 handler、`CoverageComparisonService`、`news-coverage-compare` Agent Skill 和 `coverage_compare` renderer；canonical URL、content hash 和高相似正文用于解释性分组；发布方身份优先使用规范化 source_id，否则使用 domain，同一发布方多篇不同稿件不会虚增独立来源，同稿跨站分发也只计一个来源组；共同事实和冲突必须跨独立组，标题、语气和立场差异不会单独构成事实冲突；来源不足返回 `comparison_status=insufficient`；Agent 不可用时保留来源组和基础叙事差异并标记顶层 degraded。2026-08-17 补充修复事件卡单分类造成的来源漏召回：首次结果不足两个独立来源组时才跨分类重试，重试继续遵守显式 `source_scope`，合并结果按 canonical URL 去重；主题过滤优先使用稳定命名实体（例如 `GLM-5.3`、`OpenAI`），不再把 `sooooooon` 等标题装饰词作为必需实体，也不会优先使用 `AI` 等泛词放大误召回。
6. **暴露与兼容整理：已完成。** 当前 9 个 canonical 网页可执行命令均保留，其中 7 个 public（brief、factcheck、map、report、changed、compare、schedule）、related 为 context_action、sources 为 admin；10 个 `.claude/skills` 可由 Manifest 解释，其中 `news-conversation-research` 是 internal Agent-only 工作流。README、Skill README、API 和菜单投影已同步。

### 18.2 实际文件

新增实现文件：

- `personal_news_agent/skills/manifest.py`、`catalog.py`、`arguments.py`、`result_models.py`、`changed.py`、`compare.py`；
- `personal_news_agent/services/evidence.py`、`skill_router.py`、`time_context.py`、`schedule_confirmation.py`、`change_detection.py`、`coverage_comparison.py`；
- `.claude/skills/news-change-digest/SKILL.md`、`.claude/skills/news-change-digest/agents/openai.yaml`；
- `.claude/skills/news-coverage-compare/SKILL.md`、`.claude/skills/news-coverage-compare/agents/openai.yaml`；
- `tests/test_skill_manifest.py`、`test_result_contracts.py`、`test_skill_router.py`、`test_timezone_policy.py`、`test_schedule_confirmation.py`、`test_change_detection.py`、`test_coverage_comparison.py`、`test_skill_frontend_contract.py`；
- `docs/superpowers/plans/2026-08-14-skill-system-upgrade.md`。

主要修改文件：

- `personal_news_agent/skills/base.py`、`registry.py`、`factcheck.py`、`skills/README.md`；
- `personal_news_agent/services/chat.py`、`factory.py`、`cc_runtime.py`、`tasks.py`；
- `personal_news_agent/core/models.py`、`config.py`、`api/schemas.py`、`api/routes.py`、`.env.example`；
- `personal_news_agent/static/shared.js`；
- `README.md`、`tests/test_api.py`、`tests/test_registry.py`、`tests/test_services.py`、`tests/test_subpath_frontend.py`。

未删除文件；未新增或修改数据库迁移。

### 18.3 实际验证

实施前全量基线：

```text
.venv/bin/pytest -q
300 passed, 31 warnings in 28.08s
```

完成后的重点套件：

```text
.venv/bin/pytest -q tests/test_skill_manifest.py tests/test_registry.py tests/test_result_contracts.py tests/test_skill_router.py tests/test_schedule_confirmation.py tests/test_timezone_policy.py tests/test_change_detection.py tests/test_coverage_comparison.py tests/test_services.py tests/test_api.py tests/test_subpath_frontend.py tests/test_skill_frontend_contract.py
285 passed, 47 warnings in 20.32s
```

完成后的全量回归与前端语法检查：

```text
node --check personal_news_agent/static/shared.js
.venv/bin/pytest -q
393 passed, 47 warnings in 27.88s
```

时区专项在非北京时间进程环境运行：

```text
TZ=UTC .venv/bin/pytest -q tests/test_timezone_policy.py tests/test_services.py -k 'schedule or task or timezone'
11 passed, 124 deselected, 1 warning in 0.89s
```

警告均为已有 FastAPI lifespan/httpx 兼容提示和 `reports.py` 的 `datetime.utcnow()` 弃用提示；本任务未扩大范围处理。

### 18.4 兼容、降级与未完成项

- 旧 canonical 命令和 `/r`、`/verify`、`/graph` 别名继续可用；显式 `/schedule` 的立即创建行为保持不变。
- schedule confirmation 仅适用于当前单 API 进程；服务重启 token 自动失效，多 worker 共享确认状态不在 V1 范围内。
- changed/compare 的 Agent 不可用或 JSON 无效时不会用模型常识补事实；只返回确定性安全结果与明确 fallback reason。
- 前端已通过 Web/移动共享脚本的静态契约、JavaScript 语法检查和完整回归；changed/compare 结论索引使用 `turn_id`（无 turn 时使用会话内 render sequence）限定到当前卡片证据区，schedule 网络/5xx 失败可重试。2026-08-17 已临时启动本地服务尝试真实浏览器视觉/点击验收，但浏览器安全策略阻止访问 `127.0.0.1`，临时服务随后正常停止；因此浏览器验收仍为环境受限的未完成项。确认、取消、重复点击和落库后故障的服务端行为已有 API/Service 测试覆盖。
- 官方 OpenAI 开发文档 MCP 在本会话仍未暴露 resource/template，无法执行 AGENTS 指定的在线文档核对；实现只复用仓库现有 `LLMClient.structured()` 和 CC Runtime 接口，没有引入或猜测新的 OpenAI API。
- 没有数据库迁移、新模型供应商、多 Skill 编排、Skill 管理后台或与本任务无关的大规模重构。

### 18.5 `/compare` 召回缺陷复盘（2026-08-17）

- 真实案例 `GLM-5.3突然发布！唐杰的“sooooooon”这次兑现了` 在 `category_scope=["tech"]` 下只召回腾讯新闻；同事件的新浪财经稿被归入 `economy`，因此旧实现只有一个独立来源组并提前返回 `insufficient`。
- 专题页原先显示的四条证据中有三条仅共享 `AI` 等宽泛词，不能直接作为 compare 输入；修复没有用页面数量冒充独立来源，而是在来源组不足时执行一次有边界的跨分类补召回，再走时效、主题实体、canonical URL 和同稿分组校验。
- 回归测试覆盖：单分类只有一个来源组时跨分类重试；显式 `source_scope` 在重试中保持不变；异标题报道不因装饰性英文词被排除；具体命名实体优先于 `AI` 泛词；无关背景稿不进入本轮 evidence。
- 本次缺陷修复只修改 `personal_news_agent/services/coverage_comparison.py`、`tests/test_coverage_comparison.py` 和本文档，没有修改同步维护中的前端文件。
