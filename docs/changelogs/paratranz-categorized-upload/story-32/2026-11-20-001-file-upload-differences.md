# S32 四种上传模式的本地差异比较

2026-11-20 · paratranz-categorized-upload / S32 · [上传策略](../../../dev/paratranz-categorized-upload.md) · [第一阶段本地导出](../../paratranz-context-order/story-01-02/2026-11-20-002-local-tb2-export.md)

## 操作效果

工作台的普通和分类上传、单插件和多插件上传均先比较已有目标文件。原文模式用本次读取的词条 ID 迁移排序 key，可将多个独立文件的纯改号合为一次请求。新增、原文或基础 context 变化才触发增量文件写入。两种纯译文模式匹配去掉 TB2 前缀的身份，提交云端当前 key。安全模式比较译文和状态，强制模式筛选本地非空白且正文不同的译文。组合模式先完成改号与必要的原文更新，再对需要的条目执行安全导入。

## 实现

- [file_upload.py](../../../../src/transbridge/application/file_upload.py) 和 [classification.py](../../../../src/transbridge/application/categorized_upload/classification.py) 在完整来源上取得编号，筛选、分类及补传分片沿用对应编号。[file_upload_parallel.py](../../../../src/transbridge/application/file_upload_parallel.py) 将逐文件比较接入原有并发和分片组调度。
- 新增 [file_upload_diff.py](../../../../src/transbridge/application/file_upload_diff.py) 比较每个文件中的传输身份、原文、基础 context、译文和状态。新增 [file_upload_execution.py](../../../../src/transbridge/application/file_upload_execution.py) 执行单文件写入顺序，[file_upload_renames.py](../../../../src/transbridge/application/file_upload_renames.py) 汇总纯改号并将批量回执归到各文件。
- [file_publication.py](../../../../src/transbridge/paratranz/file_publication.py) 复用批量词条编辑 API 和共享请求预算，按返回的 ID 确认改号。仅明确拒绝请求大小时拆批。部分确认保留已完成的文件。批量编辑返回 404 且没有确认项时，共用一次新文件清单并重新核对受影响的文件。
- [artifact_file_reads.py](../../../../src/transbridge/paratranz/artifact_file_reads.py) 区分缺少比较字段与无效压缩包。达到 50 个参与比较的已有文件时共享新导出包，缺少必要字段时单独补读该文件。[sync_snapshot.py](../../../../src/transbridge/paratranz/sync_snapshot.py) 在工作台下载时解开排序前缀，保留云端 ID 和原始修订依据。
- [差异规则测试](../../../../tests/paratranz/test_file_upload_diff.py)、[请求与回执测试](../../../../tests/paratranz/test_file_upload_sorted_keys.py) 和[工程上传测试](../../../../tests/ui/operations/test_categorized_upload_bridge.py) 覆盖字段比较、文件写请求数、旧 key 迁移、真实格式 ZIP 缺字段、404 重定位、部分成功及重复上传。既有 HTTP、分片补传与 IO 契约夹具已对齐新的读取和传输格式。[需求](../../../requirements.md)、[ADR-047](../../../adr/047-opt-in-categorized-project-upload.md) 与[计划](../../../../plans/paratranz-categorized-upload/plan.md) 已同步。

## 验证

本地模拟选择 100 个已有文件，其中 3 个有对应模式的变化，四种模式各产生 3 次文件写请求。另一组 50 文件 ZIP 夹具中，1 个文件缺少比较字段，四个模式均只补读该文件。两文件只有排序号不同时，一次批量编辑完成两份迁移。以上数字按请求计，未测量真实服务耗时。

跨模块回归 1537 项通过，1 项需要显式配置专用 ParaTranz 合同测试账号的用例跳过。Windows 临时工程的硬链接测试在沙箱外运行。补充批量改号 404 仅刷新一次的用例后，再跑 3 个文件上传测试模块，99 项通过，两组覆盖有重叠。

```powershell
uv run --offline --no-sync --no-cache pytest tests/paratranz tests/contracts/paratranz tests/contracts/io tests/application/categorized_upload tests/application/projects/test_categorized_upload.py tests/scripts/test_categorized_conversion.py tests/ui/operations/test_file_upload_production.py tests/ui/operations/test_file_upload_input_scope.py tests/ui/operations/test_categorized_upload_bridge.py tests/ui/operations/test_categorized_upload_checkpoints.py tests/ui/operations/test_categorized_upload_recovery.py tests/ui/operations/test_categorized_upload_retry.py tests/ui/operations/test_categorized_upload_projection.py tests/ui/operations/test_export_json_dialog.py tests/ui/test_workbench_translation_import.py -m 'not llm' -q --tb=short
uv run --offline --no-sync --no-cache pytest tests/paratranz/test_file_upload_sorted_keys.py tests/paratranz/test_file_publication.py tests/paratranz/test_concurrent_file_publication.py -q --tb=short
.venv/Scripts/ruff.exe check src tests
.venv/Scripts/ruff.exe format --check src tests
git -c core.safecrlf=false diff --check
```

全库 Ruff 与格式检查通过，覆盖 2722 个 Python 文件。验证范围为本地测试及模拟服务，未进行线上写入或全库 pytest。
