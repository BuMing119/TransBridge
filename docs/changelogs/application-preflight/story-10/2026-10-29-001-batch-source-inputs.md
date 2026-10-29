# S10：写回缺输入清单、批量补充与部分范围确认

日期：2026-10-29（Asia/Shanghai）。Epic：application-preflight；Story：S10。
关联：[计划](../../../../plans/application-preflight/plan.md)、[ADR-050](../../../adr/050-application-preflight-and-optional-source-reference.md)、[需求](../../../requirements.md)、[助手恢复 003](../story-09/2026-10-29-003-assistant-source-inputs.md)。

状态：本地实现、相关验证、增量记录与任务临时目录清理完成。

## 原因与边界

用户批准写回输入准备原型，要求一次汇总本次缺失模板，支持多文件或文件夹补充，同名候选明确选版本，并允许确认跳过尚未准备好的插件后继续原写回。原逐来源菜单难以表达整体准备进度，也不能让用户清楚选择部分写回范围。

本轮保留已有模板、已保存译文及原请求边界；普通编辑不要求补输入。候选名称只是提示，补齐仍复用既有解析、对应、发布和重开流程；来源路径不承担新校验或身份。实际输出继续执行原有预检和确认，缺少输出目标仍需提供 `path`/`output_dir` 或使用桌面已有输出界面。本次没有给助手新增输出位置选择器。

记录仅归属 S10 的新增切片及既有文件中相关段落；此前下载、术语、持久来源和 S01～S09 修改不重复记入。001、002、003 历史增量保持不变。

## 逐文件变化

生产路径相对于 `src/transbridge/`。

- 新增 `application/projects/source_input_candidates.py`：`InputNeed`/`InputCandidates` 描述输入需要及候选；`collect_input_files` 枚举用户明确选定的目录及子目录，跳过符号链接/目录联接，收集局部扫描错误；`match_input_candidates` 只向缺输入项提供同名提示，保留多个候选而不自动决定版本，不读旧参考文件内容。
- 修改 `application/projects/source_inputs.py` 的 `_source_label`：优先保存显示名，缺失时从当前或 legacy 参考字符串提取 basename，再回退稳定来源身份；仅恢复可读名称，不探测或打开旧文件。
- 新增 `ui/source_input_batch_dialog.py`：用表格模型展示缺输入及译文条数、准备进度，折叠原本就绪来源；支持多文件、目录、单项文件及候选完整路径选择。多个候选不预选；部分写回必须勾选跳过并进入范围确认，列出将处理及跳过项，零就绪项不能继续。状态变化使原确认失效，忙时禁用修改但允许取消，错误留在清单供重试。
- 新增 `ui/coordinators/source_input_batch.py`：冻结原来源集合及工程/版本，后台发现候选，合并去重后只对一对一的唯一同名候选排队补齐；多候选或多个来源竞争同一候选需要显式选择。串行委托单项补齐，每次发布/重开后重新读取就绪事实；失败保留已成功输入，取消停止余下队列，正在发布项完成后才释放会话。原范围来源消失不能静默视为部分跳过；最终仅返回原范围内明确确认的就绪来源，排队恢复回调再次核对 owner、工程/版本和来源集合。
- 修改 `ui/coordinators/source_input_coordinator.py`：模板请求委托批量切片，其他操作保留单项流程；`_ensure_one` 接受明确来源和文件，避免重复来源菜单；全部保存行已由既有匹配逻辑对齐时直接使用对应，否则保留 `SourceSupplementDialog`。来源映射支持链式解析，并在完成回调前发布新 slot 映射；延迟重开和完成回调沿用工程/版本及请求有效性边界。
- 修改 `ui/source_input_state.py`：补输入 helper 转交范围回调/部分写回参数；`SourceInputScope.active_key/active_slot` 限制在显式 slots 内，防止单项恢复借用后来活动来源。
- 修改 `ui/operations/facade.py` 的 `begin_write`：输入清单显示本次操作及已有输出位置；确认后将精确的 `SourceInputScope` 传入实际计划构造，继续保留原输出参数。文件中已有上传补输入改动不归本轮。
- 修改 `ui/operations/write_feature.py` 与 `write_batch.py`：原批量范围缩小到一个来源时仍允许构造批量计划，保持输出目录及来源自身文件名语义；空集合仍拒绝，不回退到全工程或活动来源。既有输出参考边界不重复归入本轮。
- 修改 `application/assistant_requests/source_input_selection.py`：在原失败 receipt、请求修订、事项和 lease 边界内保存 `source_scope`，包含原请求、人工选择与映射后来源 key；必须非空且属于原模板请求，原 ingress 不变。确认显示失败后的重新 admission 继续读取同一保存范围。
- 修改 `smart_assistant/request_scope.py`：恢复模板来源范围时使用上述证据，不再要求被明确跳过来源的条目选择仍可解析；文字操作仍保持既有 EntryKey 范围合同。
- 修改 `smart_assistant/tools/write_back_preparation.py` 的 `_selected_sources`：补输入恢复有冻结范围时，省略 `source_keys` 也只取确认范围；显式参数不能恢复跳过项或改变范围，执行顺序使用已保存来源列表。未就绪来源不产生输出授权或写回 draft。
- 修改 `ui/tools/smart_assistant/source_input_binding.py`：缺模板动作展示缺失来源名称，使用共享清单及原输出参数；确认范围保存到恢复证据，重新生成仅包含就绪来源的工具参数及新确认。确认重试保留选择/跳过范围，不复用旧 `_prepared_write_back` 或已消费许可，反馈如实说明本次跳过项。
- 新增 `tests/application/projects/test_source_input_candidates.py`：候选查找、同名歧义、路径仅作名称、局部错误与链接边界；新增 `tests/ui/test_source_input_batch_dialog.py`、`test_source_input_batch_coordinator.py`、`test_source_input_batch_integration.py`：清单模型/确认状态、串行发现及补齐、真实工程输入发布重开、单项部分写回和取消保留。
- 新增 `tests/application/assistant_requests/test_source_input_scope.py` 与 `tests/smart_assistant/tools/test_write_back_source_scope.py`：范围持久化、原始请求隔离、重试、恢复后不能扩大输出来源；扩展 `tests/ui/tools/smart_assistant/test_source_input_repair.py` 的 UI binding 部分范围回归，用 QWidget/binding 和替身 coordinator 验证所选范围传入新工具确认。该项不等同真实共享清单端到端，真实清单、工程补齐和重开另由 5 项 batch integration 覆盖。相关旧标题断言按新清单流程更新。`tests/ui/test_on_demand_source_inputs.py` 的缺输入 fixture 从固定来源下标改为按目标插件保存的 location/legacy.path 精确定位，避免随机来源 ID 排序把测试目标误选成 XML；这次失败不涉及产品修复。
- 更新 `plans/application-preflight/plan.md` 及直接相关索引，记录 S10 的实现范围、验证边界和当前收尾状态。

## 验证证据

下列批次有重叠，不合并为唯一测试总数。GUI 使用 `QT_QPA_PLATFORM=offscreen`；测试使用临时工程与替身，不操作真实用户工程或网络/LLM。

- 写回界面与现有操作计划组 45 项通过：输入清单 12、另存为 14、操作计划 presenter 9、插件套用 UI 8、文件上传范围 2。
- 助手/范围联合组最终 85 项全部通过（36.46s），包含清单 12、coordinator 4、source scope 11、source selection 14、writer scope 3、write options 27、input prerequisites 7、assistant source input repair 7。此前 84 项中 83 通过、1 项旧标题断言失败；断言按新清单更新，并新增一项 UI binding 部分范围回归后复跑取得上述结果。
- 候选、清单及实际补齐联合组 44 项，最终 43 通过、1 项因 Windows 符号链接权限跳过（18.92s）。此前 `empty` 参数失败来自上述 fixture 按 registry 下标误选来源，改为准确定位后该组全部可运行项通过。
- 前期候选模块 7 通过、1 项 Windows 符号链接环境跳过；清单 12、coordinator 4、真实补齐集成 5、应用 source scope 11、writer scope 3 和既有 on-demand 10 均取得聚焦通过证据。以上与联合组重复，不单独累计。
- 扩展请求执行回归 27 项通过，4 项在无关 task recovery 的 `os.link` 遇 Windows `WinError 5`；本轮未修改该成果恢复路径，也不将这些失败记录为通过。
- 最终全库 `uv run --no-sync ruff check src tests` 通过，`uv run --no-sync ruff format --check src tests` 报告 2308 files already formatted；`git -c core.safecrlf=false diff --check` 退出码 0、无输出，保持仓库原有行尾及 Git 配置。

主会话联合验证命令如下，缓存与临时目录均为本任务独占路径；前两组共用 basetemp，结果按批次独立报告。

```powershell
$env:UV_CACHE_DIR = '.tmp-batch-input-uv'
$env:TEMP = Join-Path (Get-Location) '.tmp-batch-input-runtime'
$env:TMP = $env:TEMP
$env:QT_QPA_PLATFORM = 'offscreen'
uv run --no-sync pytest tests/ui/test_source_input_batch_dialog.py tests/ui/operations/test_write_save_as.py tests/ui/operations/test_operation_plan_presenter.py tests/ui/operations/test_plugin_transfer_ui.py tests/ui/operations/test_file_upload_input_scope.py -q --basetemp .tmp-batch-input-tests
uv run --no-sync pytest tests/ui/test_source_input_batch_dialog.py tests/ui/test_source_input_batch_integration.py tests/ui/test_on_demand_source_inputs.py tests/application/projects/test_source_input_candidates.py tests/application/projects/test_source_supplement.py tests/ui/tools/smart_assistant/test_source_input_repair.py::test_template_list_choice_reproposes_only_confirmed_ready_sources -q --basetemp .tmp-batch-input-tests
uv run --no-sync pytest tests/ui/test_source_input_batch_coordinator.py tests/ui/test_source_input_batch_dialog.py tests/application/assistant_requests/test_source_input_scope.py tests/application/assistant_requests/test_source_input_selection.py tests/smart_assistant/tools/test_write_back_source_scope.py tests/smart_assistant/tools/test_write_back_options.py tests/smart_assistant/tools/test_source_input_prerequisites.py tests/ui/tools/smart_assistant/test_source_input_repair.py -q --basetemp .tmp-batch-input-assistant-tests
uv run --no-sync ruff check src tests
uv run --no-sync ruff format --check src tests
git -c core.safecrlf=false diff --check
```

## 遗留与未执行项

- 本轮范围内无已确认待修项；相关验证、文档与临时目录清理完成。
- 未跑全仓 pytest，未执行真实用户工程、真实 LLM/网络、Windows 人工视觉验收或性能基准；Qt offscreen 的真窗口集成不等同人工桌面验收。4 项硬链接环境失败如上保留，不宣称全库测试通过。
- 原参考路径仍仅参考；不增加模板完整性检查，不以同名候选猜测版本正确性。批量补充按来源分别保存，不提供跨来源全有或全无事务；取消后已保存输入继续存在，原译文不丢失。
- 助手缺少目标时仍需提供 `path`/`output_dir`；清单显示已知输出参数，不新增助手另存为界面。
- 主会话核实绝对路径及本任务归属后清理 `.tmp-batch-input-uv/`、`.tmp-batch-input-runtime/`、`.tmp-batch-input-tests/`、`.tmp-batch-input-assistant-tests/`，并用 `Test-Path` 确认无剩余；各实施 Agent 已清理各自独占临时目录。未读取或清理既有 `.transbridge/`。

## 收尾追加：目录枚举错误持续可见

最终复查发现，目录枚举返回局部错误后，自动补充其他候选的进度反馈会覆盖错误，导致用户看不到哪些目录未读取。`ui/coordinators/source_input_batch.py` 现在通过 `_discovery_errors` 保留本次扫描错误，并由 `_feedback` 与后续进度合并显示；不改变候选选择或补齐范围。

新增 `tests/ui/test_source_input_batch_coordinator.py::test_directory_errors_remain_visible_while_other_candidates_are_supplemented`。主会话使用已有虚拟环境复验，coordinator 模块 5 项通过，全库 Ruff 通过、2308 文件格式检查通过；本次复验不创建新的任务独占临时目录，既有清理状态不变。

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
.venv/Scripts/python.exe -m pytest tests/ui/test_source_input_batch_coordinator.py -q
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m ruff format --check src tests
```

前述三组测试数字保留为当时真实证据，本次 5 项包含已有用例，不与前述批次相加。S10 完成状态及未执行项保持不变。
