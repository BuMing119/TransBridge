# ParaTranz 上传兼容唯一路径大小写变体

2026-11-15，Epic `paratranz-categorized-upload`，S12 文件 API 上传补修。关联 [S12 计划](../../../../plans/paratranz-categorized-upload/plan.md) 和 [ADR-047](../../../adr/047-opt-in-categorized-project-upload.md)。

## 问题与变更

分类上传为任务生成 `BSHeartland.esm/任务/对话_[Aera the Devout's Missing Amulet].json`，云端返回的实际路径使用 `missing amulet`。原有精确查找未取得文件 ID，新建请求随后因重名返回 HTTP 400。

- `src/transbridge/application/file_upload.py` 将串行和并发上传共用的文件列表建成内存索引。精确完整路径优先，缺少精确匹配时使用忽略大小写后的唯一完整路径。存在多个候选时该文件失败，回执列出候选路径，文件保留在补传列表。原文更新、译文导入和 404 刷新后的重定位共用这套规则。
- `src/transbridge/application/file_upload_parallel.py` 沿用共用的文件列表索引，按忽略大小写的路径对可能共享 413 分片的操作分组，保留同组输入次序。
- `tests/paratranz/test_file_publication.py` 用实际失败文件名验证原文与译文请求指向已有 ID，覆盖精确优先、同名跨目录隔离、歧义只读和重定位后的大小写兼容。`tests/paratranz/test_concurrent_file_publication.py` 覆盖大小写变体分片的顺序，以及列表刷新时保留已确认创建的文件。
- `docs/adr/047-opt-in-categorized-project-upload.md` 和 `docs/dev/paratranz-categorized-upload.md` 更新路径匹配与歧义提示。`docs/changelogs/INDEX.md` 增加本记录的链接。

## 验证

旧实现执行新增用例时，唯一变体、歧义和 404 后大小写变体三项失败。修复后，聚焦套件 75 项通过。扩大套件中的工程测试依赖 SQLite 文件硬链接，沙箱返回 `WinError 5`。在沙箱外以仓库内独立临时目录复验，265 项全部通过，包含 3 条 SWIG 弃用警告，临时目录已清理。Ruff 和差异空白检查通过。

```powershell
uv --no-cache run --offline --locked --no-sync pytest tests/paratranz/test_file_publication.py -q -k 'case_insensitive or deleted_recreated'
uv --no-cache run --offline --locked --no-sync pytest tests/paratranz/test_file_publication.py tests/paratranz/test_file_upload_retry.py tests/paratranz/test_concurrent_file_publication.py -q
uv --no-cache run --offline --locked --no-sync pytest tests/paratranz/test_file_publication.py tests/paratranz/test_file_upload_retry.py tests/paratranz/test_concurrent_file_publication.py tests/paratranz/test_file_transport_keys.py tests/paratranz/test_file_transfer_timing.py tests/paratranz/test_dialogue_order_file_retry.py tests/application/categorized_upload tests/application/projects/test_categorized_upload.py tests/contracts/io/test_paratranz_classification.py (Get-ChildItem tests/ui/operations -Filter 'test_*upload*.py').FullName -q --basetemp (Join-Path $uploadQaRoot 'pytest')
uv --no-cache run --offline --locked --no-sync ruff check --no-cache src tests
uv --no-cache run --offline --locked --no-sync ruff format --no-cache --check src tests
git diff --check
```

此变更无需迁移工程或改名云端文件。验证范围为本地和模拟 HTTP，未运行全库 pytest、发布构建或真实云端写入。
