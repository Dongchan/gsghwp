#include "ActionExecutorInternal.h"

#include "OfficialApiState.h"

#include <algorithm>
#include <cstdint>
#include <cwctype>
#include <filesystem>
#include <limits>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

namespace hancom::actions::detail {

using hancom::official_api::CaptureDocumentContentSignature;
using hancom::official_api::CaptureDocumentHeadStructure;
using hancom::official_api::DocumentContentSignature;
using hancom::official_api::DocumentHeadStructure;
using hancom::official_api::DocumentTextPresence;

constexpr std::uintmax_t kMaximumBlockFileBytes = 256ULL * 1024ULL * 1024ULL;
// V1 is the legacy layout: magic line, then the encoded block.
// V2 adds one content-signature line between the two, so a restore can prove
// with document content -- not just a page count -- that it reproduced the
// checkpoint. V1 files stay readable; they simply carry no signature.
constexpr char kEncodedBlockMagic[] = "GSG_HWP_ENCODED_BLOCK_V1\n";
constexpr char kSignedBlockMagic[] = "GSG_HWP_ENCODED_BLOCK_V2\n";
// The preferred layout: the checkpoint file is the document itself, written by
// SaveAs. It carries no header of ours, so the few bytes of metadata a restore
// needs live in a sidecar file next to it. A checkpoint therefore says what it
// is by its own first bytes: the encoded block starts with one of the magics
// above, anything else is a document file.
constexpr char kDocumentFileMetaMagic[] = "GSG_HWP_DOCUMENT_FILE_V1\n";
constexpr wchar_t kDocumentFileMetaSuffix[] = L".gsgmeta";
constexpr wchar_t kDocumentFileRollbackSuffix[] = L".rollback";
constexpr std::uintmax_t kMaximumMetaFileBytes = 4096ULL;
constexpr wchar_t kDefaultDocumentFormat[] = L"HWP";
// SaveAs options, in the documented "key:value;key:value;..." syntax.
//
// lock says nothing about which document the editing session owns. The official
// SaveAs option table (HwpAutomation, IHwpObject::SaveAs) defines it as whether
// the saved file is kept open and locked afterwards, defaulting to TRUE. It is
// passed as false so this checkpoint file is not left locked by the engine, and
// that is all it is expected to do: whether SaveAs moves the editing session
// onto the copy is not something any documented option promises either way.
// What keeps the session on the user's document is the check after the call in
// CaptureDocumentFileCheckpoint, which reads the active document path back and
// puts it right.
//
// The previews are dropped because a checkpoint is never shown in a file dialog
// and the preview image is the largest avoidable part of a picture-heavy save.
constexpr wchar_t kCheckpointSaveArguments[] =
    L"lock:false;backup:false;prvimage:0;prvtext:0";
constexpr wchar_t kRestoreEvidenceMethod[] = L"DocumentCheckpointRestore";
constexpr wchar_t kRestoreEvidenceSignature[] = L"content_signature_match";
constexpr wchar_t kRestoreEvidenceStructuralSignature[] =
    L"structural_signature_match_text_empty";
constexpr wchar_t kRestoreEvidenceDocumentFileNormalized[] =
    L"document_file_reopen_normalized";
constexpr wchar_t kRestoreEvidencePageCount[] = L"page_count_only";
// What the document-file restore had to do before the checkpoint came back.
//
// These travel under their own method name, so a reader that does not know them
// simply never iterates them -- the same arrangement the capture reasons above
// use. They exist because the two defects they report on are indistinguishable
// from their symptom: a restore that is refused for a page count one too high
// looks identical whether one leftover paragraph was added or whether the
// checkpoint's sections were poured into one. The next live run says which,
// instead of it being reasoned about from a page number again.
constexpr wchar_t kRestoreInsertMethod[] = L"DocumentCheckpointRestoreInsert";
constexpr wchar_t kRestoreInsertResidueTrimmedTail[] = L"insert_residue_trimmed_tail";
constexpr wchar_t kRestoreInsertResidueTrimmedHead[] = L"insert_residue_trimmed_head";
constexpr wchar_t kRestoreInsertLeadingSectionRemoved[] =
    L"insert_leading_section_residue_removed";
constexpr wchar_t kRestoreInsertEngineUndoVerified[] =
    L"engine_undo_verified";
constexpr wchar_t kRestoreInsertSectionsFlattened[] = L"sections_flattened";
constexpr wchar_t kRestoreInsertSectionsKept[] = L"sections_kept";
constexpr wchar_t kRestoreAttemptMethod[] = L"DocumentRestoreAttempt";
constexpr wchar_t kRestoreAttemptEncodedBlock[] = L"encoded_block";
constexpr wchar_t kRestoreAttemptEngineUndo[] = L"engine_undo";
constexpr wchar_t kRestoreAttemptReopen[] = L"document_file_reopen";
constexpr wchar_t kRestoreAttemptReopenNoClear[] =
    L"document_file_reopen_no_clear";
constexpr wchar_t kRestoreAttemptFlatten[] = L"flatten";
constexpr wchar_t kRestoreAttemptKeep[] = L"keep";
constexpr wchar_t kRestoreBodyAfterEmptyingMethod[] =
    L"DocumentRestoreBodyAfterEmptying";

// The same observation in words, for the failure message.
//
// A restore that fails reports nothing else: FailureResponse carries an error
// and no call results, so every piece of evidence recorded above is thrown away
// on exactly the path where it was worth having. A live run that finally proved
// where the leftover paragraph lands could say nothing about the trim that went
// looking for it, and the answer had to be inferred from a page number again.
constexpr wchar_t kResidueNotReached[] = L"not reached";
constexpr wchar_t kResidueTrimmedTail[] =
    L"trimmed paragraph breaks between insert caret and document end";
constexpr wchar_t kResidueTrimmedHead[] =
    L"trimmed isolated leading section and its empty paragraph";
constexpr wchar_t kResidueTailHasContent[] =
    L"text between the insert caret and document end, left alone";
// Read nothing where the position check just proved there is something. That is
// a failed read, never an empty stretch of document: GetTextFile answers S_OK
// with an empty string when it cannot serialise a selection, which is the same
// answer CaptureDocumentBlock in this file already refuses to trust. Deleting on
// it would delete a selection nobody has seen.
constexpr wchar_t kResidueTailUnread[] =
    L"range between the insert caret and document end could not be read, left alone";
constexpr wchar_t kResidueHeadHasContent[] =
    L"first paragraph holds text, left alone";
constexpr wchar_t kResidueHeadUnread[] =
    L"first paragraph could not be read, left alone";
constexpr wchar_t kResidueHeadUnbounded[] = L"first paragraph could not be bounded";
constexpr wchar_t kResidueHeadSectionUnproven[] =
    L"first empty paragraph is not a proved isolated leading section, left alone";
// A page or section break is content. It is called out separately from ordinary
// text because deleting one removes exactly one page -- which is the amount the
// page-count gate is looking to remove, so it would turn a document that lost a
// break into a restore that passes.
constexpr wchar_t kResidueBreakInText[] = L"page or section break, left alone";
constexpr wchar_t kResiduePageCountAlreadyMatches[] =
    L"not looked for: page count already matches";
constexpr wchar_t kResiduePageCountBelow[] =
    L"not looked for: page count is below the checkpoint";
// Deleting inside the area the checkpoint occupies is only safe while something
// can catch a wrong deletion. The content signature is that something.
constexpr wchar_t kResidueHeadNeedsSignature[] =
    L"not looked for: checkpoint carries no content signature";
constexpr wchar_t kCaptureEvidenceMethod[] = L"DocumentCheckpointCapture";
constexpr wchar_t kCaptureEvidenceDocumentFile[] = L"document_file";
constexpr wchar_t kCaptureEvidenceEncodedBlock[] = L"encoded_block";
constexpr wchar_t kCaptureEvidenceIdentityRestored[] = L"document_identity_restored";
// Why the on-disk capture gave up without an error.
//
// Every Unavailable return below used to be silent, and the fallback that runs
// next can succeed, so a document whose copy the engine refused to save
// produced a perfectly ordinary answer with no trace of the refusal in it.
// These are what the caller reads instead of guessing.
//
// They travel under their own method name so an older reader is unaffected:
// the Python side filters call results by method, and one it does not know is
// simply not iterated.
// What the read-only block probe reports. Its own method names, so a reader
// that does not know them never iterates them and protocol 12 is unchanged.
// The three page counts every restore attempt measures, reported whether the
// attempt succeeded or failed.
//
// They used to ride only on the failure message, so a restore that worked said
// nothing at all -- and four documents that restore correctly could not be
// compared against the one that does not. The question they answer is the one
// that is now open: a 292MB single-section document and a 2.7MB three-section
// document both restore exactly, a 331MB three-section one grows by a page, and
// neither size nor section count explains it. Numbers from the working restores
// are the missing half of that table.
constexpr wchar_t kRestorePagesEmptied[] = L"DocumentRestorePagesAfterEmptying";
constexpr wchar_t kRestorePagesInserted[] = L"DocumentRestorePagesAfterInsert";
constexpr wchar_t kRestorePagesTrimmed[] = L"DocumentRestorePagesAfterTrim";
constexpr wchar_t kBlockProbeMethod[] = L"DocumentBlockProbe";
constexpr wchar_t kBlockProbeLength[] = L"DocumentBlockProbeLength";
constexpr wchar_t kBlockProbeWritten[] = L"DocumentBlockProbeWritten";
constexpr wchar_t kBlockProbeCaptured[] = L"captured";
constexpr wchar_t kBlockProbeAllocationFailed[] = L"allocation_failed";
constexpr wchar_t kBlockProbeWriteFailed[] = L"write_failed";
constexpr wchar_t kBlockProbeNonAscii[] = L"non_ascii";
constexpr wchar_t kCaptureUnavailableMethod[] = L"DocumentCheckpointUnavailable";
constexpr wchar_t kCaptureUnavailableNoPath[] = L"document_path_unknown";
constexpr wchar_t kCaptureUnavailableSaveFailed[] = L"save_as_failed";
constexpr wchar_t kCaptureUnavailableSaveRefused[] = L"save_as_refused";
constexpr wchar_t kCaptureUnavailableEmptyFile[] = L"checkpoint_file_empty";
constexpr wchar_t kCaptureUnavailableMetaFailed[] = L"checkpoint_meta_write_failed";

enum class CheckpointCaptureOutcome {
    // The checkpoint file is on disk and describes the current document.
    Written,
    // Nothing was written and the document was left exactly as it was found,
    // so the caller is free to try another way of capturing it.
    Unavailable,
    // The failure is already recorded on the result and must not be retried.
    Failed,
};

enum class CheckpointLayout { EncodedBlock, DocumentFile };

// The two ways the engine can be asked to pour a checkpoint file back into the
// document it is a copy of, and the only thing that differs between them is
// InsertFile's KeepSection.
//
// Flatten drops the incoming document's own section structure into the one
// section the emptied document still has. Keep lets the incoming document bring
// its sections -- and therefore its page layout -- with it.
//
// Neither is right for every checkpoint, which is why a restore now tries both
// rather than shipping a guess. See ReplaceDocumentWithFile for the order and
// for what each one is known to get wrong.
enum class SectionFidelity { Flatten, Keep };

// Rendered as one ASCII line so the checkpoint file can carry it. The final
// markers record that the text component was empty and whether an independent
// whole-document scan saw body text. Readers accept both legacy forms.
std::wstring FormatContentSignature(const DocumentContentSignature& signature) {
    if (!signature.captured) {
        return std::wstring();
    }
    return L"SIG " + std::to_wstring(signature.pageCount) + L' ' +
        std::to_wstring(signature.controlCount) + L' ' +
        std::to_wstring(signature.controlHash) + L' ' +
        std::to_wstring(signature.textHash) + L' ' +
        std::to_wstring(signature.textLength) + L' ' +
        std::to_wstring(signature.textEmpty ? 1 : 0) + L' ' +
        std::to_wstring(static_cast<std::uint64_t>(signature.textPresence)) + L' ' +
        std::to_wstring(signature.documentHash) + L' ' +
        std::to_wstring(signature.documentLength);
}

std::wstring CaptureContentSignature(Context* const context) {
    return FormatContentSignature(CaptureDocumentContentSignature(context->hwp));
}

struct ParsedContentSignature {
    LONG pageCount = -1;
    LONG controlCount = -1;
    std::uint64_t controlHash = 0;
    std::uint64_t textHash = 0;
    std::uint64_t textLength = 0;
    bool textEmpty = false;
    bool textPresenceRecorded = false;
    DocumentTextPresence textPresence = DocumentTextPresence::Unknown;
    bool documentFingerprintRecorded = false;
    std::uint64_t documentHash = 0;
    std::uint64_t documentLength = 0;
};

bool ParseContentSignature(
    const std::wstring& raw,
    ParsedContentSignature* const parsed) {
    // Formatted components are separated by whitespace. Reject a minus sign
    // at a field boundary before extraction because formatted extraction into
    // uint64_t otherwise accepts "-1" by wrapping it to the maximum value.
    for (std::wstring::size_type index = 1; index < raw.size(); ++index) {
        if (raw[index] == L'-' &&
            std::iswspace(raw[index - 1]) != 0) {
            return false;
        }
    }
    std::wistringstream input(raw);
    std::wstring prefix;
    if (!(input >> prefix >> parsed->pageCount >> parsed->controlCount >>
          parsed->controlHash >> parsed->textHash >> parsed->textLength) ||
        prefix != L"SIG" ||
        parsed->pageCount < 1 ||
        parsed->controlCount < 0) {
        return false;
    }
    std::uint64_t emptyMarker = 0;
    if (input >> emptyMarker) {
        if (emptyMarker > 1) {
            return false;
        }
        parsed->textEmpty = emptyMarker == 1;
    } else {
        if (!input.eof()) {
            return false;
        }
        input.clear();
        parsed->textEmpty = parsed->textLength == 0;
    }
    std::uint64_t presenceMarker = 0;
    if (input >> presenceMarker) {
        if (presenceMarker >
            static_cast<std::uint64_t>(DocumentTextPresence::Present)) {
            return false;
        }
        parsed->textPresenceRecorded = true;
        parsed->textPresence =
            static_cast<DocumentTextPresence>(presenceMarker);
    } else {
        if (!input.eof()) {
            return false;
        }
        input.clear();
    }
    if (input >> parsed->documentHash) {
        if (!(input >> parsed->documentLength)) {
            return false;
        }
        parsed->documentFingerprintRecorded = true;
    } else {
        if (!input.eof()) {
            return false;
        }
        input.clear();
    }
    std::wstring trailing;
    return !(input >> trailing);
}

bool ContentSignatureIsComplete(const std::wstring& raw) {
    ParsedContentSignature parsed;
    if (!ParseContentSignature(raw, &parsed) ||
        !parsed.textPresenceRecorded ||
        !parsed.documentFingerprintRecorded ||
        parsed.documentLength == 0) {
        return false;
    }
    switch (parsed.textPresence) {
    case DocumentTextPresence::Unknown:
        return false;
    case DocumentTextPresence::Absent:
        return parsed.textEmpty && parsed.textLength == 0;
    case DocumentTextPresence::Present:
        return !parsed.textEmpty && parsed.textLength > 0;
    }
    return false;
}

bool ContentSignaturesMatch(
    const std::wstring& expected,
    const std::wstring& actual,
    bool* const textCompared,
    const bool ignoreDocumentFingerprint = false) {
    *textCompared = false;
    ParsedContentSignature expectedParsed;
    ParsedContentSignature actualParsed;
    if (!ParseContentSignature(expected, &expectedParsed) ||
        !ParseContentSignature(actual, &actualParsed)) {
        *textCompared = expected == actual;
        return expected == actual;
    }
    if (expectedParsed.pageCount != actualParsed.pageCount ||
        expectedParsed.controlCount != actualParsed.controlCount ||
        expectedParsed.controlHash != actualParsed.controlHash) {
        return false;
    }
    if (!ignoreDocumentFingerprint &&
        (expectedParsed.documentFingerprintRecorded !=
             actualParsed.documentFingerprintRecorded ||
         (expectedParsed.documentFingerprintRecorded &&
          (expectedParsed.documentHash != actualParsed.documentHash ||
           expectedParsed.documentLength != actualParsed.documentLength)))) {
        return false;
    }
    if (!expectedParsed.textPresenceRecorded ||
        !actualParsed.textPresenceRecorded) {
        if (actualParsed.textLength == 0) {
            return expectedParsed.textLength == 0;
        }
        if (expectedParsed.textLength == 0) {
            return true;
        }
    } else {
        if (expectedParsed.textPresence != actualParsed.textPresence) {
            return false;
        }
        switch (expectedParsed.textPresence) {
        case DocumentTextPresence::Unknown:
            return false;
        case DocumentTextPresence::Absent:
            return true;
        case DocumentTextPresence::Present:
            if (expectedParsed.textEmpty || actualParsed.textEmpty) {
                return false;
            }
            break;
        }
    }
    *textCompared = true;
    return expectedParsed.textHash == actualParsed.textHash &&
        expectedParsed.textLength == actualParsed.textLength;
}

void RecordCheckpointEvidence(
    ExecutionResult* const result,
    const wchar_t* const method,
    const wchar_t* const evidence) {
    CallResult captured;
    captured.method = method;
    captured.value = std::wstring(evidence);
    result->callResults.push_back(std::move(captured));
}

void RecordRestoreEvidence(
    ExecutionResult* const result,
    const wchar_t* const evidence) {
    RecordCheckpointEvidence(result, kRestoreEvidenceMethod, evidence);
}

void RecordBlockProbeCount(
    ExecutionResult* const result,
    const wchar_t* const method,
    const std::uint64_t value) {
    CallResult counted;
    counted.method = method;
    counted.value = value;
    result->callResults.push_back(std::move(counted));
}

void RecordObservedPageCount(
    ExecutionResult* const result,
    const wchar_t* const method,
    const LONG pageCount) {
    if (pageCount < 0) {
        return;
    }
    RecordBlockProbeCount(result, method, static_cast<std::uint64_t>(pageCount));
}

void RecordInsertEvidence(
    ExecutionResult* const result,
    const wchar_t* const evidence) {
    RecordCheckpointEvidence(result, kRestoreInsertMethod, evidence);
}

void RecordBodyAfterEmptying(
    ExecutionResult* const result,
    const Position& position) {
    CallResult captured;
    captured.method = kRestoreBodyAfterEmptyingMethod;
    captured.value =
        L"empty at list " + std::to_wstring(position.list) +
        L" paragraph " + std::to_wstring(position.paragraph);
    result->callResults.push_back(std::move(captured));
}

void AppendRestoreObservation(
    ExecutionResult* const result,
    const wchar_t* const label,
    const std::wstring& observation) {
    bool needsSeparateEntry = true;
    for (auto entry = result->callResults.rbegin();
         entry != result->callResults.rend();
         ++entry) {
        if (entry->method == kRestoreBodyAfterEmptyingMethod) {
            if (auto* const text = std::get_if<std::wstring>(&entry->value)) {
                *text += L"; " + std::wstring(label) + L"=" + observation;
                return;
            }
            // Preserve the unexpected typed value and carry the observation in
            // a second text result under the same compatible method name.
            needsSeparateEntry = true;
            break;
        }
    }
    if (needsSeparateEntry) {
        const std::wstring separate =
            std::wstring(label) + L"=" + observation;
        RecordCheckpointEvidence(
            result, kRestoreBodyAfterEmptyingMethod, separate.c_str());
    }
}

std::wstring CheckpointMetaPath(const std::wstring& pathText) {
    return pathText + kDocumentFileMetaSuffix;
}

bool CreateCheckpointRollbackPath(
    const std::wstring& pathText,
    std::wstring* const rollbackPath) {
    GUID identifier{};
    if (FAILED(CoCreateGuid(&identifier))) {
        rollbackPath->clear();
        return false;
    }
    wchar_t encoded[39]{};
    const int length = StringFromGUID2(
        identifier,
        encoded,
        static_cast<int>(sizeof(encoded) / sizeof(encoded[0])));
    if (length < 3) {
        rollbackPath->clear();
        return false;
    }
    const std::wstring token(encoded + 1, static_cast<size_t>(length - 3));
    *rollbackPath =
        pathText + L"." + token + kDocumentFileRollbackSuffix;
    return true;
}

void RemoveCheckpointFiles(const std::wstring& pathText) {
    static_cast<void>(DeleteFileW(pathText.c_str()));
    static_cast<void>(DeleteFileW(CheckpointMetaPath(pathText).c_str()));
}

// A checkpoint written for the duration of one restore and deleted however that
// restore ends, including the failure paths that return early.
class CheckpointScratchFile {
public:
    explicit CheckpointScratchFile(std::wstring pathText)
        : pathText_(std::move(pathText)) {}
    CheckpointScratchFile(const CheckpointScratchFile&) = delete;
    CheckpointScratchFile& operator=(const CheckpointScratchFile&) = delete;
    ~CheckpointScratchFile() {
        if (!kept_) {
            RemoveCheckpointFiles(pathText_);
        }
    }

    const std::wstring& Path() const { return pathText_; }

    // Leaves the copy on disk instead of deleting it. Used on exactly one path:
    // the restore failed and so did the rollback, so this copy is the only
    // faithful record left of the document the call was asked to change. It is
    // named in the failure message; deleting it there would be deleting the
    // user's only way back.
    void Keep() noexcept { kept_ = true; }

private:
    std::wstring pathText_;
    bool kept_ = false;
};

bool CheckpointFileHasContent(const std::wstring& pathText) {
    std::error_code error;
    const std::uintmax_t size =
        std::filesystem::file_size(std::filesystem::path(pathText), error);
    return !error && size > 0;
}

bool SameDocumentPath(const std::wstring& left, const std::wstring& right) {
    if (left.empty() || right.empty()) {
        return false;
    }
    const std::wstring normalizedLeft =
        std::filesystem::path(left).lexically_normal().wstring();
    const std::wstring normalizedRight =
        std::filesystem::path(right).lexically_normal().wstring();
    return CompareStringOrdinal(
               normalizedLeft.c_str(),
               static_cast<int>(normalizedLeft.size()),
               normalizedRight.c_str(),
               static_cast<int>(normalizedRight.size()),
               TRUE) == CSTR_EQUAL;
}

// The format the document itself uses. A checkpoint has to round trip through
// the same filter or the copy is no longer the document.
std::wstring CheckpointDocumentFormat(const std::wstring& pathText) {
    const size_t separator = pathText.find_last_of(L"\\/");
    const size_t dot = pathText.find_last_of(L'.');
    if (dot != std::wstring::npos &&
        (separator == std::wstring::npos || dot > separator)) {
        const std::wstring extension = pathText.substr(dot);
        if (CompareStringOrdinal(
                extension.c_str(),
                static_cast<int>(extension.size()),
                L".hwpx",
                5,
                TRUE) == CSTR_EQUAL) {
            return L"HWPX";
        }
    }
    return kDefaultDocumentFormat;
}

HRESULT ReadActiveDocumentPath(IDispatch* const hwp, std::wstring* const pathText) {
    CComVariant rawDocuments;
    HRESULT status = PropertyGet(hwp, L"XHwpDocuments", &rawDocuments);
    CComPtr<IDispatch> documents;
    if (SUCCEEDED(status)) {
        status = AsDispatch(rawDocuments, documents);
    }
    CComVariant rawDocument;
    CComPtr<IDispatch> document;
    if (SUCCEEDED(status)) {
        status = PropertyGet(documents, L"Active_XHwpDocument", &rawDocument);
    }
    if (SUCCEEDED(status)) {
        status = AsDispatch(rawDocument, document);
    }
    CComVariant raw;
    if (SUCCEEDED(status)) {
        status = PropertyGet(document, L"FullName", &raw);
    }
    if (SUCCEEDED(status)) {
        status = AsString(raw, pathText);
    }
    return status;
}

bool ReadTail(
    Context* const context,
    const Position& start,
    std::wstring* const selected) {
    if (!SetPosition(context->hwp, start, context->result, L"DELETE_TAIL") ||
        !RunAction(context->action, L"MoveSelDocEnd", context->result, L"DELETE_TAIL")) {
        return false;
    }
    CComVariant raw;
    HRESULT status = Method(
        context->hwp,
        L"GetTextFile",
        {CComVariant(L"UNICODE"), CComVariant(L"saveblock:true")},
        &raw);
    if (SUCCEEDED(status)) {
        status = AsString(raw, selected);
    }
    if (FAILED(status)) {
        return SetError(context->result, L"DELETE_TAIL", L"", FormatHResult(L"GetTextFile", status));
    }
    return true;
}

bool DeleteTail(Context* const context, const Command& command) {
    const Position start{command.list, command.paragraph, command.character};
    std::wstring selected;
    if (!ReadTail(context, start, &selected)) {
        return false;
    }
    if (selected.rfind(command.first, 0) != 0) {
        static_cast<void>(RunAction(context->action, L"Cancel", context->result, L"DELETE_TAIL"));
        return SetError(context->result, L"STALE_TAIL", L"", L"tail prefix does not match the request");
    }
    return RunAction(context->action, L"Delete", context->result, L"DELETE_TAIL");
}

bool RollbackAppendTail(Context* const context, const Position& start) {
    ExecutionResult rollbackResult;
    Context rollback;
    rollback.hwp = context->hwp;
    rollback.action = context->action;
    rollback.result = &rollbackResult;
    static_cast<void>(RunAction(
        rollback.action,
        L"Cancel",
        rollback.result,
        L"ROLLBACK"));
    bool controlsDeleted = true;
    for (auto control = context->result->createdControlIds.rbegin();
         control != context->result->createdControlIds.rend();
         ++control) {
        if (!DeleteControl(&rollback, *control)) {
            controlsDeleted = false;
        }
    }
    std::wstring expected;
    if (!ReadTail(&rollback, start, &expected)) {
        return false;
    }
    Command command;
    command.kind = CommandKind::DeleteTail;
    command.list = start.list;
    command.paragraph = start.paragraph;
    command.character = start.character;
    command.first = std::move(expected);
    return DeleteTail(&rollback, command) && controlsDeleted;
}

bool CaptureCallResult(
    ExecutionResult* const result,
    const std::wstring& method,
    const CComVariant& raw) {
    CComVariant value;
    const HRESULT status = VariantCopyInd(&value, &raw);
    if (FAILED(status)) {
        return SetError(result, L"CALL_RETURN", method, FormatHResult(method.c_str(), status));
    }
    CallResult captured;
    captured.method = method;
    switch (value.vt) {
    case VT_EMPTY:
    case VT_NULL:
        captured.value = std::monostate{};
        break;
    case VT_BOOL:
        captured.value = value.boolVal != VARIANT_FALSE;
        break;
    case VT_I1:
        captured.value = static_cast<std::int64_t>(value.cVal);
        break;
    case VT_I2:
        captured.value = static_cast<std::int64_t>(value.iVal);
        break;
    case VT_I4:
        captured.value = static_cast<std::int64_t>(value.lVal);
        break;
    case VT_INT:
        captured.value = static_cast<std::int64_t>(value.intVal);
        break;
    case VT_I8:
        captured.value = static_cast<std::int64_t>(value.llVal);
        break;
    case VT_UI1:
        captured.value = static_cast<std::uint64_t>(value.bVal);
        break;
    case VT_UI2:
        captured.value = static_cast<std::uint64_t>(value.uiVal);
        break;
    case VT_UI4:
        captured.value = static_cast<std::uint64_t>(value.ulVal);
        break;
    case VT_UINT:
        captured.value = static_cast<std::uint64_t>(value.uintVal);
        break;
    case VT_UI8:
        captured.value = static_cast<std::uint64_t>(value.ullVal);
        break;
    case VT_BSTR:
        captured.value = value.bstrVal == nullptr
            ? std::wstring()
            : std::wstring(value.bstrVal, SysStringLen(value.bstrVal));
        break;
    default:
        return SetError(
            result,
            L"CALL_RETURN_TYPE",
            method,
            L"Automation method returned an unsupported VARIANT type " +
                std::to_wstring(value.vt));
    }
    result->callResults.push_back(std::move(captured));
    return true;
}

bool ExecuteCall(Context* const context, const Command& command) {
    std::vector<CComVariant> arguments;
    arguments.reserve(command.arguments.size());
    for (const Value& value : command.arguments) {
        CComVariant converted;
        if (!ConvertValue(
                context->hwp,
                value,
                &converted,
                context->result,
                command.name)) {
            return false;
        }
        arguments.push_back(converted);
    }
    CComVariant returned;
    const HRESULT status = Method(
        context->hwp,
        command.name.c_str(),
        arguments,
        &returned);
    if (FAILED(status)) {
        return SetError(context->result, L"COM_METHOD", command.name, FormatHResult(command.name.c_str(), status));
    }
    return CaptureCallResult(context->result, command.name, returned);
}

// Writes bytes to a file that must not exist yet. `created` reports whether the
// file itself could be created, so a caller can tell "the path was taken" from
// "the write failed" without this having to know either error code.
bool WriteExclusiveCheckpointFile(
    const std::wstring& pathText,
    const std::vector<BYTE>& bytes,
    bool* const created) {
    *created = false;
    const HANDLE output = CreateFileW(
        pathText.c_str(),
        GENERIC_WRITE,
        0,
        nullptr,
        CREATE_NEW,
        FILE_ATTRIBUTE_TEMPORARY,
        nullptr);
    if (output == INVALID_HANDLE_VALUE) {
        return false;
    }
    *created = true;
    DWORD written = 0;
    const BOOL writeSucceeded = WriteFile(
        output,
        bytes.data(),
        static_cast<DWORD>(bytes.size()),
        &written,
        nullptr);
    const BOOL closeSucceeded = CloseHandle(output);
    if (!writeSucceeded || written != bytes.size() || !closeSucceeded) {
        static_cast<void>(DeleteFileW(pathText.c_str()));
        return false;
    }
    return true;
}

bool SaveEncodedBlockFile(
    Context* const context,
    const std::wstring& pathText,
    const std::wstring& encodedBlock,
    const std::wstring& contentSignature) {
    const bool signed_ = !contentSignature.empty();
    const char* const magic = signed_ ? kSignedBlockMagic : kEncodedBlockMagic;
    const size_t magicLength = std::char_traits<char>::length(
        signed_ ? kSignedBlockMagic : kEncodedBlockMagic);
    const size_t headerLength =
        magicLength + (signed_ ? contentSignature.size() + 1 : 0);
    if (encodedBlock.empty() || encodedBlock.size() > kMaximumBlockFileBytes - headerLength) {
        return SetError(context->result, L"BLOCK_FILE_SIZE", pathText, L"encoded HWP block text is empty or exceeds 256 MiB");
    }
    std::vector<BYTE> bytes;
    bytes.reserve(headerLength + encodedBlock.size());
    bytes.insert(bytes.end(), magic, magic + magicLength);
    if (signed_) {
        for (const wchar_t character : contentSignature) {
            bytes.push_back(static_cast<BYTE>(character));
        }
        bytes.push_back(static_cast<BYTE>('\n'));
    }
    for (const wchar_t character : encodedBlock) {
        if (character > 0x7f) {
            return SetError(context->result, L"BLOCK_FILE_ENCODING", pathText, L"encoded HWP block text contains a non-ASCII character");
        }
        bytes.push_back(static_cast<BYTE>(character));
    }
    bool created = false;
    if (!WriteExclusiveCheckpointFile(pathText, bytes, &created)) {
        return created
            ? SetError(context->result, L"BLOCK_FILE_WRITE", pathText, L"encoded HWP block could not be written completely")
            : SetError(context->result, L"BLOCK_FILE_CREATE", pathText, L"block output file could not be created exclusively");
    }
    return true;
}

// The sidecar that carries what a document-file checkpoint cannot carry itself:
// the format its filter needs and the content signature a restore is verified
// against. Missing or unreadable means "unknown", never "mismatch".
bool WriteCheckpointMeta(
    const std::wstring& pathText,
    const std::wstring& documentFormat,
    const std::wstring& contentSignature) {
    std::string text(kDocumentFileMetaMagic);
    text += "FMT ";
    for (const wchar_t character : documentFormat) {
        if (character <= 0 || character > 0x7f) {
            return false;
        }
        text.push_back(static_cast<char>(character));
    }
    text.push_back('\n');
    if (!contentSignature.empty()) {
        text += "SIG ";
        for (const wchar_t character : contentSignature) {
            if (character <= 0 || character > 0x7f) {
                return false;
            }
            text.push_back(static_cast<char>(character));
        }
        text.push_back('\n');
    }
    std::vector<BYTE> bytes;
    bytes.reserve(text.size());
    for (const char character : text) {
        bytes.push_back(static_cast<BYTE>(character));
    }
    bool created = false;
    return WriteExclusiveCheckpointFile(
        CheckpointMetaPath(pathText),
        bytes,
        &created);
}

std::string ReadSmallCheckpointFile(const std::wstring& pathText, size_t limit) {
    const HANDLE input = CreateFileW(
        pathText.c_str(),
        GENERIC_READ,
        FILE_SHARE_READ,
        nullptr,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        nullptr);
    if (input == INVALID_HANDLE_VALUE) {
        return std::string();
    }
    std::vector<char> buffer(limit);
    DWORD read = 0;
    const BOOL readSucceeded =
        ReadFile(input, buffer.data(), static_cast<DWORD>(limit), &read, nullptr);
    static_cast<void>(CloseHandle(input));
    if (!readSucceeded) {
        return std::string();
    }
    return std::string(buffer.data(), read);
}

// The checkpoint file says which layout it is. Anything unreadable is reported
// as the encoded block so the existing reader produces the existing error.
CheckpointLayout ReadCheckpointLayout(const std::wstring& pathText) {
    const size_t magicLength = std::char_traits<char>::length(kEncodedBlockMagic);
    const std::string header = ReadSmallCheckpointFile(pathText, magicLength);
    if (header.size() < magicLength) {
        return CheckpointLayout::EncodedBlock;
    }
    if (header.compare(0, magicLength, kEncodedBlockMagic, magicLength) == 0 ||
        header.compare(0, magicLength, kSignedBlockMagic, magicLength) == 0) {
        return CheckpointLayout::EncodedBlock;
    }
    return CheckpointLayout::DocumentFile;
}

bool ParsePositiveLong(
    const std::string& text,
    const size_t valueStart,
    LONG* const value) {
    if (valueStart >= text.size()) {
        return false;
    }
    unsigned long long parsed = 0;
    for (size_t index = valueStart; index < text.size(); ++index) {
        const char character = text[index];
        if (character < '0' || character > '9') {
            return false;
        }
        parsed = parsed * 10ULL +
            static_cast<unsigned long long>(character - '0');
        if (parsed >
            static_cast<unsigned long long>(
                (std::numeric_limits<LONG>::max)())) {
            return false;
        }
    }
    if (parsed == 0) {
        return false;
    }
    *value = static_cast<LONG>(parsed);
    return true;
}

void ReadCheckpointMeta(
    const std::wstring& pathText,
    std::wstring* const documentFormat,
    std::wstring* const contentSignature,
    bool* const p1SingleTextUndo,
    std::wstring* const originSignature,
    LONG* const originPageCount) {
    documentFormat->assign(kDefaultDocumentFormat);
    contentSignature->clear();
    *p1SingleTextUndo = false;
    originSignature->clear();
    *originPageCount = -1;
    const std::string text = ReadSmallCheckpointFile(
        CheckpointMetaPath(pathText),
        static_cast<size_t>(kMaximumMetaFileBytes));
    const size_t magicLength =
        std::char_traits<char>::length(kDocumentFileMetaMagic);
    if (text.size() < magicLength ||
        text.compare(0, magicLength, kDocumentFileMetaMagic, magicLength) != 0) {
        return;
    }
    bool malformedHistory = false;
    bool sawHistory = false;
    bool sawOrigin = false;
    bool sawOriginPages = false;
    bool sawTargetSignature = false;
    size_t cursor = magicLength;
    while (cursor < text.size()) {
        const size_t end = text.find('\n', cursor);
        const std::string line = text.substr(
            cursor,
            end == std::string::npos ? std::string::npos : end - cursor);
        std::wstring* target = nullptr;
        bool* sawTarget = nullptr;
        size_t valueStart = 0;
        if (line.compare(0, 4, "FMT ") == 0) {
            target = documentFormat;
            valueStart = 4;
        } else if (line.compare(0, 4, "SIG ") == 0) {
            target = contentSignature;
            sawTarget = &sawTargetSignature;
            valueStart = 4;
        } else if (line.compare(0, 7, "ORIGIN ") == 0) {
            target = originSignature;
            sawTarget = &sawOrigin;
            valueStart = 7;
        } else if (line == "HISTORY P1_SINGLE_TEXT_UNDO") {
            if (sawHistory) {
                malformedHistory = true;
            }
            sawHistory = true;
        } else if (line.compare(0, 13, "ORIGIN_PAGES ") == 0) {
            LONG parsed = -1;
            if (sawOriginPages ||
                !ParsePositiveLong(line, 13, &parsed)) {
                malformedHistory = true;
            } else {
                *originPageCount = parsed;
                sawOriginPages = true;
            }
        } else if (
            line.compare(0, 7, "HISTORY") == 0 ||
            line.compare(0, 6, "ORIGIN") == 0) {
            malformedHistory = true;
        }
        if (target != nullptr) {
            std::wstring value;
            for (size_t index = valueStart; index < line.size(); ++index) {
                const char character = line[index];
                if (character <= 0) {
                    value.clear();
                    break;
                }
                value.push_back(static_cast<wchar_t>(character));
            }
            if (value.empty() || (sawTarget != nullptr && *sawTarget)) {
                if (sawTarget != nullptr) {
                    malformedHistory = true;
                }
            } else {
                *target = value;
                if (sawTarget != nullptr) {
                    *sawTarget = true;
                }
            }
        }
        if (end == std::string::npos) {
            break;
        }
        cursor = end + 1;
    }
    *p1SingleTextUndo =
        !malformedHistory &&
        sawHistory &&
        sawTargetSignature &&
        sawOrigin &&
        sawOriginPages;
}

// Asks Hancom Office to stream the document it currently holds in memory to
// disk. This is the point of the disk layout: no part of the document is
// serialised into a BSTR, so a picture-heavy document that cannot fit one in a
// 32-bit address space is still checkpointed, and what lands on disk is a real
// document file that a person can open by hand.
struct CheckpointCaptureDetails {
    std::wstring documentFormat;
    std::wstring contentSignature;
};

CheckpointCaptureOutcome CaptureDocumentFileCheckpoint(
    Context* const context,
    const std::wstring& pathText,
    CheckpointCaptureDetails* const details = nullptr) {
    std::wstring documentPath;
    if (FAILED(ReadActiveDocumentPath(context->hwp, &documentPath)) ||
        documentPath.empty()) {
        // A document that was never saved has no path to be put back onto if
        // SaveAs adopts the copy, so it is not checkpointed this way at all.
        RecordCheckpointEvidence(
            context->result,
            kCaptureUnavailableMethod,
            kCaptureUnavailableNoPath);
        return CheckpointCaptureOutcome::Unavailable;
    }
    const std::wstring documentFormat = CheckpointDocumentFormat(documentPath);
    CComVariant raw;
    HRESULT status = Method(
        context->hwp,
        L"SaveAs",
        {
            CComVariant(pathText.c_str()),
            CComVariant(documentFormat.c_str()),
            CComVariant(kCheckpointSaveArguments),
        },
        &raw);
    bool saved = false;
    if (SUCCEEDED(status)) {
        status = AsBool(raw, &saved);
    }
    const bool wrote =
        SUCCEEDED(status) && saved && CheckpointFileHasContent(pathText);
    // Which of the three it was, decided here while all three are still
    // distinguishable. "the call or its return value failed", "the engine
    // answered no" and "the engine answered yes and wrote nothing" are three
    // different problems, and a caller that only learns "no checkpoint" cannot
    // tell them apart or act on any of them.
    const wchar_t* const unavailableReason =
        FAILED(status) ? kCaptureUnavailableSaveFailed
        : !saved       ? kCaptureUnavailableSaveRefused
                       : kCaptureUnavailableEmptyFile;

    // SaveAs is the one call here that could move the editing session onto the
    // copy, which would point every later save at a file this code deletes. No
    // documented option prevents that, so nothing is assumed: read the active
    // document path back and put the session on the user's document when the
    // call took it away.
    std::wstring currentPath;
    if (SUCCEEDED(ReadActiveDocumentPath(context->hwp, &currentPath)) &&
        !currentPath.empty() &&
        !SameDocumentPath(documentPath, currentPath)) {
        CComVariant rawRestored;
        HRESULT restoreStatus = Method(
            context->hwp,
            L"SaveAs",
            {
                CComVariant(documentPath.c_str()),
                CComVariant(documentFormat.c_str()),
                CComVariant(kCheckpointSaveArguments),
            },
            &rawRestored);
        bool restored = false;
        if (SUCCEEDED(restoreStatus)) {
            restoreStatus = AsBool(rawRestored, &restored);
        }
        std::wstring verifiedPath;
        if (FAILED(restoreStatus) || !restored ||
            FAILED(ReadActiveDocumentPath(context->hwp, &verifiedPath)) ||
            !SameDocumentPath(documentPath, verifiedPath)) {
            RemoveCheckpointFiles(pathText);
            static_cast<void>(SetError(
                context->result,
                L"DOCUMENT_CHECKPOINT_IDENTITY",
                documentPath,
                L"SaveAs moved the editing session onto the checkpoint copy and it could not be moved back"));
            return CheckpointCaptureOutcome::Failed;
        }
        RecordCheckpointEvidence(
            context->result,
            kCaptureEvidenceMethod,
            kCaptureEvidenceIdentityRestored);
    }
    if (!wrote) {
        RemoveCheckpointFiles(pathText);
        RecordCheckpointEvidence(
            context->result,
            kCaptureUnavailableMethod,
            unavailableReason);
        return CheckpointCaptureOutcome::Unavailable;
    }
    const std::wstring contentSignature = CaptureContentSignature(context);
    if (details != nullptr) {
        details->documentFormat = documentFormat;
        details->contentSignature = contentSignature;
        return CheckpointCaptureOutcome::Written;
    }
    // Taken after the copy, so it describes the same document the copy holds.
    // An empty signature means the capture did not succeed; a later restore
    // then falls back to the page-count check instead of failing.
    if (!WriteCheckpointMeta(
            pathText,
            documentFormat,
            contentSignature)) {
        RemoveCheckpointFiles(pathText);
        RecordCheckpointEvidence(
            context->result,
            kCaptureUnavailableMethod,
            kCaptureUnavailableMetaFailed);
        return CheckpointCaptureOutcome::Unavailable;
    }
    return CheckpointCaptureOutcome::Written;
}

bool SaveDocumentFile(Context* const context, const std::wstring& pathText) {
    switch (CaptureDocumentFileCheckpoint(context, pathText)) {
    case CheckpointCaptureOutcome::Written:
        RecordCheckpointEvidence(
            context->result,
            kCaptureEvidenceMethod,
            kCaptureEvidenceDocumentFile);
        return true;
    case CheckpointCaptureOutcome::Failed:
        return false;
    case CheckpointCaptureOutcome::Unavailable:
        break;
    }
    // Fallback: serialise the document into a BSTR and write it as the legacy
    // encoded block. This is what fails on a large picture-heavy document -- a
    // failed allocation arrives as an empty string rather than as an error --
    // which is why it is no longer the first choice. It stays because it is the
    // only capture that needs nothing but GetTextFile, so it still answers when
    // the document has no path of its own or the filter refused to save a copy.
    CComVariant rawDocument;
    std::wstring documentBlock;
    HRESULT status = Method(
        context->hwp,
        L"GetTextFile",
        {CComVariant(L"HWP"), CComVariant(L"")},
        &rawDocument);
    if (SUCCEEDED(status)) {
        status = AsString(rawDocument, &documentBlock);
    }
    if (FAILED(status) || documentBlock.empty()) {
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_CAPTURE",
            pathText,
            FAILED(status)
                ? FormatHResult(L"GetTextFile HWP document", status)
                : L"GetTextFile returned an empty HWP document");
    }
    // Taken after the block, so it describes the same document the block
    // encodes. An empty signature means the capture did not succeed; the file
    // is then written in the legacy V1 layout and a later restore falls back to
    // the page-count check instead of failing.
    if (!SaveEncodedBlockFile(
            context,
            pathText,
            documentBlock,
            CaptureContentSignature(context))) {
        return false;
    }
    RecordCheckpointEvidence(
        context->result,
        kCaptureEvidenceMethod,
        kCaptureEvidenceEncodedBlock);
    return true;
}

// Writes an engine-owned block straight to disk, without ever holding a second
// copy of it.
//
// This is the whole point of the probe. The block capture that already exists
// goes BSTR -> a second BSTR inside AsString -> std::wstring -> std::vector,
// so at its peak it is holding roughly six bytes for every character the engine
// produced. On a document whose block runs to hundreds of millions of
// characters that is well past what a 32-bit process can address, and a failure
// there cannot say whether the engine could not produce the block or whether
// this code could not hold it. Writing in fixed chunks from the engine's own
// string keeps that question answerable.
bool WriteBlockToFile(
    const std::wstring& pathText,
    const wchar_t* const block,
    const size_t length,
    std::uint64_t* const written,
    bool* const sawNonAscii) {
    *written = 0;
    *sawNonAscii = false;
    const HANDLE output = CreateFileW(
        pathText.c_str(),
        GENERIC_WRITE,
        0,
        nullptr,
        CREATE_ALWAYS,
        FILE_ATTRIBUTE_NORMAL,
        nullptr);
    if (output == INVALID_HANDLE_VALUE) {
        return false;
    }
    constexpr size_t kChunk = 64 * 1024;
    std::vector<BYTE> buffer;
    buffer.reserve(kChunk);
    bool ok = true;
    for (size_t index = 0; index < length && ok; ++index) {
        const wchar_t character = block[index];
        if (character > 0x7f) {
            *sawNonAscii = true;
        }
        buffer.push_back(static_cast<BYTE>(character & 0xff));
        if (buffer.size() == kChunk || index + 1 == length) {
            DWORD chunkWritten = 0;
            ok = WriteFile(
                     output,
                     buffer.data(),
                     static_cast<DWORD>(buffer.size()),
                     &chunkWritten,
                     nullptr) != FALSE &&
                chunkWritten == buffer.size();
            *written += chunkWritten;
            buffer.clear();
        }
    }
    return CloseHandle(output) != FALSE && ok;
}

// Read-only measurement of what the encoded-block layout can actually carry.
//
// The block layout is the one restore that has never grown a document, because
// it rebuilds the whole document instead of inserting a file into whatever
// section the caret is in. What nobody has ever measured is where it stops
// working: the capture fails by returning an empty string, and the size at
// which that starts has only ever been guessed at.
//
// This changes nothing about the document. It asks the engine for the block,
// reports how many characters came back and writes them where the caller asked,
// and it deliberately ignores the 256 MiB ceiling the stored layout applies --
// the point is to find the real limit, not to re-measure our own.
bool ProbeDocumentBlock(Context* const context, const std::wstring& pathText) {
    if (!std::filesystem::path(pathText).is_absolute()) {
        return SetError(
            context->result,
            L"BLOCK_PROBE_PATH",
            pathText,
            L"block probe output path must be absolute");
    }
    CComVariant raw;
    const HRESULT status = Method(
        context->hwp,
        L"GetTextFile",
        {CComVariant(L"HWP"), CComVariant(L"")},
        &raw);
    if (FAILED(status)) {
        return SetError(
            context->result,
            L"BLOCK_PROBE_CAPTURE",
            pathText,
            FormatHResult(L"GetTextFile HWP document", status));
    }
    if (raw.vt != VT_BSTR) {
        return SetError(
            context->result,
            L"BLOCK_PROBE_CAPTURE",
            pathText,
            L"GetTextFile returned a value that is not a string");
    }
    // Measured off the engine's own string. Nothing has been copied yet, so a
    // length here means the engine produced the block whatever happens next.
    const UINT length = raw.bstrVal == nullptr ? 0U : SysStringLen(raw.bstrVal);
    RecordBlockProbeCount(context->result, kBlockProbeLength, length);
    if (length == 0) {
        // The documented failure of this call: a failed allocation arrives as an
        // empty string rather than as an error.
        RecordCheckpointEvidence(
            context->result,
            kBlockProbeMethod,
            kBlockProbeAllocationFailed);
        return true;
    }
    std::uint64_t written = 0;
    bool sawNonAscii = false;
    const bool wrote =
        WriteBlockToFile(pathText, raw.bstrVal, length, &written, &sawNonAscii);
    RecordBlockProbeCount(context->result, kBlockProbeWritten, written);
    RecordCheckpointEvidence(
        context->result,
        kBlockProbeMethod,
        wrote ? kBlockProbeCaptured : kBlockProbeWriteFailed);
    if (sawNonAscii) {
        RecordCheckpointEvidence(
            context->result,
            kBlockProbeMethod,
            kBlockProbeNonAscii);
    }
    return true;
}

bool ReadEncodedBlockFile(
    ExecutionResult* const result,
    const std::wstring& pathText,
    std::wstring* const encodedBlock,
    std::wstring* const contentSignature) {
    if (contentSignature != nullptr) {
        contentSignature->clear();
    }
    if (encodedBlock == nullptr ||
        !std::filesystem::path(pathText).is_absolute()) {
        return SetError(
            result,
            L"BLOCK_INPUT_PATH",
            pathText,
            L"block input path must be absolute");
    }
    const HANDLE input = CreateFileW(
        pathText.c_str(),
        GENERIC_READ,
        FILE_SHARE_READ,
        nullptr,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        nullptr);
    if (input == INVALID_HANDLE_VALUE) {
        return SetError(
            result,
            L"BLOCK_FILE_OPEN",
            pathText,
            L"encoded HWP block file could not be opened");
    }
    LARGE_INTEGER fileSize{};
    const BOOL sizeSucceeded = GetFileSizeEx(input, &fileSize);
    const size_t magicLength = std::char_traits<char>::length(kEncodedBlockMagic);
    static_assert(
        sizeof(kSignedBlockMagic) == sizeof(kEncodedBlockMagic),
        "both checkpoint magics must be the same length");
    if (!sizeSucceeded ||
        fileSize.QuadPart <= static_cast<LONGLONG>(magicLength) ||
        fileSize.QuadPart > static_cast<LONGLONG>(kMaximumBlockFileBytes)) {
        static_cast<void>(CloseHandle(input));
        return SetError(
            result,
            L"BLOCK_FILE_SIZE",
            pathText,
            L"encoded HWP block file is empty, truncated, or exceeds 256 MiB");
    }
    std::vector<BYTE> bytes(static_cast<size_t>(fileSize.QuadPart));
    DWORD bytesRead = 0;
    const BOOL readSucceeded = ReadFile(
        input,
        bytes.data(),
        static_cast<DWORD>(bytes.size()),
        &bytesRead,
        nullptr);
    const BOOL closeSucceeded = CloseHandle(input);
    if (!readSucceeded || bytesRead != bytes.size() || !closeSucceeded) {
        return SetError(
            result,
            L"BLOCK_FILE_READ",
            pathText,
            L"encoded HWP block file could not be read completely");
    }
    const bool legacyMagic = std::equal(
        kEncodedBlockMagic,
        kEncodedBlockMagic + magicLength,
        bytes.begin());
    const bool signedMagic = !legacyMagic && std::equal(
        kSignedBlockMagic,
        kSignedBlockMagic + magicLength,
        bytes.begin());
    if (!legacyMagic && !signedMagic) {
        return SetError(
            result,
            L"BLOCK_FILE_MAGIC",
            pathText,
            L"encoded HWP block file header is invalid");
    }
    size_t blockStart = magicLength;
    if (signedMagic) {
        using Offset = std::vector<BYTE>::difference_type;
        const auto signatureStart =
            bytes.begin() + static_cast<Offset>(magicLength);
        const auto terminator =
            std::find(signatureStart, bytes.end(), static_cast<BYTE>('\n'));
        if (terminator == bytes.end()) {
            return SetError(
                result,
                L"BLOCK_FILE_MAGIC",
                pathText,
                L"encoded HWP block file signature line is unterminated");
        }
        for (auto cursor = signatureStart; cursor != terminator; ++cursor) {
            if (*cursor == 0 || *cursor > 0x7f) {
                if (contentSignature != nullptr) {
                    contentSignature->clear();
                }
                return SetError(
                    result,
                    L"BLOCK_FILE_ENCODING",
                    pathText,
                    L"encoded HWP block signature contains a non-ASCII or NUL byte");
            }
            if (contentSignature != nullptr) {
                contentSignature->push_back(static_cast<wchar_t>(*cursor));
            }
        }
        blockStart = static_cast<size_t>(terminator - bytes.begin()) + 1;
    }
    encodedBlock->clear();
    encodedBlock->reserve(bytes.size() - blockStart);
    for (size_t index = blockStart; index < bytes.size(); ++index) {
        if (bytes[index] == 0 || bytes[index] > 0x7f) {
            encodedBlock->clear();
            return SetError(
                result,
                L"BLOCK_FILE_ENCODING",
                pathText,
                L"encoded HWP block contains a non-ASCII or NUL byte");
        }
        encodedBlock->push_back(static_cast<wchar_t>(bytes[index]));
    }
    if (encodedBlock->empty()) {
        return SetError(
            result,
            L"BLOCK_FILE_SIZE",
            pathText,
            L"encoded HWP block is empty");
    }
    return true;
}

bool CaptureDocumentBlock(
    Context* const context,
    const std::wstring& location,
    std::wstring* const documentBlock) {
    CComVariant rawDocument;
    HRESULT status = Method(
        context->hwp,
        L"GetTextFile",
        {CComVariant(L"HWP"), CComVariant(L"")},
        &rawDocument);
    if (SUCCEEDED(status)) {
        status = AsString(rawDocument, documentBlock);
    }
    if (FAILED(status) || documentBlock->empty()) {
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_CAPTURE",
            location,
            FAILED(status)
                ? FormatHResult(L"GetTextFile HWP document", status)
                : L"GetTextFile returned an empty HWP document");
    }
    return true;
}

HRESULT ReadPageCountProperty(Context* const context, LONG* const pageCount) {
    CComVariant rawPageCount;
    const HRESULT status = PropertyGet(context->hwp, L"PageCount", &rawPageCount);
    if (FAILED(status)) {
        return status;
    }
    return AsLong(rawPageCount, pageCount);
}

// A checkpoint needs a real page count: it is the baseline a restore is
// verified against, so "unknown" cannot be carried here the way an inspection
// carries it -- comparing against an unknown baseline would let a restore
// report success having proved nothing.
//
// What can be removed is the reason the count was unknown. Hancom finishes
// paginating a freshly opened document in the background and PageCount answers
// 0 until it does; that blocked every native editing recipe on a document that
// had just been opened, which is exactly when a recipe is most likely to run.
// RecalcPageCount is IHwpObject's own synchronous method for finishing
// pagination on the spot, so the count is asked for again after it, once. A
// call that does not land is a hint that failed, not a failure of the read, and
// the refusal below is the same one as before -- now reached only after the
// engine has been given its chance, rather than in place of it.
//
// This call carries no time budget, unlike the Python wait for the same value
// -- see the budget-asymmetry note on ReadSettledPageCount in LiveInspection.cpp.
bool ReadPageCount(
    Context* const context,
    const std::wstring& location,
    LONG* const pageCount) {
    HRESULT status = ReadPageCountProperty(context, pageCount);
    if (SUCCEEDED(status) && *pageCount < 1) {
        CComVariant recalculated;
        static_cast<void>(
            Method(context->hwp, L"RecalcPageCount", {}, &recalculated));
        status = ReadPageCountProperty(context, pageCount);
    }
    if (FAILED(status) || *pageCount < 1) {
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_PAGE_COUNT",
            location,
            FAILED(status)
                ? FormatHResult(L"PageCount", status)
                : L"PageCount returned a value below one even after "
                  L"RecalcPageCount");
    }
    return true;
}

bool RunCheckpointAction(
    Context* const context,
    const wchar_t* const action,
    const std::wstring& location) {
    if (!RunAction(context->action, action, context->result, location)) {
        return false;
    }
    ++context->result->actionsExecuted;
    return true;
}

bool AuthorizeCheckpointMutation(
    Context* const context,
    const std::wstring& location) {
    if (context->request == nullptr ||
        !ValidateDocumentIdentity(
            context->hwp,
            *context->request,
            context->result)) {
        return false;
    }
    const std::wstring expected =
        context->request->expectedContentSignature;
    // Legacy diagnostic callers did not carry an out-of-band precondition.
    // Updated history callers always use ExecuteActionsChecked; keeping this
    // branch preserves old non-authorizing smoke and diagnostic behavior.
    if (expected.empty()) {
        return true;
    }
    if (!ContentSignatureIsComplete(expected)) {
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_PRECONDITION",
            location,
            L"expected live content signature is incomplete or malformed; "
            L"the document was not changed");
    }
    const std::wstring actual = CaptureContentSignature(context);
    bool textCompared = false;
    if (!ContentSignatureIsComplete(actual) ||
        !ContentSignaturesMatch(expected, actual, &textCompared)) {
        return SetError(
            context->result,
            L"STALE_DOCUMENT_CONTENT",
            location,
            L"live document content changed after checkpoint authorization; "
            L"the document was not changed");
    }
    return true;
}

// What every restore has to prove before it reports success, whichever layout
// the checkpoint used.
bool VerifyRestoredDocument(
    Context* const context,
    const LONG expectedPageCount,
    const std::wstring& expectedSignature,
    // Set when the restore deleted something inside the area the checkpoint
    // occupies. The page-count fallback below is then not evidence of anything:
    // a paragraph of the checkpoint removed by mistake leaves the same count a
    // correctly removed leftover does. Such a restore is refused unless the
    // content signature says it reproduced the checkpoint.
    const bool requireContentProof,
    const std::wstring& location,
    const bool allowDocumentFileNormalization = false) {
    LONG pageCount = 0;
    if (!ReadPageCount(context, location, &pageCount)) {
        return false;
    }
    if (pageCount != expectedPageCount) {
        // The numbers are in the message on purpose. "does not match" alone
        // cost a whole live run: the failure that produced it was an insert
        // that added exactly one blank page every time, and nothing in the
        // report said so.
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_PAGE_COUNT",
            location,
            L"restored document page count does not match the checkpoint: expected " +
                std::to_wstring(expectedPageCount) + L" but read " +
                std::to_wstring(pageCount));
    }
    // A matching page count is far too weak to claim the checkpoint was
    // restored: deleting every picture on a page leaves the page count alone.
    // When the checkpoint carries a content signature, the restored document
    // has to reproduce every component that was observed. An empty text
    // component is carried as a fact but cannot prove text equality.
    const std::wstring actualSignature =
        expectedSignature.empty() ? std::wstring() : CaptureContentSignature(context);
    if (expectedSignature.empty() || actualSignature.empty()) {
        if (requireContentProof) {
            return SetError(
                context->result,
                L"DOCUMENT_CHECKPOINT_CONTENT",
                location,
                L"restored document could not be proved by content, and this "
                L"restore removed a paragraph the checkpoint may have owned: " +
                    std::wstring(
                        expectedSignature.empty()
                            ? L"the checkpoint carries no content signature"
                            : L"the restored document's content signature could "
                              L"not be read"));
        }
        RecordRestoreEvidence(context->result, kRestoreEvidencePageCount);
        return true;
    }
    bool textCompared = false;
    if (!ContentSignaturesMatch(expectedSignature, actualSignature, &textCompared)) {
        if (!allowDocumentFileNormalization ||
            !ContentSignaturesMatch(
                expectedSignature,
                actualSignature,
                &textCompared,
                true)) {
            return SetError(
                context->result,
                L"DOCUMENT_CHECKPOINT_CONTENT",
                location,
                L"restored document content does not match the checkpoint: expected " +
                    expectedSignature + L" but read " + actualSignature);
        }
        // SaveAs/Open can normalize a document-file serialization without
        // changing its page/control/text content. Keep the strict fingerprint
        // for drift authorization, but report this narrower proof explicitly
        // instead of pretending every byte-level document fingerprint matched.
        RecordRestoreEvidence(
            context->result,
            kRestoreEvidenceDocumentFileNormalized);
        return true;
    }
    RecordRestoreEvidence(
        context->result,
        textCompared ? kRestoreEvidenceSignature : kRestoreEvidenceStructuralSignature);
    return true;
}

bool ClearDocumentBody(
    Context* context,
    const std::wstring& location,
    Position* emptied);

bool ReplaceDocumentBlock(
    Context* const context,
    const std::wstring& encodedBlock,
    const LONG expectedPageCount,
    const std::wstring& expectedSignature,
    const std::wstring& location) {
    RecordCheckpointEvidence(
        context->result, kRestoreAttemptMethod, kRestoreAttemptEncodedBlock);
    Position emptied;
    if (!ClearDocumentBody(context, location, &emptied)) {
        return false;
    }
    CComVariant inserted;
    HRESULT status = Method(
        context->hwp,
        L"SetTextFile",
        {
            CComVariant(encodedBlock.c_str()),
            CComVariant(L"HWP"),
            CComVariant(L"insertfile"),
        },
        &inserted);
    bool insertedBlock = false;
    if (SUCCEEDED(status)) {
        status = AsBool(inserted, &insertedBlock);
    }
    if (FAILED(status) || !insertedBlock) {
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_INSERT",
            location,
            FAILED(status)
                ? FormatHResult(L"SetTextFile HWP insertfile", status)
                : L"SetTextFile returned false");
    }
    if (!RunCheckpointAction(context, L"MoveDocBegin", location)) {
        return false;
    }
    // The encoded-block restore deletes nothing of its own, so the page-count
    // fallback stays available to it exactly as before.
    return VerifyRestoredDocument(
        context,
        expectedPageCount,
        expectedSignature,
        false,
        location);
}

// Hands the checkpoint file to the engine and lets it read the document off
// disk. Nothing holds the document in memory, so restoring the copy of a large
// document costs no more than saving it did.
bool InsertDocumentFile(
    Context* const context,
    const std::wstring& pathText,
    const std::wstring& documentFormat,
    const SectionFidelity fidelity,
    const std::wstring& location) {
    CComPtr<IDispatch> parameterSets;
    CComPtr<IDispatch> insertFile;
    CComPtr<IDispatch> set;
    if (!GetDispatchProperty(
            context->hwp,
            L"HParameterSet",
            parameterSets,
            context->result,
            location) ||
        !GetDispatchProperty(
            parameterSets,
            L"HInsertFile",
            insertFile,
            context->result,
            location) ||
        !GetDispatchProperty(insertFile, L"HSet", set, context->result, location)) {
        return false;
    }
    CComVariant ignored;
    const HRESULT status = Method(
        context->action,
        L"GetDefault",
        {CComVariant(L"InsertFile"), CComVariant(set)},
        &ignored);
    if (FAILED(status)) {
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_INSERT",
            location,
            FormatHResult(L"GetDefault InsertFile", status));
    }
    // KeepCharshape / KeepParashape / KeepStyle only say "the incoming document
    // keeps its own character shapes, paragraph shapes and styles", which is
    // exactly what a restore wants: the checkpoint is the answer, not the
    // document it is poured into. They are on for every attempt.
    //
    // KeepSection is not a shape flag and it is the one the caller chooses. The
    // official parameter table (HInsertFile, catalog page 80) defines it as
    // whether the inserted document is split off into a section of its own so
    // that its page layout survives the insert, and both settings have been
    // measured to break a different kind of document:
    //
    //   on  -- a 28 page checkpoint came back as 29 pages, then 30, then 31.
    //   off -- a 164 page checkpoint came back as 165 pages with a blank first
    //          page and checkpoint content beginning on page 2.
    //
    // MoveNextPos is retained for compatibility with the existing restore. Its
    // resulting caret is measured below, but is not assumed to be the insertion
    // boundary: a live document has already returned checkpoint body text
    // between that caret and the document end.
    const std::pair<const wchar_t*, CComVariant> items[] = {
        {L"FileName", CComVariant(pathText.c_str())},
        {L"FileFormat", CComVariant(documentFormat.c_str())},
        {L"FileArg", CComVariant(L"")},
        {L"KeepSection",
         CComVariant(fidelity == SectionFidelity::Keep ? 1L : 0L)},
        {L"KeepCharshape", CComVariant(1L)},
        {L"KeepParashape", CComVariant(1L)},
        {L"KeepStyle", CComVariant(1L)},
        {L"MoveNextPos", CComVariant(1L)},
    };
    for (const auto& [name, value] : items) {
        if (!PutItemOrProperty(insertFile, name, value, context->result, location)) {
            return false;
        }
    }
    bool executed = false;
    if (!CallBooleanMethod(
            context->action,
            L"Execute",
            {CComVariant(L"InsertFile"), CComVariant(set)},
            &executed,
            context->result,
            location)) {
        return false;
    }
    if (!executed) {
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_INSERT",
            location,
            L"InsertFile returned false");
    }
    ++context->result->actionsExecuted;
    return true;
}

// What one restore attempt actually saw, in numbers.
//
// This is the only channel a refused restore has: FailureResponse carries an
// error and nothing else. Three live rounds were spent inferring from a single
// page number what one line of measurement says outright, and each inference
// was wrong in a different way -- the leftover paragraph was not behind the
// inserted document, then it was not in front of it either. So the attempt
// reports what it measured instead of what it concluded.
//
// `pagesAfterEmptying` is the one that has never been looked at: everything so
// far has been read after the insert, which cannot tell an insert that added a
// page from an emptying that never finished.
struct InsertObservation {
    const wchar_t* residue = kResidueNotReached;
    // Whether anything was deleted inside the area the checkpoint occupies. A
    // restore that did that has to prove itself by content; a page count cannot
    // tell a removed leftover from a removed paragraph of the checkpoint.
    bool headTrimmed = false;
    LONG pagesAfterEmptying = -1;
    LONG paragraphAfterEmptying = -1;
    LONG listAfterEmptying = -1;
    LONG pagesAfterInsert = -1;
    LONG pagesAfterTrim = -1;
    std::wstring headAfterEmptying;
    std::wstring headAfterInsert;
    std::wstring headAfterTrim;
    std::wstring tailSample;
    // Where the sample came from. The leading trim reads the paragraph in FRONT
    // of the inserted document, and reporting that under "text after the insert"
    // told a live run the opposite of what had been read -- the one thing this
    // clause exists to get right.
    const wchar_t* sampleLabel = L"text between insert caret and document end";
};

// A page count that is worth reporting even when it cannot be read. Unlike
// ReadPageCount this records no error and refuses nothing: an observation that
// failed to observe must not be able to fail the restore it is describing.
LONG ObservePageCount(Context* const context) {
    LONG pageCount = -1;
    if (FAILED(ReadPageCountProperty(context, &pageCount))) {
        return -1;
    }
    return pageCount;
}

std::wstring ObservedNumber(const LONG value) {
    return value < 0 ? std::wstring(L"?") : std::to_wstring(value);
}

// The first few characters of a stretch of text, with control characters shown
// as dots so one line of message stays one line. Enough to say what the text is
// without copying a document into an error.
std::wstring SampleText(const std::wstring& text, const size_t limit) {
    std::wstring sample;
    for (const wchar_t character : text) {
        if (sample.size() >= limit) {
            sample += L"...";
            break;
        }
        sample.push_back(character < L' ' ? L'.' : character);
    }
    return sample;
}

std::wstring DescribeInsert(const InsertObservation& observation) {
    std::wstring text = L" [emptied to " +
        ObservedNumber(observation.pagesAfterEmptying) + L" pages at list " +
        ObservedNumber(observation.listAfterEmptying) + L" paragraph " +
        ObservedNumber(observation.paragraphAfterEmptying) + L"; inserted to " +
        ObservedNumber(observation.pagesAfterInsert) + L" pages; after trim " +
        ObservedNumber(observation.pagesAfterTrim) + L" pages; leftover paragraph: " +
        observation.residue;
    if (!observation.headAfterEmptying.empty()) {
        text += L"; head after emptying: " + observation.headAfterEmptying;
    }
    if (!observation.headAfterInsert.empty()) {
        text += L"; head after insert: " + observation.headAfterInsert;
    }
    if (!observation.headAfterTrim.empty()) {
        text += L"; head after trim: " + observation.headAfterTrim;
    }
    if (!observation.tailSample.empty()) {
        text += L"; " + std::wstring(observation.sampleLabel) + L": \"" +
            observation.tailSample + L"\"";
    }
    return text + L"]";
}

// What a stretch of document text is made of, for code that is about to decide
// whether deleting it destroys anything.
//
// A form feed is deliberately not counted as a paragraph break. The engine
// writes a manual page break and a section break into the text stream as one,
// and both are content: they place the text that follows. Worse, deleting one
// removes exactly one page -- the same amount the page-count gate is trying to
// account for -- so a break that got deleted would leave the restore looking
// correct. Nothing that can pay for itself like that may be deleted quietly.
enum class TrimTextKind { Empty, ParagraphBreaks, PageOrSectionBreak, Text };

TrimTextKind ClassifyTrimText(const std::wstring& text) {
    if (text.empty()) {
        return TrimTextKind::Empty;
    }
    bool sawFormFeed = false;
    for (const wchar_t character : text) {
        if (character == L'\r' || character == L'\n') {
            continue;
        }
        if (character == L'\f') {
            sawFormFeed = true;
            continue;
        }
        return TrimTextKind::Text;
    }
    return sawFormFeed ? TrimTextKind::PageOrSectionBreak
                       : TrimTextKind::ParagraphBreaks;
}

const wchar_t* HeadTextDescription(const TrimTextKind kind) {
    switch (kind) {
    case TrimTextKind::Empty:
        return L"unread";
    case TrimTextKind::ParagraphBreaks:
        return L"paragraph_breaks";
    case TrimTextKind::PageOrSectionBreak:
        return L"page_or_section_break";
    case TrimTextKind::Text:
        return L"text";
    }
    return L"?";
}

std::wstring NextSectionDescription(
    const DocumentHeadStructure& structure) {
    if (structure.sectionStartsInParagraph < 0) {
        return L"?";
    }
    if (structure.nextSectionList < 0) {
        return L"none";
    }
    return std::to_wstring(structure.nextSectionList) + L"," +
        std::to_wstring(structure.nextSectionParagraph) + L"," +
        std::to_wstring(structure.nextSectionCharacter);
}

std::wstring PositionDescription(const Position& position) {
    return std::to_wstring(position.list) + L"," +
        std::to_wstring(position.paragraph) + L"," +
        std::to_wstring(position.character);
}

std::wstring ObserveDocumentHead(
    Context* const context,
    const std::wstring& location,
    const bool moveToHead) {
    ExecutionResult probeResult;
    Context probe;
    probe.hwp = context->hwp;
    probe.action = context->action;
    probe.result = &probeResult;
    if (moveToHead &&
        !RunAction(probe.action, L"MoveDocBegin", probe.result, location)) {
        return L"head_text=?;page_break_before=?;"
            L"section_starts_in_head_paragraph=?;next_section=?";
    }

    const DocumentHeadStructure structure =
        CaptureDocumentHeadStructure(probe.hwp);
    const wchar_t* headText = L"?";
    Position head;
    if (GetPosition(probe.hwp, &head, probe.result)) {
        const Position secondParagraph{
            head.list,
            head.paragraph + 1,
            0,
        };
        if (SelectTextRange(
                &probe,
                head,
                secondParagraph,
                location)) {
            std::wstring text;
            headText = ReadSelectedText(&probe, &text, location)
                ? HeadTextDescription(ClassifyTrimText(text))
                : L"unread";
        } else {
            headText = L"unbounded";
        }
        static_cast<void>(
            RunAction(probe.action, L"Cancel", probe.result, location));
    }
    return L"head_text=" + std::wstring(headText) +
        L";page_break_before=" + ObservedNumber(structure.pageBreakBefore) +
        L";section_starts_in_head_paragraph=" +
        ObservedNumber(structure.sectionStartsInParagraph) +
        L";next_section=" + NextSectionDescription(structure);
}

bool ClearDocumentBody(
    Context* const context,
    const std::wstring& location,
    Position* const emptied) {
    Position bodyStart;
    Position bodyEnd;
    if (!RunCheckpointAction(context, L"MoveDocBegin", location) ||
        !GetPosition(context->hwp, &bodyStart, context->result) ||
        !RunCheckpointAction(context, L"MoveDocEnd", location) ||
        !GetPosition(context->hwp, &bodyEnd, context->result)) {
        return false;
    }
    if (bodyStart.list != bodyEnd.list) {
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_RANGE",
            location,
            L"document begin and end were reported in different lists");
    }
    if (!SamePosition(bodyStart, bodyEnd) &&
        (!SelectTextRange(context, bodyStart, bodyEnd, location) ||
         !RunCheckpointAction(context, L"Delete", location))) {
        return false;
    }
    Position bodyAfterDelete;
    if (!RunCheckpointAction(context, L"MoveDocBegin", location) ||
        !GetPosition(context->hwp, &bodyAfterDelete, context->result) ||
        !RunCheckpointAction(context, L"MoveDocEnd", location) ||
        !GetPosition(context->hwp, emptied, context->result)) {
        return false;
    }
    if (!SamePosition(bodyAfterDelete, *emptied)) {
        std::wstring remaining;
        ExecutionResult probeResult;
        Context probe;
        probe.hwp = context->hwp;
        probe.action = context->action;
        probe.result = &probeResult;
        const bool read =
            bodyAfterDelete.list == emptied->list &&
            SelectTextRange(
                &probe, bodyAfterDelete, *emptied, location) &&
            ReadSelectedText(&probe, &remaining, location);
        static_cast<void>(
            RunAction(probe.action, L"Cancel", probe.result, location));
        const TrimTextKind kind =
            read ? ClassifyTrimText(remaining) : TrimTextKind::Empty;
        if (!read || kind != TrimTextKind::ParagraphBreaks) {
            std::wstring message =
                L"document body remained after verified root-range deletion at list " +
                std::to_wstring(bodyAfterDelete.list) + L" paragraph " +
                std::to_wstring(bodyAfterDelete.paragraph);
            if (read && !remaining.empty()) {
                message += L": \"" + SampleText(remaining, 48) + L"\"";
            } else {
                message += L"; the remaining range could not be read";
            }
            return SetError(
                context->result,
                L"DOCUMENT_CHECKPOINT_NOT_EMPTY",
                location,
                message);
        }
    }
    *emptied = bodyAfterDelete;
    RecordBodyAfterEmptying(context->result, bodyAfterDelete);
    return RunCheckpointAction(context, L"MoveDocBegin", location);
}

bool ReadControlAnchor(
    IDispatch* const control,
    Position* const anchor) {
    CComVariant raw;
    CComPtr<IDispatch> set;
    HRESULT status =
        Method(control, L"GetAnchorPos", {CComVariant(0L)}, &raw);
    if (SUCCEEDED(status)) {
        status = AsDispatch(raw, set);
    }
    const auto readItem =
        [&](const wchar_t* const name, LONG* const value) {
            CComVariant item;
            const HRESULT itemStatus =
                Method(set, L"Item", {CComVariant(name)}, &item);
            return FAILED(itemStatus) ? itemStatus : AsLong(item, value);
        };
    return SUCCEEDED(status) &&
        SUCCEEDED(readItem(L"List", &anchor->list)) &&
        SUCCEEDED(readItem(L"Para", &anchor->paragraph)) &&
        SUCCEEDED(readItem(L"Pos", &anchor->character));
}

bool FindLeadingSectionControl(
    Context* const context,
    const Position& head,
    const std::wstring& location,
    CComPtr<IDispatch>& found) {
    CComVariant raw;
    HRESULT status = PropertyGet(context->hwp, L"HeadCtrl", &raw);
    CComPtr<IDispatch> control;
    if (SUCCEEDED(status)) {
        status = AsDispatch(raw, control);
    }
    constexpr size_t kMaximumControls = 20'000;
    for (size_t visited = 0;
         SUCCEEDED(status) && control != nullptr && visited < kMaximumControls;
         ++visited) {
        CComVariant rawId;
        std::wstring id;
        status = PropertyGet(control, L"CtrlID", &rawId);
        if (SUCCEEDED(status)) {
            status = AsString(rawId, &id);
        }
        if (SUCCEEDED(status) && id == L"secd") {
            Position anchor;
            if (!ReadControlAnchor(control, &anchor)) {
                status = E_FAIL;
            } else if (SamePosition(anchor, head)) {
                found = control;
                return true;
            }
        }
        CComVariant rawNext;
        if (SUCCEEDED(status)) {
            status = PropertyGet(control, L"Next", &rawNext);
        }
        CComPtr<IDispatch> next;
        if (SUCCEEDED(status)) {
            status = AsDispatch(rawNext, next);
        }
        control = next;
    }
    return SetError(
        context->result,
        L"DOCUMENT_CHECKPOINT_SECTION_RESIDUE",
        location,
        L"the isolated leading section was observed but its exact section "
        L"control could not be proved");
}

bool IsIsolatedLeadingSection(
    const DocumentHeadStructure& structure,
    const Position& head,
    const Position& secondParagraph) {
    return structure.sectionStartsInParagraph == 1 &&
        structure.nextSectionList == secondParagraph.list &&
        structure.nextSectionParagraph == secondParagraph.paragraph &&
        structure.nextSectionCharacter == secondParagraph.character &&
        head.character == 0;
}

// ClearDocumentBody accepts a root range containing only paragraph breaks as
// empty because the engine retains at least a paragraph-shaped insertion point.
// The 164-page failure identified what happens to that point after InsertFile:
// one `secd` remains on paragraph 0 and the inserted document's first `secd`
// starts at paragraph 1. The point is therefore a one-paragraph leading section,
// not a paragraph that joined the checkpoint's first section.
//
// MoveNextPos is also not treated as proof of an insertion boundary. The live
// document returned checkpoint body text between the caret and document end.
// Such text is never deleted, but it must not prevent an independent check for a
// paragraph-break-only residue at the document head.
//
// An empty paragraph alone cannot be told apart from a checkpoint that begins
// with one. It is removed only when all independent observations agree:
//
//   It only runs when the document has more pages than the checkpoint. That is
//   the case an extra paragraph can explain, and the case an absorbed paragraph
//   never produces -- a restore that already matches is never touched, which is
//   what keeps the 28 page restore that has always worked working.
//
//   The first paragraph is bounded by the second and contains paragraph breaks
//   only. The structure scan must also prove exactly one `secd` at paragraph 0
//   and the next `secd` at paragraph 1, character 0.
//
// Only then is the exact paragraph-0 `secd` deleted before the empty paragraph.
// Any unavailable or different structure is left untouched. The content
// signature remains the final guard and forces rollback if the engine removed
// anything belonging to the checkpoint.
bool TrimLeadingResidue(
    Context* const context,
    const LONG expectedPageCount,
    const std::wstring& expectedSignature,
    const std::wstring& location,
    InsertObservation* const observation) {
    const wchar_t** const observed = &observation->residue;
    // Unlike the tail, this deletes at the front of the checkpoint. A content
    // signature is therefore mandatory even after the adjacent section controls
    // prove the empty paragraph is isolated; a page count cannot detect that the
    // wrong paragraph or section definition was removed.
    if (expectedSignature.empty()) {
        *observed = kResidueHeadNeedsSignature;
        return true;
    }
    LONG pageCount = 0;
    if (!ReadPageCount(context, location, &pageCount)) {
        return false;
    }
    if (pageCount == expectedPageCount) {
        *observed = kResiduePageCountAlreadyMatches;
        return true;
    }
    if (pageCount < expectedPageCount) {
        // Fewer pages than the checkpoint. Whatever went wrong, a paragraph too
        // many is not it, and removing one would only widen the gap.
        *observed = kResiduePageCountBelow;
        return true;
    }
    if (!RunCheckpointAction(context, L"MoveDocBegin", location)) {
        return false;
    }
    Position head;
    if (!GetPosition(context->hwp, &head, context->result)) {
        return false;
    }
    const DocumentHeadStructure headStructure =
        CaptureDocumentHeadStructure(context->hwp);
    // Selecting into a second paragraph that is not there fails, and that is an
    // answer rather than a fault: it says this document has nothing the first
    // paragraph could be separated from. It is kept off the caller's result so a
    // refusal to trim never reads as a failed restore.
    ExecutionResult probeResult;
    Context probe;
    probe.hwp = context->hwp;
    probe.action = context->action;
    probe.result = &probeResult;
    const Position secondParagraph{head.list, head.paragraph + 1, 0};
    if (!SelectTextRange(&probe, head, secondParagraph, location)) {
        // The refusal can still arrive with block mode switched on and a
        // partial range standing, so this exit clears the selection like every
        // other one below it. A probe that answers "no" must leave nothing of
        // its own behind for the next command to inherit.
        static_cast<void>(
            RunAction(context->action, L"Cancel", context->result, location));
        *observed = kResidueHeadUnbounded;
        return true;
    }
    std::wstring headText;
    if (!ReadSelectedText(&probe, &headText, location)) {
        static_cast<void>(
            RunAction(context->action, L"Cancel", context->result, location));
        *observed = kResidueHeadUnbounded;
        return true;
    }
    // Same rule as the tail and for the same reasons, with one difference that
    // matters: a page or section break here is inside the checkpoint's own
    // territory and removing it would take a page with it, which is exactly the
    // page this gate is trying to account for.
    const TrimTextKind kind = ClassifyTrimText(headText);
    if (kind != TrimTextKind::ParagraphBreaks) {
        static_cast<void>(
            RunAction(context->action, L"Cancel", context->result, location));
        observation->tailSample = SampleText(headText, 48);
        observation->sampleLabel = L"first paragraph";
        *observed = kind == TrimTextKind::Empty ? kResidueHeadUnread
            : kind == TrimTextKind::PageOrSectionBreak ? kResidueBreakInText
                                                       : kResidueHeadHasContent;
        return true;
    }
    if (!IsIsolatedLeadingSection(headStructure, head, secondParagraph)) {
        static_cast<void>(
            RunAction(context->action, L"Cancel", context->result, location));
        *observed = kResidueHeadSectionUnproven;
        return true;
    }
    static_cast<void>(
        RunAction(context->action, L"Cancel", context->result, location));
    CComPtr<IDispatch> leadingSection;
    if (!FindLeadingSectionControl(
            context, head, location, leadingSection)) {
        return false;
    }
    bool sectionDeleted = false;
    if (!CallBooleanMethod(
            context->hwp,
            L"DeleteCtrl",
            {CComVariant(leadingSection)},
            &sectionDeleted,
            context->result,
            location)) {
        return false;
    }
    if (!sectionDeleted) {
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_SECTION_RESIDUE",
            location,
            L"DeleteCtrl refused the proved isolated leading section");
    }
    ++context->result->actionsExecuted;
    if (!SelectTextRange(
            context, head, secondParagraph, location) ||
        !RunCheckpointAction(context, L"Delete", location)) {
        return false;
    }
    RecordInsertEvidence(context->result, kRestoreInsertResidueTrimmedHead);
    RecordInsertEvidence(
        context->result, kRestoreInsertLeadingSectionRemoved);
    observation->headTrimmed = true;
    *observed = kResidueTrimmedHead;
    return true;
}

bool TrimInsertResidue(
    Context* const context,
    const LONG expectedPageCount,
    const std::wstring& expectedSignature,
    const std::wstring& location,
    InsertObservation* const observation) {
    const wchar_t** const observed = &observation->residue;
    Position insertEnd;
    if (!GetPosition(context->hwp, &insertEnd, context->result)) {
        return false;
    }
    observation->headAfterInsert =
        ObserveDocumentHead(context, location, true);
    if (!RunCheckpointAction(context, L"MoveDocEnd", location)) {
        return false;
    }
    Position documentEnd;
    if (!GetPosition(context->hwp, &documentEnd, context->result)) {
        return false;
    }
    observation->headAfterInsert +=
        L";insert_caret=" + PositionDescription(insertEnd) +
        L";document_end=" + PositionDescription(documentEnd);
    // The caret is only a candidate boundary. When the selected range contains
    // checkpoint text, the range is left untouched and the head is checked
    // independently. A caret in another list is not used as a text boundary.
    if (insertEnd.list == documentEnd.list &&
        !SamePosition(insertEnd, documentEnd)) {
        std::wstring tail;
        if (!ReadTail(context, insertEnd, &tail)) {
            return false;
        }
        const TrimTextKind kind = ClassifyTrimText(tail);
        observation->headAfterInsert +=
            L";caret_range=" + std::wstring(HeadTextDescription(kind));
        AppendRestoreObservation(
            context->result,
            L"head_after_insert",
            observation->headAfterInsert);
        if (kind != TrimTextKind::ParagraphBreaks) {
            static_cast<void>(
                RunAction(context->action, L"Cancel", context->result, location));
            // What it is decides everything and cannot be guessed at: content of
            // the checkpoint that the caret simply did not cover reads one way,
            // body the emptying failed to remove reads another.
            //
            // Empty is its own answer. The caret and the end of the document
            // have just been proved to be different places, so the stretch
            // between them exists and reading nothing out of it describes the
            // read, not the document.
            observation->tailSample = SampleText(tail, 48);
            *observed = kind == TrimTextKind::Empty ? kResidueTailUnread
                : kind == TrimTextKind::PageOrSectionBreak ? kResidueBreakInText
                                                           : kResidueTailHasContent;
            return TrimLeadingResidue(
                context,
                expectedPageCount,
                expectedSignature,
                location,
                observation);
        } else {
            if (!RunCheckpointAction(context, L"Delete", location)) {
                return false;
            }
            RecordInsertEvidence(
                context->result, kRestoreInsertResidueTrimmedTail);
            *observed = kResidueTrimmedTail;
            return true;
        }
    }
    observation->headAfterInsert += insertEnd.list != documentEnd.list
        ? L";caret_range=cross_list"
        : L";caret_range=empty";
    AppendRestoreObservation(
        context->result,
        L"head_after_insert",
        observation->headAfterInsert);
    return TrimLeadingResidue(
        context,
        expectedPageCount,
        expectedSignature,
        location,
        observation);
}

// Load a document-file checkpoint as a document instead of inserting it into
// an emptied body. InsertFile is a content operation: on a multi-section HWP
// it can retain an empty section or flatten the checkpoint's section breaks,
// which changes the page count even though the source file is exact. Open uses
// the existing document slot (the SaveReopen lifecycle proves that reopening
// the same path retains its identity), after replacing the user's file bytes
// with the exact checkpoint. Clear normally empties that slot first, but it is
// only cleanup: if the engine refuses it, Open can still replace the active
// document. The path, position, page-count, and signature checks below remain
// the correctness gate in either case. The caller has already captured a
// rollback copy and authorized the live signature before reaching here.
bool RestoreDocumentFileByReopen(
    Context* const context,
    const std::wstring& checkpointPath,
    const std::wstring& checkpointFormat,
    const LONG expectedPageCount,
    const std::wstring& expectedSignature,
    const std::wstring& destinationPath,
    const std::wstring& location,
    const bool skipClear,
    bool* const clearFailed) {
    if (checkpointPath.empty() || destinationPath.empty()) {
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_IDENTITY",
            location,
            L"document-file restore needs both a checkpoint path and the user's document path");
    }
    if (clearFailed != nullptr) {
        *clearFailed = false;
    }
    bool usedNoClear = skipClear;
    RecordCheckpointEvidence(
        context->result,
        kRestoreAttemptMethod,
        usedNoClear ? kRestoreAttemptReopenNoClear : kRestoreAttemptReopen);

    if (!skipClear) {
        CComVariant discard;
        discard.vt = VT_I2;
        discard.iVal = 1;
        CComVariant rawClearReturn;
        const HRESULT statusAfterClear = Method(
            context->hwp,
            L"Clear",
            {discard},
            &rawClearReturn);
        if (FAILED(statusAfterClear)) {
            usedNoClear = true;
            if (clearFailed != nullptr) {
                *clearFailed = true;
            }
            RecordCheckpointEvidence(
                context->result,
                kRestoreAttemptMethod,
                kRestoreAttemptReopenNoClear);
        }
    }
    // The original file may still be represented by the active document, so do
    // not ask SaveAs to move a checkpoint document over it. Replace the bytes
    // first, then Open the user's path in the same document slot. Open is the
    // replacement operation; Clear only ensures the slot starts empty. The
    // identity and content checks below reject an Open that did not replace it.
    std::error_code copyError;
    std::filesystem::copy_file(
        checkpointPath,
        destinationPath,
        std::filesystem::copy_options::overwrite_existing,
        copyError);
    if (copyError) {
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_REOPEN",
            location,
            L"copying the document checkpoint onto the user's path failed");
    }

    CComVariant rawOpenReturn;
    const HRESULT openStatus = Method(
        context->hwp,
        L"Open",
        {
            CComVariant(destinationPath.c_str()),
            CComVariant(checkpointFormat.c_str()),
            CComVariant(L"lock:FALSE"),
        },
        &rawOpenReturn);
    bool opened = false;
    HRESULT verifiedOpenStatus = openStatus;
    if (SUCCEEDED(verifiedOpenStatus)) {
        verifiedOpenStatus = AsBool(rawOpenReturn, &opened);
    }
    if (FAILED(verifiedOpenStatus) || !opened) {
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_REOPEN",
            location,
            FAILED(verifiedOpenStatus)
                ? FormatHResult(L"Open restored document", verifiedOpenStatus)
                : L"Open restored document returned false");
    }

    std::wstring restoredPath;
    if (FAILED(ReadActiveDocumentPath(context->hwp, &restoredPath)) ||
        !SameDocumentPath(destinationPath, restoredPath)) {
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_IDENTITY",
            location,
            L"restored document was not returned to the user's document path; observed " +
                restoredPath);
    }
    if (!RunCheckpointAction(context, L"MoveDocBegin", location)) {
        return false;
    }
    if (!VerifyRestoredDocument(
            context,
            expectedPageCount,
            expectedSignature,
            false,
            location,
            true)) {
        return false;
    }
    RecordRestoreEvidence(
        context->result,
        usedNoClear ? kRestoreAttemptReopenNoClear : kRestoreAttemptReopen);
    return true;
}

bool ReplaceDocumentWithFileOnce(
    Context* const context,
    const std::wstring& pathText,
    const std::wstring& documentFormat,
    const SectionFidelity fidelity,
    const LONG expectedPageCount,
    const std::wstring& expectedSignature,
    const std::wstring& location,
    bool* const bodyCleared) {
    InsertObservation observation;
    *bodyCleared = false;
    RecordCheckpointEvidence(
        context->result,
        kRestoreAttemptMethod,
        fidelity == SectionFidelity::Flatten
            ? kRestoreAttemptFlatten
            : kRestoreAttemptKeep);
    Position emptied;
    if (!ClearDocumentBody(context, location, &emptied)) {
        return false;
    }
    *bodyCleared = true;
    // Measured here and nowhere else: what the document is once the emptying
    // that is supposed to have removed everything has run. An insert that adds a
    // page and an emptying that never finished look identical afterwards.
    observation.pagesAfterEmptying = ObservePageCount(context);
    observation.listAfterEmptying = emptied.list;
    observation.paragraphAfterEmptying = emptied.paragraph;
    observation.headAfterEmptying =
        ObserveDocumentHead(context, location, false);
    AppendRestoreObservation(
        context->result,
        L"head_after_emptying",
        observation.headAfterEmptying);
    if (!InsertDocumentFile(context, pathText, documentFormat, fidelity, location)) {
        return false;
    }
    observation.pagesAfterInsert = ObservePageCount(context);
    if (!TrimInsertResidue(
            context,
            expectedPageCount,
            expectedSignature,
            location,
            &observation)) {
        context->result->error.message += DescribeInsert(observation);
        return false;
    }
    if (!RunCheckpointAction(context, L"MoveDocBegin", location)) {
        context->result->error.message += DescribeInsert(observation);
        return false;
    }
    observation.headAfterTrim =
        ObserveDocumentHead(context, location, false);
    AppendRestoreObservation(
        context->result,
        L"head_after_trim",
        observation.headAfterTrim);
    observation.pagesAfterTrim = ObservePageCount(context);
    // Recorded before the verdict, so a restore that works reports the same
    // three numbers a restore that fails does. A count that could not be read
    // is left out rather than reported as a value.
    RecordObservedPageCount(
        context->result, kRestorePagesEmptied, observation.pagesAfterEmptying);
    RecordObservedPageCount(
        context->result, kRestorePagesInserted, observation.pagesAfterInsert);
    RecordObservedPageCount(
        context->result, kRestorePagesTrimmed, observation.pagesAfterTrim);
    if (VerifyRestoredDocument(
            context,
            expectedPageCount,
            expectedSignature,
            observation.headTrimmed,
            location)) {
        return true;
    }
    // The one place this can still be said. A refused restore reports an error
    // and nothing else, so what the attempt measured is written into the reason
    // rather than recorded as evidence nobody will receive.
    context->result->error.message += DescribeInsert(observation);
    return false;
}

// Carries a finished attempt's work and evidence onto the caller's result. The
// counters are the attempt's own; an attempt that failed still did the actions
// it reports, and hiding them would make a two-attempt restore look like a
// one-attempt restore in every count the caller has.
void AdoptAttemptResult(Context* const context, ExecutionResult* const attempt) {
    context->result->actionsExecuted += attempt->actionsExecuted;
    for (CallResult& evidence : attempt->callResults) {
        context->result->callResults.push_back(std::move(evidence));
    }
}

// A restore is a replace, and the replace has one setting -- KeepSection --
// whose right value depends on the checkpoint rather than on this code. So the
// checkpoint decides it, by being restored and checked.
//
// The flattening insert goes first because it is what shipped: every document
// this already restored correctly restores correctly on the first attempt and
// pays nothing for the second. The section-faithful insert runs only when the
// first attempt could not reproduce the checkpoint, which is exactly the
// multi-section document the first attempt reflows.
//
// Trying twice is safe in a way it would not be for an ordinary edit: the first
// attempt already emptied the document and poured a copy in, so the second one
// cannot damage anything the first one left -- it replaces all of it again. What
// it costs is time and engine history depth, and only on documents that are
// already failing.
//
// Neither attempt is allowed to loosen the check. A restore that ends with a
// page count or a content signature that is not the checkpoint's is refused
// however many ways were tried.
bool ReplaceDocumentWithFile(
    Context* const context,
    const std::wstring& pathText,
    const std::wstring& documentFormat,
    const LONG expectedPageCount,
    const std::wstring& expectedSignature,
    const std::wstring& location) {
    ExecutionResult flattenedResult;
    Context flattened;
    flattened.hwp = context->hwp;
    flattened.action = context->action;
    flattened.result = &flattenedResult;
    bool flattenedBodyCleared = false;
    if (ReplaceDocumentWithFileOnce(
            &flattened,
            pathText,
            documentFormat,
            SectionFidelity::Flatten,
            expectedPageCount,
            expectedSignature,
            location,
            &flattenedBodyCleared)) {
        RecordInsertEvidence(&flattenedResult, kRestoreInsertSectionsFlattened);
        AdoptAttemptResult(context, &flattenedResult);
        return true;
    }
    if (!flattenedBodyCleared) {
        return SetError(
            context->result,
            flattenedResult.error.code,
            flattenedResult.error.location,
            flattenedResult.error.message);
    }

    ExecutionResult keptResult;
    Context kept;
    kept.hwp = context->hwp;
    kept.action = context->action;
    kept.result = &keptResult;
    bool keptBodyCleared = false;
    if (ReplaceDocumentWithFileOnce(
            &kept,
            pathText,
            documentFormat,
            SectionFidelity::Keep,
            expectedPageCount,
            expectedSignature,
            location,
            &keptBodyCleared)) {
        RecordInsertEvidence(&keptResult, kRestoreInsertSectionsKept);
        AdoptAttemptResult(context, &flattenedResult);
        AdoptAttemptResult(context, &keptResult);
        return true;
    }

    // Both reasons, not just the second. When the two attempts fail the same way
    // the pair says the section split is not what this document is failing on;
    // when they fail differently, that is worth more than either message alone.
    return SetError(
        context->result,
        keptResult.error.code,
        keptResult.error.location,
        keptResult.error.message +
            L" (the same insert without the section split failed with: " +
            flattenedResult.error.message + L")");
}

// What a restore has to be able to undo: the state of the live document before
// any of it is deleted. The document-file copy is preferred for the same reason
// the capture prefers it; the encoded block answers when it cannot.
struct DocumentRollback {
    std::wstring filePath;
    std::wstring fileFormat;
    std::wstring originalPath;
    std::wstring block;
    std::wstring recoveryPath;
    LONG pageCount = 0;
    std::wstring signature;

    bool usesFile() const { return !filePath.empty(); }
};

bool ReplaceWithRollback(
    Context* const context,
    const DocumentRollback& rollback,
    const std::wstring& location,
    const bool skipDocumentClear) {
    if (rollback.usesFile()) {
        return RestoreDocumentFileByReopen(
            context,
            rollback.filePath,
            rollback.fileFormat,
            rollback.pageCount,
            rollback.signature,
            rollback.originalPath,
            location,
            skipDocumentClear,
            nullptr);
    }
    return ReplaceDocumentBlock(
        context,
        rollback.block,
        rollback.pageCount,
        rollback.signature,
        location);
}

bool CaptureDocumentRollback(
    Context* const context,
    const std::wstring& scratchPath,
    const std::wstring& location,
    DocumentRollback* const rollback) {
    std::wstring originalPath;
    if (SUCCEEDED(ReadActiveDocumentPath(context->hwp, &originalPath))) {
        rollback->originalPath = originalPath;
    }
    CheckpointCaptureDetails details;
    switch (CaptureDocumentFileCheckpoint(context, scratchPath, &details)) {
    case CheckpointCaptureOutcome::Written: {
        rollback->filePath = scratchPath;
        rollback->fileFormat = std::move(details.documentFormat);
        rollback->signature = std::move(details.contentSignature);
        rollback->recoveryPath = scratchPath;
        return ReadPageCount(context, location, &rollback->pageCount);
    }
    case CheckpointCaptureOutcome::Failed:
        return false;
    case CheckpointCaptureOutcome::Unavailable:
        break;
    }
    if (!CaptureDocumentBlock(context, location, &rollback->block)) {
        return false;
    }
    rollback->signature = CaptureContentSignature(context);
    if (!ReadPageCount(context, location, &rollback->pageCount)) {
        return false;
    }
    if (!SaveEncodedBlockFile(
            context,
            scratchPath,
            rollback->block,
            rollback->signature)) {
        return false;
    }
    rollback->recoveryPath = scratchPath;
    return true;
}

bool ReplaceDocumentFromCheckpoint(
    Context* const context,
    const std::wstring& pathText,
    const LONG expectedPageCount,
    const CheckpointLayout layout) {
    std::wstring targetBlock;
    std::wstring targetSignature;
    std::wstring targetFormat(kDefaultDocumentFormat);
    bool p1SingleTextUndo = false;
    std::wstring originSignature;
    LONG originPageCount = -1;
    if (layout == CheckpointLayout::EncodedBlock) {
        if (!ReadEncodedBlockFile(
                context->result,
                pathText,
                &targetBlock,
                &targetSignature)) {
            return false;
        }
    } else {
        if (!CheckpointFileHasContent(pathText)) {
            return SetError(
                context->result,
                L"BLOCK_FILE_SIZE",
                pathText,
                L"document checkpoint file is empty or unreadable");
        }
        ReadCheckpointMeta(
            pathText,
            &targetFormat,
            &targetSignature,
            &p1SingleTextUndo,
            &originSignature,
            &originPageCount);
    }
    if (!ContentSignatureIsComplete(targetSignature) ||
        (p1SingleTextUndo &&
         !ContentSignatureIsComplete(originSignature))) {
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_CONTENT",
            pathText,
            L"checkpoint content authorization signature is incomplete or "
            L"malformed; the document was not changed");
    }

    // Every restore strategy mutates the live document. Use a fresh recovery
    // name for every attempt so retrying can never erase the only faithful copy
    // retained by an earlier failure.
    std::wstring rollbackPath;
    if (!CreateCheckpointRollbackPath(pathText, &rollbackPath)) {
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_ROLLBACK_PATH",
            pathText,
            L"a unique rollback copy path could not be created; the document was not changed");
    }
    CheckpointScratchFile scratch(rollbackPath);
    DocumentRollback rollback;
    if (!CaptureDocumentRollback(
            context,
            scratch.Path(),
            pathText,
            &rollback)) {
        return false;
    }
    if (!ContentSignatureIsComplete(rollback.signature)) {
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_CONTENT",
            pathText,
            L"rollback content signature is incomplete or malformed; the "
            L"document was not changed");
    }
    // CaptureDocumentRollback may pump the HWP STA while saving the recovery
    // copy. Re-read and compare here, after that work and immediately before
    // the first Undo or whole-document replacement.
    if (!AuthorizeCheckpointMutation(context, pathText)) {
        return false;
    }

    if (p1SingleTextUndo) {
        ExecutionResult engineUndoResult;
        Context engineUndo;
        engineUndo.hwp = context->hwp;
        engineUndo.action = context->action;
        engineUndo.result = &engineUndoResult;
        RecordCheckpointEvidence(
            &engineUndoResult,
            kRestoreAttemptMethod,
            kRestoreAttemptEngineUndo);
        AppendRestoreObservation(
            &engineUndoResult,
            L"head_after_emptying",
            L"not reached;page_break_before=not reached;"
            L"section_starts_in_head_paragraph=not reached;"
            L"next_section=not reached");
        AppendRestoreObservation(
            &engineUndoResult,
            L"head_after_insert",
            L"not reached;page_break_before=not reached;"
            L"section_starts_in_head_paragraph=not reached;"
            L"next_section=not reached;insert_caret=not reached;"
            L"document_end=not reached;caret_range=not reached");
        RecordInsertEvidence(&engineUndoResult, kResidueNotReached);
        const bool undoRan =
            RunCheckpointAction(&engineUndo, L"Undo", pathText);
        if (undoRan) {
            const LONG pageCountAfterUndo = ObservePageCount(&engineUndo);
            RecordObservedPageCount(
                &engineUndoResult,
                kRestorePagesTrimmed,
                pageCountAfterUndo);
            if (RunCheckpointAction(
                    &engineUndo, L"MoveDocBegin", pathText)) {
                const std::wstring headAfterUndo =
                    ObserveDocumentHead(&engineUndo, pathText, false);
                AppendRestoreObservation(
                    &engineUndoResult,
                    L"head_after_trim",
                    headAfterUndo);
                if (VerifyRestoredDocument(
                        &engineUndo,
                        expectedPageCount,
                        targetSignature,
                        true,
                        pathText)) {
                    RecordInsertEvidence(
                        &engineUndoResult,
                        kRestoreInsertEngineUndoVerified);
                    AdoptAttemptResult(context, &engineUndoResult);
                    return true;
                }
            }

            // A true Undo that did not reach the exact target may have consumed
            // another editor's top history step. Put that one step back before
            // doing anything else, and never cross it with InsertFile.
            ExecutionResult engineRedoResult;
            Context engineRedo;
            engineRedo.hwp = context->hwp;
            engineRedo.action = context->action;
            engineRedo.result = &engineRedoResult;
            const bool redoRan =
                RunCheckpointAction(&engineRedo, L"Redo", pathText);
            const bool originRestored =
                redoRan &&
                RunCheckpointAction(
                    &engineRedo, L"MoveDocBegin", pathText) &&
                VerifyRestoredDocument(
                    &engineRedo,
                    originPageCount,
                    originSignature,
                    true,
                    pathText);
            AdoptAttemptResult(context, &engineUndoResult);
            AdoptAttemptResult(context, &engineRedoResult);
            context->result->retrySafe = false;
            if (originRestored) {
                return SetError(
                    context->result,
                    L"DOCUMENT_CHECKPOINT_ENGINE_HISTORY_DRIFT",
                    pathText,
                    L"one engine Undo did not reach the checkpoint; one Redo restored "
                    L"the exact state this call found, so InsertFile was not attempted "
                    L"across the intervening history step");
            }
            context->result->partialMutation = true;
            scratch.Keep();
            return SetError(
                context->result,
                L"DOCUMENT_CHECKPOINT_ENGINE_HISTORY",
                pathText,
                L"one engine Undo did not reach the checkpoint and one Redo could "
                L"not prove the original state; InsertFile was not attempted and "
                L"the document as this call found it was kept at " +
                    rollback.recoveryPath);
        }

        // A false return is not proof that the engine left the document alone.
        // Preserve the disk copy and stop instead of guessing or invoking Redo.
        AdoptAttemptResult(context, &engineUndoResult);
        context->result->partialMutation = true;
        context->result->retrySafe = false;
        scratch.Keep();
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_ENGINE_HISTORY",
            pathText,
            L"engine Undo was refused or failed; no file restore was attempted "
            L"because the live mutation state is uncertain, and the document as "
            L"this call found it was kept at " +
                rollback.recoveryPath);
    }

    ExecutionResult restoreResult;
    Context restore;
    restore.hwp = context->hwp;
    restore.action = context->action;
    restore.result = &restoreResult;
    bool restoreClearFailed = false;
    const bool restored = layout == CheckpointLayout::EncodedBlock
        ? ReplaceDocumentBlock(
              &restore,
              targetBlock,
              expectedPageCount,
              targetSignature,
              pathText)
        : RestoreDocumentFileByReopen(
              &restore,
              pathText,
              targetFormat,
              expectedPageCount,
              targetSignature,
              rollback.originalPath,
              pathText,
              false,
              &restoreClearFailed);
    AdoptAttemptResult(context, &restoreResult);
    if (restored) {
        return true;
    }

    ExecutionResult rollbackResult;
    Context rollbackContext;
    rollbackContext.hwp = context->hwp;
    rollbackContext.action = context->action;
    rollbackContext.result = &rollbackResult;
    if (!ReplaceWithRollback(
            &rollbackContext,
            rollback,
            pathText,
            restoreClearFailed)) {
        AdoptAttemptResult(context, &rollbackResult);
        context->result->partialMutation = true;
        context->result->retrySafe = false;
        // The document in the window is now neither the checkpoint nor what the
        // call found, and nothing here can honestly say which it is closer to.
        // What can be said is where the document the call found still exists: a
        // copy of it was written to disk before any of it was deleted, and it is
        // the only faithful record of that state left. It is deleted when this
        // returns on every other path; on this one it is kept and named, because
        // deleting it is deleting the only way back.
        std::wstring keptCopy;
        if (!rollback.recoveryPath.empty()) {
            scratch.Keep();
            keptCopy =
                L"; a copy of the document as this call found it was kept at " +
                rollback.recoveryPath;
        }
        // Both messages, not just the second one. The rollback runs the same
        // replace the restore just ran, so when it fails the same way the pair
        // of messages is what says "this is one systematic defect", and when it
        // fails differently that is worth more than either message alone. The
        // report that came back from the engine carried only the rollback's
        // half and the restore's reason was gone.
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_ROLLBACK",
            pathText,
            L"checkpoint restore failed and the previous document could not be restored: " +
                rollbackResult.error.message + L" (the restore itself failed with: " +
                restoreResult.error.message + L")" + keptCopy);
    }
    AdoptAttemptResult(context, &rollbackResult);
    context->result->retrySafe = true;
    return SetError(
        context->result,
        L"DOCUMENT_CHECKPOINT_RESTORE",
        pathText,
        L"checkpoint restore failed and the previous document was restored: " +
            restoreResult.error.message);
}

bool RestoreDocumentFile(
    Context* const context,
    const std::wstring& pathText,
    const LONG expectedPageCount) {
    // The checkpoint decides how it is read back. Files written by an older
    // bridge still carry the encoded block magic and still restore that way.
    return ReplaceDocumentFromCheckpoint(
        context,
        pathText,
        expectedPageCount,
        ReadCheckpointLayout(pathText));
}

}
