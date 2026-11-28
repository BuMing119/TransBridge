# 工程保存时只将变更的词条存入词典

2026-11-28 · Epic `translation-memory` · S11 保存 · [实施计划](../../../../plans/translation-memory/plan.md) · [FR15.1.2](../../../requirements.md#fr151-翻译词典)

以前每次点击保存，后台都要从分页工程读取当前来源的全部词条，再由 SQLite 逐条判断是否已有相同译文。现在词典与工程第一次同步时仍完整读取，之后依据最近一次成功入库的工程版本判断要读哪些词条。如果译者没有再修改这个来源，后台可以跳过全部词条读取。其他工程后来修订过的同一条译文，也不会因为原工程又点了一次保存而被覆盖。

## 实现

- `application/projects/lifecycle.py`、`gui_facade.py` 和 `save_result.py` 将已提交的变更键随工作台保存结果传出。通过 `ui/main_window.py` 显式请求，复用保存操作已经收集的键。其他调用保持原有返回结构。
- `ui/shell/project_save.py`、`saved_dictionary.py` 和 `dictionary_save_changes.py` 汇集手动与定时保存的结果。只有显式要求入库的保存才启动写入。定时保存的变更保留到下次入库，后台只取当前来源的键并去重。最多保留最近 64 批、合计 10 万个键。超过范围后仍可通过完整读取恢复。
- `ui/project_rows.py` 用工程和翻译版本号读取已经提交的行。首次入库检查不同工程词条是否会写入同一个词典位置，检查范围包含暂未翻译和已移除的词条。有位置冲突的来源再次发生变更时完整读取，按原来全量处理的顺序决定存入哪条译文。
- `translation_memory/library.py` 和 `library_matching.py` 将词条更新与最近一次成功入库的版本号放在同一 SQLite 事务里。失败时一起回滚。全量写入直接绑定词条字段，省去逐条递归转换。已有 SQLite 词典在第一次写入时就地增加版本记录表，已有译文可继续查询。

切换来源、恢复旧修订或缺少连续的变更记录时，会按需重新读取当前来源。纯内存来源沿用保存时捕获的完整快照。「翻译 → 存入词典」仍可主动按原范围入库。工程格式、查询界面和套用规则均沿用原有设计。

## 10 万条分页工程实测

Windows 11，Core i9-14900HX，Python 3.12.12，SQLite 3.50.4。用工作区改动前留存的完整入库实现，与同一工作区优化后的实现配对测量。工作区基于 `9ed5dafe`，含尚未提交的词典功能。测试创建一个 SQLite 分页工程，内含同一插件的 100,000 条英译中 NPC 名称（`NPC_:FULL`，各自独立的 FormID，均已审校）。每轮从空词典开始，两种实现使用同一份工程数据，每轮交替执行顺序。依次测首次入库、原样保存、修改正中间一条译文后保存。临时基准命令为 `measure.py --rows 100000 --repetitions 5 --mode both`。

以下为五轮中位数及最小到最大值。计时从工程保存成功后开始，包含读取源词条、生成词典记录及 SQLite 提交。工程自身提交、工作台刷新和结果核对在计时范围之外。

- **首次入库**从 16.874 秒（9.298–17.853 秒）变为 15.736 秒（15.082–15.849 秒）。两边均读取 100,000 条。两组耗时范围重叠。
- **原样再存**从 16.590 秒（16.112–16.709 秒）变为 2.854 毫秒（1.162–4.046 毫秒）。读取量从 100,000 条降至 0 条。
- **只改一句**从 16.729 秒（16.505–17.292 秒）变为 15.811 毫秒（5.160–19.438 毫秒）。读取量从 100,000 条降至 1 条。

每轮核对三个场景实际变动的词典记录数，依次为 100,000、0、1。该轮结束时逐行比较新旧两个词典中所有 100,000 条记录的 15 个字段，并单独核对修改后的译文，全部一致。另一个对照组把词条预先放入内存，单独计量词典后端，三种场景的中位数依次从 6.939 秒、7.247 秒、6.618 秒变为 5.957 秒、3.557 毫秒、21.841 毫秒。

## 回归验证

174 项相关测试通过，覆盖保存回执、分页读取、定时保存后补写、写入失败后重试、来源冲突、词典格式升级、手动入库、套用条件及工作台保存。未运行全仓 pytest。全仓 Ruff 静态检查及格式检查通过。

```powershell
uv run --offline --no-sync --no-cache pytest -q --tb=short tests/test_dictionary_library.py tests/test_translation_memory.py tests/test_translation_memory_gui.py tests/application/projects/test_dictionary_apply.py tests/application/projects/test_save_projection_cost.py tests/ui/test_dictionary_save_changes.py tests/ui/test_project_save_dictionary.py tests/ui/test_dictionary_commands.py tests/ui/test_dictionary_settings_menu.py tests/ui/test_save_completion_state.py tests/ui/test_background_gui_operations.py tests/ui/test_project_open_guards.py tests/ui/test_projection_consistency_cost.py tests/smart_assistant/tools/test_dictionary_upload_authority.py tests/ui/operations/test_download_workbench_projection.py tests/ui/operations/test_categorized_upload_projection.py tests/integration/gui/test_project_paged_workbench.py::test_workbench_batch_replace_and_save_only_read_selected_keys
uv run --offline --no-sync --no-cache ruff check src tests
uv run --offline --no-sync --no-cache ruff format --check src tests
git -c core.safecrlf=false diff --check
```
