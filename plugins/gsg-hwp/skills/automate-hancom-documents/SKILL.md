---
name: automate-hancom-documents
description: Route every Hancom/HWP document edit, analysis, and verification request to the GSG HWP (`gsg-hwp`) MCP. Never fall back to computer-use; report an unavailable or closed MCP connection and stop. Supports already-open HWP inspection and editing, native tables and captions, ratio-preserving images, PPTX/XLSX import, reference-layout rebuilding, and isolated batch workflows on Windows with Hancom Office.
---

# Hancom Document Automation

Start with the production action path. Read a reference only after a public action returns a concrete need for it.

## Non-negotiable rules

- Choose the most specific public action tool for the user's request. Never invent an API name, cell address, control ID, or hidden execution field.
- Live mode attaches only to the exact active, saved, editable HWP already registered in ROT. It does not open, save, clear, quit, or change history by default. Use `hwp_undo` or `hwp_redo` only for an explicit history request, and `hwp_save_reopen_verify` only for an explicit save-and-reopen verification request.
- Never use `computer-use`, Windows UI automation, keyboard/mouse control, or a Python COM document call as a fallback for an HWP operation. If the `gsg-hwp` MCP is unavailable, disconnected, or returns a closed transport, report that exact state and stop. If a certified native operation is unavailable, return the structured result from the selected HWP tool and stop.
- Do not hard-code a document title, page number, table size, or cell map in reusable code.
- When the user asks to analyze or verify an open HWP document, call `hwp_render_page`; it preserves document state and uses HWP `CreatePageImage`. Use fast or detailed inspection alongside the render when structural facts such as IDs, cell addresses, merges, or exact dimensions are also required.
- Document-local styles are read with `hwp_list_styles`; never use web search to infer a style ID or local title convention.
- Table-series tools copy the first table, regenerate each copied automatic-number control, and update only the literal title suffix while preserving the source caption style. Do not follow a successful series call with manual `hwp_add_caption` calls. If caption verification fails, report that failure instead of retrying captions or Undo in a loop.
- After a plugin update, stay in the current Codex task and call `hwp_runtime_info`. The stable MCP proxy reloads the worker automatically when its source hash changes and emits a tool-list change notification; call `hwp_reload` only to force a reload. Compare `mcp`, `worker_process_id`, `source_path`, and `tool_schema_hash` before continuing. If the refreshed direct tool is not yet visible, call it through `hwp_execute` with the exact tool name and arguments.
- The skill path displayed by the host may pass through a versioned Junction to the installation root. Read that exact displayed `SKILL.md` path directly; do not locate, search, or compare plugin cache and installation paths.
- For table data keyed by visible header labels, use `records`. Use `cells` only with verified cell addresses and `rows` only when the user supplied an explicit `start_cell`. A returned `target_id` changes only the target, never the chosen data format.
- Explicit 1 mm table rows and columns are supported. To make the physical cell reach that size, set every affected cell to `font_size_pt=1`, `line_spacing_percent=50`, and zero padding; larger inherited text or padding can make HWP expand the cell. HWP stores 1 mm as 283 HWPUNIT, so inspection reports about 0.998 mm per row or column.
- `headers` filters target tables; it is not a copy of the `records` keys. Use only labels known to be visibly present in the target table. If a page already narrows the request sufficiently, pass `page` without speculative `headers`.

## Production action path

Match the user's request directly to one public action tool:

| User request | Tool | Distinction |
|---|---|---|
| Fill cells in an existing table | `hwp_fill_table` | Does not add rows |
| Add rows and fill a table | `hwp_expand_and_fill_table` | Adds rows when needed |
| Repeat an empty table form | `hwp_repeat_table_template` | Does not fill each copy |
| Repeat a table and fill each copy | `hwp_build_table_series` | Accepts values and images per copy; advances a trailing caption number while inheriting its style |
| Sync preliminary viewpoints into visibility-analysis tables and numbered photos | `hwp_sync_visibility_analysis_tables` | Reads both tables by semantic headers, reconciles the complete series, and advances trailing caption numbers |
| Put images in table cells | `hwp_fill_table_images` | Keeps images inside a table |
| Format or resize an existing table | `hwp_format_table` | Applies cell formatting; `row_height_mm` resizes the cell's whole row and `column_width_mm` its whole column, down to 1mm |
| Merge table cells | `hwp_merge_table_cells` | Requires a rectangular range |
| Split one table cell | `hwp_split_table_cell` | Requires row and column counts |
| Delete a physical page | `hwp_delete_page` | Verifies that page count decreases by one |
| Delete existing controls | `hwp_delete_control` | Uses one or more IDs returned by fast structure inspection |
| Undo a live edit | `hwp_undo` | Bounded to 1–20 explicit history steps |
| Redo a cancelled edit | `hwp_redo` | Bounded to 1–20 explicit history steps |
| Insert a new image | `hwp_insert_image` | Uses the selection or document end |
| Replace an existing image | `hwp_replace_image` | Uses the selected image or a returned target ID |
| Add or update a caption | `hwp_add_caption` | Uses the selection, a returned candidate, or a fast-inspection `instance_id` |
| Replace user-selected text | `hwp_replace_selected_text` | Reads the current dragged selection and original text internally, then validates both natively |
| Apply a document style | `hwp_apply_style` | Uses a style ID on the selection |
| Format selected text | `hwp_format_text` | Applies explicit text and paragraph properties |
| Append a layout | `hwp_append_layout` | Adds content only at the document end |
| Analyze or verify an HWP page | `hwp_render_page` | Uses `CreatePageImage` without changing document state |
| Save, reopen, and verify | `hwp_save_reopen_verify` | Use only for an explicit persistence check |

1. Call the selected tool immediately. Do not precede it with capability, catalog, document-list, connect, or inspect calls. Read tools (`hwp_inspect`, `hwp_inspect_page_fast`, `hwp_inspect_structure`, `hwp_render_page`, `hwp_list_styles`) also auto-connect and require no session ID. Use `hwp_inspect_page_fast` only when an exact existing object ID or structural readback is required. Its `instance_id` goes directly in `target_id` for `hwp_add_caption`, `hwp_replace_image`, and `hwp_fill_table`; tools whose schema has a `target` parameter, including `hwp_format_table`, `hwp_merge_table_cells`, and `hwp_split_table_cell`, receive `target: {"target_id": instance_id}`. `hwp_delete_control` receives it in `control_instance_ids`.
   If that exact tool is documented here but absent from the current client's tool list, call `hwp_execute` once with the exact `tool_name` and the same argument object. This is only a stale-catalog forwarder; it does not search or reinterpret the request. If `hwp_execute` is also absent, reconnect the `gsg-hwp` MCP connection in the current task and call `hwp_runtime_info`; do not create a new task.
2. The tool connects the active editable document, resolves the target, performs the certified action, and verifies the result in the same call.
3. If the tool returns `needs_target`, call the same tool again with one returned opaque ID in the exact field reported by `required_inputs`: direct `target_id` or nested `target.target_id`. Preserve the original data format. If it returns `needs_input`, call the same tool again with only the missing user-provided value.
4. Ask the user only when the target candidates are genuinely multiple or a required user value is missing. Do not automatically retry any other failure.
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

In the QA profile, use `hwp_search_official_api` only for explicit API research or when `hwp_operate` returns an unresolved candidate. Use `hwp_get_official_api_coverage` only for coverage analysis. Both are generated from the official 2025-04 PDFs; do not substitute memory or inferred names.
