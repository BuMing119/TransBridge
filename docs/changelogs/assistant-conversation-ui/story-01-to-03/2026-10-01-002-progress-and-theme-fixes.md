# 修复进度卡来源与空白页主题刷新

日期：2026-10-01。Epic：assistant-conversation-ui；Story：S01～S03。
关联：[方案与验收](../../../../plans/assistant-conversation-ui/plan.md)。

## 原因与修改

QA 实际 Qt 诊断复现了清空后旧卡片重现、100 条消息截断后旧卡片移到最新回复下方、快捷按钮保留旧主题颜色三个问题。本增量仅记录本次修复，不包含之前的布局实现及用户已有的上下文压缩改动。

- 修改 `src/transbridge/ui/tools/smart_assistant/message_list_view.py`：先执行消息数量上限再定位卡片；缺少可见来源消息时移出布局并隐藏，来源恢复后重新定位。任务本身仍在详情页中，不删除权威请求数据。
- 修改 `src/transbridge/ui/tools/smart_assistant/conversation_presentation.py`：新增展示层主题刷新，覆盖快捷按钮、空白页、提示和详情窗口，并同步后续新卡片使用的主题；空白页根据实际布局中的卡片判断，隐藏卡片不再阻止引导出现。
- 修改 `src/transbridge/ui/tools/smart_assistant/chat_widget.py`：在已有主题刷新入口接入展示层，保持 facade 仅作局部接线，不增加业务职责。
- 修改 `tests/ui/tools/smart_assistant/test_conversation_layout.py`：新增 4 项回归，覆盖清空后后台刷新与新请求、100 条消息截断、请求先于来源历史到达及多来源恢复、浅色/深色/浅色主题往返。
- 修改 `plans/assistant-conversation-ui/plan.md`：将旧的“缺少来源时放在末尾”规则替换为隐藏消息内卡片、详情页保留任务；记录修复验收。
- 修改 `docs/changelogs/INDEX.md`：加入本增量入口。既有增量保持不变。

## 验证

使用已有 uv 环境及离线模型替身，未修改依赖。以下命令退出码均为 0：

```powershell
uv --no-cache run --no-sync pytest tests/ui/tools/smart_assistant/test_conversation_layout.py tests/ui/tools/smart_assistant/test_theme_migration.py -q --tb=short --basetemp=.tmp-assistant-fix-focus-20261001 -p no:cacheprovider
uv --no-cache run --no-sync pytest tests/ui/tools/smart_assistant tests/ui/characterization/test_chat_widget_contract.py tests/smart_assistant tests/application/assistant_requests -q --tb=short --basetemp=.tmp-assistant-fix-full-20261001 -p no:cacheprovider
uv --no-cache run --no-sync ruff check src tests
uv --no-cache run --no-sync ruff format --check src tests
git diff --check
```

定向 19 项通过；联合 1240 项通过，65 条既有弃用警告；Ruff 检查及 1414 个文件的格式检查通过。真实 LLM/远端服务、安装包与全仓测试未运行，本次范围为 UI 展示修复。

## 兼容性与遗留

无数据迁移，不修改请求协议、执行状态或撤销权限。来源不在消息区中的任务改从“任务与会话详情”访问。本次三个已复现问题均已修复，无未完成项。
