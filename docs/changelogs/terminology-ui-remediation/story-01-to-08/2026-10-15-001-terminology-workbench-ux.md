# 术语工作台三批体验修复

- 日期：2026-10-15（2026-10-14 开始）。
- Epic：terminology-ui-remediation；Story：S01～S08。
- 计划：[术语界面与操作流程整改](../../../../plans/terminology-ui-remediation/plan.md)。
- 状态：三批本地实现、联合回归、独立审查及静态检查完成。
- 归属：相对于用户本次“按修改方向分三批全部修复”开始前的源码快照核对，共 98 个 Python 文件增量；此前九类后端修复见[既有记录](../../terminology-system-remediation/story-01-to-05/2026-10-14-001-terminology-system-fixes.md)，不重复归入本轮。用户已有的 pyproject.toml / uv.lock 版本改动保持原样。

## 用户可见结果

1. 第一批：方案管理定位已采用项；保存/放弃/取消保护完整和未完成编辑，重命名不丢输入；归档前显示实际影响且可恢复。每次构建重新捕获来源，“稍后处理”不写决定，同值译名也可确认复核。
2. 第二批：冲突显示完整候选、证据、插件和当前决定；发布前审查完整差异，提交绑定同一预览。历史比较有前后内容，放弃草稿、恢复历史有独立保护。同步显示目标、中文动作、全部影响、入站阻断项和逐条失败原因；失败不冒充可执行旧计划。准备/成功/失败在当前页面可见。
3. 第三批：统一项目术语、译名方案、外部来源；可见工程、翻译版本及当前术语版本。当前生效与 AI 共用组合算法；草稿、复核、异译和方案映射分开，孤立映射不冒充生效。全量筛选、前后分页、范围总数、刷新保留定位；导入预览不再截断到500行。方案切换立即作废旧查询。

## 逐文件增量

- 修改 `src/transbridge/bootstrap/terminology.py`：ProductionTerminologyComposition 接入完整浏览查询，与 AI 共用基础术语及方案服务。
- 新增 `src/transbridge/bootstrap/terminology_context.py`：提取 require_line / task_owner，统一作用域与任务所有者检查。
- 新增 `src/transbridge/bootstrap/terminology_publication.py`：提取 TerminologyPublicationCommands / PublishRunner；预览冻结配置和候选内容，发布复验完整输入身份。
- 新增 `src/transbridge/bootstrap/terminology_review.py`：TerminologyReviewCommands 读取当前草稿、冲突证据与待办；审核和放弃采用完整身份校验。
- 修改 `src/transbridge/bootstrap/terminology_storage.py`：发布提交复验 reviewed_content_digest，拒绝与用户预览不同的内容。
- 修改 `src/transbridge/bootstrap/terminology_workloads.py`：ProductionTerminologyCommands 委托独立发布与审核切片，按所有者读取任务结果和失败原因。
- 新增 `src/transbridge/application/terminology/browse_queries.py`：TermBrowseQuery / TermFilter / TerminologyBrowseQueries 对完整集合筛选；游标绑定草稿或生效快照，错误不冒充空列表。
- 修改 `src/transbridge/application/terminology/conflicts.py`：重复统一或排除冲突复用已有项目决定身份，保留插件例外。
- 新增 `src/transbridge/application/terminology/conflict_review.py`：TerminologyConflictDetails / TerminologyReviewCounts 提供完整候选、证据、插件与待办数量。
- 修改 `src/transbridge/application/terminology/decisions.py`：对相同译名的人工确认也可解除 REVIEW_REQUIRED，保留显式人工决定。
- 新增 `src/transbridge/application/terminology/publication_review.py`：TerminologyPublishPreview 保存请求、候选版本、配置及差异，供确认后原样提交。
- 修改 `src/transbridge/application/terminology/publish.py`：VersionPublisher 校验 reviewed_content_digest，保证审核与提交内容相同。
- 修改 `src/transbridge/application/terminology/runtime.py`：任务入口提供受 owner 限定的失败/结果查询，避免其他任务数据泄漏到当前界面。
- 新增 `src/transbridge/application/terminology_profiles/browse_queries.py`：ProfiledTerminologyBrowseSnapshotPort 复用 AI 组合算法；ProfileMappingRow 区分缺少基础、未确认、未映射和生效。
- 新增 `src/transbridge/application/terminology_profiles/import_preview.py`：import_preview_rows 投影完整映射及来源独有行，明确冲突、范围歧义及保留原因。
- 修改 `src/transbridge/application/terminology_profiles/in_memory.py`：归档审核支持列出实际绑定与预期绑定校验，与持久化语义一致。
- 修改 `src/transbridge/application/terminology_profiles/ports.py`：补充方案绑定查询和归档预期状态接口。
- 修改 `src/transbridge/application/terminology_profiles/service.py`：列出归档影响，归档确认与实际绑定不一致时拒绝操作；恢复不自动重新绑定。
- 修改 `src/transbridge/application/terminology_sync/service.py`：同步任务结果优先采用真实 FAILED/CANCELLED 状态，暴露失败原因与完整逐项结果。
- 修改 `src/transbridge/application/terminology_sync/task_adapter.py`：按作用域与所有者查询同步任务终态，补充可恢复失败信息。
- 修改 `src/transbridge/persistence/terminology/conflict_queries.py`：审核列表叠加当前草稿或已发布决定；游标绑定审核状态，旧构建查询仍保留原语义。
- 修改 `src/transbridge/persistence/terminology/profiles.py`：在事务中复验归档绑定集合，提供实际 Variant 影响范围。
- 修改 `src/transbridge/persistence/terminology/repository.py`：添加窄 list_review_conflicts 委托；复杂投影留在独立查询模块。
- 修改 `src/transbridge/ui/workbench/terminology_profile_bar.py`：统一译名方案与项目默认译名文案，明确采用的已发布修订。
- 修改 `src/transbridge/ui/workbench/terminology_profile_controller.py`：管理器定位当前方案，切换上下文时保留编辑取消语义及固定旧上下文；统一用户文案。
- 新增 `src/transbridge/ui/tools/terminology/asset_snapshot.py`：read_assets 在线程中收集构建、草稿、版本、差异、任务结果和浏览上下文。
- 新增 `src/transbridge/ui/tools/terminology/async_actions.py`：AsyncActionRunner 用队列信号派发后台命令；关闭和被替代请求不再交付。
- 修改 `src/transbridge/ui/tools/terminology/build_view.py`：重新检查入口持续可用；构建结果明确待审核/待发布及下一步。
- 新增 `src/transbridge/ui/tools/terminology/change_review_dialog.py`：CanonicalDiffView / ChangeReviewDialog 展示完整可搜索的词条增删改和前后详情。
- 修改 `src/transbridge/ui/tools/terminology/conflicts_view.py`：提供完整证据审核入口及前后分页，保持筛选。
- 新增 `src/transbridge/ui/tools/terminology/conflict_review_dialog.py`：完整候选/证据虚拟表；保留、采用、按插件使用及显式排除有独立操作。
- 新增 `src/transbridge/ui/tools/terminology/draft_interactions.py`：提取增改、启停与审核交互；稍后处理不写决定，同值编辑可确认复核。
- 修改 `src/transbridge/ui/tools/terminology/draft_view.py`：增加完整集合原词/译名、范围、状态筛选与分页入口。
- 新增 `src/transbridge/ui/tools/terminology/effective_view.py`：只读当前生效列表，明确草稿需发布后生效，提供全量筛选和分页。
- 修改 `src/transbridge/ui/tools/terminology/history_view.py`：实际版本差异替代计数摘要；历史列表有明确分页。
- 新增 `src/transbridge/ui/tools/terminology/launcher.py`：从窗口提取 TerminologyLauncher，保留原窗口导入兼容。
- 修改 `src/transbridge/ui/tools/terminology/object_views.py`：区分当前生效/草稿/复核/异译/方案映射；整理动作常驻、结果可展开；提供放弃草稿入口。
- 新增 `src/transbridge/ui/tools/terminology/operation_notice.py`：持久就地操作消息与技术详情，失败无需切去报告页寻找。
- 新增 `src/transbridge/ui/tools/terminology/paged_dispatch.py`：提取分页后台派发，cancel token、generation 和 QObject 生命周期阻止迟到回调。
- 修改 `src/transbridge/ui/tools/terminology/paged_models.py`：KeysetPagedTableModel 增加手动导航与刷新，同时保留旧 fetchMore 合约。
- 新增 `src/transbridge/ui/tools/terminology/page_window.py`：分页游标地址历史与有界行缓存分离，支持已淘汰页面返回及过期复位。
- 新增 `src/transbridge/ui/tools/terminology/pagination_bar.py`：首页/上一页/下一页、范围总数和就地重试，过期原因可见。
- 修改 `src/transbridge/ui/tools/terminology/presenter.py`：每次构建重新冻结配置和捕获输入；委托审核/预览/任务失败查询，新增 browsing 可选服务保持位置参数兼容。
- 修改 `src/transbridge/ui/tools/terminology/reports_view.py`：报告生成后显示实际文件路径，支持用户打开文件或所在目录。
- 修改 `src/transbridge/ui/tools/terminology/schemes_controller.py`：方案创建/管理动作统一文案，异步完成后按钮恢复正确名称。
- 修改 `src/transbridge/ui/tools/terminology/schemes_view.py`：显示译名方案、默认译名和已应用修订，避免混淆工程翻译版本。
- 修改 `src/transbridge/ui/tools/terminology/source_browser.py`：方案映射完整搜索、范围/匹配状态筛选，基础缺失和未确认明确标注，可转到草稿。
- 新增 `src/transbridge/ui/tools/terminology/sync_copy.py`：集中中文同步动作、状态、原因和目标描述。
- 新增 `src/transbridge/ui/tools/terminology/sync_details.py`：分页查看全部计划和逐项结果；保留词条名、前后值、删除影响及重试原因。
- 新增 `src/transbridge/ui/tools/terminology/sync_inbound_view.py`：入站变化显示拟写草稿、阻断项和定位编辑，修改后重新预览。
- 修改 `src/transbridge/ui/tools/terminology/sync_presenter.py`：失败/取消终态优先，不把缺少结果的失败任务恢复为旧可执行计划。
- 修改 `src/transbridge/ui/tools/terminology/sync_task_adapter.py`：限定当前同步作业，重开读取最近状态；取消与失败原因向界面传递。
- 修改 `src/transbridge/ui/tools/terminology/sync_view.py`：组合计划/结果/入站审查；确认目标与删除影响，连接取消与草稿刷新。
- 新增 `src/transbridge/ui/tools/terminology/table_models.py`：提取表格列与工厂；查询服务缺失传递真实错误，不伪装成零条。
- 新增 `src/transbridge/ui/tools/terminology/term_browsing.py`：保留独立筛选和页位置；切方案立即清除并重新绑定生效查询。
- 新增 `src/transbridge/ui/tools/terminology/term_filters.py`：共用全量搜索、范围与草稿状态控件，待复核可直接定位。
- 新增 `src/transbridge/ui/tools/terminology/version_interactions.py`：发布确认提交同一预览；放弃草稿与恢复历史采用显式确认和失败短路。
- 修改 `src/transbridge/ui/tools/terminology/view_models.py`：日志失败识别限制到 CHANGELOG，避免 TERMINOLOGY 被误认为发布日志失败。
- 修改 `src/transbridge/ui/tools/terminology/window.py`：组合窄交互组件，后台准备与就地反馈；刷新版本/草稿/待办，拒绝方案切换晚结果。
- 修改 `src/transbridge/ui/tools/terminology/workbench_shell.py`：可见展示工程、翻译版本、来源数及当前术语版本。
- 新增 `src/transbridge/ui/tools/terminology_profiles/edit_prompts.py`：集中保存/放弃/取消编辑保护，取消不丢输入。
- 修改 `src/transbridge/ui/tools/terminology_profiles/import_dialog.py`：取消500行截断，完整预览、搜索、诊断筛选和明细；名称与选用设置不随筛选丢失。
- 新增 `src/transbridge/ui/tools/terminology_profiles/import_preview_model.py`：虚拟行模型与组合诊断过滤，筛选不改变实际待创建的完整方案。
- 修改 `src/transbridge/ui/tools/terminology_profiles/manager_dialog.py`：定位已采用方案；未保存改动保护、重命名保留输入、归档影响审核及恢复入口。
- 新增 `src/transbridge/ui/tools/terminology_profiles/mapping_editor.py`：提取映射编辑状态及原始输入脏检查，包括尚不完整的行。
- 修改 `src/transbridge/ui/tools/terminology_profiles/source_import_controller.py`：外部来源导入反馈与按钮状态统一为译名方案操作。
- 修改 `src/transbridge/ui/tools/terminology_profiles/source_picker_dialog.py`：统一外部来源文案，与方案和项目术语的对象边界一致。
- 新增 `tests/application/terminology/test_browse_queries.py`：回归覆盖：`test_filter_and_pagination_cover_the_entire_collection`、`test_scope_and_status_filters_do_not_confuse_review_or_excluded_with_effective`、`test_draft_cursor_binds_the_full_draft_identity_even_when_rows_are_unchanged` 等。
- 新增 `tests/application/terminology_profiles/test_import_preview.py`：回归覆盖：`test_preview_rows_cover_all_mappings_and_excluded_sources_without_changing_import`。
- 新增 `tests/application/terminology_profiles/test_profile_browsing.py`：回归覆盖：`test_profile_mapping_states_match_ai_and_preserve_plugin_scope`、`test_unpublished_base_explains_all_mappings_without_enabling_them`、`test_default_profile_context_uses_base_decisions_and_has_no_mapping_rows` 等。
- 修改 `tests/application/terminology_sync/test_service.py`：回归覆盖：`test_shared_service_keeps_one_plan_ref_and_pages_bounded_items`、`test_bidirectional_plan_is_visible_but_not_executable_without_s05_task_entrypoint`、`test_mapping_replacement_confirmation_is_invalid_after_target_drift`。
- 新增 `tests/application/terminology_sync/test_task_failure_feedback.py`：回归覆盖：`test_failure_before_item_results_keeps_original_reason_and_owner_guard`、`test_result_item_names_use_authorized_plan_without_new_remote_read`。
- 新增 `tests/contracts/terminology/test_task_failure.py`：回归覆盖：`test_task_failure_retains_cause_and_checks_owner_before_returning_it`、`test_cancelled_task_has_no_failure_message`。
- 新增 `tests/integration/bootstrap/test_terminology_browsing.py`：回归覆盖：`test_production_browsing_uses_sqlite_draft_and_the_ai_profile_composition`、`test_old_services_positional_construction_remains_compatible`。
- 新增 `tests/integration/bootstrap/test_terminology_conflict_review.py`：回归覆盖：`test_review_lists_overlay_current_draft_and_details_include_all_evidence`、`test_repeat_reviews_update_project_and_plugin_choices_without_duplicates`、`test_open_review_rejects_changed_draft_build_or_unknown_plugin` 等。
- 新增 `tests/integration/bootstrap/test_terminology_presenter_build.py`：回归覆盖：`test_consecutive_presenter_builds_capture_the_current_draft`、`test_presenter_rebuild_captures_new_sources_without_manual_preflight`、`test_source_change_after_fresh_capture_still_rejects_the_build_commit` 等。
- 新增 `tests/integration/bootstrap/test_terminology_publication_review.py`：回归覆盖：`test_preview_is_read_only_and_task_runtime_publishes_the_exact_reviewed_version`、`test_preview_rejects_changed_inputs_before_task_submission`、`test_reviewed_draft_changed_after_submission_is_not_published` 等。
- 新增 `tests/persistence/terminology/test_profile_archive_review.py`：回归覆盖：`test_archive_impact_is_checked_atomically_and_restore_never_rebinds`。
- 新增 `tests/ui/tools/test_terminology_import_full_preview.py`：回归覆盖：`test_import_preview_searches_after_row_500_and_preserves_creation_options`、`test_diagnostic_filters_explain_scope_conflict_and_source_only_exclusion`、`test_import_creation_requires_name_even_when_empty_at_construction`。
- 新增 `tests/ui/tools/test_terminology_profile_edit_protection.py`：回归覆盖：`test_current_profile_and_rename_preserve_edits_and_revision`、`test_rename_cannot_hide_concurrent_content_change`、`test_switch_respects_save_discard_cancel` 等。
- 修改 `tests/ui/tools/test_terminology_profile_manager.py`：回归覆盖：`test_manager_saves_mapping_without_dropping_overrides_or_bindings_and_publishes`、`test_manager_create_copy_rename_and_archive`。
- 修改 `tests/ui/tools/test_terminology_source_import_dialog.py`：回归覆盖：`test_import_dialog_explains_snapshot_and_defaults_to_not_switching`。
- 修改 `tests/ui/workbench/test_terminology_profile_switching.py`：回归覆盖：`test_controller_lists_only_published_profiles_and_persists_selection`、`test_failed_switch_retains_exact_published_rows_and_exposes_failure`、`test_controller_creates_published_scheme_from_source_without_selecting_it` 等。
- 新增 `tests/ui/tools/terminology/test_async_actions.py`：回归覆盖：`test_replaced_reads_and_closed_windows_drop_late_delivery`、`test_call_failure_preserves_original_exception_and_skips_success`。
- 新增 `tests/ui/tools/terminology/test_browsing_failures.py`：回归覆盖：`test_missing_query_service_is_reported_by_pagination_instead_of_empty_success`、`test_profile_switch_invalidates_old_page_even_when_asset_refresh_fails`。
- 新增 `tests/ui/tools/terminology/test_change_review_dialog.py`：回归覆盖：`test_review_shows_translation_scope_status_and_full_details`、`test_all_changes_are_searchable_including_rows_after_500_and_hidden_notes`、`test_missing_sides_and_conflict_status_changes_have_explicit_meaning` 等。
- 新增 `tests/ui/tools/terminology/test_draft_interactions.py`：回归覆盖：`test_postponing_a_conflict_does_not_modify_the_draft`、`test_excluding_a_conflict_is_an_explicit_draft_mutation`、`test_conflict_dialog_shows_full_evidence_and_selects_actual_plugin` 等。
- 新增 `tests/ui/tools/terminology/test_effective_browsing.py`：回归覆盖：`test_real_window_distinguishes_effective_draft_and_orphan_profile_mapping`、`test_real_window_full_search_and_refresh_preserve_page_and_filters`。
- 新增 `tests/ui/tools/terminology/test_manual_pagination.py`：回归覆盖：`test_last_page_can_return_to_evicted_first_rows_without_automatic_scroll_loading`、`test_refresh_retains_page_and_filters_and_stale_last_page_returns_to_first`、`test_failed_navigation_keeps_current_rows_and_retry_loads_requested_page` 等。
- 新增 `tests/ui/tools/terminology/test_operation_feedback.py`：回归覆盖：`test_command_failure_is_visible_on_the_current_terms_page`、`test_publish_completion_refreshes_version_but_stale_result_is_not_success`、`test_fast_terminal_event_cannot_be_overwritten_by_late_submission_acknowledgement`。
- 修改 `tests/ui/tools/terminology/test_presenter.py`：回归覆盖：`test_preflight_maps_prerequisite_failure_to_business_language`、`test_preflight_projects_scope_without_reading_sqlite_or_counting_rows`、`test_preflight_prefers_display_names_for_the_visual_project_context` 等。
- 新增 `tests/ui/tools/terminology/test_publish_flow.py`：回归覆盖：`test_window_review_to_runtime_publication_refreshes_or_reports_stale`。
- 修改 `tests/ui/tools/terminology/test_schemes_view.py`：回归覆盖：`test_scheme_switcher_keeps_selection_visible_and_management_in_a_menu`、`test_scheme_switcher_displays_none_when_no_library_is_selected`、`test_scheme_controller_passes_one_picker_selection_to_background_import`。
- 修改 `tests/ui/tools/terminology/test_source_browser.py`：回归覆盖：`test_browser_searches_the_full_published_copy_and_combines_scope_filter`、`test_browser_replaces_and_clears_selected_copy_without_stale_rows`、`test_source_strip_displays_exact_selected_revision_and_switch_failure` 等。
- 修改 `tests/ui/tools/terminology/test_sync_lifecycle.py`：回归覆盖：`test_runtime_activity_reaches_controls_only_on_gui_thread_and_detaches_on_close`、`test_background_result_after_parent_deletion_never_emits_to_deleted_qobject`、`test_open_panel_receives_background_success_and_failure_on_gui_thread` 等。
- 新增 `tests/ui/tools/terminology/test_sync_review_details.py`：回归覆盖：`test_all_plan_pages_expose_target_chinese_actions_and_delete_impact`、`test_partial_result_pages_keep_term_names_failure_reason_and_retry_recovery`、`test_runtime_failure_without_result_supersedes_old_plan_and_late_submit_completion` 等。
- 新增 `tests/ui/tools/terminology/test_version_interactions.py`：回归覆盖：`test_publish_waits_for_preview_and_submits_exact_reviewed_object`、`test_closing_context_during_review_prevents_publish`、`test_discard_requires_review_and_refreshes_only_after_success` 等。
- 修改 `tests/ui/tools/terminology/test_window_lifecycle.py`：回归覆盖：`test_window_close_releases_subscription_and_all_query_models`、`test_window_mounts_sync_as_a_versions_child_without_expanding_window_workflow`、`test_versions_page_scrolls_instead_of_clipping_on_a_short_screen` 等。

## 文档及职责

- 新增 `plans/terminology-ui-remediation/plan.md`，记录三批 Story、验收及职责复核；更新 `plans/INDEX.md` 对应条目。
- 新增本记录，最小更新 `docs/changelogs/INDEX.md`；不修改历史增量。
- 窗口、编辑、发布/历史交互、分页缓存和派发按职责提取，未用 mixin 隐藏规模。
- 既有大门面例外详见 plan：组合装配 683 行、仓库 583 行/45 方法、Presenter 453 行/36 方法。本轮复杂语义已放到独立模块，保留现有公共门面；下次增加业务职责前先拆端口分组/装配。无新增依赖、数据库迁移、提交或推送。

## 验证证据

使用仓库现有 Python 3.12.12 / uv 环境；`uv --cache-dir .tmp-terminology-ux-fixes-20261014/uv run --no-sync` 为以下命令统一前缀。任务临时目录仅用于测试、源码归属快照与 Qt 截图，交付时清理。

- 第一批：主会话74条术语/真实构建与63条方案验证通过；方案切片扩大80条通过，独立复核29条通过。
- 第二批：主会话104条联合验证通过；后端切片228条和同步切片83条通过。包含真实 Qt → 发布预览 → TaskRuntime → SQLite 成功/过期拒绝。
- 第三批：浏览新增25条、方案/导入36条、分页联动16条通过；真实窗口601条搜索/定位/生效组合新增2条通过；交叉审查发现的缺失服务与方案切换晚结果新增3条通过。上述集合相互重叠，不累加为总数。
- 实际 Qt 离屏渲染检查：中文字体、920×620工作台的生效/草稿/版本页面、完整冲突审核与612项导入预览。版本页纵向滚动，不裁掉内容。未把静态代码阅读当作人工桌面操作。

最终合并命令：

```powershell
uv --cache-dir .tmp-terminology-ux-fixes-20261014/uv run --no-sync python -m pytest tests/application/terminology tests/application/terminology_profiles tests/application/terminology_sync tests/application/translation tests/persistence/terminology tests/contracts/terminology tests/contracts/terminology_sync tests/contracts/translation tests/contracts/io/test_terminology_profile_write.py tests/contracts/paratranz/test_terms_api_contract.py tests/integration/terminology tests/integration/terminology_sync tests/integration/bootstrap tests/integration/translation tests/ui/tools/terminology tests/ui/tools/test_terminology_profile_manager.py tests/ui/tools/test_terminology_profile_edit_protection.py tests/ui/tools/test_terminology_source_import_dialog.py tests/ui/tools/test_terminology_source_import_controller.py tests/ui/tools/test_terminology_import_full_preview.py tests/ui/tools/test_ai_project_terminology.py tests/ui/workbench tests/ui/operations/test_strict_write_routes.py tests/integration/publish/test_plugin_transfer.py tests/smart_assistant/tools/test_terminology_sync_tools.py tests/ai_translator -q -m 'not slow and not llm' -o 'addopts=--tb=short --strict-markers' --basetemp=.tmp-terminology-ux-fixes-20261014/f
```

该次结果：1388 passed、1 failed、1 skipped、1 deselected，47.94秒。唯一失败为 `test_translation_replaces_full_progress_before_postprocess_request[strict]` 在既有后处理断点 `os.replace` 遇到 Windows WinError5。该测试在前一轮1209集合中通过；失败目录/目标无只读或拒绝ACL，已有revision2有效内容，检查时可独占只读打开；未发现本轮改动导致竞态的证据，不能事后确认瞬时占用来源。

随后对该文件与断点契约复验：

```powershell
uv --cache-dir .tmp-terminology-ux-fixes-20261014/uv run --no-sync python -m pytest tests/ai_translator/test_translation_progress.py tests/contracts/translation/test_postprocess_checkpoint.py -q --basetemp=.tmp-terminology-ux-fixes-20261014/t
uv --cache-dir .tmp-terminology-ux-fixes-20261014/uv run --no-sync python -m pytest tests/application/terminology/test_reports.py -q -m slow --basetemp=.tmp-terminology-ux-fixes-20261014/s
```

结果分别为8 passed、1 passed（5万条 SQLite→Excel 流式报告，42.40秒）。因此相关集合去重后1390项均获得通过证据，1项真实ParaTranz账户测试跳过；不将分组复验说成单次全绿。

此前较长 `root-final-regression` 临时路径造成2项校对SQLite日志路径过长；改为短路径后该文件24项及最终合并中的对应测试均通过。沙箱内局部文件系统、原生FAISS扩展和本地HTTP受限的测试，在获批本地环境复验；测试未访问真实远端写接口。

静态检查：

```powershell
uv --cache-dir .tmp-terminology-ux-fixes-20261014/uv run --no-sync ruff check src tests
uv --cache-dir .tmp-terminology-ux-fixes-20261014/uv run --no-sync ruff format --check src tests
git -c core.safecrlf=false diff --check
```

全部通过；格式检查1685个文件。独立审查覆盖查询/AI组合、完整导入、分页缓存及关闭、窗口失败与方案切换晚结果。

## 兼容性与边界

- 原格式、已发布不可变版本、CAS/来源新鲜度检查保留；不自动改写普通项目译文。
- 旧 TerminologyUiServices 五个位置参数兼容，分页默认自动模式继续支持现有调用。
- 未运行全仓库所有测试或真实LLM/真实ParaTranz账户写入；相关本地HTTP、SQLite、Qt、报告、导出/写回及AI消费已验证。
- 三批功能无未完成项。既有Windows临时文件瞬时占用在复验中未复现，保留本次失败事实；未以扩大无关重试逻辑来掩盖它。
