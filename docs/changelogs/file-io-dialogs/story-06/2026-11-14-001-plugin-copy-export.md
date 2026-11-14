# S06：原插件写回与同名副本导出

- 日期：2026-11-14（任务于 2026-11-13 开始）。
- Epic/Story：file-io-dialogs / S06；关联 [FR4.5/FR4.6](../../../requirements.md)、[实施计划](../../../../plans/file-io-dialogs/plan.md)及[来源路径 S11](../../project-storage-redesign/story-11-source-references/2026-11-13-001-persist-and-resolve-source-paths.md)。
- 用户授权：bm-pilot 继续修改导出流程；原位写回、目录复制后写入、保留文件名、多方案不重复选择来源。沿用已授权的 GPT-6.1 Sol/high 子 Agent，分别完成 UI、应用层和独立只读审查，主会话集成验证。
- 归属：与本轮开始时保存的完整工作树副本直接比较；不把此前路径存储、术语方案、批量崩溃修复或其他脏改动计入本轮。未提交 Git，未操作用户真实插件或工程。

## 用户行为

普通原格式插件导出提供“另存到目录”和“写回原插件”。默认另存，单插件只选目录，不填写或改动文件名；批量全插件可以写回各自已验证的原位置。原位目标来自本次成功绑定的真实绝对路径，不从历史路径提示直接获得覆盖许可。覆盖仍经确认和备份，来源/目标变化继续拒绝旧请求。

目录输出先把已确认来源字节复制到该目标的私有暂存文件，再应用译文、回读验证并原子发布。原插件保持不变；复制、翻译、验证或提交失败不留下正式半成品。每个普通术语方案从同一份已绑定快照生成自己的副本，结果位于“根目录/方案目录/原插件文件名”。即使手选的来源已经改名，也保留实际文件名。多方案显式套用的输出名使用模板插件名，单文件显式套用的既有自定义路径接口保留。

复用 S11 的路径自动验证、失效询问和同目录补齐。普通多方案共享一次来源绑定，原位覆盖确认重试也复用本次绑定。另存不能覆盖输入插件或其物理配套资源；同名输出冲突不自动改名。JSON/SST/EET/XML 保持各自格式与目标规则，格式切换不携带插件写回意图。取消覆盖后可以继续编辑选项；存在其他阻断错误时不先询问无意义的覆盖确认。

## 逐文件增量

以下除标注“新增”外均为修改；只列本轮职责，不代表文件全部 Git 差异。

- `src/transbridge/application/io/plugin_destination.py`（新增）：校验显式插件目的地；目录模式保留名称且拒绝覆盖来源/资源，原位模式要求匹配已绑定路径和既有快照指纹。不新增哈希方案。
- `src/transbridge/application/io/operation_write.py`：在现有输出指纹预检中调用目的地校验；请求 options 已由原摘要合同绑定。
- `src/transbridge/application/io/publish/validators.py`：只有插件目录导出才将复制标记传给私有暂存 renderer。
- `src/transbridge/application/io/legacy_adapters.py`：SsePluginAdapter 转发显式 copy_source 参数；普通调用默认不变。
- `src/transbridge/application/io/plugin_write.py`：应用译文前把冻结字节写入受控暂存副本，重用同字节来源的解析结果，不重复解析；无 inline 改动时保留副本原字节。取消、异常仍交由现有发布流程清理。
- `src/transbridge/ui/operations/write_destinations.py`（新增）：共享目的地字段、已绑定来源路径、原文件名与 retarget 规则，引用应用层的选项键。
- `src/transbridge/ui/operations/write_plan.py`：单插件模式和目录字段、显式 legacy/transfer 兼容；单文件目的地决策留在计划边界。
- `src/transbridge/ui/operations/write_batch.py`：批量全插件原位模式、目录同名输出、格式切换清除插件模式。
- `src/transbridge/ui/operations/write_feature.py`：接入单/批目的地与编辑字段，复用现有预检/任务流程。
- `src/transbridge/ui/operations/write_json_export.py`：清除插件目的地选项，避免 JSON 继承原插件目标；保留返回原生格式所需字段。
- `src/transbridge/ui/operations/write_dialog.py`：普通插件展示目的地选择和目录，不展示可编辑文件名；原位/多方案隐藏无关目录字段，取消或阻断时恢复可编辑状态。
- `src/transbridge/ui/operations/write_scheme_fields.py`：多方案控件与目的地统一显隐；移除表单标签时先隐藏，再延迟销毁。
- `src/transbridge/ui/operations/write_schemes.py`：普通方案按已绑定插件名分别 retarget 为目录副本；显式套用使用模板文件名和副本模式，保留独立方案投影。
- `src/transbridge/ui/operations/facade.py`：同次覆盖确认复用输入时同时核对目的地模式和方案目录。
- `tests/contracts/io/test_plugin_export_destinations.py`（新增）：16 项实际插件回归，覆盖每份副本的初始字节、源文件变化、复制/翻译失败清理、名称/别名、原位变化保护及 localized loose/BSA 原子性。
- `tests/ui/operations/test_export_flow_source_reuse.py`（新增）：4 项完整 Qt/SQLite 集成，覆盖工程重开、来源移动/改名、一次手选补齐多方案，以及原位覆盖确认不重新绑定。
- `tests/ui/operations/test_write_save_as.py`：目的地与原位、消失来源、目录选择和格式回切、拒绝覆盖后的编辑、相同来源目录阻断；测试按钮从当前输出行定位。
- `tests/ui/operations/test_write_schemes.py`：多方案真实译文与同名输出；显式套用忽略原先自定义输出名。
- `tests/ui/operations/test_write_projection_responsiveness.py`：按新目录字段操作，继续验证投影不阻塞 GUI。
- `tests/ui/operations/test_plugin_transfer_ui.py`、`test_target_plugin_workflow.py`：保留显式套用并验证清空模板回到目录模式，不遗留旧目标。
- `docs/requirements.md`：FR4.5/FR4.6 明确原位、同名复制和普通多方案来源复用。
- `plans/file-io-dialogs/plan.md`：修正当前目的地合同并增加 S06 验收/交付记录。
- `plans/terminology-localization-profiles/plan.md`：S16 的普通插件导出与本轮 S06 对齐。
- `docs/terminology-workflow.md`：说明路径自动定位、同目录补齐、方案副本和普通目的地选择。
- `plans/INDEX.md`、`docs/changelogs/INDEX.md`：仅补充本轮完成状态和记录入口。

职责审查：write_feature 保持 488 行，新目的地逻辑拆到 56 行的 write_destinations 及 160 行的 write_plan；write_dialog 为 452 行。已有 operation_write（516 行）只增加委派校验，legacy_adapters（757 行）只修改既有插件接口的参数转发，没有增加新职责；未来扩展 adapter 职责前需拆分，当前小型接线不为行数引入额外迁移。复制逻辑仍在既有插件 renderer，未另造发布引擎。

## 最终验证

Windows / Python 3.12.12，沿用 uv 环境和锁文件。使用 QT_QPA_PLATFORM=offscreen；UV_CACHE_DIR、TEMP、TMP、pytest basetemp 都限定在本任务 `.tmp-export-flow` 下。SQLite 原子建项需要 Windows 硬链接，因此最终测试在获准的沙箱外隔离进程执行。

```powershell
uv run --offline --no-sync pytest tests/contracts/io tests/integration/publish tests/application/fomod/test_plugin_publishing.py tests/smart_assistant/tools/test_safe_write_back.py tests/smart_assistant/tools/test_write_back_publication_boundary.py tests/smart_assistant/tools/test_write_back_source_scope.py tests/smart_assistant/tools/test_write_back_options.py -q -p no:cacheprovider --basetemp=.tmp-export-flow/core-final
```

925 passed、1 skipped、7 warnings，78.39 秒，退出码 0。跳过的是 test_atomic_publish 的符号链接创建能力检查；当前 Windows 测试环境不能创建该链接。warnings 为已有 SWIG 和集合兼容接口弃用提示。

```powershell
uv run --offline --no-sync pytest tests/ui/operations tests/ui/test_project_source_hydration.py tests/integration/bootstrap/test_project_source_reference_lifecycle.py tests/integration/bootstrap/test_pending_source_references.py -q -p no:cacheprovider --basetemp=.tmp-export-flow/ui-confirmation
uv run --offline --no-sync ruff check src tests
uv run --offline --no-sync ruff format --check src tests
```

540 passed、3 warnings，20.39 秒，退出码 0；全仓 Ruff 通过，2656 文件格式检查通过。两个最终批次共 1465 个通过节点，不把子 Agent 的重叠聚焦批次再相加。独立只读审查未发现普通插件流程本轮新增的可达缺陷。

视觉检查：实际创建 Qt 窗口，加载 Microsoft YaHei 字体，截取另存、原位和多方案三种状态；主会话查看截图，580 像素宽度下控件无重叠或被遮挡，三个状态只显示对应目的地输入。没有操作真实桌面或宣称完成发行包人工验收。

早期失败与处理：

- 新原位窗口测试在旧代码因缺少 destination_mode 字段失败，改后完整 4 项集成通过。
- 初次完整 UI 为 538 passed、1 failed：清空显式模板的旧测试仍要求 target_path，新流程应回到空目录与 directory 模式；修正验收断言后最终 540 passed。
- 子 Agent 早期 UI 合跑曾在测试遍历 findChildren 按钮时遇到 Qt 原生访问异常。测试改为从当前有效输出行取得按钮后，聚焦合跑 113 passed，主会话完整 UI 两轮均未发生原生异常；未捕获该异常的完整原生栈，不将此观察宣称为用户先前崩溃的新增根因或修复证据。
- 子 Agent 首次广域回归的 4 项工程建项因沙箱 WinError 5 无法建立硬链接；获准后 4 项复跑通过，且包含在最终核心完整批次中。

## 保留边界

普通多方案来源绑定复用已验证。显式跨插件套用仍保留 FR4.1 的逐方案模板预检及文件租约：模板若在方案预检之间被外部修改，先前捕获的方案会被租约拒绝，后续方案可能绑定较新模板。此行为在本轮基线已存在，未扩展为跨方案统一模板事务；不会取消已有文件变化保护。

未运行全仓 pytest、发行构建、真实 LLM/API 或用户整批文件的桌面人工导出。没有新增数据库迁移或插件持久缓存。工作树其他既有改动保留，基线副本、测试产物、日志和截图在核验归属后清理。
