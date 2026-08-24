# 本地工程 ParaTranz 目标绑定实施计划

- **Feature slug**：`paratranz-project-binding`
- **状态**：已完成（2026-08-24，综合 QA 通过）
- **日期**：2026-08-24
- **对应需求**：[FR22.6～FR22.8](../../docs/requirements.md)
- **架构约束**：[ADR-023](../../docs/adr/023-local-project-paratranz-binding.md)、[ADR-018](../../docs/adr/018-project-session-persistence-v2.md)、[ADR-019](../../docs/adr/019-unified-task-runtime.md)、[ADR-021](../../docs/adr/021-ui-presentation-modularization.md)

## 目标

让用户在本地工程中一次绑定 ParaTranz 云端项目，之后可直接上传、下载或合并；管理页浏览不再改变同步目标。未绑定、账号/端点变化或权限异常时，Workbench 与操作计划就地解释并提供选择，而不是要求先跳转管理页。

## 非目标

- 不持久化 Token/API Key，不改变现有安全凭据存储。
- 不把一个 Project 的不同 Variant 分别绑定到不同云端项目。
- 不以名称自动匹配、最近浏览项目或全局配置作为静默绑定。
- 不重写 ParaTranz 同步传输实现、TaskRuntime 或 Project V2 存储格式。
- 不删除现有公开兼容属性；仅停止把它们当作权威目标来源。

## Story 总览

| Story | 交付能力 | 优先级 | 依赖 |
|---|---|---:|---|
| S01 | 绑定值对象、Project schema 与只读 projection | P0 | ADR-023 |
| S02 | 原子绑定 command、统一 target resolver 与目录验证 | P0 | S01 |
| S03 | Workbench 目标视图/选择器与管理页显式绑定 | P0 | S02 |
| S04 | 操作计划、菜单/卡片入口和目标修订失效 | P0 | S02～S03 |
| S05 | Smart Assistant 兼容迁移、综合回归与文档收口 | P0 | S01～S04 |

## Story-01：绑定模型、Schema 与 Projection

**验收标准**：

- [x] 提供不可变 ParaTranz Project binding 值对象，校验正整数 ID、规范化 endpoint、账号 ID 与 ISO 时间。
- [x] Project V2 schema 正式声明可选 `data.remote_bindings.paratranz`，旧工程无字段时仍可读取。
- [x] Project projection 暴露只读绑定快照；Variant 切换保持绑定，Project 切换投影对应绑定。
- [x] Project 序列化/反序列化 round-trip 不包含凭据，未知 provider 扩展保持兼容。

**文件落点**：`application/projects/remote_binding.py`、Project projection/model、`persistence/v2/schema.py` 及对应 application/persistence tests。

## Story-02：原子 Command、Resolver 与目录验证

**验收标准**：

- [x] `ProjectRemoteBindingService` 以 expected revision 原子设置/解除绑定；失败保持旧 active 与 projection。
- [x] `ParaTranzTargetResolver` 固定执行“显式覆盖 → 工程绑定 → 未绑定”，不读取管理页浏览状态。
- [x] 目标结果携带来源、revision 与稳定验证状态；endpoint/account/project/member/auth/network 错误可区分。
- [x] 项目目录完整拉取“我的项目”分页，支持取消、配置 revision 隔离和有界缓存。
- [x] GUI、Agent 可复用同一 application 目标解析与诊断，不依赖 Qt。

**文件落点**：application project/paratranz 模块、ParaTranz typed service/port、composition 和 fault/contract tests。

## Story-03：Workbench 与管理页显式绑定

**验收标准**：

- [x] Workbench 在本地工程上下文旁显示云端目标、状态、更换和解除入口。
- [x] 选择器异步加载完整项目目录；配置变化、关闭页面或新请求会取消/忽略旧结果。
- [x] 没有活动工程时允许选择本次目标但不显示可持久化成功。
- [x] ParaTranz 管理页只在用户点击“设为当前工程同步目标”时写绑定；普通浏览 Project revision 不变。
- [x] Presenter/Binding 生命周期可释放，不向超限 `AppContext`/`ProjectBar` 增加网络或持久化职责。

**文件落点**：新增 `ui/workbench/remote_target_*` 组件，修改 Workbench composition、ParaTranz project panel/widget 和 UI tests。

## Story-04：操作计划与入口连续性

**验收标准**：

- [x] 未绑定时上传/下载入口仍可点击并进入一个操作计划，计划内解释原因并提供目标选择。
- [x] 用户可只覆盖本次目标或显式设为工程默认；取消不修改绑定、不发网络副作用。
- [x] operation request/digest 包含目标 ID、来源和绑定 revision；更换/解除绑定使旧 preflight/确认失效。
- [x] Workbench 卡片、菜单和协调器提交同一 intent，不再以 `current_project` 或 session ID 判断可用性。
- [x] 提交前验证当前 endpoint/account/权限，不可用时保留可编辑计划。

**文件落点**：`ui/operations/`、`ui/coordinators/operation_coordinator.py`、shell intent、Workbench upload/download cards 和 focused UI/operation tests。

## Story-05：Smart Assistant 迁移与综合 QA

**验收标准**：

- [x] Smart Assistant 显式 `project_id` 作为本次覆盖，缺省时使用统一 resolver，不回退最后浏览项目。
- [x] 新 binding 可向必要的只读兼容属性投影，但无反向读取、无双写；旧工程行为明确为未绑定。
- [x] application/persistence/UI/operation/assistant focused tests、相关完整回归、Ruff check/format 通过。
- [x] 故障注入覆盖 revision 冲突、持久化失败、账号/端点变化、分页取消、迟到结果和重复提交。
- [x] 最终 diff 审查确认无凭据泄露、无新的权威状态、无 Qt 事件循环阻塞，并更新本计划/索引状态。

## 实施顺序与提交边界

1. S01 先形成纯数据和兼容读取合同；可独立回退为忽略附加字段。
2. S02 提供唯一 command/resolver，之后 UI 才允许写绑定。
3. S03 与 S04 可在 S02 后按组件边界推进，但操作计划终验依赖 Workbench 选择器。
4. S05 最后迁移旧调用方并运行综合 QA；旧兼容字段在本轮不删除。

## 验证命令

优先运行新增 focused tests，然后运行相关 Project/ParaTranz/operation/UI/assistant 测试；最终执行 `uv run ruff check src tests`、`uv run ruff format --check src tests`，并在时间允许时运行完整 `uv run pytest -q`。真实 ParaTranz 联机冒烟需要用户凭据时只记录为人工验证项，不把 Token 写入测试或报告。

### 完成证据（2026-08-24）

- 绑定相关 application、persistence、ParaTranz、operation、UI、Assistant 与 contract 回归：`380 passed, 1 deselected`。
- deselect 的旧 Assistant 用例依赖本机 LLM API Key；当前稳定返回 `API_KEY_MISSING`，与 ParaTranz 绑定链无关。
- 本功能涉及文件的 Ruff check 与 format check 通过；仓库全量 Ruff 仍有 433 个既有基线问题，未在本功能中越权批量修改。
- `uv run` 因现有 `.venv` 指向缺失的 uv-managed Python 3.12 解释器而不可用；本轮使用可用的 Python 3.13.5 与仓库 `.venv` 内 Ruff 可执行文件验证。
- 未执行真实 ParaTranz 联机冒烟，避免读取或写入用户凭据；成员验证、分页、取消和错误分类通过 fake/contract 测试覆盖。

## 回滚策略

任一 UI 切片可撤回为“未绑定提示”，不恢复最后浏览项目回退。application 层保留兼容读取，旧版本会忽略 `remote_bindings` 扩展；若提交失败则旧 Project revision 与绑定原样保留。对已写绑定的清理由显式解除 command 完成，不通过手工编辑工程文件。
