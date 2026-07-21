# Rebuild a reference as native HWP content

Treat a screenshot, PPT export, or reference PDF as visual evidence, not as a page-sized image to paste.

Read this guidance before constructing the layout. It is also exposed verbatim as the UTF-8 MCP resource `gsg-hwp-beta://reference/native-layout`, so a client that did not receive the plugin skill catalog does not need to search the filesystem or improvise from the tool schema.

1. Inspect the HWP page size, content width, styles, anchor paragraph, and nearby native tables.
2. Separate editable structure from raster content. Photos, maps, and drawings remain individual images; headings, text, boxes, separators, arrows, and organization-chart connectors remain native.
3. Derive an adaptive grid from the reference's distinct horizontal and vertical breakpoints: box edges, text anchors, gaps, and connector endpoints. Merge near-identical breakpoints; do not create a uniform pixel grid or copy coordinates from one specific image into reusable code.
4. Build that grid with a native table using `border_mode="explicit"`. Up to 50 rows and 50 columns are available, and a narrow spacer or routing segment may be 1 mm. For a physical 1 mm cell, use 1 pt text, 50% line spacing, and zero padding.
5. Start from absent borders. In explicit mode every omitted edge is emitted as `none`, while a declared shared edge is applied consistently to both adjacent cells. Declare only outlines and connector segments visible in the source.
6. Use fill only when the source contains a filled color surface. A connector is a cell border, never a gray or colored filled cell. A blank spacing cell has no fill and no visible border.
7. Merge cells for text boxes, cards, headings, and panels only after grid breakpoints include every required line endpoint. Do not merge across a connector endpoint because one merged-cell edge cannot represent only part of a line.
8. Rebuild titles, body text, bullets, captions, and tables as native paragraphs and cells, reusing the current document's styles instead of imitating fonts manually.
9. Inspect the resulting table dimensions, merges, and cell properties, then render the page with `hwp_render_page` (`CreatePageImage`). Compare positive features and negative space: an extra border, filled connector, clipped text, shifted box, overflow, or distorted raster asset is a failed verification.

Use `hwp_insert_layout` with `target=after_page` and `page=N` when the rebuilt page belongs after an existing page. For an interior page, the tool inserts at the next page's start and adds one trailing page break; after the last page it adds one leading page break. This creates exactly one isolated page and does not leave a blank page. Use `target=current` for insertion at the user's collapsed cursor, and use `hwp_append_layout` only for the document end. Keep `auto_fit_row_heights=false` when the reference provides deliberate vertical geometry; automatic fitting may expand a compact grid across additional pages.

When two adjacent cells explicitly request different styles for the same shared border, keep the merge and cell structure and omit only those contradictory shared-edge overrides. The current document's table style then supplies that edge; unrelated outer borders and cell formatting remain explicit.

That conflict-inheritance rule applies to ordinary tables (`border_mode="inherit"`). Reference-layout grids use `border_mode="explicit"`: a visible edge wins over an omitted or explicit no-border neighbor and is mirrored to the other side, while all remaining edges are cleared. If two visible styles conflict, the upper or left cell's edge is canonical; avoid such ambiguity by declaring each source edge once.

Do not invent a figure caption or table title that is absent from the source or the target form.
