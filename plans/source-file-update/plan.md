# 更新源文件

- 状态：用户授权实现已完成，功能专项 QA 通过；完整回归及后续修正的验证证据见下文；ADR 保持提议。
- 日期：2026-10-01
- 需求：[FR19.14 更新源文件与全部翻译版本迁移](../../docs/requirements.md#fr19-source-update)；顺序衔接见 [FR22.9～FR22.11](../../docs/requirements.md#fr22-context-order)。
- 关联：[ADR-044](../../docs/adr/044-source-file-update.md)、工程版本持久化、来源登记及 paratranz-context-order。

## 目标与边界

提供“更新源文件…”入口，适用于已有来源的 ESP、EET XML、XT XML 等可解析格式。更新同一来源，迁移工程内全部翻译版本，不通过删除再新增丢弃译文。不自动写入 ParaTranz。

正常工程入口及来源丢失/已覆盖的只读恢复窗口均可进入。更新前显示新增、移除、原文变化、未能核实、顺序变化及影响版本数；确认后提交。无旧原文时不虚构差异，可靠 key 匹配的译文保留并标为待复核。严格按同来源 local_key 匹配，不模糊匹配。

## 当前事实与架构

Variant 只保存翻译状态和来源指纹；来源解析 hydration 提供原文/上下文/序号。现有 add/remove 不支持换源；只读恢复不能接受新版。单版本 save journal 不可通过循环冒充多版本原子迁移。

独立应用服务负责预览/确认令牌和迁移；独立持久化 store 负责所有版本与工程登记的 CAS、日志、故障恢复及旧数据备份；Qt coordinator/dialog 只负责选择、后台调度、确认和重新打开。超大 facade/lifecycle/UI 不承载新职责，仅接线或增加原有提交语义的持久化标志。

## Story 01：多版本来源迁移

- 新增 source_update_models/source_update_migration/source_update 应用模块。
- 新增 source_update_store 与事务恢复模块：校验全部版本与工程原始 hash/revision，先备份再提交，故障回滚，启动恢复。
- 来源登记 ID 保持稳定，更新内容 namespace/fingerprint/location；译文/标签/远端引用按唯一 key 转移。
- 原文变化或无法核实的已翻译项设为 QUESTIONABLE；新增用新版状态；移除项保存在完整备份。旧快照不改写。
- 新版源在预览后变化、工程或任一版本变化、owner 不匹配、重复确认必须拒绝。

## Story 02：界面与恢复闭环

- 工程菜单及来源管理入口使用“更新源文件…”，支持选择已有来源和新版文件。
- 重解析/预览/提交均在现有后台任务运行；当前脏改先保存，预览期间再次编辑则拒绝过期提交。
- 恢复窗口可更新指定工程；不得覆盖另一个正在编辑的工程。
- 成功后刷新当前工程来源顺序；可按已有 ParaTranz 预检流程更新远端序号。

## Story 03：验证与记录

- 真实 EET 文件覆盖多版本迁移、保存重开、顺序更新与远端同步；ESP 解析顺序相关回归。
- 覆盖未知旧原文、移除归档、CAS、重复令牌、取消、失败恢复、UI 后台接线。
- 运行聚焦 pytest、相关广泛回归和全库 Ruff；按真实 diff 生成增量记录。

阶段：分析、架构/计划、开发、QA、[增量记录](../../docs/changelogs/source-file-update/story-01-03/2026-10-01-001-source-file-update.md)均已完成。

## 验证与兼容结论

- 新增 38 项回归：12 项应用/EET/XT/重启/ParaTranz 衔接、3 项真实 ESP、17 项事务故障、6 项 UI。全部通过。
- 最终联合回归 464 passed；随后 XT 重启用例补齐后的应用测试 12 passed。全库 Ruff check/format（1432 文件）及 git diff --check 通过。
- 旧文件无法验证时保留译文但标为 QUESTIONABLE；本地化 ESP 的旧 Strings 版本未独立持久化，同样明确标记无法核验。
- 源文件本身不写入；正式工程 schema 不变；全部旧工程/版本前像保存到工程备份目录。旧快照不改写，跨来源修订直接加载仍受指纹校验保护。
- 后续修正了[首轮完整回归](../../docs/changelogs/source-file-update/story-01-03/2026-10-01-003-full-regression.md)暴露的测试、资源及生命周期问题。默认模式连续整库执行 4806 项，4797 通过、8 跳过、1 项检查点性能失败，无 Qt 崩溃；该性能瓶颈及后续发现的 shutdown 测试竞态已修复，最终 contracts 为 556 通过、2 跳过，连续 UI 为 1056 通过、1 跳过。最后修正后未重复整库，原始结果与最终复测分开记录，见[修正与验证记录](../../docs/changelogs/maintenance/regression-suite/2026-10-01-001-contracts-and-lifetimes.md)。
- 未运行发行安装包或真实 ParaTranz 写入；Qt 使用离屏自动化验证，未做用户桌面的人工验收。
- 责任审查：迁移规则、事务存储与 UI 调度均为独立模块。既有超 700 行 lifecycle 只增加其锁/代际校验与持久化提交语义接缝，不承担解析或迁移；MainWindow 仅组合接线。本轮不在数据迁移中夹带生命周期整体拆分，风险是该类仍较大；下次扩展生命周期领域行为前应先拆分 transition 与 active-content 提交并复验现有协议。
