# 应用层预检与可选来源参考路径

- 状态：S01～S10 本地实现、相关验证、增量记录与任务清理完成。保留未执行项及既有失败边界。
- 日期：2026-10-28；最终更新：2026-10-29
- 需求：[FR8.3/FR8.4、FR19.10、FR26.8、FR29.5](../../docs/requirements.md)。
- 架构：[ADR-050](../../docs/adr/050-application-preflight-and-optional-source-reference.md)，复用 [ADR-049](../../docs/adr/049-task-result-lifecycle.md) 已保存来源成果。

## 目标与边界

既有 UI 预检和业务检查迁移或委托到 application，UI 只展示稳定 diagnostic/repair intent。原始路径仅可选参考：启动、打开、导入时只判断存在性，缺失置空；不读 hash，不以路径决定来源身份、已保存内容可读性或操作可用性。操作优先消费项目保存内容，确实缺少当前操作的输入时才要求补充。

不新增检查/校验规则、不新增通用校验总管、不改变主动新输入、实际输出目标和工程自身完整性的既有保护，不扩大既有下载工作线。保留用户原有未提交修改；本计划验证只陈述可归属证据。

## S01～S04 交付事实与本轮缺口

- `application/preflight/operation.py` 拥有既有不可变检查、预检结果、repair intent 及输入/输出/覆盖/备份/锁定隐藏规则；UI 模块保留薄重导出和展示映射。`ai.py` 承担既有 AI 预检。
- `persistence/saved_project_sources.py`、`source_artifact_store.py` 和 `source_artifact_archive.py` 已提供耐久来源成果，不需要再建存储。
- 来源 location 可空、可重复，slot 按正式 `source_id` 绑定；参考路径不再承担成果重定位、包内读取和身份前提。新增来源 ID 使用已捕获内容 namespace 生成，已保存 ID 不迁移，同内容重复保护保留。
- 历史搜索、术语构建和 AI 翻译窗口消费已保存成果；术语完整输入只随当前请求持有，长期状态和任务记录保留轻量证据，fingerprint 不变。AI 助手工具链与 AI 翻译窗口不同，S09 补齐审计已确认的助手缺口。
- 纯 legacy JSON 记录不读外部来源或 SST magic，缺少真实保存内容时保留译文并进入原恢复视图；旧 portable 包自带 sources 是导入时明确提供的输入，解析后保存成果，不把 location 改写成内部路径。输出位置独立选择。
- 本轮实施前，以上整工程只读恢复不符合完整按需补输入目标：`CurrentProjectOpener` 遇首个来源错误就停止加载，`VariantMaterializer` 会跳过缺 baseline namespace，UI 安装依赖 hydration。S05 使用独立来源可用性状态、权威保存版本与可用基线的无损并集、保存状态投影修复这些根因；真实 MainWindow 还覆盖空/稀疏版本和 tombstone 可见性。

## S01：既有预检由应用层承担

- 状态：本地实现与相关验证完成。
- 文件：`src/transbridge/ui/operations/{mappers,preflight_view,write_feature,production_support}.py` 及实际调用的其他 feature adapter；新增 `src/transbridge/application/preflight/operation.py` 承担 facts/service/Kind/types，`ai.py` 承担 AI 既有预检。
- 步骤：迁移已有 DTO 与纯检查；各业务 adapter 委托其原有应用/领域服务；UI 保留展示、调度及 repair intent 路由，必要旧导入只重导出。
- 验收：既有 code、级别、顺序、修复动作和确认语义保持；应用模块不依赖 Qt/UI；同一检查不在 UI 再实现一次；不借迁移新增规则。
- 验证：现有 operation mapper、计划界面、写回、FOMOD、AI/同步相关聚焦回归；补充应用层直接调用和 UI 委托的行为测试。

## S02：来源参考可空且与身份解耦

- 状态：本地实现与相关验证完成。
- 文件：`application/projects/{source_registry,source_content,source_commands,source_update,installed_source_request}.py`、`persistence/v2/schema.py`、`persistence/{current_project,saved_project_sources,source_artifact_archive,source_artifact_codec,project_archive,project_archive_files,legacy_project_archive,archive_source_inputs}.py` 及 UI slot/来源绑定调用链。
- 步骤：移除 location 非空/唯一门禁；`application/preflight/sources.py` 统一存在性归一化，确认缺失才清空，权限等原因导致存在性未知时保留参考且不新增阻断；改用来源/成果稳定身份连接；包内内容读取和成果恢复不从参考路径推导必需内容。
- 验收：多个空路径、重复参考路径不混淆来源；删除/移动/替换原文件后打开仍使用已保存内容；清空参考不改变成果身份；包导出导入不依赖原路径/文件名；不读取参考路径 hash；缺失成果保留既有恢复诊断。
- 验证：`tests/persistence/v2/test_saved_project_sources.py`、`test_source_artifact_archive.py`、`test_project_archive.py`、`test_project_source_registry_migration.py` 及来源命令/slot 聚焦测试；验证主动输入、成果损坏和实际输出保护保持。

## S03：下游操作消费已保存内容

- 状态：本地实现；相关术语最终联合回归 210 项通过。
- 文件：`bootstrap/history_search.py`、`bootstrap/terminology*.py`、`application/terminology/{input_capture,build,corpus,runtime,workloads}.py`、`smart_assistant/tools/tool_proofreader.py`、`ui/dynamic_terminology_source.py`、AI 启动及业务预检调用链。
- 步骤：复用保存来源读取，去除路径存在或变化触发的实时重解析；AI 使用已安装基线与条目，不以 `esp_path` 非空作为可执行条件；真正缺输入沿用现有提示和修复动作。
- 验收：参考路径删除或同路径文件替换不影响历史译文搜索、术语构建和 AI 已保存输入；原文来自成果、译文来自 Variant；不会读取新外部文件覆盖保存内容；必要输入确实缺失时只影响相应操作。folded 译文来源不替换主原文，保留自身 fingerprint/fragment/关系证据；动态词库入口复用现有缓存定位，不创建第二套缓存。
- 验证：历史搜索、术语构建与 AI 启动的聚焦回归，加入“文件已删但保存成果存在”和“成果本身缺失”的成对案例。

## S04：联合验证与记录

- 状态：完成；最终 Ruff/格式、差异检查通过，任务临时目录已由各创建者及主会话核实归属后清理。
- 步骤：复核 UI 剩余业务检查和路径门禁；运行相关测试；执行 `uv run ruff check src tests`、`uv run ruff format --check src tests`；检查文档链接与任务临时文件；按真实 diff 写增量记录。
- 验收：没有新增规则或无关改动；最终记录命令、结果、未执行项和兼容限制，不把单元测试等同桌面验收。
- 证据：工程/来源组 382 通过，1 项 SQLite 长临时路径失败，短 `--basetemp .tmp-pfr` 单测复跑通过；术语最终组 210 通过。UI 旧 key 断言经修正并聚焦复验，AI 沙箱权限失败在授权环境定向复验通过。全库 Ruff 通过、2272 文件格式通过。完整分批命令、环境重试和限制见[增量记录](../../docs/changelogs/application-preflight/story-01-to-04/2026-10-28-001-application-preflight-and-saved-inputs.md)，不将重叠批次相加为唯一测试总数。

## 风险与回退

旧工程若完全没有已保存来源内容，不能伪造原文；本轮 S05～S08 要求合法已保存译文正常编辑保存，仅实际操作缺输入时补充。旧 portable 包明确携带的内容可在导入边界解析为成果；这不授权读取外部参考路径。移除参考路径约束不移除稳定 ID、项目内部引用、成果 bytes 的完整性保护。回退保留成果、空参考及补齐前后的已有译文，不把回退等同于删除未匹配条目。

动态词库继续使用既有 reference-or-stable-key 本机缓存定位；清空参考后旧 stem 缓存不自动迁移至 stable-key，缓存迁移不在本次实现范围，正式工程术语与保存原文不受影响。

## 假设与未决事项

S01～S09 已实现范围无需新的产品选择，相关历史验证不证明 S10 已完成。S10 的汇总、批量选文件/文件夹、同名候选显式选择和确认跳过交互已由用户通过原型批准；实施与验证进度单独记录。AI 助手继续使用 S09 的原事项恢复合同，不将 AI 翻译窗口的验证推广为助手已覆盖。

## S05：来源内容未就绪时正常编辑保存

- 状态：本地实现与相关验证完成；承接首轮真实缺口，不改写 S01～S04 历史证据。
- 文件：新增 `application/projects/source_inputs.py`、`persistence/project_source_inputs.py`、`ui/saved_translation_slots.py`；修改 `persistence/{current_project,project_lifecycle_loader,prepared_project_read}.py`、工程生命周期/models、`ui/{source_hydration,source_input_state,project_view_binding,projection_types,entry_projection,projection_consistency}.py`、工程打开/切换协调器；未就绪状态与展示按内聚责任提取。
- 步骤：逐来源记录内容是否就绪并继续加载其他来源；合法 Project/Variant 正式激活。物化和保存保留缺 baseline 的已存 namespace/条目；投影从权威保存状态展示译文、阶段、标签和真实身份，原文未知显式呈现。损坏的工程/版本记录继续执行现有保护。
- 验收：全部/部分来源成果缺失均可正常打开并编辑保存已有译文；重开、切版本、自动保存不丢条目；有输入的来源继续可用；没有伪造来源基线、快照、hash 或原文；不会因为来源内容缺失主动读取参考文件或弹选文件框。
- 验证：打开、版本物化、保存、UI 投影回归；覆盖 mixed 已就绪/未就绪、多版本、legacy bare key、EntryKey 带/不带 original、显式空译文、Stage/labels/refs/tombstone 保真。
- 实现事实：基线仅补实际缺省条目，所有保存行及 tombstone 保留；UI hydration 和保存态投影只展示非 tombstone，投影一致性比较同一可见集合。未知原文使用显式 `original_known` 标记区分展示载体与真实空原文，正式来源 snapshot 不伪造。

## S06：明确来源对应的补输入应用用例

- 状态：本地实现与相关验证完成；依赖 S05 的未就绪来源合同。
- 文件：新增 `application/projects/source_supplement.py`、`source_supplement_models.py`、`source_supplement_mapping.py`、`source_input_publication.py`；修改 `source_update.py` 抽出并复用原原子发布流程，`bootstrap/{persistence,composition}.py` 注册 `project_source_supplements` 用例。
- 步骤：接收原 Project/Variant/source_id 及用户明确选择的文件；后台解析、复用既有身份/条目匹配语义；保存实际输入成果、发布对应来源，再刷新该来源输入状态。旧内容不足时如实保留不能证明的映射，不从 reference 猜测对应关系。
- 验收：选定文件只补对应来源；已有译文/阶段/标签/引用不被清空，未匹配条目仍保留；完整来源不被替换；解析或保存失败保持旧工程可用；补齐后成果可复用，重开不需再次选文件。不新增校验规则或第二套解析/迁移框架。
- 实现事实：`prepare/remap/commit/discard` 返回明确条目对应预览；全部正式 Variant 保留未匹配行与自身状态。已有 artifact 仍可用但覆盖不全时保留原成果，新输入只补其缺失部分。`publish_source_input` 复用来源更新的重新准备、目标/generation 和事务提交边界，未复制第二套事务保护。
- 验证：应用层直接调用，覆盖同一来源补齐、无法对齐条目、多来源隔离、失败原子性及成功后重开；新选文件仍经过已有输入边界。

## S07：实际操作按需选文件并继续

- 状态：桌面 UI 范围本地实现与相关验证完成；AI 助手独立工具链补齐见 S09；依赖 S05、S06。
- 文件：新增 `application/preflight/inputs.py`、`ui/coordinators/source_input_coordinator.py`、`ui/source_supplement_dialog.py`，`ui/source_input_state.py` 承担输入事实提取；接入 AI 翻译窗口、写回、同步、术语与迁移的桌面启动入口。
- 步骤：操作实际需要缺失原文/模板/依赖时返回稳定补输入意图；界面明确来源并选文件，调用 S06；成功后重新准备同一操作的原范围/参数并继续。沿用既有操作许可、目标和生命周期处理，不另建通用总管。
- 桌面验收：已有译文查看编辑保存不询问输入；需要输入的操作可以完成“提示—选文件—对应来源—补齐—继续”；取消只取消当前操作，编辑及其他来源不受影响；多缺失来源只补当前操作所需；切换工程/版本后旧回调不在新目标运行。无 GUI 调用者返回明确待补输入且不调用 Qt；助手独立调用链的实现与验证见 S09，不以本节桌面证据替代。
- 验证：Qt offscreen 场景覆盖成功继续、取消、解析失败重试、切换/关闭及多个来源；应用预检测试证明没有扩大规则，只有输入可用性影响相应操作。

## S08：按需输入闭环验证与追加记录

- 状态：桌面范围验证、记录与清理完成；主会话已核实绝对路径并删除 `.tmp-pi/`，两实施 Agent 已清理独占临时目录；AI 助手后续修复单列 S09。
- 步骤：联合运行工程打开/保存/切版本、来源补齐、AI/写回等实际入口相关回归与全库 Ruff/格式；复核无参考路径读取及无伪造原文，核实任务临时文件后清理。
- 验收：每项 S05～S07 行为都有实际证据；不把旧 S01～S04 通过数用于证明新闭环。不合并重叠测试数，不宣称未执行桌面/网络/模型验收。
- 记录：[增量 002](../../docs/changelogs/application-preflight/story-05-to-08/2026-10-28-002-on-demand-source-inputs.md) 记录真实分批命令：最终 loader/打开 44 项、MainWindow/投影 42 项通过，术语生产链 8 项通过；报告/UI 硬链接环境失败授权复跑通过，全库 Ruff/2289 文件格式通过。保留两个已证实 HEAD 既有源码字符串合同失败，不宣称全仓 pytest 全绿；`001` 保持不变。
- 002 落盘后的主会话复验：`uv run --no-sync pytest tests/application/projects tests/contracts/projects -q --basetemp .tmp-pi/final --tb=short` 共 278 项，276 通过、2 项同一 HEAD 既有源码字符串断言失败；没有新增失败。结果更新本计划，不改写历史增量 002。

## S09：助手按需输入与原请求恢复

- 状态：本地实现、相关验证、记录和清理完成；真实助手窗口覆盖补齐、新确认及失败重试，独立成果合入和请求许可范围有直接证据。001、002 保持不变。
- 根因：助手翻译/润色/后处理不经过 AI 翻译窗口的补齐入口，未知原文的空展示载体可能进入实际操作；缺 artifact 但有真实 `EntryKey.original` 的 capture/persist/merge 已复现因 context `None` 序列化为 `""` 而全部 pending。助手动态词库及报告还需和 GUI 共享稳定来源定位；写回不得从快照 URI 恢复已清空的默认输出参考。
- 文件：`smart_assistant/request_execution.py`、`smart_assistant/tools/{task_source_identity,input_prerequisites}.py` 及翻译/后处理/写回调用链，成果捕获/序列化/合入的 context 证据；`application/assistant_requests/{admission,dispatch,source_input_waiting,source_input_selection}.py`。GUI 在 `ui/tools/smart_assistant/` 提取独立补输入动作绑定，通过既有 `ConfirmationView.complete_tool` 与计划回执接入 `SourceInputCoordinator`，不扩展大类为新总管。
- 输入与成果：已知真实原文无需仅为文字操作补 artifact；未知原文不进入模型输入，缺二进制模板不伪造 snapshot。仅 saved-only 的未知 context 增加明确可选证据；真实解析的空 context 仍是已知空值，保持显式字符串 context、locator 及旧记录的既有合入保护，不全局把 `None` 和 `""` 视为相同。报告 worker 仅携带冻结来源名称等轻量定位证据，词库复用现有 reference-or-stable-key 策略。
- 请求状态：工具返回专用错误 code 和结构化 `source_input_repair`，由真实 effect receipt 识别当前事项的 `source_input_required` 等待；effect 和 step 仍保存真实失败，不伪造成功。只有专用缺输入失败可等待，其他执行失败和部分结果维持既有语义。dispatch 不将纯补输入等待折叠为 `plan_failed`，也不在解除等待后由旧 dispatch 再次写入等待。
- 交互与恢复：GUI 主线程展示可执行补输入按钮，worker 不弹 Qt。动作捕获原 session/request/item、工程/版本、工具步骤及原 canonical 条目范围，不从后来活动请求推断归属。补齐后只解除原事项的该等待原因，重新选择并接受作用域受限的 admission，重新准备和确认映射后的原操作；不复用已消费许可、不重新执行计划已完成步骤、不向模型追加一段文本冒充恢复。未对应条目不自动扩大到新来源所有条目；取消只影响当前动作。
- 输出边界：默认输出只使用当前 slot 的可选参考；参考已清空时，不借 `snapshot.source.uri` 写回旧文件。显式输出目录可复用保存的文件名，缺输出目标继续返回原有 OUTPUT diagnostic。
- 验证：真实保存工程上的助手三种任务 capture/save/merge、未知原文与缺模板结构化 repair、GUI 选文件/对应来源/补齐/新确认/执行、取消与切工程隔离、原范围映射和旧 dispatch 不重复阻断；普通失败仍终结、已成功步骤不重跑、context/locator 保护及报告/动态词库定位兼容。相关回归、静态检查及清理已完成，不使用桌面窗口旧通过数代替助手证据。

- 收尾证据：[增量 003](../../docs/changelogs/application-preflight/story-09/2026-10-29-003-assistant-source-inputs.md) 记录助手最终输入/GUI/coordinator 23 项、既有请求 UI 89 项、请求流 49 项、输出边界 79 项及最终输出范围 27 项通过；来源/成果组合 151 通过、1 项 Windows fixture 失败单独复跑通过，应用请求组 285 通过、4 项沙箱硬链接失败授权复跑通过。批次有重叠，不合并总数。全库 Ruff 与 2299 文件格式通过，任务独占临时目录已清理；未跑全仓 pytest、真实模型/网络、人工桌面或性能基准，两个 HEAD 既有字符串断言失败保持原状。

## S10：写回缺输入汇总、批量补充与明确的部分写回

- 状态：本地实现、相关验证、增量记录与任务清理完成；证据见[S10 增量](../../docs/changelogs/application-preflight/story-10/2026-10-29-001-batch-source-inputs.md)。依赖 S06、S07、S09。
- 授权依据：用户已接受 `writeback-input-prototype.html` 演示的“缺输入清单—选择多个文件/从文件夹查找—选择同名版本—确认写回及跳过范围”流程，并要求实现。原型为会话可视化产物，计划中的下述契约是仓库内实施依据。
- 用户结果：一次看清本次写回还缺哪些插件模板；可以批量补充、处理同名候选，也可以明确选择仅写回已准备好的插件。已有模板继续直接使用；跳过项的译文保留，AI 助手使用同一补输入流程并在新确认后继续原事项。
- 范围：只改写回的模板补充体验及其精确范围传递。其他文字操作保留既有逐来源补齐行为；不引入来源更新、全盘搜索、版本自动判定、新检查规则或新的通用校验框架。来源路径仍只是可选参考，不能因为它缺失或变化要求补齐已有模板。

### 当前实现与责任落点

- `SourceInputCoordinator.ensure` 将模板请求交给 `ui/coordinators/source_input_batch.py`，后者持有清单和候选状态，串行复用 S06 的 `prepare/remap/commit/discard`；每项发布重开后刷新事实。其他文字操作保留单项补齐流程。
- `OperationFacade.begin_write` 将确认后的 `SourceInputScope` 一直传给实际计划构造；`SourceInputScope.active_slot` 也限制在选定来源内。`write_feature.create_write` 和 `_HydratedWriteBatch` 允许原批量请求仅剩一个来源，但拒绝空范围，保持批量输出目录语义。
- 助手 `input_prerequisites.py` 的全部/缺失来源事实继续复用；`source_input_binding.py` 接入共享清单，`source_input_selection.py` 将原请求、人工选择及映射后来源持久化为 `source_scope`。`request_scope.py` 恢复来源范围，`write_back_preparation.py` 拒绝通过参数扩大或改变已确认范围；新 admission 与新确认保持。
- 应用层实际切片为 `application/projects/source_input_candidates.py`，承载独立 DTO、用户所选目录的候选枚举及名称提示；不读旧参考内容、不创建第二套解析和发布事务。`source_inputs.py` 仅增加 legacy/reference basename 显示名回退。
- `ui/source_input_batch_dialog.py` 使用表格模型展示清单、折叠已有输入、候选选择和执行/跳过确认。唯一候选逐项委托补齐；全部保存行已由现有逻辑匹配时自动使用对应，否则保留 `SourceSupplementDialog`。文件名本身不证明词条映射。

### 设计契约

1. **操作范围是上界。** 打开清单时捕获原 Project/Variant、操作来源范围及输出选项；已有模板来源计入已准备好数量，仅缺模板来源进入待补清单。清单中的显示名、文件名与路径只用于展示和候选查找，目标仍绑定原 source identity/namespace，不由同名文件创建或扩展操作范围。
2. **批量候选与明确对应。** 多文件选择只处理用户本次选择；文件夹查找仅在用户选定目录内查找，可递归子目录以呈现不同目录的同名候选，不自动读取旧参考路径、游戏目录或其他根目录。候选枚举与解析在后台进行。重复选中同一路径应去重；无法枚举的目录或解析失败提供现有错误上下文，不能显示为“已补齐”。
3. **同名不是版本证据。** 本次所选输入中的同名唯一候选且仅对应一个缺输入来源时，可以串行补充；只有全部保存条目都已由既有规则对应时才自动应用映射，否则仍展示 `SourceSupplementDialog`。同一来源存在多个同名候选时列出可区分的路径，由用户明确选一个。不得自动按修改时间、目录顺序或原参考路径选“最新/正确”版本；同名不同来源也不能暗中合并。无法确定对应时保留逐项选择文件入口。候选只是选择结果，只有实际补齐成功且模板事实就绪才计入可写范围。
4. **只补缺，不替换。** 已有可用模板不参与批量匹配或发布，即使用户选择了同名新文件也继续使用保存模板。未发布的候选允许重新选择；已发布并就绪的来源再次选择不得变成更新来源。条目对应沿用 S06，未对应译文和其他 Variant 状态继续保留。
5. **发布有明确结果。** 多来源可串行复用 S06 发布；每次成功后以新的项目状态准备下一来源，避免预先生成的多个 publication 因前一项提交而全部过期。单项失败保留该项可重试状态和已经成功保存的成果，不能伪报整批成功，也不回滚或删除已保存译文；取消及“稍后处理”不触发写回，已经成功保存的输入可下次复用。未提交 token 必须按现有 discard 生命周期释放。
6. **部分写回必须显式选择。** 仍有未就绪项时，默认不能继续全部写回；用户选择“本次跳过”后，确认页同时列出将写回和将跳过的插件以及输出位置。跳过只影响本次操作，不删除来源、不改变工程译文状态、不永久屏蔽后续补齐。至少一个已准备好来源才能继续；零项范围不创建输出任务。
7. **就绪只指输入就绪。** 最终实际写回仍经过已有格式、输出、覆盖、备份、冲突等预检与确认。输入汇总不新增 hash/存在性规则，也不把“模板已准备好”视为输出操作已获许可。后续普通预检失败保持现有语义，不冒充用户主动跳过的缺输入项。

### 部分写回的范围约束

- 以初始来源集合 `R` 为上界，按用户确认的补齐映射得到当前身份集合；确认执行集合 `W` 只能由其中输入就绪的来源组成，显式跳过集合 `S` 是原范围内的其余来源。`W` 和 `S` 必须互斥且完整解释本次原请求；未显示或不在原请求内的来源不能进入 `W`。这属于执行既有用户范围合同，不新增文件校验规则。
- 正常恢复、只剩一个来源、重开工程、重试预检与修改输出位置均保留同一 `W`。不要把单项 partial 改成“当前活动来源”，也不要把空 tuple 当作未指定范围而回退到所有 slots。保持批量输出目录语义，允许原批量范围收缩为一个来源。
- 补齐改变 namespace/slot key 时，只使用已有明确映射解析原来源；不能以“当前所有就绪 slots”重新构建范围。捕获后的新增来源不会自动加入，原范围来源消失或工程/版本变化沿用既有失效处理，不能静默补入替代项。
- 来源范围应作为显式数据传递给计划构造、草稿、最终确认和执行结果；最终展示的将写回集合须与实际 `HydratedWriteDraft`/助手 `PreparedWriteBack.source_keys` 一致，跳过项不授权输出路径、不创建工作负载。输出参数保持原请求值，改变时使用原有重新预检/确认流程。
- 助手恢复必须保留原 session/request/item、revision/lease 和原工具参数。已确认的 `W` 与 `S` 随该次补输入恢复数据固定，并用于重新生成 `source_keys`；不能只修改临时 UI 参数后在确认重试时恢复为原全量范围。继续走 S09 的新 admission 与新确认，不复用旧 `_prepared_write_back` 或已消费许可，不自动执行已成功事项。跳过是用户批准的范围收缩，不能将未写出的插件报告为成功输出。

### 实施顺序与验收

1. 实现无 Qt 的候选汇总与范围选择模型，提供缺项、已有模板、同名候选和明确的选择结果；复用现有解析/补齐服务。以应用层测试证明仅匹配本次缺项、重复路径去重、歧义不自动选择、失败不冒充就绪。
2. 实现写回输入清单、多文件/目录入口和逐项候选选择；通过后台任务串行发布并汇总结果。验收至少涵盖已有两项可用模板、四项缺失、多个同名候选、部分补齐后继续或稍后处理，不重复要求已有模板。
3. 接通桌面精确 `W` 与助手恢复链，支持批量收缩为一项，并让最终确认可见 `W`、`S` 和输出位置；助手确认失败后的再次展示仍使用同一范围。验收实际 drafts/工作负载/输出路径，而不只检查按钮文案。
4. 聚焦验证建议扩展 `tests/application/projects/test_source_supplement.py`，为 batch 切片新增同主题测试；扩展 `tests/ui/test_on_demand_source_inputs.py`、`tests/ui/operations/test_write_save_as.py`、`tests/ui/tools/smart_assistant/test_source_input_repair.py`、`tests/application/assistant_requests/test_source_input_selection.py` 及 `tests/smart_assistant/tools/test_write_back_options.py`。
5. 关键回归：原范围外已有模板不被写回；空范围不回退；单一剩余来源写入其自身目标；同名不覆盖已有模板；取消和解析失败后可再次补齐；成功补充后重开可复用；切换/关闭使旧回调失效；未映射译文保留；助手新许可/确认与旧派发等待状态不互相覆盖；参考路径缺失不触发额外读文件或检查。
6. 完成时记录实际聚焦命令及结果，运行 `uv run ruff check src tests`、`uv run ruff format --check src tests`；人工桌面、真实模型/网络与全仓测试如未运行应明确列出，不复用 S01～S09 通过数证明 S10。

### 兼容、风险与回退

复用已有成果存储和来源补齐事务，不迁移工程格式、不改 lockfile。批量候选只决定补充哪个缺输入来源，不能把“版本选择”扩展为全来源升级。批量发布为逐来源持久成功，不承诺跨来源全有或全无；界面须诚实显示成功与失败项。回退交互代码仍保留已补成果和全部译文，既有单来源补齐可以继续使用。助手清单显示已有输出参数，缺目标仍需提供 `path`/`output_dir`，不新增助手输出位置选择器。

- 验证事实：最终写回/操作界面组 45 通过；候选与真实补齐组 43 通过、1 项 Windows 符号链接权限跳过；助手/来源范围组 85 通过。批次有重叠，不相加为唯一总数。全库 Ruff 与 2308 文件格式检查通过，diff 检查通过。扩展请求执行另有 27 通过、4 项无关 task recovery 硬链接 `WinError 5`，未混修；未跑全仓 pytest、真实 LLM/网络、真实用户工程或 Windows 人工视觉验收。任务独占临时目录已由各创建者/主会话核实绝对路径后清理并确认无剩余。
