# Personal News Agent Skills

当前注册三个斜杠命令：

```text
/report <主题> [--category tech,economy] [--time-range 30d]
/brief [主题] [--category tech,sports]
/sources [category]
```

使用入口是 `build_default_registry()`。调用方传入现有 `build_services()` 返回的服务字典和用户 ID：

```text
registry.execute(command_text, SkillContext(services=services, user_id=user_id))
```

本目录没有自动接入现有聊天服务或 API 路由；这样可以在不修改已有代码的前提下独立测试和迭代。
