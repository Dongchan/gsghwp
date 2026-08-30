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

The batch object exposes protocol version 14. `Snapshot` reads the active
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
`SaveVerify` is the normal save path. It saves the current document in-process
without `Clear`, `Open`, closing a tab, or replacing the active document. It
re-reads the page/control signature, plain-text hash, and full HWP serialization
hash, so body text, table content, and character formatting must still match
before it reports success. `SaveReopenVerify` is a separate explicit diagnostic.
It performs the save/discard/reopen round trip, compares the same fingerprints,
and restores the captured HWP document block when reopening fails.

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

Protocol 10 adds `ReferenceLayoutBulk` inside the same `ExecuteActions`
mutation engine. It receives compressed breakpoints, merges, visible edges,
style regions, and text anchors; applies `TableCreation.RowHeight/ColWidth`
arrays once; clears all borders once; uses the official multi-cell zone
actions for regions and visible edges; performs all merges against one stable
topology; and rebuilds and verifies the final topology once.

Protocol 11 adds atomic `PATCH_TEXT` targeting the current cursor/selection,
an exact native range, an unambiguous search result, or a table cell. The
replacement range remains selected for formatting and operation-specific
readback.

Protocol 12 separates non-destructive `SaveVerify` from the explicit
`SaveReopenVerify` diagnostic and adds full text/document fingerprints plus
failed-reopen session recovery.

`INSERT_PICTURE` accepts an optional crop after the millimetre box:
`INSERT_PICTURE<TAB>path<TAB>width<TAB>height<TAB>left<TAB>top<TAB>right<TAB>bottom`.
The four edges are fractions of the source image in `[0, 1)`, and opposite edges
must add up to less than 1. They are unitless on the wire because HWP's own crop
items (`ShapeDrawImageAttr` `SkipLeft`/`SkipTop`/`SkipRight`/`SkipBottom`,
`ParameterSetTable_2504.pdf` p41-42) are expressed in the same space as that
set's `OriginalSizeX`/`OriginalSizeY`, which only the bridge can read from the
inserted control at runtime. The bridge applies the crop first and then sizes the
object to what is still visible, so HWP does not stretch the remainder. The image
file is only read; no cropped copy is written and the picture keeps its full
original inside the document, so the crop stays adjustable in HWP. If this HWP
build does not return `OriginalSizeX`/`OriginalSizeY`, the crop is skipped and the
picture is inserted exactly as it was before crop existed, rather than refused.

Excel/PPT planning stays outside HWP, but the resulting table or report is sent
to HWP as one ATL call. This keeps source parsing replaceable while all document
mutation remains in-process. By default the bridge never closes, restarts, or
opens another copy of the user's active document. `SaveVerify` saves in place;
only an explicit `SaveReopenVerify` call performs the save/discard/reopen
lifecycle described above.

Protocol 12 introduced the inspection and history-safety members.
`ContentSignature()` returns the same whole-document `SIG ...` record used by
checkpoint verification, without writing a checkpoint file.

Protocol 13 extends every complete `SIG` with the normalized HWPML2X document
hash and length. Those two fields cover formatting-only edits that leave page,
control, and plain-text observations unchanged. History and checked checkpoint
restore callers require protocol 13 and fail closed for an older bridge.

Protocol 14 adds the additive `ExecuteProtocolBundle` member while preserving
every existing DISPID and wire format. Its `HLB1` receipt correlates
observation, checked mutation, undo/redo history, save/reopen, native page
rendering, capability diagnostics, document routing, before/after signatures,
rollback evidence, and native error stages under caller-provided request and
operation IDs. Existing HCS1/HCA2/HCH1/HLS1/HCL12/HCV1 responses are carried
unchanged inside the bundle receipt so their focused decoders remain
authoritative.

`ExecuteHistory(direction, expected_signature)` and
`ExecuteActionsChecked(payload, expected_signature)` are fail-closed mutation
members. They verify the routed active document and compare a complete live
content signature inside the same native `Invoke` immediately before Undo,
Redo, or checkpoint replacement. Updated history callers do not fall back when
these additive members are absent; the matching DLL is required for mutation.

`ContentRevision()` is an additive freshness token beside `ContentSignature()`,
not a replacement for it. It answers `REV <pages> <controls> <control_hash>
<text_hash> <text_length> <session_tag> <write_epoch>`, or an empty string when
any component could not be observed. `session_tag` is the bridge process ID and
`write_epoch` counts writes that entered this bridge's `Invoke`.

It exists because a signature costs what is embedded in the document, not what
is in it. Measured on a 34-page 82MB document: `ContentSignature()` 6236ms, of
which `GetTextFile("HWPML2X")` alone is 6020ms over 111,079,555 characters;
`ContentRevision()` reads the page count, the control chain, and 19,819
characters of plain text instead. Use it only to date a document. Anything that
compares two documents -- checkpoint restore, `ExecuteHistory`,
`ExecuteActionsChecked`, rollback evidence -- still requires a complete `SIG`,
and a caller that cannot get a usable `REV` must fall back to recomputing, never
to reusing.

**What the token cannot see.** Against a complete `SIG` it drops four fields
(`text_empty`, `text_presence`, `document_hash`, `document_length`), of which
only `document_hash` -- the normalized HWPML of the whole document -- carries
independent discriminating power, and with it every change that hash was
covering and these observations are not:

- character, paragraph and table formatting, including cell borders and shading
- control geometry and ordering
- style definitions
- page setup that does not change the page count

(An image replaced in place is not on this list: `CaptureHwpmlHash` normalizes
BINDATA content and its `Size` attribute away, so a pure image-byte swap was
not covered by `SIG` either -- whether surrounding HWPML attributes change is
unverified.)

`write_epoch` covers those changes *only when they arrive as a member of this
interface*. It does not cover a user's own hand editing, and it does not cover
the product's direct Hangul-automation paths: `HAction.Execute` and
`HAction.Run` are called straight from Python (`hwp_live_table_format.py:52`,
`hwp_page_setup.py:123`, `hwp_live_caption_edit.py:21`, among others) and never
reach `BatchAutomation::Invoke`, so a table border applied that way moves
nothing in the token. Widening the counter to cover those paths is separate
work. Until then: if missing a formatting change would be wrong, read the
signature.

**A document with no text never gets a token.** `text_length == 0` is reported
as incomplete rather than as an empty document, because `GetTextFile` answers
`S_OK` with an empty string when it cannot serialise and the scan that
distinguishes the two is the cost this token exists to avoid. Such a document
therefore stays permanently on the caller's cold path. That is the intended
fail-closed behaviour, not an oversight.

**Across workers.** The token cache is per worker process; `write_epoch` is per
Hangul process. A second worker writing through the bridge does move the epoch,
so the first worker sees a changed token on its next read. A worker that edits
without calling this bridge does not, and neither does a person typing -- both
reach the first worker only through the engine change event, which depends on
HancomEventBridge being alive.

`InspectParagraphStyles` keeps the `HPS1` envelope and accepts detail level 3.
Level 3 extends each `PSTYLE` record from 17 to 26 fields with a verified
paragraph-tail sample, total text length, an end-reached flag, and the Latin,
Hanja, Japanese, other, symbol, and user font slots. Detail levels 0-2 retain
their original field counts. Old callers request at most level 2, and old
bridges clamp a level-3 request to the level they support and echo that level.

## Ribbon slot actions and the status page (0.5.173)

`IHncUserActionModule::EnumAction` advertises the three lifecycle actions it
always did, followed by a fixed pool of 32 slot action IDs
(`Bridge.cpp`, `kSlotActions`). The MCP worker builds one ribbon button per slot,
so a click identifies which recipe was pressed. The slot AIDs are one generated
GUID family whose last four hex digits are the slot index; the Python mirror is
`hwp_custom_action_store.CUSTOM_ACTION_SLOT_AIDS` and a test parses this source
to prove the two lists are identical.

`DoAction` on a slot AID does not publish to the ROT and makes no COM call. It
writes `{sequence, slotIndex, tick}` into a 16-entry ring in the shared status
page and sets `Local\HancomLiveBridgeSlotClick.<PID>`, then returns — the Hangul
UI thread is never held while a recipe runs.

`UpdateUI` on a slot AID returns `*state = 0` while the worker's heartbeat is
fresh and `*state = 1` once it goes stale, so the buttons grey out when no worker
is there to consume clicks. The bit meaning is **not measured** — there is no
Hancom header for this interface here — but the guess is fail-safe: `0` is the
value the three lifecycle actions have always returned, and a wrong disabled bit
only leaves a button that looks live and does nothing, which is what it did
before.

`BridgeStatus` therefore moves to **version 2**, 384 bytes. Every version-1
field keeps its byte offset (`doActionCount` at 32, `lastAction` at 60, 124 bytes
total); the slot ring, the heartbeat, and the last UI decision are appended after
it, and `static_assert`s in `BridgeStatus.h` hold that contract. A version-1
reader that maps the first 124 bytes still reads exactly what it read before.
Ticks are 32-bit `GetTickCount` values on purpose: a 64-bit tick would tear when
this 32-bit DLL reads what a 64-bit worker wrote, while unsigned wrap-around
subtraction stays correct across the 49.7-day rollover.

### Measured: Hangul drives this module through `DoAction`, not `UpdateUI`

Read-only sampling of a live Hangul process (0.5.172, no MCP ribbon buttons
present) over six seconds: `queryCount=3`, `enumCount=12`, **`updateUiCount=0`**,
`doActionCount=6`, `lastAction=kOnLoad`. Two consequences.

- The republish-on-`UpdateUI` branch has **never run** in a real Hangul process.
  The ROT publisher that actually works is `DoAction(kOnInitialLoad|kOnLoad)`,
  and Stage 2 leaves that branch byte-identical. Dropping the bootstrap AID from
  the ribbon pool therefore removes no recovery path that was ever executing.
  Adding COM to the slot `UpdateUI` branch to "restore" it would be strictly
  worse: it would run per button, per idle pass, on the UI thread.
- The same measurement means **slot greying is unproven in Hangul**. The DLL
  answers `UpdateUI` correctly (harness: enabled/stale/cleared/future-dated all
  verified), but whether Hangul polls `UpdateUI` for ribbon buttons at all has
  not been observed — that process had no buttons. If it does not, a dead
  worker leaves buttons that look live and do nothing, which is the pre-Stage-2
  behavior and not a regression.

Build with Visual Studio C++ for `Release|Win32`. The output uses the static C
runtime and has no MFC dependency:

```powershell
msbuild HancomLiveBridgeNative.vcxproj /p:Configuration=Release /p:Platform=Win32
```

In HWP 2024, register `bin\Release\HancomLiveBridge.dll` through
`도구 > COM 추가 기능 설정`. Administrator privileges are not required.
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
