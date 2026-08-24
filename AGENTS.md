# TransBridge Agent Guide

## Project scope

- TransBridge converts translation files between XTranslator, ESP-ESM Translator, and ParaTranz.
- Production code lives in `src/transbridge/`; tests live in `tests/`; one-off utilities live in `scripts/`.
- Keep domain and conversion logic independent of PyQt UI code. Add application or entry-point wiring only at the appropriate boundary.
- CLI entry point: `transbridge.cli:main`; MCP entry point: `transbridge.entrypoints.mcp:main`.

## Environment and commands

- Target Python is 3.12 or later; use the repository's existing `uv` environment and lockfile.
- Run focused tests first: `uv run pytest tests/<relevant-path> -q`.
- Before handoff, run relevant tests plus `uv run ruff check src tests` and `uv run ruff format --check src tests` when practical.
- Pytest markers: use `slow` for lengthy tests, `integration` for external-system integration, and `llm` only for tests requiring an LLM API.

## Code conventions

- Follow `pyproject.toml`: Ruff formatting, double quotes, 120-character lines, and sorted imports.
- Keep changes small and local; preserve public APIs and file-format fidelity unless the task explicitly changes them.
- When correcting code, configuration, or documentation, update the canonical source at the root cause and remove superseded or contradictory content. Do not append compensating notes, overrides, duplicated clauses, or patch-layer workarounds unless the artifact is explicitly historical or incremental (such as a changelog or migration record), or compatibility requires it; document any required temporary layer and its removal condition.
- Add or update focused regression tests for behavior changes and bug fixes.
- Keep UI work responsive: place long-running work in the established worker/coordinator patterns rather than blocking the Qt event loop.
- Do not silently swallow exceptions. Provide actionable context and retain the original cause where appropriate.

## Code size and responsibility

- Keep hand-written production classes and modules focused on one cohesive responsibility.
- A file over 500 physical lines or a primary class over 30 methods requires a responsibility review. Over 700 lines or 40 methods must normally be split before further expansion.
- Do not add new responsibilities to code already above these thresholds. Small, local bug fixes may remain in place, but new features should be extracted as complete, independently testable slices.
- Split by responsibility and data flow, with explicit public interfaces between modules. Do not hide size in generic helpers, miscellaneous modules, mixins, deep inheritance, or a replacement god Controller/Manager.
- Preserve public interfaces and observable behavior during extraction, and verify the split with focused regression tests.
- Generated code, third-party code, and data-only declaration files are exempt from the numeric thresholds.
- If a safe split is not currently practical, document the reason, risk, and concrete condition for revisiting the exception.

## Repository hygiene

- Do not commit credentials, user translation data, build artifacts, virtual environments, caches, or temporary QA directories.
- At the end of a task, remove temporary files and directories created by that task (such as task-specific `.tmp-*` or `qa-tmp-*` paths). Verify their ownership first; never remove pre-existing user files or shared temporary directories.
- Treat `uv.lock`, package/build configuration, and installer files as intentional changes: modify them only when required by the task.
- Preserve unrelated working-tree changes. Do not use destructive Git commands without explicit user approval.
- Reuse the repository's existing `.agents` / `.codex` workflow assets when a task calls for their corresponding process.

## Completion expectations

- State which validation commands were run and any checks intentionally not run.
- Summarize user-visible behavior changes and note compatibility or migration implications when applicable.
