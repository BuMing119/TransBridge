# S24：恢复 BSA 本地化插件写回

日期：2026-10-25。
关联：[当前计划](../../../../plans/paratranz-categorized-upload/plan.md)、[FR1.3](../../../requirements.md)、[写回说明](../../../dev/writer.md)。

## 故障与旧版证据

用户写回 80 个插件得到 `SOURCE_LOCATOR_NOT_FOUND`。最新日志 `20261025_224430.log`
显示正常读取能从 BSA 获取条目，写回临时源却全部提取为零。应用层的完整源快照仅捕获松散
Strings 文件，临时目录只有插件文件，没有可用于重新定位的归档字符串资源。

对照 `7270205` 的旧 GUI：解析器持有插件和 `strings_lookup`，直接交给已有 `PluginWriter`，
因此 BSA 能工作。改用快照后应补齐依赖字节，而不能绕过来源、定位或发布校验。

实际 Dragonborn 还暴露既有跨表假设错误：ID 148 同时存在于 `.strings` 和 `.dlstrings`，
但文字不同。旧读取使用 `merged.update()` 合并，再只按数字查找，后表可能覆盖前表；
旧写入器已经按字段类型分表。当前快照代码则直接拒绝跨表重号。本轮复用既有分流规则修正
查表、重建和校验的身份范围，同表内矛盾译文仍拒绝，并非新增一套格式规则。

## 实现增量

- 新增 `parser/localized_resources.py`：统一松散/BSA 发现和原始成员捕获，复用安装的
  `sse-bsa.BSAArchive.glob()` / `get_file_stream()`，不实现解压或归档编码。
  每类型松散文件优先，合法空表也优先；仅缺失类型进入 BSA。保留基础游戏 Interface 归档和
  `_ResourcePack` 自身归档的既有选择，流及时关闭，失败保留路径和原异常原因。
- `parser/strings_file.py`：移除重复发现实现，保留资源原字节与分类型表；新增 typed lookup，
  复用 `PluginStringsWriter._ext_for()`。旧 `get(id)`、字典构造、空表返回语义保留。
- `parser/plugin/plugin_with_context.py`：把插件库已解析的子记录类型传给 typed lookup；
  旧 lookup 对象仍回落原接口。`parser/plugin_parser.py` 复用库的 `RecordFlags.Localized`，
  仅本地化插件发现 BSA；inline 继续复用 `from_strings_dir` 保留有效松散表。
- `application/io/strings_adapter.py`：将已有文件解析提取成可复用的 `parse_bytes()`，
  不读取虚拟 URI；原编码、BOM、记录次序、重复检查和写回合同不变。
- 新增 `application/io/plugin_localized_sources.py`：集中完整来源快照及物理依赖映射。
  BSA 成员保留原始字节、成员名和真实归档来源；虚拟成员 SHA 不冒充整个归档 SHA。
  取消返回取消结果；inline 快照 fallback 和验证使用相同的仅松散策略。
- `application/io/legacy_adapters.py`：复用解析器已捕获的资源，避免二次读取；捕获失败保留原因
  和堆栈。严格 live-source 写入验证资源选择与字节；既有 hydrated 模式仍使用已确认快照。
- `application/io/plugin_write.py`：从已捕获字节物化插件及完整表，复用原解析器/写入器。
  保留 namespace、定位、精确原文和 ID 校验；hydrated DTO 不携带字段类型时，从已核验物理源
  恢复库解析结果，不猜测 UI context。以 `(表类型, ID)` 合并译文，其余记录保持原字节。
- `application/io/publish/plugin_fidelity.py`：物理回读及共享引用验证使用相同类型身份，
  跨表污染继续拒绝；阶段策略和同表有效文本一致性检查保留。
- `fomod/plugin_publishing.py`、`application/io/plugin_transfer_snapshot.py`：租约使用真实 BSA
  指纹，捕获前后验证成员及来源选择；松散遮蔽、归档变化、取消仍拦截。
  独立审查在隔离 fixture 实证输出可覆盖来源/模板归档后，补上实际归档禁止输出规则。
- `application/io/operation_write.py`：普通 hydrated 写回同样补齐 `SOURCE_ARCHIVE_PROTECTED`，
  覆盖所有 bundle 目标；显式覆盖不能把真实依赖归档替换成 TES4 插件。
  原有 staging、备份、整组校验和原子替换流程复用。

所有改动对照本轮开始前副本核验，保留已有工作区的 AI、同步、阶段策略及其他写回修改。
未改 `uv.lock`、依赖版本、工程 schema 或发行配置。未移除真实来源/阶段/身份保护。

职责审查：新增资源发现和应用快照分别为独立小模块，`strings_file.py` 缩到 448 行，
`plugin_write.py` 为 373 行。既有 `legacy_adapters.py`（当前 719 行）、
`strings_adapter.py`（537 行）、`plugin_with_context.py`（502 行）仅保留原职责的局部修复或解析提取；
本轮不夹带高风险全格式拆分。以后增加适配器职责或新的格式处理前，须将 SSE 适配器及公共支撑
按格式边界拆出，并保留现有合同测试。

## 测试证据

新增真实压缩 BSA fixture 使用库的 `create_archive()`，不是 mock 解压器。

- `tests/parser/test_localized_resource_capture.py`：19 项，完整原字节、混合来源、空表优先、
  基础归档、大小写、损坏资源、单次读取和流关闭。
- `tests/parser/test_localized_variant_identity.py`：11 项，真实字段路由、同数字跨表、
  兼容旧接口和缺失类型不猜测。
- `tests/parser/test_localized_capture_boundaries.py`：6 项，inline 忽略无关坏 BSA、保留有效松散表，
  本地化插件仍拒绝损坏依赖，空表和捕获中取消。
- `tests/contracts/io/test_strings_parse_bytes.py`：33 项，文件/字节解析等价、编码/BOM、
  次序及原始 payload、损坏/重复/取消、虚拟来源无需读盘。
- `tests/contracts/io/test_bsa_source_leases.py`：17 项，真实归档租约、同归档三成员、归档其他成员变化、
  松散遮蔽、捕获竞态、模板迁移，来源/模板归档输出禁止的两项回归先失败后通过。
- `tests/integration/publish/test_bsa_localized_write.py`：28 项，mod 与基础游戏真实归档、
  hydrated 脱离/变更原文件仍能完成、live-source 冲突、未引用 CP1252 记录、整组回滚、
  覆盖及普通归档输出禁止（后两项新保护回归旧代码均放行、修后通过）。
- `tests/integration/publish/test_bsa_shared_variant_ids.py`：16 项，三表同 ID 独立更新、
  未修改表字节一致、同表共享、三类污染拒绝及矛盾写入不产生新产物。
- `tests/bsa_fixtures.py`：共用真实归档生成器。

以上新增共 130 个参数化用例，已包含在最终扩大回归中，不另行相加。

最终命令（统一设置 TEMP/TMP 为本轮短路径临时目录，复用现有 uv 环境）：

```powershell
uv --cache-dir .tmp-bsa24/uv-cache run --no-sync pytest tests/contracts/io tests/integration/publish tests/writer tests/parser tests/application/fomod tests/application/projects tests/persistence/v2 tests/ui/operations/test_strict_write_routes.py tests/ui/operations/test_plugin_transfer_ui.py tests/ui/operations/test_write_execution_feedback.py tests/ui/operations/test_fomod_execution_feedback.py tests/ui/test_project_source_hydration.py tests/ui/test_v2_project_transfer.py tests/ui/test_fomod_journey.py -q --disable-warnings --basetemp .tmp-bsa24/m -k 'not test_strict_parse_preserves_loading_exception_and_default_compatibility and not test_strict_parse_accepts_valid_plugin_without_translatable_strings and not test_reordered_source_projects_into_context_only_remote_plan'
uv --cache-dir .tmp-bsa24/uv-cache run --no-sync ruff check src tests
uv --cache-dir .tmp-bsa24/uv-cache run --no-sync ruff format --check src tests
```

结果：**1264 通过、1 跳过、3 排除**，64.04 秒；全库 Ruff 检查通过，2209 个文件格式通过。
7 条警告为既有 SWIG/运行警告。本轮最终源码冻结后执行上述扩大回归。

排除项与中间失败：

- 两项 `test_plugin_parser_failures.py` 严格解析测试仅 mock 库函数，却不创建 `test.esp`；
  既有严格 header 读取先触发 FileNotFoundError。本轮用修改前 `PluginParser` 副本重跑该文件，
  同样两项失败。未削弱真实文件的严格检查以迁就 mock。
- `test_reordered_source_projects_into_context_only_remote_plan` 为此前已确认的旧上传关联/顺序断言失败，
  本轮扩大执行再次复现；来源更新/上传规划不在本轮修改集合。
- 第一次扩大命令误带不存在的 `tests/fomod`，未收集测试；移除后实际执行。
  随后 1260 项通过但发现 valid loose 辅助表捕获回归；已恢复原行为，原测试保持不变。
  之后新增普通入口归档输出保护，再执行最终全部范围，结果如上。

## 实际游戏输入的隔离验证

根据上述日志提取 80 个实际游戏插件，只读取游戏目录，所有合成译文和产物输出到本轮临时目录。
通过真实 `SsePluginAdapter → HydratedWritePreflightService → HydratedWriteWorkload → bundle publish`
链路，每插件选择一条合成译文，写出后重新解析，核对条目总数、改动译文和完整产物集合。

首次 79 个通过，Dragonborn 因既有跨表重号假设失败；源目录的 156 个实际依赖 SHA256 均未变化。
修复类型身份后，Dragonborn 单独复验通过（10,907 条、3 种字符串表、4 个产物），
因此 80 个插件分别均有通过证据，不声称最后一次同时重跑全部 80 个。

另对实际 Dragonborn 的 ID 148 选择两种表的两个真实位置，分别写入不同合成译文，
走完整 hydrated 发布并回读，2 个类型、2 条更新、4 个产物均通过；插件和 Interface BSA 哈希未变。
这不代表已执行用户工程 96,306 条真实译文的全量写回，也不是性能基准。

## 兼容性、清理与边界

已有工程无需 schema 迁移；旧进程/旧缓存需重新读取来源以获得完整资源快照。
源 BSA 不改写；输出仍为插件和目标名对应的松散字符串文件。保留同表共享文字、阶段策略、
旧字典 lookup 和有效松散来源行为。低层 writer API 文档与当前受保护应用入口已明确区分。

本轮拥有的 `.tmp-bsa24`（含副本、压缩 fixture、实际游戏的隔离合成产物、uv 缓存）
在完成核验后清理；保留既有 `.tmp-batch-download-a0eb07b2`、用户日志、工程和其他临时目录。
未进行真实云端写入、真实工程全译文导出、全库 pytest、发行构建、commit 或 push。
上述三项既有测试问题保留；本次修复范围无未完成项。
