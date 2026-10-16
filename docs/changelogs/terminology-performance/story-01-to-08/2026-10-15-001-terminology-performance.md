# 术语系统三批性能优化与回归验证

日期：2026-10-15。Epic：`terminology-performance`；Story：S01–S08。关联：[实施计划](../../../../plans/terminology-performance/plan.md)。

三批实现、性能复测和相关功能回归已完成。大集合编辑、分页、搜索、投影与同步展示的重复工作已减少；保存和昂贵读取进入既有后台执行模式。冷首屏仍受整份 JSON 解码、摘要校验和 GC 影响，本记录不宣称全部性能预算通过。

归属依据是任务开始时的源码快照、哈希与本轮差异，而非将 dirty HEAD 的全部变化归到本次。逐文件清单中的新增/修改均相对该起始快照；此前术语系统修复、UI 整改与用户既有修改保留。`pyproject.toml`、`uv.lock` 与本轮起始内容一致；没有数据库迁移、摘要 namespace 或持久化格式变更，没有新增依赖，没有提交或推送 Git。

## 计划与归属

- **修改 `docs/changelogs/INDEX.md`**：仅增加本轮记录入口与验证结果，保留已有历史内容。
- **新增 `docs/changelogs/terminology-performance/story-01-to-08/2026-10-15-001-terminology-performance.md`**：记录三批改动、逐文件归属、实际验证及仍未达到的性能预算。

- **修改 `plans/INDEX.md`**：增加本轮 `terminology-performance` 计划入口，便于从计划索引追溯性能工作；不重算无关计划状态。
- **新增 `plans/terminology-performance/plan.md`**：定义 S01–S08 三批实施顺序、数据与摘要兼容约束、性能测量边界和批次验证记录，明确保护此前 dirty 工作树、不访问真实服务。

## S01：草稿保存退出 Qt 主线程

- **新增 `src/transbridge/ui/tools/terminology/draft_write_queue.py`**：`DraftWriteQueue.submit/_start_next/_saved/_failed/close` 将同窗口草稿命令串行交给后台，成功提交后再通知；失败允许继续，关闭丢弃未开始命令与 UI 回调，已开始提交继续完成。
- **修改 `src/transbridge/ui/tools/terminology/window.py`**：`_run_draft_command/_draft_saved/closeEvent` 接入串行保存与准确反馈；同文件的 `_bind_existing_assets/_apply_assets` 改为 S07 的刷新协调器和呈现组件，避免窗口继续承担异步合并业务。
- **新增 `tests/ui/tools/terminology/test_draft_write_queue.py`**：覆盖事件循环仍可响应、保存顺序、原异常保留与后续恢复、关闭时已开始提交继续、窗口仅在后台提交成功后显示“已保存”。

## S02：完整集合搜索预计算与防抖

- **新增 `src/transbridge/ui/tools/terminology/row_search.py`**：`SearchDocument/RowMatches/build_search_index/match_search_index` 提供与 Qt 无关的大小写归一化索引、范围/状态组合筛选和源行到可见行映射；逐项检查取消，不逐键重新构造全部展示文本。
- **新增 `src/transbridge/ui/tools/terminology/async_row_filter.py`**：`AsyncRowFilter.set_rows/set_query/_finished/close` 管理数据版本、请求代次、200ms 防抖和生命周期；大集合在 worker 构建/筛选纯数据，小集合保留直接路径，错误交付给视图。
- **新增 `src/transbridge/ui/tools/terminology/filtered_rows_model.py`**：`FilteredRowsModel.set_matches/mapToSource/mapFromSource` 仅映射筛选后的源行，复用源模型数据和选择身份，避免 Qt 逐行调用筛选逻辑扫描完整集合。
- **修改 `src/transbridge/ui/tools/terminology/change_review_dialog.py`**：`CanonicalDiffView.set_comparison/_filter/_matches_ready/_search_status/close_search` 接入预计算后台搜索、等待/错误状态并恢复选择详情；移除旧 `_search_document` 即时整行拼接路径。
- **修改 `src/transbridge/ui/tools/terminology/source_browser.py`**：`SourceBrowser.render/render_context/_filter/_matches_ready/_update_count` 使用完整映射集合的后台索引，保留原文/方案译文/基础译文搜索和范围、映射状态筛选；替换原 `_SourceFilter` 逐行过滤实现。
- **修改 `src/transbridge/ui/tools/terminology_profiles/import_preview_model.py`**：`ImportPreviewFilterModel` 改接公共行映射模型和 `AsyncRowFilter`，`set_filters/close` 维护文本、范围及诊断筛选与生命周期，避免每次输入重新扫描并格式化 Qt 数据。
- **修改 `src/transbridge/ui/tools/terminology_profiles/import_dialog.py`**：`_apply_filters/_filters_ready/_remember_selection/_search_status/_update_count` 显示异步筛选状态并保持选中源记录，关闭/拒绝对话框停止待处理搜索。
- **修改 `src/transbridge/ui/tools/terminology/__init__.py`**：通过 `TYPE_CHECKING` 与 `__getattr__` 延迟导入 `TerminologyWindow/TerminologyLauncher`，保留公共名字，解除公共搜索模型被方案 UI 复用后的包初始化循环。
- **新增 `tests/ui/tools/terminology/search_test_support.py`**：`wait_search` 为使用后台筛选的 Qt 测试提供有界事件循环等待，替代立即读取旧模型状态。
- **新增 `tests/ui/tools/terminology/test_async_row_filter.py`**：覆盖一次索引、多次筛选、真实 worker 执行与 GUI 交付、防抖、数据/查询替换后的晚结果隔离、关闭取消以及可见错误。
- **修改 `tests/ui/tools/terminology/test_change_review_dialog.py`**：原完整差异搜索用例等待异步完成，并增加筛选后原选中行详情保留的断言；不把既有 500 行后搜索覆盖重复记为新增。
- **修改 `tests/ui/tools/terminology/test_source_browser.py`**：既有搜索断言适配 `wait_search`；新增后台搜索保留选择、基础译文、范围和状态组合以及原有搜索字段边界的验证。
- **修改 `tests/ui/tools/test_terminology_import_full_preview.py`**：既有完整导入/诊断筛选测试等待后台结果；新增搜索保持选中映射、清空搜索保持身份及 reject 停止待处理任务的覆盖。
- **新增 `tests/ui/tools/terminology/test_package_imports.py`**：在独立解释器中验证术语包与方案包的不同导入顺序，防止共享模型导致循环导入回归。

## S03：方案映射编辑器采用 Model/View

- **新增 `src/transbridge/ui/tools/terminology_profiles/mapping_model.py`**：`ProfileMappingModel` 保存原始可编辑行和 dirty 状态；`setData/removeRows/append_mapping/read_mappings` 支持无效中间输入、范围切换及精确有序内容比较，避免逐格编辑时重新扫描全表。
- **新增 `src/transbridge/ui/tools/terminology_profiles/mapping_delegate.py`**：`ProfileMappingDelegate.createEditor/setEditorData/setModelData/commit_pending` 只为当前编辑单元格创建控件，处理范围选择和未离焦文本提交，移除每行常驻下拉框成本。
- **修改 `src/transbridge/ui/tools/terminology_profiles/mapping_editor.py`**：`ProfileMappingEditor` 从 `QTableWidget` 控件集合迁至表视图、专属模型和委托；保留 load、读映射、增删及 dirty 接口，新增 `commit_pending_edits`，删除原全表反查/重建逻辑。
- **修改 `src/transbridge/ui/tools/terminology_profiles/manager_dialog.py`**：`_guard_unsaved/_rename_profile` 先提交活动委托中的文本，再判断未保存状态或重命名，避免焦点尚未离开时漏掉用户输入。
- **新增 `tests/ui/tools/test_terminology_profile_mapping_model.py`**：验证大集合只实例化活动编辑器、模型单格修改、无效输入后恢复、插件范围编辑性、结构变更/还原的 dirty 判断，以及管理器读取未离焦文本。
- **修改 `tests/ui/tools/test_terminology_profile_manager.py`**：原保存/发布及绑定保留验证改为通过模型 `setData/headerData` 操作，保持原业务断言而适配新编辑接口。
- **修改 `tests/ui/tools/test_terminology_profile_edit_protection.py`**：原保存/放弃/取消、重命名并发保护、无效行和上下文切换用例改为模型编辑，并等待 S06 的异步方案加载；既有编辑保护语义不变。

## S04：草稿保存去重复、JSON 与 canonical 热路径

- **修改 `src/transbridge/application/terminology/drafts.py`**：新增 `DraftWriteExpectation.from_ref` 并让 `from_draft` 委托；`DraftService.save` 使用命令已有快照作事务外检查，把最终状态验证留在原子事务内，避免重复读取完整草稿。
- **修改 `src/transbridge/application/terminology/identity.py`**：`_canonical_value` 增加精确内建标量快速路径，`_field_names` 有界缓存 dataclass 字段名；`canonical_bytes` 在完整归一化后委托专属编码器，保留自定义 canonical 调用顺序、Enum、非有限浮点拒绝、原 bytes 和 namespace。
- **新增 `src/transbridge/application/terminology/canonical_json.py`**：`encode_normalized_json/_requires_chunking/_parts` 以 512 节点预算判断小集合单次 C 编码路径，大列表按 128 项拆分编码后拼接；保留原 JSONEncoder 参数与精确输出，减少常规扁平记录集合的一次长 C 调用，不把任意单个巨值的响应时间作为保证。
- **修改 `src/transbridge/bootstrap/terminology_draft_access.py`**：`LifecycleDraftLineReader.read_line` 读取轻量 `effective_version_ref`，仅为构造预期状态时不再解码整个已发布版本。
- **修改 `src/transbridge/bootstrap/terminology_review.py`**：`TerminologyReviewCommands._decision_service` 用完整 `active_ref` 构造写入预期，保留用户打开审核后携带的引用校验，避免额外草稿物化。
- **修改 `src/transbridge/persistence/terminology/codec.py`**：`_encode/_decode` 使用预计算的受支持 DTO 字段/必填信息及精确标量快速路径；`loads/_object_boundary` 对大 JSON 添加仅返回原对象的解析回调，`_decode/_consume_items` 消费本次解析的私有树并及时释放旧对象。保持 DTO 构造校验、类型标签优先级、未知字段忽略、旧默认字段和公开错误包装。
- **修改 `src/transbridge/persistence/terminology/drafts.py`**：新增 `DraftStore.active_ref` 只读完整草稿身份；`update_current` 复用事务内已加载草稿并要求处于事务，以完整身份 SQL CAS 和 append-only 审计校验完成更新；旧 `update` 保留兼容委托路径。
- **修改 `src/transbridge/persistence/terminology/draft_transactions.py`**：`save_draft/save_reconciliation` 将已校验的 current 草稿传给 `update_current`，删除事务内重复解码，保留同一事务内的状态检查、审计追加和回滚。
- **修改 `src/transbridge/persistence/terminology/repository.py`**：仅新增 `active_ref` 的 `DraftStore` 窄委托，以及 S05 使用的锁内 `read_cache_token` 连接变更标记；本轮未把缓存、筛选或新的草稿业务加入仓库门面。
- **新增 `tests/application/terminology/test_identity_compatibility.py`**：golden canonical bytes、bytes/映射验证、Enum 与标量子类、自定义 canonical 优先级、非有限浮点及 namespace 隔离回归。
- **新增 `tests/application/terminology/test_canonical_json_chunking.py`**：验证 0/127/128/129/257 项的精确字节、大小集合编码分支、自定义 payload 原调用顺序与序列优先级，以及 Unicode/浮点异常、注入 hash 和 collision guard 的兼容性。
- **新增 `tests/persistence/terminology/test_codec_compatibility.py`**：golden tagged JSON、完整版本往返、Enum/tuple/float 标签、预期类型、旧默认字段与未知字段以及损坏 payload 的兼容验证。
- **新增 `tests/persistence/terminology/test_codec_consumption.py`**：覆盖未知字段中的非法嵌套标签仍被忽略、混合标签优先级、旧非列表 tuple 行为、嵌套容器结果与非法 payload 的错误链，以及解析回调门槛两侧的内容一致性。
- **新增 `tests/persistence/terminology/test_draft_write_snapshots.py`**：验证 `active_ref` 不解码 payload，双连接竞争修改被拒绝，复用快照只能在事务内写入，完整 SQL CAS 仍阻止身份变化。
- **新增 `tests/integration/bootstrap/test_terminology_save_snapshots.py`**：通过真实 `ProductionTerminologyCommands` 和 SQLite 验证单词保存减少读取，同时审计、结果摘要、持久化内容正确；包含跨工程/变体预期引用拒绝。

## S05：版本化读取与有界浏览索引

- **新增 `src/transbridge/application/terminology/browse_index.py`**：`TermBrowseIndex` 为单一精确快照预排序并缓存规范化搜索文本，最多复用 8 个筛选结果；按 cursor 身份及排序键验证分页，逐项检查取消。
- **修改 `src/transbridge/application/terminology/browse_queries.py`**：`TermFilter.matches_normalized` 复用投影文本；`TerminologyBrowseQueries._read_draft/_page` 使用完整草稿引用、连接标记和最多 2 个快照索引，读取前后复核；相同引用下新解码的草稿必须按 source 对象身份重建索引。端口接受可选只读 cancellation，保留旧请求形式。
- **修改 `src/transbridge/application/terminology_profiles/models.py`**：新增不可变 `ProfileSummary` 选择器元数据 DTO，不读取可能很大的可编辑方案正文；本轮未把此前已有模型改动重复归入。
- **修改 `src/transbridge/application/terminology_profiles/__init__.py`**：导出 `ProfileSummary`，为使用轻量列表的公共调用方保持统一包入口。
- **修改 `src/transbridge/application/terminology_profiles/ports.py`**：`TerminologyProfileRepository` 增加 `list_profile_summaries/get_selected_published` 窄读取契约，减少选择器与已选方案的全量草稿读取。
- **修改 `src/transbridge/application/terminology_profiles/in_memory.py`**：实现轻量 summaries、已选 published 读取及 `read_cache_token`，让内存适配器维持与生产端口相同的选择/失效语义。
- **修改 `src/transbridge/application/terminology_profiles/service.py`**：`list_profile_summaries/read_cache_token/selected_revision` 转发新读取能力；`_require_unique_name` 用元数据验证重名，不为名称检查解码全部方案草稿。
- **修改 `src/transbridge/application/terminology_profiles/browse_queries.py`**：`ProfiledTerminologyBrowseSnapshotPort.describe/_compose` 对基础快照和已选方案读取前后核对 token，并按工程/变体、基础身份和方案修订复用最多 2 个完整 context；`_mapping_rows` 逐项可取消，仍使用与 AI 相同的有效方案组合语义。
- **修改 `src/transbridge/application/terminology_profiles/effective.py`**：`snapshot/_profiled_snapshot` 接收可选 cancellation，在方案映射索引和基础术语覆盖阶段逐项检查；版本标识、摘要和旧引用读取语义保持不变。
- **新增 `src/transbridge/persistence/terminology/profile_reads.py`**：`ProfileReadStore` 集中轻量 summaries、选用关系/发布版本读取及容量 2 的解码缓存；通过 `data_version/total_changes/in_transaction` 检测本地和外部变更，重读时沿用原发布内容验证。
- **修改 `src/transbridge/persistence/terminology/profiles.py`**：初始化并窄委托给 `ProfileReadStore` 的 summaries、selected、published 和 token；原方案写入、存储格式及审计职责不变。
- **修改 `src/transbridge/persistence/terminology/effective.py`**：`SqliteEffectiveTerminologySnapshotPort.snapshot/_snapshot_uncached` 按仓库生命周期、工程/变体、版本引用和 storage state 维护容量 2 的缓存；未命中重新验证版本摘要，连接变更或关闭不能返回旧缓存。
- **修改 `src/transbridge/persistence/terminology/connection.py`**：`TerminologyConnectionFactory._connect` 为只读 SQLite 连接同样设置 `check_same_thread=False`，配合仓库既有锁支持后台元数据读取，避免只读恢复模式移至 worker 后触发线程归属错误。
- **新增 `tests/application/terminology/test_browse_index_cache.py`**：验证暖页不重复规范化/筛选、快照和筛选 LRU 有界，投影/筛选/方案组合取消不安装半成品，已取消暖请求不交付旧页。
- **新增 `tests/persistence/terminology/test_browse_read_cache.py`**：真实 SQLite 验证完整 DraftRef 变化使旧 cursor 失效，同/双连接修改与损坏 payload 不被旧缓存掩盖，关闭/回滚、发布/切换/归档和轻量 summary 行为；同引用 payload 重读只保证不返回旧集合，不宣称新增全面草稿摘要篡改验证。
- **新增 `tests/persistence/terminology/test_read_only_background.py`**：专门验证只读方案元数据可以在后台线程读取，覆盖只读连接与 UI 后台化的集成边界。

## S06：方案索引复用与后台预览

- **修改 `src/transbridge/application/terminology_profiles/projection.py`**：新增 `_ProfileIndexes/PreparedProfileProjector` 和 `TerminologyProfileProjector.prepare`，一次建立范围映射与 occurrence/override 索引后重复投影；`SourceTermIndex` 对 ASCII 术语使用最稀有 token 候选桶缩小扫描，保留原匹配、优先级、边界及歧义判定。
- **修改 `src/transbridge/application/terminology_profiles/entry_projection.py`**：`ProfileEntryInput.capture` 冻结条目文本/插件身份，`ProfileEntryProjector` 复用 prepared 投影并提供 `project_input`，使 worker 不依赖可变 UI 条目且不逐词重算完整方案内容。
- **新增 `src/transbridge/ui/workbench/profile_refresh.py`**：`ProfileRefreshCoordinator.request/_start_next/_finished/invalidate/close` 合并方案元数据刷新并串行执行选择命令，捕获工程/变体、隔离晚结果及已关闭窗口；独立读取职责避免继续膨胀工作台控制器。
- **新增 `src/transbridge/ui/workbench/terminology_preview.py`**：`TerminologyPreviewCoordinator` 分批冻结条目，后台批量 prepared 投影并在 GUI 交付；`preview_identity/profile_snapshot_key` 绑定项目、变体、内容修订、基础版本和方案，丢弃失效结果并显示等待/失败状态。
- **修改 `src/transbridge/ui/workbench/terminology_profile_controller.py`**：`refresh/select/_refreshed/_refresh_failed/_loading` 接入专属刷新协调器，轻量列表、已选方案和基础快照在后台读取，选用按捕获上下文串行执行；`open_manager` 保留现有上下文保护。
- **修改 `src/transbridge/ui/workbench/terminology_profile_bar.py`**：`TerminologyProfileBarState` 增加 `loading` 字段，使控制器和 AI 入口能一致呈现后台加载状态。
- **修改 `src/transbridge/ui/tools/ai_translator/naming_scheme_controller.py`**：`AiNamingSchemeBinding.render` 呈现 loading；`_task_profile_key/_state_changed` 只在精确选用方案身份/修订变化时刷新翻译任务，避免 loading 等重复状态触发无效刷新。
- **修改 `src/transbridge/ui/workbench/step2.py`**：`Step2PreviewWidget` 将方案快照、后台投影和生命周期交给 `TerminologyPreviewCoordinator`，原 `_terminology_profile/_terminology_projection` 访问保持兼容；表格填充及刷新只消费当前结果，不在页面中新增投影业务。
- **修改 `src/transbridge/ui/workbench/translation_table.py`**：`set_terminology_projection/_render_row` 增加预览 loading 的只读文本及提示呈现，防止未完成方案预览被误认为可编辑的已确认译文；没有新增方法或投影算法。
- **新增 `tests/application/terminology_profiles/test_prepared_projection.py`**：验证 prepared 与全扫描等价、插件遮蔽/明确绑定优先、复用不重散列/遍历方案、共享 ASCII 前缀的稀有 token 候选，以及冻结输入保持独立。
- **新增 `tests/ui/workbench/terminology_async_support.py`**：`wait_until/wait_profile/wait_preview` 提供有界异步完成等待，相关测试在真实 Qt 事件循环中核对最终状态。
- **新增 `tests/ui/workbench/test_profile_refresh_async.py`**：覆盖 worker 读取、刷新合并、串行选用、旧上下文/关闭晚结果、原错误呈现、捕获上下文提交，以及拒绝关闭和循环 GC 销毁边界。
- **新增 `tests/ui/workbench/test_terminology_preview_async.py`**：覆盖分批冻结让出事件循环、worker 只消费不可变输入、旧方案/项目/集合修订失效、真实 AppContext 修订联动、失败恢复和关闭/GC 生命周期。
- **修改 `tests/ui/workbench/test_terminology_profile_switching.py`**：原方案选择、失败保持、插件范围及只读预览测试等待后台完成；新增 pending 时只读展示以及 loading 不提前触发 AI 任务刷新的断言。
- **修改 `tests/ui/workbench/test_terminology_projection_consistency.py`**：既有预览/写入一致与基础版本变化验证改为等待方案和预览两个异步阶段，保留原 fallback、diagnostics 和 override 断言。
- **修改 `tests/ui/tools/terminology/test_context_isolation.py`**：窗口/管理器上下文隔离测试等待异步方案加载与选择结束，继续验证旧服务和新工程窗口不混用；本轮未改变原场景含义。

## S07：资产刷新合并、协作取消和同步展示复用

- **新增 `src/transbridge/application/terminology/query_control.py`**：`QueryCancellation/QueryCancelled` 提供仅用于只读查询的 Event 取消标记和安全检查点，不使用共享 SQLite interrupt，也不回滚已开始写入。
- **修改 `src/transbridge/ui/tools/terminology/async_actions.py`**：`AsyncActionRunner.run_read/_submit/_complete` 将读取消与普通命令分开，`_ReadLifetime` 在关闭/销毁时取消查询；独立 signal 桥保证 worker 生命周期和旧回调隔离，`run` 已开始写入继续执行。
- **修改 `src/transbridge/ui/tools/terminology/paged_dispatch.py`**：`QueryRunnable/PageQueryDispatch.start_read` 将取消标记交给读取函数，在返回或失败前检查取消；旧无 token loader 仍通过 `start` 兼容。
- **修改 `src/transbridge/ui/tools/terminology/paged_models.py`**：`KeysetPagedTableModel` 增加可选 `read_loader` 并在 `_request` 传递 token；使用独立生命周期 finalizer 关闭 dispatch，继续由代次和 Qt 排队交付保护模型。
- **修改 `src/transbridge/ui/tools/terminology/table_models.py`**：`make_table_model` 为全量术语浏览接入 cancellation-aware loader，原查询模型和公共构造接口保留兼容。
- **修改 `src/transbridge/ui/tools/terminology/asset_snapshot.py`**：`read_assets` 在每个资产读取阶段前后检查取消并传递给 browse，过时刷新不会继续读取剩余大对象。
- **新增 `src/transbridge/ui/tools/terminology/asset_refresh.py`**：`AssetRefreshCoordinator` 合并为一个活动读取和一个待续读；保存各 workload 最新完成 run，取消后合并尚未交付的完成反馈，历史回放只触发一次共享资产读取。
- **新增 `src/transbridge/ui/tools/terminology/asset_presentation.py`**：`WorkbenchAssetPresentation.apply_refresh/apply_shared/apply_completion` 分离共享资产刷新与各 workload 的发布、构建、比较、报告反馈；先构建摘要后更新当前待办，保留 stale/committed 判断。
- **修改 `src/transbridge/ui/tools/terminology/inbound_review.py`**：`InboundReviewDrafts.snapshot` 仅在 GUI 冻结原始编辑，`choices_from_edits` 将排序、校验和构造正式 choice 留到 worker；`choices` 继续兼容旧调用。
- **新增 `src/transbridge/ui/tools/terminology/sync_projection.py`**：`OutcomeProjection/ProposalProjection` 一次构造名称、计数、选择及诊断索引；`page` 只格式化当前页，保留显式请求全部行的兼容 helper。
- **修改 `src/transbridge/ui/tools/terminology/sync_presenter.py`**：`TerminologySyncViewState` 按 result/proposal 与条目对象身份复用投影；`preview_inbound/complete_job` 在 worker 构建投影并在昂贵阶段后复核身份。新增 `_begin_operation` 生命周期代次与锁，阻止重复旧完成读取在新预检清空 job_ref 后覆盖新状态；`complete_job/observe_activity` 也拒绝新操作开始后才到达的无绑定旧 run，代次 0 保留新窗口历史恢复；相同结果身份不重建投影。
- **修改 `src/transbridge/ui/tools/terminology/sync_details.py`**：`SyncDetailsTable.set_projection/_show_projection/_move` 保留完整只读投影，但仅格式化和渲染当前 50 行；相同投影重呈现仅更新 busy 控件，保留选择、滚动和详情，原 set_rows/set_page 仍可用。
- **修改 `src/transbridge/ui/tools/terminology/sync_inbound_view.py`**：`render/_render_proposal/_locate_blocked` 读取已缓存 choices、diagnostics、counts 和首个阻断项；`preview` 捕获原始 edits 后由 worker 转换，翻页不重建完整提案投影或诊断文本。
- **修改 `src/transbridge/ui/tools/terminology/sync_view.py`**：`render_sync/_summary_text` 消费 state 已准备的结果投影与计数，避免每次 Qt 呈现重新扫描所有同步 outcomes。
- **新增 `tests/ui/tools/terminology/test_asset_refresh.py`**：验证 100 条历史完成事件合并共享读取、各 workload 最新反馈保留、取消停止后续阶段、后续刷新合并前一完成、关闭拒绝交付及原错误保留。
- **新增 `tests/ui/tools/terminology/test_read_cancellation.py`**：真实 worker 检查替换/关闭停止过时读、分页 loader 收到 token、Qt owner 删除/循环 GC 后取消且无控件晚回调。
- **修改 `tests/ui/tools/terminology/test_operation_feedback.py`**：新增合并资产后构建、发布、比较及两类报告反馈均被应用的断言，验证只做一次共享刷新且不漏 workload 专属结果。
- **修改 `tests/ui/tools/terminology/test_browsing_failures.py`**：浏览失败测试替身接受新可选 `cancellation` 参数，保留方案切换即使资产刷新失败也拒绝旧页的原断言。
- **新增 `tests/ui/tools/terminology/test_sync_projection.py`**：验证投影只建一次、结果/提案翻页只格式化 50 行、页间诊断与选择保持、原始编辑到 choice 在 worker 转换、索引创建期间 generation 变化拒绝旧提案。
- **新增 `tests/ui/tools/terminology/test_sync_completion_isolation.py`**：确定性覆盖重复 complete_job 与新 preflight 的交错，终态和非终态读取均拒绝旧生命周期结果；补充新预检后才开始的旧 activity/completion 不重新绑定历史，代次 0 仍可恢复历史，并验证相同结果/条目对象复用展示投影。
- **修改 `tests/ui/tools/terminology/test_inbound_review_edits.py`**：面板执行替身通过 try/except 向 `_failed` 交付调用异常，成功时才进入 `_completed`，与正式 worker 的成功/失败路径一致；保留无效入站编辑的原业务断言。
- **修改 `tests/ui/tools/terminology/test_sync_review_details.py`**：运行失败用例经 presenter 的 `execute` 建立当前操作绑定，再交付任务终态；用 service 替身维持原失败执行器，继续验证晚提交回调不能覆盖失败结果，适配新的操作代次契约。

## S08：可复现工具与交付验证入口

- **修改 `scripts/benchmark_project_terminology_ui.py`**：`_measure_qt_feedback` 更新术语构建反馈控件的真实断言，跟随已存在的 UI 接口，修复过时字段导致的基准失败，不放宽既有性能预算。
- **新增 `scripts/benchmark_terminology_browse_ui.py`**：`sample/_check` 复用合成 SQLite 夹具，以新仓库/适配器经 `KeysetPagedTableModel` 测冷首屏交付和 QTimer gap；验证准确行、方案译名、总数及快照身份。夹具准备和断言在计时外，完成包含模型行更新但不包含后续绘制；正式运行结果见下文。
- **新增 `scripts/benchmark_terminology_interactions.py`**：`fixtures/measure/worker/main` 复用原审计合成数据，测导入、来源、差异逐键搜索和管理器加载/单格提交；记录原始样本、同步回调、事件交付完成及 QTimer gap，区分索引准备与交互时段。
- **新增 `scripts/benchmark_terminology_projection.py`**：`make_fixture/measure/load_baseline/_check` 比较相同合成数据上的冷准备、暖批量投影与完整输出摘要，可显式载入可信任务起始源码中的两个投影模块作局部前后对照；不冒充完整历史应用基准。
- **新增 `scripts/benchmark_terminology_save_ui.py`**：`measure/main` 复用公开合成 SQLite 夹具与真实 Production 命令，经 `DraftWriteQueue` 测提交回调、完成与 QTimer gap；计时不包含后续工作台资产刷新。
- **新增 `scripts/benchmark_terminology_storage.py`**：`create_fixture/open_browse/commands_for` 公开安全合成夹具入口；`measure_legacy/measure_browse/measure_edits/measure_read_counts` 保留原查询序列并区分冷/暖读取、真实串行保存和调用计数。workspace/output 必填且拒绝已有路径，至少 5 次，默认查询含 50k 而保存为 1k/10k，可选 RSS 保持 legacy 序列峰值含义。
- **新增 `scripts/benchmark_terminology_sync.py`**：`fixture/render_suite/task_history_counts/cancel_probe/main` 提供离线同步结果/提案显示、翻页、任务历史合并和取消的可复现证据；隐藏 offscreen 回调测量明确不等于后续窗口合成/绘制耗时。
- **新增 `tests/scripts/test_benchmark_terminology_storage.py`**：验证 CLI 参数和已有路径拒绝、非生成夹具/缺数据库拒绝、201 条真实 SQLite 的冷暖/legacy 分页及隔离保存、单词计数和失败时保留不完整报告；不在单元测试中运行默认完整性能组。

## 责任边界与明确例外

- `src/transbridge/persistence/terminology/repository.py`：任务起始 **583 行 / 45 方法**，本轮 **597 行 / 47 方法**。新增 `read_cache_token` 仅暴露锁内连接时钟，`active_ref` 仅委托 `DraftStore`；缓存和查询在 `ProfileReadStore`、effective adapter、browse index 等完整职责切片中。为保留公共仓库门面，本轮不机械拆分现有接口；风险是既有门面仍超阈值，后续新增任何存储业务前必须先提取对应 Store，不以本例外允许继续增长。
- `src/transbridge/ui/workbench/step2.py`：任务起始 **701 行 / 49 方法**，本轮 **700 行 / 49 方法**。新后台预览和上下文校验职责已完整交给 `TerminologyPreviewCoordinator`，页面只接线/消费结果；既有大页面保留兼容入口，后续新增页面职责先抽出独立切片。
- `src/transbridge/ui/workbench/translation_table.py`：任务起始 **519 行 / 32 方法**，本轮 **528 行 / 32 方法**。仅在现有呈现方法增加 loading 文案/编辑状态，没有新增方法或投影业务；后续扩充表格职责前先提取相应呈现切片。


## 性能复测与解释

环境：Windows 11、Python 3.12.12、Qt 6.10.0 / PyQt 6.10.2、24 物理核 / 32 逻辑核、约 31.6 GiB 内存。全部为合成数据，使用既有 uv 环境；性能组串行执行，每组至少五次。OS 负载和磁盘缓存未完全控制，观察到部分进程初期显著快于后续阶段，因此保留全部样本，以中位数及范围表述，不选择最快样本。以下时间除特别注明外均为毫秒。

- **真实 SQLite 单条修改（含提交、不含后续资产刷新）**：10k 总量由 6026（5741～6270）降至 **2305（2270～2434）**，约降低 62%；完整 Draft 解码由 5 次降为 2 次、Version 由 2 次降为 0 次。1k 由 156（154～164）变为 **218（195～228）**，未显示吞吐收益，不把大数据改善推广到全部规模。
- **真实后台保存 UI**：10k 提交回调中位 0.70、最大 3.52；提交至 UI 完成中位 **2400（2050～2532）**，QTimer 最大间隔五轮最高 **183.09**，本组满足 200ms 响应预算。1k UI 完成约 50ms 属于另一个进程/测试时段，不替代上面的存储基准或据此掩盖小规模差异。
- **浏览与缓存**：沿用旧查询序列的 50k 下一页由 **10922（10645～11401）降为 4.06（3.49～4.66）**；同序列搜索约 24ms。全新适配器冷首屏另计：50k **8282（8226～8420）**；冷后热下一页约 3.49。旧序列在前面已执行 describe/base 读取，不能把其约 375ms 的 first-page 指标冒充完整冷启动。
- **真实 SQLite → Qt 模型冷首屏**：10k 完成 **1536（405～1556）**，最大事件间隔 123.49；50k 完成 **8422（8307～8509）**，最长事件间隔中位 **219.08（198.97～222.45）**。本轮冷加载附加修复前同脚本 50k 间隔为 **586.21（581.39～620.94）**，修复改善响应性，但 **50k 五轮中四轮仍超 200ms，10k/50k 冷首屏未达到 500ms**。完成点是模型接受页数据，非后续绘制完成；QTimer 间隔含定时周期、调度、轮询和 GIL 竞争，不等于纯回调 CPU 时长。
- **映射编辑器**：2k 打开同步回调由 3230（826～3341）降至 **35.79（35.38～41.57）**；包含 processEvents 的加载中位 41.18。单格编辑中位 0.049，不再逐行查找或维护常驻下拉框。
- **完整集合搜索**：55k 导入预览最慢单键中位由 1431 降至 **0.202**；最终查询完成中位 **304.81（234.40～316.33）**，包含 200ms 防抖，最大事件间隔 52.74。50k 来源/差异视图最终查询完成约 237/234，输入回调约 0.124/0.137；搜索索引准备在该输入计时之外，不将它描述为整个对话框启动耗时。
- **方案投影**：相同 10k 映射与 20 条固定文本的五轮局部源码对照，完整 entry 暖批次由 **22658（22475～22891）降为 2.45（2.31～3.22）**；含准备的首条由 1181 降为 351。旧版只载入起始快照中两个 S06 模块，共享当前模型等依赖，属于模块对照，不是完整历史应用或真实翻译端到端比较。另一个 1000 条文本的当前版暖批次中位 262（83～336），不可与 20 条批次直接作倍数比较。
- **同步展示**：50k 入站“下一页再上一页”由 **1030（729～1108）降至 74.52（73.83～78.44）**；相同结果重复渲染由 228 降至 **0.074**；首次渲染回调 2.68，另有 worker 结果/提案投影约 29.70/19.33。全部编辑的 GUI 冻结约 0.60（0.55～1.02），后续 choice 构造仍需 worker **1620（1604～1956）**，并未把后台 CPU 工作计为消失。
- **后台收敛**：1/20/100 条历史完成通知五轮均合并为 1 次入队、1 次实际资产读取、1 次交付；原 100 条为 100 次入队、实际读取 1～5 次。已启动的读取消后不继续后续阶段，不交付旧结果；资产替代结果正常交付。取消仍在检查点生效，单个 SQLite 调用或 JSON 步骤不会强制中断，写入保持原提交语义。
- **内存代价**：50k 旧查询序列采样的 RSS 峰值增量中位约 **195 MiB（178～217）**，初测约 176 MiB。有界缓存换取热查询速度，不能宣称整体内存下降；该指标含分配器保留与多阶段对象，不等同于单查询用量或泄漏证明。
- **兼容性与小对象代价**：冷加载修复使用 no-op parser hook、私有树消费和规范化后分块 C 编码，不改全局 GC/线程参数。小 canonical 对象五轮每轮 10k 次由中位 46.50ms 变为 52.44ms，约增加 0.59µs/次；保留小对象单次 C 编码，避免逐字段编码放大。已用 golden bytes、128 边界、Unicode/浮点、unknown fields、tag 优先级、旧错误 cause、自定义 hook 顺序覆盖格式契约。

旧 UI 基准的真实控件断言已经修正并完成五轮：反馈最慢 0.203ms、事件间隔 11.22ms。该脚本使用模拟准备工作，仅作为补充，不能代替真实 SQLite/大数据指标。

## 实际验证

- 第一批关联 UI 集合 144 项通过；第二批保存相关 103 项、读取相关 80 项、工作台相关 110 项通过；第三批术语 UI 全目录 134 项通过。这些集合有重叠，不累加为总数。
- 冷加载附加修复聚焦 70 项通过；独立只读复核未发现格式兼容阻断。S05 审查覆盖完整引用、事务回滚/跨连接 token 与生命周期；S07 审查发现并修复同一 run 重复完成与新预检的代次竞态，补充确定性并发回归。
- 最终联合 pytest：**1563 passed、1 skipped、1 deselected，51.31s**。唯一跳过为未显式配置专用 token/project 的 ParaTranz live 合同；唯一 deselected 为 slow 的 50k 报告用例，随后以原断言单独通过。因此本轮最终相关集合去重 **1564 通过、1 跳过**，不是全仓库 pytest。
- `ruff check src tests`、`ruff format --check src tests` 均通过（1723 文件）；七个术语性能脚本的 Ruff check / format 同样通过；`git diff --check` 退出 0。Git 的 LF→CRLF 提示不是检查失败。
- Windows sandbox 已确认阻止报告原子发布的 `os.link`，也限制本机 FAISS/HTTP 测试；最终联合回归和 slow 报告在获准放开该限制后运行。未改原子发布代码、削弱断言或用异常吞掉环境失败。
- 未执行真实 LLM、真实 ParaTranz 写入、发行安装包验证或全仓库 pytest；这些不在本轮术语性能优化的验证范围。Qt 为 offscreen 自动化，不声称完成人工桌面视觉验收。

联合回归的实际完整命令：

```powershell
uv --cache-dir .tmp-terminology-perf-20261015/uv run --no-sync python -m `
  pytest `
  tests/application/terminology `
  tests/application/terminology_profiles `
  tests/application/terminology_sync `
  tests/application/translation `
  tests/persistence/terminology `
  tests/contracts/terminology `
  tests/contracts/terminology_sync `
  tests/contracts/translation `
  tests/contracts/io/test_terminology_profile_write.py `
  tests/contracts/paratranz/test_terms_api_contract.py `
  tests/integration/terminology `
  tests/integration/terminology_sync `
  tests/integration/bootstrap `
  tests/integration/translation `
  tests/ui/tools/terminology `
  tests/ui/tools/test_terminology_profile_manager.py `
  tests/ui/tools/test_terminology_profile_edit_protection.py `
  tests/ui/tools/test_terminology_profile_mapping_model.py `
  tests/ui/tools/test_terminology_source_import_dialog.py `
  tests/ui/tools/test_terminology_source_import_controller.py `
  tests/ui/tools/test_terminology_import_full_preview.py `
  tests/ui/tools/test_ai_project_terminology.py `
  tests/ui/workbench `
  tests/ui/test_step2_incremental_rendering.py `
  tests/ui/operations/test_strict_write_routes.py `
  tests/integration/publish/test_plugin_transfer.py `
  tests/smart_assistant/tools/test_terminology_sync_tools.py `
  tests/ai_translator `
  tests/scripts/test_benchmark_terminology_storage.py `
  -q `
  -m `
  'not slow and not llm' `
  -o `
  'addopts=--tb=short --strict-markers' `
  --basetemp=.tmp-terminology-perf-20261015/f
```

补充 slow 报告与静态检查：

```powershell
uv --cache-dir .tmp-terminology-perf-20261015/uv run --no-sync python -m pytest tests/application/terminology/test_reports.py::test_quality_renderer_streams_fifty_thousand_terms_from_sqlite_pages -q -o 'addopts=--tb=short --strict-markers' --basetemp=.tmp-terminology-perf-20261015/slow-final
uv --cache-dir .tmp-terminology-perf-20261015/uv run --no-sync ruff check src tests
uv --cache-dir .tmp-terminology-perf-20261015/uv run --no-sync ruff format --check src tests
git diff --check
```

性能命令使用同一前缀 `uv --cache-dir .tmp-terminology-perf-20261015/uv run --no-sync python`，其后的实际参数如下；workspace/output 必须为新路径，所有默认 repetitions/runs 均为五次：

```text
scripts/benchmark_terminology_storage.py --workspace .tmp-terminology-perf-20261015/storage-cold-fix --output .tmp-terminology-perf-20261015/storage-final.json --repeats 5 --rss
scripts/benchmark_terminology_save_ui.py --workspace .tmp-terminology-perf-20261015/save-ui-cold-fix --output .tmp-terminology-perf-20261015/save-ui-final.json
scripts/benchmark_terminology_browse_ui.py --workspace .tmp-terminology-perf-20261015/browse-ui-cold-fix --output .tmp-terminology-perf-20261015/browse-ui-final.json
scripts/benchmark_terminology_interactions.py --output .tmp-terminology-perf-20261015/interactions-after.jsonl
scripts/benchmark_terminology_projection.py --output .tmp-terminology-perf-20261015/projection-final.json
scripts/benchmark_terminology_projection.py --sizes 1000 10000 --entries 20 --repeats 5 --baseline-archive .tmp-terminology-perf-20261015/before-worktree.zip --baseline-directory .tmp-terminology-perf-20261015/projection-before --output .tmp-terminology-perf-20261015/projection-paired.json
scripts/benchmark_terminology_sync.py --suite render --sizes 1000 10000 50000 --runs 5 --output .tmp-terminology-perf-20261015/sync-render-final.json
scripts/benchmark_terminology_sync.py --suite counts --runs 5 --output .tmp-terminology-perf-20261015/sync-counts-final.json
scripts/benchmark_project_terminology_ui.py --output .tmp-terminology-perf-20261015/legacy-ui-final.json
```

## 遗留边界与证据

剩余性能预算：10k/50k 完整冷首屏超过 500ms，50k 冷加载部分事件间隔超过 200ms；小数据保存未显示吞吐收益，缓存有额外内存成本。单个巨型标量/嵌套记录不受 128 项分块的硬时限保证。如需所有规模的冷首屏均达标，后续须独立设计可重建的按行查询投影，验证事务一致性、迁移和版本身份，不能通过跳过摘要校验或增加第二权威数据源实现。

原始五轮样本、基线、冷修复前后报告、诊断对照、实际命令、验证日志、源码差异与起始哈希归档在仓库外 `C:/Users/admin/.codex/visualizations/2026/10/14/01a13aae-215e-73e2-b0ab-09baecf99ee9/terminology-performance-optimized-20261015.zip`。归档不含用户翻译数据、数据库夹具、凭据、虚拟环境或缓存。本轮独占临时目录在归档校验后清理；既有用户/其他任务临时目录不在清理范围。
