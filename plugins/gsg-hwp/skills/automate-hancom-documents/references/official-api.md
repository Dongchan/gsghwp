# Official API catalog

The runtime catalog is generated from the local official 2025-04 PDFs and packaged at:

`resources/hancom_official_api_catalog_v1.json`

For an explicit official API lookup in production, use `hwp_search_tools(query="...", include_official_api=true, limit=5)`. The optional `official_api` response includes `action`, `parameter_set`, and `automation` matches, declarations where available, and the exact source PDF and page. The per-call path limits official results to five and each result to 4 KiB.

Use `hwp_execute(tool_name="hwp_get_official_api_coverage", arguments={})` for a fast, mechanical split between native generic dispatch, exact ParameterSet mappings, unsupported value types, and catalog-only Automation members. These gateway calls are stateless and do not connect to HWP. In the QA profile the two underlying tools are also directly callable.

Coverage is static catalog/protocol coverage. It does not prove that every Action exists in the installed HWP version or can execute in the current cursor, selection, control, or document state.

The catalog keeps all 1,452 official cases searchable. Runtime batch execution routes 1,448 cases and intentionally excludes `action:0067:CharShapeTextColorGreen`, `action:0068:CharShapeTextColorRed`, `action:0365:MakeIndex`, and `action:0608:SaveHistoryItem`.

Use `hwp_get_capabilities` separately to learn what the current plugin exposes and whether each tool is C++/ATL-required, native-preferred, COM, ROT, or hybrid. Catalog presence does not mean a public mutation tool exists.

Rules:

- Search exact official identifiers before implementing a call.
- For an Action, inspect its referenced ParameterSet and item types.
- For an Automation member, use the documented declaration and account for return and out parameters.
- Do not route raw `Open`, `Save`, `SaveAs`, `Clear`, `Close`, or `Quit` members from natural language. Production exposes two bounded lifecycle operations: `document.save` executes `IHwpObject.Save` without closing or reopening and verifies text plus full HWP fingerprints; `document.save_reopen_verify` is the explicit diagnostic that additionally executes `Clear` and `Open`, verifies the same fingerprints, and restores a captured HWP block if reopening fails.
- Never substitute Windows UI automation or Python COM document access for an unavailable official operation.
- Never expose an undocumented ABI as an official Hancom API.
- Rebuild the catalog only with `tools/build_official_api_catalog.py` and preserve the recorded PDF hashes.
