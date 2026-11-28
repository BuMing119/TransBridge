# 翻译词典的主库、个人库与独立窗口

2026-11-26 · [translation-memory S11–13](../../../../plans/translation-memory/plan.md) · [FR15.1、FR16.5](../../../requirements.md)

## 交付

- 新增 `translation_memory/library.py`、`library_matching.py`。个人库及具名主库使用 SQLite 事务增量保存；游戏和语言分区独立。同原文的不同记录及不同补丁的覆盖译法分别保存。按原记录、类型与字段、类型、原文查找。相同等级优先个人库，再采用稳定顺序。
- 旧 `.tbdict` 在原目录只读，缺少索引的普通条目可按原文使用，受原文限定的定位仍需严格匹配。单个旧文件损坏时记录文件名并继续读取其余内容。旧数据无法恢复曾合并丢失的译文。
- `application/projects/dictionary_apply.py` 准备精确到词条身份的变更集，工程命令保留修订校验和翻译阶段。项目语言来自 `source_encoding.py`；桌面和助手的打开文件、批量解析及显式套用共用此匹配入口。`migration_snapshot.py` 为个人库保存和套用提供冻结的工程行。
- `ui/tools/dictionary_panel.py` 和 `ui/shell/tool_windows.py` 提供独立的非模态单例窗口。日常保存直达个人库；建库、更新、打开目录和刷新位于管理菜单。警告和错误写日志，成功仅显示数量。项目语言设置变动会刷新窗口，关闭或切换来源可使旧任务失效。`ui/workers.py` 允许本地任务关闭网络请求状态通知。移除了旧保存选项、候选仲裁窗口和已停用的 `publication.py`。
- `smart_assistant/tools/_dictionary_application.py`、`tool_migrator.py`、`types.py` 和提示词对齐个人库保存、精确来源和提交撤销。套用仅返回填入与命中统计；保存返回实际变化数。沿用旧工具的调用方请移除 `mod_file_id`、`scope` 和 `tags` 保存参数。
- `bootstrap/history_search.py` 将新库与旧文件都加入历史搜索；FOMOD 保留已有独立查询和来源校验。需求、计划和 ADR 索引已同步。

## 验证

联合回归 492 项通过。Windows 账户缺少创建符号链接权限，安全契约中的一项自动跳过。新增二进制插件样本验证两个同文异译的主文件记录，补丁重新解析后按对应记录继承译文。Qt 离屏验证覆盖关闭、切换来源、后台提交、同名主库和日志提示，640 × 420 窗口已做截图目检。

```powershell
uv run --offline --no-sync --no-cache pytest `
  tests/test_dictionary_library.py `
  tests/test_translation_memory_gui.py `
  tests/test_translation_memory.py `
  tests/test_original_match_memory_migration.py `
  tests/test_fomod_tm_provenance.py `
  tests/application/projects/test_dictionary_apply.py `
  tests/application/projects/test_project_text_encoding.py `
  tests/application/history_search `
  tests/bootstrap/test_history_search_providers.py `
  tests/persistence/test_history_search.py `
  tests/integration/bootstrap/test_history_search_composition.py `
  tests/config/test_text_encoding.py `
  tests/contracts/io/test_open_translation_file.py `
  tests/contracts/projects/test_authoritative_mutation_paths.py `
  tests/contracts/security/test_tool_schema_security.py `
  tests/smart_assistant/tools/test_dictionary_upload_authority.py `
  tests/smart_assistant/test_undo_capture.py `
  tests/smart_assistant/tools/test_file_import_parity.py `
  tests/smart_assistant/test_tool_consolidation.py `
  tests/smart_assistant/test_english_prompt_surface.py `
  tests/ui/test_open_file_workflow.py `
  tests/ui/test_parse_hydration_authority.py `
  tests/ui/test_project_text_encoding.py `
  tests/ui/operations/test_write_text_encoding.py `
  tests/ui/test_project_editing_boundary.py `
  tests/ui/test_long_text_window_layout.py `
  tests/ui/test_destructive_confirmation_defaults.py `
  tests/ui/tools/test_dictionary_apply_feedback.py `
  tests/test_fomod_pipeline.py `
  tests/test_fomod_typed_pipeline.py `
  tests/application/fomod `
  tests/contracts/io/test_stage_policy.py `
  tests/ui/tools/test_history_search_interactions.py `
  tests/ui/tools/test_history_search_window.py -q -rs --tb=short
uv run --offline --no-sync --no-cache ruff check src tests
uv run --offline --no-sync --no-cache ruff format --check src tests
git diff --check
```

本轮验证针对词典及关联入口。未执行全仓 pytest，也未制作发行包。
