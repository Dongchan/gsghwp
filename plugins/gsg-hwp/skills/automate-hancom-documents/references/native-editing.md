# Native editing

In production, use the most specific public tool. `hwp_operate`, `hwp_replace_selection`, `hwp_apply_layout`, `hwp_update_table_cells`, and `hwp_insert_table_images` are QA-only internal surfaces.

## Text

Use `hwp_replace_selected_text(replacement=...)` to replace the complete text that the user currently dragged. The public tool reads the current endpoints and original text internally and the native bridge validates both immediately before mutation; do not ask the model to reproduce either value. Use `hwp_format_text` or `hwp_apply_style` for selection formatting. The separate `hwp_replace_selection` surface remains QA-only because it accepts low-level expected endpoints and original text explicitly.

## Layout

Use `hwp_append_layout` for editable paragraphs, tables, page breaks, and individual images at the document end. QA-only `hwp_apply_layout` is reserved for inspected insertion-position diagnostics.

- Reuse styles returned by `hwp_list_styles`.
- For a table caption, provide only the title text. The engine creates the native automatic number field and copies the document's caption paragraph formatting.
- Initialize the table anchor and cells from the document's base style. Set left margin, right margin, and indentation explicitly to zero when inherited offsets are not intended.
- Define merges, borders, cell padding, alignment, and vertical alignment as native properties.
- A requested 1 mm row or column remains explicit through the MCP and native command path. For an actual 1 mm cell, also use 1 pt cell text, 50% line spacing, and 0 mm padding; otherwise HWP expands the cell to fit its content. Read back the table dimensions and report HWP's 283-HWPUNIT rounding (about 0.998 mm per equal row or column).
- Image width and height are bounding boxes. The inserted image preserves its original aspect ratio.

## Existing tables

Use `hwp_fill_table` for existing cell values and `hwp_fill_table_images` for pictures in existing cells. The lower-level `hwp_update_table_cells` and `hwp_insert_table_images` tools are QA-only.

Every write is bounded but a multi-action HWP operation is not a database transaction. Verify the affected structure immediately after completion.

For an existing table, `hwp_format_table` can resize the complete row and/or column containing `cell`. Pass `row_height_mm` and `column_width_mm` in the 1–250mm range. To resize non-adjacent rows or columns, call it once per row/column intersection; for example, use `B1` and `D3` to set rows 1 and 3 plus columns 2 and 4 in two calls. Verify the resulting `width_hwpunit`, `height_hwpunit`, `width_mm`, and `height_mm` with `hwp_inspect_page_fast(include_cells=true)`.
