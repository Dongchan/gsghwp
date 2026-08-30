#pragma once

#include <oaidl.h>

#include <cstdint>
#include <iosfwd>
#include <string>
#include <vector>

namespace hancom::official_api {

struct DocumentState {
    LONG pageCount = -1;
    LONG modified = -1;
    LONG list = -1;
    LONG paragraph = -1;
    LONG character = -1;
    LONG controlCount = -1;
    std::uint64_t controlHash = 0;
};

struct DocumentSectionFingerprint {
    std::uint64_t hash = 0;
};

struct DocumentFingerprint {
    DocumentState state;
    bool captured = false;
    // Whether the HWPML2X half answered. An engine that cannot serialize a
    // document this large returns S_OK with an empty string, which costs the
    // full serialization attempt and yields nothing, so a caller taking two
    // captures of one document can read this from the first to decide whether
    // the second should pay that price again.
    bool documentCaptured = false;
    std::uint64_t textHash = 0;
    std::uint64_t documentHash = 0;
    std::uint64_t textLength = 0;
    std::uint64_t documentLength = 0;
    std::vector<DocumentSectionFingerprint> documentSections;
};

enum class DocumentTextPresence : std::uint64_t {
    Unknown = 0,
    Absent = 1,
    Present = 2,
};

// Reproducible content-and-format signature of the whole document.
//
// Just as deliberately, it carries no caret (list/paragraph/character) and no
// `modified` flag. Both move when the engine merely drops a selection or when
// the caret is parked somewhere else, neither of which is a change to the
// document. Including them would make two captures of an untouched document
// compare unequal, which is exactly the mistake documented at
// hwp_live_native_history.py:15-21.
struct DocumentContentSignature {
    bool captured = false;
    LONG pageCount = -1;
    LONG controlCount = -1;
    std::uint64_t controlHash = 0;
    std::uint64_t textHash = 0;
    std::uint64_t textLength = 0;
    bool textEmpty = false;
    DocumentTextPresence textPresence = DocumentTextPresence::Unknown;
    std::uint64_t documentHash = 0;
    std::uint64_t documentLength = 0;
};

// Cheap freshness token, additive beside DocumentContentSignature.
//
// It answers one question only: has this document changed since the token was
// last read. It is not a content signature and must never be used where one is
// required (checkpoint restore, undo/redo preconditions, rollback evidence) --
// those compare documents, and this compares observations.
//
// What it costs is the point. A signature serialises the whole document through
// GetTextFile("HWPML2X") -- measured at 6.02s and 111,079,555 characters for a
// 34-page 82MB document, because every embedded image travels through it as
// base64. This token reads the page count, walks the control chain, and takes
// the plain text (14ms, 19,819 characters for the same document). Every
// component is O(1) or O(document text), never O(embedded bytes).
//
// What it gives up against a signature is exactly one field: `documentHash`,
// the normalized HWPML of the whole document. So what it cannot see is what
// that hash was covering and these observations are not -- character, paragraph
// and table formatting including borders and shading; control geometry and
// ordering; an image replaced in place; style definitions; page setup that does
// not move the page count.
//
// `writeEpoch` covers those only for writes that arrive through this bridge's
// Invoke. It does not cover a user's own hand editing, and it does not cover
// the product's direct `HAction.Execute` / `HAction.Run` paths, which drive
// Hangul's automation from Python without ever reaching this bridge (see the
// comment on gWriteEpoch in BatchAutomation.cpp). A caller using this for cache
// freshness therefore also drops it on the engine's own change event, and a
// caller that must not miss a formatting change needs the signature instead.
struct DocumentContentRevision {
    bool captured = false;
    LONG pageCount = -1;
    LONG controlCount = -1;
    std::uint64_t controlHash = 0;
    std::uint64_t textHash = 0;
    std::uint64_t textLength = 0;
    // Filled by the bridge, not by the capture: which bridge process answered,
    // and how many writes it had performed when it did. The process tag keeps
    // two runs from colliding on an identical-looking document.
    std::uint64_t sessionTag = 0;
    std::uint64_t writeEpoch = 0;
};

// Read-only structure around the paragraph at the current caret.
//
// A negative value means the official API did not expose that observation.
// `nextSection*` remains negative when the control scan succeeded but found no
// later section; `sectionStartsInParagraph >= 0` distinguishes that from an
// unavailable scan.
struct DocumentHeadStructure {
    LONG pageBreakBefore = -1;
    LONG sectionStartsInParagraph = -1;
    LONG nextSectionList = -1;
    LONG nextSectionParagraph = -1;
    LONG nextSectionCharacter = -1;
};

DocumentState CaptureDocumentState(IDispatch* hwp) noexcept;
// Pass false for captureDocumentHash to skip the HWPML2X half outright. The
// fingerprint then reports documentCaptured == false and captured == false,
// which is exactly what a failed attempt reports, with the same zeroed hash,
// length and sections -- so the answer is unchanged and only the cost is gone.
DocumentFingerprint CaptureDocumentFingerprint(
    IDispatch* hwp,
    bool captureDocumentHash = true) noexcept;
DocumentContentSignature CaptureDocumentContentSignature(IDispatch* hwp) noexcept;
std::wstring FormatDocumentContentSignature(
    const DocumentContentSignature& signature);
bool DocumentContentSignatureIsComplete(const std::wstring& raw);
DocumentContentRevision CaptureDocumentContentRevision(IDispatch* hwp) noexcept;
std::wstring FormatDocumentContentRevision(
    const DocumentContentRevision& revision);
bool DocumentContentRevisionIsComplete(const std::wstring& raw);
DocumentHeadStructure CaptureDocumentHeadStructure(IDispatch* hwp) noexcept;
void AppendDocumentState(std::wostream& output, const DocumentState& state);

}
