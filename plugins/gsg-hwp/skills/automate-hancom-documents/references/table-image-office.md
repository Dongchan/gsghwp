# Tables, images, and Office data

## Fill an existing table

Use `hwp_fill_table` with `records`, verified `cells`, or user-supplied `rows` plus `start_cell`. Use direct `target_id` for an inspected table ID or returned candidate.

When an Excel path supplies values for existing HWP tables, read the workbook once, inventory the document tables at most once, and issue one `hwp_fill_table` per target. Do not route through the append/import tools, search unrelated body text, or reformat by replacing every cell with the same value. Apply a uniform existing-text style with one target-wide `hwp_format_table` only when the user requested that style.

## Repeat a native template

Use `hwp_repeat_table_template` when one existing table must be copied and filled repeatedly.

- Identify the source by its inspected control identity, not by a fixed page.
- Provide each record's text and image mappings in source order.
- If the source has a caption, pass the title without its automatic number as `caption_title`.
- The engine copies the table control, restores its anchor format, recreates the caption immediately, and then fills the copy in one C++/ATL batch.
- Do not emulate repetition with page breaks plus clipboard paste.

## Folder images

Use `hwp_fill_table_images` for explicit image-to-cell mappings. Match each target through unique record and role keys and reject missing or ambiguous matches. `hwp_insert_folder_images` is QA-only.

## PPTX and XLSX

Use `hwp_append_excel_table` to append an editable table from XLSX while preserving supported merges, fills, borders, row heights, and column ratios. It does not copy a screenshot. Call it once without a guessed `cell_range`; empty-row and empty-column gutters become separate tables, and a remaining wide merged table splits only at header groups that do not cut a merge.

`hwp_sync_visibility_analysis_tables` is the one-shot path from a 예비조망점 선정표 to the matching 가시권 분석표 series. Missing photo folders leave the tables filled and report the photo gap; they do not abort the sync.

`hwp_import_office_table` and `hwp_propagate_table_cells` are QA-only internal tools for guarded existing-table diagnostics; do not recommend them as production actions.
