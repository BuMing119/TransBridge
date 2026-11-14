# S13：修复重复提取的构建标识冲突

- 日期：2026-11-13
- Epic / Story：project-terminology-build-versioning-reporting / S13
- 计划：[项目术语计划](../../../../plans/project-terminology-build-versioning-reporting/plan.md)

## 原因与修改

冻结结果原来直接以输入 build_key 为唯一键。相同输入遇到模型重抽、模型非确定性输出或来源部分失败后重试，可能产生不同内容，仓库因此报告 `identifies different canonical payloads`。

- 修改 `application/terminology/build.py` 的 `freeze_fragments`：用既有输入标识和结果内容标识直接组合冻结结果键，不新增哈希。输入缓存键保持原样，结果不同可分别保存；相同结果仍幂等。
- 新增 `tests/application/terminology/test_build_result_identity.py`：对内存和 SQLite 验证模型重抽、旧输入键记录兼容、重复结果幂等、部分失败重试与历史结果可读。

不改变数据库结构，不覆盖旧构建，不放宽仓库碰撞检测。无需删除现有术语、草稿或历史版本。

## 验证

```powershell
uv run --no-sync --cache-dir .tmp-build-key-fix/uv pytest tests/application/terminology/test_build_result_identity.py tests/application/terminology/test_build.py tests/application/terminology/test_incremental.py tests/persistence/terminology/test_repository.py -q --basetemp .tmp-build-key-fix/tests --tb=short
uv run --no-sync --cache-dir .tmp-build-key-fix/uv pytest tests/integration/bootstrap/test_terminology_llm_build.py tests/integration/bootstrap/test_terminology_task_wiring.py -q --basetemp .tmp-build-key-fix/integration --tb=short
.venv/Scripts/ruff.exe check src tests
.venv/Scripts/ruff.exe format --check src tests
git -c core.safecrlf=false diff --check
```

28 项应用/持久化测试、16 项生产集成测试通过；全仓 Ruff 与 2570 文件格式检查通过。集成测试因 Windows 硬链接夹具需要提升权限。未运行全仓 pytest、未调用付费模型、未打包发布；未读取或修改真实项目数据库。任务临时目录完成后删除。
