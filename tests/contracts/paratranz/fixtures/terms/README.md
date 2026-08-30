# ParaTranz terms HTTP contract fixtures

These fixtures are public, synthetic HTTP transcripts captured against the controlled test server on 2026-08-30.
They document the API shape assumed by FR5.17 Story 00; they are **not** represented as a live ParaTranz sample.
The endpoint family is `/api/projects/{project_id}/terms` as consumed by the repository's current v1 client.

Every JSON fixture contains the request shape, HTTP status, a deliberately small header set, response body, and a
scenario note. Synthetic project/term IDs and example words are used throughout. Authorization, Cookie, API tokens,
email addresses, real project names, user translation data, and unrestricted response headers are forbidden.

Observed/assumed capability baseline:

- list accepts `page` and `pageSize` and may return `terms`, `results`, `items`, or a bare array; a page shorter than
  `pageSize` terminates pagination when explicit pagination metadata is absent;
- records require a positive integer `id`, `term`, and `translation`; writable fields are `term`, `translation`,
  `variants`, `caseSensitive`, `pos`, and `note`; timestamps and unknown fields are read-only snapshot metadata;
- no live evidence currently proves a global revision/ETag, conditional writes, or an idempotency-key contract;
  therefore snapshots aggregate canonical page digests and an executor must fully re-read the target before writing;
- POST create, PUT update, and DELETE are treated as non-retryable without reconciliation. A timeout after a write is
  an unknown outcome, never evidence that the server did not apply the operation;
- successful delete may be `204` with an empty body. This confirms only the requested HTTP operation, not ownership;
- authentication is required; `401` is authentication failure, `403` is permission failure, and `429` preserves
  `Retry-After`.

The product calibration is fixed as follows: plugin-scoped terms are visible lossy skips; one remote target activates
only one Variant mapping; and deletion is permitted only for items proven managed by a stored baseline. The
`sync-scenarios.json` golden input records these rules and the required conflict/echo/deletion cases.

A release claim still requires an explicitly configured integration smoke against an empty dedicated test project.
When such a sample is taken, it must be separately reviewed and redacted before replacing any controlled assumption.
