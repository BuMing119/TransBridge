# 助手工具复用桌面功能

S1 至 S5 已完成，最终验收覆盖 9 个命名空间、63 个注册工具及其 138 个顶层参数 · 2026-11-22 · 原始核查基线 `12b9d7da`（0.2.1.15）

助手只开放软件正在使用的业务操作。工具负责参数、权限、确认、取消和本轮撤销；筛选、匹配、覆盖、提交和任务执行走桌面正在调用的接口。规则仍在 Qt 控件里时，从原调用点提取，并让桌面和助手同时调用。后台任务沿用现有执行器。依据为[现行需求](../../docs/requirements.md)、[助手分层决策](../../docs/adr/008-smart-assistant-code-layering.md)和[助手撤销决策](../../docs/adr/043-assistant-turn-results-and-undo.md)。

S1 已完成三项工具下线、逐词条上传与关联恢复清理、失效提示与参数清理。助手的文件比较和上传已接入共享文件流程。参见[删减记录](../../docs/changelogs/assistant-shared-business-tools/story-01/2026-11-22-001-retire-assistant-tools.md)。工作台筛选与编辑见 S2，工程绑定、翻译任务执行和文件写回分别见 S3 至 S5。

## 复核后的删减清单

67 个注册工具的原始核查结果为 26 个直接复用、37 个存在业务分叉、4 个没有独立桌面操作。这是核查前的实现分类。整改按具体操作决定去留，同一工具可保留现役业务并删除其中的旧模式。

- **删除 `recover_upload_associations` 和旧上传链。** 桌面普通上传和分类上传都已改用文件 API。整改前助手按词条上传、按本地保存的远端 ID 覆盖、写回逐条关联，并提示用户拿旧回执补关联。S1/S3 一并删除这条执行链、恢复工具和提示，撤销上版方案的“保留旧上传回执查询”专项要求。不给旧回执加按钮、工具、别名或重放路径。桌面现有任务历史仍可按原行为展示已有文件。依据见[上传决策](../../docs/adr/047-opt-in-categorized-project-upload.md)、[助手文件上传入口](../../src/transbridge/smart_assistant/tools/tool_paratranz.py)和[桌面历史服务](../../src/transbridge/application/tasks/history_service.py)。
- **删除两项没有对应桌面操作的助手工具。** 原 `migrate_entries` 一直返回 `NOT_AVAILABLE`，删注册和“按 key 迁移”的说明。工程更新来源、导入译文和 FOMOD 迁移各有真实调用，继续走各自入口。`plan_sync` 提供助手自己的整项目、含双向的规划，撤下这个工具。工作台的文件上传差异、项目下载预览以及有其他调用方的同步服务各按原职责使用。依据见[保留的词典工具](../../src/transbridge/smart_assistant/tools/tool_migrator.py)、[工程更新来源](../../src/transbridge/application/projects/source_update.py)和[同步范围约定](../../docs/adr/047-opt-in-categorized-project-upload.md)。
- **删除助手仍在宣传的失效能力。** FR7.13.3 的旧长期记忆于 2026-09-13 废除，新长期记忆 FR31 仍为草稿；删系统提示中“可跨会话记住信息”的承诺。会话记录与当前请求上下文照常使用。翻译 Agent 指定的“术语辅助翻译”预设原先把 `lookup_terms`、`translate_entries`、`check_quality` 写在依赖、允许列表和指令中，三个名称在 67 个工具中均不存在。移除这套旧调用说明；预设中有当前翻译流程支持的意图，直接交给现行翻译任务及其术语处理，其余步骤删去。依据见[助手提示](../../src/transbridge/smart_assistant/prompts.py)、[预设文件](../../data/skills/translate_with_terms.toml)、[面板加载入口](../../src/transbridge/ui/tools/smart_assistant/panel.py)和[旧工具移除记录](../../docs/changelogs/agent-tool-expansion/story-16-dead-code-registration/2026-05-18-002-废弃代码清理全项目.md)。
- **删掉没有桌面语义的操作。** 桌面范围没有“排除选区”操作，S2 已从说明、执行及预览撤下 `set_scope(action="exclude")`。S4 核实桌面“质量处理”是任务配置页，已撤下 `run_postprocess` 及其参数。旧配置中的 `combined` 继续由桌面读取器归一为 `proofread`。见[范围选择](../../src/transbridge/ui/tools/ai_translator/scope_presenter.py)、[桌面任务配置页](../../src/transbridge/ui/tools/ai_translator/single_task_view.py)和[校对策略决策](../../docs/adr/028-compact-proofread-and-custom-workflow-profiles.md)。

已从注册表移除的五个旧后处理子工具、旧格式写回工具和旧筛选/标签命令，不再计入待删工具数。落实本方案时清理仍供模型或用户读取的引用，历史 changelog 保留。第 4 个无独立桌面操作的工具 `get_tool_help` 属于助手查阅自身目录所需的协议功能。

## S1　工具目录、提示与预设

删除清单中的入口需同时从注册、原生工具描述、帮助、按名分派、计划校验、代理预设和对外适配器撤下。核对内置 Skill 的依赖、允许列表和正文指令。历史计划遇到已下线操作，按现有无效步骤机制中止，不重放旧写入，也不将旧请求自动换成范围更大的操作。

实施时同步修订现行 FR9 工具清单、FR19.15.9 和 [ADR-049](../../docs/adr/049-task-result-lifecycle.md) 第 14 项涉及助手逐条上传的约定。助手 `get_task_result` 中专门枚举/读取旧上传回执的分叉随旧路径退出，现役任务通过共享任务历史和现有权限查询。ParaTranz 的 `get_upload_history` 查的是平台远端修订，继续按桌面远端历史语义核对。

验收从实际注册表及面板加载的预设出发。退役名称无法被发现、直接调用或经旧计划执行，已发布的系统提示不承诺旧长期记忆，内置 Skill 中的每个业务工具名能在该角色的可用目录解析。涉及 `test_native_tools.py`、`test_tool_consolidation.py` 和同步工具契约测试。

## S2　筛选、编辑和翻译范围（优先）

同一请求的筛选、查询、批量标签预览与实际写入使用同一范围，真实表格可跨页选中、取消或清空。见[筛选、选区及标签记录](../../docs/changelogs/assistant-shared-business-tools/story-02/2026-11-22-001-workbench-filters-selection-labels.md)。第二批把 `edit_translation` 和 `set_stage` 接入工作台正在使用的编辑命令，并在确认前保留待写词条的原值和修订。翻译、润色、混合任务沿用桌面范围与执行分组，见[编辑与翻译范围记录](../../docs/changelogs/assistant-shared-business-tools/story-02/2026-11-22-002-workbench-entry-edits.md)。

S2 的三个只读工具已接入工作台。统计按阶段分组和桌面分类返回，标签按当前来源的完整身份计数，状态读取已加载来源、工程版本、请求筛选及当前窗口的凭据配置状态。见[统计、标签及应用状态记录](../../docs/changelogs/assistant-shared-business-tools/story-02/2026-11-22-003-workbench-read-tools.md)。

涉及 `get_app_state`、`get_current_filters`、`get_statistics`、`set_filters`、`get_visible_entries`、`select_entries`、`edit_translation`、`set_stage`、`list_labels`、`manage_entry_labels`、`set_scope`、`get_scope_preview`。工作台表格以 `step2.py`、`filters_presenter.py`、`inline_edit.py`、`translation_stage_action.py` 为准，翻译任务使用窗口的范围选择器和 `task_scope.py`。筛选成员按完整词条身份比较；界面显示顺序和助手查询顺序按各自合同。`set_scope` 通过阶段、标签和分类设置包含范围，预览和启动读取同一范围。

同一夹具中，3 条无标签记录按指定标签筛选，两端应均为 0，随后批量打标应写入 0 条。阶段 0 首次填入非空译文，两端默认转阶段 1。对已删除的排除模式验证没有任务启动或词条写入。正常范围的预览集合与启动集合一致。覆盖分页选区、跨来源切换、显式改阶段及并发编辑保护。

## S3　ParaTranz 上传、比较与工程绑定（优先）

涉及 `upload_entries`、`compare_with_remote`、`export_artifact`、`get_project_info`、`get_paratranz_project`、`switch_paratranz_project`。保留“上传”意图，按桌面[上传入口](../../src/transbridge/ui/operations/categorized_upload_bridge.py)接到[文件上传服务](../../src/transbridge/application/file_upload.py)。普通上传到根目录的 `<插件名>.json`、分类上传和窗口内按失败文件或分片补传都是现役功能。去掉旧 `force_overwrite` 按远端词条 ID 覆盖的语义，采用桌面明确提供的上传模式。单词条或一组选区无法由共享接口精确表达时拒绝执行，不能扩大成整文件上传。

比较使用同一文件路径、TB2 身份与差异计算；只读预览如需提取，桌面也应使用提取后的步骤。项目绑定读取工作台当前绑定，远端详情与导出分别使用桌面的项目查询和导出流程。按请求区分触发导出与下载成品。

已完成。查询本地绑定可离线执行；绑定前由桌面和助手共同核验当前账号的项目成员资格。远端详情共用项目接口和字段解释。触发导出与下载已有 ZIP 分别接入桌面对应操作。见[工程绑定、项目详情与导出记录](../../docs/changelogs/assistant-shared-business-tools/story-03/2026-11-22-001-paratranz-binding-export.md)。

`download_entries` 已复用[项目下载服务](../../src/transbridge/application/sync/project_download.py)。旧助手专用的下载成果 ID 和“下载后再调一次合入”已取消。11 月版本的共享服务仍会把有目标来源且配置了任务存储的下载候选交给[统一任务存储](../../src/transbridge/application/sync/download_task_results.py)，遵循 [FR32.5](../../docs/requirements/project-storage-redesign.md)。保留桌面任务中心对这类记录实际提供的操作；工具完成后不引导二次合入，也不添加专用恢复、重新下载或撤销命令。

验收用模拟远端捕获请求。比较、上传在同模式下的目标文件、发送字段、跳过项和差异与桌面一致。旧 key 与带 TB2 序号的同一身份可匹配。预览零写入；部分成功后的补传只发送共享服务判定为未完成的部分。新流程不写本地逐条关联、不读取旧回执决定补传，也不自动重发结果未知的远端写入。下载同时验证工具返回统计和共享任务中心记录。

## S4　翻译、润色、后处理、术语与任务成果

`start_translation` 和 `start_polish` 已接入桌面 `AiTaskWorker` 和 `SourceExecutor`，覆盖翻译、润色和混合模式。助手沿用桌面保存的模式配置，显式润色参数仅覆盖本次运行。见[同输入对照和实施记录](../../docs/changelogs/assistant-shared-business-tools/story-04/2026-11-22-001-desktop-translation-execution.md)。

第二批已撤下助手独立后处理工具。对已有译文进行校对或运行严格策略使用 `start_polish`，翻译及混合任务中的质量处理随 `start_translation` 运行。引用旧工具的计划按无效步骤中止。见[独立后处理下线记录](../../docs/changelogs/assistant-shared-business-tools/story-04/2026-11-22-002-retire-standalone-postprocess.md)。

第三批已将 `list_quality_reports` 接入桌面“历史报告”的导出文件查询。两端共用跨插件发现、报告类型识别和时间排序，已删除按旧助手任务目录查找 Excel 的实现。见[历史报告对照记录](../../docs/changelogs/assistant-shared-business-tools/story-04/2026-11-22-003-exported-report-history.md)。

第四批将 `apply_dictionary` 接入桌面“套用词典”。两端按当前来源查词典，对空译文使用同一套匹配与阶段规则，遇到多个译法时在同一个窗口仲裁。确认后只提交变更的词条，关闭仲裁窗口沿用自动选中的译法。助手已撤下专用的覆盖、语言参数及词典成果生成流程。见[套用词典实施记录](../../docs/changelogs/assistant-shared-business-tools/story-04/2026-11-22-004-workbench-dictionary-apply.md)。

第五批通过桌面任务中心的修订控制接口执行暂停、继续与停止。术语同步返回的原生任务 ID 也可直接查询和停止，归属和修订校验与桌面一致。成果查询共用任务历史服务，成果合入共用工程合入命令、候选与回执校验。见[任务控制及成果记录](../../docs/changelogs/assistant-shared-business-tools/story-04/2026-11-22-005-task-center-control-results.md)。

第六批已将翻译配置、术语来源排序、动态库计数与入站审核接入桌面设置和术语服务。入站预览由 `prepare_import_selection` 取得当前修订，提交草稿及签发授权沿用人工确认。见[配置和术语实施记录](../../docs/changelogs/assistant-shared-business-tools/story-04/2026-11-22-006-translation-terminology-config.md)。

任务中心按实际动作和归属开放状态、停止、成果查询和合入；通用成果 ID 本身不赋予写权限。逐类核对翻译、导入和下载成果的项目、版本和可执行动作。核查已有词典记录时，以桌面历史实际提供的操作为准。已保存的 AI 候选按现用恢复流程处理；彻底退役的后处理步骤和旧 checkpoint 写入不接回。当前任务详情和已导出的 Excel 历史报告分别走对应桌面入口。离线对照相同输入的范围、候选、终检和成果，核对 1 条动态术语显示为 1 条，并覆盖跨会话隔离、取消、合入冲突、报告历史及入站草稿过期。

## S5　文件导入和写回

涉及 `parse_esp`、`parse_eet`、`parse_xt`、`parse_sst`、`import_json`、`write_back`。删工具说明中“新建全局槽位、任意追加到集合”的旧说法，按桌面区分“在当前工程登记来源”和“向现有来源导入译文”。已撤销的独立解析插件窗口无需恢复。当前工程添加翻译文件、按现有身份导入和正式写回仍有桌面入口。`import_json` 按桌面的[独立来源登记范围](../../src/transbridge/application/io/open_translation_file.py)接受 ParaTranz 和 DSD JSON；内部 TransBridge JSON 只向已有来源导入匹配译文。SSU8/SSU9 仍是现役格式，沿用现行读写条件。见 [SST 决策](../../docs/adr/052-sst-io-integration.md)。

导入沿用桌面[译文导入服务](../../src/transbridge/application/io/translation_import.py)和对应格式迁移规则。写回从 `write_feature.py` 复用材料、目标、预检、备份及发布流程。按格式分别验证登记与导入译文，覆盖同 key 不同原文、歧义匹配、重复导入及旧工程。单插件与批量写回分别对照目标、诊断和成品。通用 `filter_files` 继续执行文件筛选；涉及 FOMOD 打包的预览须使用桌面读取的 XML 资源引用。

来源登记与译文导入已经按八种可登记格式和九种译文来源完成桌面同输入对照。来源提交与大型视图准备在后台完成，确认前取消保留原工程，已提交的来源可在回执失败后重新载入。见[文件导入记录](../../docs/changelogs/assistant-shared-business-tools/story-05/2026-11-22-001-file-import.md)和[提交线程与回执记录](../../docs/changelogs/assistant-shared-business-tools/story-05/2026-11-22-004-source-publication-thread.md)。

单插件、混合批量写回及 FOMOD 资源预览已通过成品回读、覆盖、备份和取消对照。归档生成和远端修订列表的参数也已按现役桌面入口修订。见[文件写回记录](../../docs/changelogs/assistant-shared-business-tools/story-05/2026-11-22-002-file-writeback.md)和[归档与远端分页记录](../../docs/changelogs/assistant-shared-business-tools/story-05/2026-11-22-003-catalog-remote-archive.md)。

## 发布与验收

S1 先定助手目录和撤下的调用，再与 S3 同步切换上传。S2 与 S3 优先，S4 接续 S2，S5 可独立实施。每次切换检查同输入的桌面和助手结果，清除被替代的助手实现。只处理工具、说明与执行路径，既有用户工程和历史文件不作迁移或清理。

最终按实际注册表逐项核对了 62 个业务工具及目录查询协议 `get_tool_help`。四个退役名称已从可执行目录及角色、预设移除。存词典、来源切换、术语参数和远端分页等终验差异均已整改。各工具签名、生产入口与同输入测试见[最终验收记录](../../docs/changelogs/assistant-shared-business-tools/story-05/2026-11-22-005-final-registry-acceptance.md)。
