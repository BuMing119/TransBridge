# 003: QA 审计修复 — 9 Blocker/Critical + 2 Major 全量修复

**日期**: 2026-05-18
**类型**: 改
**关联**: Epic: Agent 工具系统全面扩展 > QA修复

## 修改文件

### `src/transbridge/smart_assistant/tools/tool_proofreader.py` (改)
- **修改内容 (B1+B2)**: `_tool_run_postprocess` 中 `PostProcessor(llm_client=, config=, term_manager=, esp_path=)` 四参数构造改为 `PostProcessor(config)` 单参数 + `processor.register_default_checkers(term_manager=, llm_client=)`。原代码三个参数 `__init__` 不接受
- **原因**: `PostProcessor.__init__` 仅接受 `config`，`llm_client`/`term_manager` 需通过 `register_default_checkers()` 注入

### `src/transbridge/smart_assistant/tools/tool_translator.py` (改)
- **修改内容 (B3)**: `_tool_start_polish` 中 `LLMPolisher(intensity=intensity)` → `LLMPolisher(llm_client=llm_client, polish_level=polish_level)`（补传必填第一参数+纠正参数名）
- **修改内容 (B4)**: 导入路径 `...post_processor.llm_polisher` → `...post_processor.polisher`（实际文件名为 polisher.py）
- **修改内容 (B5)**: 新增 `create_llm_client(llm_cfg)` 创建 LLMClient，原代码无 LLM 客户端
- **修改内容 (N1)**: 行112 `from ...api_client import ParatranzClient` → `from src.transbridge.paratranz import ParatranzClient`
- **修改内容 (C5)**: polish 完成后通过 `tool_proofreader._last_report = {...}` 写入报告缓存
- **修改内容 (C6)**: 新增 `{"light":"light","medium":"moderate","heavy":"aggressive"}` 值映射，将用户参数 `intensity` 转为 `LLMPolisher` 的 `polish_level`
- **原因**: 全项目健康扫描发现 5 个 Blocker + 2 个 Major，`start_polish` 从未工作过

### `src/transbridge/smart_assistant/tools/tool_paratranz.py` (改)
- **修改内容 (N1)**: 4 处 `from src.transbridge.paratranz.api_client import ParatranzClient` → `from src.transbridge.paratranz import ParatranzClient`（模块 `api_client.py` 不存在，类定义在 `paratranz_client.py` 由 `__init__.py` 重导出）
- **原因**: 全部 9 个 PT 工具在当前状态下运行时均会 `ModuleNotFoundError` 崩溃

### `src/transbridge/smart_assistant/tools/tool_parser.py` (改)
- **修改内容 (N2)**: `from src.transbridge.parser.sst_parser import SST_Parser` → `from src.transbridge.parser.xt.sst_parser import SST_Parser`（实际路径）
- **修改内容 (N3)**: `StringsImporter` 导入替换为 `ToolResult.fail("import_strings 暂不可用")` — 类在代码库中完全不存在
- **原因**: 导入路径错误 + 模块完全不存在

### `src/transbridge/smart_assistant/agents/agent_registry.py` (改)
- **修改内容 (N4)**: orchestrator 工具列表中 `editor:get_statistics` → `default:get_statistics`（get_statistics 注册在 default namespace）
- **原因**: 命名空间错误导致 orchestrator 无法找到统计工具

### `docs/test-reports/story-25-postprocess-unification-qa.md` (改)
- **修改内容**: 新增章节七「全项目扫描新增发现」（N1-N4）+ 章节八「修订后的审查结论」。更新日期、审计范围、结论
- **原因**: 全项目健康扫描发现的 4 个额外问题补充入 QA 报告
