# 修复 AI 空原文拆分重试与结构化输出诊断

- 日期：2026-10-02
- Epic/Story：maintenance/ai-empty-source（直接修复）

## 原因与行为

用户任务日志中，首批 90 条包含 56 条空原文。模型返回空译文后，领域解析器按缺项处理，反复拆批；97 次正式翻译调用中，95 次只包含空原文。10 次 JSON 错误均来自 Markdown 代码块包装。

空或纯空白原文现在在任务范围和翻译候选筛选时排除，不计入可处理数量，不发起翻译或后处理，保留原文、译文和阶段。ActionPlanner 将此类输入标记为 empty_source / SKIP，独立批次规划入口也不会创建空内容请求。有效原文仍要求有效非空译文。

Responses 的 text.format 明确发送 strict=true；服务仍返回 Markdown 代码块时，提示检查接口或模型的原生结构化输出支持。遵守 ADR-032，不将代码块剥除后静默当作成功。

## 文件变更

- 修改 `src/transbridge/ai_translator/translator.py`：候选和后处理筛选排除空白原文。既有大模块只做局部缺陷修复，没有新增职责。
- 修改 `src/transbridge/application/translation/planning.py`：规则匹配前将空白原文分配到 SKIP，保留完整且互斥的行动划分。
- 修改 `src/transbridge/ui/tools/ai_translator/task_scope.py`：翻译、混合、校对任务范围及可处理数量一致排除空白原文。
- 修改 `src/transbridge/infra/llm_structured_outputs.py`：请求开启 strict；代码块响应给出可操作诊断，不泄露正文。
- 修改 `docs/adr/032-native-structured-outputs-for-ai-translation.md`：同步规范请求示例。
- 修改 `tests/ai_translator/test_translator_term_conflicts.py`：混合/全空输入、覆盖开关、请求数、进度及持久化原值回归。
- 修改 `tests/contracts/translation/test_planning.py`：空白输入在翻译/校对规则下均跳过，不进入上下文批次。
- 新增 `tests/ui/tools/test_ai_task_empty_source.py`：三种任务模式及覆盖开关下范围和估算一致。
- 修改 `tests/infra/test_llm_structured_outputs.py`：代码块拒绝和敏感正文不回显。
- 修改 `tests/infra/test_openai_structured_outputs.py`：普通、流式与缓存拒绝重试保持 strict 契约。
- 修改 `docs/changelogs/INDEX.md`：添加本条记录。

## 验证

```powershell
uv run --no-sync pytest tests/ai_translator/test_translator_term_conflicts.py tests/contracts/translation/test_planning.py tests/contracts/io/test_stage_policy.py tests/ui/tools/test_ai_task_empty_source.py tests/infra/test_llm_structured_outputs.py tests/infra/test_openai_structured_outputs.py tests/infra/test_anthropic_structured_outputs.py tests/ai_translator/test_structured_output_contracts.py tests/ai_translator/test_prompt_builder.py tests/ui/tools/test_unified_ai_execution.py tests/ui/tools/test_workflow_logging_client.py -q -p no:cacheprovider --basetemp=.tmp-empty-source-qa/regression
.venv/Scripts/ruff.exe check src tests
.venv/Scripts/ruff.exe format --check src tests
git diff --check
```

- 最终回归 217 项通过；Ruff 检查和格式检查通过，Git 差异无空白错误。
- 初次测试受 Windows 沙箱临时目录权限影响，改为沙箱外现有 uv 环境和本次专用临时目录完成验证；期间修正新增用例读取集合旧对象、混合模式缺少规则的夹具错误。
- 只读回放实际日志：97 个翻译请求中 95 个空原文请求被新筛选排除；首批从 90 条筛到 34 条，规划 34 条，已有响应解析成功 34 条，缺项 0。未复制用户翻译数据进仓库，未调用远端 LLM。

## 限制与兼容性

- 不改变翻译文件格式或持久化结构，无数据迁移。
- 未重新打包 exe；已有 exe 需用更新源码重新构建。
- 未验证远端服务对 strict 参数的执行情况；忽略该参数的服务仍可能返回不合法响应，客户端继续明确拒绝。
- 未修改本次开始前已存在的 build.bat、installer/setup.iss、pyproject.toml、uv.lock 变更。
