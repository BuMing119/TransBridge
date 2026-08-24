# FR26 P0 fixed journey evidence

- **Date / platform**: 2026-08-24, Windows, Python 3.13.5, Qt offscreen tests
- **Contract**: [UX contract](ux-contract.md)
- **Baseline and target source**: [journey inventory](journey-inventory.md)
- **Directed result**: 73 passed

The counts use the S01 definitions: D is a necessary business decision, M is an `exec()` dialog/system picker/message box, and N is a top-level menu traversal. Non-modal pages/windows are not M. The fixed common path accepts safe context-derived defaults; voluntarily opening advanced configuration is outside the core count.

## Quantitative result

| Journey | Final D/M/N | Evidence result |
| --- | ---: | --- |
| J01 first plugin and local project | 2/1/0 | Corrected target met; the necessary source picker is one M. J01 remains outside the 30% aggregate because the baseline path was blocked. |
| J02 continue local project without cloud config | 0/0/0 | Target met. |
| J03 import existing translation into current content | 2/1/0 | Target met; only the necessary source picker is modal. |
| J04 AI translate and inspect | 1/0/0 | Target met. |
| J05-U upload to ParaTranz | 1/0/0 | Target met. |
| J05-D download and merge | 1/0/0 | Target met. |
| J06 write current source | 1/0/0 | Better than the original 1/1/0 target because the safe sibling output is prefilled and the plan is non-modal. |
| J07 build FOMOD | 2/2/0 | Target met; panel plus necessary source picker are the two M. |
| J08 recover/retry after failure | 1/0/0 | Replacement target met; only capabilities backed by a checkpoint/retry factory are shown. |
| J09 expert shortcuts | no added D/M; N=0 | No shortcut conflict and one canonical intent owner. |

For the comparable J02-J07 set, final `D=8, M=3` versus baseline `D=20, M=15`: D decreases 60% and M decreases 80%, exceeding NFR1.6's 30% target.

## Per-journey contract records

### J01 - first plugin and local project

- **Fixture / intent**: one readable ESP/ESM/ESL, no active local project; `project.create_from_source` (`IntentId.PROJECT_CREATE_FROM_SOURCE`).
- **Focus / cancel**: Start Center focuses “选择插件开始翻译”; draft focuses the first invalid field or its primary action. Cancelling the source picker or draft creates no visible project; editing after preview discards the token.
- **Failure / identity**: stable provisioning diagnostics remain beside the editable draft; prepare/commit uses one authoritative token and returns real Project/Variant IDs, with no second parse.
- **Return / artifact**: success opens Workbench with the hydrated collection; failure remains on the draft and keeps the previous active project/generation.
- **Tests**: `tests/ui/test_start_center_guided_project.py`, `tests/application/projects/test_provisioning.py`, `tests/persistence/v2/test_project_provisioning.py`.

### J02 - continue a local project without cloud configuration

- **Fixture / intent**: a valid active V2 project reference and no optional ParaTranz token; automatic `project.open` restore.
- **Focus / cancel**: no prompt on success; Workbench focuses the current translation-content selector. No unrelated cloud/config cancellation point exists.
- **Failure / identity**: an invalid reference returns to Start Center with a recoverable reason; a valid reference keeps authoritative Project/Variant/Collection identity.
- **Return / artifact**: success remains in the restored Workbench; no network task or Run ID is created.
- **Tests**: `tests/ui/ux/test_current_user_journeys.py`, `tests/ui/test_start_center_guided_project.py`.

### J03 - import existing translation

- **Fixture / intent**: one active hydrated translation content plus a supported JSON/EET/XT/SST/Strings source; `translation.import_source`.
- **Focus / cancel**: non-modal migration draft owns focus; only the source picker is M. Closing/cancelling the draft starts no worker and preserves the current Workbench object.
- **Failure / identity**: parse/mapping diagnostics stay with the draft; stale callbacks are bound to the active slot/generation.
- **Return / artifact**: success refreshes the same collection/selection context and reports the migration count; no separate durable Run ID is claimed for the legacy bounded adapter.
- **Tests**: `tests/ui/test_main_window_coordinators.py::test_migration_draft_is_non_blocking_and_owned_until_finished` plus parse/migration compatibility coverage.

### J04 - AI translate and inspect

- **Fixture / intent**: current translation content, valid local scope/config; `translation.ai.run`.
- **Focus / cancel**: the default entry opens Quick Run directly with the translate mode control focused; no target-selection modal. Batch remains an explicit advanced secondary action. Closing before start creates no run; explicit task cancel is required after start.
- **Failure / identity**: missing scope/config/model is shown as the adjacent preflight reason. Start freezes an immutable RunSpec and real Run ID; late callbacks are owner/generation guarded.
- **Return / artifact**: progress is activated once; report navigation validates owner and entry identity and returns to the originating Workbench content.
- **Tests**: `tests/ui/tools/test_ai_translator_story08.py` (default target-free entry, advanced batch, 10k/100 lifecycle, result navigation) and `tests/ui/ux/test_current_user_journeys.py`.

### J05-U/J05-D - ParaTranz upload/download

- **Fixture / intent**: one hydrated current content, selected remote project, valid credential/permission; `sync.paratranz.upload` or `sync.paratranz.download`.
- **Focus / cancel**: the non-modal operation plan focuses the first editable/invalid field or “运行预检”. Reject/destroy/parent teardown cancels the plan, releases its session and performs zero remote side effects.
- **Failure / identity**: preflight shows remote revision/permission/conflict diagnostics. Exactly one confirmation token creates one TaskRuntime Run ID; retry of a failed subset re-preflights and creates a new Run ID.
- **Return / artifact**: result projection returns to the same Workbench context with per-object success/failure; download does not silently overwrite a dirty Variant.
- **Tests**: `tests/ui/operations/test_production_facade.py`, `tests/ui/operations/test_operation_plan_presenter.py`, `tests/contracts/paratranz/`.

### J06 - write current source

- **Fixture / intent**: hydrated source snapshot/FormatId and writable sibling output; `publish.write`.
- **Focus / cancel**: context-derived safe output opens in the same non-modal plan; preflight is the default action. Rejecting before confirmation creates no file; cancellation invalidates the commit permit.
- **Failure / identity**: overwrite, writability, fingerprint and stage blockers appear in preflight. One confirmation creates one TaskRuntime Run ID and guarded atomic write.
- **Return / artifact**: completed result retains the Workbench context and exposes the output path; failed/cancelled commit cannot publish a late artifact.
- **Tests**: `tests/ui/operations/test_production_facade.py::test_production_write_uses_hydration_and_task_runtime_after_one_confirmation`, `tests/contracts/io/test_hydrated_write_operation.py`, operation task-adapter tests.

### J07 - FOMOD build

- **Fixture / intent**: one supported new FOMOD archive, optional old archive, safe sibling output; `publish.fomod`.
- **Focus / cancel**: FOMOD panel focuses the new-archive field; the common path uses the panel and one source picker (M=2), while output is suggested beside the source. Manual output is never overwritten by later format changes.
- **Failure / identity**: archive traversal/budget/input/output diagnostics remain in the typed pipeline/plan; confirmation creates a real guarded run when the production facade is used.
- **Return / artifact**: result remains in the FOMOD/task context and identifies the validated archive output; cancellation before confirmation publishes nothing.
- **Tests**: `tests/ui/test_fomod_journey.py`, `tests/application/fomod/test_operation_task_entrypoint.py`, safe-drop archive policy tests.

### J08 - failure recovery or retry

- **Fixture / intent**: failed/partial task with owner-bound activity; `task.open_activity`, then capability-backed `task.resume` or `task.retry`.
- **Focus / cancel**: Task Center retains activity focus; Esc never stops a task. The user makes one explicit recovery/retry decision.
- **Failure / identity**: checkpoint digest/input/owner/schema mismatch disables resume with a stable reason. Retry uses a typed factory and a new Run ID; a historical terminal state is never rewritten to running.
- **Return / artifact**: the activity/result projection retains the original project/content context and only exposes real logs/artifacts/navigation capabilities.
- **Tests**: `tests/contracts/tasks/test_activity_history_recovery.py`, `tests/contracts/tasks/test_operation_task_adapter.py`, `tests/ui/test_task_center.py`, accessibility Task Center coverage.

### J09 - expert operation

- **Fixture / intent**: current Workbench plus the Action Catalog; canonical intent varies by selected command.
- **Focus / cancel**: command palette search is the focus target; Enter dispatches the selected canonical intent once and Esc closes only the palette. Compact/guided modes keep the same enabled result.
- **Failure / identity**: stale/recent commands remain visible with a reason instead of dispatching an invalid request; one shortcut owner maps to one intent.
- **Return / artifact**: command execution uses the same destination and result as the visible UI entry, with no duplicate command or hidden second path.
- **Tests**: `tests/ui/test_intent_router.py`, `tests/ui/test_command_palette_help.py`, `tests/ui/guidance/`.

## Reproduction command

```text
python -m pytest -p no:cacheprovider tests/ui/test_start_center_guided_project.py tests/ui/ux/test_current_user_journeys.py tests/ui/test_main_window_coordinators.py tests/ui/tools/test_ai_translator_story08.py tests/ui/operations/test_operation_intent_routing.py tests/ui/operations/test_production_facade.py tests/ui/operations/test_operation_plan_presenter.py tests/ui/test_fomod_journey.py tests/contracts/tasks/test_activity_history_recovery.py tests/ui/test_intent_router.py tests/ui/test_command_palette_help.py -q
```

Result: **73 passed**. This evidence measures only the fixed common paths above; optional advanced configuration may add decisions or dialogs without changing the P0 count.
