# Live inspection

## Connect

Call the selected read tool directly. Production inspection auto-connects the single active, saved, editable HWP and does not require a session ID. Pass `document_path` only when more than one open document makes the target ambiguous. `hwp_connect` remains available for explicit session diagnostics; repeated calls for the same connected document return the existing session instead of failing.

## Choose the smallest read

| Need | Tool |
|---|---|
| Page text, controls, table anchors, dimensions | `hwp_inspect_page_fast` |
| The same plus cell addresses and values | `hwp_inspect_page_fast(include_cells=true)` |
| Current-or-last HWP cursor, selected text/cells/table, current paragraph and character formatting | `hwp_inspect` |
| Native style names and IDs | `hwp_list_styles` |
| Table refs, merged cells, captions, page span, optimistic token | `hwp_inspect_structure` |
| HWP page analysis or result verification | `hwp_render_page` (`CreatePageImage`) |
| Wait for cursor, structure, window, or dialog changes in QA | `hwp_watch_state` |

Fast inspection is C++/ATL in-process and must be tried before detailed structure inspection. Detailed inspection is an edit preflight, not a polling API.

An identical fast inspection is cached for the current live-change revision, with at most 32 page/mode entries. Any MCP mutation, external HWP change event, reconnect, or disconnect invalidates it, so repeated reasoning reads avoid a second full control scan without serving pre-edit structure after a change.

`hwp_list_styles` likewise reuses one style scan for the same document and `state_token`. A local mutation, external HWP change event, document change, reconnect, or disconnect invalidates that result.

When `hwp_inspect_structure` receives an explicit nonzero `page`, native control traversal is limited to that requested page instead of scanning unrelated pages. Use `page=0` only for the current-page form.

`hwp_inspect`, `hwp_list_styles`, `hwp_inspect_page_fast`, `hwp_inspect_structure`, and `hwp_render_page` return a short MCP text summary and keep the complete result once in the structured payload. Read the structured result; do not expect or parse a duplicate JSON copy from the text summary.

Every top-level `instance_id` returned by fast inspection is immediately reusable. Pass it directly as `target_id` to `hwp_add_caption`, `hwp_replace_image`, or `hwp_fill_table`. For a tool whose schema has a `target` object, including `hwp_format_table`, `hwp_merge_table_cells`, and `hwp_split_table_cell`, pass `target: {"target_id": instance_id}`. Include it in `control_instance_ids` for `hwp_delete_control`. After a worker reload, inspect again before relying on cached page and index metadata.

For an HWP document analysis or verification request, render the relevant page with `hwp_render_page` and inspect the returned PNG. Rendering establishes visual facts; use the structural reads above alongside it for IDs, cell addresses, merges, and exact dimensions.

## State discipline

- Moving focus from HWP to Codex stops the visual caret blink but does not invalidate HWP's last caret. Treat `active_target.basis=current_or_last_hwp_position` as the live edit anchor until an HWP state change is observed.
- Use `active_target.kind`, `selection_mode_raw`, `control_instance_id`, and `cell_address` instead of inferring selection type from whether `selected_text` is empty. A cell block can have empty selected text, and a selected table is a control selection.
- Page numbers are current results, not durable anchors. Prefer control identity and structure.
- Pass exact cursor, selection, original text, `table_ref`, and `state_token` values back to writes.
- If any expected value changes, discard the pending write and inspect again.
- Do not use screenshots to infer cell addresses, merges, captions, or styles.
