# 预存测试失败根因定位 — QA 报告

**日期**: 2026-08-13
**范围**: 全量测试套件（`pytest tests/`）
**结论**: **19 失败**（非文档记载的 2 预存失败）。全部为「测试用例问题」，**零真实代码缺陷**。

---

## 一、实际失败全景

全量运行 `python -m pytest tests/ -q`：**516 通过 / 19 失败 / 535 总计**。

| # | 测试 | 失败现象 | 根因 |
|---|------|---------|------|
| 1 | `converter/test_translation_entry.py::test_creat_from_eet_entry` | `entry.id` 断言 `"TestEdid"` 实为 `"TestEdid:12345|0~INFO:NAM1"` | id 复合格式变更 |
| 2 | `converter/...::test_create_from_plugin_entry` | `Mock` 无 `.index`/`.context`；`quest_formid_ori.split` 失败 | Mock 未配新属性 |
| 3 | `converter/...::test_try_update_from_xt` | 更新返回 `None` | entry.id/context 旧格式 |
| 4 | `converter/...::test_to_dict` | dict 多了 5 字段 | 数据模型新增字段 |
| 5 | `parser/test_plugin_parser.py::test_parse_plugin_basic` | `dummy.esp` 文件不存在 | patch 目标 `SSEPlugin` 已失效 |
| 6 | `parser/...::test_parse_plugin_without_skip_empty` | 同上 | 同上 |
| 7 | `parser/...::test_progress_callback_called` | `progress.call_count == 0` | 同上 |
| 8 | `parser/...::test_create_item` | `FakePluginString` 无 `index` 属性 | fixture 未随代码演进 |
| 9 | `parser/test_xt_parser.py::test_to_csv_file` | CSV 行多了 `index` 键 | CSV 新增 index 列 |
| 10 | `smart_assistant/test_execution_engine.py::test_execute_linear_graph` | `PermissionGuard: 未知工具 tool_a/b/c` | 护栏拒绝未注册工具 |
| 11 | `smart_assistant/...::test_execute_single_node` | `PermissionGuard: 未知工具 solo_tool` | 同上 |
| 12 | `writer/test_eet_xml_writer.py::test_apply_collection_updates` | `updated == 0` | 集合 key 旧格式不匹配 |
| 13 | `writer/test_plugin_writer.py::test_apply_collection_updates` | `'Mock' not iterable` | `extract_strings` 已改名 |
| 14 | `writer/...::test_apply_collection_skip_no_translation` | 同上 | 同上 |
| 15 | `writer/...::test_apply_collection_skip_same_translation` | 同上 | 同上 |
| 16 | `writer/...::test_apply_collection_no_updates` | 同上 | 同上 |
| 17 | `writer/test_xt_xml_writer.py::test_apply_collection_updates` | `updated == 0` | 集合 key 旧格式不匹配 |
| 18 | `writer/...::test_apply_collection_partial_match` | `updated == 0` | 同上 |
| 19 | `writer/...::test_apply_collection_with_none_translation` | `updated == 0` | 同上 |

---

## 二、根因分类

### 根因 A：陈旧单元测试（17 项）—— 数据模型/API 已演进，测试未同步

**证据链**：`git log` 显示 `tests/converter/`、`tests/parser/`、`tests/writer/` 下相关测试文件仅被一个提交触碰 —— `f1009b4 测试基础设施: 恢复被 /tests/ gitignore 屏蔽的文件`。而 `src/transbridge/converter/translation_entry.py` 最后一次实质改动为 `48eb35a 更新数据层与写入器集成`。

**结论**：这批测试此前被 `.gitignore` 屏蔽（不参与 CI/QA），`f1009b4` 恢复跟踪后，其断言仍停留在**旧数据模型**：

| 旧模型（测试断言） | 新模型（当前代码 `translation_entry.py`） |
|-------------------|------------------------------------------|
| `id` = `"edid:formid"` | `id` = `"{edid|None}:{formid}|{index}~{GRUP}:{REC}"`（`_build_eet_id` L62-65） |
| `key` = rec（如 `"INFO:NAM1"`） | `key` = 原来的 id 值（同 id 复合格式） |
| `context` = `None` | `context` = 原来的 key（如 `"INFO:NAM1"`） |
| 无 `index`/`string_id`/DSD 字段 | 新增 `string_id`、`form_id_with_plugin`、`dsd_type`、`dsd_index`、`editor_id` |
| parser 调 `SSEPlugin.from_file` | parser 调 `SSEPluginWithContext.from_file`（`plugin_parser.py` L47） |
| writer 调 `plugin.extract_strings()` | writer 调 `plugin.extract_strings_with_context()`（`plugin_writer.py` L71） |
| collection 按 `get(id)` 查 | collection 按 `get_by_key(entry_id)` 查（key 复合格式） |

这些变更均属**有意架构演进**（ADR-001 统一数据模型 + DSD 双向格式支持），代码注释明确标注「现在存储原来的 id 值」「现在存储原来的 key 值」。**当前源码行为正确，测试期望过时。**

### 根因 B：护栏默认拒绝未注册工具（2 项）

`test_execution_engine.py` 用 `FakeToolRegistry`（`get()` 对任意名字返回 spec）+ `FakeToolSpec(permission="read")`，但 `ExecutionEngine` 内置的默认 `PermissionGuard`（`guardrails/permission.py:19`）按**已知工具清单**校验，`tool_a/tool_b/tool_c/solo_tool` 不在清单内 → 拒绝。此为文档记载的「GuardMiddleware 测试注册问题」，属测试侧 fixture 与默认护栏行为未对齐。

---

## 三、修复清单（供 `/bm-dev` 执行）

> 原则：更新测试以匹配**当前正确代码**，不改业务代码。逐文件列出：

### 1. `tests/converter/test_translation_entry.py`（4 项）
- `test_creat_from_eet_entry`：断言改为 `id == "TestEdid:12345|0~INFO:NAM1"`、`key == <同 id>`、`context == "INFO:NAM1"`（status=99 分支同理 `TestEdid99`）。
- `test_create_from_plugin_entry`：`Mock` 需设置 `.index = 1`、`.context`（INFO 分支需 `.context.quest`）；断言 id 含 `|1~INFO:NAM1`、`context` 含 `INFO:NAM1|...`。建议改用真实 `PluginStringWithContext` 或补齐 Mock 属性。
- `test_try_update_from_xt`：entry 改用新 id 格式（如 `"TestEdid:FormID|1~INFO:NAM1"`）+ `context="INFO:NAM1"`、`key="INFO:NAM1"`。
- `test_to_dict`：期望 dict 增加 `string_id`、`full_form_id`、`dsd_type`、`dsd_index`、`dsd_editor_id` 字段。

### 2. `tests/parser/test_plugin_parser.py`（4 项）
- 三个 `@patch("...plugin_parser.SSEPlugin")` 改为 patch `SSEPluginWithContext`（导入自 `src.transbridge.parser.plugin.plugin_with_context`）；另需处理 `PluginStringsLookup.from_plugin`（可 `@patch` 返回 `None`）。
- `FakePluginString` 增加 `.index`、`.context`、`.string_id` 属性；`test_create_item` 断言 id/context 改用新格式。

### 3. `tests/writer/test_plugin_writer.py`（4 项）
- `make_fake_plugin()` 中 `plugin.extract_strings.return_value` → `plugin.extract_strings_with_context.return_value`；Mock 需 stub `find_string_subrecord`（当前代码 L94 调用）。
- `FakePluginString` 增加 `.index`；集合 entry 的 `id`/`key`/`context` 改用新复合格式，使 `get_by_key` 能命中。

### 4. `tests/writer/test_eet_xml_writer.py` + `test_xt_xml_writer.py`（4 项）
- 集合 entry 的 `id` 改为 `"{edid}:{formid}|{index}~{REC}"` 新格式，`key`=id、`context`=rec，匹配写入器 `get_by_key` 的 key 构造逻辑。

### 5. `tests/parser/test_xt_parser.py::test_to_csv_file`（1 项）
- CSV 行键集合期望加入 `"index"`。

### 6. `tests/smart_assistant/test_execution_engine.py`（2 项）
- 让 `PermissionGuard` 认识测试工具：在 `setUp` 中向护栏注册 `tool_a/tool_b/tool_c/solo_tool`（或改用真实已注册工具名）。若护栏默认拒绝未知工具属设计意图，则测试需显式注册；若属过严，需在 `ExecutionEngine` 构造时关闭/注入测试护栏 —— 此项涉及一处**设计决策**，建议 `/bm-dev` 修复时确认。

---

## 四、审查结论

- **方案一致性**: ✅ 当前源码行为与 ADR-001/DSD 架构演进一致，无超范围改动。
- **代码质量**: ✅ 业务代码无缺陷；问题全在测试侧陈旧断言。
- **安全性**: ✅ 无安全影响。
- **严重级别**: **Major**（测试套件产生 19 个假阴性失败，侵蚀回归测试可信度，阻塞「全绿」验收）。

### 发现的问题（均已分类）
- [ ] 17 项陈旧单元测试需更新（根因 A，纯测试侧）
- [ ] 2 项 execution_engine 护栏测试需对齐（根因 B，含 1 处设计决策待确认）

### 签名
**需修复**（非 Blocker/Critical，测试侧更新；可移交 `/bm-dev` 直接修复）
