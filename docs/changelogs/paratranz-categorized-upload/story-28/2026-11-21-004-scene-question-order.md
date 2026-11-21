# S28 场景与问答的阅读顺序

2026-11-21 · paratranz-categorized-upload · [实施计划](../../../../plans/paratranz-categorized-upload/plan.md#s28-纯插件对话阅读布局) · [分类上传说明](../../../dev/paratranz-categorized-upload.md)

## 阅读规则

同一任务里，先列当前插件各 SCEN 的对话动作所引用的 DIAL。场景按插件记录顺序，话题按动作引用顺序，共用话题归入首次引用处。剩余话题沿用已有的跳转和分支阅读顺序。DIAL 有非空 FULL 时，默认回复排在独立选项之前。同一 INFO 内 RNAM 排在 NAM1 之前，多条 NAM1 保持字段先后。缺少可见 FULL 时，INFO 各组保持原有次序。

## 实现

- `parser/plugin/scene_links.py` 提供共享的 Skyrim 场景动作读取逻辑。`dialogue_catalog.py`、`dialogue_order_binary.py` 和 `dialogue_order_evidence.py` 分别接入导航目录、插件字节与已解析记录，并保存有序场景引用。
- `application/io/plugin_dialogue_order.py` 和 `persistence/project_store/archive_native.py` 读取旧版顺序元数据及新版场景证据。旧工程优先利用已存插件字节，也能从已存场景目录恢复引用；只有旧图时仍可排列问答。
- `application/io/dialogue_order_projection.py` 在完整来源上决定场景和话题位置。`dialogue_question_order.py` 在话题内按 INFO 身份重排问答。`dialogue_reading_view.py` 和 `dialogue_reading_cache.py` 保存相同的话题顺序并计入场景缓存大小。
- `application/dialogue/index.py` 和 `loading.py` 将完整来源秩投影到可见话题、台词及场景导航。`ui/source_hydration.py`、`ui/coordinators/project_coordinator.py`、`ui/workbench/dialogue_reading_order.py`、`persistence/project_store/writes.py` 和 `scripts/benchmark_dialogue_order.py` 捕获 FULL、RNAM 原文是否非空，供后台判定默认回复和独立选项。
- `persistence/project_store/reading_order_refresh.py`、`repository.py` 和 `paged_reads.py` 处理旧工程首次按阅读顺序分页的场景。可写工程使用同一事务更新派生行号和证据版本。只读工程继续使用已保存的分页行号。
- 已同步 FR3.4.7、ADR-047、S28 计划和分类上传开发说明。排序规则应用于本地阅读、分类上传编号与分类 JSON。云端沿用现有原文更新流程。

## 回归与真实文件

- `tests/parser/test_scene_order_evidence.py`、`test_dialogue_order_evidence.py` 和 `tests/persistence/project_store/test_dialogue_catalog.py` 覆盖已解析记录、压缩记录、存储、旧版元数据及异常输入。`tests/persistence/project_store/test_reading_order_refresh.py` 覆盖旧行号刷新、只读及事务回滚。
- `tests/contracts/io/test_dialogue_question_order.py` 和 `test_dialogue_sequence_integration.py` 覆盖空白 FULL/RNAM、多回复、部分选择、跨场景共用话题、未归组话题跳转及分类 JSON 与上传记录的一致性。
- 从开发工程当前登记的 3DNPC 来源读取 53,648 条词条，以其已存目录恢复 1,180 个 SCEN。Fire and Blood 的 `00044023` 至 `00044028` 依次是话题 FULL、默认 INFO 的两句 NAM1、另一 INFO 的 RNAM 及其两句 NAM1。在工程数据库的临时副本上，分页工作台能按同样顺序读回六条原文。
- HearthFires 备份的临时副本中，任务 `020042B4` 先列场景 `0201652E` 的话题，再列 `0201652D` 的话题。两份插件均核对了非空子集沿用完整 TB2 编号。验证在本地完成。
- pytest 联合运行工程存储、I/O 契约、ParaTranz 和对话相关用例，共 1,668 项通过，16 条弃用警告。工程重开导出的保真测试按现有 TB2 格式断言完整传输 key，并继续逐字段核对远端 ID、译文、状态和扩展数据。对 `src`、`tests` 和对话排序基准脚本的 Ruff 检查与格式检查均通过。
