# S2：恢复检查点使用短名称临时文件

- 日期：2026-11-11
- Epic/Story：ai-task-recovery / S2 执行恢复，恢复记录保存缺陷补修。
- 关联：[恢复计划](../../../../plans/ai-task-recovery/plan.md)、[完整恢复首次实现记录](../story-01-to-05/2026-10-24-001-full-task-recovery.md)。

## 原因与行为

用户反馈 AI 翻译任务在 `ai-translation-resume/postprocess` 下创建 `.json.<随机串>.tmp` 时出现 `Errno 2`。依据截图路径结构，目录约 162 字符、正式文件路径约 244 字符；旧实现把完整正式文件名拼入临时文件名后，临时路径约 266 字符。在未启用长路径支持的 Windows 环境中，这会超过传统 260 字符限制。此判断已通过模拟限制的回归用例验证，但尚未在报错电脑上直接复现。

保存现在使用标准库 `tempfile.mkstemp` 在正式文件同一目录创建短名称临时文件；按上述目录结构，临时路径约 181 字符。后处理使用 `.post-` 前缀，候选检查点提交头使用 `.head-` 前缀。保留写入、fsync、回读验证、`os.replace` 原子替换和退出清理。正式文件名称、位置及载荷格式不变，已有恢复记录无需迁移。

## 可归属变更

- 修改 [postprocess_checkpoint.py](../../../../src/transbridge/application/translation/postprocess_checkpoint.py)：`FilesystemPostProcessCheckpointPort.save` 先完成 JSON 序列化，再用短名称临时文件写入；不再追加完整目标文件名。保留修订递增检查、旧记录读取、句柄关闭和失败清理。
- 修改 [candidate_checkpoint_journal.py](../../../../src/transbridge/application/translation/candidate_checkpoint_journal.py)：`publish_head` 使用同目录短名称临时文件；文件描述符在交给文件流后标记为已接管，转交失败时由 finally 关闭，防止 Windows 上未关闭句柄阻止清理并掩盖原始异常。
- 新增 [test_checkpoint_file_paths.py](../../../../tests/contracts/translation/test_checkpoint_file_paths.py)：8 个用例。以脱敏但等长的 AppData 路径模拟 260 字符限制，验证旧后处理记录的读取与更新、候选提交头发布；参数化覆盖两种保存路径在 `fdopen`、`fsync`、`replace` 失败时保留原文件并清理临时文件。
- 新增本记录；[changelog 索引](../../INDEX.md) 和 [恢复计划](../../../../plans/ai-task-recovery/plan.md) 仅增加直接关联链接。既有增量文件保持原样。

无文件删除或移动，不新增哈希机制，不改任务身份、工程 schema、依赖或持久化格式。本记录不包含同会话的 ParaTranz 后缀兼容修复及工作区既有发布改动。

## 实际验证

使用 Windows、Python 3.12.12 和现有 uv 环境。沿用此前已确认的沙箱临时目录权限限制，获准在沙箱外运行测试；未同步依赖。

修复前专项测试 **5 通过、3 失败**。两个失败复现临时路径超限；另一个复现候选提交头在 `fdopen` 失败后因句柄未关闭而无法清理临时文件：

```powershell
$env:TEMP = 'D:\MyCode\TransBridge\.tmp-checkpoint-path-20261111'
$env:TMP = $env:TEMP
$env:PYTHONIOENCODING = 'utf-8'
uv run --no-sync pytest tests/contracts/translation/test_checkpoint_file_paths.py -q -p no:cacheprovider --basetemp .tmp-checkpoint-path-20261111/pytest --tb=short
```

修复后检查点、自动续跑和候选日志联合回归 **42 通过、3 条 SWIG 弃用告警**：

```powershell
uv run --no-sync pytest tests/contracts/translation/test_checkpoint_file_paths.py tests/contracts/translation/test_postprocess_checkpoint.py tests/contracts/translation/test_postprocess_automatic_resume.py tests/application/translation/test_candidate_checkpoint_journal.py -q -p no:cacheprovider --basetemp .tmp-checkpoint-path-20261111/pytest
$env:UV_CACHE_DIR = 'D:\MyCode\TransBridge\.tmp-checkpoint-path-20261111\uv-cache'
uv run --no-sync ruff check src tests --no-cache
uv run --no-sync ruff format --check src tests --no-cache
git diff --check
```

首次 Ruff 检查发现新增测试的导入排序问题，修正后全仓检查通过，2528 个文件格式检查通过，diff 空白检查通过。以上是实现阶段已执行的验证，本次归档不重复运行运行时测试。任务专用临时目录已核对绝对路径及归属后删除。

## 遗留与验证边界

- 未在原报错电脑或已安装的发行程序中复验，也未进行真实 LLM 调用、全仓 pytest、打包或发布。
- 本修复消除两处原子保存因临时文件名额外增长而超限的问题；不宣称解决所有任意深度目录、正式目标文件或候选日志文件本身的长路径问题，也未更改系统长路径设置。
- 只有已成功持久化的恢复记录可继续读取，本次不追补此前未保存的结果。
