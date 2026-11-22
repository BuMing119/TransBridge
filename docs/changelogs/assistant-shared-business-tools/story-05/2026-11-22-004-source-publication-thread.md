# 在助手工作线程登记工程来源

2026-11-22 · [助手工具复用桌面功能](../../../../plans/assistant-shared-business-tools/plan.md) · S5 第四批

桌面“打开翻译文件”在后台登记来源并从解析结果构造工作台集合。助手此前虽然共用了登记命令，却把磁盘提交和整个集合的构造也调度到了 Qt 主线程。较大来源可能在这两个步骤阻塞界面。[桌面打开流程](../../../../src/transbridge/ui/coordinators/open_file_coordinator.py)

助手现在在调用工具的工作线程执行 `register_opened_source`。正式提交使用请求许可，提交前检查取消，工程命令继续检查捕获的工程、版本及修订。许可释放后，工作线程调用 `OpenedSourceView.prepare_slot`。Qt 线程负责开启和关闭工作台视图事务、安装准备好的集合以及更新助手的投影目标。显示重试复用已登记的解析结果。请求遭拒或提交前取消时释放视图事务，已完成工程提交但请求回执保存失败时继续安装来源并返回告警。[助手登记实现](../../../../src/transbridge/smart_assistant/tools/_source_import.py)

线程回归在后台执行真实工具和工程数据库命令，并记录线程标识及命令执行时的许可状态。正常登记、拒绝提交、执行许可内取消、提交后回执失败四种情况均覆盖。既有测试继续覆盖集合构造失败、视图安装失败、修订变化和来源写入后停止。[线程与结果对照](../../../../tests/smart_assistant/tools/test_file_import_parity.py) · [刷新重试](../../../../tests/smart_assistant/tools/test_source_import_authority.py)

## 验证

以下测试 92 项全部通过。两个修改的 Python 文件通过 Ruff 静态和格式检查。

```powershell
uv run --offline --no-sync --no-cache pytest tests/smart_assistant/tools/test_file_import_parity.py tests/smart_assistant/tools/test_source_import_authority.py tests/ui/test_open_file_workflow.py tests/smart_assistant/test_request_execution.py -q --tb=line
.venv/Scripts/ruff.exe check tests/smart_assistant/tools/test_file_import_parity.py src/transbridge/smart_assistant/tools/_source_import.py
.venv/Scripts/ruff.exe format --check tests/smart_assistant/tools/test_file_import_parity.py src/transbridge/smart_assistant/tools/_source_import.py
```
