# Official API catalog

The runtime catalog is generated from the local official 2025-04 PDFs and packaged at:

`resources/hancom_official_api_catalog_v1.json`

Use `hwp_search_official_api(query, category, limit)` to search it. Categories are `action`, `parameter_set`, and `automation`. Results include the exact source PDF and page.

Use `hwp_get_official_api_coverage` for a fast, mechanical split between native generic dispatch, exact ParameterSet mappings, unsupported value types, and catalog-only Automation members. It does not connect to HWP.

Coverage is static catalog/protocol coverage. It does not prove that every Action exists in the installed HWP version or can execute in the current cursor, selection, control, or document state.

The catalog keeps all 1,452 official cases searchable. Runtime batch execution routes 1,448 cases and intentionally excludes `action:0067:CharShapeTextColorGreen`, `action:0068:CharShapeTextColorRed`, `action:0365:MakeIndex`, and `action:0608:SaveHistoryItem`.

Use `hwp_get_capabilities` separately to learn what the current plugin exposes and whether each tool is C++/ATL-required, native-preferred, COM, ROT, or hybrid. Catalog presence does not mean a public mutation tool exists.

Rules:

- Search exact official identifiers before implementing a call.
- For an Action, inspect its referenced ParameterSet and item types.
- For an Automation member, use the documented declaration and account for return and out parameters.
- Do not route raw `Open`, `Save`, `SaveAs`, `Clear`, `Close`, or `Quit` members from natural language. The only production live lifecycle operation is the certified `document.save_reopen_verify`, and it is available only when the user explicitly requests it. It executes documented `IHwpObject.Save`, `Clear`, and `Open` calls inside one protocol-9 C++/ATL command and rechecks the same path and structure.
- Never substitute Windows UI automation or Python COM document access for an unavailable official operation.
- Never expose an undocumented ABI as an official Hancom API.
- Rebuild the catalog only with `tools/build_official_api_catalog.py` and preserve the recorded PDF hashes.
