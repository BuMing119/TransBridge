# TransBridge 横向质量、安全、性能与发布审查

- 审查日期：2026-08-18
- 审查角色：质量 / 安全 / 性能 / 发布负责人
- 审查范围：`docs/requirements.md` 的 NFR1–NFR6，以及会影响 FR1–FR16 用户成功链的测试、错误处理、资源生命周期、平台兼容和发行配置
- 审查方式：只读静态核对需求、源码、测试、构建配置和历史测试记录
- 明确边界：遵照任务要求，本次未运行测试、未构建 wheel/PyInstaller、未启动 GUI/MCP、未访问真实 LLM/ParaTranz，也未修改业务代码、需求或 Plan

## 1. 结论

当前仓库具备较多自动化测试代码，但尚不能用这些测试证明“需求已完成”或“可发布”。静态统计显示 `tests/` 下有 56 个 Python 文件、约 615 个 `test_*` 定义；然而测试高度集中于 Smart Assistant 的局部对象行为和 mock 路径。核心用户成功链仍存在明显证据断层：

1. AI 翻译主流水线没有针对 `AutoTranslator.translate()` 的成功、并发、失败隔离、暂停/取消和 checkpoint 恢复测试。
2. SST 的 SSU8/SSU9 真实 fixture 虽存在，但当前 pytest 测试没有调用 SST parser/serializer；历史手工报告不可由当前测试套件重放。
3. FOMOD 测试没有执行完整 `FomodPipeline.run()`，无法证明解包、迁移、AI、写回、过滤、打包和失败报告的端到端正确性。
4. Agent parser 测试只覆盖空路径、文件不存在、扩展名错误，没有真实 ESP/EET/XT/SST 成功路径。
5. MCP 测试直接调用内部认证/工具列表方法，没有应用启动、stdio、AppContext 注入、Windows 停止与真实工具调用链。
6. 没有 wheel 隔离安装、console script、PyInstaller 成品、Windows 10/11、升级安装或卸载保留数据测试。
7. NFR1 的 30 秒解析目标没有固定数据集和基准；没有大文件、资源占用、并发竞争或 UI 事件循环延迟门禁。
8. 仓库未发现 CI 工作流、覆盖率阈值或发布门禁配置。历史报告的“全绿”结论无法自动约束后续提交。

发布判断：**当前应视为“功能实现广、质量证据不足，且存在数项高优先级可靠性/安全/发行风险”，不建议标记为 release-ready。**

## 2. 需求与测试证据矩阵

| 需求域 | 现有正向证据 | 关键证据缺口 | 判断 |
|---|---|---|---|
| FR1 解析（ESP/EET/XT/SST） | EET/XML 单元测试；PluginParser mock 测试；一个真实 ESP smoke | 真实 ESP 测试可 skip；没有 ESM/ESL/ESL-flagged、Strings、DSD JSON 成品矩阵；SST fixture 未被 pytest 使用；没有 Agent 入口成功解析 | 部分证明 |
| FR2 核心模型/集合 | TranslationEntry/Collection 单元测试较多 | 没有大集合并发读写、跨格式同键不变量、迁移后写回一致性属性测试 | 基础证明 |
| FR3 ParaTranz | workflow/API 大量 mock 测试；存在手工联机脚本 | 没有受控 sandbox 契约测试；429/5xx/超时/断网/重复提交幂等性不足；凭据泄露回归缺失 | 部分证明 |
| FR4 写回 | ESP/EET/XT XML writer 单元测试 | PluginWriter 多为 mock；没有 parse→edit→write→reparse 真实文件往返；没有 ESL/localised 组合矩阵；没有中途失败的原文件保护 | 部分证明 |
| FR5 AI 翻译 | embedding client/index 有 10 个左右针对性测试 | 主 `AutoTranslator` 三轮翻译、并发、遗漏拆分、流式写回、暂停/停止、断点恢复几乎无测试 | 未证明成功链 |
| FR6 后处理/报告 | 参数、配置等价、报告模型、任务管理测试较多 | 缺少真实 LLM 合同、完整阶段链、checkpoint 损坏/恢复、Excel 成品打开验证和并发故障注入 | 部分证明 |
| FR7/FR9–FR14 Smart Assistant | controller、session、registry、tool、UI 小组件测试最集中 | 大量为 FakeRegistry/MockAppContext；缺真实应用 composition root、真实 parser/writer 成功链、MCP stdio 和长会话资源回收 | 局部证明 |
| FR8 项目/版本持久化 | 部分 persistence/session 单元测试与原子写工具 | 缺项目导入导出 E2E、覆盖已有项目回滚、崩溃恢复、旧 schema 迁移、并发 autosave | 部分证明 |
| FR15 FOMOD | XML/过滤器的少量单元测试；`PipelineResult` 序列化测试 | `FomodPipeline.run()` 未被端到端执行；无 7z/RAR、真实插件、取消、错误报告、临时目录清理、可安装成品验证 | 未证明成功链 |
| FR16 通用文件工具 | ZIP 正常往返/选择提取、diff、filter、key migrate 测试 | 无恶意归档、zip bomb、符号链接、UNC、目录前缀碰撞；无 7z/RAR 测试；无 GB 级选择提取与资源预算 | 部分证明 |
| NFR1 性能 | 代码中有线程池、QThread、缓存 | 无固定规模/机器基线；无 30 秒断言、内存/磁盘/句柄预算、UI 卡顿测量 | 未证明 |
| NFR2 可靠性 | 存在 retry、checkpoint、任务隔离结构 | 核心翻译 checkpoint 非原子；无 kill/restart、断网、半写、竞争测试；FOMOD 静默吞错 | 未证明 |
| NFR3 兼容性 | 代码目标为 Python 3.12/PyQt6/Windows | 无 Windows 10/11 矩阵；无冻结环境/非 ASCII 路径/长路径；格式组合矩阵不全 | 未证明 |
| NFR4 安全性 | Agent 有权限、输入、输出护栏；部分路径拒绝测试 | ParaTranz 直接打印 token；明文配置；归档无资源配额；没有 secrets scan/dependency audit/恶意 fixture 门禁 | 不通过 |
| NFR5 可扩展性 | 包结构、registry、factory 已存在 | 没有 provider/parser/writer 插件合同测试；扩展仍依赖 UI/分派条件修改 | 部分证明 |
| NFR6 打包分发 | 有 `transbridge.spec`、`build.bat`、Inno Setup | 需求写 onefile，实际 spec 是 onedir；CLI/导入风险；7z/RAR 依赖与 unrar 未随包声明；无安装冒烟 | 不通过 |

## 3. 高优先级风险

### Q-01 [P0 发布阻断] 发行入口与包导入契约没有任何安装测试

证据：

- `pyproject.toml:30-31` 将命令指向 `transbridge:main`，而 `src/transbridge/__init__.py` 只定义 `__version__`。
- `src/transbridge/main.py:1` 导入 `src.transbridge.ui.app`；项目大量源码同样使用 `src.transbridge...`。标准 src-layout wheel 通常暴露 `transbridge`，不保证顶层 `src` 可导入。
- `pyproject.toml:59-67` 只配置 pytest 路径和 marker，没有 wheel 安装测试。
- 仓库未发现 CI workflow。

影响：源码目录中测试通过也不能证明用户安装后的 `transbridge` 命令或模块导入可用；这是发布成品的第一跳。

建议 Story：`release-hardening / S01-wheel-install-contract`。

验收标准：

- 在全新虚拟环境从构建出的 wheel 安装，不使用仓库 `pythonpath`。
- `python -c "import transbridge"`、`python -c "import transbridge.ui.app"` 成功。
- console script 能启动到可观察入口；桌面 GUI 可提供 `--smoke-test` 或等价无交互启动探针。
- wheel 内不依赖顶层 `src` 包；源码导入规范由静态检查阻止回归。

### Q-02 [P0/P1 发布阻断] FR15/FR16 声明支持 7z/RAR，但依赖和 RAR 后端没有形成可分发闭环

证据：

- `src/transbridge/fileops/archive.py:103-126` 在运行时才 import `py7zr`/`rarfile`。
- `pyproject.toml:10-28` 未声明 `py7zr`、`rarfile`。
- `src/transbridge/fileops/archive.py:23-41` 要求 `unrar.exe`，但仓库中 `src/transbridge/fileops/bin/unrar.exe` 与同目录 `unrar.exe` 均不存在。
- `transbridge.spec:19-45` 没有收集 unrar 或这两个归档实现。
- `tests/test_fileops.py:45-73` 只验证 ZIP；没有 7z/RAR 测试。

影响：干净安装或冻结成品中的 7z/RAR 路径很可能直接失败，与 FR16.1 和 FOMOD 打包承诺冲突。

建议 Story：`release-hardening / S02-archive-runtime-bundle`。

验收标准：

- wheel 与 Windows 成品分别验证 ZIP、7z 解包/打包；RAR 若承诺支持，必须验证捆绑后端和许可证 NOTICE。
- 缺后端时给出可操作错误，不能到流程中段才失败。
- SBOM/依赖清单能定位实际 `py7zr`/`rarfile` 版本。

### Q-03 [P1 可靠性] FOMOD 流水线吞掉关键失败并仍生成“成功”成品

证据：

- `src/transbridge/fomod/pipeline.py:157-168` 捕获 AI 翻译的所有异常后仅返回 0。
- `src/transbridge/fomod/pipeline.py:170-183` 捕获写回的所有异常并 `pass`，随后继续组装、打包。
- `src/transbridge/fomod/pipeline.py:91-100` 无论翻译/写回是否部分失败，仍生成归档并返回结果。
- `src/transbridge/ui/tools/fomod/fomod_panel.py:231-244` 对任何返回结果展示“翻译完成”，结果模型没有 `failed_plugins`、`errors`、`status`。
- `tests/test_fomod_pipeline.py:15-47` 只有扩展名、无配置时 AI 返回 0、结果转 dict 三项测试；没有调用 `run()`。

影响：用户可能得到一个看似成功、实际未翻译或未写回的安装包；静默数据质量失败比明确失败更危险。

设计建议：流水线返回强类型阶段结果，状态至少包含 `SUCCESS/PARTIAL/FAILED/CANCELLED`、每插件错误、已完成阶段和成品是否可发布；写回采用 staging + 验证 + 原子替换。默认策略应为关键写回失败则不产“正式成品”，允许用户显式选择导出诊断包。

建议 Story：`fomod-translation / S05-transactional-pipeline-outcome`。

验收标准：

- 注入解析、词典、LLM、写回、打包各阶段失败，结果精确标记阶段和插件。
- 任一插件写回失败时 UI 不显示全量成功。
- 失败前的输入归档/插件字节不变；临时输出不被当作最终成品。
- 部分成功策略有明确配置和报告。

### Q-04 [P1 安全/可用性] 统一归档接口缺应用层资源配额与一致成员校验

证据：

- `src/transbridge/fileops/archive.py:77-100` ZIP 只做字符串 `startswith()` 边界检查，没有成员数、单文件大小、累计大小、压缩比和链接策略。
- `src/transbridge/fileops/archive.py:103-119` 7z 直接 `extract/extractall`；`122-142` RAR 直接逐项 extract，没有统一应用层成员策略。
- 归档能力已暴露给 Agent，非可信输入面扩大。
- `tests/test_fileops.py:45-73` 只有正常 ZIP 和不支持扩展名测试。

影响：即使底层库能清理典型 `..`，仍可能遭遇目录前缀边界、绝对/盘符/UNC、链接逃逸、超大解压、海量小文件和压缩炸弹资源耗尽。

建议 Story：`agent-infra-tools / S06-safe-archive-policy`。

验收标准：

- 三种格式共享 `ArchivePolicy`：最大成员数、单文件字节、累计字节、压缩比、路径深度、是否允许链接。
- 所有成员规范化后必须 `relative_to(dest.resolve())`；拒绝绝对路径、盘符相对、UNC、设备路径和链接越界。
- 恶意 fixture 覆盖 ZIP/7z/RAR；失败后目标目录不留越界或半成品文件。
- 选择性提取在大归档中只消耗被选成员预算。

### Q-05 [P1 安全] 凭据在配置和错误路径中缺少端到端保护

证据：

- `src/transbridge/paratranz/paratranz_client.py:87-92` 与 `139-142` 在 401 时直接打印完整 token。
- `src/transbridge/config/llm.py:166-213` 明确将 LLM、Embedding、MCP 等配置写入 INI；注释承认 API Key 明文。
- `src/transbridge/config/paratranz.py:61-77` 将 ParaTranz token 写入同一 INI。
- `src/transbridge/smart_assistant/guardrails/output_validator.py:9-19` 有脱敏能力，但 ParaTranz 客户端的 `print()` 不经过工具输出护栏。
- 当前 tests 中找不到 token redaction/sanitize 的直接回归测试。

影响：控制台、日志截屏、共享配置、备份或诊断包可能泄露密钥；MCP token 与 API key 共存同一明文配置进一步扩大影响面。

建议 Story：`security-hardening / S01-credential-store-and-redaction`。

验收标准：

- Windows Credential Manager/keyring 保存秘密，INI 只保存引用或非敏感配置。
- 从旧 INI 一次性迁移，迁移成功后清除明文；失败可回滚且有提示。
- 所有异常、日志、ToolResult、观测文件和报告对 canary secrets 脱敏。
- 自动 secret scan 检查仓库与构建产物；401 测试断言 stdout/stderr/log 不含原 token。

### Q-06 [P1 可靠性] AI 翻译与后处理 checkpoint 非原子，损坏后静默丢弃恢复状态

证据：

- `src/transbridge/ai_translator/translator.py:82-110` 直接以 `"w"` 覆盖 checkpoint；解析异常返回 `None`。
- `src/transbridge/ai_translator/post_processor/checkpoint.py:45-83` 同样直接覆盖，任何加载异常都返回 `None`。
- 项目持久化已经有可复用的原子写实现：`src/transbridge/persistence/_utils.py:8-16`。
- tests 中没有 `ProgressCheckpoint`/`PostProcessCheckpoint` 测试。

影响：进程终止、磁盘满、杀毒软件占用或断电可能留下半截 JSON；下一次启动把损坏文件当作“无 checkpoint”，从头重跑并可能重复计费、重复写回。

设计建议：统一 `DurableCheckpointStore`，采用 temp + fsync + replace、schema/version、输入集合 fingerprint、写入序号和 `.corrupt` 保留；恢复操作必须幂等。

建议 Story：`ai-translation / S15-durable-idempotent-checkpoint`，同时让后处理和 Graph checkpoint 复用。

验收标准：

- 在写入前/中/replace 前后故障注入，始终可加载旧版或新版完整 checkpoint。
- checkpoint 损坏不静默从头执行：隔离损坏文件、提示用户并提供安全重建选项。
- 输入文件、目标范围、配置 fingerprint 不匹配时拒绝误恢复。
- 完成批次恢复后不重复调用 LLM、不重复累计统计、不覆盖锁定条目。

### Q-07 [P1 质量证据] AI 翻译 NFR2 的主成功链没有测试

证据：

- `src/transbridge/ai_translator/translator.py:528-615` 有多轮、多层 ThreadPoolExecutor 并发。
- `translator.py:424-525` 每批保存 checkpoint，`705` 以后有遗漏拆分和流式增量写回。
- `tests/ai_translator/test_term_vector_index.py:68-176` 只覆盖向量索引；全 tests 搜索不到 `AutoTranslator`、`ProgressCheckpoint` 或 `_run_batch` 的测试。

影响：NFR2 的断点续传、错误隔离和递归重试全部只是代码存在，不能证明在并发和取消时保持 exactly-once/at-least-once 的明确语义。

建议 Story：`ai-translation / S16-translation-chaos-contract-tests`。

验收标准：

- 模拟某批 429、超时、流中断、畸形 JSON、重复输出、遗漏 ID，验证其他批继续。
- 并发数始终不超过配置；共享 collection、动态术语和计数无重复/丢失。
- pause/stop 在有流式连接时于预算内生效，并生成可恢复 checkpoint。
- 重试有最大深度/总尝试/总耗时预算，失败条目有稳定错误码。

### Q-08 [P1 兼容性] 当前测试无法证明真实格式矩阵，SST 历史结论不可重放

证据：

- `tests/trans_exe/xt/` 下有 SSU8/SSU9 fixture，但 pytest 测试代码没有引用 `SST_Parser`、`SST_Serializer`、`SSU8` 或 `SSU9`。
- `tests/parser/test_plugin_parser_integration.py:19-22` 的唯一真实 ESP smoke 在 fixture 缺失时允许 skip。
- tests 中没有 ESM、真实 ESL、ESL-flagged、localised Strings、非 ASCII 路径和长路径组合。
- `docs/test-reports/sst-full-suite.md:5-43` 记录过手工验证，但对应断言不在当前自动套件中；同报告 `61-68` 一方面称无已知限制，另一方面写明 serializer 不支持 SSU8。

影响：格式实现的“已完成”依赖某次人工运行，无法防止后续回归；Windows 文件格式兼容承诺未被持续验证。

建议 Story：`file-parsing / S12-format-corpus-and-roundtrip`。

验收标准：

- 建立小型、可公开分发的 golden corpus：ESP/ESM/ESL/ESL-flagged、inline/localised、EET、XT XML、SSU8/SSU9、DSD JSON、Strings。
- 每个格式执行 parse→统一模型→write→reparse；校验 key、原文、译文、stage、上下文和未修改二进制区域。
- fixture 必需，核心格式测试禁止 skip；受许可证限制的大样本放入可校验的外部测试数据包并用 hash 固定版本。
- 对截断、非法长度、编码错误和未知子记录做 fuzz/property 测试。

### Q-09 [P1 Windows/集成] MCP 只有内部方法测试，应用组合根与 stdio 生命周期未被证明

证据：

- `src/transbridge/ui/app.py:53-63` 条件启动 MCP；`ToolRegistry` 在此作用域未导入，且构造 server 时未注入应用 `ctx`。
- `src/transbridge/smart_assistant/mcp/server.py:43-50` 对 `sys.stdin` 使用 `select.select()`；Windows 通常只对 socket 支持 select。
- `tests/smart_assistant/test_mcp.py:14-82` 只直接测试 `_authenticate()` 和 adapter/registry 查询，没有调用 `run_stdio()` 或应用入口。

影响：认证单元测试通过并不代表 Windows 上服务能启动、停止或操作当前项目；这是典型“组件绿、组合根红”。

建议 Story：`agent-upgrade / S13-mcp-windows-composition-root`。

验收标准：

- 应用只创建一个真实 AppContext，并注入 registry/adapter/server。
- Windows 10/11 上通过子进程 stdio 完成 initialize/list/call/stop；关闭应用无遗留线程。
- token 可稳定配置且不打印秘密；未授权请求不执行工具。
- 切换项目后的 ctx 行为有明确策略和测试。

### Q-10 [P1 性能/资源] NFR1 没有可执行预算，且仍有明确同步 UI I/O 与临时目录泄漏

证据：

- `docs/requirements.md:421-425` 只定义“中等规模 MOD ≤30 秒”“耗时操作后台线程”，没有定义中等规模、参考机器、内存或 P95。
- tests 中没有 benchmark、30 秒断言、内存或 UI heartbeat 测试。
- `src/transbridge/ui/main_window.py:1393-1417` 在 GUI 线程同步保存并压缩整个项目；`1420-1473` 在 GUI 线程同步校验并解压导入包。
- `src/transbridge/fomod/pipeline.py:62-69` 使用 `mkdtemp()` 创建工作目录，但 `run()` 没有 `finally` 清理。
- `src/transbridge/fileops/archive.py:103-119` 的 7z 路径没有 progress 回调；ZIP/RAR 进度按成员数而非字节，无法反映大文件进度。

影响：大项目导入导出可能冻结 UI；重复 FOMOD 任务会积累完整解包副本；当前 30 秒目标不可比较、不可作为门禁。

建议 Story：`quality-foundation / S03-performance-resource-budgets` 与 `project-persistence / S09-async-import-export`。

验收标准见第 6 节性能基线。

### Q-11 [P1 发行需求漂移] NFR6 要求单文件，实际设计明确为 onedir

证据：

- `docs/requirements.md:452-455` 要求 PyInstaller 单文件。
- `transbridge.spec:3-5` 明确“文件夹式（onedir）”，并使用 `COLLECT`（97-106）。
- `build.bat:41-49` 输出 `dist/TransBridge/`，随后由 Inno Setup 制作安装器。

影响：这不一定意味着 onedir 设计不好；对 torch、Qt、模型文件而言 onedir + installer 通常更适合启动性能和增量更新。但需求、架构和验收口径不一致，任何“满足 NFR6”的结论都不成立。

设计建议：先做 ADR，在 onefile 与 onedir+installer 中明确选择。建议将正式 Windows 交付物定义为“签名安装器 + onedir payload”，另提供便携包；不要为了旧文字强行 onefile。

建议 Story：`release-hardening / S03-distribution-adr-and-matrix`。

### Q-12 [P2 测试治理] 数量不能代替可信门禁，历史“已完成”证据存在漂移

证据：

- 静态统计约 615 个测试定义，但 `pyproject.toml:63-67` 声明 slow/integration/llm marker，实际只有一个 `@pytest.mark.integration`；slow/llm 未形成分层执行策略。
- `docs/test-reports/unit-test-staleness-qa-2026-08-13.md:4-11` 记录 516 passed/19 failed，并承认测试陈旧。
- `docs/test-reports/fr5.12-embedding-optimization-qa-2026-08-13.md:6-13` 又记录 550 passed。
- `docs/test-reports/translation-memory-granularity-refactor-qa-2026-08-14.md:27-32` 记录全量 28 failed + 50 errors，但模块 27/27 通过后仍称“零回归”。
- 当前测试定义数量又已超过这些历史总数，历史报告没有绑定 commit、环境锁、JUnit、coverage 或产物 hash。

影响：局部测试绿不能推出全量零回归；“预存失败”“沙箱问题”没有机器可读 quarantine，会长期弱化质量信号。

建议 Story：`quality-foundation / S01-reproducible-test-evidence`。

验收标准：每份 QA 证据包含 commit SHA、Python/Windows 版本、锁文件 hash、完整命令、JUnit、coverage、skip/xfail 列表和构建产物 hash；发布不得排除未登记失败。

## 4. 建议测试金字塔

### L0：静态与供应链（每次提交，目标 2–4 分钟）

- ruff format/lint、类型检查、导入契约检查。
- secret scan、依赖漏洞扫描、许可证/SBOM。
- `python -m build` 后检查 wheel 内容和元数据。
- 需求追溯校验：FR/NFR → Story → test id，不允许“已完成但无验收测试”。

### L1：纯单元/属性测试（每次提交，目标 5 分钟）

- TranslationEntry/Collection 不变量。
- parser/writer 的边界长度、编码、坏输入 property/fuzz。
- archive policy、路径规范化、过滤规则。
- checkpoint schema、原子写、损坏恢复和幂等。
- guardrail 全类型递归脱敏与权限矩阵。

### L2：组件合同测试（每次 PR，目标 10 分钟）

- 每种 parser/writer 遵循统一 `parse/write/reparse` contract。
- 每种 LLM provider 用 fake HTTP server 验证流式、取消、429/5xx、超时。
- ParaTranz 用本地契约 server；所有网络错误有稳定错误码。
- Agent ToolRegistry 使用真实 registry + 最小真实文件，而非只用 MockAppContext。

### L3：进程/桌面集成（Windows PR/nightly，目标 20 分钟）

- QApplication offscreen + 主窗口 composition root。
- MCP 子进程 stdio。
- FOMOD 小型真实归档 E2E。
- 项目创建、保存、关闭、重启恢复、版本切换、导入导出。
- 取消/退出时线程、句柄、临时目录无泄漏。

### L4：性能/故障/真实服务（nightly 或发布候选）

- 固定中/大型 corpus 基准。
- kill -9/TerminateProcess、磁盘满、权限拒绝、断网、慢流和并发压力。
- 可控测试账号的 ParaTranz smoke；LLM contract 仅做最小、限额、可重放验证。
- Windows 10/11 虚拟机中的安装、升级、卸载、首次启动和签名检查。

## 5. CI 与 Release Gates

### PR 必过门禁

1. L0/L1/L2 全绿；不允许无 issue/到期日的新增 skip/xfail。
2. changed-lines coverage ≥ 90%；核心 parser/writer/checkpoint/security 模块分支覆盖 ≥ 85%。全仓总覆盖率先建立基线，再逐迭代提升，避免为了数字写低价值测试。
3. wheel 必须构建并在隔离 venv 安装、import、启动 smoke。
4. 依赖高危漏洞为 0；secret scan 为 0；许可证策略通过。
5. 每个变更 Story 至少绑定一个用户成功路径和一个失败/边界测试。

### Windows nightly 门禁

1. Windows 10 与 Windows 11、Python 3.12 支持版本矩阵。
2. 完整 pytest + JUnit + coverage；禁止以“预存失败”文本豁免，必须使用有期限 quarantine 清单。
3. 真实格式 corpus roundtrip。
4. FOMOD ZIP/7z/RAR E2E、MCP stdio、GUI heartbeat、checkpoint kill/restart。
5. 性能基准与前一稳定基线比较：P95 回退 >10% 或内存回退 >15% 阻断。

### 发布候选门禁

1. wheel + PyInstaller/Inno 成品都在干净 VM 安装；成品 hash 与报告绑定。
2. 首次启动、非 ASCII 用户名/路径、长路径、无网络、无 API key、有旧配置升级均验证。
3. 安装/升级不覆盖用户项目和词典；卸载策略明确保留或提示清理数据。
4. SBOM、第三方 NOTICE、unrar 许可证、签名与杀毒误报 smoke 完成。
5. 所有 P0/P1 已关闭；若接受风险，必须有 owner、到期日、影响和回滚方案。

## 6. 可执行性能与资源基线

建议将 NFR1 从模糊描述改成可测 SLO。具体数字应在首轮基准后校准，以下可作为初始门禁：

| 场景 | 固定工作负载 | 初始预算 |
|---|---|---|
| ESP 解析中型 | 固定 50–100MB、约 10 万字符串 corpus；Windows 11、4 核/8GB 基准 VM | P95 ≤ 30s；峰值 RSS ≤ 1.0GB；UI heartbeat 间隔 ≤ 200ms |
| ESP 解析小型交互 | 固定 5–10MB corpus | P95 ≤ 3s；首个进度事件 ≤ 500ms |
| AI 并发 | 100 条、固定 fake streaming server、max_concurrent=3 | 活跃请求 ≤3；取消 P95 ≤1s；无重复写回；句柄回到基线 +5 内 |
| checkpoint | 10 万条状态/连续 100 次保存 | 单次 P95 ≤100ms；崩溃恢复 100%；文件永不出现半 JSON |
| FOMOD 选择提取 | 2GB 逻辑归档、仅选择 50MB 必需文件 | 实际写盘 ≤60MB；不展开未选资源；临时目录任务后为 0 |
| FOMOD 完整小包 | 固定 ZIP/7z/RAR corpus | 成品可重新解包；错误报告完整；取消后无正式成品 |
| 项目导入导出 | 500MB 上限附近的 `.transbridge` | UI heartbeat ≤200ms；可取消；峰值临时磁盘有明确预算 |
| 长会话 | 500 轮消息、100 次工具调用 | RSS 稳态增长 ≤15%；关闭会话后线程/对象可回收 |

测量规则：固定 CPU/内存/磁盘 VM、预热次数、重复至少 5 次、记录 P50/P95/max、RSS/句柄/磁盘/网络；基准 JSON 随 commit 保存，报告不得只写“感觉正常”。

## 7. 跨 Epic 质量迭代 Plan

### Epic A：quality-foundation（P0，先做）

- S01 可复现测试证据：CI、JUnit、coverage、commit/environment/artifact 绑定、quarantine 机制。
- S02 格式合同与 golden corpus：所有 parser/writer 的统一往返合同。
- S03 性能/资源基准：pytest-benchmark 或独立 harness、Windows 基准 VM、趋势报告。
- S04 故障注入框架：fake HTTP、磁盘错误、kill/restart、慢流、竞争屏障。

完成定义：没有这组基础设施，后续 Epic 的“QA 通过”不能进入发布基线。

### Epic B：release-hardening（P0）

- S01 wheel/CLI/import 契约。
- S02 7z/RAR/unrar 依赖与许可证闭环。
- S03 onedir/onefile ADR、版本单一来源、产物矩阵。
- S04 Windows 安装/升级/卸载/签名 smoke。

### Epic C：security-hardening（P1）

- S01 系统凭据库迁移 + 全通道脱敏。
- S02 ArchivePolicy + 恶意 corpus + 解压沙箱。
- S03 Agent 文件授权根模型：允许用户选择的 Windows 绝对路径，同时拒绝逃逸/UNC/设备路径。
- S04 供应链扫描、SBOM、依赖锁定。

### Epic D：ai-translation reliability（P1）

- S15 durable/idempotent checkpoint。
- S16 并发、暂停、取消、遗漏拆分、失败隔离 chaos contract。
- S17 统一重试预算与稳定错误模型。
- S18 结果提交协议：批次 staging、去重 token/fingerprint、统计一致性。

### Epic E：fomod-translation hardening（P1）

- S05 事务式流水线结果与失败策略。
- S06 可取消的阶段编排、字节级进度与临时目录 RAII 清理。
- S07 ZIP/7z/RAR + 真实插件 E2E 成品验证。
- S08 输出 manifest：输入 hash、阶段、继承/词典/AI/失败统计、工具版本。

### Epic F：agent-upgrade/MCP integration（P1）

- S13 Windows composition root/stdio E2E。
- S14 工具真实成功路径（ESP/EET/XT/SST parse + write）与权限确认矩阵。
- S15 长会话/线程/信号生命周期 soak test。

### Epic G：project-persistence（P1/P2）

- S09 `.transbridge` 导入导出后台化、进度、取消。
- S10 覆盖导入采用 staging + validate + atomic swap，禁止直接叠加到现有目录。
- S11 schema migration、损坏恢复、旧版升级 corpus。

## 8. 推荐实施顺序

1. 先完成 Epic A + Epic B 的最小骨架，让每次迭代都有可信门禁，并修复当前发行第一跳。
2. 同一里程碑处理 Q-03、Q-04、Q-05、Q-06；这些会造成静默错误、秘密泄露或不可恢复状态。
3. 建立真实格式 corpus 后，再宣称 FR1/FR4/FR15/FR16 完成；历史手工报告只作为线索，不作为持续验收。
4. 最后用 Windows nightly 和 release candidate 门禁收口 NFR1–NFR6，并回写需求状态与追溯矩阵。

## 9. 最终验收清单

- [ ] 核心成功链在干净 Windows 环境可自动重放，无人工预置文件和隐式开发路径。
- [ ] 所有核心格式 fixture 必需且不可静默 skip。
- [ ] AI 翻译并发/取消/重试/checkpoint 有故障注入和幂等证明。
- [ ] FOMOD 关键阶段失败不会显示全量成功，也不会发布半成品。
- [ ] 归档三格式有统一路径与资源策略、恶意 corpus 和依赖版本锁。
- [ ] stdout/stderr/log/ToolResult/trace/report/build artifact 不出现 canary secret。
- [ ] wheel、CLI、PyInstaller/Inno 成品、升级与卸载均有 hash 绑定的测试证据。
- [ ] NFR1 的时间、内存、磁盘、句柄、UI heartbeat 均有固定基准与趋势门禁。
- [ ] 测试报告包含 commit、环境、锁文件、命令、JUnit、coverage、skip/xfail 和产物 hash。
- [ ] requirements/ADR/Plan/Story/test/release evidence 可双向追溯。

综合判断：现有代码已经形成多个可用组件，但质量体系仍停留在“组件测试 + 静态 QA 报告”阶段。下一轮迭代最有价值的架构变化不是继续增加零散 mock 测试，而是建立统一合同、事务式结果、耐久 checkpoint、恶意/故障 corpus 和 Windows 发行门禁，使“已实现”升级为“可重复证明、可安全发布”。

## 10. ParaTranz JSON 专项质量门禁

根据用户提供的约 12MB/32,372 条样本结构，格式 corpus 需要新增 ParaTranz JSON 类别，并与 DSD/内部 JSON 分开统计。

最低门禁：

- 数值 id + 字符串 key 导入后按 key 建立 32,372 个稳定身份（若样本确有重复，则必须产生显式冲突统计）；
- 模拟平台重写全部 id 后再次导入，key/translation/stage digest 不变；
- 缺 key 必须失败，缺 id 必须可导入；
- 重复 key 不得静默 last-write-wins；
- 七级 Stage round-trip，未知 Stage 有稳定 error code；
- 离线文件导入与 fake ParaTranz HTTP 下载生成相同 ChangeSet；
- GUI/Agent/MCP 三入口 contract parity；
- 12MB 文件导入/导出记录时间、峰值内存和错误报告，不把“能 json.loads”当完整验收；
- 测试 fixture 使用脱敏小样本进入 PR，完整用户样本只用于本地/受控性能验证，不提交仓库。

该门禁应加入 `quality-foundation` 的格式合同与 golden corpus Story。详见 [专项调整](paratranz-json-compatibility-adjustment.md)。

---

## 整改回填（2026-08-18，Phase 6）

本报告为综合整改正式输入审查结论，保留历史判定与证据不改写。Phase 0～7 已完成，对应根因（R-xxx）由各 V2 Plan/Story 承接并通过 EvidenceManifest 与综合 QA；完整根因→Story→evidence 追踪见 [remediation-ledger](./remediation-ledger.md)，最终汇总见 [final-release-qa-2026-08-18](./final-release-qa-2026-08-18.md)。综合整改 V2 共 37/37 Story 实现完成并通过综合 QA；最终锁定 uv 门禁合计 1374 passed、5 skipped、0 failed。
