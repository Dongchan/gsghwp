# QA Operation Internals

Use this reference only with the QA profile for diagnostics, routing evaluation, or implementation maintenance. Production models use the concrete public action tools listed in `SKILL.md`.

## Shared executor boundary

Every public action builds an explicit canonical operation and a fixed logging intent, then delegates to the same internal executor. The executor attaches the single active editable document, applies idempotency, invokes the existing native bridge, and converts its result. A public action never invokes natural-language workflow search.

The fixed runtime path remains launcher to MCP to the C++/ATL component inside Hwp.exe, followed by the official Automation API and the protocol-9 native batch. Python COM editing and UI automation are not fallback paths.

## Generic QA operation

`hwp_operate(intent, inputs, guards)` is exposed only in QA. When `inputs.operation` is present, it is authoritative; `intent` is logging and search-history metadata and does not select a different workflow. Structural contradictions between typed fields may return `schema_conflict`.

When `inputs.operation` is absent, only an exact canonical ID, registered exact alias, or deterministic corpus match may auto-execute. The QA router may retain up to 24 candidates using the Korean corpus, BM25, 2/3-character n-grams, and compact local semantic embeddings, but search-only or near-tied results return at most three candidates instead of executing.

Only a certified recipe or primitive graph may execute. The 1,452-entry official index is a local single-primitive fallback and must not be used to invent a multi-API sequence. Detailed official API lookup belongs in `official-api.md`.

## Replay and recovery

Writes use a stable `inputs.request_id` for an identical payload. A committed replay returns the recorded result without executing native commands again, while a changed payload with the same ID returns `request_id_conflict`.

Do not automatically retry `operation_stale`, `known_failure`, `unsupported`, partial changes, or transport failures. Reconciliation and explicitly confirmed recovery follow `verification-recovery.md`. Guards are opt-in and a missing cursor guard is not a reason to block execution.
