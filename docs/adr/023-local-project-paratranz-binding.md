# ADR-023：本地工程拥有 ParaTranz 同步目标绑定

- **状态**：已接受
- **日期**：2026-08-24
- **对应需求**：[FR22.6～FR22.8](../requirements.md)
- **关联 ADR**：[ADR-016](016-modular-monolith-application-composition.md)、[ADR-018](018-project-session-persistence-v2.md)、[ADR-019](019-unified-task-runtime.md)、[ADR-021](021-ui-presentation-modularization.md)

## 背景

现有 GUI 同时用 `AppContext.current_project` 表示 ParaTranz 管理页正在浏览的项目，并用会话级 `paratranz_project_id` 为同步操作提供目标；部分路径还会回退到最后浏览项目。用户因此必须先进入管理页并点击云端项目，上传/下载入口才可用，而且浏览行为可能无意改变真正的同步目标。该状态既不随本地工程持久化，也无法可靠区分账号、服务端点和项目权限。

本次改造必须保持 ADR-018 的 Project/Variant 权威持久化和原子提交边界，复用 ADR-019 的操作计划/任务语义，并遵守 ADR-021 的 View/Presenter/Binding 分层。绑定不得成为 `AppContext` 的第二权威状态，也不得把凭据写入工程文件。

## 决策

### 1. 绑定由本地 Project 拥有

Project V2 的可选 `data.remote_bindings.paratranz` 保存一个类型化绑定：

- `project_id`：ParaTranz 项目整数 ID；
- `project_name`：仅用于离线展示的缓存名称，不参与身份判断；
- `endpoint`：去除多余路径和结尾斜线后的规范化服务端点；
- `account_user_id`：绑定/验证时的 ParaTranz 用户 ID，可为空表示待验证；
- `bound_at`、`validated_at`：ISO 8601 时间，可为空。

字段缺失表示未绑定。绑定属于 Project，不属于 Variant；工程切换改变当前绑定投影，版本切换不改变它。工程文件不得包含 Token、API Key、credential reference 的值或其他秘密。

### 2. 通过 application command 原子修改

新增类型化 `ProjectRemoteBindingService`（计划落点：`application/projects/remote_binding.py`）作为唯一写入口。命令携带预期 Project revision，并通过 Project 生命周期/Repository UnitOfWork 提交；只有持久化和 active pointer 更新全部成功后才发布新 projection。冲突、写失败或激活失败保持旧 Project、旧绑定和旧 projection 不变。

UI、Agent 和兼容 facade 不直接修改 Project 字典，不双写 `AppContext` 会话字段。旧工程缺少绑定字段时自然解释为未绑定，不需要模式版本迁移。

### 3. 浏览状态和操作目标分离

`current_project` 继续仅表示 ParaTranz 管理页浏览状态。管理页的选中、切页和刷新不得调用绑定 command；仅显式“设为当前工程同步目标”动作可修改绑定。

新增 `ParaTranzTargetResolver` 供 GUI、Agent 和后续 MCP 共用，目标优先级固定为：

1. 不可变操作请求中的显式临时覆盖；
2. 当前本地 Project 的持久绑定；
3. 未绑定结果。

Resolver 不读取最后浏览项目、名称匹配结果或全局最近项目。解析结果携带来源、绑定修订和验证状态；目标或修订变化后，旧 preflight/digest 无效。

### 4. 独立目录查询与验证

新增 Qt 无关的 ParaTranz 项目目录查询边界，完整拉取“我的项目”分页结果，并支持取消、限时缓存和配置修订隔离。缓存键至少包含规范化 endpoint、账号 ID 和 ParaTranz 配置 revision；旧账号/端点的迟到结果不得进入当前选择器。

验证状态至少包括 `unbound`、`unverified`、`available`、`not_found`、`not_member`、`account_mismatch`、`endpoint_mismatch`、`authentication_failed`、`temporarily_unavailable`。名称只用于展示；ID、endpoint 和账号身份决定匹配。网络暂不可用不得清除已有绑定，认证/权限错误也不得自动选择其他项目。

### 5. UI 组合与操作计划

Workbench 新增独立 `RemoteTargetView`，由 Presenter/Binding 投影当前工程绑定、验证状态和更换/解除 intent；不把网络加载职责塞入超限的 `AppContext` 或 `ProjectBar`。目标选择器进入已有单一操作计划，用户可以只覆盖本次目标，或明确勾选“设为工程默认”。未绑定不会把上传/下载入口变成不可发现的死按钮，而是在计划内给出可操作原因。

ParaTranz 管理页增加显式绑定动作并复用同一 command。项目目录加载使用既有 worker/coordinator 模式，不阻塞 Qt 事件循环；页面销毁或配置变化时取消/忽略迟到结果。

### 6. 兼容迁移

- 旧 Project 没有 `remote_bindings` 时按未绑定读取，首次显式绑定再写入新字段。
- 旧 `paratranz_project_id`/`current_project.id` 不自动写入工程，避免把偶然浏览状态固化为错误目标。
- 迁移期可以从新 binding 向只读兼容属性投影，但不得反向读取作为权威回退，也不得双写。
- Smart Assistant 的 ParaTranz 工具迁移到同一 resolver；显式 `project_id` 仍表示本次覆盖。

## 关键契约

- 任何浏览、刷新、搜索或选择云端项目的动作均不改变 Project revision。
- 绑定写入成功恰好发布一次新 Project/projection；失败发布零次。
- 没有活动本地 Project 时只能创建本次临时目标，不能持久化。
- endpoint 或 account 不匹配时现有绑定保持可见但不可执行，用户必须显式重绑或切回对应配置。
- 操作计划的 request digest 必须包含目标 ID、来源和绑定 revision；目标变化后必须重新预检。
- UI、Agent、MCP 对同一目标解析输入得到等价结果和诊断。

## 备选方案

### A. 继续使用会话级“最后选择项目”

不采用。它无法随本地工程恢复，会把浏览动作变成远端副作用的隐式输入，并在多个本地工程之间串线。

### B. 按本地工程名自动匹配 ParaTranz 项目

不作为绑定。名称不唯一且可变，只可在选择器中作为建议；正式目标必须由用户确认项目 ID。

### C. 在全局 ParaTranz 配置中维护映射

不采用。映射与 Project 生命周期、导入/备份和并发 revision 脱离，并容易跨账号或端点污染。

### D. 让管理页当前选中即绑定

不采用。管理和同步是不同用户意图；显式动作才能产生 Project 变更。

## 风险、迁移与回退

分页目录可能受限流或大型账号影响，查询层需支持取消、缓存与明确失败状态；缓存只优化展示，不替代提交前验证。旧兼容字段在所有调用方迁移并通过 parity 测试后才能删除。

回退时可隐藏新 Workbench/管理页入口并停止写新绑定；已有 `remote_bindings.paratranz` 是可忽略的附加字段，不破坏旧读取器。不得通过恢复“最后浏览项目”回退目标解析规则。
