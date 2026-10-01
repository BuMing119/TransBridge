# 拒绝空白摘要条目

- 日期：2026-09-23。
- Epic：assistant-context-compaction。
- Story：[S05 按预算生成可验证摘要候选](../../../../plans/assistant-context-compaction/stories/story-05-budgeted-compaction.md)。
- 范围：用户明确要求仅修复空白摘要校验；本记录对应当前两个代码/测试文件的 Git 差异。

## 问题与行为变化

原校验将包含纯空白字符串的非空列表视为有历史语义。例如 unresolved_questions 为 `["   "]`、其他字段均为空时，摘要仍能通过并替代模型输入中的原历史。

现在问题、建议列表内的每一项及决定陈述都必须包含非空白文本，否则返回 COMPACTION_INVALID_OUTPUT，沿用既有有限修复流程。没有问题、建议或决定时仍允许空列表；任一字段提供有效内容即可，不强制所有字段都有内容。不裁剪或改写有效摘要正文。

## 逐文件变化

- 修改 `src/transbridge/application/assistant_context/compaction.py`：`validate_summary()` 在类型检查后拒绝空白问题、建议条目；对决定的 statement 增加非空白检查，提供明确诊断。原有来源校验、整体空摘要拒绝及调用额度保持不变。
- 修改 `tests/application/assistant_context/test_compaction.py`：新增 15 个参数化案例，覆盖空字符串、空格/制表符/换行、全角空格、有效内容夹杂空白条目、单字段有效内容和正文原样保留；验证空白摘要修复成功后才产生有效候选，修复再次失败不产生候选且原历史保持不变。
- 新增本增量，并最小更新 `docs/changelogs/INDEX.md`；不修改历史日志或计划状态。

## 验证证据

代码修复阶段已执行：

```powershell
uv run pytest tests/application/assistant_context/test_compaction.py tests/smart_assistant/test_context_summary.py -q --disable-warnings
```

结果：45 passed，3 warnings，退出码 0。

最终静态及差异检查：

```powershell
.venv/Scripts/ruff.exe check src tests
.venv/Scripts/ruff.exe format --check src tests
git -c core.safecrlf=false diff --check
```

均通过，格式检查覆盖 1410 文件。中间检查发现一条新增错误消息超过行长，已使用 Ruff 格式化并复验通过。本次记录阶段复用上述实际验证证据，不重复运行业务测试。

## 兼容性与遗留项

- 不改变摘要字段结构、压缩策略、旧摘要链保留规则及持久存储格式，无需数据迁移。
- 不回写或重新生成已存摘要；本次校验收紧针对新生成及修复输出。
- 本次修复范围内无未完成项。未运行全仓测试或真实模型语义评估，校验通过不等于证明摘要语义完整。
- 未提交或推送 Git。
