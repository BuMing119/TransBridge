# 统一新建工程的 XML 译文来源入口

- **日期**：2026-11-07
- **Epic / Story**：`guided-ui-workflows` / S02、S04
- **关联计划**：[plan](../../../../plans/guided-ui-workflows/plan.md)、[S02](../../../../plans/guided-ui-workflows/stories/story-02-authoritative-project-provisioning.md)
- **类型**：局部缺陷修复，无持久化 schema、依赖或公共 API 变更。

## 原因与结果

工作台的解析／迁移对话框已经支持 EET XML，但新建工程的“选择已有译文”筛选遗漏 `*.xml`，且建项来源在未指定格式时只根据插件后缀识别，导致手动选择 XML 仍然报 `PROJECT_SOURCE_FORMAT_REQUIRED`。

现在默认筛选可见 XML，并提供 EET / XTranslator XML 专用筛选。新建工程复用共享格式目录及适配器探测，按内容区分 EET 和 XT，支持 UTF-8、UTF-8 BOM 和 UTF-16；显式格式选择仍由对应解析器校验。无法识别、格式歧义、读取失败或损坏文件继续拒绝，保留诊断和失败来源。

沿用既有来源模型：新建工程将 XML 保存为独立双语来源及可恢复成果，不自动折叠到 ESP 的词条中；工作台迁移仍使用既有匹配规则。插件副本的 namespace 配对行为保持不变。保存后即使输入 XML 已移走，也能从工程保存成果恢复词条。

## 文件变更

- **修改 `src/transbridge/persistence/project_provisioning.py`**：`_infer_format()` 保留插件推断，其余来源使用 `default_format_catalog().resolve()`，包括适配器对 BOM/UTF-16 XML 的探测；补充歧义候选诊断和保留原始异常的读取错误。
- **修改 `src/transbridge/ui/shell/start_center_controller.py`**：`_choose_migrations()` 默认筛选增加 XML，并增加专用 XML 筛选组；多选、取消及后台建项链路保持原有实现。
- **新增 `tests/persistence/v2/test_project_provisioning_xml.py`**：17 项测试覆盖两种 XML 方言、多种编码、XML 内容使用 `.eet` 扩展名、大写扩展名、与插件共同建项、单来源解析、保存重开、输入文件移走、无效文件原子拒绝、显式格式不符、内容歧义及读取错误原因保留。
- **修改 `tests/ui/test_start_center_multi_plugin.py`**：增加 1 项选择器回归，验证默认筛选、路径保留、取消不清空以及实际建项请求携带 XML 来源。
- **修改 `docs/changelogs/INDEX.md`**：仅新增本记录链接；保留任务开始前已有的强制上传确认记录。

## 验证证据

生产代码修改前，新用例已复现 12 项失败（11 项来源识别失败、1 项 XML 筛选缺失）；其余 4 项拒绝行为通过。最初新测试夹具遗漏 bootstrap 的工厂参数，已在建立上述基线前修正。

修复后执行：

```powershell
uv run pytest tests/persistence/v2/test_project_provisioning_xml.py tests/ui/test_start_center_multi_plugin.py::test_existing_translation_picker_includes_xml_and_preserves_selection -q
```

结果：**16 passed**（当时后端文件含 15 项测试）。

扩大回归：

```powershell
uv run pytest tests/persistence/v2/test_project_provisioning_xml.py tests/persistence/v2/test_project_provisioning.py tests/persistence/v2/test_project_provisioning_multiple_sources.py tests/application/projects/test_provisioning.py tests/ui/test_start_center_multi_plugin.py tests/ui/test_start_center_guided_project.py tests/ui/test_parse_migrations.py tests/ui/test_legacy_migration_safety.py tests/contracts/io/test_migration_import.py -q
```

结果：**145 passed，1 failed**。唯一失败为 `test_choose_multiple_plugins_creates_one_project_and_loads_every_collection` 的旧断言：期待 slot key 等于文件路径，实际为 `source-0` 等来源身份；修改生产代码前同一命令子集已经复现该失败，本次未改其断言或相关行为。

补充歧义与读取异常用例后执行 `uv run pytest tests/persistence/v2/test_project_provisioning_xml.py -q`：**17 passed**。新增测试合计 18 项均有通过证据，各轮重叠测试不累加。

静态检查：`uv run --offline ruff check src tests`、`uv run --offline ruff format --check src tests` 均通过（2383 个文件格式通过）；`git diff --check` 通过。uv 缓存指向任务专用 `.tmp-xml-source-qa/uv-cache`。

首次沙箱执行因 uv 默认缓存权限失败；切换缓存后又遇到 faiss 原生库 Windows 句柄异常和测试运行错误，因此测试通过自动审批在正常本机环境执行。未修改依赖或绕过业务检查。

## 兼容性、遗留与未验证项

- 不要求迁移现有工程；未改变工作台 EET/XT 匹配规则、插件配对及原子提交机制。
- 此次仅统一 XML 入口支持，没有新增二进制 EET、Excel 或其他格式适配器；原有筛选中的其他格式不属于本次支持承诺。
- 已知遗留：上述 1 项旧 UI 断言失败。未执行全仓 pytest、真实用户文件验收、原生文件选择器人工操作或打包安装验证。
- 任务临时缓存目录收尾清理。任务开始前的 `.transbridge/`、分类上传代码、文档、测试和增量保持原样，未纳入本记录。
- 未执行 commit、push 或发布。
