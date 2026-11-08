# 修复译文来源在建项、拖放和补充输入间的能力差异

- **日期**：2026-11-07
- **Epic / Story**：`maintenance/translation-import`，跨入口格式一致性维护。
- **关联**：[前序 XML 建项修复](../../guided-ui-workflows/story-02-04-project-creation/2026-11-07-005-xml-translation-source.md)、[文件解析计划](../../../../plans/file-parsing/plan.md)。
- **类型**：缺陷修复、现有读取器接线和回归验证；不修改依赖、持久化 schema 或发布包。

## 用户可见结果

1. 拖放识别支持 UTF-8 BOM、UTF-16 和 UTF-16BE XML，与文件选择入口一致；保留大文件有限前缀探测预算。
2. 新建工程可直接读取 DSD JSON、TransBridge JSON、SSU8/SSU9 SST，并保存来源内容；原文件移走后仍可重开。新增适配器只声明读取能力，不开放写回。
3. 二进制 EET 不再被拖放预填到 EET XML 输入框。拖放和建项给出导出 XML 的提示；补充输入不再提供未实现的二进制 EET 选项。
4. 新建工程筛选去掉没有建项解析器的 Excel，补上 SST。`.eet` 只出现在明确标为 XML 的筛选组中，识别仍以内容为准。

沿用既有工程来源模型：新建工程中的译文文件作为独立来源，不自动将 XML/JSON/SST 内容折叠到原始插件词条；工作台迁移继续沿用原有匹配规则。

## 文件与责任边界

- **修改 `application/io/catalog.py`**：XML 根节点探测交给 ElementTree 识别编码，去掉只接受 ASCII `<` 开头的前置判断；注册四种读取适配器；将 `entry_key` 作为 TransBridge JSON 身份证据，避免精简 V2 数组被误判为 ParaTranz/TransBridge 歧义。
- **新增 `application/io/import_adapters.py`**：提供 JSON、SST 读取适配器，复用现有解析逻辑；保留快照、词条状态及已有身份，为 legacy 身份分配文件来源命名空间；支持取消、拒绝重复身份和不完整内容。写入显式返回 `FORMAT_WRITE_UNSUPPORTED`，能力声明受既有 policy ceiling 约束。
- **移动 JSON 读取实现至 `application/io/json_source.py`**：从 `smart_assistant/tools/_json_import.py` 提取；允许传入已捕获字节，避免解析内容和快照来源不一致；验证 JSON 条目对象类型，并保留 DSD 原始记录数量供适配器检测冲突丢弃。
- **修改 `smart_assistant/tools/_json_import.py`**：仅保留原 `ParsedJsonSource`、`parse_json_source` 导入接口的兼容转发，不保留第二份实现。该转发是现有调用方兼容边界，未移除旧接口。
- **修改 `parser/xt/sst_parser.py`**：`from_file()` 委托新增的 `from_bytes()`，供来源快照使用同一份字节；初始化 SSU8 空文件和 SSU9 缺尾部情况下的局部变量，避免 `UnboundLocalError`。
- **修改 `application/io/use_case.py`**：二进制 EET 无适配器时保留稳定错误码并给出导出 XML 的操作提示。
- **修改 `persistence/project_provisioning.py`**：将解析器的具体诊断附在建项错误中，避免把导出 XML、重复身份和截断数据等原因吞成通用提示。该文件前序的内容识别修复不重复计入本增量。
- **修改 `ui/drop_router.py`**：移除二进制 EET 到 XML 迁移种类的映射，识别后直接给出专用拒绝诊断。
- **修改 `ui/workbench/_parse_config_dialog.py`**：EET 预填只接受未知或 `xml.eet` 格式，拒绝明确的 `binary.eet`。
- **修改 `ui/coordinators/source_input_coordinator.py`**：去掉二进制 EET 选项，加入已有读取适配器支持的 DSD/TransBridge JSON；保留 SST。
- **修改 `ui/shell/start_center_controller.py`**：对齐已有译文筛选，去掉 `.xlsx/.xls`、加入 `.sst`，提供明确 XML/JSON/SST 分组。

上述路径均相对于 `src/transbridge/`。新适配器模块 167 行，共享 JSON 读取模块 135 行。原 SST 解析器现为 526 行，已做责任复核：本次仅增加同一解析职责的字节入口及两处局部变量初始化，没有加入 UI、存储或写入职责；因此保留局部修复，后续扩展解析职责前仍需按记录布局拆分。

## 测试变更

- 新增 `tests/source_format_fixtures.py`：生成完整且最小的 DSD/TransBridge JSON、SSU8/SSU9 文件，不使用用户翻译数据。
- 新增 `tests/contracts/io/test_import_adapters.py`：27 项覆盖内容识别、文本和状态、快照、命名空间、取消、拒绝写回、JSON 兼容导入接口、重复身份、格式冲突、截断 SST、空 SST 及精简 V2 JSON。
- 新增 `tests/persistence/v2/test_project_provisioning_import_formats.py`：5 项覆盖四种格式建项/保存/原文件移走后重开，以及二进制 EET 的可操作诊断。
- 修改 `tests/contracts/io/test_catalog.py`：更新实际注册适配器及实验性能力预期。
- 修改 `tests/ui/test_safe_drop_router.py`：8 项编码/方言有限前缀组合，以及二进制 EET 提前拒绝；替换旧的二进制 EET 可迁移预期。
- 修改 `tests/ui/test_structured_migration_import.py`：补二进制 EET 不得预填 XML 字段的回归。
- 修改 `tests/ui/test_start_center_multi_plugin.py`：在前序 XML 选择器测试中验证 SST 可见、Excel 移除和二进制 EET 不在默认支持筛选内。

## 验证命令与结果

首轮聚焦 75 项中，读取、建项和界面用例已通过；4 项写回拒绝用例揭示 `OperationResult` 缺少失败计数，补齐后通过。格式检查发现的行布局问题均已修复。

完整 I/O 契约及相关入口第一次扩大验证：**464 passed、1 skipped**。补充边界后执行：

```powershell
uv run pytest tests/contracts/io/test_import_adapters.py tests/persistence/v2/test_project_provisioning_import_formats.py tests/persistence/v2/test_project_provisioning_xml.py -q
```

结果：**49 passed**。

最终扩大回归：

```powershell
uv run pytest tests/contracts/io tests/smart_assistant/tools tests/persistence/v2/test_project_provisioning.py tests/persistence/v2/test_project_provisioning_multiple_sources.py tests/persistence/v2/test_project_provisioning_xml.py tests/persistence/v2/test_project_provisioning_import_formats.py tests/application/projects/test_provisioning.py tests/ui/test_safe_drop_router.py tests/ui/test_safe_drop_binding.py tests/ui/test_structured_migration_import.py tests/ui/test_start_center_multi_plugin.py tests/ui/test_start_center_guided_project.py tests/ui/test_parse_migrations.py tests/ui/test_legacy_migration_safety.py -q
```

结果：**829 passed、1 skipped、3 failed、12 errors**，不是全绿。失败/错误全部在隔离进程内加载 Git HEAD 原版修改模块后复现；使用 `git show HEAD:<path>` 和内存 import loader，没有替换、回滚工作区文件：

- `test_source_receipt_failure_retry_recognizes_committed_source`：已提交来源的回执重试被“来源已存在”拒绝。
- `test_localized_prepared_source_roundtrip_preserves_binary_companions_and_metadata`：夹具期望与新增的术语来源 metadata 不一致。
- `test_choose_multiple_plugins_creates_one_project_and_loads_every_collection`：旧断言期待路径 slot key，实际为来源身份。
- `test_saved_source_task_inputs.py` 的 12 项 setup 错误：`_saved()` 返回值数量与三变量解包不匹配。

原版模块对照针对上述三个失败节点及整个 `test_saved_source_task_inputs.py`，结果为 **3 failed、12 errors、1 passed**，与当前版本一致。测试范围外的上传确认代码未纳入原版模块替换。

额外执行：

```powershell
uv run pytest tests/application/projects/test_source_supplement.py -q
uv run --offline ruff check src tests
uv run --offline ruff format --check src tests
git diff --check
```

结果：来源补充 **8 passed**；全库 Ruff 和 **2388 个文件**格式检查通过；diff 空白检查通过。各轮测试有重叠，不累加为唯一通过数。测试在正常本机环境执行，沿用之前确认的沙箱原生库限制；静态检查使用任务专用 uv 缓存目录。

## 限制与未验证项

- 二进制 EET、Excel 没有新增解析能力：本次修复入口误导和错误路由；二进制 EET 需先导出 XML。
- 新共享读取适配器不提供 JSON/SST 写回；DSD/SST 的实验性标记不提升。
- 建项来源需要完整、无身份冲突且属于单一来源命名空间；截断 SST、冲突 JSON、混合来源身份文件不会按成功的部分内容建项。已有助手部分成果策略和工作台迁移匹配不变。有歧义的旧 JSON 仍需在支持格式选择的入口显式指定格式。
- 额外命令 `uv run pytest tests/ui/test_on_demand_source_inputs.py tests/application/projects/test_source_supplement.py -q` 在前 4 个 UI 用例之后长期无进展，已核对精确命令行并结束本次测试进程。该 UI 集成验证未完成，不归为已通过或已证实基线问题；应用层已单独验证通过。
- 未跑全仓 pytest、真实用户文件验收、安装包或线上服务测试；既有 3 项失败、12 项夹具错误留待独立任务。
- 任务专用 `.tmp-format-unification` 缓存收尾清理；原有 `.transbridge/`、上传确认变更、前序 XML 修复及增量保持原样。
- 未执行 commit、push 或发布。
