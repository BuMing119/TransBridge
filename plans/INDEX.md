# 方案索引 (Plans Index)

## 已实现 (✔️)

| Epic | 状态 | Stories | 文档 |
|------|------|---------|------|
| core-data-model | ✔️ | 5/5 | [plan](core-data-model/plan.md) · [s01](core-data-model/stories/story-01-translation-entry.md) [s02](core-data-model/stories/story-02-collection.md) [s03](core-data-model/stories/story-03-context-categories.md) [s04](core-data-model/stories/story-04-dsd-json.md) [s05](core-data-model/stories/story-05-categorized-export.md) |
| file-parsing | ✔️ | 11/11 | [plan](file-parsing/plan.md) · [s01](file-parsing/stories/story-01-plugin-parser.md) [s02](file-parsing/stories/story-02-strings-lookup.md) [s03](file-parsing/stories/story-03-context-extraction.md) [s04](file-parsing/stories/story-04-eet-parser.md) [s05](file-parsing/stories/story-05-xt-parser.md) [s06](file-parsing/stories/story-06-dsd-import.md) [s07](file-parsing/stories/story-07-strings-import.md) [s08](file-parsing/stories/story-08-formid-trans.md) · [s09](file-parsing/stories/story-09-sst-parser.md) · [s10](file-parsing/stories/story-10-sst-migration-source.md) · [s11](file-parsing/stories/story-11-sst-serializer.md) |
| file-writing | ✔️ | 7/7 | [plan](file-writing/plan.md) · [s01](file-writing/stories/story-01-inline-write.md) [s02](file-writing/stories/story-02-localised-write.md) [s03](file-writing/stories/story-03-eet-update.md) [s04](file-writing/stories/story-04-eet-build.md) [s05](file-writing/stories/story-05-xt-update.md) [s06](file-writing/stories/story-06-xt-build.md) [s07](file-writing/stories/story-07-strings-only.md) |
| paratranz-integration | ✔️ | 8/8 | [plan](paratranz-integration/plan.md) · [s01](paratranz-integration/stories/story-01-api-client.md) [s02](paratranz-integration/stories/story-02-config.md) [s03](paratranz-integration/stories/story-03-upload.md) [s04](paratranz-integration/stories/story-04-download.md) [s05](paratranz-integration/stories/story-05-strings-api.md) [s06](paratranz-integration/stories/story-06-terms-api.md) [s07](paratranz-integration/stories/story-07-export-artifact.md) [s08](paratranz-integration/stories/story-08-project-ui.md) |
| ai-translation | 🚧 扩展中 | 14 | [plan](ai-translation/plan.md) · [s01](ai-translation/stories/story-01-llm-client.md) [s02](ai-translation/stories/story-02-prompt-builder.md) [s03](ai-translation/stories/story-03-term-database.md) [s04](ai-translation/stories/story-04-batch-planner.md) [s05](ai-translation/stories/story-05-translator.md) [s06](ai-translation/stories/story-06-noun-extractor.md) [s07](ai-translation/stories/story-07-vector-index.md) [s08](ai-translation/stories/story-08-embedding-client.md) · [s09](ai-translation/plan.md#story-09) |
| ai-post-process | ✔️ 已实现 | 13 | [plan](ai-post-process/plan.md) · [s01](ai-post-process/stories/story-01-consistency-checker.md) [s02](ai-post-process/stories/story-02-format-validation.md) [s03](ai-post-process/stories/story-03-quality-gate.md) [s04](ai-post-process/stories/story-04-llm-refiner.md) [s05](ai-post-process/stories/story-05-llm-polisher.md) [s06](ai-post-process/stories/story-06-llm-arbiter.md) [s07](ai-post-process/stories/story-07-post-processor.md) [s08](ai-post-process/stories/story-08-report.md) [s09](ai-post-process/stories/story-09-standalone-polish.md) [s10](ai-post-process/stories/story-10-report-backend.md) [s11](ai-post-process/stories/story-11-report-dialog.md) [s12](ai-post-process/stories/story-12-integration.md) [s13](ai-post-process/stories/story-13-history-viewer.md) |
| ui-workbench | ✔️ 已实现 | 22 | [plan](ui-workbench/plan.md) · [s01](ui-workbench/stories/story-01-app-context.md) ... [s18](ui-workbench/stories/story-18-layout-simplify.md) · [s20](ui-workbench/plan.md#story-20) · [s21](ui-workbench/plan.md#story-21) · [s22](ui-workbench/plan.md#story-22) |
| batch-operations | ✔️ | 7/7 | [plan](batch-operations/plan.md) · [s01](batch-operations/stories/story-01-batch-esp.md) [s02](batch-operations/stories/story-02-batch-upload.md) [s03](batch-operations/stories/story-03-batch-download.md) [s04](batch-operations/stories/story-04-batch-write.md) [s05](batch-operations/stories/story-05-batch-translation.md) [s06](batch-operations/stories/story-06-multi-slot.md) [s07](batch-operations/stories/story-07-strings-batch.md) |
| vector-term-retrieval | ✔️ | — | [plan](vector-term-retrieval/plan.md) |
| stage-unification | ✔️ | 3/3 | [plan](stage-unification/plan.md) · [s01](stage-unification/plan.md#story-01) · [s02](stage-unification/plan.md#story-02) · [s03](stage-unification/plan.md#story-03) |
| label-system | ✔️ | 4/4 | [plan](label-system/plan.md) · [s01](label-system/plan.md#story-01) · [s02](label-system/plan.md#story-02) · [s03](label-system/plan.md#story-03) · [s04](label-system/plan.md#story-04) |
| llm-chat | ✔️ | 5/5 | [plan](llm-chat/plan.md) · [s01](llm-chat/stories/story-01-panel-framework.md) [s02](llm-chat/stories/story-02-core-backend.md) [s03](llm-chat/stories/story-03-loop-control-cards.md) [s04](llm-chat/stories/story-04-tool-system.md) [s05](llm-chat/stories/story-05-experience-optimization.md) |

## 规划中

| Epic | 状态 | Stories | 预估 | 文档 |
|------|------|---------|------|------|
| project-persistence | ✔️ 已实现 | 8 | 21h | [plan](project-persistence/plan.md) |

## 统计

- 已实现: 5+10+7+8+9+13+22+7+3+4+5+8 = 101 Story
- 规划中: 0
- 总计: 101 Story 文档
