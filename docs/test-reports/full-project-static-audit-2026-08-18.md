# TransBridge 全项目需求—Plan—代码静态审计报告

- **审计日期**：2026-08-18
- **复核修订**：2026-08-18（Sol 模型二次复核）
- **审计方式**：多 Agent 并行只读静态审查（需求/Plan/代码映射、可追溯性审查、安全与发布风险审查）
- **审计范围**：`docs/requirements.md`、`docs/adr/`、`plans/`、`docs/changelogs/`、`src/transbridge/`、`tests/`、`pyproject.toml` 与 `uv.lock`
- **未执行事项**：未运行自动化测试、未启动 GUI/MCP、未构建或安装发行包、未修改任何业务代码。
- **结论边界**：本报告的“已实现”仅表示存在 Plan、源码或历史变更记录的静态证据；不构成功能验收或发布批准。

## 1. 执行摘要

项目的实现覆盖范围较完整：`plans/` 下共有 **25 个包含 `plan.md` 的 Epic**，`plans/INDEX.md` 记录 **195 个 Story**，并声称“待实现：无”。源码中亦能找到各主要功能域的实现模块和大部分对应测试。

但当前不能以“所有 Epic 已完成”作为发布依据。审计发现：

1. **1 项 P0 发布阻断项**：发行包 CLI 入口及源码导入体系存在高置信启动失败风险。
2. **7 项 P1 高优先级问题**：归档边界与资源限制、Agent 解析分派、凭据暴露、依赖遗漏、Agent 路径策略、MCP 启动与 Windows 兼容性均存在实际不可用或高风险路径。
3. **需求—Plan—代码的状态严重漂移**：多个需求仍写“待方案/待编码”，而代码、索引或 changelog 显示已完成；部分 Story 链接失效或缺少独立工件。
4. **流程配置缺失**：`.Codex/bm_config/paths.json` 不存在，`bm-orchestrator` 无法从项目配置稳定解析各输出目录。

推荐优先级是：先消除 P0 与核心 P1 代码风险，再完成文档基线回写与可追溯矩阵，最后运行针对性回归和发布安装验证。

## 2. 审计方法与证据等级

### 2.1 方法

- Agent A：读取需求、索引、Plan、ADR、changelog，检查需求状态与工件完整性。
- Agent B：将每项需求相关 Plan 映射到实现包、关键类/函数及测试目录。
- Agent C：对归档、Agent 工具、MCP、认证、打包元数据进行静态安全和可用性审查。
- 主审：复核配置门禁、git 工作区状态、时间戳与各审计结论的交叉一致性。

### 2.2 证据等级

- **静态确认**：可由源码、配置、文件路径和调用关系直接确认缺陷本身；若最终影响依赖第三方库或运行环境，仍单独标为待运行验证。
- **静态高置信风险**：调用契约或运行环境限制明确不匹配，仍需运行测试确认最终表现。
- **待运行验证**：依赖实际安装、GUI、网络、平台或集成环境，不能由本次审计单独确认。

## 3. 项目流程与文档基线

### 3.1 配置门禁

`bm-orchestrator` 所要求的 `.Codex/bm_config/paths.json` 与 `.codex/bm_config/paths.json` 均不存在。当前文档目录可按默认值 `docs/`、`plans/` 被人工扫描，但自动化流程不应声称已正确订阅配置。

**影响**：后续分析、方案、记录与测试阶段无法可靠地按项目约定定位目录；记录门禁也难以自动执行。

**建议**：先执行 `/bm-init` 创建并确认路径配置，再用该配置驱动后续文档工作。

### 3.2 工作区状态

审计期间 `git status --short` 仅显示 `.agents/` 与 `.codex/` 为未跟踪目录；本次审计未写入业务源码。本文档是用户明确要求持久化的审计产物。

## 4. 需求—Plan—代码映射

### FR1：插件文件解析

- **Plan**：`core-data-model`（5 Story）、`file-parsing`（11 Story）。
- **主要实现**：`converter/translation_entry.py`、`converter/translation_entry_collection.py`、`parser/plugin_parser.py`、`parser/eet_parser.py`、`parser/strings_file.py`、`parser/xt/xt_parser.py`、`parser/xt/sst_parser.py`、`parser/xt/sst_serializer.py`。
- **覆盖判断**：FR1.1–FR1.9.5 均有实现文件；Agent 入口的解析分派存在严重缺陷，见 **P1-2**。

### FR2：翻译条目管理

- **Plan**：`core-data-model`、`ui-workbench`、`stage-unification`（3 Story）、`label-system`（4 Story）。
- **主要实现**：`converter/`、`ui/workbench/`、`ui/context.py`、`converter/context_categories.py`。
- **覆盖判断**：统一模型、筛选、Stage、标签均有实现证据。

### FR3：ParaTranz 平台集成

- **Plan**：`paratranz-integration`（8 Story）。
- **主要实现**：`paratranz/paratranz_client.py`、`paratranz/api/`、`paratranz/workflow/`、`ui/paratranz/`。
- **覆盖判断**：上传、下载、术语与导出工件均有模块；认证日志存在敏感信息暴露，见 **P1-3**。

### FR4：文件写回

- **Plan**：`file-writing`（7 Story）。
- **主要实现**：`writer/plugin_writer.py`、`writer/eet_xml_writer.py`、`writer/xt_xml_writer.py`。
- **覆盖判断**：ESP、EET、XT 与 Strings 写回均存在实现证据。

### FR5：AI 自动翻译与语义检索

- **Plan**：`ai-translation`（14 Story）、`vector-term-retrieval`、`fr5.12-embedding-optimization`（3 Story）。
- **主要实现**：`ai_translator/translator.py`、`prompt_builder.py`、`term_database.py`、`term_vector_index.py`、`batch_planner.py`、`infra/embedding_client.py` 与 `ui/tools/ai_translator/`。
- **静态证据**：`TermVectorIndex.search_batch()` 和 `TermDatabase.match_terms_enhanced()` 存在，符合 FR5.12 的批量召回与混合检索方向。

### FR6：后处理、润色与报告

- **Plan**：`ai-post-process`（13 Story）。
- **主要实现**：`ai_translator/post_processor/{consistency_checker,format_validator,quality_gate,llm_refiner,polisher,llm_arbiter,post_processor,report_generator}.py`。
- **覆盖判断**：五阶段处理、独立润色和报告生成均有实现模块。

### FR7：工作台与智能助手

- **Plan**：`ui-workbench`（22 Story）、`llm-chat`（10 Story）、`agent-upgrade`（12 Story）、`smart-assistant-qa-fix`。
- **主要实现**：`ui/workbench/`、`ui/tools/smart_assistant/`、`smart_assistant/{agents,guardrails,mcp,memory,observability,skills,workers}/`、`graph_executor.py`、`execution_engine.py`。
- **覆盖判断**：实现模块齐全，但 FR7.13 的需求正文与 Plan 状态冲突；MCP 启动链存在高风险问题。

### FR8：项目持久化与翻译版本

- **Plan**：`project-persistence`（8 Story）。
- **主要实现**：`persistence/workspace.py`、`persistence/project.py`、`persistence/variant_store.py`。
- **覆盖判断**：项目、版本、工作区和快照均有实现证据。

### FR9：Agent 工具系统

- **Plan**：`agent-tool-expansion`（26 Story）。
- **主要实现**：`smart_assistant/tool_registry.py` 与 `smart_assistant/tools/` 下的 parser/editor/translator/writer/paratranz/archive/migrator 工具。
- **覆盖判断**：工具注册和说明体系存在；解析工具的实际调用契约错误，且需求状态与 S22 完成状态不一致。

### FR10：Smart Assistant 模块拆分重构

- **Plan**：`smart-assistant-refactor`（4 Story）。
- **主要实现**：`smart_assistant/execution_engine.py`、`graph_executor.py`、`tool_execution_handler.py`、`conversation_orchestrator.py` 及相关 UI 控制器。
- **覆盖判断**：拆分后的模块和历史 QA/changelog 均存在，但本次未运行重构回归测试。

### FR11：工具提示词分层加载

- **Plan**：`tool-prompt-layering`（5 Story）。
- **主要实现**：`smart_assistant/tool_registry.py`、各工具描述构建器及 `ToolRegistry.build_tool_help()`。
- **覆盖判断**：分层工具说明与元工具代码存在；Plan、Story 与实现状态仍需纳入统一追溯矩阵。

### FR12：SessionController 会话控制流

- **Plan**：`session-controller`（2 Story）。
- **主要实现**：`smart_assistant/session_controller.py` 及 ChatWidget/Orchestrator/ToolExecutionHandler 接入代码。
- **覆盖判断**：`SessionController` 类和接入代码存在；Plan 引用的两个独立 Story 文件缺失，验收复选框也未回写。

### FR13：多会话管理

- **Plan**：`session-manager`（3 Story）。
- **主要实现**：`smart_assistant/session_manager.py`、`ui/tools/smart_assistant/session_list_widget.py` 及 `panel.py` 集成。
- **覆盖判断**：`SessionManager`、会话列表和面板集成代码存在；Plan 引用的三个独立 Story 文件缺失，验收复选框未回写。

### FR14：后台任务监控面板

- **Plan**：`task-monitor`（2 Story）。
- **主要实现**：`ui/tools/smart_assistant/task_monitor.py`、`panel.py` 与 `chat_widget.py` 中的刷新/控制接入。
- **覆盖判断**：`TaskMonitorWidget` 和接入代码存在；Plan 引用的两个独立 Story 文件缺失，验收复选框未回写。

### FR15：FOMOD 与翻译记忆

- **Plan**：`translation-memory`（10 Story）、`fomod-translation`（4 Story）。
- **主要实现**：`translation_memory/{model,manager}.py`、`fomod/{fomod_xml,builder,pipeline}.py`、`ui/tools/fomod/fomod_panel.py`、`ui/tools/dictionary_panel.py`。
- **覆盖判断**：FOMOD 面板已静态具备过滤预设、zip/7z、目标语言、旧归档、AI 开关和输出路径等 FR15.9 能力；但需求文件仍写“待方案”。

### FR16：通用文件与词条工具

- **Plan**：`agent-infra-tools`（5 Story）。
- **主要实现**：`fileops/{archive,differ,filter_rules}.py`、`migrator/key_migrator.py`、`smart_assistant/tools/{tool_archive,tool_migrator}.py`。
- **覆盖判断**：模块与 Agent 工具均已存在；归档安全、发布依赖与绝对路径策略需要优先整改。

### NFR1：性能

- **需求重点**：大文件处理、批量翻译吞吐、后台任务不阻塞 UI。
- **实现证据**：QThread/worker 模式、批量规划、向量批量检索、编码缓存、增量索引和任务监控模块。
- **审计结论**：存在性能优化代码，但本次没有基准测试数据；归档解压还缺少文件数、累计体积和压缩比限制。

### NFR2：可靠性

- **需求重点**：断点续传、失败隔离、原子持久化、异常可恢复。
- **实现证据**：翻译/后处理 checkpoint、TaskManager 状态、SessionManager 原子替换、全局异常日志。
- **审计结论**：机制存在；Agent parser 与 MCP 启动链缺陷会直接降低关键路径可靠性，需集成测试确认。

### NFR3：兼容性

- **需求重点**：Windows、Python 与多种 Bethesda/翻译文件格式兼容。
- **实现证据**：多格式 parser/writer、Windows 路径与 PyQt6 桌面应用代码。
- **审计结论**：MCP 对 Windows `stdin` 使用 `select.select()`，以及 Agent 拒绝绝对路径，均与目标平台存在冲突。

### NFR4：安全性

- **需求重点**：路径安全、凭据保护、Agent 权限与 MCP 认证。
- **实现证据**：GuardChain、InputValidationGuard、PermissionGuard、MCP token 校验和输出脱敏逻辑。
- **审计结论**：安全框架存在，但路径策略过度阻断正常使用；完整 token 日志、明文凭据和归档资源限制仍需修复。

### NFR5：可扩展性

- **需求重点**：统一数据模型、模块分层、工具注册与可替换 LLM/Embedding 实现。
- **实现证据**：TranslationEntry/Collection、Agent 工具注册表、独立 infra/fileops/migrator/fomod 包。
- **审计结论**：总体模块化方向明确；解析器缺少统一适配契约，需求到实现也缺少机器可读追溯矩阵。

### NFR6：打包分发

- **需求重点**：可安装、可启动、依赖完整、Windows 发行包行为一致。
- **实现证据**：`pyproject.toml`、PyInstaller 开发依赖、`build.bat` 与运行入口。
- **审计结论**：当前 console script、`src.transbridge` 导入体系、版本号和 7z/RAR 依赖声明存在发布阻断或高风险问题；必须通过 wheel/可执行文件隔离安装验证。

## 5. 代码风险与错误检测

### P0-1：发行包 CLI 入口及导入体系存在启动失败风险

**静态高置信风险，待构建验证**。

- `pyproject.toml:31` 定义 `transbridge = "transbridge:main"`，但 `src/transbridge/__init__.py` 仅定义版本号，没有导出 `main`。
- 可调用入口位于 `src/transbridge/main.py`，但该文件使用 `from src.transbridge.ui.app import main`。
- `src/transbridge/` 中约有 71 个 Python 文件使用 `src.transbridge` 绝对导入。标准 src-layout 安装通常暴露顶层包 `transbridge`，不保证安装环境存在顶层 `src` 包。

**影响**：仅把 console script 改为 `transbridge.main:main` 仍可能在导入 `src.transbridge...` 时失败；风险可能影响的不只是 CLI，也包括 wheel/打包应用中的懒加载模块。

**修复建议**：

1. 明确项目的唯一包导入规范，优先统一为 `transbridge...` 或包内相对导入。
2. 将 console script 指向实际可调用函数，并避免通过包根隐式转发掩盖导入问题。
3. 构建 wheel，在全新虚拟环境安装后验证 `python -c "import transbridge"`、`python -c "import transbridge.ui.app"` 和 `transbridge --help`。
4. 对 PyInstaller/Windows 成品执行独立启动 smoke test。

### P1-1：归档边界校验不严谨且缺少资源限额

**边界检查缺陷静态确认；实际越界写入待恶意归档验证**。

- `fileops/archive.py:88-92` 使用字符串 `startswith()` 判断目标是否位于解压目录内，存在目录名前缀碰撞，不能作为可靠的安全边界。
- 但 ZIP 分支最终调用 Python `ZipFile.extract()`；标准库会尝试清理绝对路径和 `..`，因此仅凭上述前缀碰撞不能静态证明 ZIP 文件最终可写出目标目录。
- 当前版本的 `py7zr` 和 `rarfile` 具有各自的路径清理逻辑；项目未声明/锁定这两个依赖，无法从仓库确定实际运行版本及安全行为。
- 三种格式均未设置最大成员数、最大累计解压字节数或最大压缩比，压缩炸弹/磁盘耗尽风险是确定的。
- 能力已通过 `smart_assistant/tools/tool_archive.py` 暴露给 Agent 工具，扩大了非可信归档输入面。

**修复建议**：

1. 使用 `Path.resolve().relative_to(destination.resolve())` 或等效 `is_relative_to()` 统一校验所有成员，避免依赖字符串前缀。
2. 声明并锁定无已知路径穿越漏洞的 `py7zr`/`rarfile` 版本，同时保留应用层逐成员校验。
3. 配置最大成员数、最大累计解压字节数、单文件上限和最大压缩比。
4. 增加 `../`、绝对路径、Windows 盘符/UNC、符号链接、目录前缀碰撞和压缩炸弹测试；只有复现越界写入后才升级为 P0。

**参考**：[Python `zipfile` 官方文档](https://docs.python.org/3/library/zipfile.html)说明 `extract()`/`extractall()` 会尝试阻止绝对路径和 `..`；[`py7zr` 官方仓库](https://github.com/miurahr/py7zr)的安全说明建议使用已修复历史路径穿越漏洞的安全版本；[`rarfile` 当前源码](https://github.com/markokr/rarfile/blob/master/rarfile.py)在 `_extract_one()` 中调用 `sanitize_filename()`。

### P1-2：Agent 解析器分派无法成功调用主要格式

**静态确认**。

- `smart_assistant/tools/tool_parser.py:126-155` 为 EET/XT 指向不存在的 `parser.eet_xml_parser` 与 `parser.xt_xml_parser`。
- 同文件约 175-184 行对全部格式统一执行 `cls().parse(path)`。
- 实际契约并不统一：EET/XT 使用 `from_file`；SST 构造需要 entries 且使用 `from_file`；插件解析使用 `parse_plugin`。
- 当前 Agent 集成测试主要覆盖空值、路径不存在或扩展名错误，未覆盖真实文件成功解析路径。

**影响**：`parse_esp`、`parse_eet`、`parse_xt`、`parse_sst` 可能全部只能返回失败结果，FR9 的关键工具不可用。

**修复建议**：建立“格式 → 导入路径 → 调用适配器”的显式注册表，为每种格式封装统一的 `parse(path)` 适配层；使用最小真实/fixture 文件覆盖四条成功路径与异常路径。

### P1-3：认证 token 可能泄露且存储保护不足

**静态确认**。

- `paratranz/paratranz_client.py:87-92`、`139-142` 在 401 分支直接打印完整 token。
- LLM、Embedding、MCP、ParaTranz token 通过 `config/llm.py:166-213` 与 `config/paratranz.py:61-77` 保存为明文 INI。

**修复建议**：立即移除 token 输出并使用统一的脱敏日志器；优先使用系统凭据库（Windows DPAPI/Keyring），至少设置凭据文件权限、支持迁移并在 UI 明确告知风险。

### P1-4：7z/RAR 运行依赖未声明

**静态确认**。

- 运行代码在 `fileops/archive.py` 中动态导入 `py7zr` 和 `rarfile`。
- `pyproject.toml`、`uv.lock` 未声明这两个依赖。

**影响**：干净安装环境中 FOMOD 的 7z/RAR 路径将失败，和 FR15/FR16 的发布承诺不一致。

**修复建议**：将库及 RAR 后端分发策略显式写入项目依赖/打包配置；在隔离环境做 zip、7z、rar 端到端测试并明确缺后端时的用户提示。

### P1-5：绝对路径策略使桌面 Agent 文件操作不可用

**静态确认**。

- `tool_parser.py:35-40` 无条件拒绝绝对路径。
- `guardrails/input_validator.py:66-89` 按参数名执行同类拒绝。
- 桌面文件选择器通常返回绝对路径；`tool_archive` 的 `archive_path`、`dest_dir`、`src_dir` 也会受此影响。

**修复建议**：改为规范化路径后校验“用户已选择/授权根目录”或项目工作区白名单，保留对目录穿越、UNC、设备路径与驱动器相对路径的拒绝；补 Windows 路径测试。

### P1-6：MCP 启动与调用上下文链不完整

**静态高置信风险**。

- `ui/app.py:53-63` 在 `mcp_enabled` 时使用未导入的 `ToolRegistry`，会触发 `NameError`。
- 即使补入导入，构造 `MCPServer(ToolRegistry, adapter)` 时未注入 `MainWindow/AppContext`；`mcp/adapter.py:42-49` 在 `_ctx is None` 时拒绝工具调用。
- `mcp_auth_token` 未从应用配置传入服务器；`mcp/server.py:27-36` 将在每次启动时生成随机 token。
- 当前测试未覆盖“应用启动 + 注入上下文 + 真实调用”的完整路径。

**修复建议**：明确 MCP 启动组合根，注入单一 AppContext；从安全存储读取稳定认证 token；新增启动/认证/有上下文调用的集成测试。

### P1-7：Windows 环境中 MCP 标准输入轮询不兼容

**静态高置信风险**。

- `mcp/server.py:45-50` 对 `sys.stdin` 使用 `select.select`。
- Windows 的 `select` 通常只支持 socket，标准输入可能抛出 `OSError`，导致 MCP 线程退出。

**修复建议**：改为阻塞 reader 线程 + queue + stop sentinel，或仅在 POSIX 使用 `select`；加入 Windows smoke test。

### P2-1：版本元数据漂移

- `pyproject.toml:3` 为 `0.1.1.1`。
- `src/transbridge/__init__.py:1` 为 `0.1.1.8`。

**建议**：建立单一版本源，在构建时生成运行时版本或统一从包元数据读取。

## 6. 需求、Plan 与工件一致性问题

### 6.1 需求状态已过期

1. `docs/requirements.md:1042-1145` 中 FR15、FR15.9、FR16 仍为“待方案”，而 `plans/INDEX.md`、源码与 changelog 已记录实现。
2. `docs/requirements.md:223` 对 FR7.13 同时描述“Phase 1 + 2 全部完成”与“Phase 2 待方案”。
3. `docs/requirements.md:480` 表示 FR9 Story 22 待编码，但 Plan 索引、工具描述代码和 Story 22 changelog 均显示已完成。
4. FR6、FR7 主体缺少 `### FR6` / `### FR7` 标题；FR9–FR16 被追加在系统边界/变更历史之后，机器扫描容易遗漏或误分类。

### 6.2 Plan 与 Story 状态已过期

1. `plans/fomod-translation/plan.md` 与 `plans/agent-infra-tools/plan.md` 仍写“已确认”，Story 勾选框也未更新，但索引与实现显示完成。
2. `plans/agent-upgrade/plan.md` 仍保留“Phase 2 待实现”，Story 08/12 的状态与验收项未勾选，而源码已有对应包。
3. `plans/ai-translation/stories/story-10-action-rule-editor.md` 至 Story 14 仍写待编码，但 `_rule_editor_widget.py`、`_mixed_worker.py`、`ai_translator_window.py` 与 changelog 表明已落地。
4. `plans/llm-chat/stories/story-10-toolresult-observation.md` 仍写待编码，但 `tool_execution_handler.py`、`conversation_manager.py`、`chat_widget.py` 已形成 ToolResult observation 链路。

### 6.3 Story 工件与链接不完整

1. `plans/INDEX.md` 指向 `ui-workbench/stories/story-18-layout-simplify.md`，文件不存在。
2. `session-controller`、`session-manager`、`task-monitor` 的 Plan 链接独立 Story 文件，但相关目录不存在；可能使用了 plan 内联 Story，需显式标注并修正索引。
3. `ui-workbench` 声称 22 个 Story，但仅部分 Story 有独立文件；`vector-term-retrieval` 无 stories 目录。
4. `plans/INDEX.md` 的 Agent Tool Expansion 评审纪要使用 `../../docs/council-review-fr9-tool-allocation.md`；从 `plans/INDEX.md` 解析会越出项目目录，疑似应为 `../docs/...`。
5. Agent Tool Expansion 声称 26 个 Story，且 `story-26-checkpoint-pause.md` 实际存在，但 `plans/INDEX.md` 的 Story 链接只列到 Story 25。

### 6.4 可追溯性缺口

项目缺少可机器校验的矩阵：

`需求条目 → ADR → Epic/Plan → Story → 源码模块/符号 → changelog → 测试用例/报告`

因此目前只能依据分散文档与源码推断状态，无法证明每条需求完成了验收闭环。

## 7. 分阶段整改路线

### 阶段 A：发布与安全阻断（最高优先级）

1. 统一 `transbridge` 包导入规范，修复 console script，并完成 wheel/Windows 成品安装启动 smoke test。
2. 修复归档应用层边界校验、资源限额，锁定安全依赖版本并运行恶意归档测试。
3. 修复 Agent parser 适配层与成功解析 fixture 测试。
4. 移除 token 日志，制定凭据存储迁移方案。
5. 显式声明并验证 py7zr/rarfile 及 RAR 后端发布策略。

**建议 skill 顺序**：`/bm-dev`（修复）→ `/bm-qa`（安全、安装、解析回归）→ `/bm-chronicle`（记录）。

### 阶段 B：Agent/MCP 可用性

1. 定义可授权根目录的路径策略，兼容 Windows 绝对路径。
2. 修复 MCP 的 import、AppContext 注入、认证 token 生命周期和 Windows stdin 实现。
3. 增加启动、认证、工具调用和 Windows 平台测试。

**建议 skill 顺序**：`/bm-arch`（涉及安全边界和跨模块契约）→ `/bm-plan` → `/bm-dev` → `/bm-qa`。

### 阶段 C：文档与流程治理

1. 执行 `/bm-init` 补齐 `.Codex/bm_config/paths.json`。
2. 用 `/bm-analyze` 规范需求标题、编号和完成状态，尤其回写 FR7.13、FR9、FR15、FR15.9、FR16。
3. 用 `/bm-plan` 或 `/bm-story` 补齐/修复 Story 文件、链接和完成状态；若使用 plan 内联 Story，显式标记此约定。
4. 建立需求可追溯矩阵，并由 `/bm-qa` 在每次发布前更新。
5. 使用 `/bm-chronicle` 为每次状态回写和代码修复创建增量记录。

## 8. 建议验收清单

- [ ] 恶意 zip/7z/rar 不能写出目标目录，且触发资源限额时可控失败。
- [ ] 新建隔离环境安装 wheel 后，`import transbridge`、`import transbridge.ui.app` 与 `transbridge --help` 均正常。
- [ ] Agent 可成功解析 ESP、EET、XT、SST 四类 fixture，并正确返回结构化错误。
- [ ] 日志、异常和 UI 不包含完整 token；旧明文 token 可安全迁移或明确提示。
- [ ] zip、7z、rar FOMOD 在干净环境中均按预期成功或给出可操作错误。
- [ ] Agent 接受用户授权目录内的绝对 Windows 路径，拒绝越界与危险路径。
- [ ] MCP 在 Windows 启动、认证、注入 AppContext 后可调用至少一个只读工具与一个受策略保护的写工具。
- [ ] `requirements.md`、`plans/INDEX.md`、各 Plan、Story、changelog 和测试报告的状态一致。
- [ ] 需求可追溯矩阵覆盖所有 FR/NFR，包含对应测试和最近验收记录。

## 9. 结论

TransBridge 已具备广泛的功能实现基础，尤其 FR15/FR16 的 FOMOD、翻译记忆和通用工具并非“待方案”，而是已有代码与历史产物的已实现领域。当前最大风险不在于功能目录缺失，而在于：安全边界、Agent/MCP 真实可用性、发布可安装性，以及文档基线滞后导致的错误决策。

在完成阶段 A 的 P0/P1 整改并以真实测试验证前，本项目不建议标记为“可发布”。其中归档路径穿越目前应视为待恶意样本验证的 P1 风险，不再作为已确认 P0；发行包入口与导入体系是当前唯一的 P0 级发布阻断候选。
