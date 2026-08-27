# English-only LLM prompts

- **Status**: Implemented (2026-08-28; Stories 01–06 focused QA passed)
- **Request**: Replace every repository-maintained Chinese LLM/Agent prompt with an English equivalent.
- **Related ADR**: [ADR-005](../../docs/adr/005-toml-prompt-no-langchain.md), [ADR-033](../../docs/adr/033-shared-language-profiles-and-neutral-prompts.md)

## Goal

All static instructions maintained by TransBridge and sent to an LLM or Agent are written in English, while preserving prompt variables, JSON contracts, tool names, cache boundaries, target-language behavior, and native tool-calling semantics.

## Scope

In scope:

- Translation, extraction, quality-gate, refinement, polishing, and arbitration prompt templates and built-in fallbacks.
- Smart Assistant system/routing prompts, Agent prompts, retry prompts, Skill prompt templates, native tool descriptions, and parameter descriptions exposed to a model.
- Other direct LLM prompts, including FOMOD text translation and explicit provider capability probes.
- Focused regression tests and an automated inventory guard for repository-owned prompt instructions.

Out of scope:

- Chinese UI labels, accessibility text, notifications, logs, exceptions, source comments, and documentation that are never sent to a model.
- Dynamic user content, source/translated text, terminology values, target-language names, and Chinese translation examples needed to describe or test Chinese output.
- Renaming public tool identifiers, JSON fields, namespaces, prompt variables, or the `zh_CN` locale files.

## Current facts and constraints

- Prompt templates use TOML plus `string.Template`; `$...` placeholders and strict JSON examples are validated by the existing prompt-contract tests.
- Smart Assistant tool descriptions are part of the model-visible tool directory/native schemas even when their corresponding UI display names remain Chinese.
- The working tree contains substantial unrelated and overlapping user changes, especially around native function calling. Edits must be limited to prompt text and matching assertions without reverting those changes.
- English instructions may still contain dynamic Chinese payloads or target-language examples. The guard must inspect repository-owned static prompt text, not reject arbitrary runtime user data.

## Stories

### Story 01 — Translation and post-processing prompts

**Status**: Completed

**Acceptance criteria**

- All repository-maintained instructions in `data/prompts/**` and matching Python fallbacks/dynamic prompt fragments are English.
- Existing variables, output JSON keys, enums, ordering requirements, and cache layout remain unchanged.
- Chinese target output examples and locale metadata remain valid where they are data rather than instructions.

**Files**

- `data/prompts/**/*.toml`
- `src/transbridge/ai_translator/prompt_builder.py`
- `src/transbridge/ai_translator/post_processor/{quality_gate,llm_refiner,polisher,llm_arbiter}.py`
- Focused tests under `tests/ai_translator/`

**Validation**

- Prompt builder and post-processor prompt/contract tests.

### Story 02 — Smart Assistant, Agent, Skill, and tool-schema prompts

**Status**: Completed

**Acceptance criteria**

- The Smart Assistant's system/routing instructions, built-in Agent prompts, retry prompt, Skill prompt template, tool descriptions, summaries, and parameter descriptions exposed to the model are English.
- User-facing display names and UI notifications are not changed merely because the same registration structure contains model-facing text.
- Native function-calling and layered tool discovery behavior remain unchanged.

**Files**

- `src/transbridge/smart_assistant/prompts.py`
- `src/transbridge/smart_assistant/agents/agent_registry.py`
- `src/transbridge/smart_assistant/reflexion/retry_handler.py`
- `src/transbridge/smart_assistant/tool_registry.py`
- `src/transbridge/smart_assistant/tools/*.py`
- `data/skills/*.toml`
- Focused tests under `tests/smart_assistant/`

**Validation**

- Smart Assistant prompt layering, native tools, tool registration, Agent, Skill, and retry tests.

### Story 03 — Remaining direct LLM prompts and completeness guard

**Status**: Completed

**Acceptance criteria**

- Direct LLM prompts outside the main prompt subsystems use English instructions.
- A focused test inventories known static prompt producers and fails if repository-maintained Chinese instructions are reintroduced, while allowing dynamic payloads and explicit Chinese target examples.
- No ordinary UI or diagnostic localization is changed.

**Files**

- `src/transbridge/fomod/*.py`
- Other direct `chat(...)` callers found by the audit
- Focused FOMOD/infra/UI tests and a prompt-language regression test

**Validation**

- Direct-call tests plus the new prompt-language guard.

### Story 04 — Language-neutral post-processing templates

**Status**: Completed

**Acceptance criteria**

- Quality-gate, refinement, polish, and arbitration each maintain one stage-level `default.toml`; adding a target locale does not require copying those four templates.
- Runtime prompts receive human-readable source and target language names from `langs/{target_locale}.toml` through `$source_lang` and `$target_lang`.
- Loaders read only `default.toml`; legacy `{target_locale}.toml` stage files are not supported. The existing built-in Python fallback remains available when the default file is missing or invalid.
- Refinement prompts explicitly identify both source and target languages, matching the other three stages.
- Existing placeholders, structured-output contracts, cache boundaries, post-processing behavior, and target-locale configuration remain compatible.

**Files**

- `data/prompts/{quality_gate,refinement,polish,arbitration}/default.toml`
- `src/transbridge/ai_translator/post_processor/{quality_gate,llm_refiner,polisher,llm_arbiter}.py`
- Focused tests under `tests/ai_translator/post_processor/`
- Post-processor developer documentation describing prompt layout and locale extension

**Implementation outline**

1. Rename the four maintained `zh_CN.toml` stage templates to `default.toml` without changing their output schemas.
2. Change each stage loader to read only the stage-level default template and remove the old locale-specific files.
3. Require and render the stable game/source/target variables in all stage system templates; add the missing language pair to refinement.
4. Add default-path, missing-template, and non-Chinese-locale rendering tests; update documented extension steps.

**Validation**

- `python -m pytest tests/ai_translator/post_processor -q --basetemp=<workspace-task-temp>`: `105 passed, 1 warning`; the warning is the existing `.pytest_cache` permission warning.
- Focused `.venv/Scripts/ruff.exe check`: passed.
- Focused `.venv/Scripts/ruff.exe format --check`: 5 files already formatted.
- All four `default.toml` files parse with `tomllib`; focused `git diff --check` passed with only line-ending warnings.

### Story 05 — Strict shared language profiles and primary prompt split

**Status**: Completed

**Acceptance criteria**

- A shared configuration module validates and loads language metadata for every prompt consumer.
- Main translation and noun extraction use stage-level `default.toml` templates; `langs/{locale}.toml` contains metadata and optional example data only.
- Unknown, malformed, or incomplete language profiles fail before an LLM request instead of silently selecting Simplified Chinese.
- Built-in prompt fallbacks are language-neutral and retain the existing structured-output and cache contracts.
- Legacy prompt tables inside language files are not read.

### Story 06 — Shared language contract across consumers and UI

**Status**: Completed

**Acceptance criteria**

- Post-processing and both FOMOD implementations resolve model-facing language names through the shared profile loader.
- Smart Assistant translation Agent/Skill instructions do not impose a fixed English-to-Chinese or name-rendering policy.
- The AI translation target selector is populated from installed language profiles and persists locale codes rather than display labels.
- The Smart Assistant configuration tool rejects an unavailable target locale before mutating or saving configuration.
- Focused regression tests cover language discovery, fail-fast behavior, prompt injection, FOMOD resolution, and UI round-tripping.

**Validation**

- 219 focused tests passed across language profiles, translation/extraction, all post-processing stages, FOMOD, Smart Assistant, and both target-language selectors.
- Focused Ruff lint and format checks passed for the implementation surface.
- Repository prompt/layout scans found no remaining hardcoded locale-to-language maps or Chinese-only translation/name-rendering policies in active source and prompt data.

## Dependency order and parallelization

- Stories 01–03 have disjoint production ownership and can be implemented by separate Agents in parallel.
- Story 04 depends on the English templates from Stories 01 and 03 and supersedes their per-locale post-processing file layout.
- Story 05 supersedes the remaining per-locale primary prompt layout; Story 06 depends on its shared loader.
- The main session owns plan/index updates, integration review, overlap resolution, completeness scanning, and final validation.
- Tests that assert Chinese wording must be updated to assert the equivalent English contract, not weakened or removed.

## Risks and rollback

- Risk: semantic drift in strict JSON/output rules. Mitigation: preserve schemas and run existing contract tests.
- Risk: treating UI localization as prompt text. Mitigation: require evidence that a string reaches an LLM message/schema before changing it.
- Risk: prompt-language checks reject Chinese translation payloads. Mitigation: test only static instructions or explicitly allow locale data/examples.
- Story 04 intentionally removes compatibility with locale-specific post-processing stage files. Existing custom stage templates must be moved to the corresponding `default.toml`; language names remain in `langs/{locale}.toml`.
- Rollback is textual: each Story can be reverted independently because public APIs and persisted data formats are unchanged.

## Assumptions and open questions

- “Chinese prompts” means model-visible repository instructions, not all Chinese strings in the project.
- No user decision is required unless the audit reveals a string serving both a model contract and a user-visible localization contract that cannot be separated safely.
