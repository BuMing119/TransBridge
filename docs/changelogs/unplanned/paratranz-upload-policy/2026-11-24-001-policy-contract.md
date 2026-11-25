# ParaTranz 项目上传政策 v1 数据契约

2026-11-24 · unplanned / paratranz-upload-policy · [FR3.8 需求与示例](../../../requirements/paratranz-upload-policy.md)

## 交付

- 新增 `docs/requirements/paratranz-upload-policy.md`，在 `docs/requirements.md` 登记 FR3.8。明确文件格式、原文取值、独立名单、同组管理员分工示例及后续运行时接入标准。
- 新增 `application/file_upload_modes.py` 的 `UploadOperation`。`application/file_upload_diff.py` 复用该处派生的 `UPLOAD_MODES`，现有入口仍获取原有五个字符串及顺序。
- 新增 `application/upload_policy.py`，定义项目布局、译文方式集合、人员选择器和按操作的允许/排除规则。`UploadPolicy.permits_operation` 仅在内存中计算该项目政策对已知成员和指定操作的结果。
- 新增 `application/upload_policy_codec.py`，负责词条数组的解析、整份校验与规范化输出。译文和状态不参与政策计算。
- 新增 `tests/paratranz/test_upload_policy.py`，覆盖格式错误、往返、五种独立授权、项目写入方式与排除优先。

## 验证

```powershell
uv run --offline --no-sync --no-cache pytest tests/paratranz/test_upload_policy.py -q
uv run --offline --no-sync --no-cache pytest tests/paratranz/test_file_upload_diff.py tests/paratranz/test_file_upload_fill_empty.py tests/paratranz/test_file_upload_retry.py tests/smart_assistant/tools/test_project_file_upload.py -q
uv run --offline --no-sync --no-cache ruff check src tests --output-format concise
uv run --offline --no-sync --no-cache ruff format --check src tests
git diff --check
```

政策测试 35 项、上传回归 84 项通过。全库 Ruff 静态检查及格式检查通过。另从 FR3.8 文档直接提取 JSON，核对解析与写回、三个管理员的实际操作集合。未运行全库 pytest 或真实 ParaTranz 请求；发现、读取政策以及在各上传入口执行检查属于后续阶段。
