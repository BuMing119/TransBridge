# 恢复旧版插件内嵌文本读取兼容性

- 日期：2026-11-19。
- Epic / Story：`project-text-encoding` / S08 后续修复；涉及 S09 历史解释边界及 S12 回归验证，不新增 Story。
- 关联：[计划 S08](../../../../plans/project-text-encoding/plan.md#s08逐字段语言读取与完整严格转码)、[FR33.2](../../../requirements/project-text-encoding.md#fr332-按语言逐字段读取)、[ADR-055](../../../adr/055-project-text-encoding.md)、[前次实施记录](2026-11-14-001-language-reading-legacy-compatibility.md)。
- 归属依据：本会话开始时的只读 Git 状态与实际文件内容，以及本次逐文件编辑和最终 Git diff。范围为一个生产模块、三个测试文件和两份规范文档；工作区已有的开始中心、工作台、菜单、其他计划及其记录不属于本次修复。新增测试文件当前为 untracked，已直接读取其内容核验；目标文件没有暂存差异。

## 问题与修复边界

用户截图显示，创建 `3DNPC` 工程时，`3DNPC.esp INFO NAM1 (0529C112, subrecord 6)` 在字段内字节偏移 89 处抛出 `PluginTextEncodingError`，外层呈现 `PLUGIN_PARSE_FAILED`。用户确认插件以前可以读取，并明确要求恢复兼容性。

代码与依赖实现对比确认：旧版插件 decoder 依次尝试 UTF-8、Windows-1250、Windows-1252、Windows-1251，仍失败时使用最后一个 codec 替换无法解码的字符；新读取规则对默认英语只尝试 UTF-8 与 Windows-1252，因而会拒绝一部分以前可读的输入。合成字节对比复现了旧逻辑成功、新逻辑抛出 `charmap` 错误的差异。未获得实际用户 ESP，因此不将合成复现写为真实文件读取结论。

修复在插件内嵌文本边界完成：`xtranslator-v1` 且输入为 UTF-8 时，仍优先使用 UTF-8 与源语言备用规则；两者无法解释该字段时，复用 vendor 已完成的历史解码结果。此前成功读取的字段不改变解释。显式非 UTF-8 编码、`strict-v1` 及 Strings 的语言备用规则保持原行为。没有修改依赖、锁文件、工程存储格式或用户数据，无需迁移工程。

## 逐文件变更

1. **修改 `src/transbridge/parser/plugin/text_subrecords.py`**：新增 `_historical_text()`，提取既有 `legacy-v1` 的文字、实际 codec 与替换标记处理，供历史策略及本次兼容补救共用。`EncodedStringSubrecord.from_parsed()` 仅在默认 UTF-8 当前策略解码失败后复用历史结果，记录文件、记录/字段、失败字节位置、实际 codec 和是否使用 replacement 的警告，并提示文字不正确时选择明确输入编码。保留原始字段载荷、后缀与 `_source_replaced`，继续使用现有 fallback 计数和诊断；写出校验没有放宽。
2. **修改 `tests/parser/test_plugin_text_encoding.py`**：增加英语、中文下 Windows-1250、Windows-1251、replacement 三类回归，核对文字、实际编码、fallback 次数、警告定位、源字节不变及严格 UTF-8 写出回读。增加显式 Windows-1252 / 严格 UTF-8 仍失败，以及 replacement 输入不能绕过 Windows-1251 严格输出校验的测试。将原“中文无备用即失败”用例改为显式严格策略，保留 `legacy-v1` 原文解释断言。
3. **新增 `tests/integration/publish/test_plugin_text_compatibility.py`**：构造具有任务、话题与对话子组的真实格式合成插件，精确复现截图的 `INFO/NAM1`、FormID、subrecord 6 和字节偏移 89。三类异常字节分别验证默认 adapter 解析、来源策略与 fallback 诊断、中文话题写出、完整 fidelity / strict-v1 UTF-8 回读，以及工程创建、保存和重启服务后重开；原始插件字节保持不变。夹具名 `3DNPC.esp` 不代表使用了用户真实插件。
4. **修改 `tests/smart_assistant/tools/test_source_import_authority.py`**：调整插件导入回归矩阵，验证英语语言备用与中文历史兼容回退均可成功；显式 Windows-1252 读取无法表示的 Windows-1251 字节仍失败，失败前后来源登记和已有版本状态保持不变。保留来源语言、读取策略的快照断言。
5. **修改 `docs/requirements/project-text-encoding.md`**：在 FR33.2 的规范正文中明确插件 UTF-8 读取的末级历史兼容回退、可定位警告、建项/保存重开/严格输出验收，以及显式编码、严格模式和 Strings 的边界，移除与修复行为冲突的“历史规则只用于既有来源”限制。
6. **修改 `docs/adr/055-project-text-encoding.md`**：修订背景及 `xtranslator-v1` 合同，区分入站历史兼容和出站严格无损。明确此次只扩展原先拒绝的输入、保留载荷与替换标记、沿用 fallback 诊断，不改变成功读取结果及严格输出。ADR 的提议状态没有提升。

归档时新增本文件，并仅在 `docs/changelogs/INDEX.md`、`plans/INDEX.md` 的本 Epic 条目及 `plans/project-text-encoding/plan.md` 的 S08 状态处增加本记录链接。既有增量文件保持不变；索引中的用户已有无关修改予以保留。

## 实际验证

以下结果来自本次修复阶段的工具输出，归档阶段未重复运行测试；测试组有重叠，不累计为独立通过总数。

### 聚焦回归与完整路径

```powershell
uv --no-cache run --no-sync pytest tests/parser/test_plugin_text_encoding.py -q
```

- 仅添加回归、尚未修生产代码时：退出码 1，7 failed、19 passed，失败均涉及待恢复的历史兼容读取。
- 修复后：退出码 0，26 passed，0.21 秒。

```powershell
uv --no-cache run --no-sync pytest tests/parser/test_plugin_text_encoding.py tests/integration/publish/test_plugin_text_compatibility.py tests/smart_assistant/tools/test_source_import_authority.py tests/integration/publish/test_project_text_encoding_publish.py tests/application/projects/test_project_text_encoding.py tests/persistence/v2/test_project_provisioning.py tests/persistence/v2/test_project_provisioning_multiple_sources.py -q
```

- 最终运行退出码 0：**103 passed**，4.87 秒；3 项既有 SWIG 类型 `DeprecationWarning`。
- 最初在沙箱内执行 parser、新增 integration 和助手三个测试文件时：29 passed、3 failed、13 errors；项目提交被 Windows `os.link` 的 `PermissionError: [WinError 5]` 阻止。沙箱外使用同一项目环境验证，没有替换硬链接实现或跳过项目发布保护。
- 第一次沙箱外扩大组：100 passed、3 failed；三项失败来自新增测试误访问 `VariantEntryState.original`。将文字断言改为读取正式创建 hydration 中的条目，版本状态仍用于保存重开比对后，上述最终 103 项全部通过。没有因此修改生产存储 API。

### 静态检查

```powershell
uv --no-cache run --no-sync ruff check src tests
uv --no-cache run --no-sync ruff format --check src tests
git diff --check
```

最终均退出码 0：Ruff 检查通过，格式检查为 2700 files already formatted，Git 差异空白检查通过。修复过程中发现的新增长行已调整。使用 `--no-cache --no-sync` 沿用既有 uv 环境并避开受限缓存目录，没有安装或更新依赖。

## 遗留项与未验证范围

- 本次代码范围无已知待实现项。
- 未获取实际用户 `3DNPC.esp`，没有对该文件直接读取或发布验证；截图字段定位由合成插件复现，不能据此确认真实异常字节内容。
- 历史 replacement 只能保持旧版可读能力，不能恢复无法解释字节的原始字符；回退会记录警告，输入文件保持不变，输出依然执行严格编码与回读校验。
- 未运行全仓 pytest、可见桌面完整操作或外部 xTranslator/EET 联动。本次没有修复截图中错误文字显示区域的截断问题。
- 未提交或推送 Git，未构建发行包；其他未提交工作区变更及旧记录中的历史测试结论不归入本次成果。
