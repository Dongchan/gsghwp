# Verification and recovery

## Verify proportionally

- A visual question — how the page looks, whether anything is clipped, distorted, or displaced — is answered by `hwp_render_page` and the PNG that HWP `CreatePageImage` produces. A render cannot report an ID, a cell address, a merge, or an exact dimension; structural inspection does that.
- Text or cell update: use the public write's operation-specific readback when `status=succeeded` and `verified=true`; do not repeat fast/structure inspections after that proof. This covers text and cell updates only — an image-to-layout reconstruction owes the closed loop of `native-layout.md` (inspect structure, render the changed page, compare with the source, patch locally, render again), and its readback is not a substitute for that comparison. For XLSX-driven patches this public write is one `hwp_patch_text_batch` — or one `hwp_fill_table` per target table — carrying the values from a single bounded `hwp_workflow_query.py read-xlsx` run, and its `request_id` must equal the caller's operation ID. If visual proof is needed, render only the highest-risk page once at 72 DPI. Re-read affected cells only when operation readback is unavailable, false, or contradictory, and save exactly once after final verification rather than before it.
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
- A blocking Hancom modal reports `target_modal_dialog=true`, the dialog diagnostic, and `reconnect_required=true`. Automatic production diagnostics never dismiss or choose an action. Call `hwp_inspect_dialog` to obtain the current dynamic control graph, let the model select one returned `actionable=true` control, and call `hwp_invoke_dialog_action` with that exact dialog handle and `control_id`. Then verify the returned post-action structure and reconnect. If the result also says `reconcile_required=true`, follow the same uncertain-mutation rule.

## Idempotency recovery

- Journal entries include `started_at`, `updated_at`, heartbeat time, attempt number, and `failed` or `aborted` terminal state.
- An `accepted` or `executing` attempt with no heartbeat for the configured timeout returns `operation_stale`; it is not re-executed automatically.
- After the user explicitly confirms recovery, send the same request ID and payload with `inputs.recovery.action` set to `reconcile` or `recover` and `confirmed=true`.
- `reconcile` records the stale attempt as aborted and performs only the native resolve/fast-snapshot path. It does not edit the document.
- `recover` archives the prior attempt as aborted and starts the next attempt. Use it only when the user accepts possible re-execution after an uncertain process failure.

## Finish

Call `hwp_disconnect` to release only the MCP transport reference. Never use Windows UI automation or Python COM document access to close, save, or reopen HWP. By default the document stays open and unsaved. When the user explicitly requests persistence and reopen verification, call `hwp_save_reopen_verify`; accept success only when the native result reports the same full path, matching page/control structure, and `modified=false` after reopen.
