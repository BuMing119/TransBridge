# XT Windows 码页优先读取与旧库降级

- 日期：2026-11-20。
- Epic / Story：`project-text-encoding` / S13；[实施计划](../../../../plans/project-text-encoding/plan.md#s13xt-windows-码页优先与旧库降级2026-11-20)、[FR33.2 / FR33.5](../../../requirements/project-text-encoding.md)、[ADR-055](../../../adr/055-project-text-encoding.md)。
- 状态：用户通过 bm-pilot 明确授权“先按 XT 来，不行再切换原来的降级模式”；本地实现与相关 QA 完成。未获取实际用户 ESP，未运行 XT 可执行程序验收。
- 基线：本轮从 2026-11-20 指令开始；2026-11-19 的旧库兜底、对应测试、FR33/ADR 修订及 [002 记录](../story-07-to-12/2026-11-19-002-plugin-inline-reading-compatibility.md)已经存在于未提交工作区。以下仅记录本轮新增的 Windows 转换、策略版本、入口切换与验证，不重复归属前次 `_historical_text()` 等实现。工作台、开始中心、菜单及其他既有变更不属于本记录；目标暂存区无差异。没有提交或推送 Git。

## 原因与交付行为

此前“XT 风格”只复现了 UTF-8 加语言备用的顺序，备用直接使用 Python codec。XT 的 [固定源码](https://github.com/MGuffin/xTranslator/blob/9aa38d60860273401f8bb0dd1557c82a295c41b3/TESVT_fstreamSave.pas#L132-L181)使用 `SetCodePage` / `string(rbs)` 路径。[English 配置](https://github.com/MGuffin/xTranslator/blob/9aa38d60860273401f8bb0dd1557c82a295c41b3/Data/SkyrimSE/codepage.txt)为 UTF-8 / 1252。Windows CP1252 将 `0x81` 映射为 `U+0081`，Python 的 `cp1252` 严格 codec 则拒绝；旧库会继续尝试 CP1251 得到 `U+0403`。三者不能只按“是否读取成功”视为一致。

本轮新默认 `xtranslator-v2` 先严格读 UTF-8，再以源语言备用码页调用 Windows `MultiByteToWideChar`（flags=0）。插件该路径仍失败时，复用原库解码结果降级并报告上下文。没有覆盖已有 `xtranslator-v1` 的解释；v1、legacy、strict 来源继续按各自保存的版本重现。显式非 UTF-8 仍直接严格读取；Strings 共享原生语言备用，保留其独立历史规则，不套用插件的旧库码页列表。

输出仍全量严格编码并回读核验。删除“文本和编码名不变就复用旧字节”的 inline 编码快路径，避免 Windows 能读、Python 严格目标 codec 不能写的字符绕过输出检查。读取和普通源 dump 保留原始字节，失败输出不发布。没有新依赖、项目字段、缓存或 hash，没有修改用户翻译数据；无自动工程迁移，明确更新来源才应用新策略。

## 逐文件变化

### 生产代码

- **修改 `src/transbridge/config/text_encoding.py`**：保留 `XTRANSLATOR_READ_POLICY = xtranslator-v1`；新增 `XTRANSLATOR_WINDOWS_READ_POLICY = xtranslator-v2`、集中默认 `DEFAULT_READ_POLICY`，扩展策略白名单并切换 `TextReadOptions` 的新请求默认。
- **新增 `src/transbridge/parser/windows_codepage.py`**：以 ctypes 绑定 Windows 原生转换，分别查询输出长度、分配缓冲和转换；不通过 NUL 终止长度截断显式载荷。空载荷返回空文本，原生调用失败保留 `WinError`；非 Windows 环境明确报告 decoder 不可用。Python 的 `codecs.code_page_decode` 包装器在本轮希腊文与日文对照中仍有差异，因此未用它替代 API。
- **修改 `src/transbridge/parser/text_reader.py`**：UTF-8 和语言备用选择仍集中于 `decode_game_text()`；v2 的备用交给 Windows 边界，v1 继续 Python codec，legacy Strings 和 strict 行为保持独立。
- **修改 `src/transbridge/parser/plugin/text_subrecords.py`**：将已有旧库兜底扩展到 v2；Windows decoder 不可用或调用失败时也可按受控异常降级，保留失败原因和记录定位。保留原生 replacement 的诊断信息。`encode_text()` 删除原字节快路径，全部先严格编码、校验再修改，不绕过原有大小/NUL/往返保护。
- **修改 `src/transbridge/parser/localized_string_records.py`**：为原生 `LookupError` / `OSError` 补上 String ID 上下文，保留原始异常原因，使 adapter 按既有解析失败结果报告。
- **修改 `src/transbridge/parser/plugin_parser.py`**：默认参数改为集中默认策略，继续由请求及来源快照显式传递版本。
- **修改 `src/transbridge/parser/plugin/binary_io.py`**：文件/流入口默认统一，避免顶层省略默认参数时回到 v1。
- **修改 `src/transbridge/parser/strings_file.py`**：文件、字节、目录与插件关联 lookup 的默认策略统一切换。
- **修改 `src/transbridge/application/io/text_source_options.py`**：新请求无显式策略时选 v2；`snapshot_read_options()` 的历史明确值、缺事实 legacy、明确编码 strict 判定保持不变。
- **修改 `src/transbridge/application/io/initial_translation.py`**：初始译文准备使用集中默认。
- **修改 `src/transbridge/application/io/legacy_migration.py`**：迁移准备的默认策略统一，显式历史策略仍可透传。
- **修改 `src/transbridge/application/io/migration_import.py`**：结构化迁移导入的默认策略统一。
- **修改 `src/transbridge/application/io/open_translation_file.py`**：打开翻译来源及可选供体的默认策略统一。
- **修改 `src/transbridge/application/io/translation_import.py`**：译文导入的默认策略统一。
- **修改 `src/transbridge/ui/coordinators/parse_migration.py`**：无 Qt 的迁移准备函数改用集中默认，删除旧版本号字面量；未新增或修改界面操作。

### 测试、规范及计划

- **新增 `tests/parser/test_xtranslator_reading.py`**：七种语言码页的 0～255 字节载荷直接对照 Windows API；单独覆盖 CP1252 的五个未定义字节、有效 UTF-8 优先、新默认与保存 v1、显式/strict 隔离、原生 decoder 不可用及无备用时的旧库降级、同 codec 写出不能绕过严格校验、三种外部 Strings 的 native 与旧策略区别。
- **修改 `tests/parser/test_plugin_text_encoding.py`**：将前次旧库降级及 replacement 测试明确绑定保存的 v1，使其继续验证旧来源重现，而非因新默认变化丢失历史覆盖。
- **修改 `tests/integration/publish/test_plugin_text_compatibility.py`**：此文件在本轮前已存在且为 untracked；本轮扩展夹具为同时记录历史与 Windows 预期文字，参数化 v1 / v2 的严格发布与源快照绑定，默认建项/保存重开验证 v2 的文字及快照版本。`3DNPC.esp` 是合成夹具名，不是用户实际文件。
- **修改 `tests/config/test_text_encoding.py`**：语言/编码的读取选项断言跟随集中默认。
- **修改 `tests/smart_assistant/tools/test_source_import_authority.py`**：助手成功导入的快照版本断言更新为 v2，沿用先前显式错误编码不登记来源的保护。
- **修改 `docs/requirements/project-text-encoding.md`**：规范正文明确 v2 的原生映射、XT 优先及旧库降级，v1 历史事实与全量严格输出边界，删除与本轮合同矛盾的笼统“同名 codec 等价”解释。
- **修改 `docs/adr/055-project-text-encoding.md`**：区分 v1 / v2 的转换语义和不可用环境，记录独立 Windows 边界及旧来源不升级的原因，补齐同编码写出的严格校验。ADR 仍为提议，不将源码与 API 对照记成 XT 可执行程序验收。
- **修改 `plans/project-text-encoding/plan.md`**：新增 S13 的范围、阶段计划、文件落点、验收、环境与迁移边界，并回填本轮实际结果；S07～S12 的历史测试记录保留。
- **新增本记录**；仅在 `docs/changelogs/INDEX.md` 和 `plans/INDEX.md` 的直接相关入口增加链接，保留已有未提交索引内容及所有旧增量文件。

## 本轮实际验证

各组有包含关系，不把下列结果累加为独立总数。测试沿用现有 uv 环境及锁文件；`--no-cache --no-sync` 避开受限 uv 缓存且不更新依赖。项目发布测试在允许 Windows 硬链接的沙箱外执行，没有 monkeypatch 发布保护。

### 测试驱动与原生对照

```powershell
uv --no-cache run --no-sync pytest tests/parser/test_xtranslator_reading.py -q
```

- 仅新增回归、未改生产代码时：退出码 1，12 failed、3 passed；确认原严格 codec 和旧默认未满足新合同。
- 首次采用标准库 `codecs.code_page_decode` 后：退出码 1，2 failed、13 passed。希腊文/日文字节的 Python replacement 与 API flags=0 结果不一致；没有通过放宽断言掩盖差异。
- 改为直接 Windows API 后：退出码 0，15 passed。随后扩展到 21 个用例，包含在下列聚焦及广域组中。

```powershell
uv --no-cache run --no-sync pytest tests/parser/test_xtranslator_reading.py tests/parser/test_plugin_text_encoding.py tests/config/test_text_encoding.py -q
```

退出码 0：56 passed，0.43 秒。

### 解析、I/O、来源、工程与发布

```powershell
uv --no-cache run --no-sync pytest tests/parser tests/writer tests/config tests/contracts/io tests/integration/publish tests/application/projects/test_project_text_encoding.py tests/application/projects/test_source_update.py tests/application/projects/test_source_update_plugin.py tests/persistence/project_store/test_source_extraction.py tests/persistence/v2/test_project_provisioning.py tests/persistence/v2/test_project_provisioning_multiple_sources.py tests/smart_assistant/tools/test_source_import_authority.py -q --tb=short
```

退出码 0：**1503 passed、3 skipped**，92.11 秒，7 项既有弃用 warnings。覆盖新旧读取、三类 Strings、BSA、源快照重建、来源更新、工程保存重开、严格输出回读及原子发布。

```powershell
uv --no-cache run --no-sync pytest tests/ui/test_parse_migrations.py tests/ui/test_structured_migration_import.py tests/ui/test_legacy_migration_safety.py tests/application/fomod/test_plugin_publishing.py -q --tb=short
```

退出码 0：**71 passed**，9.95 秒，4 项既有弃用 warnings；覆盖迁移协调器和 FOMOD 入口的默认策略联动。

### 最终静态检查

```powershell
uv --no-cache run --no-sync ruff check src tests
uv --no-cache run --no-sync ruff format --check src tests
git -c core.safecrlf=false diff --check
```

Ruff 检查与格式检查最终退出码 0，2702 files already formatted；修复过程中的导入顺序和长行已在本轮文件内修正。Git 差异空白检查及新增记录链接/编号检查在归档收尾执行。

## 遗留项与范围限制

- 本次实现范围无已知待实现项。
- 未取得实际用户 `3DNPC.esp`，未进行该文件的直接读取、导出或与 XT 输出逐字段比较；源 FormID / NAM1 / subrecord / 偏移场景由合成插件复现。
- 未运行 XT 可执行程序、可见桌面全流程、真实用户工程原位迁移或全仓 pytest。原生 API 一致不代表整个 XT 的全部配置、格式和损坏文件容错都已复现；显式非 UTF-8 严格读取及输出无损约束仍是本项目边界。
- Windows decoder 不可用时，插件会告警并用旧库降级；Strings 明确失败。跨环境降级若与已有 v2 baseline 不同，仍由来源匹配阻止发布，不覆盖已存原文。需要应用新规则的既有来源继续通过“更新来源”。
- 没有构建发行包、提交或推送 Git；未新增任务专用 QA 目录或把测试源文件纳入版本控制。

## 归档收尾复核

新增记录的本地路径、计划锚点、两个索引链接及 Story 内 `001` 编号唯一性检查通过；新增 Python/Markdown 文件尾换行与行尾空白检查通过；`git -c core.safecrlf=false diff --check` 退出码 0。已复读本记录并回填 S13 阶段状态，未改写前次增量文件。
