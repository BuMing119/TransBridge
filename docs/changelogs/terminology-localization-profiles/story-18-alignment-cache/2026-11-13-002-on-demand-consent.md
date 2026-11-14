# S18：首次实际需要模型时询问定位许可

- 日期：2026-11-13
- 关联：FR5.18.17、ADR-039、terminology-localization-profiles Story 18
- 原因：用户明确“每次打开软件只询问一次”限制的是运行内次数，弹出时机应为首次需要 LLM 补充定位缓存。

## 行为与修改

启动本身不弹窗。规则处理和本版本缓存读取先在后台完成；只有实际模型缺失才请求选择，且未选择前不准备模型配置或发起请求。同意后按当前有效输入自动继续，拒绝或关闭后本次运行不再自动询问；已有缓存仍可读取。“永不再问”的允许和拒绝、设置菜单主动修改选择均保留。

本次仅记录以下修订，不将工作区其他未提交功能计入：

- `src/transbridge/ui/shell/window_lifecycle.py`：撤销前次修订在 `WindowLifecycle.start` 中加入的启动许可调用；相对 Git 基线恢复原行为。
- `src/transbridge/ui/workbench/terminology_alignment.py`：`LazyAlignmentService` 在实际缺失入口返回 `AlignmentConsentRequired`；`AlignmentBarBinding` 允许未选择时执行规则/缓存探测，将已选择状态纳入调度键，接收按需询问信号并在决定后自动继续。
- `src/transbridge/ui/workbench/terminology_preview.py`：传递许可检查标记，校验任务身份后以独立信号交回主线程；过期任务不能触发弹窗。
- `src/transbridge/ui/workbench/alignment_consent.py`：更新职责说明；既有运行内去重和偏好持久化逻辑不变。
- `tests/ui/workbench/test_alignment_consent_demand.py`：新增 6 项规则/持久缓存命中、首次缺失及自动继续、拒绝/关闭后切换、过期请求测试；使用模拟模型和隔离项目。
- `tests/ui/workbench/test_alignment_consent.py`、`test_terminology_alignment.py`：启动断言改为不询问，已拒绝状态继续禁止模型任务。
- `docs/requirements.md`、`docs/adr/039-terminology-localization-profiles.md`、`plans/terminology-localization-profiles/plan.md` 及直接相关索引：同步触发时机、验收和结果；历史增量不修改。

## 验证

聚焦 37 项通过后，以下合并回归 **150 项通过**，不重复累加。使用仓库现有 uv 虚拟环境；因沙盒 FAISS 原生导入问题，在授权的进程中设置任务专属 TEMP/TMP 和 pytest basetemp。仅 3 项依赖 SWIG 弃用警告。

```powershell
.venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp .tmp-alignment-demand-qa/regression tests/ui/workbench/test_alignment_consent_demand.py tests/ui/workbench/test_alignment_consent.py tests/ui/workbench/test_terminology_alignment.py tests/ui/workbench/test_alignment_cache_preview.py tests/ui/workbench/test_terminology_preview_async.py tests/ui/workbench/test_terminology_profile_switching.py tests/ui/workbench/test_terminology_projection_consistency.py tests/ui/test_alignment_settings_menu.py tests/ui/test_main_window_shell.py tests/ui/test_ai_task_shutdown.py tests/ui/test_worker_shutdown_registry.py tests/ui/test_runtime_task_shutdown.py tests/application/terminology_profiles/test_variant_alignment_cache.py tests/application/terminology_profiles/test_profile_alignment.py tests/infra/test_terminology_occurrence_aligner.py -q
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m ruff format --check src tests
```

Ruff 检查通过，2,645 个文件格式检查通过。无数据库、配置格式或依赖迁移。未调用真实 LLM、未跑全仓 pytest 或重新打包；本次没有重测性能，原 S18 五轮缓存微基准不作为本次弹窗调度的性能证据。代码与相关回归无遗留项。
