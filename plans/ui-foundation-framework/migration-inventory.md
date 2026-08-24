# FR26 -> FR24 presentation migration inventory

- **Updated**: 2026-08-24
- **Source interaction contract**: [guided-ui-workflows](../guided-ui-workflows/plan.md)
- **Target visual migration**: [ui-foundation-framework](plan.md) S06-S09
- **Status vocabulary**: `verified` means the production-reachable surface consumes the FR24 Foundation and has focused behavior/theme evidence; `pending` is reserved for unreachable compatibility UI with an explicit removal gate.
- **FR24 alignment**: S01～S09 completed on 2026-08-24; FR26 interaction contracts remain frozen and the final audit reports zero production blockers.

## Handoff status

### Shell / Start Center / guidance / discovery -> FR24 S06/S08

- **FR26 state**: `interaction-frozen`, covering Start Center destinations, Action Catalog/Intent Router, Guidance modes, Task Center, Command Palette/Context Help, safe drop and keyboard/accessibility contracts.
- **FR24 state**: `verified`. Shell, Start Center, Guidance, Task Center, Command Palette/Context Help, safe drop and settings use Palette/Foundation adapters; menu/settings msgids and locale persistence are integrated.
- **Stable component boundaries**:
  - shell composition and canonical intent ownership: `ui/shell/action_catalog.py`, `intent_router.py`, `intent_composition.py`, `menu_builder.py`;
  - visible roots: `start_center.py`, `task_center.py`, `command_palette_qt.py`, `context_help_qt.py`, `ui/guidance/qt.py`;
  - drop routing stays in `drop_binding.py`, `drop_review.py` and `drop_router.py`; theme code may style the review surface but cannot classify files or dispatch a drop.
- **Theme migration constraint**: a theme/locale revision must not change start destination, guidance mode, action availability, selected command, shortcut owner, Task capability, drop classification or canonical intent dispatch. Esc continues to close transient UI without stopping a task.

### Workbench / MainWindow -> FR24 S06

- **FR26 state**: `interaction-frozen`, with public intent routing, start-center entry, Workbench slices, save/filter/table state and TaskActivity projection covered by tests.
- **FR24 state**: `verified` for the production MainWindow/Workbench/Step2/project-bar/cards composition. Unreachable Step1/Step3/prompt-overlay compatibility files remain `pending` and are not reintroduced into production.
- **Stable component boundaries**:
  - shell ownership: `ui/shell/*`, coordinators and canonical `IntentId` routing;
  - Workbench composition: `workbench/widget.py` and public Step2 facade;
  - source/parse: `source_input_view.py`, `parse_presenter.py`, Step1 compatibility facade;
  - table workflow: `translation_table.py`, filters/labels/save/workflow presenters and views;
  - operation surfaces: Workbench cards delegate execution to application/operation ports.
- **Theme migration constraint**: a theme revision may update palette/brush/delegate caches only. It must not change collection identity, row keys, selection, scroll, edit buffers, render generation, save state, operation draft or TaskRuntime state.
- **Current FR25 exemptions retained**: `context.py`, `workbench/step1.py`, `workbench/step2.py`, `workbench/cards/download_card.py`, `workbench/cards/upload_views.py`. Each remains owned and expiring in `scripts/audit_ui_modularity.py`; FR24 must not silently broaden them.
- **Production reachability note**: `WorkbenchWidget` currently composes the Workbench/Step2 facade plus Guidance; historical Step1/Step3 and prompt-overlay modules are not default visual targets. S01 must prove a compatibility module is constructed by a production entry before S06 spends migration work on it.

### AI Translator -> FR24 S07

- **FR26 state**: `interaction-frozen` after S08. Quick-run/config/scope/run/result slices use stable public ports; production composition can use `AppRuntime.tasks`; unsupported recovery/log/artifact/retry capability remains disabled instead of being inferred.
- **FR24 state**: `verified`. Config, scope, single/batch/mixed progress, preview, report/history and post-process surfaces consume ThemeView while preserving RunSpec, Run ID, report identity and live input.
- **Stable component boundaries**: config/scope/run/result presenters and views; immutable run summary; Workbench result-location port; bounded worker/TaskRuntime adapter ownership.
- **Theme migration constraint**: theme changes must not mutate a frozen RunSpec, scope/config revision, worker ownership, checkpoint identity, result report, failed subset or commit outcome. A live theme subscription belongs to the subsystem root and must be closed with the window.

### Sync / write / FOMOD operations -> FR24 S06-S07

- **FR26 state**: `interaction-frozen` after S09 production wiring and shared-worktree reverification. Qt-free operation plan/preflight/result contracts feed the production facade and real `AppRuntime.tasks` ownership without replacing the four domain use cases.
- **FR24 state**: `verified`. Operation cards/plan/result surfaces and FOMOD consume semantic/domain tokens without regenerating plans, confirm tokens, preflight or task state.
- **Stable boundary**: UI plan/presenter emits one canonical intent/request; application sync, hydrated write and typed FOMOD pipelines retain validation, idempotency, staging, commit guard and atomic publish ownership. Unsupported batch/non-hydrated legacy contexts use the explicit compatibility path rather than a second partially migrated executor.
- **Theme migration constraint**: theme changes must not regenerate a confirm token, rerun preflight, submit a command, change retry subset or touch remote/file/archive side effects.

### FR26 accessibility baseline -> FR24 S08

- **FR26 state**: `interaction-frozen`, with tests for accessible names/descriptions, visible focus, default focus, Enter/Esc, dangerous operations and non-colour status across the key journeys.
- **FR24 state**: `verified`. Contrast validation, accessibility helpers, DPI/font contracts, locale catalog/fallback and the FR26 behavior source are covered by the light/dark/system accessibility matrix.
- **Theme migration constraint**: light/dark/system and locale preference changes cannot alter shortcut ownership, tab/escape semantics, enabled reasons, confirmation policy or the accessible text that identifies the current project/content/task.

## Current visual hotspot snapshot

The final machine-checkable audit covers 135 production-reachable UI modules plus the Markdown renderer. Running `scripts/audit_ui_foundation.py --final --include-pending-migrations` reports `blocker_count=0` and no audit errors. Remaining records are verified palette/token adapters, bounded Foundation internals, four structured exemptions, or unreachable compatibility modules with removal gates; they are not accepted as new theme ownership paths.

## Cross-feature handoff rules

1. Theme access enters through application/MainWindow composition only; presenters and application use cases do not discover a global ThemeService.
2. Theme revision is independent of project/projection/render/run/operation revisions.
3. No polling, window-tree scanning, QObject parent walking, private cross-feature access or second command path is introduced for theme updates.
4. Status remains understandable through text/icon/border/accessible description, never colour alone.
5. P1 safe drop, command palette/context help and accessibility enhancements do not block P0 handoff and must still emit the same canonical intents.
6. Performance comparison keeps the FR25 fixed Windows workload: 10,000 rows, 20 samples and 100 lifecycle iterations. FR24 adds theme-specific cold/switch/RSS gates without replacing this baseline.
7. FR24 final QA reruns FR26 J01-J09 under light/dark/system and during active switching; D/M/N, focus, cancel, return context, intent trace, operation digest and Run ID/side-effect counters remain unchanged.

## Final FR24 evidence

- Final directed UI/GUI/Provider/performance suite: 325 passed, 1 skipped on the repository Python 3.12 environment.
- Full repository suite excluding the separately executed FR24 performance file: 1875 passed, 6 skipped; one unrelated TaskRuntime timing assertion passed immediately in isolated rerun.
- FR26 × FR24 matrix covers J01～J09 under light, dark, system and running-switch modes without changing intents, D/M/N, focus, cancellation, return context, Run IDs or side-effect counters.
- Windows-visible authoritative profile (10,000 rows, 20 samples, 100 lifecycle iterations): cold P95 10.80 ms / RSS 1.02 MiB; theme switch P95 56.20 ms; heartbeat P95 55.80 ms; window baseline/final P95 1.090/1.022 s; 100-cycle RSS growth 12 KiB; icon cache 2 KiB of 8 MiB; idle/noop counters zero.
- Final static audit: zero blockers with pending migrations included; AI Translator, Smart Assistant, Workbench, ParaTranz, operations and FOMOD focused suites are green.
