# S17～18 补记：直接排除与项目提取缓存清理

日期：2026-11-13。Epic：project-terminology-build-versioning-reporting；S17 后续交互与 S18。此记录补齐本对话此前已落地但未单独记录的行为；本轮只有文档变更与复验，没有重新实现功能。

## 代码事实与原因

- `src/transbridge/ui/tools/terminology/conflict_review_pane.py`：原位“排除此术语”调用既有 IGNORE 并直接保存，无需选择译名或打开高级弹窗；插件特例保留在其他译名的高级设置。披露控件展开后滚到可见区域，解决点击无反馈。
- `src/transbridge/ui/tools/terminology/conflict_review_controller.py`：排除成功后前进，包括从已处理队列排除；失败保留当前项，复用预期草稿版本和安全撤销。
- `src/transbridge/ui/tools/terminology/extraction_cache_actions.py`、`window.py`、`presenter.py`：提取设置增加清理入口，后台执行，显示清理条数、空缓存、失败；防止重复提交及当前窗口任务期间清理，关闭/上下文变化丢弃回调。
- `src/transbridge/bootstrap/terminology_workloads.py`：`clear_extraction_cache` 绑定当前项目后调用项目 cache 的 extraction 清理。
- `src/transbridge/persistence/terminology/cache.py`：计数与清理在既有连接锁内完成。只清 extraction，不清草稿、发布版本、构建/解析缓存或其他项目。
- 对应既有测试：`test_conflict_review_pane.py`、`test_conflict_review_controller.py`、`test_terminology_cache_cleanup.py`、`test_extraction_cache_actions.py`。

## 本轮文档变更

FR5.16.8/.9 区分确定性归一化和模型初判；.37/.38 去除与已确认执行策略矛盾的三秒终态和缓存不再调用模型承诺；新增 .42～.45 描述模型整组判断、清理范围、直接排除和冲突工作簿。ADR-034 同步取消、缓存及判断阶段边界，保持提议状态。

计划补记 S17 后续及 S18，索引保留 S12 未完成；新增用户说明和核验报告。本记录不改写已有模型、性能或主从审核历史日志。

## 验证与边界

本轮聚焦复验中的缓存隔离/UI 4 项及冲突控件/协调器 13 项通过；完整命令、37 项联合证据见 [核验报告](../../../test-reports/terminology-conversation-documentation-2026-11-13.md)。文档本地链接、编号唯一性、`git -c core.safecrlf=false diff --check` 已核验。

未使用用户真实工程、未清理用户缓存、未重新测真实模型和打包退出时长。丢弃结果不等于清除缓存；排除不是删除原词条或永久屏蔽未来新输入。无新增迁移。无已知文档阻塞。
