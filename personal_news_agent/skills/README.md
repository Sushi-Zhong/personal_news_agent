# Personal News Agent Skills

Manifest 当前注册九个 canonical 斜杠命令：

```text
/report <主题> [--category tech,economy] [--time-range 30d]
/brief [主题] [--category tech,sports]
/factcheck <待核查说法> [--category tech,economy]
/map <热点事件> [--category tech,economy]
/related <人物、队伍、机构、产品或事件> [--category game,sports]
/sources [category]
/schedule <周期、时间、主题和报告要求>
/changed <主题> [--baseline 昨天|过去一周] [--category tech,economy]
/compare <主题> [--category tech,economy] [--source source-a,source-b]
```

其中 `brief`、`factcheck`、`map`、`report`、`schedule`、`changed`、`compare` 为 public；`related` 是 context action；`sources` 是 admin。`news-conversation-research` 只供内部 Agent 路径使用，不是网页斜杠命令。公开菜单由 `GET /api/skills` 动态生成。

使用入口是 `build_default_registry()`。旧斜杠文本和自然语言结构化路由分别使用：

```text
registry.execute_text(command_text, SkillContext(services=services, user_id=user_id))
registry.execute_structured(skill_id, arguments, SkillContext(services=services, user_id=user_id))
```

兼容入口 `registry.execute(...)` 仍委托给 `execute_text()`。聊天服务通过统一 `SkillResult` 返回 `status`、`output_kind`、`evidence` 和 `fallback_reason`，业务详情保留在 `data` 中。

自然语言 schedule 只返回 15 分钟有效的进程内确认卡片，确认前不会创建任务；显式 `/schedule` 保持旧的立即创建行为。关闭联网时 factcheck 只使用本地证据：证据充分为 `success + local_only`，不足为 `degraded + web_search_disabled`。
