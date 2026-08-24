# FR26 Story-10 P0 final QA and FR24 handoff evidence

- **Date / platform**: 2026-08-24, Windows, Python 3.13.5, PyQt offscreen platform
- **Candidate**: shared FR26 worktree
- **Performance baseline**: Git `HEAD` `5c2ad1609cf5d86cdc0e3f5f5178ef0fcf0308b7`
- **Verdict**: **S10 P0 acceptance passes**. Fixed journeys, per-journey D/M/N, compatibility, S08/S09 production integration, architecture and fixed Windows performance gates all have matching evidence. This is the P0 milestone verdict; it does not claim FR24 theme implementation or erase the separate P1 acceptance rules.

## Resolved performance findings

### P0 - initial cold-import regression detected and resolved

The fixed comparator was run twice with 10,000 rows, 20 samples and 100 create/destroy lifecycles. It permits the wider of 5% or 10 ms.

- Run 1: baseline `0.9360 s`, candidate `1.0527 s`; allowed delta `46.8 ms`, observed delta `116.7 ms` -> fail.
- Run 2: baseline `1.0086 s`, candidate `1.0635 s`; allowed delta `50.4 ms`, observed delta `54.9 ms` -> fail.

These repeated failures correctly kept the S10 performance checkbox open while the eager import graph was profiled.

The import-time probe identified eager composition rather than Qt paint as the cause: importing `transbridge.ui.main_window` initially accumulated roughly `860 ms`; `transbridge.ui.__init__ -> app -> bootstrap` accounted for about `695 ms`, and later the direct MainWindow graph still pulled the whole ParaTranz/converter/parser chain through public package imports. The fix made UI/package entrypoints, ParaTranz public exports, ParaTranzWidget/ToolWindows construction and operation production composition lazy while preserving the public API.

Final authoritative rerun after the fix:

- baseline cold-start P95 `0.999186 s`, candidate `0.522746 s` -> pass;
- baseline window-open P95 `4.884 ms`, candidate `4.660 ms` -> pass;
- baseline 10k interaction P95 `1.377149 s`, candidate `0.557148 s` -> pass;
- candidate heartbeat max `49.163 ms` -> pass against `200 ms`;
- 100 lifecycles, `0` surviving wrappers;
- candidate lifecycle RSS delta `25,985,024 bytes` vs baseline `40,050,688 bytes`;
- comparator result: `failures=[]`.

A second complete confirmation run also returned `failures=[]` with a normal baseline distribution: cold-start P95 `0.481597 s` vs `0.892756 s`; window-open `4.641 ms` vs `4.973 ms`; 10k interaction `0.485521 s` vs `0.473533 s` (delta `11.988 ms`, allowed `23.677 ms`); heartbeat `40.542 ms`; 100 lifecycles with `0` surviving wrappers; RSS delta `26,054,656 bytes` vs `41,111,552 bytes`.

S10's Windows performance checkbox is therefore supported by two consecutive passing final runs and may be checked. No threshold was changed.

### P0 - initial 10k interaction regression detected and resolved

- Run 1: baseline `0.4722 s`, candidate `0.5221 s`; allowed delta `23.6 ms`, observed delta `49.8 ms` -> fail.
- Run 2: baseline `0.4952 s`, candidate `0.5188 s`; observed delta `23.6 ms` within the `24.8 ms` allowance -> pass, with less than 2 ms margin.

The table hot path was subsequently simplified and the final comparator passed as recorded above. The earlier runs remain useful evidence that the gate detected both import and interaction regressions before the fixes; no threshold was relaxed.

## Passing evidence

### P0 journey and feature-directed checks

- Initial combined journey/contract run: 89 passed, 1 failed because the new AI ToolWindows path required `host.app_runtime` even for the legacy public-port test host.
- S08 corrected the compatibility path to treat runtime as optional. Reverification: 72 passed across current journeys, AI S08, AI 10k/100 lifecycle, operation-plan UI, ParaTranz sync and FOMOD task entry.
- Current fixed journey file: 8 passed, including local startup without optional token, authoritative project restore, single public intent routing and AI progress-context transfer.

After S09 stabilized, the shared-worktree operation/compatibility group passed **98/98** tests: production operation routing/plan/facade, MainWindow/Workbench, background GUI operations, ParaTranz, hydrated write, typed FOMOD and bootstrap composition.

The final fixed P0 journey group passed **73/73** tests. [P0 journey evidence](../../plans/guided-ui-workflows/p0-journey-evidence.md) records fixture identity, intent, final D/M/N, focus, cancel, diagnostic, Run ID semantics, return context and artifact summary for J01-J09. J02-J07 finish at `D=8, M=3` versus `20/15`, a 60%/80% reduction. J01 is correctly `2/1/0` because the necessary source picker counts as one modal and remains outside the aggregate.

### Compatibility / parity

Command:

```text
python -m pytest -p no:cacheprovider tests/application/projects tests/persistence/v2 tests/application/sessions tests/contracts/test_task_runtime.py tests/contracts/test_task_runtime_backends.py tests/contracts/tasks tests/contracts/io tests/contracts/paratranz tests/application/fomod tests/integration/bootstrap tests/contracts/ui tests/integration/gui -q
```

Result: **390 passed** in 38.33 s. The warnings are existing compatibility deprecations (`get_by_key`, legacy `add(overwrite=True)`), not failures.

Coverage includes Project/Variant/Collection persistence and provisioning, sessions, TaskRuntime/backends/activity/recovery, I/O formats and identity mutation, ParaTranz sync, typed FOMOD entry, bootstrap composition, public UI imports and GUI parity.

### Architecture / lifecycle

- `python scripts/audit_ui_modularity.py`: exit 0, no non-exempt findings. This includes import cycles, module UI singletons, parent lookup, private collaborator access and private cross-feature imports.
- `--include-exempt` reports only the five registered FR25 compatibility hotspots listed in the migration inventory.
- Intent/ownership directed suite: **19 passed**, covering canonical intent dispatch, coordinators/shell, projection ownership and audit behavior.
- Performance/lifecycle directed suite: **43 passed**, covering general budgets, AI/Workbench 10k projections, AI/operation/Step2 100 lifecycles and shell timer release.
- `python -m compileall -q src/transbridge tests`: passed.
- Lazy public-package compatibility: ParaTranz's 18 legacy public exports and UI public imports passed 5/5 directed contract tests.

The Qt timers found by the source scan are single-shot scheduling/debounce, animation, autosave or event-coalescing owners; no new periodic TaskRuntime/window-tree polling path was found. Static audit and lifecycle tests are the enforceable evidence.

### Absolute candidate benchmark snapshot

The pre-fix absolute candidate run produced:

- window-open P95: `7.46 ms`;
- 10k interaction P95: `475.95 ms`;
- heartbeat max: `39.10 ms` (budget `200 ms`);
- 100 lifecycle iterations, `0` surviving Python wrappers;
- lifecycle RSS delta: `25,763,840 bytes`.

These values were diagnostic only. The authoritative relative comparator now passes with the final post-fix values recorded in “Resolved performance findings”.

### RSS probe fixture correction

The first performance suite run had 42 passes and one infrastructure failure: the isolated medium-parse RSS probe wrote into `qa-tmp-s03` without creating it. The test runner now creates its private directory before writing. The isolated check then passed, and the complete directed performance suite passed 43/43. This changes test setup only, not a budget or product path.

## FR24 handoff

`plans/ui-foundation-framework/migration-inventory.md` records Workbench, AI and operations as interaction-frozen but theme-not-started. It also freezes the rule that theme revisions cannot mutate data, RunSpec, task, operation or side-effect state.

P1 safe drop, command palette/help and accessibility remain independent and do not block the P0 handoff.

## Maintenance rerun conditions

1. rerun the 390-item parity group (or the full regression) if later code changes touch shared contracts;
2. rerun the fixed P0 journey group if an entry intent, modal boundary, default focus, cancel point or return context changes;
3. rerun the exact Windows comparator against the same baseline commit if later shared-import or table-render code changes; the current final result is two consecutive `failures=[]` runs.
