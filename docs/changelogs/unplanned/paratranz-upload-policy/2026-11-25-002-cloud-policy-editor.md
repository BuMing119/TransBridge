# Owner 后台保存云端上传政策

2026-11-25 · unplanned / paratranz-upload-policy · [需求与交互](../../../requirements/paratranz-upload-policy.md)

- 新增 `paratranz/project_policy.py`。从绑定项目根目录读取原始附件，核对账号及项目所有者。默认或自定义设置均在内存中生成 UTF-8 JSON，以 multipart 创建或完整替换云端政策；保存前复核目标及已有内容，保存后读回验证。首次提示取决于本次云端读取结果。
- `paratranz/project_upload.py` 与 `application/file_upload.py` 在共用上传及补传流程复用本轮文件清单进行政策检查。项目上传文件选择和 `paratranz/download_paths.py` 排除根目录政策文件。
- 新增 `ui/operations/upload_policy_flow.py` 和 `upload_policy_editor.py`。后台线程处理读取与保存，所有者可按昵称选择成员、编辑独立名单。修改同目录 `categorized_upload_bridge.py`、`upload_mode_dialog.py`、`categorized_upload_dialog.py`，在布局选择前读取政策，并按授权设置选项及提交按钮。
- 更新 FR3.4、FR3.8 及上传使用说明。新增 `test_project_upload_policy.py`、`test_upload_policy_flow.py`，扩展上传会话、入口路由回归并更新助手测试的网关注入。测试以模拟云端核对首次创建、替换、修改冲突、取消、重复打开、目录无政策副本和后台线程归属。

## 验证

```powershell
uv run --offline --no-sync --no-cache pytest tests/paratranz tests/ui/operations tests/smart_assistant/tools/test_project_file_upload.py -q
uv run --offline --no-sync --no-cache pytest tests/paratranz/test_project_upload_policy.py tests/ui/operations/test_upload_policy_flow.py -q
uv run --offline --no-sync --no-cache ruff check --no-cache src tests
uv run --offline --no-sync --no-cache ruff format --no-cache --check src tests
git -c core.safecrlf=false diff --check
```

扩大回归 1,202 项通过。最后调整后复验 8 项通过，Ruff 两项检查及差异空白检查通过。离屏渲染检查了中文编辑器布局。未运行全库 pytest，未向真实 ParaTranz 项目写入文件。
