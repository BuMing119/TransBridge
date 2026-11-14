# 项目统一文本编码、完整转码与安全原文重读

- 日期：2026-11-14。
- 状态：S01～S06 本地实现与相关验证完成；5 项已确认的基线失败和未运行场景明确保留。
- Epic / Story：`project-text-encoding` / S01～S06；[计划](../../../../plans/project-text-encoding/plan.md)、[FR33](../../../requirements/project-text-encoding.md)、[ADR-055](../../../adr/055-project-text-encoding.md)。需求已确认，ADR 保持提议、设计保持草稿，不把已授权实施误记为额外设计批准。
- 归属依据：开始时捕获的工作区文件清单及逐文件内容基线。以下按基线直接比较本轮变化，并结合目标文件 Git 差异核对；同文件中已有的术语、多方案、存储与导出布局改动不属于本记录。没有提交或推送 Git，没有修改依赖、锁文件、安装器或真实用户翻译数据。

## 结果与设计边界

程序设置提供默认输入/输出编码，新项目复制后独立保存。项目导入、追加、更新、补充输入和单文件/批量/多方案导出使用同一组值；项目菜单和项目栏提供“文本编码”，导出只显示本次冻结的生效值。独立文件操作和 FOMOD 使用程序默认，FOMOD 整次运行固定一组值。

普通插件显示文本与三种 Strings 严格按选定输入编码读取、按目标编码写出。未译、未改和未引用 Strings 同样参与纯转码，不静默尝试其他编码，不替换无法表示的字符。SST、EET、XML 等翻译存档保持各自编码；VMAD 等专用字段与非文本载荷保持格式规则。暂存输出以目标编码回读并核对预期文字和结构。

项目输入编码变更先后台重读全部受影响来源，再预览和原子提交所有翻译版本。可靠映射保留译文、标签与完整状态，变更原文的已有译文待复核，隐藏/锁定继续保护。输出编码修改不读取源，也不保存未提交译文。恢复点保留旧编码对，恢复时安全重建其他版本身份，不静默丢译。

## S01：设置与持久化

- 新增 `src/transbridge/config/text_encoding.py`：`TextEncodings`、`TextEncodingDefaults`，标准 codec 规范化后限定 UTF-8、Windows-1250～1258、874、932、936、949、950；全局由 ConfigRepository 保存，未知值明确失败。
- 新增 `application/projects/source_encoding.py`：项目输入编码写入操作局部请求，界定插件/Strings 适用格式；权威追加边界校验已解析来源快照及声明编码与项目一致，拒绝旧编码预解析结果绕过项目设置。
- 修改 `application/projects/provisioning.py`：创建准备时捕获程序默认，首次解析和项目 settings 保存使用同一值；`provisioning_sources.py` 对全部创建来源统一注入输入编码。
- 修改 `persistence/project_provisioning.py`：解析受控格式时传递编码，原生格式不继承插件输入覆盖，插件译文供体读取使用同一输入编码。
- 修改 `persistence/project_store/records.py`：读取验证编码，持久设置白名单保留项目值；`archive_validation.py` 校验项目及快照中的编码字段，允许缺字段现行项目按 UTF-8 打开。
- 修改 `application/projects/source_fields.py`：保留 input_encoding 作为来源解释事实；`source_extraction.py` 重建来源快照使用明确编码；`translation_templates.py` 重建 Strings 时保留既有 Unicode 与历史记录编码，不重新猜测已保存文字。
- 修改 `bootstrap/composition.py`：注册全局默认和项目编码服务；`bootstrap/persistence.py` 装配服务与建项默认读取。

## S02～S03：严格字节读写与完整转码

- 新增 `parser/game_text.py`：严格编码、按格式约束内嵌 NUL 及 Windows 码页回读一致性检查，拒绝可编码但会变成另一 Unicode 字符的别名，诊断带字符位置。长度前缀 DLSTRINGS/ILSTRINGS 保留合法内嵌 NUL，普通 NUL 终止字段继续拒绝。
- 新增 `parser/localized_string_records.py`：统一三种 Strings 的记录边界、显式严格解码、编码与目录重建；格式探测可只校验结构，不猜文本编码。
- 新增 `parser/plugin/text_subrecords.py`：`EncodedStringSubrecord` 在原始普通文本字节边界应用指定编码，保留尾部载荷；写入前验证文本与 65535 字节 inline 上限，失败带插件/record/field/物理位置。
- 新增 `parser/plugin/binary_io.py`：实例局部插件树适配，复用原库 group/record/压缩能力，保留未修改结构化及不透明载荷；不修改第三方全局类型或编码列表。
- 修改 `parser/plugin/plugin_with_context.py`：接入局部 PluginBinaryIO；`parser/plugin_parser.py` 接收明确输入编码并传给普通字段和关联 Strings，严格解码异常向上报告。
- 修改 `parser/strings_file.py`：删除 UTF-8→cp1252/replacement 回退与部分损坏继续解析，复用共享记录 codec；本地化 writer 从完整类型表初始化、保留未引用项，先编码所有表再写文件。
- 修改 `writer/plugin_writer.py`：在修改树之前验证所有普通文本；阶段选择后仍对未译/未改字段转码；零译文也可输出，完整关联 Strings 按目标编码写入。
- 修改 `application/io/legacy_adapters.py`：插件读取传递 input_encoding 并捕获到快照；`plugin_localized_sources.py` 将相同选项传到关联 Strings。
- 修改 `application/io/plugin_publication_source.py`：运行内源解析复用键包含输入编码，防止不同解释共享解析树；`plugin_write.py` 分别传递源输入与目标输出编码。
- 修改 `application/io/strings_adapter.py`：复用共享记录 codec，所有记录按目标编码渲染；保留 string ID、顺序和阶段策略，输出用目标编码回读，错误带文件和记录。
- 修改 `application/io/publish/plugin_fidelity.py`：产物及关联 Strings 按 output_encoding 回读；`publish/validators.py` 构造回读请求时将目标编码作为 input_encoding，避免用源编码验证目标。

## S04：全来源重读、全版本事务与恢复

- 新增 `application/projects/text_encoding.py`：`ProjectTextEncodingService` 的 active_encodings/read、prepare/commit/discard；聚合来源预览、原路径补充、owner/generation/脏状态保护、取消和提交前全来源重解析。
- 新增 `application/projects/text_encoding_matching.py`：实际原来源指纹成立后，以两端唯一 local_key 或重复插件项的持久字段顺序建立一对一身份映射；拒绝行数/键集合/定位歧义和冲突，不模糊匹配。
- 修改 `application/projects/source_update_migration.py`：支持 matched_keys 迁移完整版本状态，编码重读保留隐藏/锁定保护；`source_update.py` 提炼 updated_registry，并使普通源更新遵守当前项目输入编码。
- 修改 `application/projects/source_commands.py`：追加来源前捕获版本和项目前像并统一项目输入编码；`source_supplement.py` 补充读取遵守相同设置；`write_inputs.py` 给实际插件绑定明确输入编码。
- 修改 `application/projects/source_input_publication.py`：提炼 publish_source_revisions，统一多来源基线和活动/非活动发布，使用实际预期版本修订并保留诊断。
- 修改 `application/projects/lifecycle.py`：既有项目元数据提交增加预期 generation 和可选 clean guard，保证编码设置保存不覆盖并发状态。
- 修改 `persistence/project_store/source_updates.py`：previous_many 复用一次项目读取，commit_many 在单 SQLite 事务核验项目/全部版本/引用路径、写恢复点和全部来源/设置/状态；单来源更新委托该路径。
- 修改 `persistence/project_store/history.py`：来源快照保存旧输入编码，编码重读恢复点额外保存旧输出编码；`historical_restore.py` 同事务恢复解释配置与来源，并对其他版本安全重映射。
- 新增 `persistence/project_store/encoding_restore.py`：只在同源指纹和可靠定位条件下反向映射条目身份，保留其他版本译文和保护状态；不安全时明确失败。

## S05：可见入口与操作一致性

- 新增 `ui/text_encoding_fields.py`：非编辑码页下拉控件；`ui/settings/text_encoding_page.py`：程序默认页，读取/保存失败明确反馈；`ui/project_text_encoding_dialog.py`：项目编辑与全来源预览对话框。
- 新增 `ui/text_encoding.py`：活动项目或程序默认的有效值决议与显示文本；`ui/coordinators/project_text_encoding_coordinator.py`：后台 prepare/commit、进度取消、缺失来源定位、预览确认与成功后工作台刷新。
- 修改 `ui/app.py`、`ui/context.py`：提供已装配的服务；`ui/main_window.py`：协调器和项目栏入口接线。
- 修改 `ui/settings_dialog.py`：默认编码保存、错误与按钮状态；`ui/settings/center_layout.py`、`sections.py`：新增设置页；`ui/shell/tool_windows.py` 传入默认配置 facade。
- 修改 `ui/shell/action_catalog.py`、`intent_composition.py`、`menu_builder.py`：统一项目编码意图、菜单和可用性；`ui/workbench/_project_bar.py` 提供同一动作。
- 修改 `ui/coordinators/open_file_coordinator.py`、`parse_coordinator.py`、`parse_migration.py`、`migration_coordinator.py`：导入、插件解析与已有译文迁移使用明确有效输入编码；`project_coordinator.py` 的旧插件恢复读也传递编码并报告失败。
- 修改 `ui/workbench/file_source_formats.py`、`open_file_dialog.py`：普通来源入口列出三种独立 Strings，已有译文供体仍使用独立的原格式列表，避免将纯字符串表误当结构化译文供体。
- 修改 `ui/shell/project_creation_form.py`、`start_center_controller.py`：建项同样区分原文来源与已有译文供体列表；`ui/coordinators/source_update_coordinator.py` 补齐三种 Strings 的更新文件过滤器。
- 修改 `ui/source_input_state.py`：scope 保存冻结编码对；`ui/coordinators/write_input_coordinator.py`：缺输入补充期间继续使用该对值；`ui/operations/facade.py`：从入口捕获并在补充、范围选择和后续导出间保留。
- 修改 `ui/operations/production.py`：注入有效值读取与错误转换；`write_feature.py`：单个/批量/多方案 draft 使用同一输入输出 options；`ui/coordinators/operation_coordinator.py` 呈现无法构造导出计划的错误。
- 修改 `ui/operations/plan_view.py`、`mappers.py`：传输编码摘要；`write_plan.py`、`write_batch.py`、`write_json_export.py`：依据实际输出格式构造摘要；`write_dialog.py`、`write_window_layout.py`：显示只读生效值，切到原生翻译格式时隐藏插件编码说明。
- 修改 `application/io/initial_translation.py`、`legacy_migration.py`、`migration_import.py`、`open_translation_file.py`、`translation_import.py`：插件/Strings 供体及迁移读取透传 input_encoding，原生翻译格式不改变。
- 新增 `smart_assistant/tools/text_encoding.py`：按助手操作所绑定项目统一读取编码或独立操作默认；修改 `tool_parser.py` 将输入编码带入解析与来源登记，`write_back_preparation.py` 复用该决议并传到输入绑定及输出草稿；auto 接受独立三种 Strings，显式 Strings 目标保持来源原生类型。
- 修改 `fomod/plugin_publishing.py`：准备来源携带编码对并用于严格发布；`fomod/pipeline.py`：整次流水线冻结一次程序默认，旧插件译文迁移按同一输入编码读取。

## S06：回归与文档

- 新增 `tests/config/test_text_encoding.py`：明确支持列表/别名、非法输入及程序默认持久化。
- 新增 `tests/parser/test_plugin_text_encoding.py`：码页严格读取和线程隔离、普通文本全量转码、专用/不透明载荷保留、先验证后修改、零译文、inline 上限、Windows 回读变字拒绝、三种 Strings、合法长度前缀 NUL 及未引用项。
- 新增 `tests/application/projects/test_project_text_encoding.py`：默认隔离、项目包、两来源/多版本、original 限定身份、重复定位、取消/过期/缺输入、输出单改与脏译文、事务故障及恢复前后解释；独立复审后补充旧编码预解析来源拒绝与长度前缀 NUL 原文重开回归。
- 新增 `tests/integration/publish/test_project_text_encoding.py`：真实 adapter/publisher 完整纯转码、不可表示失败、已有目标保留、BSA 全类型与未引用 ID、Strings 阶段、供体编码、目标模板及 SST/EET/XML 原生编码隔离。
- 新增 `tests/ui/test_project_text_encoding.py`：默认页独立保存/取消、缺输入补充、聚合重读确认及默认取消；`tests/ui/operations/test_write_text_encoding.py`：单/批及方案展开保持冻结编码，插件显示摘要、SST/JSON 隐藏摘要。
- 修改 `tests/ui/test_open_file_workflow.py`：三种独立 Strings 按项目 cp936 打开和登记；`tests/ui/operations/test_write_source_references.py`：缺输入补充/恢复保持同一编码对；`tests/smart_assistant/tools/test_write_back_options.py`：真实插件与三种独立 Strings 的 cp936 输出字节。
- 修改 `tests/smart_assistant/tools/test_source_import_authority.py`：真实 cp1252/cp936 助手导入及来源登记，错误编码不登记；`tests/plugin_fixtures.py` 为合成插件夹具增加显式编码参数。
- 修改 `tests/application/projects/test_source_commands.py`：追加来源请求断言显式项目 UTF-8，不再将空 options 当作合同。
- 新增 `tests/contracts/io/fixtures/strings/utf8-integrity.strings`、`utf8-integrity.dlstrings`、`utf8-integrity.ilstrings` 三个统一 UTF-8 二进制夹具；原 `integrity.*` 混合编码夹具保持原样。
- 修改 `tests/contracts/io/test_strings_parse_bytes.py`、`test_localized_strings_adapter.py`：统一 UTF-8 正常路径，BOM 作为实际 U+FEFF 文本保留；增加原混合字节夹具严格失败且无逐记录回退断言，明确 cp1252 输出不可表示时失败。
- 修改 `tests/contracts/io/test_write_recovery.py`、`tests/integration/publish/test_atomic_publish.py`、`tests/persistence/project_store/test_exchange_fidelity.py`、`tests/performance/terminology/dataset.py`、`tests/quality/test_success_chains.py`：正常流程使用新的统一 UTF-8 夹具，保留既有恢复、交换、性能数据及成功链合同；`tests/quality/success_chains.py` 沿用已有夹具防漂移登记，保留旧摘要并加入三个新夹具摘要，未新增生产哈希机制。
- 修改 `tests/integration/publish/test_bsa_localized_write.py`、`test_bsa_shared_variant_ids.py`：BSA 合成样本的 Café 统一 UTF-8，保持同编码未引用项原字节断言；`tests/parser/test_localized_capture_boundaries.py` 的单一 cp1252 案例显式声明输入和输出码页。
- 修改 `tests/integration/publish/test_plugin_write_reuse.py`、`tests/ui/operations/test_export_flow_source_reuse.py`：测试替身接收并透传新编码参数，保留解析复用断言。
- 新增 [FR33](../../../requirements/project-text-encoding.md)、[ADR-055](../../../adr/055-project-text-encoding.md)和[六项 Story 计划](../../../../plans/project-text-encoding/plan.md)；修改 `docs/requirements.md` 的 FR1.3 原始字节合同以容纳明确转码，并增加 FR33 入口；最小更新 `docs/INDEX.md`、`plans/INDEX.md` 和 changelog 索引。

## 实际验证

使用仓库现有 Windows/Python/uv 环境，临时文件与 uv cache 隔离在任务自有目录。主会话通过 `uv --cache-dir <任务缓存> run --offline python` 调用 `pytest.main`，以下记录实际 pytest 选择器及结果，省略临时目录参数。各组有重叠，不相加为独立测试总数。

- parser/writer/Strings 第一组：499 通过；包含随后广域 I/O 中部分共享 Strings 测试。
- `tests/contracts/io tests/integration/publish tests/application/fomod tests/smart_assistant/tools/test_write_back_options.py tests/smart_assistant/tools/test_write_back_source_scope.py tests/smart_assistant/tools/test_write_back_publication_boundary.py tests/smart_assistant/tools/test_safe_write_back.py`：952 通过、1 项 Windows 符号链接能力跳过，7 项既有 deprecation warnings，115.02 秒。
- `tests/integration/publish/test_project_text_encoding.py`：最后补入已有输出保护、BSA 未引用字符串、真实 EET/XML 原生编码与三种独立 Strings 后，16 通过。
- `tests/test_fomod_pipeline.py tests/test_fomod_typed_pipeline.py tests/test_fomod_tm_provenance.py tests/application/fomod/test_plugin_publishing.py`：72 通过。
- `tests/config tests/application/projects tests/persistence/project_store`：414 通过、1 跳过、5 失败。5 项均位于 `tests/application/projects/test_source_artifact_publication_cost.py`，已在保留的任务前基线独立重跑并相同失败，原因是旧“来源路径为空”断言与本轮前已有 source_reference 持久化行为冲突；本轮不修改这些既有语义或把失败归为编码回归。
- 项目专项前一组 64 通过；最终项目范围包含第二个 Variant 写入失败故障注入，验证两来源、设置、版本、恢复历史完整回滚，以及编码恢复和其他版本译文保留。
- `tests/smart_assistant/tools/test_write_back_options.py -k encoding`：4 通过，验证真实插件及三种独立 Strings 的 cp936 发布字节；完整 options 34 项随后包含于最终 118 项复验。助手 options 与 quality 合计 46 项已通过，其中 quality 12 项。
- UI 最终扩大组：`tests/ui/test_ui_settings_dialog.py tests/ui/test_settings_keyboard_navigation.py tests/ui/test_settings_responsive_layout.py tests/ui/test_open_file_workflow.py tests/ui/test_source_update.py tests/ui/test_project_text_encoding.py tests/ui/test_parse_hydration_authority.py tests/ui/test_workbench_translation_import.py tests/ui/test_legacy_migration_safety.py tests/ui/operations`，通过 628 项，44.24 秒、4 项既有弃用警告。包含此前 150 项聚焦验证，不重复累计。实际命令使用 `uv --cache-dir <任务缓存> run --offline pytest`，参数含 `-q --tb=short` 及任务自有 `--basetemp`。
- 独立复审修复后最终边界组：`tests/application/projects/test_project_text_encoding.py tests/application/projects/test_source_commands.py tests/contracts/io/test_open_translation_file.py tests/smart_assistant/tools/test_write_back_options.py tests/ui/test_open_file_workflow.py tests/ui/test_parse_hydration_authority.py`，118 通过，覆盖追加来源编码权威校验、长度前缀 NUL 原文保存重开和真实输出。
- 独立复审的助手读取/codec 组：101 通过，覆盖实际 cp1252/cp936 导入、错误编码不登记、共享助手编码决议及严格 codec 边界；精确选择器未在本记录中保留，不编造命令。
- 所有实现与复审修复后，`ruff check src tests` 通过；`ruff format --check src tests` 通过，2680 个文件已格式化；`git -c core.safecrlf=false diff --check` 通过。静态工具通过既有 uv 环境运行。

首次沙箱执行受到 Windows `os.link`/临时数据库操作权限限制；相同工作区隔离命令在允许这些文件操作的环境完成。该环境失败不作功能验证通过证据，也未通过禁用发布保护绕过。

## 兼容与限制

现行项目缺编码字段按 UTF-8 读取配置，打开不重新解码保存原文、不访问来源插件。旧项目原先可能依赖回退解释非法输入，现在实际读取明确失败，需要用户选择正确编码；这不自动改写已存文字。只承诺当前程序读取现行缺字段项目，不能承诺旧程序接受新字段项目包。

重读验证实际来源指纹与已存定位；项目未保存历史 Strings 字节，不能证明其过去字节或重建被丢弃的信息，旧原文以已存 Unicode 为准。预览到提交重验本次完整来源。入站 `XXXX` 不透明载荷保留，当前不新增其可编辑字符串解析；普通 inline 字段转码后超过 65535 字节明确失败，不自动升级为另一结构。无自动编码检测、无文件级覆盖、无临时导出覆盖。

离屏验证覆盖设置窗口 720×480、明暗主题、10/14 pt 字体，以及项目编辑/重读预览 480×220、520×345 的几何和键盘操作；未进行可见桌面完整操作、真实大型 Mod 或外部 xTranslator/EET 软件联动验收，不能将自动化夹具通过等同于这些场景。五项既有测试失败保留，不声称全仓 pytest 全绿。

临时基线、合成样本、输出与测试目录属于本任务，交付前由主会话核对归属后清理；本记录保留结果和选择器，不将临时目录作为长期证据链接。代码范围无待实现项，剩余仅为上述未运行验收范围与已确认基线测试失败。
