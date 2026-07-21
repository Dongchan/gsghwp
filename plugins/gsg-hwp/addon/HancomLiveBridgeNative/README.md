# Hancom Live Bridge (native)

This Win32 UserAction DLL publishes the `IHwpObject` supplied by the running
Hancom HWP process as `!HancomLiveBridge.<PID>` and an ATL-backed batch
`IDispatch` object as `!HancomLiveBatch.<PID>`. It does not open or copy the
active document.

The module enumerates the two HWP lifecycle actions plus a private bootstrap
action. Any lifecycle or bootstrap callback with a live `IHwpObject` publishes
the bridge. Callback counts, the last publication HRESULT, and ROT cookies are
mirrored in `Local\\HancomLiveBridgeStatus.<PID>` so startup failures can be
diagnosed without UI automation, temporary XML, or touching the document.

The batch object exposes protocol version 9. `Snapshot` reads the active
document, cursor, selection text, current control/cell, paragraph style, and
character/paragraph formatting in one in-process call. `InspectPage` preserves
the protocol-v2 response for older clients. `InspectPageV3` reads a requested
physical page directly and returns its text, controls, tables, cell addresses,
spans, text, and each table anchor's style and paragraph formatting while
restoring the user's cursor and selection. `InspectPageSummary` returns the
same page/control/anchor metadata without walking table cells, so cursor/page
targeting stays fast even when a page contains large tables.
`InspectRoutingContext` resolves a supplied page hint or the current page and
returns that cell-free summary in one native call for each `hwp_operate` request.
`InspectPagesV3` accepts a bounded comma-separated page list, preserves the
cursor and selection once, walks `HeadCtrl` once, and returns all requested
page inspections as one HPM1 response. Long table-series work therefore scales
with document controls plus requested tables instead of multiplying a whole-
document control walk by every candidate page.
`SaveReopenVerify` is the sole explicit lifecycle exception: it saves the current
document in-process, confirms that `IsModified` cleared, discards the in-memory
document, reopens the same path with `lock:FALSE`, and returns protocol-9
before/after structural evidence.

`InspectStructure` uses only the official in-process Automation API to return
page-spanning tables, cell ranges and merges, nested pictures/tables, caption
text, automatic-number presence, and caption style identity. Python only
decodes this HDS1 response into the MCP structure contract; it does not inspect
the document through COM or HWPML/XML.

`ExecuteActions` accepts one HCA1 batch. It checks DocumentID, full path,
expected cursor, expected selection, and all image paths before writing. The
batch supports direct physical-page movement, exact list/paragraph/character
movement, control selection by instance ID, arbitrary HAction `Run`, arbitrary
HAction/HParameterSet execution with nested values and arrays, IHwpObject
method calls, text, table creation/geometry/merge/style/caption, and
ratio-preserving picture insertion. Cell pictures use zero cell and paragraph
margins and are contained in the requested box without stretching.

The three generic records cover bounded portions of the official automation
surface: `RUN` dispatches any ActionTable action name, `ACTION` executes exact
ActionTable/ParameterSetTable pairs with supported scalar, nested, and array
values, and `CALL` invokes HwpAutomation methods with supported input values and
returns typed Void, Boolean, Integer, or Text results. Out/dispatch arguments,
properties, events, binary values, and PDF
wildcard or alias ParameterSet tokens are cataloged but are not generic runtime
operations. `hwp_get_official_api_coverage` reports these boundaries from the
packaged official catalog. Frequently repeated template work has verified native compound
commands: `COPY_CONTROL` selects and copies an existing control by instance
ID, `PASTE_TABLE` pastes an exact table clone and captures its new structure,
and `CAPTURE_TABLE` binds the currently selected or entered table for following
`CELL`, `MERGE`, picture, caption, and formatting commands. These commands
use the official in-memory HWP block path (`GetTextFile` with `saveblock:true`,
then `SetTextFile` with `insertfile`) instead of Windows clipboard format
negotiation or the modal generic `Paste` action. The created control is selected
and verified by exact instance ID. `COPY_CONTROL` also captures the source table
anchor's native style and paragraph formatting. `PASTE_TABLE` verifies and, only
when needed, corrects the new anchor in-process; `APPLY_COPIED_TABLE_ANCHOR`
repairs an existing clone without relying on a document name, page number, or
style name.

Protocol 9 adds bounded document checkpoints for logical MCP undo/redo around
control and page deletion. Documents whose on-disk size makes an encoded
checkpoint unsafe automatically use grouped native HWP history instead. The
client runs native undo/redo one step at a time and stops as soon as the saved
page/control signature is restored, so one MCP undo reverses one MCP deletion
batch without allocating a full-document BSTR checkpoint.

Excel/PPT planning stays outside HWP, but the resulting table or report is sent
to HWP as one ATL call. This keeps source parsing replaceable while all document
mutation remains in-process. By default the bridge never saves, closes, restarts,
or opens another copy of the user's active document; only an explicit
`SaveReopenVerify` call performs the save/discard/reopen lifecycle described above.

Build with Visual Studio C++ for `Release|Win32`. The output uses the static C
runtime and has no MFC dependency:

```powershell
msbuild HancomLiveBridgeNative.vcxproj /p:Configuration=Release /p:Platform=Win32
```

In HWP 2024, register `bin\Release\HancomLiveBridge.dll` through
`도구 > COM 추가 기능 설정`. Elevated privileges are not required.
Restart HWP once only when replacing the loaded DLL.

The Win32 smoke executable can verify a running HWP process from the same
bitness as the add-in:

```powershell
smoke\bin\Release\BridgeSmoke.exe --probe <HWP-PID>
```

`CreateProcessWithTokenW` limits its child command line to 1,024 characters.
Do not pass generated Python batches through a long `-c` argument. Redirect the
script file to the child's standard input instead:

```powershell
smoke\bin\Release\BridgeSmoke.exe --run-as-target-stdin `
  <HWP-PID> <output-path> <script-path> <python.exe> -B -
```

The existing `--run-as-target` mode remains available for short commands.
