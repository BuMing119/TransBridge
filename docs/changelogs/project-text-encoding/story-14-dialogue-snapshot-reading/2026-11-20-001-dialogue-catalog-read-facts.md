# 对话目录重建绑定快照读取事实

- 日期：2026-11-20。
- Epic / Story：`project-text-encoding` / S14；[实施计划](../../../../plans/project-text-encoding/plan.md#s14对话目录重建遵循快照读取事实2026-11-20)、[FR33.5 / FR33.7](../../../requirements/project-text-encoding.md#fr335-历史来源保持读取事实)、[ADR-055](../../../adr/055-project-text-encoding.md#2-当前读取策略与历史事实分离)。
- 状态：用户先指定 bm-plan 的最小修复范围，随后通过 bm-pilot 授权实施至验证和记录；本地实现与相关 QA 完成。
- 归属证据：本轮启动的 Git 状态中 `application/dialogue/loading.py` 和 `tests/application/test_dialogue_loading.py` 均无未提交修改，前者修复前的目标 diff 也为空。当前这两个文件的全部差异可归属 S14。文本编码 S13、开始中心、工作台、菜单及其他既有未提交修改均不归入本轮；计划及索引仅做本 Story 的状态和链接更新。目标暂存区无差异，没有提交或推送 Git。

## 问题与修复结果

对话目录重建虽然共用 `PluginBinaryIO`，但在捕获快照字节的分支直接调用 `SSEPluginWithContext.from_stream()`，没有携带保存的编码、源语言和读取策略。这会将明确 CP936、保存的 v1、严格来源或无读取事实的旧来源，交给当前 UTF-8 / English / v2 默认解释。目录主要消费结构关系，不直接改写翻译原文，但仍应遵守已有来源的读取事实。

本轮直接复用 `snapshot_read_options(source)`，不复制默认值或历史判定逻辑。快照明确事实、缺策略但有编码时的 strict、编码事实也缺失时的 legacy 均由现有 helper 决定。仍只读捕获字节，活 plugin 对象分支、缓存、预览、取消和错误传播保持原边界；不重开源路径、不读外部 Strings、不创建临时插件，不新增项目字段或迁移。

## 逐文件变化

1. **修改 `src/transbridge/application/dialogue/loading.py`**：增加 `snapshot_read_options` 导入，在 `DialogueIndexLoader.build()` 的 `SourceSnapshot` 分支把该 helper 的返回值传入 `from_stream()`。生产差异仅此，未改公开参数、返回模型、锁、来源选择或缓存成功提交位置，未放宽严格输出和发布验证。
2. **修改 `tests/application/test_dialogue_loading.py`**：复用真实插件二进制构造，增加 `encoded_snapshot()` 与观察目录输入的 `catalog_texts` fixture。包装器观察真实 parser 中的 FULL 文字后仍调用原始 `read_dialogue_catalog()`，不以参数字典 mock 代替解码；fixture 禁止 `Path.open()`，验证只消费捕获字节。新增 11 个参数展开用例，覆盖明确 CP936、俄语源语言、保存 v1/v2 对同一 `0x81` 的区别、同字节变更读取事实重建缓存、缺事实 strict/legacy、严格失败与原始异常原因、失败未安装缓存及可重试、非法已有读取事实、无字节时复用已解析 plugin。验证成功读取后快照与词条不变，保留原有预览/取消/阅读范围测试。
3. **修改 `plans/project-text-encoding/plan.md`**：回填 S14 的当前行为、阶段状态和本轮验证证据，不复用 S13 的测试数字，也不扩大到统一所有业务入口的重构。
4. **修改 `plans/INDEX.md`**：将 S14 从草稿规划条目移至当前实施成果，链接本记录，保留其他既有未提交索引内容。
5. **新增本记录并修改 `docs/changelogs/INDEX.md`**：增加 S14 入口。已有 S13、S08 等增量文件未改写；未新增独立 QA 报告。

## 本轮实际验证

使用现有 uv 环境与锁文件，`--no-cache --no-sync` 避开受限缓存且不更新依赖。以下两组测试目标不同，共 65 项；不将此前 S13 或入口审查的结果计入本轮。

### 先复现，再修复

```powershell
uv --no-cache run --no-sync pytest tests/application/test_dialogue_loading.py -q --tb=short
```

- 最初只新增测试时：退出码 1，10 failed、13 passed。其中一个失败为新测试构造 `content=None` 的快照时遗漏必须的 `lease_id`；补上测试租约后再次运行。
- 生产代码仍未修改、有效回归就绪时：退出码 1，**9 failed、14 passed**。失败体现实际文字被默认解码，以及严格或非法快照没有按保存事实失败。
- 修复参数透传后：退出码 0，**23 passed**，0.13 秒。原有 12 项测试继续通过，新增 11 项全部通过。

### 对话索引、来源发布和后台交付

```powershell
uv --no-cache run --no-sync pytest tests/application/test_dialogue_index.py tests/application/projects/test_dialogue_order_publication.py tests/ui/test_dialogue_lazy_index.py tests/ui/test_dialogue_filter_context_loading.py tests/ui/test_dialogue_worker_lifetime.py -q --tb=short
```

退出码 0：**42 passed**，3.08 秒；3 项既有 SWIG 类型弃用 warnings。命令在允许项目数据库 Windows 硬链接的沙箱外执行，没有修改测试中的项目发布保护。

### 静态检查与复审

```powershell
uv --no-cache run --no-sync ruff check src tests
uv --no-cache run --no-sync ruff format --check src tests
git -c core.safecrlf=false diff --check
```

本轮实际均退出码 0；Ruff 检查通过，格式检查为 2702 files already formatted。没有为了修复无关静态问题扩展代码范围。

主会话审查最终两文件 diff；独立只读复审未发现可操作问题，确认快照参数、真实字段解码、缓存/取消和异常边界一致。复审未另运行测试，不计为额外通过数。新增记录的相对路径、锚点、Story 内编号及直接索引在归档收尾复核。

## 遗留项与未验证范围

- 本 Story 实现范围内无已知待实现项。
- 没有实际用户 ESP 或 XT 可执行程序联验、可见桌面完整操作，也未重跑全仓 pytest 或 S13 的广域组。本轮使用真实格式的合成插件、应用测试及现有后台交付测试验证参数遗漏，不能据此宣称所有上层业务入口都已重构为同一个函数。
- 不涉及工程格式迁移、依赖/锁文件变更或用户数据写入；没有构建发行包、提交或推送 Git。未新增任务专用 QA 目录，测试夹具以内存字节构造，未提交临时插件。

## 归档收尾复核

记录本地路径、计划/规范锚点、三个计划或索引入口、Story 内 `001` 编号唯一性和新增记录行尾空白检查通过；`git -c core.safecrlf=false diff --check` 退出码 0。复读记录后，S14 的开发、QA 与归档阶段均已回填完成，未改写其他 Story 的历史增量。
