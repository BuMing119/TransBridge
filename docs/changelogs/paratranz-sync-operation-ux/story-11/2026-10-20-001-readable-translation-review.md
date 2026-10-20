# 用翻译内容和操作结果组织同步确认窗口

- 日期：2026-10-20
- Epic / Story：`paratranz-sync-operation-ux` / S11
- 计划：[plan.md](../../../../plans/paratranz-sync-operation-ux/plan.md)
- 承接：[S08～S10 上传往返](../story-08-to-10/2026-10-20-001-upload-roundtrip.md)

## 问题与结果

原复核窗口突出记录标识、候选和重新检查步骤，用户需要先理解同步内部流程。

窗口现在左侧按译文和原文浏览，右侧逐条比较“我的内容”和“ParaTranz 上的内容”。原文、译文、使用场景保持可见，记录编号按需展开。云端选项用场景与内容识别；上传、下载、跳过和撤销动作说明实际结果。默认展示第一条本地翻译，但不替用户选择云端内容。

选择 A 后再浏览 B 会说明当前仍选择 A，再次点击采用才改变决定。搜索框回车不会触发取消。完成选择仍返回主窗口自动检查，正式确认才写入；领域匹配、执行授权和持久化合同不变。

## 文件增量

以下路径相对仓库根目录。前轮下载、上传仍未提交，本记录只归属本轮界面增量；不计其他工作区修改。

- 修改 `src/transbridge/ui/operations/sync_review_dialog.py`：以内容导航和单条比较替代全宽表格；次要筛选按需展开；支持下一条未决定、批量跳过和取消隔离；详情区可滚动，底部返回按钮常驻。
- 新增 `src/transbridge/ui/operations/sync_review_detail.py`：独立负责两侧内容、场景化云端选项、操作后果、当前决定与浏览区别、不可用说明及折叠记录信息；正文保持纯文本。
- 新增 `src/transbridge/ui/operations/sync_review_list.py`：按应用调色板绘制内容和状态，不为每条创建控件。
- 修改 `src/transbridge/ui/operations/sync_review_model.py`：用户语言状态和可访问文本；选择变化一次通知所有显示列，保留批量校验。
- 修改 `src/transbridge/ui/operations/sync_review_direction.py`：区分“用我的译文更新这条”和“采用这条云端译文”，说明替换方向。
- 修改 `src/transbridge/ui/operations/sync_review_view.py`：增加默认字段 `preserves_local_source`，准确表达两种下载来源行为。
- 修改 `src/transbridge/ui/operations/sync_review_projection.py`：根据可选内容给出可执行建议，无可用候选时建议跳过；投影是否保留本地来源。
- 修改 `src/transbridge/ui/operations/paratranz_dialog.py`：入口显示“确认 N 条翻译”，保留既有确认与刷新状态机。
- 修改 `src/transbridge/ui/operations/sync_preflight_presentation.py`：使用“待确认”“不下载”“不上传”，移除重复冲突提示。
- 修改 `src/transbridge/ui/operations/sync_preparation.py`：修正方向及来源保护提示，保留检查合同。
- 修改 `tests/ui/operations/test_sync_review_dialog.py`：32 项真实控件回归，含预览与决定分离、回车、不可用内容、撤销、导航、筛选、取消、纯文本及 2,181 条批量一次通知。
- 新增 `tests/ui/operations/test_sync_review_layout.py`：4 项真实浅色/深色及字号测试，覆盖 900×620 窗口、长正文、字段不重叠、可滚动操作区和底部按钮可达。
- 修改 `tests/ui/operations/test_paratranz_sync_dialog.py`：适配新控件及文字，补充无可选目标建议和重复提示测试。
- 修改 `tests/ui/operations/test_paratranz_download_authority.py`：保留下载及重开行为断言，更新来源保护提示的文字匹配。
- 修改本 Epic 的 `plan.md`、`plans/INDEX.md`、`docs/changelogs/INDEX.md`：记录 S11 交付，不改前轮历史记录。

## 验证

```text
uv run --no-sync --no-cache pytest tests/ui/operations tests/contracts/paratranz -q -p no:cacheprovider --basetemp D:\MyCode\TransBridge\.tmp-review-ux-verified
uv run --no-sync --no-cache ruff check src tests
uv run --no-sync --no-cache ruff format --check src tests
git diff --check -- src/transbridge/ui/operations tests/ui/operations
```

联合回归 343 通过、1 个真实账户测试按配置跳过，3 条 SWIG 弃用警告；包含真实 V2 下载、编辑、上传、保存重开及安全部分执行。Ruff 和格式检查通过，检查时 1,944 个文件格式符合要求。Git 仅提示已有 LF/CRLF 转换信息。

沙箱测试最初因临时目录 WinError 5 失败，使用本地提升权限后正常运行。首次联合回归有 4 项旧提示文字断言失败，更新提示匹配后通过；未修改业务断言规避错误。

已渲染并查看真实应用浅色主题下的上传和下载窗口，内容为示例翻译；图片保存在当前聊天的 visualization 目录。五个自建 `.tmp-review-ux-*` 临时目录已逐个验证路径并清理。

## 边界与遗留

无数据迁移。没有真实 ParaTranz 写入、全库 pytest、安装包构建或 Git 提交。无已知未完成项。
