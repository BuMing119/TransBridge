# 阻止无效术语候选入库与匹配

日期：2026-10-03。归属：maintenance/term-candidate-validation。
计划：[术语候选词法校验](../../../../plans/term-candidate-validation/plan.md)。

## 行为与边界

拒绝空白、纯标点、纯受保护标签和占位符；英文抽取候选必须完整匹配，并保持同一原译配对中的逐字证据。标记内部的字母不能单独成为抽取证据。

已有无效记录保留在原动态库内，但不再参与精确、普通、强制、语义及批次间共享术语匹配，旧缓存也经过过滤。合法缩写、原译相同的名称、数字及非拉丁名称继续允许。

遵照用户限定，不改变 Eye/Far 等现有词条的语义判定、参考/强制属性及来源优先级，不新增用户确认步骤；不批量重写用户术语文件，也不重算旧任务结果。

## 逐文件改动

- 新增 `src/transbridge/ai_translator/term_validation.py`：独立词法校验，复用受保护语法识别，提供有效词法内容、原译配对有效性及完整英文单词证据检查；采用有界缓存。
- 修改 `src/transbridge/ai_translator/noun_extractor.py`：LLM 抽取结果经过词法与同配对完整匹配检查。
- 修改 `src/transbridge/ai_translator/existing_term_extractor.py`：直接名称初始化拒绝无效内容；从已有译文建立抽取证据时使用同一规则。
- 修改 `src/transbridge/ai_translator/term_database.py`：动态新增入口拒绝无效值；精确/普通匹配表过滤无效主词和变体；语义召回及 in-flight 过滤覆盖旧索引。原有数据加载与保存格式不变。
- 修改 `src/transbridge/ai_translator/required_term_matching.py`：强制匹配入口拒绝无效词形、主词和译名。此前完整词与最长命中实现不计入本次增量。
- 修改 `src/transbridge/ai_translator/translator.py`：自动名称候选在写入、更新通知和新增计数前过滤，避免被拒候选仍算作新增术语；其他既有未提交修改不归入本次。
- 新增 `tests/ai_translator/test_term_validation.py`：覆盖纯语法内容、正常名称、旧动态文件无损保留、无效变体、语义缓存及两种作用域下的 in-flight 过滤。
- 扩展 `tests/ai_translator/test_noun_extractor.py`：覆盖纯标点/标签/占位符、双语单词片段、标签内部字母、合法缩写与 Eye 映射保留。
- 扩展 `tests/ai_translator/test_existing_term_extractor.py`：验证初始化不会保存无效名称和抽取片段，正常候选保留。
- 扩展 `tests/ai_translator/test_translator_term_conflicts.py`：验证无效名称不落盘、不触发通知、不计入新增数。
- 新增 `plans/term-candidate-validation/plan.md`，更新 `plans/INDEX.md` 与 `docs/changelogs/INDEX.md`。

较大既有模块只增加局部校验接线；完整规则提取至独立模块，未增加 UI 或流程编排职责。

## 验证

以下命令均退出 0：

```powershell
uv run pytest tests/ai_translator/test_noun_extractor.py tests/ai_translator/test_term_validation.py tests/ai_translator/test_existing_term_extractor.py tests/ai_translator/test_term_database.py tests/ai_translator/test_required_term_matching.py -q --disable-warnings
# 74 passed

uv run pytest tests/ai_translator tests/application/translation tests/contracts/translation tests/ui/tools/test_ai_task_consistency.py tests/ui/tools/test_unified_ai_execution.py -q --disable-warnings
# 627 passed

# 随后补齐自动名称新增计数过滤，再验证其调用链
uv run pytest tests/ai_translator/test_translator_term_conflicts.py -q --disable-warnings
# 23 passed（包含 2 项新增计数回归，与综合组有重叠）

uv run ruff check src tests
uv run ruff format --check src tests
git -c core.safecrlf=false diff --check
```

另外通过 `uv run python -` 只读加载用户当前 821 条动态术语并调用新校验：仅 `... → ...` 被过滤，Eye 映射仍有效；前后 SHA-256 一致，未改写该文件。

## 兼容与遗留

无需数据迁移或用户中途决策；新进程中的任务使用新规则，旧记录仍展示原运行结果。未知自定义占位符语法不在本次标准识别范围；普通词的语义歧义按用户要求暂不处理。未运行安装包构建、真实模型或外部服务；未提交或推送 Git。已批准范围无实现遗留项。
