# Rebuild a reference as native HWP content

Treat a screenshot, PPT export, or reference PDF as visual evidence, not as a page-sized image to paste.

1. Inspect the HWP page size, content width, styles, anchor paragraph, and nearby native tables.
2. Separate editable structure from raster content.
3. Rebuild titles, body text, bullets, captions, and tables as native paragraphs and tables.
4. Insert only photos, maps, drawings, and other inherently raster assets as individual images.
5. Reuse the current document's paragraph, caption, and cell styles. Do not imitate the reference font manually.
6. Use native merged cells as layout grids when the reference has cards, two-column sections, or comparison panels.
7. Verify structure first. Render only to check clipping, overflow, spacing, and image distortion.

When two adjacent cells explicitly request different styles for the same shared border, keep the merge and cell structure and omit only those contradictory shared-edge overrides. The current document's table style then supplies that edge; unrelated outer borders and cell formatting remain explicit.

Do not invent a figure caption or table title that is absent from the source or the target form.
