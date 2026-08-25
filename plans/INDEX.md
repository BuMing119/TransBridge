# 方案索引 (Plans Index)

## 综合整改 V2（37/37 Story 实现完成，综合 QA 通过）

> 下列 7 个 Plan 按 R-001～R-050 的去重根因组织，共 37 个 Story；已完成实现、逐 Story 证据、综合 QA 与最终索引门禁。

| 顺序 | Epic | 状态 | Stories | 主要依赖 |
|---|---|---|---|---|
| 1 | [platform-contract-foundation-v2](platform-contract-foundation-v2/plan.md) | 实现完成，综合 QA 通过 | 5 | 无 |
| 2 | [translation-io-kernel-v2](translation-io-kernel-v2/plan.md) | 实现完成，综合 QA 通过 | 6 | platform S01～S03 |
| 3 | [project-session-persistence-v2](project-session-persistence-v2/plan.md) | 实现完成，综合 QA 通过 | 5 | platform S02～S03；I/O S01～S02 |
| 4 | [unified-task-translation-runtime-v2](unified-task-translation-runtime-v2/plan.md) | 实现完成，综合 QA 通过 | 7 | platform；I/O identity/Stage；Session owner |
| 5 | [paratranz-sync-service-v2](paratranz-sync-service-v2/plan.md) | 实现完成，综合 QA 通过 | 4 | platform；ParaTranz JSON；TaskRuntime |
| 6 | [fomod-pipeline-v2](fomod-pipeline-v2/plan.md) | 实现完成，综合 QA 通过 | 5 | platform；I/O publish；Task/translation runtime |
| 横切 | [release-hardening-v2](release-hardening-v2/plan.md) | 实现完成，综合 QA 通过 | 5 | S01 先行；S05 最后 |

## 历史完成状态纠偏（2026-08-18）

> 下列状态是对历史交付记录的增量验证结论，不删除或伪造原“已实现”上下文。具体 `blocked_by` / `superseded_by` 见各 Plan 末尾状态增量。

| 历史 Plan | 本轮状态 | 主要承接 V2 Plan |
|---|---|---|
| [core-data-model](core-data-model/plan.md) | `partially-verified` | translation-io-kernel-v2 |
| [file-parsing](file-parsing/plan.md) | `partially-verified` | translation-io-kernel-v2 |
| [file-writing](file-writing/plan.md) | `partially-verified` | translation-io-kernel-v2 |
| [stage-unification](stage-unification/plan.md) | `partially-verified` | translation-io-kernel-v2 |
| [project-persistence](project-persistence/plan.md) | `partially-verified` | project-session-persistence-v2 |
| [ui-workbench](ui-workbench/plan.md) | `partially-verified` | project-session-persistence-v2 |
| [session-controller](session-controller/plan.md) | `partially-verified` | project-session-persistence-v2 / unified-task-translation-runtime-v2 |
| [session-manager](session-manager/plan.md) | `partially-verified` | project-session-persistence-v2 / unified-task-translation-runtime-v2 |
| [task-monitor](task-monitor/plan.md) | `partially-verified` | unified-task-translation-runtime-v2 |
| [ai-translation](ai-translation/plan.md) | `partially-verified` | unified-task-translation-runtime-v2 |
| [ai-post-process](ai-post-process/plan.md) | `partially-verified` | unified-task-translation-runtime-v2 |
| [fr5.12-embedding-optimization](fr5.12-embedding-optimization/plan.md) | `partially-verified` | platform-contract-foundation-v2 / unified-task-translation-runtime-v2 |
| [agent-upgrade](agent-upgrade/plan.md) | `partially-verified` | platform-contract-foundation-v2 / unified-task-translation-runtime-v2 |
| [agent-tool-expansion](agent-tool-expansion/plan.md) | `partially-verified` | platform/I-O/task/ParaTranz V2 |
| [smart-assistant-refactor](smart-assistant-refactor/plan.md) | `partially-verified` | platform-contract-foundation-v2 / persistence V2 |
| [tool-prompt-layering](tool-prompt-layering/plan.md) | `partially-verified` | platform-contract-foundation-v2 |
| [paratranz-integration](paratranz-integration/plan.md) | `partially-verified` | paratranz-sync-service-v2 |
| [translation-memory](translation-memory/plan.md) | `partially-verified` | translation-io-kernel-v2 / fomod-pipeline-v2 |
| [fomod-translation](fomod-translation/plan.md) | `partially-verified` | fomod-pipeline-v2 |
| [agent-infra-tools](agent-infra-tools/plan.md) | `partially-verified` | fomod-pipeline-v2 |

## 已实现 (✔️)

> 以下状态保留历史上下文，不代表已通过 2026-08-18 综合整改验收。

| Epic | 状态 | Stories | 文档 |
|------|------|---------|------|
| smart-assistant-workspace-redesign | 已完成（2026-08-25，综合 QA 通过） | 3/3 | [plan](smart-assistant-workspace-redesign/plan.md) |
| project-catalog-self-healing | 已完成（2026-08-25，综合 QA 通过） | 3/3 | [plan](project-catalog-self-healing/plan.md) |
| start-center-launch-hub | S01～S06 已完成（2026-08-25，自动化 QA 通过） | 6/6 | [plan](start-center-launch-hub/plan.md) |
| ui-layout-stability | 已完成（2026-08-24，UI 全量回归通过） | 5/5 | [plan](ui-layout-stability/plan.md) |
| paratranz-project-binding | 已完成（2026-08-24，综合 QA 通过） | 5/5 | [plan](paratranz-project-binding/plan.md) |
| core-data-model | ✔️ | 5/5 | [plan](core-data-model/plan.md) · [s01](core-data-model/stories/story-01-translation-entry.md) [s02](core-data-model/stories/story-02-collection.md) [s03](core-data-model/stories/story-03-context-categories.md) [s04](core-data-model/stories/story-04-dsd-json.md) [s05](core-data-model/stories/story-05-categorized-export.md) |
| file-parsing | ✔️ | 11/11 | [plan](file-parsing/plan.md) · [s01](file-parsing/stories/story-01-plugin-parser.md) [s02](file-parsing/stories/story-02-strings-lookup.md) [s03](file-parsing/stories/story-03-context-extraction.md) [s04](file-parsing/stories/story-04-eet-parser.md) [s05](file-parsing/stories/story-05-xt-parser.md) [s06](file-parsing/stories/story-06-dsd-import.md) [s07](file-parsing/stories/story-07-strings-import.md) [s08](file-parsing/stories/story-08-formid-trans.md) · [s09](file-parsing/stories/story-09-sst-parser.md) · [s10](file-parsing/stories/story-10-sst-migration-source.md) · [s11](file-parsing/stories/story-11-sst-serializer.md) |
| file-writing | ✔️ | 7/7 | [plan](file-writing/plan.md) · [s01](file-writing/stories/story-01-inline-write.md) [s02](file-writing/stories/story-02-localised-write.md) [s03](file-writing/stories/story-03-eet-update.md) [s04](file-writing/stories/story-04-eet-build.md) [s05](file-writing/stories/story-05-xt-update.md) [s06](file-writing/stories/story-06-xt-build.md) [s07](file-writing/stories/story-07-strings-only.md) |
| paratranz-integration | ✔️ | 8/8 | [plan](paratranz-integration/plan.md) · [s01](paratranz-integration/stories/story-01-api-client.md) [s02](paratranz-integration/stories/story-02-config.md) [s03](paratranz-integration/stories/story-03-upload.md) [s04](paratranz-integration/stories/story-04-download.md) [s05](paratranz-integration/stories/story-05-strings-api.md) [s06](paratranz-integration/stories/story-06-terms-api.md) [s07](paratranz-integration/stories/story-07-export-artifact.md) [s08](paratranz-integration/stories/story-08-project-ui.md) |
| ai-translation | Story 1-16 已实现 | 16 | [plan](ai-translation/plan.md) · [s01](ai-translation/stories/story-01-llm-client.md) [s02](ai-translation/stories/story-02-prompt-builder.md) [s03](ai-translation/stories/story-03-term-database.md) [s04](ai-translation/stories/story-04-batch-planner.md) [s05](ai-translation/stories/story-05-translator.md) [s06](ai-translation/stories/story-06-noun-extractor.md) [s07](ai-translation/stories/story-07-vector-index.md) [s08](ai-translation/stories/story-08-embedding-client.md) · [s09](ai-translation/plan.md#story-09) · [s15](ai-translation/stories/story-15-prompt-cache-structure.md) · [s16](ai-translation/stories/story-16-entry-scoped-terminology.md) |
| ai-post-process | Story 1-14 已实现 | 14 | [plan](ai-post-process/plan.md) · [s01](ai-post-process/stories/story-01-consistency-checker.md) [s02](ai-post-process/stories/story-02-format-validation.md) [s03](ai-post-process/stories/story-03-quality-gate.md) [s04](ai-post-process/stories/story-04-llm-refiner.md) [s05](ai-post-process/stories/story-05-llm-polisher.md) [s06](ai-post-process/stories/story-06-llm-arbiter.md) [s07](ai-post-process/stories/story-07-post-processor.md) [s08](ai-post-process/stories/story-08-report.md) [s09](ai-post-process/stories/story-09-standalone-polish.md) [s10](ai-post-process/stories/story-10-report-backend.md) [s11](ai-post-process/stories/story-11-report-dialog.md) [s12](ai-post-process/stories/story-12-integration.md) [s13](ai-post-process/stories/story-13-history-viewer.md) [s14](ai-post-process/stories/story-14-prompt-contract-stage-cache.md) |
| ui-workbench | ✔️ 已实现 | 22 | [plan](ui-workbench/plan.md) · [s01](ui-workbench/stories/story-01-app-context.md) ... [s18](ui-workbench/plan.md#story-18) · [s20](ui-workbench/plan.md#story-20) · [s21](ui-workbench/plan.md#story-21) · [s22](ui-workbench/plan.md#story-22) |
| ui-presentation-modularization | 已完成（2026-08-19） | 8 | [plan](ui-presentation-modularization/plan.md) |
| ui-foundation-framework | 已完成（2026-08-24） | 9 | [plan](ui-foundation-framework/plan.md) |
| guided-ui-workflows | 已完成（2026-08-24） | 13/13 | [plan](guided-ui-workflows/plan.md) · [S10 QA](../docs/test-reports/guided-ui-workflows-s10-qa-2026-08-24.md) |
| modern-workbench-visual-shell | 已完成（2026-08-24，S06 渐进式菜单通过 QA） | 6/6 | [plan](modern-workbench-visual-shell/plan.md) |
| batch-operations | ✔️ | 7/7 | [plan](batch-operations/plan.md) · [s01](batch-operations/stories/story-01-batch-esp.md) [s02](batch-operations/stories/story-02-batch-upload.md) [s03](batch-operations/stories/story-03-batch-download.md) [s04](batch-operations/stories/story-04-batch-write.md) [s05](batch-operations/stories/story-05-batch-translation.md) [s06](batch-operations/stories/story-06-multi-slot.md) [s07](batch-operations/stories/story-07-strings-batch.md) |
| vector-term-retrieval | ✔️ | — | [plan](vector-term-retrieval/plan.md) |
| stage-unification | ✔️ | 3/3 | [plan](stage-unification/plan.md) · [s01](stage-unification/plan.md#story-01) · [s02](stage-unification/plan.md#story-02) · [s03](stage-unification/plan.md#story-03) |
| label-system | ✔️ | 4/4 | [plan](label-system/plan.md) · [s01](label-system/plan.md#story-01) · [s02](label-system/plan.md#story-02) · [s03](label-system/plan.md#story-03) · [s04](label-system/plan.md#story-04) |
| llm-chat | ✅ S01-10 全部完成 | 10 | [plan](llm-chat/plan.md) · [s01](llm-chat/stories/story-01-panel-framework.md) [s02](llm-chat/stories/story-02-core-backend.md) [s03](llm-chat/stories/story-03-loop-control-cards.md) [s04](llm-chat/stories/story-04-tool-system.md) [s05](llm-chat/stories/story-05-experience-optimization.md) · [s06](llm-chat/stories/story-06-layering-backend.md) [s07](llm-chat/stories/story-07-layering-ui.md) · [s08](llm-chat/stories/story-08-experience-overhaul.md) · [s09](llm-chat/stories/story-09-chatwidget-refactor.md) · [s10](llm-chat/stories/story-10-toolresult-observation.md) |
| agent-upgrade | ✔️ 已实现 | 12/12 | [plan](agent-upgrade/plan.md) · [s01](agent-upgrade/stories/story-01-infra-extraction.md) [s02](agent-upgrade/stories/story-02-skill-system.md) [s03](agent-upgrade/stories/story-03-file-upload.md) [s04](agent-upgrade/stories/story-04-long-term-memory.md) [s05](agent-upgrade/stories/story-05-reflexion-retry.md) [s06](agent-upgrade/stories/story-06-agent-infrastructure.md) [s07](agent-upgrade/stories/story-07-agent-orchestration.md) [s08](agent-upgrade/stories/story-08-safety-guardrails.md) [s09](agent-upgrade/stories/story-09-graph-engine-core.md) [s10](agent-upgrade/stories/story-10-checkpoint-hitl.md) [s11](agent-upgrade/stories/story-11-observability.md) [s12](agent-upgrade/stories/story-12-mcp-server.md) |
| agent-tool-expansion | ✔️ S01-20+S23-26已实现 + ✔️ S21已实现 + ✔️ S22已实现 | 26 | [plan](agent-tool-expansion/plan.md) · [确认书](agent-tool-expansion/modification-confirmation.md) · [s01](agent-tool-expansion/stories/story-01-infra-tools-package.md) [s02](agent-tool-expansion/stories/story-02-task-manager.md) [s03](agent-tool-expansion/stories/story-03-appcontext-viewmodel.md) [s04](agent-tool-expansion/stories/story-04-p0-filter-search-tools.md) [s05⛔](agent-tool-expansion/stories/story-05-p0-edit-select-tools.md) [s06](agent-tool-expansion/stories/story-06-p0-translation-control.md) [s07](agent-tool-expansion/stories/story-07-p0-state-query-proofread.md) [s08](agent-tool-expansion/stories/story-08-p1-label-tools.md) [s09](agent-tool-expansion/stories/story-09-p1-translation-config.md) [s10](agent-tool-expansion/stories/story-10-p1-postprocess-tools.md) [s11](agent-tool-expansion/stories/story-11-p1-paratranz-tools.md) [s12](agent-tool-expansion/stories/story-12-p2-parser-writer-project.md) [s13](agent-tool-expansion/stories/story-13-agent-integration.md) · [s14](agent-tool-expansion/stories/story-14-integration-tests.md) · [s15](agent-tool-expansion/stories/story-15-tool-completion.md) · [s16](agent-tool-expansion/stories/story-16-dead-code-registration.md) [s17](agent-tool-expansion/stories/story-17-set-filters-merge.md) [s18](agent-tool-expansion/stories/story-18-stop-task-merge.md) [s19](agent-tool-expansion/stories/story-19-write-back-merge.md) [s20](agent-tool-expansion/stories/story-20-manage-entry-labels-merge.md) [s21](agent-tool-expansion/stories/story-21-descriptions-tests.md) · [s22](agent-tool-expansion/stories/story-22-tool-description-rewrite.md) · [s23](agent-tool-expansion/stories/story-23-key-primary-index.md) · [s24](agent-tool-expansion/stories/story-24-parser-side-effects.md) · [s25](agent-tool-expansion/stories/story-25-postprocess-unification.md) · [评审纪要](../docs/council-review-fr9-tool-allocation.md) |
| project-persistence | ✔️ 已实现 | 8 | [plan](project-persistence/plan.md) |
| smart-assistant-qa-fix | ✅ 第五轮全量修复完成 (166/166) | 7 | [plan](smart-assistant-qa-fix/plan.md) |
| smart-assistant-refactor | ✔️ 已实现 | 4 | [plan](smart-assistant-refactor/plan.md) |
| tool-prompt-layering | ✅ 全部完成 | 5 | [plan](tool-prompt-layering/plan.md) · [s01](tool-prompt-layering/stories/story-01-token-measurement.md) · [s02](tool-prompt-layering/stories/story-02-summary-and-builders.md) · [s03](tool-prompt-layering/stories/story-03-get-tool-help-and-prompt.md) · [s04](tool-prompt-layering/stories/story-04-regression-tests.md) · [s05](tool-prompt-layering/stories/story-05-tuning.md) |
| session-controller | ✅ 全部完成 | 2 | [plan](session-controller/plan.md) |
| session-manager | ✅ 全部完成 | 3 | [plan](session-manager/plan.md) |
| task-monitor | ✔️ 已实现 | 2 | [plan](task-monitor/plan.md) |
| fr5.12-embedding-optimization | ✔️ 已实现 | 3 | [plan](fr5.12-embedding-optimization/plan.md) · [s01](fr5.12-embedding-optimization/stories/story-01-batch-recall-threshold.md) [s02](fr5.12-embedding-optimization/stories/story-02-incremental-index-cache.md) [s03](fr5.12-embedding-optimization/stories/story-03-bm25-hybrid.md) |
| translation-memory | ✅ 全部完成 (S01-10) | 10 | [plan](translation-memory/plan.md) · [s01](translation-memory/stories/story-01-data-model.md) [s02](translation-memory/stories/story-02-query-fallback.md) [s03](translation-memory/stories/story-03-save-from-collection.md) [s04](translation-memory/stories/story-04-query-apply.md) [s05](translation-memory/stories/story-05-gui.md) · [s06](translation-memory/stories/story-06-model-refactor.md) [s07](translation-memory/stories/story-07-locate-load-refactor.md) [s08](translation-memory/stories/story-08-multi-dict-query.md) [s09](translation-memory/stories/story-09-scope-share.md) [s10](translation-memory/stories/story-10-gui-arbitration.md) |
| agent-infra-tools | ✔️ 已实现（S01-05，12 测试通过） | 5 | [plan](agent-infra-tools/plan.md) · [s01](agent-infra-tools/stories/story-01-archive.md) [s02](agent-infra-tools/stories/story-02-differ.md) [s03](agent-infra-tools/stories/story-03-filter-rules.md) [s04](agent-infra-tools/stories/story-04-key-migrator.md) [s05](agent-infra-tools/stories/story-05-dictionary-tools.md) |
| fomod-translation | ✔️ 已实现（S01-04，4 测试通过） | 4 | [plan](fomod-translation/plan.md) · [s01](fomod-translation/stories/story-01-fomod-xml.md) [s02](fomod-translation/stories/story-02-builder.md) [s03](fomod-translation/stories/story-03-pipeline.md) [s04](fomod-translation/stories/story-04-gui.md) |

## 统计

- 历史已实现记录：195 Story（含 FR16 agent-infra-tools 5 Story + FR15 fomod-translation 4 Story）。
- 综合整改 V2：37 Story，37 个 Story 已全部完成增量验证、0 待实现；不计入历史已实现数量，最终状态由综合 QA 决定。
- 历史状态纠偏：已按各 V2 Plan 的追溯表增量写回，不重写历史正文。
