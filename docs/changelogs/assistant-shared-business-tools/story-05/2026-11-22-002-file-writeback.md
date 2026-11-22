# 写回复用桌面导出的来源、目标和预检

2026-11-22 · [助手工具复用桌面功能](../../../../plans/assistant-shared-business-tools/plan.md) · S5 第二批

- 从工作台“导出文件”追到单来源和批量草稿。桌面和助手共用原生写回的快照、编码、输出格式、覆盖和备份策略。批量来源沿用同一目标分配、译名方案冻结、输出重叠诊断、逐来源发布和取消回执。保存目录里的文件名由桌面实际选择规则决定。[导出入口](../../../../src/transbridge/ui/workbench/cards/write_card.py) · [生产组合](../../../../src/transbridge/ui/operations/write_feature.py) · [导出窗口](../../../../src/transbridge/ui/operations/write_dialog.py) · [写回请求](../../../../src/transbridge/application/io/native_write.py) · [批量视图](../../../../src/transbridge/ui/operations/write_batch.py) · [目标控件](../../../../src/transbridge/ui/operations/write_destinations.py)
- 两端共用工程分页模板、已保存或待保存的插件引用、输入指纹核对，以及按工程版本记住已验证的新路径。助手继续校验插件和附属资源的读取权限、授权输出目录及已确认的来源范围。写回后按实际文件记录本轮撤销。[工程材料及路径修复](../../../../src/transbridge/application/projects/write_inputs.py) · [桌面材料选择](../../../../src/transbridge/ui/coordinators/write_input_coordinator.py) · [助手材料授权](../../../../src/transbridge/smart_assistant/tools/write_back_preparation.py)
- 修正普通插件通过助手另存时可任意改名、复用来源目录覆盖插件及省略输出位置的差异。现在普通插件单个或批量另存均指定 `output_dir` 并保留绑定插件的文件名。写回已定位的原插件使用 `destination_mode="original"`，已有文件须显式 `overwrite=true` 并通过来源版本检查。普通插件使用 `path` 会报错。单个非插件格式的文件可通过 `path` 选择输出位置。批量中的多个来源即使指向相同目标，其回执也按原来源顺序归属。无关的插件输入映射会被拒绝。[助手写回工具与参数](../../../../src/transbridge/smart_assistant/tools/tool_writer.py)
- `filter_files` 的通用筛选仍接受相对文件列表和可选规则。FOMOD 打包预览传入 `fomod_dir`，两端使用相同的 `find_fomod_xml_files`、XML 解析和分类规则。预览逐个核验规则文件和 XML 的读取授权。[助手过滤](../../../../src/transbridge/smart_assistant/tools/tool_archive.py) · [桌面流水线](../../../../src/transbridge/fomod/stages.py) · [XML 引用](../../../../src/transbridge/fomod/xml_fidelity.py)
- 双入口对照使用相同来源、目标与覆盖策略，比较单插件及混合批量的成品字节、诊断、备份和成果列表，测试原插件已变更、另存重叠、合规写回原插件及 FOMOD ZIP 真实成员。原有写回发布边界、项目引用修复、取消和撤销测试改用桌面支持的输出方式。[双入口对照](../../../../tests/smart_assistant/tools/test_desktop_write_parity.py) · [来源及权限](../../../../tests/smart_assistant/tools/test_safe_write_back.py) · [参数、取消和备份](../../../../tests/smart_assistant/tools/test_write_back_options.py) · [发布故障回执](../../../../tests/smart_assistant/tools/test_write_back_publication_boundary.py)

## 验证

以下两组分别通过 113 项和 94 项。变更涉及的 15 个 Python 文件通过 Ruff 检查与格式检查。对照使用临时目录，FOMOD 运行关闭 AI，全程离线。此前混合运行桌面和助手套件时，Qt 下拉框可见性断言曾失败，单独复验和整组桌面回归均通过。

```powershell
uv run --offline --no-sync --no-cache pytest tests/smart_assistant/tools/test_desktop_write_parity.py tests/smart_assistant/tools/test_safe_write_back.py tests/smart_assistant/tools/test_write_back_options.py tests/smart_assistant/tools/test_write_back_publication_boundary.py tests/smart_assistant/tools/test_write_back_request_undo.py tests/smart_assistant/tools/test_write_back_source_scope.py tests/smart_assistant/tools/test_eet_workflow.py tests/test_fomod_xml_resource_fidelity.py tests/test_fileops.py -q --tb=short
uv run --offline --no-sync --no-cache pytest tests/ui/operations/test_write_save_as.py tests/ui/operations/test_write_source_references.py tests/ui/operations/test_export_flow_source_reuse.py tests/ui/operations/test_strict_write_routes.py tests/ui/operations/test_write_binary_eet.py tests/ui/operations/test_write_batch_sst.py tests/ui/operations/test_batch_output_conflicts.py -q --tb=short
```

本批无遗留项。
