# 词典粒度重构 QA 测试报告

**日期**: 2026-08-14
**范围**: translation-memory 词典粒度重构（FR15.1.6，Story 06-10）
**结果**: ✅ 通过（27/27 翻译记忆测试全绿，零回归）

## 测试执行

```
$env:QT_QPA_PLATFORM="offscreen"
python -X utf8 -m pytest tests/test_translation_memory.py tests/test_translation_memory_gui.py -q
→ 27 passed
```

## 覆盖范围

| Story | 测试点 | 数量 |
|-------|--------|------|
| S06 数据模型 | DictionaryEntry/Dictionary 字段往返、scope 校验、主键 sha1(mod名\|原文) scope 解耦、schema_version | 5 |
| S07 定位/命名/加载 | .tbdict 往返、同名 load 抛错、损坏文件现场保留、requires mod_file_id | 5 |
| S08 多词典查询仲裁 | 同名 mod 键命中即停、EXACT/STALE、文本兜底、键优先、多词典冲突仲裁（project 优先）、无冲突去重 | 6 |
| S09 scope/分享 | set_scope 切换 + 非法抛错、import_dict 同名/覆盖 | 3 |
| S10 GUI | SaveToDictionaryDialog mod 名推断/手填/scope 切换、面板空载、冲突对话框 | 3（GUI）+ coding 逻辑复用 |

## 已知限制（与本次重构无关）

全量测试 `pytest tests/` 出现 28 failed + 50 errors，均为**沙箱环境 `tmp_path` 写系统临时目录被拒**（`PermissionError: [WinError 5]`），分布在 ai_translator / paratranz / parser / smart_assistant 等未改动模块，属预存问题，非本次重构引入。

## 结论

- **零 Blocker / Critical / Major**：词典粒度重构的 backend + GUI 全部按计划落地，27 个测试覆盖新行为全绿
- **未破坏既有功能**：translation-memory 模块原有 19 测试已迁移至新模型并扩展至 27 个，全部通过
- **性能关注点**：Story 08 的多词典查询采用「同名 mod 命中即短路」策略，覆盖 mod 更新复译的最常见场景（同名 mod 键命中 O(1)），未命中的原文才走其余词典兜底
