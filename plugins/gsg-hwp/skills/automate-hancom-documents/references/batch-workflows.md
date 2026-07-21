# Isolated batch workflows

Batch mode is separate from live mode. It creates a new output and never overwrites the source.

## Existing template

Use `hwp_automation.py apply template.hwp data.json output.hwp`. Follow `data-schema.md`. Native field counts and repeated value counts must match before mutation.

## Exact document rebuild

Use `hwp_rebuild_document(source, output)` to reproduce an HWP/HWPX as a new HWP. The output must not exist. The tool uses isolated Automation instances, preserves the source, reopens the saved output, and accepts it only when page count, full text, embedded bytes, and semantic HWPX structure match after normalizing verified Hancom save canonicalization.

## No template

Only after confirming no HWP/HWPX form exists, use `pdf_hybrid.py analyze` and then `hwp_automation.py assemble --confirm-no-template`. Follow `hybrid-strategy.md`.

## Validation

Use `hwp_automation.py verify` against the new output. Reject an existing output path, missing assets, invalid manifests, page-count drift, or geometry mismatch.

General batch commands must not start while a conflicting HWP/HwpApi process is active. `hwp_rebuild_document` is the isolated exception and may close only the Automation instances it created.
