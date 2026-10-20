# 多插件创建同一工程 QA 记录

- 日期：2026-10-20。
- 关联需求：[FR26.3](../requirements.md)；关联计划：[guided-ui-workflows S02 / S04](../../plans/guided-ui-workflows/plan.md)。
- 环境：Windows、仓库现有 uv 环境、Python 3.12.12、pytest 9.1.1、Qt `offscreen`。
- 范围：开始中心一次选择多个 ESP / ESM / ESL，共用一次建项请求和事务，提交后加载全部插件；包括单插件、空工程和已有译文迁移兼容。
- 结论：本轮定向复核 **27 项通过**，本功能 11 个源码／测试文件的 Ruff 检查通过。限定范围内未发现问题。全仓 Ruff 尚未通过，具体为其他同步 UI 文件的检查问题，见下文。

## 验收覆盖与证据

- **S04 多选与一次提交**：`tests/ui/test_start_center_multi_plugin.py::test_choose_multiple_plugins_creates_one_project_and_loads_every_collection` 验证多选文件对话框接线、首插件名称建议、3 个插件的数量／列表／路径提示、重复点击只调度一次、一次 prepare 和 commit，以及全部插件载入工作台。
- **S04 草稿与布局**：同文件覆盖取消文件选择保留原草稿、重新选择后刷新名称／列表、切换空工程、解析失败保留路径／名称／选项，以及 50 个路径在 850 × 650 窗口中的滚动列表与主按钮可见性。该布局测试只处理路径列表，不代表 50 个大插件的解析性能。
- **S02 同一工程与一次解析**：`tests/application/projects/test_provisioning.py` 验证多个原始来源只执行一次 `begin → provision → commit`，prepare 无持久化副作用，hydration 仅由原 owner 消费一次，新增来源纳入请求 fingerprint。`tests/persistence/v2/test_project_provisioning_multiple_sources.py::test_real_plugins_create_one_project_and_reopen_with_all_untranslated_entries` 使用生成的真实插件格式 fixture，验证 ESP / ESM / ESL 各解析一次、一个工程目录记录、全部原文与未翻译状态保存／重开一致，输入文件内容不变。
- **S02 失败不留孤立记录**：第二个插件损坏时，诊断包含其路径，原活动工程、generation 和持久化 JSON 保持不变。Variant、工程目录和活动指针三个写入位置的故障注入均回滚，不留下部分 Project / Variant / catalog / active pointer；提交失败不提供 hydration。应用层另验证已有工程在提交失败后仍保持活动。
- **S02 身份与译文迁移配对**：规范化后的重复路径、大小写／`..` 等价路径、重复原始插件身份被拒绝。两个原始插件各自匹配对应的已有译文插件，不将额外原始插件当作译文，也不把不同插件的迁移关系交叉配对。
- **Hydration 与兼容**：本轮 UI 回归验证相同 legacy ID / local key 的不同插件仍保留独立 EntryKey，以及各自 `string_id`。代码复核确认继续复用 `slot_from_hydration()`；原 `source` / `source_path` 单插件入口保留，新增集合通过 `sources` / `source_paths` 访问，无持久化 schema 升级。前轮 `test_project_source_hydration.py` 的 4 项测试另覆盖 metadata、external refs、provenance、revision、源快照与精确身份。

本次复核读取了建项调用链、两份 Story、现有计划和测试配置。模型／验证与来源准备分别落入 `provisioning_models.py`、`provisioning_sources.py`，原模块继续转出既有公开合同；应用层未引入 Qt。

## 本轮执行记录

在仓库根目录执行。测试使用本任务独占临时目录和现有环境，未同步依赖：

```powershell
$env:UV_CACHE_DIR = 'D:\MyCode\TransBridge\qa-tmp-multi-plugin-docs-20261020\uv-cache'
$env:QT_QPA_PLATFORM = 'offscreen'
uv run --offline --no-sync pytest tests/application/projects/test_provisioning.py tests/persistence/v2/test_project_provisioning_multiple_sources.py tests/ui/test_start_center_multi_plugin.py -q --basetemp qa-tmp-multi-plugin-docs-20261020/pytest
```

- 沙箱首跑：退出码 **1**。16 项 application 与 5 项 UI 测试执行成功；6 项 persistence 测试在 `tmp_path` 建立阶段报错，pytest 结束清理同样报 `PermissionError: [WinError 5]`，指向上述专用 `pytest` 临时目录。该次失败未作为业务失败归因。
- 经自动审批允许，在沙箱外重跑同一命令：退出码 **0**，**27 passed in 0.60s**（application 16、persistence 6、UI 5）。

本功能 11 个文件的精确检查命令：

```powershell
uv run --offline --no-sync ruff check --no-cache src/transbridge/application/projects/provisioning.py src/transbridge/application/projects/provisioning_models.py src/transbridge/application/projects/provisioning_sources.py src/transbridge/ui/coordinators/guided_project_coordinator.py src/transbridge/ui/shell/start_center.py src/transbridge/ui/shell/start_center_controller.py src/transbridge/ui/shell/start_center_landing.py tests/application/projects/test_provisioning.py tests/persistence/v2/test_project_provisioning_multiple_sources.py tests/ui/test_start_center_guided_project.py tests/ui/test_start_center_multi_plugin.py
uv run --offline --no-sync ruff format --check --no-cache src/transbridge/application/projects/provisioning.py src/transbridge/application/projects/provisioning_models.py src/transbridge/application/projects/provisioning_sources.py src/transbridge/ui/coordinators/guided_project_coordinator.py src/transbridge/ui/shell/start_center.py src/transbridge/ui/shell/start_center_controller.py src/transbridge/ui/shell/start_center_landing.py tests/application/projects/test_provisioning.py tests/persistence/v2/test_project_provisioning_multiple_sources.py tests/ui/test_start_center_guided_project.py tests/ui/test_start_center_multi_plugin.py
```

两条命令退出码均为 **0**，分别输出 `All checks passed!`、`11 files already formatted`。

全仓只读检查：

```powershell
uv run --offline --no-sync ruff check --no-cache src tests
uv run --offline --no-sync ruff format --check --no-cache src tests
```

- `ruff check`：退出码 **1**，当次工作区快照有 3 项问题：`src/transbridge/ui/operations/sync_result.py` 的导入顺序（I001）与第 44 行超长（E501），`src/transbridge/ui/operations/sync_review_view.py` 的导入块格式（I001）。
- `ruff format --check`：退出码 **1**，4 个文件需要格式化：`paratranz_dialog.py`、`sync_preflight_presentation.py`、`sync_review_dialog.py`、`sync_review_model.py`，均位于 `src/transbridge/ui/operations/`；其余 **1912 个文件**已符合格式。
- 这些文件属于共享工作区的其他同步 UI 工作线；本任务未改动它们，也未将全仓检查表述为通过。

## 承接的上一轮验证

下列为本会话功能实现阶段的既有证据，本轮仅复核上述 27 项；不将重叠测试重复累计成新的覆盖数量：

- `uv run pytest tests/application/projects tests/persistence/v2 -q --basetemp .tmp-multi-source-backend-regression`：**469 项通过**。
- 六个 UI 文件的去重覆盖共 **54 项通过**：`test_start_center_multi_plugin.py`（5）、`test_start_center_guided_project.py`（26）、`test_start_center_draft_focus.py`（9）、`test_start_center_responsive_layout.py`（7）、`test_start_center_primary_contrast.py`（3）、`test_project_source_hydration.py`（4）。前轮存在重叠分组，不能将各次运行的 50 与 35 相加成 85。

前轮两个 UI 分组的精确命令由主会话提供；共同使用 `$env:UV_CACHE_DIR = 'D:\MyCode\TransBridge\qa-tmp-multi-plugin-root-20261020\uv-cache'`：

```powershell
uv run --offline --no-sync pytest tests/ui/test_start_center_multi_plugin.py tests/ui/test_start_center_guided_project.py tests/ui/test_start_center_draft_focus.py tests/ui/test_start_center_responsive_layout.py tests/ui/test_start_center_primary_contrast.py -q --basetemp=qa-tmp-multi-plugin-root-20261020/pytest
uv run --offline --no-sync pytest tests/ui/test_start_center_multi_plugin.py tests/ui/test_start_center_guided_project.py tests/ui/test_project_source_hydration.py -q -p no:cacheprovider
```

两条命令退出码均为 **0**，分别为 **50 passed, 1 warning** 与 **35 passed**；警告来自预先存在的 `.pytest_cache` ACL。上述历史结果作为承接证据保留，不冒充本轮运行。

## 文档核验

- 检查本次新增行与两份新文档中的 34 个本地 Markdown 文件链接，全部目标存在。
- 受影响文档的 `git diff --check` 通过；两份新文档另以 `git diff --no-index --check -- NUL <文件>` 检查，无空白错误。后者退出码 1 表示相对空文件存在新增内容。
- 计划、S02/S04 Story、UX 合同、线框图和旅程记录中不再保留 `project.create_from_source`、`project.create_empty`、不存在的 `GuiProjectCommandFacade.create_project` 或单主来源限制；当前建项契约统一为 `project.create`。

## 未运行项与边界

- 未人工操作 Windows 原生多选文件对话框；自动化替换 `QFileDialog.getOpenFileNames()` 并验证其调用与返回路径处理。
- 未使用用户真实翻译数据，未测大插件／大批量解析的耗时与内存；生成的最小插件 fixture 和 50 条路径布局测试不能替代性能基准。
- 本轮未重跑全仓 pytest、469 项后端组或完整 54 项 UI 组；本轮目标为文档补齐后的定向复核，源码未再修改。
- 未执行发行构建、安装包或发布验证；本功能不改变包配置、版本、依赖或文件格式 schema。

本任务专用 `qa-tmp-multi-plugin-docs-20261020` 目录在结果记录后，经绝对路径边界和任务归属核验后清理，`Test-Path` 返回 `False`。沙箱内清理同样遇到目录 ACL，受审查重试退出码为 0；未清理共享临时目录或其他工作线的测试产物。
