# 更新源文件与全部翻译版本迁移

- 日期：2026-10-01
- Epic：source-file-update
- Story：01 多版本迁移；02 UI 与恢复闭环；03 验证与记录
- 计划：[plan](../../../../plans/source-file-update/plan.md)
- 架构：[ADR-044](../../../adr/044-source-file-update.md)

## 用户可见变化

“项目”菜单、工作台工程管理菜单、只读恢复窗口新增“更新源文件…”。选择工程中的既有来源和同格式新版文件，预览新增、移除、内容变化、原文无法核验、顺序变化及影响版本数；确认后更新全部翻译版本。覆盖 ESP、EET XML、XT XML 等既有解析器支持的来源。

按来源内唯一 key 保留译文、标签与远端引用；内容变化的译文标为待复核；新词条取新版基线；移除条目的旧状态保存在更新前备份。旧源文件已被覆盖或丢失时可从恢复窗口继续，不再只有恢复旧文件这一条路。若旧原文无法验证则明确提示并把匹配译文标为待复核。本地化 ESP 的历史 Strings 指纹未独立保存，也采用此规则。

正常工程先保存现有修改。预览后再编辑工程、任何版本文件或新版源文件，会拒绝过期提交；确认令牌限制 owner 且只能使用一次。恢复其他工程不会切走当前工作台。源文件更新不改写磁盘上的插件/XML，不自动写 ParaTranz；新顺序可随后通过既有同步预检更新远端 context。

## 逐文件变化

- 新增 `application/projects/source_update_models.py`：来源选择、预览及结果 DTO。
- 新增 `application/projects/source_update_migration.py`：来源内容/顺序比较与全部版本的精确 key 迁移。
- 新增 `application/projects/source_update.py`：两阶段更新、owner/代际校验、来源登记与 namespace 更新；保留配对关系和 plugin_scope；活动/非活动工程分别提交。
- 修改 `application/projects/lifecycle.py`：原有 active-content 提交增加持久化 revision 标志和 generation 检查；非活动内容提交串行化并使旧激活计划失效。
- 新增 `persistence/source_update_store.py`：完整工程与全部版本的 revision/hash CAS、持久备份、事务日志、提交标记、失败回滚及启动恢复。
- 修改 `bootstrap/persistence.py`、`bootstrap/composition.py`：启动早期恢复事务，注册 project_source_updates。
- 新增 `ui/coordinators/source_update_coordinator.py`、`ui/source_update_preview.py`：后台选择/预览/提交、取消、失败提示、成功重开。
- 修改 `ui/coordinators/__init__.py`、`project_coordinator.py`、`ui/main_window.py`、`ui/project_recovery.py`、`ui/workbench/_project_bar.py`、`ui/shell/action_catalog.py`、`intent_composition.py`、`menu_builder.py`：菜单、意图、恢复窗与组合接线。
- 新增 `tests/application/projects/test_source_update.py`：12 项 EET/XT、多版本、保存重开、远端序号衔接、过期、owner、取消、备份回归。
- 新增 `tests/application/projects/test_source_update_plugin.py`：3 项真实二进制 ESP，覆盖 namespace 迁移、配对来源和本地化 Strings 覆盖。
- 新增 `tests/persistence/v2/test_source_update_store.py`：17 项，覆盖各发布点失败、崩溃/启动恢复、外部改写拒绝、备份失败、完成标记失败、日志清理失败后继续保存重启。
- 新增 `tests/ui/test_source_update.py`：6 项，覆盖菜单意图、确认文字、后台调度、取消、失败与恢复工程隔离。
- 新增 ADR/plan，并更新相关索引；更新 ParaTranz 当前计划的来源更新入口及顺序持久化说明，既有历史增量保持原样。

以上路径以 `src/transbridge/` 为生产文件根。此前 ParaTranz 格式改动、助手 UI 与上下文压缩工作区改动未归入本次实现。

## 验证

最终联合回归：464 passed，3 项既有依赖弃用警告，无失败。

```powershell
uv run pytest tests/application/projects tests/persistence/v2 tests/persistence/test_project_recovery.py tests/ui/test_source_update.py tests/ui/test_project_recovery.py tests/contracts/paratranz/test_sync_order_refresh.py tests/integration/bootstrap tests/parser/test_plugin_tree_order.py tests/ui/test_main_window_shell.py tests/ui/test_main_window_coordinators.py tests/ui/test_intent_router.py -q
```

随后补齐 XT 运行时重启验证，应用测试 12 passed（包含在总计 38 项新增回归中，其他集合有重叠，不累加）：

```powershell
uv run pytest tests/application/projects/test_source_update.py -q
uv run ruff check src tests
uv run ruff format --check src tests
git diff --check
```

全库 Ruff 通过，1432 文件格式合规，diff 检查通过。独立审查发现并修复了配套 Strings 旧版本不可核验、plugin_scope 保留以及成功事务遗留日志影响后续保存的问题。

## 边界与责任审查

工程 schema 不变；翻译版本仍只保存业务状态，原文与顺序由登记来源解析。旧历史快照不迁移，跨新版来源直接恢复仍需满足既有指纹条件。旧文件不可用时无法精确区分原文变化与仅顺序变化，预览会明确说明。

事务備份保存旧工程及全部版本数据，不承诺还原已被用户覆盖的旧插件文件。备份位于工程备份目录，可用于取回被移除词条的译文；本轮不新增备份浏览器或一键逆向迁移界面。

新职责均在独立模块，既有大 lifecycle/MainWindow 仅增加提交接缝与组合调用；拆分条件详见计划。未运行全库 pytest、发行打包、用户桌面人工验收或真实 ParaTranz 写入。未提交 Git。
