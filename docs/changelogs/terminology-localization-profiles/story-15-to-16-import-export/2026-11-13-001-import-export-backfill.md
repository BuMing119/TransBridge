# S15～16 补记：指定方案导入、命名及多方案导出

日期：2026-11-13。Epic：terminology-localization-profiles；S15～16。功能此前在本对话实现，本轮补文档、核验源码和既有测试；此前 S14 差异查看记录保持原样。

## S15：导入目标和新名称

- 新增于此前实现的 `application/terminology_profiles/existing_import.py`：调用既有来源合并后生成 publication 预览；保留目标 mappings 权威属性、overrides、bindings，以目标本身而不是项目底板作为已有内容。
- `application/terminology_profiles/importing.py`、`service.py`：支持指定已有映射为合并基线，携带发布前提和读取明确 profile，复用原策略、版本校验与提交。
- `ui/workbench/terminology_profile_controller.py`、`ui/tools/terminology/context_binding.py`、`schemes_controller.py`：串联当前目标、项目/Variant 校验、原子 publication 和刷新，保留名称与 profile ID，不改正文实际关联。
- `ui/tools/terminology_profiles/import_policy_dialog.py`、`import_dialog.py`、`source_import_controller.py`：明确“导入当前方案/创建新方案”，新名称在读取前和预览中可编辑，已有名称只读；后台读来源，显示导入成功而非误报创建另一个方案。
- 测试覆盖空方案、两种策略、现有权威/特例保留、其他项目隔离、过期提交、输入名称实际保存和共享真实窗口绑定。

## S16：一次导出多个方案

- 新增于此前实现的 `application/terminology_profiles/export_source.py`：后台解析勾选方案，使用原正文来源和基础术语构造各自 FrozenProfileWriteProjection。
- `ui/coordinators/operation_coordinator.py`、`ui/workbench/terminology_profile_controller.py`：捕获可选方案及源投影，不切换当前 UI 选择。
- 新增于此前实现的 `ui/operations/write_schemes.py`、`write_scheme_fields.py`：组合原请求、多选控件、根目录及安全/唯一方案目录名；`write_dialog.py` 接入控件及紧凑布局。
- `ui/operations/write_feature.py`：复用预检缓存、原 task submitter 和结果，展开后每份保留独立投影，不能把第一方案套到所有输出。
- `application/io/operation_write_batch.py`、`plugin_transfer.py`、`json_export.py`：复用既有批量/模板租约及写入工作流，JSON 按来源使用各自投影；不新增文件格式或第二套写入引擎。
- `tests/ui/operations/test_write_schemes.py`：真实插件回读证明两套译名不同；覆盖单/多插件、JSON、覆盖保护、预检后修订冻结、模板不变、目录合法化及 Qt 交互。

## 文档与验证

本轮更新 FR5.18.20/.23/.25 并新增 .26/.27；ADR-039 的“只能创建新方案”在原章节修订为明确目标合同，补多方案/比较数据流，保持实现契约提议状态。计划新增 S15～16 补记，修正总索引进度；新增 [使用说明](../../../terminology-workflow.md) 与 [核验报告](../../../test-reports/terminology-conversation-documentation-2026-11-13.md)。

本轮 37 项联合复验中，S15 应用与真实窗口 7 项、S16 8 项通过；完整实际命令见核验报告。本对话此前 125 项方案回归、90 项导出回归是历史证据，没有把重复测试相加作为新覆盖数。

未打包安装版，未替用户导出实际插件，不新增数据库迁移，不自动提交或推送。无已知文档阻塞。模型不能可靠匹配的术语仍保持原译文，比较报告也不重写原文。
