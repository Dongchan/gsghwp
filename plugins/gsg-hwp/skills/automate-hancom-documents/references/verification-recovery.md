# Verification and recovery

## Verify proportionally

- For any request to analyze or verify an open HWP document, call `hwp_render_page` and inspect the PNG produced by HWP `CreatePageImage`; pair it with structural inspection when exact IDs, cells, merges, or dimensions matter.
- Text or cell update: re-read the affected cells.
- Table copy or layout insertion: fast-inspect the new controls and anchor format; detailed-inspect captions, merges, and page spans only when needed.
- Image insertion: verify the target cell contains a picture and render only to assess crop, distortion, or clipping.
- Page deletion: require the physical page count to decrease by exactly one.
- Control deletion: fast-inspect the affected page and require every requested control ID to be absent.
- Undo or redo: accept only a bounded explicit step count and the native action result for the same document identity.
- Never declare success from a render alone when structural data is available.

## Failure handling

- Do not assume a failed multi-action batch rolled back.
- Do not retry against old page numbers, cursor positions, control IDs, or state tokens.
- For a failed composite edit, re-inspect only the affected structure and report the exact partial state.
- Do not issue an arbitrary number of Undo commands. For an explicit rollback request, call `hwp_undo` with 1–20 steps; use `hwp_redo` only when the user explicitly asks to restore cancelled edits. Do not save implicitly after a failure; save-and-reopen is allowed only when the user explicitly requests `document.save_reopen_verify`.
- A failed API call does not persist a document-wide write lock. A later independent operation may continue, while a retry that depends on the failed operation must use fresh target data.

## Process loss and modal dialogs

- A blocked call watches the target HWP process and reports its exit without waiting for the overall call timeout. A process-loss result contains the `target_process_lost=true` and `reconnect_required=true` signals and invalidates the old live session. Do not retry through that session; reconnect only after the target HWP document is available again.
- If the process died after a mutation began, treat `reconcile_required=true` and `retry_safe=false` as an uncertain result. Report it and require explicit recovery confirmation before possible re-execution.
- A blocking Hancom modal reports `target_modal_dialog=true`, the dialog diagnostic, and `reconnect_required=true`. Production diagnostics never dismiss the dialog. Have the user inspect or close it, then reconnect; if the result also says `reconcile_required=true`, follow the same uncertain-mutation rule.

## Idempotency recovery

- Journal entries include `started_at`, `updated_at`, heartbeat time, attempt number, and `failed` or `aborted` terminal state.
- An `accepted` or `executing` attempt with no heartbeat for the configured timeout returns `operation_stale`; it is not re-executed automatically.
- After the user explicitly confirms recovery, send the same request ID and payload with `inputs.recovery.action` set to `reconcile` or `recover` and `confirmed=true`.
- `reconcile` records the stale attempt as aborted and performs only the native resolve/fast-snapshot path. It does not edit the document.
- `recover` archives the prior attempt as aborted and starts the next attempt. Use it only when the user accepts possible re-execution after an uncertain process failure.

## Finish

Call `hwp_disconnect` to release only the MCP transport reference. Never use Windows UI automation or Python COM document access to close, save, or reopen HWP. By default the document stays open and unsaved. When the user explicitly requests persistence and reopen verification, call `hwp_save_reopen_verify`; accept success only when the native result reports the same full path, matching page/control structure, and `modified=false` after reopen.
