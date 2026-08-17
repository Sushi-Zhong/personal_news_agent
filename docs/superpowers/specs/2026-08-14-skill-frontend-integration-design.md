# Skill 前端闭环补全设计

## 目标

让前端以 `/api/skills` 的公开 Manifest 投影为唯一业务 Skill 清单。新增 `public` Skill 后，桌面端和移动端无需继续维护命令白名单，即可完成菜单展示、命令发送、结构化结果渲染和证据查看。

## 范围

- 保留 `/topic`、`/track`、`/feed`、`/search` 等前端本地命令行为。
- 保留 `/report`、`/brief`、`/factcheck`、`/map`、`/schedule` 的现有兼容参数整理。
- 对未被本地逻辑消费、但存在于公开 Skill 清单中的命令，原样发送到聊天后端。
- 补齐 `change_digest`、`coverage_compare`、`schedule_confirmation` 和统一证据列表的浅色、深色、移动端样式。
- 不修改后端接口、Manifest 字段或 Skill 业务实现。

## 数据流

1. `shared.js` 请求 `/api/skills`，缓存启用且公开的 Skill 投影。
2. `/` 菜单按命令、名称、描述和别名过滤。
3. 用户发送命令后，页面先执行现有本地/兼容分支。
4. 若命令属于公开 Skill，则把原始消息交给 `sendChatIntoTurn()`。
5. 后端统一通过 `SkillRouter` / `SkillRegistry` 执行并返回 `output_kind`。
6. `shared.js` 按 `output_kind` 渲染结果；未知类型回退到 Markdown。

## 错误与兼容

- Skill 清单尚未加载完成时，命令判断等待同一个加载 Promise，避免启动竞态。
- 清单加载失败时保留现有本地命令，动态 Skill 菜单保持关闭。
- 非公开、禁用或未知命令不进入通用透传，继续显示简洁的可用命令提示。
- 原始命令文本不重写，避免丢失 `changed`、`compare` 的结构化参数表达。

## 视觉

- 结构化结果使用一个低对比度内容表面，内部以留白和细分隔线建立层级。
- 状态只使用统一蓝色轻量标签，不使用渐变或多色语义块。
- 正文沿用现有字号体系，标题通过字重而非夸张字号区分。
- 深色模式使用现有深色 token；移动端取消固定宽度并压缩内边距。

## 验收

- `/changed`、`/compare` 在桌面端和移动端均会发送到后端。
- 新增任意 `public` Skill 时无需修改页面命令分支。
- 菜单能用 canonical command、中文名称、描述或 alias 搜索。
- 新结构化结果在浅色、深色和移动端均可读且不溢出。
- 相关前端契约、Skill API/Registry 和业务 Skill 测试全部通过。
