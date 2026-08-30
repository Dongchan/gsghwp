# Native editing

In production, use the most specific public tool. `hwp_operate`, `hwp_replace_selection`, `hwp_apply_layout`, `hwp_update_table_cells`, and `hwp_insert_table_images` are QA-only internal surfaces.

## Text

Use `hwp_replace_selected_text(replacement=...)` to replace the complete text that the user currently dragged. Pass `replacement=""` to delete that selected text. The public tool reads the current endpoints and original text internally and the native bridge validates both immediately before mutation; do not ask the model to reproduce either value. Use `hwp_format_text` or `hwp_apply_style` for selection formatting. The separate `hwp_replace_selection` surface remains QA-only because it accepts low-level expected endpoints and original text explicitly.

For insertion without a text selection, `hwp_insert_layout(layout={"target": "current", ...})` uses the current-or-last HWP caret. This is the HWP caret even while focus is in Codex and the HWP caret is no longer blinking; it works both in body text and inside a table cell.

## Layout

Use `hwp_insert_layout` for editable paragraphs, tables, and individual images at a collapsed live cursor (`target=current`) or on a new page immediately after a numbered page (`target=after_page`, `page=N`). The `after_page` target brackets the inserted content with page breaks, so pass content blocks without adding another page break. Use `hwp_append_layout` only for the document end. QA-only `hwp_apply_layout` is reserved for inspected insertion-position diagnostics.

- Reuse styles returned by `hwp_list_styles`.
- For a table or picture caption, provide only the title text. The engine creates the native automatic number field and copies the document's caption paragraph formatting. When the requested caption style matches the nearest existing hierarchical table caption, a layout insertion also inherits that numbering format so the hierarchy continues; never include the number prefix in the title. Edit an existing caption with `hwp_add_caption`, which replaces the literal title and leaves the number field where it is; `hwp_patch_text` selects the rendered number as ordinary characters and the engine recomputes the field before the replacement lands.
- Initialize the table anchor and cells from the document's base style. Set left margin, right margin, and indentation explicitly to zero when inherited offsets are not intended.
- Define merges, borders, cell padding, alignment, and vertical alignment as native properties.
- A requested 1 mm row or column remains explicit through the MCP and native command path. For an actual 1 mm cell, also use 1 pt cell text, 50% line spacing, and 0 mm padding; otherwise HWP expands the cell to fit its content. Read back the table dimensions and report HWP's 283-HWPUNIT rounding (about 0.998 mm per equal row or column).
- `hwp_insert_image` and `hwp_replace_image` accept optional `width_mm` and `height_mm`. Omit both to leave size unspecified, or provide both as a bounding box; a one-sided size is rejected. Fitting preserves the image's original aspect ratio.
- `hwp_insert_image` also accepts `fit`. The default `contain` is that aspect-preserving fit, which leaves an empty margin on one axis when the picture and the box disagree. `fit=cover` fills the box instead and hides the overflowing edges with HWP's own picture crop (`ShapeDrawImageAttr` `SkipLeft`/`SkipTop`/`SkipRight`/`SkipBottom`), split evenly around the centre; it requires `width_mm` and `height_mm`. Never pre-crop a copy of the source file to fill a box: both modes read the image file and never rewrite it, and a cropped picture keeps its full original inside the document, so the crop stays adjustable in HWP. Whether to crop is the caller's decision, never the tool's. If the attached HWP build does not expose the picture's `OriginalSizeX`/`OriginalSizeY`, the bridge inserts the picture the `contain` way rather than failing, and the result message says the crop was not applied.

## Existing tables

Use `hwp_fill_table` for existing cell values and `hwp_fill_table_images` for pictures in existing cells. With `rows`, omit `start_cell` to use the live current table cell, the upper-left owner of a contiguous selected cell block, or A1 of a selected table. A row matrix that would leave a selected multi-cell block is rejected before mutation. The lower-level `hwp_update_table_cells` and `hwp_insert_table_images` tools are QA-only.

Every write is bounded but a multi-action HWP operation is not a database transaction. Verify the affected structure immediately after completion.

For an existing table, `hwp_format_table` can resize complete rows/columns and apply text, fill, border width/color/style, and vertical alignment. Omit `cell` to use the live current table cell, every cell in a contiguous selected block, or every cell in a selected table. Pass `row_height_mm` and `column_width_mm` in the 1–250mm range. Use an explicit `cell` selector only when the requested target is not the live HWP selection. Verify resulting dimensions with `hwp_inspect_page_fast(include_cells=true)`.
