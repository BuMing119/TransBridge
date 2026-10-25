# 混合模式默认动作规则与任务快照修复

- **日期**：2026-10-23
- **Epic**：`ai-workflow-presets`
- **Story**：Story 1 模式预设配置与有效执行档案；Story 3 翻译/润色/混合入口统一接线
- **关联**：[计划](../../../../plans/ai-workflow-presets/plan.md)、[ADR-026](../../../adr/026-ai-workflow-presets-and-effective-profile.md)
- **状态**：已实现，相关回归通过

## 问题与结果

规则编辑器初始化时有默认规则，但 `LLMConfig` 默认及旧配置缺失规则时返回空列表，随后 `WindowConfigView` 正常加载配置时清空编辑器。混合模式因没有动作分配而把全部条目跳过；直接翻译不使用该规则，因此同一批内容仍能翻译。

默认规则现在由配置层统一提供：未翻译条目翻译，状态 1/2/3/5 的已有译文条目进入润色/校对，锁定和隐藏条目跳过。无需先有译文，也无需每次手动重置。配置默认、加载和编辑器重置复用同一来源。

## 本次变更

- 修改 `src/transbridge/config/llm.py`：新增 `default_action_rules()`，每次返回独立规则及集合，使用稳定规则 ID；`LLMConfig.action_rules` 使用该默认工厂；缺失或空白配置恢复默认，显式 `[]` 保留为空；序列化空规则时写入 `[]`，避免再次与缺失配置混淆。
- 修改 `src/transbridge/ui/tools/ai_translator/_rule_editor_widget.py`：删除 UI 内重复的默认规则声明和组装代码，`reset_to_default()` 使用配置层默认工厂。
- 修改 `src/transbridge/ui/tools/ai_translator/task_config_snapshot.py`：`execution_snapshot()` 将规则中的 `set`/`frozenset` 按稳定顺序转换为 JSON 列表，支持任务保存和恢复。
- 修改 `src/transbridge/ui/tools/ai_translator/task_recovery_binding.py`：仅在 `_signature_value()` 新增集合的稳定投影，避免规则恢复后无法计算配置签名。该文件原有的恢复范围、空任务处理等工作区改动不属于本次记录。
- 新增 `tests/contracts/config/test_mixed_action_rules.py`：验证默认动作、实例隔离、缺失/空白旧配置、读取不写文件、自定义及显式空规则往返。
- 修改 `tests/contracts/config/test_unified_repository.py`：将清空规则的契约更新为持久化显式空列表，并验证重读后仍为空。
- 新增 `tests/ui/tools/test_mixed_default_rules.py`：验证全部未翻译或包含已有译文时，首次打开、模式切换、重新打开均自动产生任务并允许启动；验证自定义/显式空规则及重置默认的一致性。
- 新增 `tests/ui/tools/test_mixed_rule_snapshots.py`：验证默认和自定义规则 JSON 快照往返、凭据隔离、集合顺序不影响签名、动作变化影响签名、旧任务显式空规则恢复。
- 修改 `docs/changelogs/INDEX.md`：仅添加本次记录入口，保留已有工作区索引内容。

## 验证

新增配置和 UI 回归在旧实现中复现默认规则丢失；扩展回归发现规则集合原先无法保存快照和计算恢复签名，修复后相关测试通过。

最终联合回归：

```powershell
uv run --no-sync pytest tests/contracts/config tests/config tests/application/translation tests/ui/tools/test_mixed_default_rules.py tests/ui/tools/test_mixed_rule_snapshots.py tests/ui/tools/test_ai_rule_editor.py tests/ui/tools/test_mixed_preflight.py tests/ui/tools/test_unified_ai_execution.py tests/ui/tools/test_unified_task_config.py tests/ui/tools/test_run_config_projection.py tests/ui/tools/test_profile_manager_workflows.py tests/ui/tools/test_task_recovery_binding.py tests/ui/tools/test_unified_task_runtime.py tests/ui/tools/test_ai_task_consistency.py tests/ui/tools/test_task_recovery_discovery.py -q --basetemp .tmp-mixed-rules-c77c
```

- 退出码 0：**440 passed**，17 条既有依赖/兼容接口弃用警告。
- 首轮默认临时路径下为 439 passed、1 failed：SQLite 无法打开过长的临时数据库路径；改用上面的任务专用短路径后 440 项全部通过，未修改相关业务逻辑。
- `uv run --no-sync ruff check src tests`：退出码 0，全部通过。
- `uv run --no-sync ruff format --check src tests`：退出码 0，1996 个文件格式通过。
- `git diff --check`：退出码 0。
- 未运行全量仓库测试、真实 LLM 调用或安装包构建；本次针对默认配置、任务范围及保存恢复进行验证。

## 兼容与遗留

- 旧配置缺少规则键或值为空白时自动使用默认；读取不改写用户配置文件。
- 已有自定义规则和显式 JSON 空列表不会被默认值覆盖。旧版本清空规则后删除配置键的情况与从未配置不可区分，按缺失配置采用默认；此后主动清空会保存为 `[]`。
- 旧任务快照中的显式空规则继续保持为空，恢复后签名不变。
- 无待完成修复；本次任务专用 QA 目录在收尾时清理。未提交或推送 Git，未重启用户正在运行的程序。
