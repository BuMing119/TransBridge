# ESM 对话树顺序与父 DIAL 元数据

- **状态**：已完成（2026-08-30，相关 QA 与 Vigilant 实物验证通过）
- **日期**：2026-08-30
- **对应请求**：按 ESM `group.children` 输出 `DIAL → TopicChildren/INFO`，并持久保存父 DIAL 与稳定源顺序
- **架构约束**：[ADR-017](../../docs/adr/017-translation-io-kernel-v2.md)

## 目标

- 插件解析按 ESM 物理树顺序输出记录，同一 DIAL 的可翻译字段和 INFO 保持连续。
- 每个插件条目在命名空间 metadata 中保存稳定源顺序；INFO 额外保存完整父 DIAL FormID。
- XT/EET 译文迁移、Collection 序列化和分类 ParaTranz JSON 不破坏该顺序或元数据。

## 非目标

- 不改变现有 EntryKey、legacy `id`/`key`、`context` 或 ParaTranz JSON schema。
- 不依赖 XT/EET XML 节点顺序重建插件树。
- 不承诺或改造 ParaTranz 服务端页面的默认排序。
- 不迁移或删除已有 ParaTranz 远端文件。

## 当前实现事实与约束

- `SSEPluginWithContext.extract_group_strings_with_context()` 先处理全部直接 Record，再处理全部子 Group；顶层提取完成后又按每条字符串自己的 FormID 全局排序。
- `InfoContext.dialogue_topic` 已含完整父 DIAL FormID，但 `TranslationEntry.create_from_plugin_entry()` 当前丢弃该字段。
- `TranslationEntry.metadata` 已由 ADR-017 定义为来源特有字段的持久化 envelope；`to_dict()`/`from_dict()` 已支持 JSON round-trip。
- `TranslationEntryCollection`、XT/EET 覆盖更新和分类 JSON writer 保持输入顺序。对旧条目必须保留无 metadata 的兼容路径。
- `plugin_with_context.py` 已超过 500 行，本次只做局部根因修复；插件 metadata 的构造和排序读取提取为独立小模块，避免继续扩张职责。

## Story 01：按插件树输出 DIAL 与 INFO

### 验收标准

- Normal DIAL group 中交替出现的 `DIAL Record` 与 `TopicChildren Group` 按 `group.children` 原顺序输出。
- 一个 DIAL 的直接字符串先于其 TopicChildren INFO；后续 DIAL 不得插入当前话题块。
- 不再对插件全部字符串执行全局 FormID 排序；非对话记录同样保持插件源顺序。
- 非本地化、本地化字符串解析和既有上下文提取行为不回归。

### 文件与实施

- `src/transbridge/parser/plugin/plugin_with_context.py`：把直接 Record 与子 Group 合并为一次按 child 顺序的遍历，移除最终 FormID sort。
- `tests/parser/test_plugin_tree_order.py`：构造最小 DIAL/TopicChildren 结构，覆盖 INFO FormID 大于后续 DIAL 的回归场景。
- `tests/parser/test_plugin_parser_integration.py`：保留现有真实插件 smoke，并补充 metadata/顺序不变量的可选实物验证入口。

## Story 02：持久化父 DIAL 与稳定源顺序

### 验收标准

- 所有插件条目包含整数 `plugin.source_order`；顺序在 `skip_empty=True/False` 之间以完整提取列表为基准，不因过滤空字符串重编号。
- INFO 在存在父话题时包含字符串 `plugin.parent_dial_formid`，值保留 `FormID|定义插件`；DIAL 与普通记录不伪造父 DIAL。
- metadata 经 `TranslationEntry.to_dict()`/`from_dict()` 保真，并在 XT/EET `replace()` 更新后保持不变。
- 分类对话 JSON 在同一任务的全部条目都有合法 `plugin.source_order` 时按该值恢复；旧数据缺失或混合 metadata 时保持 collection 原顺序。
- EntryKey、legacy key、context 和 ParaTranz 输出字段保持兼容。

### 文件与实施

- `src/transbridge/converter/plugin_entry_metadata.py`：集中定义命名空间 metadata key、构造/读取和兼容排序策略。
- `src/transbridge/parser/plugin_parser.py`：在过滤前的完整提取序列上分配 source order，并传入条目映射。
- `src/transbridge/converter/translation_entry.py`：映射插件 metadata，不改变身份字段。
- `src/transbridge/converter/translation_entry_collection_export.py`：仅在一个任务组的 source order 完整有效时恢复树序。
- `tests/parser/test_plugin_parser.py`、`tests/converter/test_translation_entry.py`、`tests/contracts/io/test_paratranz_facades.py`：覆盖映射、round-trip、排序和 legacy fallback。

## 依赖顺序与验证

1. 先修复结构遍历，再分配 source order，确保 metadata 记录的是正确树序。
2. 完成 metadata round-trip 后再让分类导出消费该值。
3. 先运行 parser/converter/ParaTranz facade 聚焦测试，再运行相关 integration 测试和 Ruff。
4. 使用用户提供的 Vigilant.esm 与 XT XML 做非提交型端到端验证：同一 DIAL 为单一连续块、迁移前后顺序相同、分类 JSON 锚点符合树序。

## 兼容、风险与回退

- 旧条目没有新 metadata 时不排序，输出行为保持原样。
- 新 metadata 不进入 legacy key 或 ParaTranz 核心字段，避免远端词条身份变化。
- 若真实插件表明 `group.children` 不能代表 XT/EET 的展示语义，回退 Story 01 的遍历改动并保留测试证据；不得恢复全局 FormID 近似排序。
- ParaTranz 已有文件可能继续按远端历史 ID 展示，本计划只保证本地 collection 与上传 JSON。

## 未决问题与假设

- 假设 sse-plugin-interface 保留 ESM group child 的物理顺序；该事实由依赖实现和 Vigilant 实物验证共同确认。
- 不创建独立 Story 细化文档；两个 Story 都可在当前任务内直接实现和验收。

## 完成与验证证据

- Story 01、Story 02 均已完成；EntryKey、legacy key、context 与 ParaTranz 核心 JSON 字段未改变。
- `pytest tests/parser tests/converter tests/contracts/io tests/paratranz -q -k "not test_paratranz_public_package_keeps_legacy_exports_after_lazy_loading"`：252 passed、1 deselected。
- `ruff check src tests` 与 `ruff format --check src tests`：通过（1054 files）。
- Vigilant 实物：7612 条插件词条；任务 `02126838` 共 397 条、142 个父 DIAL 块，碎片块从问题复现时的非零降为 0。
- XT 实物迁移：7612 条更新成功，迁移前后 key 顺序和 metadata 完全一致；分类 ParaTranz JSON 与任务树顺序一致。
- 已知无关基线问题：`test_paratranz_public_package_keeps_legacy_exports_after_lazy_loading` 仍因 `ParaTranzTermsService` 多出于 `__all__` 而失败，本次未修改对应公共导出。
