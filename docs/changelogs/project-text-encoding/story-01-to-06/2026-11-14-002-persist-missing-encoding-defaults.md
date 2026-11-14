# 老项目保存时补齐缺省编码配置

- 日期：2026-11-14。
- Epic / Story：`project-text-encoding` / S01（项目编码设置可保存与恢复），回归覆盖关联 S06。
- 关联：[计划](../../../../plans/project-text-encoding/plan.md)、[FR33.1](../../../requirements/project-text-encoding.md)、[ADR-055](../../../adr/055-project-text-encoding.md)、[此前六项功能实施记录](2026-11-14-001-project-text-encoding.md)。
- 状态：本次补充实现与相关验证完成；不变更 ADR 提议及计划设计草稿状态。
- 归属：依据本次“补充缺省配置”请求、实际编辑上下文及目标文件 Git 差异，仅记录下述九个文件中的本次变化。工作区其余未提交修改以及此前六项功能不重复归入本记录；暂存区无变化。

## 行为与兼容性

此前缺字段项目可按 UTF-8 解释，但序列化仅保留已存在的编码字段；无其他修改的保存会直接返回，项目修订未变时存储层也跳过项目配置写入，因此默认解释不会自动落库。

现在打开项目仍不写入。下次保存会把缺失的 `text_input_encoding`、`text_output_encoding` 补为 `utf-8`，即使没有修改译文也执行；已有编码值保留，不能继承此刻的程序默认。补齐不读取源文件，不改变已存原文、译文、翻译状态或项目修订。事务提交成功后同步内存，随后无修改保存恢复为无操作。保存结果核验检查数据库字段确实存在，不能仅以按缺省解释后的值相等判定写入成功。

本次不改变项目格式主版本，没有运行用户真实项目的批量迁移；已有项目在实际保存时获得补齐。

## 逐文件变化

以下路径均相对于仓库根目录，操作类型均为修改；部分文件由此前主功能新增，本次只修改其中相关段落或测试。

1. `src/transbridge/persistence/project_store/records.py`：`project_settings()` 将 `TextEncodings.from_project_data()` 得到的两项规范值全部写入 settings，移除仅保留已有键的过滤，从统一序列化边界补齐缺失配置。
2. `src/transbridge/application/projects/save_changes.py`：新增 `project_needs_encoding_defaults()` 和 `_with_encoding_defaults()`；`acknowledge_saved_changes()`、`rebase_committed_variant_save()` 在提交成功后的确认路径同步内存配置，避免后续重复补写，并保留现有值。
3. `src/transbridge/application/projects/lifecycle.py`：`save_active()` 仅在项目既无修改、也不缺编码字段时提前返回，让无译文修改的老项目也能正常保存配置。
4. `src/transbridge/persistence/project_store/lifecycle.py`：`ProjectLifecycleStore._save()` 在项目缺编码字段时调用 `_write_project()`，即使项目修订未变也将配置落入现有事务；补齐本身不触发来源保存。
5. `src/transbridge/persistence/project_store/save_verification.py`：`save_matches()` 对数据库读回的项目先检查字段完整性，阻止缺字段项目经规范化后被误认为已完成持久化。
6. `tests/application/projects/test_project_text_encoding.py`：新增 `test_saving_clean_legacy_project_persists_only_missing_encoding_defaults`，参数化覆盖两项均缺失、仅保留输入 cp1252、仅保留输出 cp936 共三种情况。通过真实 SQLite 项目验证：打开不补写、改变程序默认不影响补齐、源文件删除后仍可保存、保存前后结果核验正确、翻译快照及项目修订不变、成功后项目保持干净、再次保存数据库字节不变。
7. `docs/requirements/project-text-encoding.md`：FR33.1 验收补充“下次保存补齐缺失字段”，明确无其他修改也可保存、已有值保留、不读取程序默认及不重读来源。
8. `docs/adr/055-project-text-encoding.md`：设置归属段落补充打开与保存边界、提交后内存同步、重复保存无操作和数据库字段完整性核验。
9. `plans/project-text-encoding/plan.md`：S01 验收同步缺省配置的保存规则与状态保留要求。

本次归档另新增此记录，并仅更新 changelog、docs、plans 索引中直接相关的入口；既有增量文件保持不变。

## 验证证据

以下为本次实现阶段实际执行的最终验证结果，归档阶段不重复运行代码测试。

### 应用与持久化回归

```powershell
uv --cache-dir .tmp-encoding-defaults-20261114/uv-cache run --offline python -c "import tempfile; from pathlib import Path; p=Path('.tmp-encoding-defaults-20261114'); tempfile.tempdir=str(p.resolve()); import pytest; raise SystemExit(pytest.main(['tests/application/projects/test_project_text_encoding.py','tests/application/projects/test_lifecycle.py','tests/persistence/v2/test_lifecycle_save_transactions.py','tests/persistence/project_store','-q','--tb=short','--basetemp',str(p/'pytest3')]))"
```

结果：**125 passed**，3 条既有依赖弃用警告。覆盖项目编码、项目生命周期、生命周期保存事务及 project_store 测试。Windows 沙箱阻止现有原子发布所需的 `os.link`，获准后在工作区临时目录执行上述离线测试；仅使用合成项目数据。

### 静态与差异检查

```powershell
uv --cache-dir .tmp-encoding-defaults-20261114/uv-cache run --offline ruff check src tests
uv --cache-dir .tmp-encoding-defaults-20261114/uv-cache run --offline ruff format --check src tests
git -c core.safecrlf=false diff --check
```

结果：Ruff 检查通过；格式检查通过（2680 个文件无需格式化）；差异空白检查通过。任务临时目录 `.tmp-encoding-defaults-20261114` 已核验归属、清理并确认不存在。

## 遗留与验证边界

本次补齐配置功能遗留项：无。本次未重跑全量 pytest，未做可见桌面或用户真实项目验收；不以本次 125 项通过替代此前主功能记录中的广域测试及限制，也不消除其中已记录的 5 项既有失败。未提交或推送 Git，未修改依赖、锁文件或安装器。
