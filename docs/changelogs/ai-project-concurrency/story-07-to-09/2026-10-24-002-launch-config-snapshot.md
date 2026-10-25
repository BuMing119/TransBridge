# 修复 AI 启动配置快照复制 RLock 失败

- 日期：2026-10-24
- Epic / Story：ai-project-concurrency / S07～S09，启动配置捕获回归
- 关联：[实施计划](../../../../plans/ai-project-concurrency/plan.md)

## 问题与原因

启动后台化后，`TaskLaunch._capture` 对整个 LLM 和 ParaTranz 配置执行 `deepcopy`。真实 `ParatranzConfig` 持有 `ConfigRepository`，仓库包含不能深拷贝的 `threading.RLock`，绑定 ParaTranz 项目后因此在启动准备阶段报 `cannot pickle '_thread.RLock' object`。仓库加载的 LLM 配置也包含相同边界；此前启动测试采用不带仓库的 LLM 配置和简化远端配置，未覆盖此情况。

两个新增启动用例在修改生产代码前均复现相同错误；修复后通过。该错误发生在启动配置捕获阶段，与词条去重速度无关。

## 本次变化

- 修改 `src/transbridge/config/paratranz.py`：新增 `ParatranzConfig.copy_for_execution`，显式捕获 URL、超时、请求头、用户标识、当前内存凭据、凭据引用和配置修订；不深拷贝原仓库或凭据服务。副本使用空环境映射和不可用凭据存储，构造时不读取配置文件、系统凭据或之后变化的环境变量；沿用现有客户端接口和凭据脱敏行为。
- 修改 `src/transbridge/ui/tools/ai_translator/task_launch.py`：两个配置捕获点复用 `FrozenExecutionConfig` 的执行复制协议，LLM 使用其已有 `copy_for_execution`。保留后台快照、预检、取消及过期结果丢弃流程。
- 新增 `tests/ui/tools/test_ai_launch_config_snapshot.py`：使用真实仓库配置验证本地 LLM 和绑定 ParaTranz 项目的启动；实例化真实远端客户端而不发请求，并验证任务参数与后续设置修改隔离。
- 新增 `tests/paratranz/test_execution_config.py`：覆盖内存、环境、凭据存储、缺失凭据四种来源，验证复制不读取实时服务、不写文件、请求参数和凭据固定、请求头独立、重复复制及脱敏。
- 更新 `plans/ai-project-concurrency/plan.md`、`plans/INDEX.md` 和 `docs/changelogs/INDEX.md`：在原启动合同中明确配置复制边界，链接本次记录。既有增量记录未改动。

## 验证

使用现有 uv 环境，`QT_QPA_PLATFORM=offscreen`；未调用真实 LLM 或 ParaTranz 服务。以下命令实际执行：

```powershell
uv --cache-dir .q30/uv-cache run --offline --no-sync pytest tests/ui/tools/test_ai_launch_config_snapshot.py -q --basetemp .q30/repro-llm -o cache_dir=.q30/pytest-cache
uv --cache-dir .q30/uv-cache run --offline --no-sync pytest tests/ui/tools/test_ai_launch_config_snapshot.py tests/paratranz/test_execution_config.py -q --basetemp .q30/fix -o cache_dir=.q30/pytest-cache
uv --cache-dir .q30/uv-cache run --offline --no-sync pytest tests/paratranz tests/contracts/config tests/ui/tools/test_unified_task_runtime.py tests/ui/tools/test_ai_task_launch_background.py tests/ui/tools/test_ai_launch_config_snapshot.py tests/ui/tools/test_run_request_preparation.py tests/ui/tools/test_ai_task_consistency.py tests/ui/tools/test_task_recovery_binding.py -q -m 'not llm and not integration' --basetemp .q30/verified -o cache_dir=.q30/pytest-cache
uv --cache-dir .q30/uv-cache run --offline --no-sync ruff check src tests
uv --cache-dir .q30/uv-cache run --offline --no-sync ruff format --check src tests
```

- 修复前启动回归：**2 失败**，均为截图中的 RLock 错误。
- 修复后专项：**6 通过**。
- 扩展回归：**291 通过，24 个既有弃用警告，13.88 秒**，包含上述 6 项。首次沙箱运行为 233 通过、58 失败，原因是临时文件权限、恢复硬链接和 localhost 测试服务器连接被沙箱拒绝；在受批准的本地测试环境中将 TEMP/TMP 定向至本任务 `.q30/system-temp` 后，同一集合全部通过，期间未修改生产代码或测试断言。
- 全库 Ruff lint 和 format check：通过，2082 个文件格式符合要求。
- 未重复全仓 pytest、海量词条性能基准或真实 API 验证；本次仅改变小型配置快照，相关后台生命周期和 GUI 心跳回归已覆盖。

## 兼容与遗留

无配置格式或任务记录迁移；原客户端调用接口保持兼容。已有应用进程需要重新启动才能加载修改。本次未重启用户应用、取消用户任务或提交 Git。

本次缺陷无已知遗留；此前大样本启动仍有短时延迟的性能限制不因本次修复而改变。
