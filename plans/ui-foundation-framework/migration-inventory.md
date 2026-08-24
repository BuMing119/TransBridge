# FR26 -> FR24 presentation migration inventory

- **Updated**: 2026-08-24
- **Source interaction contract**: [guided-ui-workflows](../guided-ui-workflows/plan.md)
- **Target visual migration**: [ui-foundation-framework](plan.md) S06-S09
- **Status vocabulary**: `interaction-frozen` means FR26 has fixed the public UI/data/lifecycle contract; it does **not** mean FR24 theme tokens or visual migration are implemented.
- **FR24 alignment**: accepted into the FR24 plan on 2026-08-24; all FR26 interaction prerequisites are satisfied, while every FR24 visual/runtime item remains `not-started`.

## Handoff status

### Shell / Start Center / guidance / discovery -> FR24 S06/S08

- **FR26 state**: `interaction-frozen`, covering Start Center destinations, Action Catalog/Intent Router, Guidance modes, Task Center, Command Palette/Context Help, safe drop and keyboard/accessibility contracts.
- **FR24 state**: `not-started`. These surfaces still require semantic tokens, palette/component adapters, light/dark/system verification and locale integration.
- **Stable component boundaries**:
  - shell composition and canonical intent ownership: `ui/shell/action_catalog.py`, `intent_router.py`, `intent_composition.py`, `menu_builder.py`;
  - visible roots: `start_center.py`, `task_center.py`, `command_palette_qt.py`, `context_help_qt.py`, `ui/guidance/qt.py`;
  - drop routing stays in `drop_binding.py`, `drop_review.py` and `drop_router.py`; theme code may style the review surface but cannot classify files or dispatch a drop.
- **Theme migration constraint**: a theme/locale revision must not change start destination, guidance mode, action availability, selected command, shortcut owner, Task capability, drop classification or canonical intent dispatch. Esc continues to close transient UI without stopping a task.

### Workbench / MainWindow -> FR24 S06

- **FR26 state**: `interaction-frozen`, with public intent routing, start-center entry, Workbench slices, save/filter/table state and TaskActivity projection covered by tests.
- **FR24 state**: `not-started`. MainWindow status/menu/progress, Workbench Step1/Step2/project bar/cards and domain/status colours still need semantic/domain tokens and real light/dark/system verification.
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
- **FR24 state**: `not-started`. Config, progress, mixed/batch progress, result/report and post-process surfaces still require semantic tokens and theme-revision tests.
- **Stable component boundaries**: config/scope/run/result presenters and views; immutable run summary; Workbench result-location port; bounded worker/TaskRuntime adapter ownership.
- **Theme migration constraint**: theme changes must not mutate a frozen RunSpec, scope/config revision, worker ownership, checkpoint identity, result report, failed subset or commit outcome. A live theme subscription belongs to the subsystem root and must be closed with the window.

### Sync / write / FOMOD operations -> FR24 S06-S07

- **FR26 state**: `interaction-frozen` after S09 production wiring and shared-worktree reverification. Qt-free operation plan/preflight/result contracts feed the production facade and real `AppRuntime.tasks` ownership without replacing the four domain use cases.
- **FR24 state**: `not-started`. Workbench operation cards, confirmation/result surfaces and FOMOD panel still require semantic/domain tokens after S09 freezes their final composition.
- **Stable boundary**: UI plan/presenter emits one canonical intent/request; application sync, hydrated write and typed FOMOD pipelines retain validation, idempotency, staging, commit guard and atomic publish ownership. Unsupported batch/non-hydrated legacy contexts use the explicit compatibility path rather than a second partially migrated executor.
- **Theme migration constraint**: theme changes must not regenerate a confirm token, rerun preflight, submit a command, change retry subset or touch remote/file/archive side effects.

### FR26 accessibility baseline -> FR24 S08

- **FR26 state**: `interaction-frozen`, with tests for accessible names/descriptions, visible focus, default focus, Enter/Esc, dangerous operations and non-colour status across the key journeys.
- **FR24 state**: `foundation-not-started`. FR24 adds token contrast validation, common helpers, DPI/font checks and locale resources; it must extend `tests/ui/test_accessibility_contracts.py` instead of replacing the FR26 behavior source.
- **Theme migration constraint**: light/dark/system and locale preference changes cannot alter shortcut ownership, tab/escape semantics, enabled reasons, confirmation policy or the accessible text that identifies the current project/content/task.

## Current visual hotspot snapshot

The following counts are routing hints, not accepted exemptions and not proof that every match is a colour. They count `hex/QColor/setStyleSheet/styleSheet` occurrences on 2026-08-24:

| Area | Matches |
| --- | ---: |
| `ui/main_window.py` | 0 |
| `ui/shell` | 3 |
| `ui/guidance` | to be classified by S01 |
| `ui/workbench` | 116 |
| `ui/tools/ai_translator` | 86 |
| `ui/tools/smart_assistant` | 160 |
| `ui/paratranz` | 17 |
| `ui/operations` | 0 |
| `ui/tools/fomod` | to be classified by S01 |

FR24 S06/S07 must classify each real visual occurrence as semantic, domain, user-data or structural; migrate it or record an owner/reason/removal gate. The counts must not be used as a target by deleting structural QSS blindly.

## Cross-feature handoff rules

1. Theme access enters through application/MainWindow composition only; presenters and application use cases do not discover a global ThemeService.
2. Theme revision is independent of project/projection/render/run/operation revisions.
3. No polling, window-tree scanning, QObject parent walking, private cross-feature access or second command path is introduced for theme updates.
4. Status remains understandable through text/icon/border/accessible description, never colour alone.
5. P1 safe drop, command palette/context help and accessibility enhancements do not block P0 handoff and must still emit the same canonical intents.
6. Performance comparison keeps the FR25 fixed Windows workload: 10,000 rows, 20 samples and 100 lifecycle iterations. FR24 adds theme-specific cold/switch/RSS gates without replacing this baseline.
7. FR24 final QA reruns FR26 J01-J09 under light/dark/system and during active switching; D/M/N, focus, cancel, return context, intent trace, operation digest and Run ID/side-effect counters remain unchanged.

## Evidence available at handoff

- Public import, persistence/project/session, TaskRuntime, I/O, ParaTranz, FOMOD and UI parity suite: 390 passed on Windows/Python 3.13.5.
- UI intent/ownership/modularity directed suite: 19 passed; `python scripts/audit_ui_modularity.py` returned no non-exempt finding.
- Final S09 shared-worktree production/compatibility group: 98 passed, covering operation routing/plan/facade, MainWindow/Workbench, background GUI operations, ParaTranz, hydrated write, FOMOD and bootstrap composition.
- FR25 fixed benchmark remains a release gate. After eager UI/ParaTranz/operation imports and the table hot path were corrected, two consecutive final 10,000-row/20-sample/100-lifecycle comparator runs returned `failures=[]`; see [S10 QA report](../../docs/test-reports/guided-ui-workflows-s10-qa-2026-08-24.md).
