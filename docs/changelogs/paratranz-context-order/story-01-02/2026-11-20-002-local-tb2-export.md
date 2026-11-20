# ParaTranz 本地导出的 TB2 排序码

2026-11-20 · paratranz-context-order · Story 01–02 后续，第一阶段

普通及分类导出在传输 key 前写入八位零基序号和 `|TB2|`。例如 `00000428|TB2|20AnumLaQuestTopicH:051BE868|1~DIAL:FULL`。分类文件复用来源级编号，包括有间隔的编号和筛选后保留的编号。读回时恢复内部词条身份并识别旧 context 序号。在线发布和迁移策略留待第二阶段。

## 实现

- `application/io/paratranz_keys.py` 增加外层排序格式和保留前缀转义。内部原文限定编码继续区分同 key 不同原文。
- `application/io/paratranz_mapping.py` 为传输记录保存独立序号，读取时先拆外层，再解码内部身份。输出时保留原文、译文、状态、远端引用和扩展。
- `application/io/paratranz_context_order.py` 的原有批次取号规则支持已解析的传输记录。
- `application/io/paratranz_export.py` 承接离线写出校验和格式投影。重复身份按内部传输 key 校验。未编号的原生 JSON 仍支持保真往返。
- `application/io/paratranz.py` 将离线写出委托给上述模块，按内部传输 key 计算默认来源身份。`application/io/json_export.py` 为显式普通导出启用排序。
- `application/io/paratranz_classification.py` 和 `application/categorized_upload/classification.py` 共用分类与筛选函数。`converter/translation_entry_collection_export.py` 逐文件传入整份来源已确定的序号。
- `docs/requirements.md` 的 FR22.9、FR22.10 与 `docs/dev/paratranz-categorized-upload.md` 记录本地 JSON 契约和既有上传边界。

## 回归覆盖

- `test_paratranz_order_export.py`、`test_paratranz_order_readback.py` 覆盖稀疏序号、整批回退、筛选、没有上下文的词条、旧文件转换及反复导出。
- `test_paratranz_transport_keys.py` 覆盖原文限定词条、形似 TB2 的业务 key 和缺失内部 key 的诊断。
- `test_dialogue_order_projection.py`、`test_paratranz_facades.py` 对照本地 key 和既有上传 context 的编号，并校验分类、内容和上传分片。`test_export_json_dialog.py` 验证普通及批量导出入口。

## 验证结果

最终联合回归 722 项通过，出现 7 条弃用警告。Ruff 静态检查通过，2717 个 Python 文件符合格式要求，Git 差异检查通过。范围覆盖本地转换及现有上传请求的回归测试。

```powershell
uv run --offline --no-sync --no-cache pytest tests/contracts/io tests/scripts/test_categorized_conversion.py tests/paratranz/test_file_transport_keys.py tests/paratranz/test_dialogue_order_file_retry.py tests/paratranz/test_entry_upload_order.py tests/application/categorized_upload tests/ui/operations/test_export_json_dialog.py tests/ui/test_workbench_translation_import.py -q
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\ruff.exe format --check src tests
git diff --check
```

本阶段仅操作本地文件。已有第三方 key 如果恰好以完整的八位 ASCII 数字加 `|TB2|` 开头，读取时会按这个保留格式解释。实际网页排序和在线更新策略属于第二阶段。
