# 工具目录复核：归档、目录比较和远端分页

2026-11-22 · [助手工具复用桌面功能](../../../../plans/assistant-shared-business-tools/plan.md) · S5 最终目录复核

- `extract_archive` 使用桌面 FOMOD 解压阶段调用的 `ArchiveExtractor.extract`，传入取消令牌。助手执行整包解压；发现、参数校验及直接调用均撤下仅选部分成员的 `files`。底层通用提取器继续支持成员选择。打包、解压校验分别区分现有输入与待创建输出，相对路径按请求的授权工作目录定位。[FOMOD 解压和根目录识别](../../../../src/transbridge/fomod/discovery.py) · [助手归档工具](../../../../src/transbridge/smart_assistant/tools/tool_archive.py) · [参数与路径校验](../../../../src/transbridge/smart_assistant/guardrails/input_validator.py)
- `pack_archive` 复用桌面发布器的 ZIP/7z 打包函数。普通打包在同卷临时目录完成并检查取消状态，再替换目标。7z 改为逐成员写入相对路径，解决原先传入空根成员后正式 FOMOD 回读失败的问题。桌面正式 FOMOD 发布继续写自己的验证清单和备份，助手通用打包沿用已有单文件撤销。[归档实现](../../../../src/transbridge/fileops/archive.py) · [桌面正式发布](../../../../src/transbridge/application/fomod/publish.py)
- `diff_directories` 使用 FOMOD 差异阶段的 `diff_directories()` 和默认 `FilterRules.strip_exts`，同输入返回相同摘要、成员及重新校验记录。助手同时校验归一化后的目录权限。`skip_hash_exts` 只标识对应资源的哈希校验策略，现实现会计算当前摘要。[差异阶段](../../../../src/transbridge/fomod/stages.py) · [目录比较](../../../../src/transbridge/fileops/differ.py)
- `list_projects` 与管理面板共用 200 项一页的完整目录查询。助手的 `uid` 支持 `my` 与空串；前者在传输前解析为经验证的数字用户 ID，后者向平台查询全部项目，其他用户 ID 会被拒绝。`get_upload_history` 与“文件上传历史”共用远端修订分页。参数为项目及 `page`（1 至 9999，默认第 1 页，每页 50 条）；撤下 `limit` 和原先描述的同步成功回执字段。缺少修订 ID 的记录按桌面行为显示远端字段。[查询接口](../../../../src/transbridge/paratranz/management_queries.py) · [项目面板](../../../../src/transbridge/ui/paratranz/project_panel.py) · [远端历史](../../../../src/transbridge/ui/paratranz/history_tab.py) · [分页控件](../../../../src/transbridge/ui/paratranz/history_pager.py) · [服务](../../../../src/transbridge/paratranz/service.py) · [端口](../../../../src/transbridge/application/ports/paratranz.py) · [助手注册与分派](../../../../src/transbridge/smart_assistant/tools/tool_paratranz.py)
- 回归通过两个真实 Qt 控件和助手发出的请求对照项目第二页、当前账号 ID、全部项目和第 2 页修订记录；归档对照实际运行 FOMOD 发布、解包和目录比较阶段。校验了受限目录、已撤参数、打包撤销及取消后保留原文件。[双入口回归](../../../../tests/smart_assistant/tools/test_archive_catalog_parity.py) · [适配器契约](../../../../tests/paratranz/test_typed_client.py) · [助手远端工具](../../../../tests/smart_assistant/tools/test_paratranz_tools.py)

## 验证

下列组合通过 181 项。本批涉及的 13 个 Python 文件通过 Ruff 检查和格式检查。

```powershell
uv run --offline --no-sync --no-cache pytest tests/smart_assistant/test_input_validation_guard.py tests/smart_assistant/tools/test_archive_catalog_parity.py tests/smart_assistant/tools/test_paratranz_tools.py tests/paratranz/test_project_api.py tests/paratranz/test_typed_client.py tests/ui/test_worker_shutdown_registry.py tests/ui/test_paratranz_navigation_flow.py tests/application/assistant_requests/test_file_undo_capture.py tests/test_fomod_archive_policy.py tests/test_fomod_staging_publish.py tests/test_fomod_xml_resource_fidelity.py tests/test_fileops.py -q --tb=short
```

先前调整测试收集顺序的两次运行分别出现过 9 个缺失 `current_project_factory` 夹具的 setup 错误和 5 个历史面板在 360 像素下的 Qt 布局断言。上述最终组合包含这些用例，均已通过。远端请求在 HTTP 传输层使用离线数据；未访问真实账号。
