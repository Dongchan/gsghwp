# G03 PagePlan compiler contract v1

G03 turns a validated G02 `PagePlan + SourceRegistry` into a deterministic,
non-mutating compiled PagePlan. It is the boundary before G04 native lowering.
Every accepted result carries canonical input, evidence, and result SHA-256 seals;
replays require the analyzer identity and canonical evidence to remain unchanged.
It never accepts preview HTML, SVG, DOM, CSS, OCR text, or generated pixels as
authoritative document content.

## Authority

| Decision | Authority |
| --- | --- |
| page count, blocks, boxes, and styles | `PagePlan` and G01 observation |
| text/table values and final image bytes | `SourceRegistry` original source records |
| generated/reference-image block-to-region relationship | host-recorded explicit mapping |
| visual evidence | clean reference image, only for layout analysis |
| HWP mutation | none; G03 is read/artifact only |

The compiler revalidates the PagePlan, registry, hashes, source references, style
roles, final-insertable assets, and G01 provenance at the trust boundary. A
rejected result always has `plan: null`.

## Branches

### Structured

A structured G02 plan already owns its layout. G03 verifies sources and body
boxes, records the G02 slot mapping, and returns the plan unchanged. It must
report:

```json
{
  "analysis_skipped": true,
  "image_analyzer_called": false,
  "snap_skipped": true
}
```

No reference image is required or accepted for this branch.

### Generated/reference image

The generated branch requires one clean image and positional guide metadata per
PagePlan page, plus an explicit host-recorded mapping for every non-page-break
block. The sequence is fixed:

1. verify the clean file and its SHA-256;
2. verify image dimensions, G01 body hash, and guide coordinates against the
   observed PageSetup/body geometry;
3. reject any mapped region that would intersect a guide strip;
4. write a separate coordinate-only guide mask, preserving the clean source;
5. call the existing `hwp_analyze_reference_image` implementation on the mask;
6. verify the deterministic analysis ID, version/source hash, optional expected
   analysis-result seal, and every mapped region box;
7. snap row/column breakpoints using the existing deterministic non-text policy;
8. convert breakpoints to HWP units using the observed body geometry and the
   minimum grid interval, then require <= 1 px round-trip error;
9. rebuild and validate the PagePlan and return the mapping, mask, analysis, and
   snap receipts.

The compiler never removes guides with a color threshold and never infers a
source or slot through OCR or pixel similarity. The clean image hash remains in
the receipt; the masked image is analysis-only.

## Request shape

The machine-readable request and response schemas are in
`gsg_hwp_pageplan_g03_v1.schema.json`. The public MCP surface has exactly one
new tool, `hwp_compile_page_plan`, with `category=layout`,
`operation=read`, `effect=artifact`, `execution_path=python_catalog`,
`requires_session=false`, and both production and QA profiles. The request is
an extra-forbid, frozen discriminated union on `branch`:

```text
structured:
  candidate, page_plan, source_registry, grounding

generated/reference_image:
  candidate, page_plan_template, source_registry, grounding,
  reference_pages (with optional expected analysis-result seals), mapping,
  optional artifact_root
```

`mapping.producer` is `host_recorded` and its canonical SHA-256 is checked
before analysis. `analysis_id`, when supplied, must load from the existing
analysis cache for the exact masked-image hash; a stale ID is rejected.

## Deterministic seals

The request is hashed from its canonical JSON representation. Analyzer evidence
is hashed from stable analysis content (identity, version, source hash, geometry,
regions, gaps, segments, breakpoints, and dimensions), excluding timing, cache
state, and artifact paths. The accepted response records
`canonical_input_sha256`, `canonical_evidence_sha256`, and
`deterministic_result_sha256`. A caller may replay a cached analysis by supplying
`analysis_id` and `expected_analysis_result_sha256`; a changed identity or seal
is rejected before a plan is accepted.

## Rejection rules

G03 fails closed for missing or mismatched G01 grounding, missing or changed
reference bytes, invalid guide metadata, guide/source collision, stale or
hash-mismatched analysis, missing/duplicate/out-of-plan mappings, slot/count
mismatch, boxes larger than the body, source/hash drift, non-final assets,
unsupported layout vocabulary, round-trip error over one pixel, and
response-budget overflow. A box that is outside the body but no larger than it
is deterministically fitted into the observed body before snapping; it is not
silently left outside. Representative stable error codes are:

```text
MISSING_G01_GROUNDING
REFERENCE_IMAGE_NOT_FOUND
REFERENCE_IMAGE_SHA256_MISMATCH
GUIDE_METADATA_MISMATCH
GUIDE_MASK_SOURCE_COLLISION
STALE_ANALYSIS_ID
ANALYSIS_SOURCE_HASH_MISMATCH
ANALYSIS_RESULT_SEAL_MISMATCH
BODY_BOX_OVERFLOW
BOX_EXCEEDS_BODY
SLOT_COUNT_MISMATCH
BLOCK_MAPPING_MISSING
BLOCK_MAPPING_DUPLICATE
SOURCE_HASH_MISMATCH
NON_FINAL_INSERTABLE_ASSET
ROUND_TRIP_ERROR_EXCEEDED
RESPONSE_BUDGET_EXCEEDED
```

No insert-render-adjust loop is introduced. HWP lowering, insertion, and
round-trip document verification remain G04 responsibilities.

## QA commands

```text
python -B -m pytest -q tests/test_hwp_pageplan_g03.py tests/test_hwp_reference_image_guide_mask.py
python -B -m pytest -q tests/test_hwp_pageplan_contract.py tests/test_hwp_pageplan_assets.py tests/test_hwp_pageplan_preview.py tests/test_hwp_pageplan_compiler_boundary.py tests/test_hwp_pageplan_real_source.py
python -B tools/sync_release_tool_catalogs.py check .
ruff check skills/automate-hancom-documents/scripts/hwp_pageplan_g03.py skills/automate-hancom-documents/scripts/hwp_pageplan_g03_contract.py skills/automate-hancom-documents/scripts/hwp_reference_image_guide_mask.py skills/automate-hancom-documents/scripts/hwp_mcp_pageplan_tools.py
basedpyright skills/automate-hancom-documents/scripts/hwp_pageplan_g03.py skills/automate-hancom-documents/scripts/hwp_pageplan_g03_contract.py skills/automate-hancom-documents/scripts/hwp_reference_image_guide_mask.py skills/automate-hancom-documents/scripts/hwp_mcp_pageplan_tools.py
```

The real-HWP acceptance check is read-only: ground a disposable copy, compare
body geometry/render hashes before and after, and report any margin-dependent
error in millimetres. G03 itself must not open, save, insert into, or terminate
an HWP document.
