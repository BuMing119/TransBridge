# 直接选择目标 ESP 套用当前译文

- 日期：2026-10-09
- Epic / Story：[target-plugin-write / S01～S03](../../../../plans/target-plugin-write/plan.md)
- 需求：用户授权 bm-pilot 实现独立目标插件写回，不强制先保存或更新项目来源。

## 行为

单插件写回窗口可选择 ESP/ESM/ESL 模板，默认输出该插件的 `_translated` 副本，也可明确确认覆盖并备份。后台预检把当前译文匹配到目标条目身份，显示统计及原文变化、歧义、缺失、阶段不允许等逐项原因；未匹配及新增内容保留。项目来源、版本和 dirty 状态不变，已有当前文件导出、XML 和批量模式保持兼容。

匹配以完整定位、精确原文和记录主文件归属为依据。共享 localized ID 检查全部物理引用；目标插件与独立 Strings、输出文件均冻结指纹，在执行前及最终提交内复核。校验依据所选新版模板，不比较旧新版是否相同。已确认的正常保留返回 COMPLETED、WARNING 和 skipped 计数，不伪报发布失败；真正发布失败或 PARTIAL 保留原语义与实际产物。

## 逐文件变更

- 新增 `src/transbridge/application/io/plugin_transfer.py`：定义独立 draft / preflight / workload；原项目术语投影只固定一次，再用目标身份构建底层写回；提供完整报告与成功/跳过计数。继承项目 Strings 语言，显式语言优先。
- 新增 `src/transbridge/application/io/plugin_transfer_matching.py`：定位和原文唯一匹配、master/SELF 归属、阶段策略与共享 Strings 组级保护，产出逐条保留原因。
- 新增 `src/transbridge/application/io/plugin_transfer_snapshot.py`：从捕获快照重建匹配条目，避免实时解析与 Strings 快照读取时序混合；保护原项目来源，冻结原请求路径与解析路径及文件指纹，最终提交守卫复核。
- 新增 `src/transbridge/ui/operations/write_feature.py`：独立写回 feature，管理普通、批量与所选目标预检缓存；所选目标提交仅消费冻结检查，不在 GUI 再解析。
- 新增 `src/transbridge/ui/operations/write_plan.py`：普通导出及所选插件计划，模板路径与输出路径独立，覆盖通过布尔控件展示。
- 新增 `src/transbridge/ui/operations/write_batch.py`：完整迁出已有批量计划和工作负载，保留行为。
- 修改 `src/transbridge/ui/operations/production.py`：写回职责迁出，保留工厂装配、术语服务注入及兼容导入；模块从本轮开始时 593 行降至 208 行。
- 扩展 `src/transbridge/ui/operations/write_dialog.py`：插件/输出浏览、自动副本路径、清除所选模板、覆盖复选框、后台状态、匹配摘要和可滚动逐项报告；输入变化撤销旧确认，正常保留使用完成提示。
- 修改 `src/transbridge/ui/operations/coordinator.py`：真实写回窗口使用既有 worker 预检流程；输入变更信号使 presenter 中的旧预检与令牌失效。关闭后忽略晚到回调并释放草稿。
- 新增 `tests/integration/publish/test_plugin_transfer.py`：40 项真实二进制回归，覆盖版本增删改、原文消歧、空白、ESP/ESM/ESL、master 归属、术语、localized 共享引用和语言、原地备份、读取/提交期间变化、取消和真实发布 PARTIAL。
- 新增 `tests/ui/operations/test_plugin_transfer_ui.py`：8 项浏览控件、输出/覆盖、延期预检、输入错误、旧令牌拒绝和关闭清理回归。
- 新增 `tests/ui/operations/test_target_plugin_workflow.py`：3 项真实 Qt → ApiWorker → TaskRuntime → 文件输出回归，验证后台运行、未保存项目不变、新版完整记录保留、文件变化拒绝及预检关闭。
- 更新 `tests/ui/operations/test_write_execution_feedback.py`：既有 10 项测试等待后台预检并使用覆盖复选框；相关新测试的 QApplication 保留至 session，避免模块间销毁再创建导致旧 Qt 信号总线失效。
- 更新 `docs/requirements.md` 的 FR4.1、新增 `plans/target-plugin-write/plan.md`，最小更新 `plans/INDEX.md`、`docs/changelogs/INDEX.md`。

本轮不重复归属之前的空白原文定位、保存状态、日志及写回反馈修复。工作区其他已有需求、ADR、依赖、锁文件、打包配置等变更不属于本增量。

## 验证

广泛回归：

```powershell
uv run --no-sync pytest tests/integration/publish tests/contracts/io tests/parser/test_original_match_plugin.py tests/writer/test_plugin_writer.py tests/persistence/v2/test_project_provisioning.py tests/application/projects/test_provisioning.py tests/ui/operations -q
```

结果 **459 passed、1 skipped**，38.22 秒。跳过为 Windows 符号链接检查；7 条既有 SWIG/兼容 API 弃用警告。此轮包含新增插件测试的前 38 项。

补充非英文 Strings 语言继承及显式覆盖后的最终验证：

```powershell
uv run --no-sync pytest tests/integration/publish/test_plugin_transfer.py tests/ui/operations -q
```

结果 **117 passed**，4.08 秒，包含最终 40 项插件回归与 77 项操作界面回归。与广泛回归重叠，不累计为独立测试总数。

```powershell
uv run --no-sync ruff check src tests
uv run --no-sync ruff format --check src tests
git -c core.safecrlf=false diff --check
```

全部通过，格式检查覆盖 1588 个文件。独立只读审查的 Strings 读取时序问题已修复并补注入回归；最终复核未发现新的阻塞问题。离屏 Qt 截图使用本机中文字体检查了 680px 窗口的字段、摘要、详情和页脚布局，临时检查文件已清理。

## 兼容与未验证项

无项目 schema 或数据迁移，无需重译已保存译文。当前内存中的未保存译文也可参与本次套用。仅有归档内 Strings 而无可写独立 Strings 的本地化目标会明确阻止；无法证明的记录不做模糊套用。

本地实现范围无未完成项。未运行全仓库测试、用户原始 Neiva ESP 现场复验、安装包构建或发布。未提交或推送。要在朋友机器使用仍需包含本变更的新构建。
