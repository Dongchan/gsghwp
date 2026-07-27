---
name: automate-hancom-documents
description: Route every Hancom/HWP document edit, analysis, and verification request to the GSG_HWP_BETA (`gsg-hwp-beta-live`) MCP. Never fall back to computer-use; report an unavailable or closed MCP connection and stop. Supports already-open HWP inspection and editing, native tables and captions, ratio-preserving images, PPTX/XLSX import, reference-layout rebuilding, and isolated batch workflows on Windows with Hancom Office.
---

# Hancom Document Automation

Start with the production action path. For a screenshot or image-to-editable-table request, immediately read `references/native-layout.md`; when the host did not expose this skill path, read the identical MCP resource `gsg-hwp-beta://reference/native-layout`. Read other references only after a public action returns a concrete need for them.

## Non-negotiable rules

- Route every Hancom/HWP edit, analysis, and verification request through the `gsg-hwp-beta-live` MCP and this skill.
- Treat a change as successful only after the selected public tool reaches a terminal success result and its operation-specific readback confirms the requested state. A dispatched call or mutation attempt without that readback is not success.
- Choose the most specific public action tool for the user's request. Never invent an API name, cell address, control ID, or hidden execution field.
- Before every public write, choose a caller-owned `operation_id` (for example, a UUID) and keep it unchanged until that logical operation reaches a terminal result. Reuse the same ID only when retrying the same payload or recovering a lost response; use a new ID for an intentional second execution, even when its payload is identical. Never derive the ID from the payload. The same ID with different inputs is a conflict. Completed results remain replayable for up to 30 days by default; when the terminal journal exceeds 10,000 records or 64 MiB, the oldest terminal results are evicted first. Never recycle an ID after journal expiry or a `not_found` response.
- Live mode attaches only to the exact active, saved, editable HWP already registered in ROT. It does not open, save, clear, quit, or change history by default. Use `hwp_undo` or `hwp_redo` only for an explicit history request, and `hwp_save_reopen_verify` only for an explicit save-and-reopen verification request.
- Never use `computer-use`, Windows UI automation, keyboard/mouse control, or a Python COM document call as a fallback for an HWP operation. If the `gsg-hwp-beta-live` MCP is unavailable, disconnected, or returns a closed transport, report that exact state and stop. If a certified native operation is unavailable, return the structured result from the selected HWP tool and stop.
- Do not hard-code a document title, page number, table size, or cell map in reusable code.
- When the user asks to analyze or verify an open HWP document, call `hwp_render_page`; it preserves document state and uses HWP `CreatePageImage`. Use fast or detailed inspection alongside the render when structural facts such as IDs, cell addresses, merges, or exact dimensions are also required.
- `hwp_render_page` publishes each completed PNG at a unique path only after `CreatePageImage` finishes. Active session directories are protected by a worker ownership lock. Released results have a 24-hour TTL and at least a 15-minute client-read grace; cleanup runs at worker startup and every 15 minutes, removes abandoned partial files, and evicts the oldest eligible results when the preview store exceeds 64 MiB.
- Document-local styles are read with `hwp_list_styles`; never use web search to infer a style ID or local title convention.
- Table-series tools copy the first table, regenerate each copied automatic-number control, and update only the literal title suffix while preserving the source caption style. Do not follow a successful series call with manual `hwp_add_caption` calls. If caption verification fails, report that failure instead of retrying captions or Undo in a loop.
- After a plugin update, stay in the current Codex task and call `hwp_runtime_info`. The stable MCP proxy reloads the worker automatically when its source hash changes and emits a tool-list change notification; call `hwp_reload` only to force a reload. Compare `mcp`, `worker_process_id`, `source_path`, and `tool_schema_hash` before continuing. If the refreshed direct tool is not yet visible, call it through `hwp_execute` with the exact tool name and arguments.
- The host may expose this skill directly from the plugin manifest, from a host cache, or through a legacy global Junction. Read the exact displayed `SKILL.md` path directly without assuming its storage form; do not locate, search, or compare other cache or installation paths.
- For table data keyed by visible header labels, use `records`. Use `cells` only with verified cell addresses and `rows` only when the user supplied an explicit `start_cell`. A returned `target_id` changes only the target, never the chosen data format.
- Explicit 1 mm table rows and columns are supported. To make the physical cell reach that size, set every affected cell to `font_size_pt=1`, `line_spacing_percent=50`, and zero padding; larger inherited text or padding can make HWP expand the cell. HWP stores 1 mm as 283 HWPUNIT, so inspection reports about 0.998 mm per row or column.
- For an irregular or partially merged table, use the inspected `CellTopology`: actual cell address, `row_span`, `column_span`, and physical right/down neighbours. Never synthesize `F1`, `A11`, or an address-distance move sequence. Column and row formatting starts at the requested real cell; merging proceeds only through the recorded neighbour path after the covered owners form a gap-free rectangle.
- `hwp_split_table_cell` has two deliberately different modes. Use `split_mode="equal"` only to subdivide one unmerged physical cell. Use `split_mode="existing_grid"` only to restore the exact `row_span` × `column_span` grid hidden by a merged cell. Do not split a merged header merely to reach a column for formatting; format the real intersecting cell instead. A requested existing-grid split that does not match the inspected spans must be rejected before mutation.
- For screenshot or reference-layout reconstruction, leave photos, maps, and drawings as separate raster images and build the remaining editable structure with an adaptive native table grid. Set `border_mode="explicit"`; omitted edges then become real no-border edges instead of inheriting the table grid. Derive row and column breakpoints from visible box edges, text anchors, and connector endpoints, using 1 mm subdivisions only where the source needs them. Use cell fill only for a visible color surface and cell borders only for visible outlines or connectors; never use a filled spacer cell as a line. Do not merge across a connector endpoint. A rendered extra line is a failed verification, even when all requested text is present.
- Use `hwp_insert_layout` for live insertion away from the document end. `layout.target=current` inserts at the user's collapsed cursor. `layout.target=after_page` with `page=N` inserts at the start of the existing next page and adds one trailing page break, so it creates exactly one isolated page without a blank page; after the last page it appends with one leading page break. Do not add another `page_break` block. Continue to use `hwp_append_layout` only for the document end.
- For a reference image, use this direct sequence: exact public tool selection, compact `hwp_analyze_reference_image`, its `ordinary_layout` or `reference_layout_bulk` recommendation, `hwp_preflight_layout`, one layout bulk write, structural inspection plus one required-page render, then a range-based patch only when verification identifies a local defect. Do not call capabilities, enumerate all tools, search source, connect explicitly, or inspect unrelated pages first. If one exact deferred tool is missing, search that exact name at most once.
- The default image analysis response is sufficient for routing and preflight. Call `hwp_get_reference_image_analysis_section` only for one needed section with a small limit. Use artifact paths or a low-resolution/cropped render during refinement and inspect the full-resolution page only for final verification.
- Never delete and rebuild a page for a local correction. Use `hwp_patch_text` for a sentence or line break, table formatting tools for an existing ordinary table, image replace/size paths for one picture, and `reference_layout_patch` for existing reference-grid rows, columns, styles, or edges. Rebuild only when structural inspection proves that no local target can represent the requested correction.
- `headers` filters target tables; it is not a copy of the `records` keys. Use only labels known to be visibly present in the target table. If a page already narrows the request sufficiently, pass `page` without speculative `headers`.

## Skill registration ownership and migration

The plugin manifest (`.codex-plugin/plugin.json` with `"skills": "./skills/"`) is the normal and only active owner of this skill. MCP worker startup reports manifest and legacy-path status but never creates, replaces, or removes anything under the global Codex skills directory.

For an existing installation, migrate in this order:

1. Confirm the installed plugin exposes `automate-hancom-documents` from its manifest. A missing global Junction is the expected state.
2. Inspect the old path without changing it: `Get-Item -LiteralPath (Join-Path $codexHome "skills\automate-hancom-documents") -Force | Select-Object LinkType, Target`, where `$codexHome` is the configured `CODEX_HOME` or the user `.codex` directory.
3. Remove that path only when it is a Junction and its resolved target is exactly this plugin's `skills\automate-hancom-documents` directory. Leave a real directory, an unreadable path, or a Junction to another installation untouched.
4. Reconnect the plugin and confirm the host exposes one skill entry.

From the installed plugin root, this PowerShell guard removes only the current plugin's own Junction and stops on every other path:

```powershell
$codexHome = if ($env:CODEX_HOME) { [IO.Path]::GetFullPath($env:CODEX_HOME) } else { Join-Path $env:USERPROFILE ".codex" }
$legacySkill = Join-Path $codexHome "skills\automate-hancom-documents"
$currentSkill = (Resolve-Path ".\skills\automate-hancom-documents").Path
$item = Get-Item -LiteralPath $legacySkill -Force
$junctionTarget = [IO.Path]::GetFullPath([string]$item.Target)
if ($item.LinkType -ne "Junction" -or $junctionTarget -ne $currentSkill) { throw "Refusing to remove a path not owned by this plugin" }
Remove-Item -LiteralPath $legacySkill
```

Only a legacy host that cannot discover manifest skills should install or repair the compatibility Junction explicitly with `uv run python .\skills\automate-hancom-documents\scripts\hwp_codex_skill_install.py`. The command accepts an absent path or the already-current Junction and refuses every other existing path. It is never called by MCP worker startup.

## Production action path

Match the user's request directly to one public action tool:

| User request | Tool | Distinction |
|---|---|---|
| Fill cells in an existing table | `hwp_fill_table` | Does not add rows; rows may start at the live selected cell/block when `start_cell` is omitted |
| Add rows and fill a table | `hwp_expand_and_fill_table` | Adds rows when needed |
| Repeat an empty table form | `hwp_repeat_table_template` | Does not fill each copy |
| Repeat a table and fill each copy | `hwp_build_table_series` | Accepts values and images per copy; advances a trailing caption number while inheriting its style |
| Sync preliminary viewpoints into visibility-analysis tables and numbered photos | `hwp_sync_visibility_analysis_tables` | Reads both tables by semantic headers, reconciles the complete series, and advances trailing caption numbers |
| Put images in table cells | `hwp_fill_table_images` | Keeps images inside a table |
| Format or resize an existing table | `hwp_format_table` | Omit `cell` to use the live current cell, selected contiguous cell block, or selected table; row/column sizes go down to 1mm |
| Merge table cells | `hwp_merge_table_cells` | Requires a rectangular range |
| Split one table cell | `hwp_split_table_cell` | Requires row/column counts and explicit `equal` or `existing_grid` semantics |
| Delete a physical page | `hwp_delete_page` | Verifies that page count decreases by one |
| Delete existing controls | `hwp_delete_control` | Uses one or more IDs returned by fast structure inspection |
| Undo a live edit | `hwp_undo` | Bounded to 1–20 explicit history steps |
| Redo a cancelled edit | `hwp_redo` | Bounded to 1–20 explicit history steps |
| Insert a new image | `hwp_insert_image` | Uses the selection or document end |
| Replace an existing image | `hwp_replace_image` | Uses the selected image or a returned target ID |
| Add or update a caption | `hwp_add_caption` | Uses the selection, a returned candidate, or a fast-inspection `instance_id` |
| Replace or delete user-selected text | `hwp_replace_selected_text` | Reads the dragged selection and original text internally; an empty replacement deletes it |
| Insert, replace, or format text without a selection | `hwp_patch_text` | Uses current cursor, an exact range, a unique find result, or a verified table cell and reads back only the changed range |
| Apply a document style | `hwp_apply_style` | Uses a style ID on the selection |
| Format selected text | `hwp_format_text` | Applies explicit text and paragraph properties |
| Analyze a reference image | `hwp_analyze_reference_image` | Returns a compact execution recommendation, verified coordinates, artifact paths, and a direct reference-layout draft when appropriate |
| Read one detailed analysis section | `hwp_get_reference_image_analysis_section` | Pages only the requested object, text, line, breakpoint, tile, or artifact section |
| Check layout fit before writing | `hwp_preflight_layout` | Uses the live section's paper and margins and returns overflow risk, problem blocks, and safe adjustment choices |
| Insert text, a table, or a native layout in the middle | `hwp_insert_layout` | `current` uses the cursor; `after_page` plus `page` creates the requested following page |
| Append a layout | `hwp_append_layout` | Adds content only at the document end |
| Check or recover a write result | `hwp_get_operation_status` | Uses the original `operation_id`; returns committed results again and reports in-progress, failed, aborted, stale, or missing operations |
| Analyze or verify an HWP page | `hwp_render_page` | Uses `CreatePageImage` without changing document state |
| Save, reopen, and verify | `hwp_save_reopen_verify` | Use only for an explicit persistence check |

1. For a write, choose its `operation_id`, then call the selected tool immediately. Do not precede it with capability, catalog, document-list, connect, or inspect calls. Read tools (`hwp_inspect`, `hwp_inspect_page_fast`, `hwp_inspect_structure`, `hwp_render_page`, `hwp_list_styles`) also auto-connect and require no session ID. Use `hwp_inspect_page_fast` only when an exact existing object ID or structural readback is required. Its `instance_id` goes directly in `target_id` for `hwp_add_caption`, `hwp_replace_image`, and `hwp_fill_table`; tools whose schema has a `target` parameter, including `hwp_format_table`, `hwp_merge_table_cells`, and `hwp_split_table_cell`, receive `target: {"target_id": instance_id}`. `hwp_delete_control` receives it in `control_instance_ids`.
   If that exact tool is documented here but absent from the current client's tool list, call `hwp_execute` once with the exact `tool_name` and the same argument object. This is only a stale-catalog forwarder; it does not search or reinterpret the request. If `hwp_execute` is also absent, reconnect the `gsg-hwp-beta-live` MCP connection in the current task and call `hwp_runtime_info`; do not create a new task.
2. The tool connects the active editable document, resolves the target, performs the certified action, and verifies the result in the same call.
   When HWP loses foreground focus, its caret stops blinking but the current-or-last HWP position remains the active target. Do not replace it with the Codex input caret. `hwp_inspect.active_target` distinguishes a caret, current table cell, selected text, selected cell block, selected table, and other selected controls.
   After `succeeded`, continue without a blanket re-inspection when the response already supplies `affected_pages`, `state_token`, `selected_target_id`, and `affected_target_ids`. Use those fields to carry the verified page and target scope into the next logical operation. `retry_operation_id` is only for recovering the same payload after a lost response; a new operation still needs a new `operation_id`.
3. A public table tool automatically selects exactly one candidate only when the caller's `page`, `caption`, `headers`, or `table_index` actually narrowed the match to that candidate. A lone global fallback that does not satisfy those filters is not auto-selected, and genuinely multiple candidates return `needs_target`. Call the same tool again with one returned opaque ID in the exact field reported by `required_inputs`: direct `target_id` or nested `target.target_id`. Preserve the original data format. If it returns `needs_input`, use `required_inputs` and `input_guidance`; when `format_candidates` is empty and `cells` is requested, provide the final display strings directly instead of waiting for another candidate list.
4. If the response is lost or the caller disconnects after dispatch, call `hwp_get_operation_status` with the original `operation_id` and document selector. Retry the write only with that exact ID and unchanged payload. Ask the user only when the target candidates are genuinely multiple or a required user value is missing. Do not automatically retry any other failure.
5. Do not call generic `hwp_operate` in production. It remains a QA-only diagnostic surface documented in `references/qa-operation-internals.md`.

## Route by task

| Task | Read |
|---|---|
| Connect, inspect cursor/selection/page, watch changes | `references/live-inspection.md` |
| Replace text or insert native paragraphs/tables/images | `references/native-editing.md` |
| Fill tables, repeat templates, place folder images, import PPTX/XLSX | `references/table-image-office.md` |
| Rebuild a screenshot or reference page as editable HWP content | `references/native-layout.md` |
| Find or verify an official Action, ParameterSet, method, property, or event | `references/official-api.md` |
| Validate results, handle partial failures, disconnect, or recover | `references/verification-recovery.md` |
| Inspect QA routing, generic operation, recipe, or replay internals | `references/qa-operation-internals.md` |
| Rebuild a document, apply a template, or assemble from PDF without a live session | `references/batch-workflows.md` |

## Official source of truth

For an explicit request with the intent of “공식 API 찾아봐”, “API 열어봐”, “API 확인”, “명세 확인”, or an equivalent expression, do not switch profiles. In production, call `hwp_search_tools` with the user's lookup text as `query` and `include_official_api=true`. This route reads only the packaged official catalog, returns at most five detailed matches, and applies only to that call; it stores no enabled state, so the next call is automatically off. Narrow a follow-up lookup by sending the returned exact identifier as `query` with the same per-call flag and a small `limit`.

For explicit catalog coverage analysis, use the same production gateway with `tool_name="hwp_get_official_api_coverage"` and `arguments={}`. In the QA profile the two tools remain directly callable. Both paths are generated from the official 2025-04 PDFs; do not substitute memory or inferred names.
