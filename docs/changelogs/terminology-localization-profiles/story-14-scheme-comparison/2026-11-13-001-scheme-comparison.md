# S14：方案译文差异查看与 Excel 工作簿

- 日期：2026-11-13
- Epic：terminology-localization-profiles；Story 14。
- 范围：在此前多方案导出基础上增加只读差异查看与工作簿输出，不把此前多方案插件导出的实现计入本次增量。

## 用户行为

在“导出文件”勾选至少两个方案，点击“查看译文差异”。无需输出目录或先导出插件。独立窗口只展示各方案译文不同的来源词条，保留来源、词条身份、原文、当前译文、各方案译文及转换提示；选中行可查看完整长文本。

“导出 Excel 工作簿”保存同一份比较快照，工作表为“译文差异”“方案信息”，记录方案标识、版本、内容标识、比较时间和统计。没有差异时仍能导出表头及方案信息。

## 文件与实现

- 新增 `application/terminology_profiles/comparison.py`：不可变结果与逐词条比较，复用 `ProfileEntryProjector` 和既有输入转换；按来源保留词条身份，支持取消，不修改原译文或持久方案选择。
- 新增 `ui/operations/scheme_comparison_dialog.py`：QAbstractTableModel/QTableView 按需绘制差异，完整文本详情、独立窗口、Excel 按钮，初始尺寸限制在可用屏幕内。
- 新增 `ui/operations/scheme_comparison_controller.py`：捕获导出草稿，复用 AsyncActionRunner 在后台比较和保存；关闭取消比较并丢弃迟到回调，错误留在窗口中。
- 修改 `ui/operations/write_schemes.py`：新增比较输入读取接口，拒绝不完整来源；原多方案导出逻辑不属于本次新增。
- 修改 `write_scheme_fields.py`、`write_dialog.py`、`coordinator.py`：比较按钮、信号与当前草稿绑定；至少两项勾选才启用。
- 新增 `infra/scheme_comparison_workbook.py`：并排译文列及版本信息页，复用工作表格式、安全单元格、长文本分段；来源与词条身份在续行重复。
- 修改 `infra/terminology_workbook.py`：将工作表封装公开为 WorkbookSheet，保留 `_Sheet` 兼容别名，允许自定义说明和续行身份列；原冲突工作簿回归通过。
- 修改 `infra/terminology_export.py`：增加比较工作簿类型，复用同目录临时文件、取消检查、原子替换与失败清理。
- 新增 `tests/ui/operations/test_scheme_comparison.py`：来源隔离、无差异、多方案、取消、长文本与公式保护、工作簿回读、真实 Qt 后台查看/导出及关闭取消。
- 更新对应 plan、plans 索引和 changelog 索引；未修改其他既有增量记录。

## 验证

执行并通过（68 项）：

```powershell
uv run --no-sync --cache-dir .tmp-scheme-comparison/uv pytest tests/ui/operations/test_scheme_comparison.py tests/ui/operations/test_write_schemes.py tests/infra/test_terminology_workbook.py tests/infra/test_terminology_export.py tests/ui/tools/terminology/test_exports.py tests/ui/operations/test_operation_plan_coordinator_async.py tests/ui/operations/test_export_json_dialog.py tests/ui/operations/test_operation_plan_layout_stability.py -q --tb=short --basetemp .tmp-scheme-comparison/regression
.venv/Scripts/ruff.exe check src tests
.venv/Scripts/ruff.exe format --check src tests
git -c core.safecrlf=false diff --check
```

Qt 使用 offscreen 测试，不等同于用户桌面视觉验收。工作簿用 openpyxl 回读验证，未操作用户的 Excel 会话。未用真实大项目做性能基准、未打包安装版、未调用模型。无需数据迁移。临时测试目录在收尾移除。

## 遗留

无已知阻塞；比较当前来源的方案投影，套用到其他插件时最终匹配范围仍由既有导出预检确定。
