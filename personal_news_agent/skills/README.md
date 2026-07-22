# Personal News Agent Skills

当前注册四个斜杠命令：

```text
/report <主题> [--category tech,economy] [--time-range 30d]
/brief [主题] [--category tech,sports]
/factcheck <待核查说法> [--category tech,economy]
/sources [category]
```

使用入口是 `build_default_registry()`。调用方传入现有 `build_services()` 返回的服务字典和用户 ID：

```text
registry.execute(command_text, SkillContext(services=services, user_id=user_id))
```

聊天服务会优先识别这些斜杠命令并通过 `skill_result` 返回结构化结果。
