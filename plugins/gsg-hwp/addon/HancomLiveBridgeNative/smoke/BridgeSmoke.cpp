#include <Windows.h>
#include <ObjIdl.h>
#include <Ocidl.h>
#include <Ole2.h>
#include <atlcomcli.h>

#include "../BridgeStatus.h"
#include "../TableInspection.h"
#include "FakeParameterArrayDispatch.h"

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <cwchar>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <map>
#include <string>
#include <vector>

namespace {

constexpr char kOnInitialLoad[] = "{B91A2981-A001-44a9-933F-5BF70A747967}";
constexpr char kOnLoad[] = "{3E4DC866-051C-4989-820E-DADC1E6264B9}";
constexpr char kBootstrapAction[] = "{CFB0F99F-3589-4A85-9D8B-2D6BCE5B35D1}";
constexpr IID kDispatchEventIid = {
    0xF04A09A0,
    0xF319,
    0x40D5,
    {0x88, 0xCA, 0xCA, 0x43, 0x9C, 0xC6, 0x82, 0x0C},
};

std::wstring EncodeUtf8Base64(const std::wstring& value) {
    const int byteCount = WideCharToMultiByte(
        CP_UTF8,
        WC_ERR_INVALID_CHARS,
        value.data(),
        static_cast<int>(value.size()),
        nullptr,
        0,
        nullptr,
        nullptr);
    if (byteCount <= 0) {
        return L"";
    }
    std::vector<unsigned char> bytes(static_cast<size_t>(byteCount));
    if (WideCharToMultiByte(
            CP_UTF8,
            WC_ERR_INVALID_CHARS,
            value.data(),
            static_cast<int>(value.size()),
            reinterpret_cast<char*>(bytes.data()),
            byteCount,
            nullptr,
            nullptr) != byteCount) {
        return L"";
    }
    constexpr wchar_t alphabet[] =
        L"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::wstring encoded;
    encoded.reserve(((bytes.size() + 2) / 3) * 4);
    for (size_t index = 0; index < bytes.size(); index += 3) {
        const size_t remaining = bytes.size() - index;
        const std::uint32_t triple =
            static_cast<std::uint32_t>(bytes[index]) << 16 |
            (remaining > 1
                ? static_cast<std::uint32_t>(bytes[index + 1]) << 8
                : 0U) |
            (remaining > 2
                ? static_cast<std::uint32_t>(bytes[index + 2])
                : 0U);
        encoded.push_back(alphabet[(triple >> 18) & 0x3f]);
        encoded.push_back(alphabet[(triple >> 12) & 0x3f]);
        encoded.push_back(remaining > 1 ? alphabet[(triple >> 6) & 0x3f] : L'=');
        encoded.push_back(remaining > 2 ? alphabet[triple & 0x3f] : L'=');
    }
    return encoded;
}

bool WriteCheckpointFixture(
    const std::filesystem::path& path,
    const std::string& encodedBlock) {
    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    constexpr char magic[] = "GSG_HWP_ENCODED_BLOCK_V1\n";
    output.write(magic, static_cast<std::streamsize>(sizeof(magic) - 1));
    output.write(
        encodedBlock.data(),
        static_cast<std::streamsize>(encodedBlock.size()));
    output.close();
    return output.good();
}

hancom::inspection::CellTopologyCell IndexedTopologyCell(
    const wchar_t* const address,
    const long rowSpan = 1,
    const long columnSpan = 1) {
    hancom::inspection::CellTopologyCell cell;
    cell.address = address;
    cell.rowSpan = rowSpan;
    cell.columnSpan = columnSpan;
    return cell;
}

bool CellTopologyOwnerIndexSmoke() {
    hancom::inspection::CellTopology topology;
    std::wstring error;
    if (!topology.Build(
            {
                IndexedTopologyCell(L"A1", 1, 2),
                IndexedTopologyCell(L"A2"),
                IndexedTopologyCell(L"B2"),
            },
            &error)) {
        return false;
    }
    const auto addressAt = [](const hancom::inspection::CellTopology& value,
                              const long row,
                              const long column) {
        const hancom::inspection::CellTopologyCell* const owner =
            value.OwnerAt(row, column);
        return owner == nullptr ? std::wstring{} : owner->address;
    };
    if (addressAt(topology, 1, 1) != L"A1" ||
        addressAt(topology, 1, 2) != L"A1" ||
        addressAt(topology, 2, 1) != L"A2" ||
        addressAt(topology, 2, 2) != L"B2" ||
        topology.OwnerAt(0, 1) != nullptr ||
        topology.OwnerAt(3, 1) != nullptr) {
        return false;
    }

    const hancom::inspection::CellTopology copied = topology;
    topology.Clear();
    if (topology.OwnerAt(1, 1) != nullptr ||
        addressAt(copied, 1, 2) != L"A1") {
        return false;
    }
    return topology.Build(
               {
                   IndexedTopologyCell(L"A1"),
                   IndexedTopologyCell(L"B1"),
               },
               &error) &&
        addressAt(topology, 1, 1) == L"A1" &&
        addressAt(topology, 1, 2) == L"B1" &&
        topology.OwnerAt(2, 1) == nullptr;
}

struct IHncUserActionModule {
    virtual LPCSTR EnumAction(int iterator) = 0;
    virtual BOOL GetActionImage(
        LPCSTR action,
        UINT state,
        HBITMAP* bitmap,
        int* imageIndex) = 0;
    virtual BOOL UpdateUI(LPCSTR action, LPDISPATCH object, UINT* state) = 0;
    virtual int DoAction(LPCSTR action, LPDISPATCH object) = 0;
};

class FakeListParaPosDispatch final : public IDispatch {
public:
    void SetPosition(
        const LONG list,
        const LONG paragraph,
        const LONG character) noexcept {
        list_ = list;
        paragraph_ = paragraph;
        character_ = character;
    }

    HRESULT STDMETHODCALLTYPE QueryInterface(
        REFIID interfaceId,
        void** const object) override {
        if (object == nullptr) {
            return E_POINTER;
        }
        if (interfaceId == IID_IUnknown || interfaceId == IID_IDispatch) {
            *object = static_cast<IDispatch*>(this);
            static_cast<void>(AddRef());
            return S_OK;
        }
        *object = nullptr;
        return E_NOINTERFACE;
    }

    ULONG STDMETHODCALLTYPE AddRef() override {
        return static_cast<ULONG>(InterlockedIncrement(&references_));
    }

    ULONG STDMETHODCALLTYPE Release() override {
        const LONG remaining = InterlockedDecrement(&references_);
        if (remaining == 0) {
            delete this;
        }
        return static_cast<ULONG>(remaining);
    }

    HRESULT STDMETHODCALLTYPE GetTypeInfoCount(UINT* const count) override {
        if (count == nullptr) {
            return E_POINTER;
        }
        *count = 0;
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE GetTypeInfo(UINT, LCID, ITypeInfo**) override {
        return E_NOTIMPL;
    }

    HRESULT STDMETHODCALLTYPE GetIDsOfNames(
        REFIID,
        LPOLESTR* const names,
        const UINT count,
        LCID,
        DISPID* const members) override {
        if (names == nullptr || members == nullptr || count != 1 ||
            std::wstring(names[0]) != L"Item") {
            return DISP_E_UNKNOWNNAME;
        }
        members[0] = 1;
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE Invoke(
        const DISPID member,
        REFIID,
        LCID,
        const WORD flags,
        DISPPARAMS* const parameters,
        VARIANT* const result,
        EXCEPINFO*,
        UINT*) override {
        if (member != 1 || (flags & DISPATCH_METHOD) == 0 || result == nullptr ||
            parameters == nullptr || parameters->cArgs != 1 ||
            parameters->rgvarg[0].vt != VT_BSTR) {
            return DISP_E_MEMBERNOTFOUND;
        }
        const std::wstring name(parameters->rgvarg[0].bstrVal);
        VariantInit(result);
        result->vt = VT_I4;
        result->lVal = name == L"List"
            ? list_
            : name == L"Para"
            ? paragraph_
            : name == L"Pos"
            ? character_
            : 0;
        return name == L"List" || name == L"Para" || name == L"Pos"
            ? S_OK
            : DISP_E_MEMBERNOTFOUND;
    }

private:
    ~FakeListParaPosDispatch() = default;

    volatile LONG references_ = 1;
    LONG list_ = 0;
    LONG paragraph_ = 0;
    LONG character_ = 0;
};

class FakeDispatch final
    : public IDispatch,
      public IConnectionPointContainer,
      public IConnectionPoint {
public:
    explicit FakeDispatch(
        const LONG windowHandle = 4242,
        const LONG documentId = 17)
        : windowHandle_(windowHandle),
          documentId_(documentId) {}

    LONG ReferenceCount() noexcept {
        return InterlockedCompareExchange(&references_, 0, 0);
    }

    enum Member : DISPID {
        XHwpDocuments = 1,
        ActiveDocument = 2,
        DocumentId = 3,
        FullName = 4,
        HAction = 5,
        CellShape = 6,
        ParentCtrl = 7,
        CurSelectedCtrl = 8,
        SelectCtrl = 9,
        GetCtrlInstId = 10,
        Run = 11,
        GetAnchorPos = 12,
        Item = 13,
        SetPos = 14,
        HParameterSet = 15,
        HStyle = 16,
        HParaShape = 17,
        HSet = 18,
        GetDefault = 19,
        Execute = 20,
        Apply = 21,
        AlignType = 22,
        LineSpacing = 23,
        LeftMargin = 24,
        RightMargin = 25,
        Indentation = 26,
        PrevSpacing = 27,
        NextSpacing = 28,
        SetMessageBoxMode = 29,
        SelectText = 30,
        GetTextFile = 31,
        SetTextFile = 32,
        InitScan = 33,
        GetText = 34,
        ReleaseScan = 35,
        HInsertText = 36,
        Text = 37,
        ReturnBoolean = 38,
        ReturnInteger = 39,
        ReturnText = 40,
        ReturnVoid = 41,
        CreateAction = 42,
        CreateSet = 43,
        PageCount = 44,
        IsModified = 45,
        SetItem = 46,
        ItemExist = 47,
        XHwpMessageBox = 48,
        Application = 49,
        SetCurMetatagName = 50,
        Password = 51,
        SetID = 52,
        GetSelectedPosBySet = 53,
        HeadCtrl = 54,
        CtrlID = 55,
        Next = 56,
        GetPos = 57,
        Save = 58,
        Clear = 59,
        Open = 60,
        DeleteCtrl = 61,
        HArrayFixture = 62,
        SelectionMode = 63,
        XHwpWindows = 64,
        ActiveWindow = 65,
        WindowHandle = 66,
        FindItem = 67,
        SetActiveDocument = 68,
        SaveAs = 69,
        HCharShape = 70,
        XHwpDocumentInfo = 71,
        CurrentPage = 72,
        Properties = 73,
        KeyIndicator = 74,
        FaceNameHangul = 75,
        CharacterHeight = 76,
        Bold = 77,
        TextColor = 78,
        RatioHangul = 79,
        SpacingHangul = 80,
        LineSpacingType = 81,
        GenericParameter = 82,
        LastCtrl = 83,
        Prev = 84,
        UserDesc = 85,
        GetPageText = 86,
        HwpLineType = 87,
        HwpLineWidth = 88,
        MutateTableTopology = 89,
    };

    bool CompletedLifecycleSequence() const noexcept {
        return saveCalledWithTrue_ && clearCalledWithDiscard_ && openCalledWithArguments_ &&
            lifecycleCalls_.size() == 3 && lifecycleCalls_[0] == L"Save" &&
            lifecycleCalls_[1] == L"Clear" && lifecycleCalls_[2] == L"Open" &&
            activeDocumentFullNameReads_ == 2;
    }

    bool StoppedLifecycleAfterSave() const noexcept {
        return saveCalledWithTrue_ && !clearCalledWithDiscard_ && !openCalledWithArguments_ &&
            lifecycleCalls_.size() == 1 && lifecycleCalls_[0] == L"Save";
    }

    bool StoppedLifecycleBeforeDestructiveReopen() const noexcept {
        return StoppedLifecycleAfterSave() &&
            !lifecycleRecovered_ && !saveAsCalledWithArguments_ &&
            documentOpen_ && !modified_ &&
            fullName_ == lifecyclePath_;
    }

    bool RecoveredLifecycleSession() const noexcept {
        return lifecycleRecovered_ && documentOpen_ && !modified_ &&
            fullName_ == lifecyclePath_;
    }

    bool UsedExpectedLifecycleRecoveryFormat() const noexcept {
        return saveAsCalledWithArguments_;
    }

    bool RecoveredLifecycleFingerprintMismatch() const noexcept {
        return lifecycleMismatchAfterOpen_ && !lifecycleMismatchActive_ &&
            clearCallCount_ == 1 &&
            RecoveredLifecycleSession();
    }

    void ResetLifecycleExpectations() {
        lifecyclePath_ = L"C:\\\uD55C\uAE00\\x.hwp";
        lifecycleReopenedPath_ = L"C:\\\uD55C\uAE00\\X.HWP";
        lifecycleFormat_ = L"HWP";
        saveAsCalledWithArguments_ = false;
        lifecycleRecoveryBlockCaptured_ = true;
        lifecycleMismatchAfterOpen_ = false;
        lifecycleMismatchActive_ = false;
        clearCallCount_ = 0;
    }

    void PrepareLifecycleSuccess() {
        ResetLifecycleExpectations();
        lifecycleCalls_.clear();
        saveCalledWithTrue_ = false;
        clearCalledWithDiscard_ = false;
        openCalledWithArguments_ = false;
        lifecycleRecovered_ = false;
        saveReturnsTrue_ = true;
        saveClearsModified_ = true;
        openReturnsTrue_ = true;
        modified_ = true;
        documentOpen_ = true;
        fullName_ = lifecyclePath_;
        requireActiveDocumentFullName_ = true;
        activeDocumentFullNameReady_ = false;
        activeDocumentFullNameReads_ = 0;
        unstableSerializationMetadataFixture_ = false;
        unstableCaretMetadataFixture_ = true;
        diagnosticSectionMismatchFixture_ = false;
        diagnosticSectionBeforeHwpml_.clear();
        diagnosticSectionAfterHwpml_.clear();
        serializationReads_ = 0;
    }

    void PrepareLifecycleHwpxSuccess() {
        PrepareLifecycleSuccess();
        lifecyclePath_ = L"C:\\\uD55C\uAE00\\x.HwPx";
        lifecycleReopenedPath_ = L"C:\\\uD55C\uAE00\\X.HWPX";
        lifecycleFormat_ = L"HWPX";
        fullName_ = lifecyclePath_;
    }

    void PrepareLifecycleHwpxOpenFailure() {
        PrepareLifecycleHwpxSuccess();
        openReturnsTrue_ = false;
    }

    void PrepareLifecycleRecoveryCaptureFailure() {
        PrepareLifecycleSuccess();
        lifecycleRecoveryBlockCaptured_ = false;
    }

    void PrepareLifecycleOpenSuccessFingerprintMismatch() {
        PrepareLifecycleSuccess();
        lifecycleMismatchAfterOpen_ = true;
    }

    void PrepareLifecycleHwpxOpenSuccessFingerprintMismatch() {
        PrepareLifecycleHwpxSuccess();
        lifecycleMismatchAfterOpen_ = true;
    }

    void PrepareSaveVerifySerializationFixture() {
        PrepareLifecycleSuccess();
        unstableSerializationMetadataFixture_ = true;
        unstableCaretMetadataFixture_ = false;
        fullName_ = kSaveEvidenceFile;
        const HANDLE file = CreateFileW(
            kSaveEvidenceFile,
            GENERIC_WRITE,
            FILE_SHARE_READ,
            nullptr,
            CREATE_ALWAYS,
            FILE_ATTRIBUTE_NORMAL,
            nullptr);
        if (file != INVALID_HANDLE_VALUE) {
            constexpr char content[] = "HWP";
            DWORD written = 0;
            static_cast<void>(WriteFile(
                file,
                content,
                static_cast<DWORD>(sizeof(content) - 1),
                &written,
                nullptr));
            CloseHandle(file);
        }
    }

    void PrepareSaveVerifySectionDiagnosticFixture() {
        PrepareSaveVerifySerializationFixture();
        unstableSerializationMetadataFixture_ = false;
        diagnosticSectionMismatchFixture_ = true;
        constexpr wchar_t prefix[] = L"<HWPML><BODY>";
        diagnosticSectionBeforeHwpml_ = prefix;
        diagnosticSectionBeforeHwpml_.append(8 * 1024 * 1024, L'A');
        diagnosticSectionBeforeHwpml_ += L"</BODY></HWPML>";
        diagnosticSectionAfterHwpml_ = diagnosticSectionBeforeHwpml_;
        diagnosticSectionAfterHwpml_[
            std::size(prefix) - 1 + 4 * 1024 * 1024 + 123] = L'B';
        serializationReads_ = 0;
    }

    void PrepareLifecycleSaveFailure() {
        ResetLifecycleExpectations();
        lifecycleCalls_.clear();
        saveCalledWithTrue_ = false;
        clearCalledWithDiscard_ = false;
        openCalledWithArguments_ = false;
        lifecycleRecovered_ = false;
        saveReturnsTrue_ = true;
        saveClearsModified_ = false;
        openReturnsTrue_ = true;
        modified_ = true;
        documentOpen_ = true;
        fullName_ = lifecyclePath_;
        requireActiveDocumentFullName_ = true;
        activeDocumentFullNameReady_ = false;
        activeDocumentFullNameReads_ = 0;
    }

    void PrepareLifecycleFalseSaveReturn() {
        ResetLifecycleExpectations();
        lifecycleCalls_.clear();
        saveCalledWithTrue_ = false;
        clearCalledWithDiscard_ = false;
        openCalledWithArguments_ = false;
        lifecycleRecovered_ = false;
        saveReturnsTrue_ = false;
        saveClearsModified_ = true;
        openReturnsTrue_ = true;
        modified_ = true;
        documentOpen_ = true;
        fullName_ = lifecyclePath_;
        requireActiveDocumentFullName_ = true;
        activeDocumentFullNameReady_ = false;
        activeDocumentFullNameReads_ = 0;
    }

    void PrepareLifecycleCleanNoOpSave() {
        PrepareLifecycleFalseSaveReturn();
        modified_ = false;
    }

    void PrepareLifecycleOpenFailure() {
        PrepareLifecycleSuccess();
        openReturnsTrue_ = false;
    }

    void RestoreLifecycleFixture() {
        ResetLifecycleExpectations();
        saveReturnsTrue_ = true;
        saveClearsModified_ = true;
        openReturnsTrue_ = true;
        lifecycleRecovered_ = false;
        modified_ = false;
        documentOpen_ = true;
        fullName_ = L"C:\\x.hwp";
        requireActiveDocumentFullName_ = false;
        activeDocumentFullNameReady_ = false;
        activeDocumentFullNameReads_ = 0;
        unstableSerializationMetadataFixture_ = false;
        unstableCaretMetadataFixture_ = false;
        diagnosticSectionMismatchFixture_ = false;
        diagnosticSectionBeforeHwpml_.clear();
        diagnosticSectionAfterHwpml_.clear();
        serializationReads_ = 0;
    }

    bool UsedAnchorSelectionForCopy() const noexcept {
        return copyUsedAnchorSelection_;
    }

    bool PasteUsedSourceAnchorFormat() const noexcept {
        return pasteUsedSourceAnchorFormat_;
    }

    bool UsedNativeTableBlockCopy() const noexcept {
        return nativeTableBlockCopied_;
    }

    bool UsedNativeTableBlockPaste() const noexcept {
        return nativeTableBlockPasted_;
    }

    bool UsedClipboardTableTransfer() const noexcept {
        return clipboardTableTransferUsed_;
    }

    bool RanBreakPage() const noexcept {
        return ranBreakPage_;
    }

    void PrepareSelectCtrlFrontFailure() noexcept {
        failSelectCtrlFront_ = true;
    }

    void RestoreSelectCtrlFrontFixture() noexcept {
        failSelectCtrlFront_ = false;
        ranBreakPage_ = false;
    }

    void PrepareAtomicAppendFailure(const bool failRollback) noexcept {
        atomicAppendFixture_ = true;
        failSelectCtrlFrontWithHresult_ = true;
        failAtomicTailDelete_ = failRollback;
        tailSelection_ = false;
        pageCount_ = 1;
        currentList_ = 0;
        currentParagraph_ = 0;
        currentCharacter_ = 0;
        atomicTail_.clear();
        atomicTailDeletes_ = 0;
        atomicTailMaximumOccurrences_ = 0;
        insertedText_.clear();
        ranBreakPage_ = false;
    }

    void RestoreAtomicAppendFixture() noexcept {
        atomicAppendFixture_ = false;
        failSelectCtrlFrontWithHresult_ = false;
        failAtomicTailDelete_ = false;
        tailSelection_ = false;
        pageCount_ = 1;
        atomicTail_.clear();
        insertedText_.clear();
        ranBreakPage_ = false;
    }

    bool AtomicTailRollbackRestored() const noexcept {
        return atomicTail_.empty() && pageCount_ == 1 && atomicTailDeletes_ == 2 &&
            atomicTailMaximumOccurrences_ == 1;
    }

    bool AtomicTailRollbackRestoredOnce() const noexcept {
        return atomicTail_.empty() && pageCount_ == 1 && atomicTailDeletes_ == 1 &&
            atomicTailMaximumOccurrences_ == 1;
    }

    bool AtomicTailRollbackFailureRetainedTail() const noexcept {
        return !atomicTail_.empty() && pageCount_ == 2 && atomicTailDeletes_ == 0;
    }

    void PrepareCheckpointRestoreFixture(const bool wrongTargetPageCount) {
        checkpointRestoreFixture_ = true;
        checkpointWrongTargetPageCount_ = wrongTargetPageCount;
        checkpointDocumentBlock_ = L"CHECKPOINT_ROLLBACK";
        checkpointSetAttempts_ = 0;
        pageCount_ = 2;
        runActions_.clear();
    }

    bool CheckpointRestoreSucceeded() const noexcept {
        const std::vector<std::wstring> expected{
            L"MoveDocBegin",
            L"SelectAll",
            L"Delete",
            L"MoveDocBegin",
        };
        return checkpointRestoreFixture_ &&
            checkpointSetAttempts_ == 1 &&
            checkpointDocumentBlock_ == L"CHECKPOINT_TARGET" &&
            pageCount_ == 4 &&
            runActions_ == expected;
    }

    bool CheckpointRollbackSucceeded() const noexcept {
        const std::vector<std::wstring> expected{
            L"MoveDocBegin",
            L"SelectAll",
            L"Delete",
            L"MoveDocBegin",
            L"MoveDocBegin",
            L"SelectAll",
            L"Delete",
            L"MoveDocBegin",
        };
        return checkpointRestoreFixture_ &&
            checkpointSetAttempts_ == 2 &&
            checkpointDocumentBlock_ == L"CHECKPOINT_ROLLBACK" &&
            pageCount_ == 2 &&
            runActions_ == expected;
    }

    void RestoreCheckpointRestoreFixture() {
        checkpointRestoreFixture_ = false;
        checkpointWrongTargetPageCount_ = false;
        checkpointDocumentBlock_.clear();
        checkpointSetAttempts_ = 0;
        pageCount_ = 1;
        runActions_.clear();
    }

    void PrepareReferenceLayoutFixture(const bool failAfterFirstText) {
        referenceLayoutFixture_ = true;
        failReferenceLayoutAfterFirstText_ = failAfterFirstText;
        dropReferenceEdges_ = false;
        preflightSelectionFixture_ = false;
        preflightTableControlSelection_ = false;
        failPreflightSelectionRestore_ = false;
        preflightSelectionControlCaptured_ = false;
        preflightSelectionReadOccurred_ = false;
        preflightSelectionRestoreAttempted_ = false;
        preflightSelectionRestored_ = false;
        parameterSet_->EnableGenericProperties(true);
        atomicAppendFixture_ = true;
        failSelectCtrlFrontWithHresult_ = false;
        failAtomicTailDelete_ = false;
        tailSelection_ = false;
        tableSelected_ = false;
        referenceTableExists_ = false;
        referenceMerged_ = false;
        pageCount_ = 1;
        currentList_ = 0;
        currentParagraph_ = 108;
        currentCharacter_ = 0;
        selectionMode_ = 0;
        selectedStartList_ = 0;
        selectedStartParagraph_ = 0;
        selectedStartCharacter_ = 0;
        selectedEndList_ = 0;
        selectedEndParagraph_ = 0;
        selectedEndCharacter_ = 0;
        atomicTail_.clear();
        atomicTailDeletes_ = 0;
        atomicTailMaximumOccurrences_ = 0;
        insertedText_.clear();
        referenceTextInsertions_ = 0;
        referenceParagraphBreaks_ = 0;
        referenceEdgeExecutions_ = 0;
        referenceEdgeApplications_ = 0;
        referenceBorderReadbacks_ = 0;
        referenceTopologyInspections_ = 0;
        referenceTopologyChanged_ = false;
        referenceRunTopologyMutated_ = false;
        referenceActionTopologyMutated_ = false;
        referenceCallTopologyMutated_ = false;
        staleTopologyCellPositionAttempted_ = false;
        parameterSet_->ClearGenericValues();
        parameterSet_->ResetGenericValueReads();
        referenceFillApplications_ = 0;
        referenceControlDeletes_ = 0;
        referenceCells_.clear();
        InitializeReferenceCells();
    }

    void RestoreReferenceLayoutFixture() noexcept {
        referenceLayoutFixture_ = false;
        emptyCellTextFixture_ = false;
        emptyCellSelectAll_ = false;
        preflightSelectionFixture_ = false;
        preflightTableControlSelection_ = false;
        failPreflightSelectionRestore_ = false;
        preflightSelectionControlCaptured_ = false;
        preflightSelectionReadOccurred_ = false;
        preflightSelectionRestoreAttempted_ = false;
        preflightSelectionRestored_ = false;
        failReferenceLayoutAfterFirstText_ = false;
        dropReferenceEdges_ = false;
        parameterSet_->EnableGenericProperties(false);
        atomicAppendFixture_ = false;
        tailSelection_ = false;
        tableSelected_ = false;
        referenceTableExists_ = false;
        referenceMerged_ = false;
        referenceTopologyInspections_ = 0;
        referenceTopologyChanged_ = false;
        referenceRunTopologyMutated_ = false;
        referenceActionTopologyMutated_ = false;
        referenceCallTopologyMutated_ = false;
        staleTopologyCellPositionAttempted_ = false;
        pageCount_ = 1;
        currentList_ = 0;
        currentParagraph_ = 0;
        currentCharacter_ = 0;
        selectionMode_ = 1;
        atomicTail_.clear();
        insertedText_.clear();
        referenceCells_.clear();
        referenceParagraphBreaks_ = 0;
    }

    void PrepareReferenceLayoutDroppedEdgesFixture() {
        PrepareReferenceLayoutFixture(false);
        dropReferenceEdges_ = true;
    }

    bool ReferenceLayoutCreatedExactly() const noexcept {
        const ReferenceCell* const a1 = ReferenceCellByAddress(L"A1");
        const ReferenceCell* const a2 = ReferenceCellByAddress(L"A2");
        const ReferenceCell* const b2 = ReferenceCellByAddress(L"B2");
        const ReferenceCell* const a3 = ReferenceCellByAddress(L"A3");
        const ReferenceCell* const b3 = ReferenceCellByAddress(L"B3");
        return referenceLayoutFixture_ && referenceTableExists_ && referenceMerged_ &&
            pageCount_ == 2 && ReferenceActiveCellCount() == 5 &&
            a1 != nullptr && a1->columnSpan == 2 && a1->text == L"Merged header" &&
            a2 != nullptr && a2->text == L"A2" &&
            b2 != nullptr && b2->text == L"B2" &&
            a3 != nullptr && a3->text == L"A3" &&
            b3 != nullptr && b3->text == L"B3" &&
            ReferenceFormatMatches(a1->format, HeaderReferenceFormat()) &&
            ReferenceFormatMatches(a2->format, BodyReferenceFormat()) &&
            ReferenceFormatMatches(b2->format, BodyReferenceFormat()) &&
            ReferenceFormatMatches(a3->format, BodyReferenceFormat()) &&
            ReferenceFormatMatches(b3->format, BodyReferenceFormat()) &&
            referenceTextInsertions_ == 5 && referenceEdgeApplications_ == 15 &&
            referenceFillApplications_ == 1 && referenceControlDeletes_ == 0;
    }

    bool ReferenceBorderReadbackCoveredRequestedEdges() const noexcept {
        return referenceEdgeExecutions_ == 15 &&
            referenceEdgeApplications_ == 15 &&
            referenceBorderReadbacks_ == 5 &&
            parameterSet_->GenericValueReads() == 45;
    }

    bool ReferenceLayoutDroppedEdgesObserved() const noexcept {
        return referenceEdgeExecutions_ == 15 &&
            referenceEdgeApplications_ == 0 &&
            referenceBorderReadbacks_ == 1 &&
            parameterSet_->GenericValueReads() == 2;
    }

    bool ReferenceLayoutRollbackRestored() const noexcept {
        return referenceLayoutFixture_ && !referenceTableExists_ && pageCount_ == 1 &&
            atomicTail_.empty() && referenceControlDeletes_ == 1 &&
            atomicTailDeletes_ == 1 && referenceTextInsertions_ == 1;
    }

    void PrepareEmptyCellTextFixture() {
        PrepareReferenceLayoutFixture(false);
        emptyCellTextFixture_ = true;
        emptyCellSelectAll_ = false;
        referenceTableExists_ = true;
        atomicAppendFixture_ = false;
        static_cast<void>(SetReferenceCurrentCell(L"A2"));
        selectionMode_ = 0;
        selectedStartList_ = 0;
        selectedStartParagraph_ = 0;
        selectedStartCharacter_ = 0;
        selectedEndList_ = 0;
        selectedEndParagraph_ = 0;
        selectedEndCharacter_ = 0;
    }

    bool EmptyCellTextFixtureReady() const noexcept {
        const ReferenceCell* const cell = ReferenceCellByAddress(L"A2");
        return emptyCellTextFixture_ && cell != nullptr && cell->text.empty() &&
            currentList_ == 650 && currentParagraph_ == 0 && currentCharacter_ == 0 &&
            selectionMode_ == 0 && selectedStartList_ == 0 &&
            selectedStartParagraph_ == 0 && selectedStartCharacter_ == 0;
    }

    bool EmptyCellTextInsertedExactly() const noexcept {
        const ReferenceCell* const cell = ReferenceCellByAddress(L"A2");
        return cell != nullptr && cell->text == L"filled" &&
            referenceTextInsertions_ == 1;
    }

    void PrepareMultilineCellTextFixture(const bool patchText) {
        PrepareReferenceLayoutFixture(false);
        referenceTableExists_ = true;
        atomicAppendFixture_ = false;
        const std::wstring address = patchText ? L"B2" : L"A2";
        ReferenceCell* const cell = ReferenceCellByAddress(address);
        if (patchText && cell != nullptr) {
            cell->text = L"old";
        }
        static_cast<void>(SetReferenceCurrentCell(address));
        selectionMode_ = 0;
    }

    bool MultilineCellTextWrittenExactly(
        const std::wstring& address,
        const std::wstring& expected) const noexcept {
        const ReferenceCell* const cell = ReferenceCellByAddress(address);
        return cell != nullptr && cell->text == expected &&
            referenceTextInsertions_ == 2 && referenceParagraphBreaks_ == 1;
    }

    void PrepareAllTargetCellTextPreflightFixture() {
        PrepareReferenceLayoutFixture(false);
        referenceTableExists_ = true;
        atomicAppendFixture_ = false;
        ReferenceCell* const later = ReferenceCellByAddress(L"B2");
        if (later != nullptr) {
            later->text = L"live";
        }
        static_cast<void>(SetReferenceCurrentCell(L"A2"));
        selectionMode_ = 0;
    }

    bool AllTargetCellTextPreflightPreserved() const noexcept {
        const ReferenceCell* const earlier = ReferenceCellByAddress(L"A2");
        const ReferenceCell* const later = ReferenceCellByAddress(L"B2");
        return earlier != nullptr && earlier->text.empty() &&
            later != nullptr && later->text == L"live" &&
            ReferenceFormatMatches(earlier->format, BodyReferenceFormat()) &&
            ReferenceFormatMatches(later->format, BodyReferenceFormat()) &&
            referenceTextInsertions_ == 0 && referenceParagraphBreaks_ == 0;
    }

    void PrepareTopologyReuseFixture() {
        PrepareReferenceLayoutFixture(false);
        referenceTableExists_ = true;
        atomicAppendFixture_ = false;
        ReferenceCell* const first = ReferenceCellByAddress(L"A2");
        ReferenceCell* const second = ReferenceCellByAddress(L"B2");
        if (first != nullptr) {
            first->text = L"old";
        }
        if (second != nullptr) {
            second->text = L"old";
        }
        static_cast<void>(SetReferenceCurrentCell(L"A2"));
        selectionMode_ = 0;
    }

    bool TopologyWasReusedAcrossTextBatch() const noexcept {
        const ReferenceCell* const first = ReferenceCellByAddress(L"A2");
        const ReferenceCell* const second = ReferenceCellByAddress(L"B2");
        return first != nullptr && first->text == L"new-a" &&
            second != nullptr && second->text == L"new-b" &&
            referenceTopologyInspections_ == 2;
    }

    void PrepareFormattingTopologyPolicyFixture() {
        PrepareReferenceLayoutFixture(false);
        referenceTableExists_ = true;
        atomicAppendFixture_ = false;
        static_cast<void>(SetReferenceCurrentCell(L"A1"));
        selectionMode_ = 0;
    }

    bool TopologyWasPreservedAfterCellFormatting() const noexcept {
        const ReferenceCell* const b1 = ReferenceCellByAddress(L"B1");
        return b1 != nullptr && currentList_ == b1->listId &&
            referenceTopologyInspections_ == 1;
    }

    bool TopologyWasPreservedAfterCellPadding() const noexcept {
        const ReferenceCell* const b1 = ReferenceCellByAddress(L"B1");
        return b1 != nullptr && currentList_ == b1->listId &&
            referenceTopologyInspections_ == 1;
    }

    bool TopologyWasInvalidatedAfterCellSizeChange() const noexcept {
        const ReferenceCell* const b1 = ReferenceCellByAddress(L"B1");
        return b1 != nullptr && currentList_ == b1->listId &&
            referenceTopologyInspections_ == 2;
    }

    bool TopologyWasInvalidatedAfterUnknownAction() const noexcept {
        const ReferenceCell* const b1 = ReferenceCellByAddress(L"B1");
        return b1 != nullptr && currentList_ == b1->listId &&
            referenceTopologyInspections_ == 2;
    }

    void PrepareTopologyInvalidationFixture() {
        PrepareReferenceLayoutFixture(false);
        referenceTableExists_ = true;
        atomicAppendFixture_ = false;
        static_cast<void>(SetReferenceCurrentCell(L"A1"));
        selectionMode_ = 0;
    }

    bool TopologyWasInvalidatedAfterMerge() const noexcept {
        return referenceMerged_ &&
            ReferenceCellByAddress(L"B1") == nullptr &&
            referenceTopologyInspections_ == 2 &&
            !staleTopologyCellPositionAttempted_;
    }

    void PrepareRunTopologyInvalidationFixture() {
        PrepareReferenceLayoutFixture(false);
        referenceTableExists_ = true;
        atomicAppendFixture_ = false;
        static_cast<void>(SetReferenceCurrentCell(L"A1"));
        selectionMode_ = 0;
    }

    bool TopologyWasInvalidatedAfterRunDeleteRow() const noexcept {
        const ReferenceCell* const b1 = ReferenceCellByAddress(L"B1");
        return referenceRunTopologyMutated_ &&
            b1 != nullptr && b1->listId == 651 && currentList_ == b1->listId &&
            referenceTopologyInspections_ == 2 &&
            !staleTopologyCellPositionAttempted_;
    }

    void PrepareActionTopologyInvalidationFixture() {
        PrepareReferenceLayoutFixture(false);
        referenceTableExists_ = true;
        atomicAppendFixture_ = false;
        for (ReferenceCell& cell : referenceCells_) {
            if (cell.column == 2) {
                cell.active = false;
            }
        }
        static_cast<void>(SetReferenceCurrentCell(L"A1"));
        selectionMode_ = 0;
    }

    bool TopologyWasInvalidatedAfterActionInsertColumn() const noexcept {
        const ReferenceCell* const b1 = ReferenceCellByAddress(L"B1");
        return referenceActionTopologyMutated_ &&
            b1 != nullptr && currentList_ == b1->listId &&
            referenceTopologyInspections_ == 2 &&
            !staleTopologyCellPositionAttempted_;
    }

    void PrepareCallTopologyInvalidationFixture() {
        PrepareReferenceLayoutFixture(false);
        referenceTableExists_ = true;
        atomicAppendFixture_ = false;
        static_cast<void>(SetReferenceCurrentCell(L"A1"));
        selectionMode_ = 0;
    }

    bool TopologyWasInvalidatedAfterCall() const noexcept {
        return referenceCallTopologyMutated_ &&
            ReferenceCellByAddress(L"B1") == nullptr &&
            referenceTopologyInspections_ == 2 &&
            !staleTopologyCellPositionAttempted_;
    }

    void PrepareOversizedCellPatchFixture() {
        PrepareReferenceLayoutFixture(false);
        referenceTableExists_ = true;
        atomicAppendFixture_ = false;
        ReferenceCell* const cell = ReferenceCellByAddress(L"B2");
        if (cell != nullptr) {
            cell->text = L"old";
        }
        static_cast<void>(SetReferenceCurrentCell(L"A2"));
        selectionMode_ = 0;
    }

    bool OversizedCellPatchPreservedDocument() const noexcept {
        const ReferenceCell* const cell = ReferenceCellByAddress(L"B2");
        return cell != nullptr && cell->text == L"old" &&
            referenceTopologyInspections_ == 0 &&
            referenceTextInsertions_ == 0;
    }

    void PrepareTableTextPreflightSelectionFixture(
        const bool tableControlSelection,
        const bool failRestore) {
        PrepareReferenceLayoutFixture(false);
        referenceTableExists_ = true;
        atomicAppendFixture_ = false;
        preflightSelectionFixture_ = true;
        preflightTableControlSelection_ = tableControlSelection;
        failPreflightSelectionRestore_ = failRestore;
        preflightSelectionControlCaptured_ = false;
        preflightSelectionReadOccurred_ = false;
        preflightSelectionRestoreAttempted_ = false;
        preflightSelectionRestored_ = false;
        ReferenceCell* const cell = ReferenceCellByAddress(L"A2");
        if (cell != nullptr) {
            cell->text = L"old";
        }
        static_cast<void>(SetReferenceCurrentCell(
            tableControlSelection ? L"A2" : L"B2"));
        selectedStartList_ = 650;
        selectedStartParagraph_ = 0;
        selectedStartCharacter_ = 0;
        selectedEndList_ = tableControlSelection ? 650 : 651;
        selectedEndParagraph_ = 0;
        selectedEndCharacter_ = 0;
        referenceSelectionAnchorList_ = selectedStartList_;
        selectionMode_ = tableControlSelection ? 4L : 0x13L;
        tableSelected_ = tableControlSelection;
    }

    bool TableTextPreflightSelectionBatchSucceeded() const noexcept {
        const ReferenceCell* const cell = ReferenceCellByAddress(L"A2");
        return preflightSelectionControlCaptured_ &&
            preflightSelectionReadOccurred_ &&
            preflightSelectionRestoreAttempted_ &&
            preflightSelectionRestored_ &&
            cell != nullptr && cell->text == L"new" &&
            referenceTextInsertions_ == 1 && referenceParagraphBreaks_ == 0;
    }

    bool TableTextPreflightRestoreFailurePreservedDocument() const noexcept {
        const ReferenceCell* const cell = ReferenceCellByAddress(L"A2");
        return preflightSelectionControlCaptured_ &&
            preflightSelectionReadOccurred_ &&
            preflightSelectionRestoreAttempted_ &&
            !preflightSelectionRestored_ &&
            cell != nullptr && cell->text == L"old" &&
            ReferenceFormatMatches(cell->format, BodyReferenceFormat()) &&
            referenceTextInsertions_ == 0 && referenceParagraphBreaks_ == 0;
    }

    bool UsedAutoYesMessageBoxMode() const noexcept {
        return autoYesMessageBoxModeUsed_;
    }

    const std::wstring& InsertedText() const noexcept {
        return insertedText_;
    }

    size_t InsertTextExecutions() const noexcept {
        return insertTextExecutions_;
    }

    void PrepareSelectedControlTextPatch() noexcept {
        selectionMode_ = 4;
        insertedText_.clear();
        insertTextExecutions_ = 0;
    }

    void RestoreTextPatchSelectionFixture() noexcept {
        selectionMode_ = 1;
        insertedText_.clear();
    }

    void PrepareTextDeletionPatch() noexcept {
        textDeletionFixture_ = true;
        textSelectionActive_ = true;
        selectionMode_ = 1;
        currentList_ = 0;
        currentParagraph_ = 108;
        currentCharacter_ = 0;
        insertedText_.clear();
        insertTextExecutions_ = 0;
        textDeletionActions_ = 0;
    }

    bool DeletedSelectedText() const noexcept {
        return textDeletionFixture_ && !textSelectionActive_ &&
            selectionMode_ == 0 && textDeletionActions_ == 1 &&
            insertTextExecutions_ == 0;
    }

    void RestoreTextDeletionPatch() noexcept {
        textDeletionFixture_ = false;
        textSelectionActive_ = true;
        selectionMode_ = 1;
        textDeletionActions_ = 0;
    }

    void PrepareScopedInspectionFixture() {
        inspectionScopeFixture_ = true;
        inspectionControls_ = {
            {L"gso", L"page-1-shape", L"shape", 1},
            {L"gso", L"page-29-shape", L"shape", 29},
            {L"tbl", L"page-30-table", L"table", 30},
            {L"gso", L"page-31-shape-a", L"shape", 31},
            {L"gso", L"page-31-shape-b", L"shape", 31},
        };
        inspectionControlIndex_ = -1;
        inspectionAnchorRoot_ = false;
        inspectionCurrentPage_ = 30;
        inspectionHeadReads_ = 0;
        inspectionLastReads_ = 0;
        inspectionNextReads_ = 0;
        inspectionPrevReads_ = 0;
        inspectionIdentityReads_ = 0;
        pageCount_ = 31;
        currentList_ = 31;
        currentParagraph_ = 0;
        currentCharacter_ = 0;
        selectionMode_ = 0;
        modified_ = false;
        documentOpen_ = true;
        fullName_ = L"C:\\x.hwp";
    }

    bool UsedBoundedBackwardInspection() const noexcept {
        return inspectionLastReads_ == 1 && inspectionPrevReads_ == 3 &&
            inspectionIdentityReads_ >= 4;
    }

    bool PreservedUnscopedForwardInspection() const noexcept {
        return inspectionHeadReads_ >= 1 && inspectionLastReads_ == 0 &&
            inspectionNextReads_ >= inspectionControls_.size() &&
            inspectionPrevReads_ == 0 &&
            inspectionIdentityReads_ >= inspectionControls_.size();
    }

    void RestoreScopedInspectionFixture() noexcept {
        inspectionScopeFixture_ = false;
        inspectionControls_.clear();
        inspectionControlIndex_ = -1;
        inspectionAnchorRoot_ = false;
        pageCount_ = 1;
        currentList_ = 0;
        currentParagraph_ = 108;
        currentCharacter_ = 0;
        selectionMode_ = 1;
    }

    bool MessageBoxModeRestored() const noexcept {
        return messageBoxMode_ == kInitialMessageBoxMode;
    }

    void PrepareTransientMessageBoxModeRestoreFailure() noexcept {
        messageBoxMode_ = kInitialMessageBoxMode;
        failNextMessageBoxModeRestore_ = true;
        messageBoxModeRestoreAttempts_ = 0;
    }

    bool RetriedMessageBoxModeRestore() const noexcept {
        return !failNextMessageBoxModeRestore_ &&
            messageBoxModeRestoreAttempts_ == 2 &&
            MessageBoxModeRestored();
    }

    bool UsedStreamingTextScan() const noexcept {
        return scanStarted_ && scanReleased_;
    }

    bool PreservedAttachedCaptionNumber() const noexcept {
        const auto attached = std::find(
            runActions_.begin(), runActions_.end(), L"ShapeObjAttachCaption");
        const auto moved = std::find(
            attached, runActions_.end(), L"MoveParaEnd");
        const auto selected = std::find(moved, runActions_.end(), L"SelectAll");
        const auto closed = std::find(selected, runActions_.end(), L"CloseEx");
        return attached != runActions_.end() &&
            moved != runActions_.end() &&
            selected != runActions_.end() &&
            closed != runActions_.end() &&
            std::find(attached, closed, L"Delete") == closed;
    }

    HRESULT STDMETHODCALLTYPE QueryInterface(
        REFIID interfaceId,
        void** const object) override {
        if (object == nullptr) {
            return E_POINTER;
        }
        if (interfaceId == IID_IUnknown || interfaceId == IID_IDispatch) {
            *object = static_cast<IDispatch*>(this);
            static_cast<void>(AddRef());
            return S_OK;
        }
        if (interfaceId == IID_IConnectionPointContainer) {
            *object = static_cast<IConnectionPointContainer*>(this);
            static_cast<void>(AddRef());
            return S_OK;
        }
        if (interfaceId == IID_IConnectionPoint) {
            *object = static_cast<IConnectionPoint*>(this);
            static_cast<void>(AddRef());
            return S_OK;
        }
        *object = nullptr;
        return E_NOINTERFACE;
    }

    ULONG STDMETHODCALLTYPE AddRef() override {
        return static_cast<ULONG>(InterlockedIncrement(&references_));
    }

    ULONG STDMETHODCALLTYPE Release() override {
        const LONG remaining = InterlockedDecrement(&references_);
        if (remaining == 0) {
            delete this;
        }
        return static_cast<ULONG>(remaining);
    }

    HRESULT STDMETHODCALLTYPE GetTypeInfoCount(UINT* const count) override {
        if (count == nullptr) {
            return E_POINTER;
        }
        *count = 0;
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE GetTypeInfo(
        UINT,
        LCID,
        ITypeInfo**) override {
        return E_NOTIMPL;
    }

    HRESULT STDMETHODCALLTYPE GetIDsOfNames(
        REFIID,
        LPOLESTR* const names,
        const UINT count,
        LCID,
        DISPID* const members) override {
        if (names == nullptr || members == nullptr || count != 1) {
            return E_INVALIDARG;
        }
        const std::wstring name(names[0]);
        if (name == L"XHwpDocuments") {
            members[0] = XHwpDocuments;
        } else if (name == L"Active_XHwpDocument") {
            members[0] = ActiveDocument;
        } else if (name == L"DocumentID") {
            members[0] = DocumentId;
        } else if (name == L"FullName") {
            if (requireActiveDocumentFullName_ && !activeDocumentFullNameReady_) {
                return DISP_E_UNKNOWNNAME;
            }
            members[0] = FullName;
        } else if (name == L"HAction") {
            members[0] = HAction;
        } else if (name == L"CellShape") {
            members[0] = CellShape;
        } else if (name == L"ParentCtrl") {
            members[0] = ParentCtrl;
        } else if (name == L"CurSelectedCtrl") {
            members[0] = CurSelectedCtrl;
        } else if (name == L"SelectCtrl") {
            members[0] = SelectCtrl;
        } else if (name == L"GetCtrlInstID") {
            members[0] = GetCtrlInstId;
        } else if (name == L"Run") {
            members[0] = Run;
        } else if (name == L"GetAnchorPos") {
            members[0] = GetAnchorPos;
        } else if (name == L"Item") {
            members[0] = Item;
        } else if (name == L"SetPos") {
            members[0] = SetPos;
        } else if (name == L"HParameterSet") {
            members[0] = HParameterSet;
        } else if (name == L"HStyle") {
            members[0] = HStyle;
        } else if (name == L"HParaShape") {
            members[0] = HParaShape;
        } else if (referenceLayoutFixture_ && name == L"HCharShape") {
            members[0] = HCharShape;
        } else if (name == L"HSet") {
            members[0] = HSet;
        } else if (name == L"GetDefault") {
            members[0] = GetDefault;
        } else if (name == L"Execute") {
            members[0] = Execute;
        } else if (name == L"Apply") {
            members[0] = Apply;
        } else if (name == L"AlignType") {
            members[0] = AlignType;
        } else if (name == L"LineSpacing") {
            members[0] = LineSpacing;
        } else if (name == L"LeftMargin") {
            members[0] = LeftMargin;
        } else if (name == L"RightMargin") {
            members[0] = RightMargin;
        } else if (name == L"Indentation") {
            members[0] = Indentation;
        } else if (name == L"PrevSpacing") {
            members[0] = PrevSpacing;
        } else if (name == L"NextSpacing") {
            members[0] = NextSpacing;
        } else if (name == L"SetMessageBoxMode") {
            members[0] = SetMessageBoxMode;
        } else if (name == L"SelectText") {
            members[0] = SelectText;
        } else if (name == L"GetTextFile") {
            members[0] = GetTextFile;
        } else if (name == L"SetTextFile") {
            members[0] = SetTextFile;
        } else if (name == L"InitScan") {
            members[0] = InitScan;
        } else if (name == L"GetText") {
            members[0] = GetText;
        } else if (name == L"ReleaseScan") {
            members[0] = ReleaseScan;
        } else if (name == L"HInsertText") {
            members[0] = HInsertText;
        } else if (name == L"HArrayFixture") {
            members[0] = HArrayFixture;
        } else if (name == L"SelectionMode") {
            members[0] = SelectionMode;
        } else if (name == L"XHwpWindows") {
            members[0] = XHwpWindows;
        } else if (name == L"Active_XHwpWindow") {
            members[0] = ActiveWindow;
        } else if (name == L"WindowHandle") {
            members[0] = WindowHandle;
        } else if (name == L"FindItem") {
            members[0] = FindItem;
        } else if (name == L"SetActive_XHwpDocument") {
            members[0] = SetActiveDocument;
        } else if (name == L"Text") {
            members[0] = Text;
        } else if (name == L"ReturnBoolean") {
            members[0] = ReturnBoolean;
        } else if (name == L"ReturnInteger") {
            members[0] = ReturnInteger;
        } else if (name == L"ReturnText") {
            members[0] = ReturnText;
        } else if (name == L"ReturnVoid") {
            members[0] = ReturnVoid;
        } else if (name == L"CreateAction") {
            members[0] = CreateAction;
        } else if (name == L"CreateSet") {
            members[0] = CreateSet;
        } else if (name == L"PageCount") {
            members[0] = PageCount;
        } else if (name == L"IsModified") {
            members[0] = IsModified;
        } else if (name == L"SetItem") {
            members[0] = SetItem;
        } else if (name == L"ItemExist") {
            members[0] = ItemExist;
        } else if (name == L"XHwpMessageBox") {
            members[0] = XHwpMessageBox;
        } else if (name == L"Application") {
            members[0] = Application;
        } else if (name == L"SetCurMetatagName") {
            members[0] = SetCurMetatagName;
        } else if (name == L"Password") {
            members[0] = Password;
        } else if (name == L"SetID") {
            members[0] = SetID;
        } else if (name == L"GetSelectedPosBySet") {
            members[0] = GetSelectedPosBySet;
        } else if (name == L"HeadCtrl") {
            members[0] = HeadCtrl;
        } else if (inspectionScopeFixture_ && name == L"LastCtrl") {
            members[0] = LastCtrl;
        } else if (name == L"CtrlID") {
            members[0] = CtrlID;
        } else if (name == L"Next") {
            members[0] = Next;
        } else if (inspectionScopeFixture_ && name == L"Prev") {
            members[0] = Prev;
        } else if (inspectionScopeFixture_ && name == L"UserDesc") {
            members[0] = UserDesc;
        } else if (inspectionScopeFixture_ && name == L"GetPageText") {
            members[0] = GetPageText;
        } else if (name == L"GetPos") {
            members[0] = GetPos;
        } else if (name == L"Save") {
            members[0] = Save;
        } else if (name == L"SaveAs") {
            members[0] = SaveAs;
        } else if ((referenceLayoutFixture_ || inspectionScopeFixture_) &&
                   name == L"XHwpDocumentInfo") {
            members[0] = XHwpDocumentInfo;
        } else if ((referenceLayoutFixture_ || inspectionScopeFixture_) &&
                   name == L"CurrentPage") {
            members[0] = CurrentPage;
        } else if (referenceLayoutFixture_ && name == L"Properties") {
            members[0] = Properties;
        } else if (referenceLayoutFixture_ && name == L"KeyIndicator") {
            members[0] = KeyIndicator;
        } else if (referenceLayoutFixture_ && name == L"FaceNameHangul") {
            members[0] = FaceNameHangul;
        } else if (referenceLayoutFixture_ && name == L"Height") {
            members[0] = CharacterHeight;
        } else if (referenceLayoutFixture_ && name == L"Bold") {
            members[0] = Bold;
        } else if (referenceLayoutFixture_ && name == L"TextColor") {
            members[0] = TextColor;
        } else if (referenceLayoutFixture_ && name == L"RatioHangul") {
            members[0] = RatioHangul;
        } else if (referenceLayoutFixture_ && name == L"SpacingHangul") {
            members[0] = SpacingHangul;
        } else if (referenceLayoutFixture_ && name == L"LineSpacingType") {
            members[0] = LineSpacingType;
        } else if (referenceLayoutFixture_ && name == L"HwpLineType") {
            members[0] = HwpLineType;
        } else if (referenceLayoutFixture_ && name == L"HwpLineWidth") {
            members[0] = HwpLineWidth;
        } else if (referenceLayoutFixture_ && name == L"MutateTableTopology") {
            members[0] = MutateTableTopology;
        } else if (name == L"Clear") {
            members[0] = Clear;
        } else if (name == L"Open") {
            members[0] = Open;
        } else if (name == L"DeleteCtrl") {
            members[0] = DeleteCtrl;
        } else if (referenceLayoutFixture_) {
            members[0] = GenericParameter;
        } else {
            return DISP_E_UNKNOWNNAME;
        }
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE Invoke(
        const DISPID member,
        REFIID,
        LCID,
        const WORD flags,
        DISPPARAMS* const parameters,
        VARIANT* result,
        EXCEPINFO*,
        UINT*) override {
        if ((flags & (DISPATCH_PROPERTYPUT | DISPATCH_PROPERTYPUTREF)) != 0) {
            if (parameters == nullptr || parameters->cArgs != 1) {
                return DISP_E_TYPEMISMATCH;
            }
            if (member == Text) {
                if (parameters->rgvarg[0].vt != VT_BSTR) {
                    return DISP_E_TYPEMISMATCH;
                }
                insertedText_.assign(
                    parameters->rgvarg[0].bstrVal,
                    SysStringLen(parameters->rgvarg[0].bstrVal));
                return S_OK;
            }
            if (referenceLayoutFixture_) {
                const VARIANT& value = parameters->rgvarg[0];
                if (member == GenericParameter || member == Properties) {
                    return S_OK;
                }
                if (member == FaceNameHangul) {
                    if (value.vt != VT_BSTR) {
                        return DISP_E_TYPEMISMATCH;
                    }
                    pendingReferenceFormat_.faceName.assign(
                        value.bstrVal,
                        SysStringLen(value.bstrVal));
                    return S_OK;
                }
                if (member == Bold) {
                    if (value.vt != VT_BOOL) {
                        return DISP_E_TYPEMISMATCH;
                    }
                    pendingReferenceFormat_.bold = value.boolVal != VARIANT_FALSE;
                    return S_OK;
                }
                if (value.vt == VT_I4) {
                    if (member == CharacterHeight) {
                        pendingReferenceFormat_.height = value.lVal;
                        return S_OK;
                    }
                    if (member == TextColor) {
                        pendingReferenceFormat_.textColor = value.lVal;
                        return S_OK;
                    }
                    if (member == RatioHangul) {
                        pendingReferenceFormat_.widthRatio = value.lVal;
                        return S_OK;
                    }
                    if (member == SpacingHangul) {
                        pendingReferenceFormat_.letterSpacing = value.lVal;
                        return S_OK;
                    }
                    if (member == AlignType) {
                        pendingReferenceFormat_.alignment = value.lVal;
                        return S_OK;
                    }
                    if (member == LineSpacingType) {
                        pendingReferenceFormat_.lineSpacingType = value.lVal;
                        return S_OK;
                    }
                    if (member == LineSpacing) {
                        pendingReferenceFormat_.lineSpacing = value.lVal;
                        return S_OK;
                    }
                    if (member == LeftMargin) {
                        pendingReferenceFormat_.leftMargin = value.lVal;
                        return S_OK;
                    }
                    if (member == RightMargin) {
                        pendingReferenceFormat_.rightMargin = value.lVal;
                        return S_OK;
                    }
                    if (member == Indentation) {
                        pendingReferenceFormat_.indentation = value.lVal;
                        return S_OK;
                    }
                    if (member == PrevSpacing) {
                        pendingReferenceFormat_.previousSpacing = value.lVal;
                        return S_OK;
                    }
                    if (member == NextSpacing) {
                        pendingReferenceFormat_.nextSpacing = value.lVal;
                        return S_OK;
                    }
                }
            }
            if (parameters->rgvarg[0].vt != VT_I4) {
                return DISP_E_TYPEMISMATCH;
            }
            const LONG value = parameters->rgvarg[0].lVal;
            if (member == Apply) {
                pendingProfile_.styleId = value;
            } else if (member == AlignType) {
                pendingProfile_.alignment = value;
            } else if (member == LineSpacing) {
                pendingProfile_.lineSpacing = value;
            } else if (member == LeftMargin) {
                pendingProfile_.leftMargin = value;
            } else if (member == RightMargin) {
                pendingProfile_.rightMargin = value;
            } else if (member == Indentation) {
                pendingProfile_.indentation = value;
            } else if (member == PrevSpacing) {
                pendingProfile_.previousSpacing = value;
            } else if (member == NextSpacing) {
                pendingProfile_.nextSpacing = value;
            } else {
                return DISP_E_MEMBERNOTFOUND;
            }
            return S_OK;
        }
        CComVariant ignoredResult;
        if (result == nullptr && (flags & DISPATCH_METHOD) != 0 &&
            (member == Run || member == SetItem || member == ReturnVoid ||
             member == ReleaseScan || member == GetPos || member == KeyIndicator)) {
            result = &ignoredResult;
        }
        if (result == nullptr) {
            return E_POINTER;
        }
        VariantInit(result);
        if ((flags & DISPATCH_PROPERTYGET) != 0) {
            if (preflightSelectionFixture_ &&
                !preflightSelectionReadOccurred_ &&
                ((preflightTableControlSelection_ &&
                  member == CurSelectedCtrl &&
                  (selectionMode_ & 0x0F) == 4) ||
                 (!preflightTableControlSelection_ &&
                  member == ParentCtrl &&
                  (selectionMode_ & 0x0F) == 3))) {
                preflightSelectionControlCaptured_ = true;
            }
            if (member == HSet) {
                result->vt = VT_DISPATCH;
                result->pdispVal = parameterSet_;
                static_cast<void>(parameterSet_->AddRef());
                return S_OK;
            }
            if (member == GenericParameter && referenceLayoutFixture_) {
                result->vt = VT_DISPATCH;
                result->pdispVal = parameterSet_;
                static_cast<void>(parameterSet_->AddRef());
                return S_OK;
            }
            if (member == XHwpDocuments || member == ActiveDocument ||
                member == XHwpWindows || member == ActiveWindow ||
                member == HeadCtrl || member == LastCtrl ||
                member == HAction || member == CurSelectedCtrl ||
                member == HParameterSet ||
                member == XHwpMessageBox || member == Application ||
                member == XHwpDocumentInfo || member == Properties) {
                if (inspectionScopeFixture_ &&
                    (member == HeadCtrl || member == LastCtrl)) {
                    if (inspectionControls_.empty()) {
                        result->vt = VT_EMPTY;
                        return S_OK;
                    }
                    inspectionControlIndex_ = member == HeadCtrl
                        ? 0
                        : static_cast<LONG>(inspectionControls_.size() - 1);
                    if (member == HeadCtrl) {
                        ++inspectionHeadReads_;
                    } else {
                        ++inspectionLastReads_;
                    }
                }
                if (member == HeadCtrl &&
                    (!documentOpen_ ||
                     (referenceLayoutFixture_ && !referenceTableExists_))) {
                    result->vt = VT_EMPTY;
                    return S_OK;
                }
                if (member == CurSelectedCtrl && referenceLayoutFixture_ &&
                    !referenceTableExists_) {
                    result->vt = VT_EMPTY;
                    return S_OK;
                }
                if (member == ActiveDocument) {
                    activeDocumentFullNameReady_ = true;
                }
                result->vt = VT_DISPATCH;
                result->pdispVal = this;
                static_cast<void>(AddRef());
                return S_OK;
            }
            if (member == HStyle || member == HParaShape || member == HInsertText ||
                member == HCharShape) {
                activeParameter_ = member;
                result->vt = VT_DISPATCH;
                result->pdispVal = this;
                static_cast<void>(AddRef());
                return S_OK;
            }
            if (member == HArrayFixture) {
                result->vt = VT_DISPATCH;
                result->pdispVal = parameterSet_;
                static_cast<void>(parameterSet_->AddRef());
                return S_OK;
            }
            if (member == DocumentId) {
                result->vt = VT_I4;
                result->lVal = documentId_;
                return S_OK;
            }
            if (member == WindowHandle) {
                result->vt = VT_I4;
                result->lVal = windowHandle_;
                return S_OK;
            }
            if (member == FullName) {
                if (requireActiveDocumentFullName_) {
                    activeDocumentFullNameReady_ = false;
                    ++activeDocumentFullNameReads_;
                }
                result->vt = VT_BSTR;
                result->bstrVal = SysAllocStringLen(
                    fullName_.data(),
                    static_cast<UINT>(fullName_.size()));
                return result->bstrVal != nullptr ? S_OK : E_OUTOFMEMORY;
            }
            if (referenceLayoutFixture_ &&
                (member == CellShape || member == ParentCtrl)) {
                if (member == ParentCtrl &&
                    (!referenceTableExists_ || ReferenceCurrentCell() == nullptr)) {
                    result->vt = VT_EMPTY;
                    return S_OK;
                }
                result->vt = VT_DISPATCH;
                result->pdispVal = this;
                static_cast<void>(AddRef());
                return S_OK;
            }
            if (member == CellShape || member == ParentCtrl) {
                result->vt = VT_I4;
                result->lVal = member == CellShape ? 1 : 0;
                return S_OK;
            }
            if (member == PageCount) {
                result->vt = VT_I4;
                result->lVal = documentOpen_ ? pageCount_ : 0L;
                return S_OK;
            }
            if (member == CurrentPage) {
                result->vt = VT_I4;
                result->lVal = inspectionScopeFixture_
                    ? inspectionCurrentPage_
                    : referenceLayoutFixture_ && referenceTableExists_ ? 1L : 0L;
                return S_OK;
            }
            if (member == IsModified) {
                result->vt = VT_BOOL;
                result->boolVal = modified_ ? VARIANT_TRUE : VARIANT_FALSE;
                return S_OK;
            }
            if (member == SelectionMode) {
                result->vt = VT_I4;
                result->lVal = selectionMode_;
                return S_OK;
            }
            if (member == CtrlID) {
                if (inspectionScopeFixture_ && InspectionControlReady()) {
                    ++inspectionIdentityReads_;
                    const std::wstring& type = inspectionControls_[
                        static_cast<size_t>(inspectionControlIndex_)].type;
                    result->vt = VT_BSTR;
                    result->bstrVal = SysAllocStringLen(
                        type.data(),
                        static_cast<UINT>(type.size()));
                    return result->bstrVal != nullptr ? S_OK : E_OUTOFMEMORY;
                }
                result->vt = VT_BSTR;
                result->bstrVal = SysAllocString(L"tbl");
                return result->bstrVal != nullptr ? S_OK : E_OUTOFMEMORY;
            }
            if (inspectionScopeFixture_ &&
                (member == Next || member == Prev)) {
                if (member == Next) {
                    ++inspectionNextReads_;
                    ++inspectionControlIndex_;
                } else {
                    ++inspectionPrevReads_;
                    --inspectionControlIndex_;
                }
                if (!InspectionControlReady()) {
                    result->vt = VT_EMPTY;
                    return S_OK;
                }
                result->vt = VT_DISPATCH;
                result->pdispVal = this;
                static_cast<void>(AddRef());
                return S_OK;
            }
            if (member == Next || member == Prev) {
                result->vt = VT_EMPTY;
                return S_OK;
            }
            if (inspectionScopeFixture_ && member == UserDesc &&
                InspectionControlReady()) {
                const std::wstring& description = inspectionControls_[
                    static_cast<size_t>(inspectionControlIndex_)].description;
                result->vt = VT_BSTR;
                result->bstrVal = SysAllocStringLen(
                    description.data(),
                    static_cast<UINT>(description.size()));
                return result->bstrVal != nullptr ? S_OK : E_OUTOFMEMORY;
            }
            if (member == SetID) {
                result->vt = VT_BSTR;
                result->bstrVal = SysAllocString(L"TableCreation");
                return result->bstrVal != nullptr ? S_OK : E_OUTOFMEMORY;
            }
            if (referenceLayoutFixture_ && member == FaceNameHangul) {
                result->vt = VT_BSTR;
                result->bstrVal = SysAllocStringLen(
                    pendingReferenceFormat_.faceName.data(),
                    static_cast<UINT>(pendingReferenceFormat_.faceName.size()));
                return result->bstrVal != nullptr ? S_OK : E_OUTOFMEMORY;
            }
            if (referenceLayoutFixture_ && member == Bold) {
                result->vt = VT_BOOL;
                result->boolVal = pendingReferenceFormat_.bold
                    ? VARIANT_TRUE
                    : VARIANT_FALSE;
                return S_OK;
            }
            if (referenceLayoutFixture_ &&
                (member == CharacterHeight || member == TextColor ||
                 member == RatioHangul || member == SpacingHangul ||
                 member == AlignType || member == LineSpacingType ||
                 member == LineSpacing || member == LeftMargin ||
                 member == RightMargin || member == Indentation ||
                 member == PrevSpacing || member == NextSpacing)) {
                result->vt = VT_I4;
                result->lVal = ReferenceFormatValue(pendingReferenceFormat_, member);
                return S_OK;
            }
            if (member >= Apply && member <= NextSpacing) {
                result->vt = VT_I4;
                result->lVal = ProfileValue(currentProfile_, member);
                return S_OK;
            }
        }
        if ((flags & DISPATCH_METHOD) != 0) {
            if (member == MutateTableTopology && referenceLayoutFixture_) {
                if (parameters != nullptr && parameters->cArgs != 0) {
                    return DISP_E_BADPARAMCOUNT;
                }
                const bool mutated = MergeFirstReferenceRow();
                referenceCallTopologyMutated_ = mutated;
                result->vt = VT_BOOL;
                result->boolVal = mutated ? VARIANT_TRUE : VARIANT_FALSE;
                return S_OK;
            }
            if (member == GetPageText && inspectionScopeFixture_) {
                if (parameters == nullptr || parameters->cArgs != 2 ||
                    parameters->rgvarg[0].vt != VT_I4 ||
                    parameters->rgvarg[1].vt != VT_I4) {
                    return DISP_E_TYPEMISMATCH;
                }
                result->vt = VT_BSTR;
                result->bstrVal = SysAllocString(L"scoped page text");
                return result->bstrVal != nullptr ? S_OK : E_OUTOFMEMORY;
            }
            if (member == KeyIndicator && referenceLayoutFixture_) {
                if (parameters == nullptr || parameters->cArgs != 8 ||
                    parameters->rgvarg[0].vt != (VT_BSTR | VT_BYREF) ||
                    parameters->rgvarg[0].pbstrVal == nullptr) {
                    return DISP_E_TYPEMISMATCH;
                }
                const ReferenceCell* const cell = ReferenceCurrentCell();
                const std::wstring indicator = cell == nullptr
                    ? L""
                    : L"\uD45C(" + cell->address + L")";
                *parameters->rgvarg[0].pbstrVal = SysAllocStringLen(
                    indicator.data(),
                    static_cast<UINT>(indicator.size()));
                return *parameters->rgvarg[0].pbstrVal != nullptr
                    ? S_OK
                    : E_OUTOFMEMORY;
            }
            if ((member == HwpLineType || member == HwpLineWidth) &&
                referenceLayoutFixture_) {
                if (parameters == nullptr || parameters->cArgs != 1 ||
                    parameters->rgvarg[0].vt != VT_BSTR) {
                    return DISP_E_TYPEMISMATCH;
                }
                const std::wstring value(parameters->rgvarg[0].bstrVal);
                result->vt = VT_I4;
                if (member == HwpLineWidth) {
                    result->lVal = value == L"0.12mm"
                        ? 12L
                        : value == L"0.3mm"
                        ? 30L
                        : 99L;
                    return S_OK;
                }
                const std::vector<std::wstring> lineTypes{
                    L"None",
                    L"Solid",
                    L"Dash",
                    L"Dot",
                    L"DashDot",
                    L"DashDotDot",
                    L"LongDash",
                    L"Circle",
                    L"DoubleSlim",
                    L"SlimThick",
                    L"ThickSlim",
                    L"SlimThickSlim",
                };
                const auto found =
                    std::find(lineTypes.begin(), lineTypes.end(), value);
                result->lVal = found == lineTypes.end()
                    ? -1L
                    : static_cast<LONG>(
                          std::distance(lineTypes.begin(), found));
                return S_OK;
            }
            if (member == GenericParameter && referenceLayoutFixture_) {
                if (parameters == nullptr || parameters->cArgs != 1 ||
                    parameters->rgvarg[0].vt != VT_BSTR) {
                    return DISP_E_TYPEMISMATCH;
                }
                const std::wstring value(parameters->rgvarg[0].bstrVal);
                result->vt = VT_I4;
                result->lVal = value == L"Left"
                    ? 1L
                    : value == L"Center"
                    ? 3L
                    : value == L"Right"
                    ? 2L
                    : 0L;
                return S_OK;
            }
            if (member == FindItem) {
                if (parameters == nullptr || parameters->cArgs != 1 ||
                    parameters->rgvarg[0].vt != VT_I4) {
                    return DISP_E_TYPEMISMATCH;
                }
                pendingDocumentId_ = parameters->rgvarg[0].lVal;
                result->vt = VT_DISPATCH;
                result->pdispVal = this;
                static_cast<void>(AddRef());
                return S_OK;
            }
            if (member == SetActiveDocument) {
                if (parameters != nullptr && parameters->cArgs != 0) {
                    return DISP_E_BADPARAMCOUNT;
                }
                if (pendingDocumentId_ <= 0) {
                    return E_INVALIDARG;
                }
                documentId_ = pendingDocumentId_;
                pendingDocumentId_ = 0;
                result->vt = VT_EMPTY;
                return S_OK;
            }
            if (member == GetPos) {
                if (parameters == nullptr || parameters->cArgs != 3 ||
                    parameters->rgvarg[0].vt != (VT_I4 | VT_BYREF) ||
                    parameters->rgvarg[1].vt != (VT_I4 | VT_BYREF) ||
                    parameters->rgvarg[2].vt != (VT_I4 | VT_BYREF) ||
                    parameters->rgvarg[0].plVal == nullptr ||
                    parameters->rgvarg[1].plVal == nullptr ||
                    parameters->rgvarg[2].plVal == nullptr) {
                    return DISP_E_TYPEMISMATCH;
                }
                const ReferenceCell* const cell = ReferenceCurrentCell();
                const bool staleEmptySelection =
                    emptyCellTextFixture_ && emptyCellSelectAll_ &&
                    cell != nullptr && cell->text.empty();
                *parameters->rgvarg[2].plVal =
                    staleEmptySelection ? 0L : currentList_;
                *parameters->rgvarg[1].plVal =
                    staleEmptySelection ? 0L : currentParagraph_;
                *parameters->rgvarg[0].plVal =
                    staleEmptySelection ? 0L : currentCharacter_;
                return S_OK;
            }
            if (member == Save) {
                if (parameters == nullptr || parameters->cArgs != 1 ||
                    parameters->rgvarg[0].vt != VT_BOOL) {
                    return DISP_E_TYPEMISMATCH;
                }
                saveCalledWithTrue_ = parameters->rgvarg[0].boolVal == VARIANT_TRUE;
                lifecycleCalls_.push_back(L"Save");
                if (saveClearsModified_) {
                    modified_ = false;
                }
                result->vt = VT_BOOL;
                result->boolVal = saveReturnsTrue_ ? VARIANT_TRUE : VARIANT_FALSE;
                return S_OK;
            }
            if (member == SaveAs) {
                if (parameters == nullptr || parameters->cArgs != 3 ||
                    parameters->rgvarg[0].vt != VT_BSTR ||
                    parameters->rgvarg[1].vt != VT_BSTR ||
                    parameters->rgvarg[2].vt != VT_BSTR) {
                    return DISP_E_TYPEMISMATCH;
                }
                const std::wstring options(parameters->rgvarg[0].bstrVal);
                const std::wstring format(parameters->rgvarg[1].bstrVal);
                const std::wstring path(parameters->rgvarg[2].bstrVal);
                saveAsCalledWithArguments_ =
                    path == lifecyclePath_ &&
                    format == lifecycleFormat_ &&
                    options.empty();
                const bool saved =
                    lifecycleRecovered_ && saveAsCalledWithArguments_;
                if (saved) {
                    fullName_ = path;
                    documentOpen_ = true;
                    modified_ = false;
                }
                lifecycleCalls_.push_back(L"SaveAs");
                result->vt = VT_BOOL;
                result->boolVal = saved ? VARIANT_TRUE : VARIANT_FALSE;
                return S_OK;
            }
            if (member == Clear) {
                if (parameters == nullptr || parameters->cArgs != 1 ||
                    parameters->rgvarg[0].vt != VT_I2) {
                    return DISP_E_TYPEMISMATCH;
                }
                ++clearCallCount_;
                clearCalledWithDiscard_ = parameters->rgvarg[0].iVal == 1;
                lifecycleCalls_.push_back(L"Clear");
                documentOpen_ = false;
                modified_ = false;
                fullName_.clear();
                lifecycleMismatchActive_ = false;
                result->vt = VT_EMPTY;
                return S_OK;
            }
            if (member == Open) {
                if (parameters == nullptr || parameters->cArgs != 3 ||
                    parameters->rgvarg[0].vt != VT_BSTR ||
                    parameters->rgvarg[1].vt != VT_BSTR ||
                    parameters->rgvarg[2].vt != VT_BSTR) {
                    return DISP_E_TYPEMISMATCH;
                }
                const std::wstring options(parameters->rgvarg[0].bstrVal);
                const std::wstring format(parameters->rgvarg[1].bstrVal);
                const std::wstring path(parameters->rgvarg[2].bstrVal);
                openCalledWithArguments_ =
                    path == lifecyclePath_ &&
                    format == lifecycleFormat_ &&
                    options == L"lock:FALSE";
                lifecycleCalls_.push_back(L"Open");
                const bool opened =
                    openReturnsTrue_ && openCalledWithArguments_;
                if (opened) {
                    documentOpen_ = true;
                    modified_ = false;
                    fullName_ = lifecycleReopenedPath_;
                    lifecycleMismatchActive_ =
                        lifecycleMismatchAfterOpen_;
                }
                result->vt = VT_BOOL;
                result->boolVal =
                    opened ? VARIANT_TRUE : VARIANT_FALSE;
                return S_OK;
            }
            if (member == ReturnBoolean) {
                result->vt = VT_BOOL;
                result->boolVal = VARIANT_TRUE;
                return S_OK;
            }
            if (member == ReturnInteger) {
                result->vt = VT_I4;
                result->lVal = -42;
                return S_OK;
            }
            if (member == ReturnText) {
                result->vt = VT_BSTR;
                result->bstrVal = SysAllocString(L"C:/Temp/BIN0001.png");
                return result->bstrVal != nullptr ? S_OK : E_OUTOFMEMORY;
            }
            if (member == ReturnVoid) {
                return S_OK;
            }
            if (member == CreateAction) {
                if (parameters == nullptr || parameters->cArgs != 1 ||
                    parameters->rgvarg[0].vt != VT_BSTR) {
                    return DISP_E_TYPEMISMATCH;
                }
                const std::wstring action(parameters->rgvarg[0].bstrVal);
                currentAction_ = action;
                if (action == L"AutoSpellRun" || action == L"ChangeRome") {
                    return S_OK;
                }
                result->vt = VT_DISPATCH;
                result->pdispVal = this;
                static_cast<void>(AddRef());
                return S_OK;
            }
            if (member == CreateSet) {
                if (parameters != nullptr && parameters->cArgs != 0 &&
                    !(parameters->cArgs == 1 && parameters->rgvarg[0].vt == VT_BSTR)) {
                    return DISP_E_BADPARAMCOUNT;
                }
                if (referenceLayoutFixture_ && parameters != nullptr &&
                    parameters->cArgs == 1 &&
                    std::wstring(parameters->rgvarg[0].bstrVal) == L"ListParaPos") {
                    result->vt = VT_DISPATCH;
                    result->pdispVal = new FakeListParaPosDispatch();
                    return S_OK;
                }
                result->vt = VT_DISPATCH;
                result->pdispVal = this;
                static_cast<void>(AddRef());
                return S_OK;
            }
            if (member == SetMessageBoxMode) {
                if (parameters == nullptr || parameters->cArgs != 1 ||
                    parameters->rgvarg[0].vt != VT_I4) {
                    return DISP_E_TYPEMISMATCH;
                }
                const LONG requested = parameters->rgvarg[0].lVal;
                if (requested == kInitialMessageBoxMode &&
                    messageBoxMode_ != kInitialMessageBoxMode) {
                    ++messageBoxModeRestoreAttempts_;
                    if (failNextMessageBoxModeRestore_) {
                        failNextMessageBoxModeRestore_ = false;
                        return E_FAIL;
                    }
                }
                const LONG previous = messageBoxMode_;
                messageBoxMode_ = requested;
                autoYesMessageBoxModeUsed_ = autoYesMessageBoxModeUsed_ ||
                    messageBoxMode_ == 0x00011010;
                result->vt = VT_I4;
                result->lVal = previous;
                return S_OK;
            }
            if (member == SelectCtrl) {
                if (referenceLayoutFixture_) {
                    if (parameters == nullptr || parameters->cArgs != 2 ||
                        parameters->rgvarg[1].vt != VT_BSTR) {
                        return DISP_E_TYPEMISMATCH;
                    }
                    tableSelected_ = referenceTableExists_ &&
                        std::wstring(parameters->rgvarg[1].bstrVal) ==
                            L"reference-table";
                    if (preflightSelectionFixture_ && tableSelected_) {
                        if (preflightTableControlSelection_ &&
                            preflightSelectionControlCaptured_ &&
                            preflightSelectionReadOccurred_) {
                            preflightSelectionRestoreAttempted_ = true;
                            preflightSelectionRestored_ = true;
                        }
                        selectionMode_ = 4;
                    }
                    result->vt = VT_BOOL;
                    result->boolVal = tableSelected_ ? VARIANT_TRUE : VARIANT_FALSE;
                    return S_OK;
                }
                result->vt = VT_BOOL;
                result->boolVal = VARIANT_FALSE;
                return S_OK;
            }
            if (member == DeleteCtrl) {
                if (parameters == nullptr || parameters->cArgs != 1 ||
                    parameters->rgvarg[0].vt != VT_DISPATCH) {
                    return DISP_E_TYPEMISMATCH;
                }
                if (referenceLayoutFixture_) {
                    const bool deleted = referenceTableExists_;
                    if (deleted) {
                        referenceTableExists_ = false;
                        tableSelected_ = false;
                        currentList_ = 0;
                        currentParagraph_ = 108;
                        currentCharacter_ = 0;
                        selectionMode_ = 0;
                        ++referenceControlDeletes_;
                    }
                    result->vt = VT_BOOL;
                    result->boolVal = deleted ? VARIANT_TRUE : VARIANT_FALSE;
                    return S_OK;
                }
                result->vt = VT_BOOL;
                result->boolVal = VARIANT_TRUE;
                return S_OK;
            }
            if (member == GetAnchorPos) {
                if (inspectionScopeFixture_) {
                    if (parameters == nullptr || parameters->cArgs != 1 ||
                        parameters->rgvarg[0].vt != VT_I4) {
                        return DISP_E_TYPEMISMATCH;
                    }
                    inspectionAnchorRoot_ = parameters->rgvarg[0].lVal == 2;
                }
                anchorRead_ = true;
                result->vt = VT_DISPATCH;
                result->pdispVal = this;
                static_cast<void>(AddRef());
                return S_OK;
            }
            if (member == Item) {
                if (parameters == nullptr || parameters->cArgs != 1 ||
                    parameters->rgvarg[0].vt != VT_BSTR) {
                    return DISP_E_TYPEMISMATCH;
                }
                const std::wstring item(parameters->rgvarg[0].bstrVal);
                if (inspectionScopeFixture_ && InspectionControlReady()) {
                    const InspectionControl& control = inspectionControls_[
                        static_cast<size_t>(inspectionControlIndex_)];
                    result->vt = VT_I4;
                    result->lVal = item == L"List"
                        ? control.page
                        : item == L"Para"
                        ? inspectionControlIndex_
                        : item == L"Pos"
                        ? 0L
                        : 0L;
                    return item == L"List" || item == L"Para" || item == L"Pos"
                        ? S_OK
                        : DISP_E_MEMBERNOTFOUND;
                }
                if (referenceLayoutFixture_) {
                    if (item == L"Cell") {
                        result->vt = VT_DISPATCH;
                        result->pdispVal = this;
                        static_cast<void>(AddRef());
                        return S_OK;
                    }
                    const ReferenceCell* const cell = ReferenceCurrentCell();
                    result->vt = VT_I4;
                    result->lVal = item == L"List"
                        ? 0L
                        : item == L"Para"
                        ? 108L
                        : item == L"Pos"
                        ? 0L
                        : item == L"Width" && cell != nullptr
                        ? cell->width
                        : item == L"Height" && cell != nullptr
                        ? cell->height
                        : 0L;
                    return item == L"List" || item == L"Para" || item == L"Pos" ||
                            item == L"Width" || item == L"Height"
                        ? S_OK
                        : DISP_E_MEMBERNOTFOUND;
                }
                result->vt = VT_I4;
                result->lVal = item == L"Para" ? 108L : (item == L"Height" ? 1L : 0L);
                return S_OK;
            }
            if (member == SetItem) {
                if (parameters == nullptr || parameters->cArgs != 2 ||
                    parameters->rgvarg[1].vt != VT_BSTR) {
                    return DISP_E_TYPEMISMATCH;
                }
                if (currentAction_ == L"SetWithoutDefault") {
                    actionInputApplied_ =
                        parameters->rgvarg[0].vt == VT_BSTR &&
                        std::wstring(parameters->rgvarg[1].bstrVal) == L"FileName" &&
                        std::wstring(parameters->rgvarg[0].bstrVal) == L"C:\\probe\\fixture.dat";
                }
                result->vt = VT_BOOL;
                result->boolVal = VARIANT_TRUE;
                return S_OK;
            }
            if (member == ItemExist) {
                if (parameters == nullptr || parameters->cArgs != 1 ||
                    parameters->rgvarg[0].vt != VT_BSTR) {
                    return DISP_E_TYPEMISMATCH;
                }
                result->vt = VT_BOOL;
                result->boolVal =
                    std::wstring(parameters->rgvarg[0].bstrVal) == L"ReportedMissing"
                    ? VARIANT_FALSE
                    : VARIANT_TRUE;
                return S_OK;
            }
            if (member == SetCurMetatagName) {
                if (parameters == nullptr || parameters->cArgs != 1 ||
                    parameters->rgvarg[0].vt != VT_BSTR) {
                    return DISP_E_TYPEMISMATCH;
                }
                result->vt = VT_BOOL;
                result->boolVal = VARIANT_TRUE;
                return S_OK;
            }
            if (member == SetPos) {
                if (inspectionScopeFixture_ && parameters != nullptr &&
                    parameters->cArgs == 3 &&
                    parameters->rgvarg[0].vt == VT_I4 &&
                    parameters->rgvarg[1].vt == VT_I4 &&
                    parameters->rgvarg[2].vt == VT_I4) {
                    currentList_ = parameters->rgvarg[2].lVal;
                    currentParagraph_ = parameters->rgvarg[1].lVal;
                    currentCharacter_ = parameters->rgvarg[0].lVal;
                    if (currentList_ >= 1 && currentList_ <= pageCount_) {
                        inspectionCurrentPage_ = currentList_ - 1;
                    }
                    result->vt = VT_BOOL;
                    result->boolVal = VARIANT_TRUE;
                    return S_OK;
                }
                if (referenceLayoutFixture_ && parameters != nullptr &&
                    parameters->cArgs == 3 &&
                    parameters->rgvarg[0].vt == VT_I4 &&
                    parameters->rgvarg[1].vt == VT_I4 &&
                    parameters->rgvarg[2].vt == VT_I4) {
                    const LONG list = parameters->rgvarg[2].lVal;
                    if (referenceTopologyChanged_ && list == 649 &&
                        referenceTopologyInspections_ < 2) {
                        staleTopologyCellPositionAttempted_ = true;
                    }
                    const bool positioned = list == 0 ||
                        (referenceTableExists_ &&
                         ReferenceCellByList(list) != nullptr);
                    if (positioned) {
                        currentList_ = list;
                        currentParagraph_ = parameters->rgvarg[1].lVal;
                        currentCharacter_ = parameters->rgvarg[0].lVal;
                        selectionMode_ = 0;
                        tailSelection_ = false;
                    }
                    result->vt = VT_BOOL;
                    result->boolVal = positioned ? VARIANT_TRUE : VARIANT_FALSE;
                    return S_OK;
                }
                if (atomicAppendFixture_ && parameters != nullptr && parameters->cArgs == 3 &&
                    parameters->rgvarg[0].vt == VT_I4 &&
                    parameters->rgvarg[1].vt == VT_I4 &&
                    parameters->rgvarg[2].vt == VT_I4) {
                    currentList_ = parameters->rgvarg[2].lVal;
                    currentParagraph_ = parameters->rgvarg[1].lVal;
                    currentCharacter_ = parameters->rgvarg[0].lVal;
                    tailSelection_ = false;
                }
                positionedAfterAnchor_ = anchorRead_;
                result->vt = VT_BOOL;
                result->boolVal = VARIANT_TRUE;
                return S_OK;
            }
            if (member == SelectText) {
                if (parameters == nullptr || parameters->cArgs != 4) {
                    return DISP_E_BADPARAMCOUNT;
                }
                if (referenceLayoutFixture_) {
                    selectedStartList_ = currentList_;
                    selectedStartParagraph_ = parameters->rgvarg[3].lVal;
                    selectedStartCharacter_ = parameters->rgvarg[2].lVal;
                    selectedEndList_ = currentList_;
                    selectedEndParagraph_ = parameters->rgvarg[1].lVal;
                    selectedEndCharacter_ = parameters->rgvarg[0].lVal;
                    selectionMode_ = 1;
                    result->vt = VT_BOOL;
                    result->boolVal = VARIANT_TRUE;
                    return S_OK;
                }
                nativeTableRangeSelected_ = positionedAfterAnchor_;
                result->vt = VT_BOOL;
                result->boolVal = nativeTableRangeSelected_ ? VARIANT_TRUE : VARIANT_FALSE;
                return S_OK;
            }
            if (member == InitScan) {
                if (parameters == nullptr || parameters->cArgs != 6) {
                    return DISP_E_BADPARAMCOUNT;
                }
                scanStarted_ = true;
                scanReleased_ = false;
                scanStep_ = 0;
                if (preflightSelectionFixture_ &&
                    preflightSelectionControlCaptured_) {
                    preflightSelectionReadOccurred_ = true;
                }
                result->vt = VT_BOOL;
                result->boolVal = VARIANT_TRUE;
                return S_OK;
            }
            if (member == GetText) {
                if (!scanStarted_ || scanReleased_ || parameters == nullptr ||
                    parameters->cArgs != 1 ||
                    parameters->rgvarg[0].vt != (VT_BSTR | VT_BYREF) ||
                    parameters->rgvarg[0].pbstrVal == nullptr) {
                    return DISP_E_TYPEMISMATCH;
                }
                const bool content = scanStep_++ == 0;
                const ReferenceCell* const cell = referenceLayoutFixture_
                    ? ReferenceCurrentCell()
                    : nullptr;
                const std::wstring scanned = referenceLayoutFixture_ && cell != nullptr
                    ? cell->text
                    : L"cell-text";
                *parameters->rgvarg[0].pbstrVal = SysAllocString(
                    content ? scanned.c_str() : L"");
                if (*parameters->rgvarg[0].pbstrVal == nullptr) {
                    return E_OUTOFMEMORY;
                }
                result->vt = VT_I4;
                result->lVal = content ? 2L : 1L;
                return S_OK;
            }
            if (member == ReleaseScan) {
                if (!scanStarted_) {
                    return E_UNEXPECTED;
                }
                scanReleased_ = true;
                return S_OK;
            }
            if (member == GetTextFile) {
                if (parameters == nullptr || parameters->cArgs != 2 ||
                    parameters->rgvarg[0].vt != VT_BSTR ||
                    parameters->rgvarg[1].vt != VT_BSTR) {
                    return DISP_E_TYPEMISMATCH;
                }
                const std::wstring options(parameters->rgvarg[0].bstrVal);
                const std::wstring format(parameters->rgvarg[1].bstrVal);
                if (referenceLayoutFixture_ && format == L"UNICODE" &&
                    options == L"saveblock:true") {
                    std::wstring selected;
                    if (tailSelection_) {
                        selected = atomicTail_;
                    } else if (selectionMode_ == 1) {
                        const ReferenceCell* const cell = ReferenceCellByList(
                            selectedStartList_);
                        if (cell != nullptr &&
                            selectedStartList_ == selectedEndList_) {
                            const size_t first = ReferenceTextOffset(
                                cell->text,
                                selectedStartParagraph_,
                                selectedStartCharacter_);
                            const size_t second = ReferenceTextOffset(
                                cell->text,
                                selectedEndParagraph_,
                                selectedEndCharacter_);
                            const size_t start = (std::min)(first, second);
                            const size_t end = (std::max)(first, second);
                            selected = cell->text.substr(start, end - start);
                        }
                    }
                    result->vt = VT_BSTR;
                    result->bstrVal = SysAllocStringLen(
                        selected.data(),
                        static_cast<UINT>(selected.size()));
                    return result->bstrVal != nullptr ? S_OK : E_OUTOFMEMORY;
                }
                nativeTableBlockCopied_ = nativeTableRangeSelected_ &&
                    format == L"HWP" && options == L"saveblock:true";
                result->vt = VT_BSTR;
                const bool unstableHwpml =
                    unstableSerializationMetadataFixture_ &&
                    format == L"HWPML2X" && options.empty();
                const bool unstableCaretHwpml =
                    unstableCaretMetadataFixture_ &&
                    format == L"HWPML2X" && options.empty();
                const wchar_t* const hwpml =
                    diagnosticSectionMismatchFixture_ &&
                    format == L"HWPML2X" && options.empty()
                    ? ((serializationReads_++ % 2 != 0)
                        ? diagnosticSectionAfterHwpml_.c_str()
                        : diagnosticSectionBeforeHwpml_.c_str())
                    : unstableHwpml &&
                    (serializationReads_++ % 2 != 0)
                    ? L"<HWPML><BINDATA Encoding=\"Base64\" Id=\"3\" "
                      L"Size=\"5360\">REENCODED_SAME_IMAGE</BINDATA>"
                      L"<CHARSHAPE TextColor=\"255\"/>"
                      L"<TEXT CharShape=\"1\">body-text</TEXT></HWPML>"
                    : unstableCaretHwpml &&
                      (serializationReads_++ % 2 != 0)
                    ? L"<HWPML><HEAD><DOCSETTING>"
                      L"<CARETPOS List=\"0\" Para=\"0\" Pos=\"0\"/>"
                      L"</DOCSETTING></HEAD>"
                      L"<BODY><PARAMETERSET Count=\"1\" SetId=\"537\">"
                      L"<ITEM ItemId=\"614\" Type=\"Set\">"
                      L"<PARAMETERSET Count=\"0\" SetId=\"614\"/>"
                      L"</ITEM></PARAMETERSET>"
                      L"<CHARSHAPE TextColor=\"255\"/>"
                      L"<TEXT CharShape=\"1\">body-text</TEXT></BODY></HWPML>"
                    : unstableCaretHwpml
                    ? L"<HWPML><HEAD><DOCSETTING>"
                      L"<CARETPOS List=\"26\" Para=\"0\" Pos=\"7\"/>"
                      L"</DOCSETTING></HEAD>"
                      L"<BODY><PARAMETERSET Count=\"1\" SetId=\"537\">"
                      L"<ITEM ItemId=\"16385\" Type=\"BinData\">0</ITEM>"
                      L"</PARAMETERSET>"
                      L"<CHARSHAPE TextColor=\"255\"/>"
                      L"<TEXT CharShape=\"1\">body-text</TEXT></BODY></HWPML>"
                    : L"<HWPML><BINDATA Encoding=\"Base64\" Id=\"3\" "
                      L"Size=\"5373\">UNCHANGED_IMAGE</BINDATA>"
                      L"<CHARSHAPE TextColor=\"255\"/>"
                      L"<TEXT CharShape=\"1\">body-text</TEXT></HWPML>";
                const wchar_t* const value = atomicAppendFixture_ && tailSelection_ &&
                    format == L"UNICODE" && options == L"saveblock:true"
                    ? atomicTail_.c_str()
                    : checkpointRestoreFixture_ &&
                      format == L"HWP" && options.empty()
                    ? checkpointDocumentBlock_.c_str()
                    : nativeTableBlockCopied_
                    ? L"SFdQX05BVElWRV9UQUJMRQ=="
                    : (format == L"TEXT" && options.empty()
                    ? (lifecycleMismatchActive_
                        ? L"body-text-mismatch\tcell-text"
                        : L"body-text\tcell-text")
                        : (format == L"HWPML2X" && options.empty()
                            ? hwpml
                        : (format == L"HWP" && options.empty()
                            ? (lifecycleRecoveryBlockCaptured_
                                ? L"SERIALIZED_BODY_TABLE_RED_STYLE"
                                : L"")
                    : (format == L"UNICODE" && options == L"saveblock:true"
                        ? L"old"
                        : L""))));
                result->bstrVal = SysAllocString(value);
                return result->bstrVal != nullptr ? S_OK : E_OUTOFMEMORY;
            }
            if (member == GetSelectedPosBySet) {
                if (parameters == nullptr || parameters->cArgs != 2 ||
                    parameters->rgvarg[0].vt != VT_DISPATCH ||
                    parameters->rgvarg[1].vt != VT_DISPATCH) {
                    return DISP_E_TYPEMISMATCH;
                }
                if (referenceLayoutFixture_) {
                    auto* const start = dynamic_cast<FakeListParaPosDispatch*>(
                        parameters->rgvarg[1].pdispVal);
                    auto* const end = dynamic_cast<FakeListParaPosDispatch*>(
                        parameters->rgvarg[0].pdispVal);
                    if (start == nullptr || end == nullptr) {
                        return DISP_E_TYPEMISMATCH;
                    }
                    const bool emptyCellSelection =
                        emptyCellTextFixture_ && selectionMode_ == 0;
                    const bool selected = emptyCellSelection || selectionMode_ != 0;
                    const LONG baseSelectionMode = selectionMode_ & 0x0F;
                    if (baseSelectionMode == 1 ||
                        baseSelectionMode == 3 ||
                        emptyCellSelection) {
                        start->SetPosition(
                            selectedStartList_,
                            selectedStartParagraph_,
                            selectedStartCharacter_);
                        end->SetPosition(
                            selectedEndList_,
                            selectedEndParagraph_,
                            selectedEndCharacter_);
                    } else {
                        start->SetPosition(
                            selected ? currentList_ : 0L,
                            selected ? currentParagraph_ : 0L,
                            selected ? currentCharacter_ : 0L);
                        end->SetPosition(
                            selected ? currentList_ : 0L,
                            selected ? currentParagraph_ : 0L,
                            selected ? currentCharacter_ : 0L);
                    }
                    result->vt = VT_BOOL;
                    result->boolVal = selected ? VARIANT_TRUE : VARIANT_FALSE;
                    return S_OK;
                }
                result->vt = VT_BOOL;
                result->boolVal =
                    textDeletionFixture_ && !textSelectionActive_
                    ? VARIANT_FALSE
                    : VARIANT_TRUE;
                return S_OK;
            }
            if (member == SetTextFile) {
                if (parameters == nullptr || parameters->cArgs != 3 ||
                    parameters->rgvarg[0].vt != VT_BSTR ||
                    parameters->rgvarg[1].vt != VT_BSTR ||
                    parameters->rgvarg[2].vt != VT_BSTR) {
                    return DISP_E_TYPEMISMATCH;
                }
                const std::wstring options(parameters->rgvarg[0].bstrVal);
                const std::wstring format(parameters->rgvarg[1].bstrVal);
                const std::wstring block(parameters->rgvarg[2].bstrVal);
                if (checkpointRestoreFixture_ &&
                    format == L"HWP" && options == L"insertfile" &&
                    (block == L"CHECKPOINT_TARGET" ||
                     block == L"CHECKPOINT_ROLLBACK")) {
                    ++checkpointSetAttempts_;
                    checkpointDocumentBlock_ = block;
                    pageCount_ = block == L"CHECKPOINT_TARGET"
                        ? (checkpointWrongTargetPageCount_ ? 3L : 4L)
                        : 2L;
                    result->vt = VT_I4;
                    result->lVal = 1;
                    return S_OK;
                }
                if (block == L"SERIALIZED_BODY_TABLE_RED_STYLE" &&
                    format == L"HWP" &&
                    options.empty()) {
                    lifecycleRecovered_ = true;
                    lifecycleMismatchActive_ = false;
                    documentOpen_ = true;
                    modified_ = true;
                    result->vt = VT_I4;
                    result->lVal = 1;
                    return S_OK;
                }
                nativeTableBlockPasted_ = nativeTableBlockCopied_ &&
                    block == L"SFdQX05BVElWRV9UQUJMRQ==" &&
                    format == L"HWP" && options == L"insertfile";
                if (nativeTableBlockPasted_) {
                    currentProfile_ = sourceProfile_;
                }
                pasteUsedSourceAnchorFormat_ = nativeTableBlockPasted_ &&
                    currentProfile_ == sourceProfile_;
                result->vt = VT_I4;
                result->lVal = nativeTableBlockPasted_ ? 1L : 0L;
                return S_OK;
            }
            if (member == Run) {
                if (parameters == nullptr || parameters->cArgs > 1 ||
                    (parameters->cArgs == 1 && parameters->rgvarg[0].vt != VT_BSTR)) {
                    return DISP_E_TYPEMISMATCH;
                }
                const std::wstring action = parameters->cArgs == 0
                    ? currentAction_
                    : std::wstring(parameters->rgvarg[0].bstrVal);
                runActions_.push_back(action);
                if (action == L"SelectCtrlFront" && failSelectCtrlFrontWithHresult_) {
                    return E_FAIL;
                }
                if (referenceLayoutFixture_) {
                    result->vt = VT_BOOL;
                    result->boolVal = RunReferenceLayoutAction(action)
                        ? VARIANT_TRUE
                        : VARIANT_FALSE;
                    return S_OK;
                }
                if (action == L"Delete" && textDeletionFixture_ &&
                    textSelectionActive_) {
                    textSelectionActive_ = false;
                    selectionMode_ = 0;
                    currentList_ = 0;
                    currentParagraph_ = 108;
                    currentCharacter_ = 0;
                    ++textDeletionActions_;
                } else if (action == L"MoveDocEnd" && atomicAppendFixture_) {
                    currentList_ = 0;
                    currentParagraph_ = 108;
                    currentCharacter_ = 0;
                    tailSelection_ = false;
                } else if (action == L"MoveSelDocEnd" && atomicAppendFixture_) {
                    tailSelection_ = currentList_ == 0 && currentParagraph_ == 108 &&
                        currentCharacter_ == 0;
                } else if (action == L"Delete" && atomicAppendFixture_ && tailSelection_) {
                    if (failAtomicTailDelete_) {
                        return E_ACCESSDENIED;
                    }
                    atomicTail_.clear();
                    pageCount_ = 1;
                    tailSelection_ = false;
                    ++atomicTailDeletes_;
                } else if (action == L"Cancel" && atomicAppendFixture_) {
                    tailSelection_ = false;
                } else if (action == L"SelectCtrlFront") {
                    frontSelectedAfterPosition_ = positionedAfterAnchor_;
                } else if (action == L"BreakPage") {
                    ranBreakPage_ = true;
                    if (atomicAppendFixture_) {
                        atomicTail_.push_back(L'\f');
                        pageCount_ = 2;
                        UpdateAtomicTailMaximumOccurrences();
                    }
                } else if (action == L"Copy") {
                    clipboardTableTransferUsed_ = true;
                    copyUsedAnchorSelection_ = anchorRead_ &&
                        positionedAfterAnchor_ && frontSelectedAfterPosition_;
                    copyCompleted_ = true;
                    currentProfile_ = destinationProfile_;
                } else if (action == L"Paste") {
                    clipboardTableTransferUsed_ = true;
                    pasteUsedSourceAnchorFormat_ = currentProfile_ == sourceProfile_;
                }
                result->vt = VT_BOOL;
                result->boolVal =
                    action == L"Copy" || action == L"Paste" ||
                        (action == L"SelectCtrlFront" && failSelectCtrlFront_)
                    ? VARIANT_FALSE
                    : VARIANT_TRUE;
                return S_OK;
            }
            if (member == GetDefault) {
                if (currentAction_ == L"SetWithoutDefault") {
                    return E_FAIL;
                }
                if (referenceLayoutFixture_) {
                    if (parameters != nullptr && parameters->cArgs >= 2 &&
                        parameters->rgvarg[1].vt == VT_BSTR) {
                        currentAction_.assign(
                            parameters->rgvarg[1].bstrVal,
                            SysStringLen(parameters->rgvarg[1].bstrVal));
                    }
                    parameterSet_->ClearGenericValues();
                    const ReferenceCell* const cell = ReferenceFormattingCell();
                    pendingReferenceFormat_ = cell == nullptr
                        ? BodyReferenceFormat()
                        : cell->format;
                    if (currentAction_ == L"CellBorderFill" &&
                        referenceEdgeExecutions_ > 0) {
                        PopulateReferenceBorderReadback(cell);
                        ++referenceBorderReadbacks_;
                    }
                    result->vt = VT_BOOL;
                    result->boolVal = VARIANT_TRUE;
                    return S_OK;
                }
                pendingProfile_ = currentProfile_;
                result->vt = VT_BOOL;
                result->boolVal = VARIANT_TRUE;
                return S_OK;
            }
            if (member == Execute) {
                if (referenceLayoutFixture_) {
                    if (parameters != nullptr && parameters->cArgs >= 2 &&
                        parameters->rgvarg[1].vt == VT_BSTR) {
                        currentAction_.assign(
                            parameters->rgvarg[1].bstrVal,
                            SysStringLen(parameters->rgvarg[1].bstrVal));
                    }
                    if (currentAction_ == L"TableCreate") {
                        referenceTableExists_ = true;
                        tableSelected_ = false;
                        pageCount_ = 2;
                        atomicTail_ = L"\f";
                        UpdateAtomicTailMaximumOccurrences();
                        SetReferenceCurrentCell(L"A1");
                    } else if (currentAction_ == L"TableInsertRightColumn") {
                        if (!InsertReferenceRightColumn()) {
                            return E_FAIL;
                        }
                    } else if (currentAction_ == L"CharShape") {
                        ReferenceCell* const cell = ReferenceFormattingCell();
                        if (cell != nullptr) {
                            ApplyReferenceCharacterFormat(
                                &cell->format,
                                pendingReferenceFormat_);
                        }
                    } else if (currentAction_ == L"ParagraphShape") {
                        ReferenceCell* const cell = ReferenceFormattingCell();
                        if (cell != nullptr) {
                            ApplyReferenceParagraphFormat(
                                &cell->format,
                                pendingReferenceFormat_);
                        }
                    } else if (currentAction_ == L"InsertText") {
                        if (failReferenceLayoutAfterFirstText_ &&
                            referenceTextInsertions_ == 1) {
                            return E_FAIL;
                        }
                        if (!InsertReferenceText(insertedText_)) {
                            return E_FAIL;
                        }
                        ++referenceTextInsertions_;
                    } else if (currentAction_ == L"CellBorderFill") {
                        ClearReferenceBorders();
                    }
                    if (currentAction_ == L"CellZoneBorder") {
                        ++referenceEdgeExecutions_;
                        if (!dropReferenceEdges_) {
                            if (!ApplyReferenceVisibleEdge()) {
                                return E_FAIL;
                            }
                            ++referenceEdgeApplications_;
                        }
                    } else if (currentAction_ == L"CellFill") {
                        ++referenceFillApplications_;
                    }
                    result->vt = VT_BOOL;
                    result->boolVal = VARIANT_TRUE;
                    return S_OK;
                }
                if (currentAction_ == L"SetWithoutDefault" && !actionInputApplied_) {
                    return E_FAIL;
                }
                if (currentAction_ == L"RaiseStructured") {
                    RaiseException(0xE0001234UL, 0, 0, nullptr);
                }
                if (activeParameter_ == HStyle) {
                    currentProfile_.styleId = pendingProfile_.styleId;
                } else if (activeParameter_ == HParaShape) {
                    currentProfile_.alignment = pendingProfile_.alignment;
                    currentProfile_.lineSpacing = pendingProfile_.lineSpacing;
                    currentProfile_.leftMargin = pendingProfile_.leftMargin;
                    currentProfile_.rightMargin = pendingProfile_.rightMargin;
                    currentProfile_.indentation = pendingProfile_.indentation;
                    currentProfile_.previousSpacing = pendingProfile_.previousSpacing;
                    currentProfile_.nextSpacing = pendingProfile_.nextSpacing;
                } else if (activeParameter_ == HInsertText) {
                    ++insertTextExecutions_;
                    if (atomicAppendFixture_) {
                        atomicTail_.append(insertedText_);
                        currentCharacter_ += static_cast<LONG>(insertedText_.size());
                        UpdateAtomicTailMaximumOccurrences();
                    }
                }
                result->vt = VT_BOOL;
                result->boolVal = VARIANT_TRUE;
                return S_OK;
            }
            if (member == GetCtrlInstId) {
                result->vt = VT_BSTR;
                if (inspectionScopeFixture_ && InspectionControlReady()) {
                    const std::wstring& instance = inspectionControls_[
                        static_cast<size_t>(inspectionControlIndex_)].instance;
                    result->bstrVal = SysAllocStringLen(
                        instance.data(),
                        static_cast<UINT>(instance.size()));
                } else {
                    result->bstrVal = SysAllocString(
                        referenceLayoutFixture_
                            ? L"reference-table"
                            : L"captured-table");
                }
                return result->bstrVal != nullptr ? S_OK : E_OUTOFMEMORY;
            }
        }
        return DISP_E_MEMBERNOTFOUND;
    }

    HRESULT STDMETHODCALLTYPE EnumConnectionPoints(
        IEnumConnectionPoints**) override {
        return E_NOTIMPL;
    }

    HRESULT STDMETHODCALLTYPE FindConnectionPoint(
        REFIID interfaceId,
        IConnectionPoint** const connectionPoint) override {
        if (connectionPoint == nullptr) {
            return E_POINTER;
        }
        *connectionPoint = nullptr;
        if (interfaceId != kDispatchEventIid) {
            return E_NOINTERFACE;
        }
        *connectionPoint = static_cast<IConnectionPoint*>(this);
        static_cast<void>(AddRef());
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE GetConnectionInterface(IID* const interfaceId) override {
        if (interfaceId == nullptr) {
            return E_POINTER;
        }
        *interfaceId = kDispatchEventIid;
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE GetConnectionPointContainer(
        IConnectionPointContainer** const container) override {
        if (container == nullptr) {
            return E_POINTER;
        }
        *container = static_cast<IConnectionPointContainer*>(this);
        static_cast<void>(AddRef());
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE Advise(IUnknown*, DWORD*) override {
        return E_NOTIMPL;
    }

    HRESULT STDMETHODCALLTYPE Unadvise(DWORD) override {
        return E_NOTIMPL;
    }

    HRESULT STDMETHODCALLTYPE EnumConnections(IEnumConnections**) override {
        return E_NOTIMPL;
    }

private:
    struct InspectionControl {
        std::wstring type;
        std::wstring instance;
        std::wstring description;
        LONG page = 0;
    };

    bool InspectionControlReady() const noexcept {
        return inspectionControlIndex_ >= 0 &&
            static_cast<size_t>(inspectionControlIndex_) <
                inspectionControls_.size();
    }

    struct ReferenceFormat {
        std::wstring faceName = L"Arial";
        LONG height = 900;
        bool bold = false;
        LONG textColor = 1315860;
        LONG widthRatio = 100;
        LONG letterSpacing = 0;
        LONG alignment = 1;
        LONG lineSpacingType = 0;
        LONG lineSpacing = 100;
        LONG leftMargin = 0;
        LONG rightMargin = 0;
        LONG indentation = 0;
        LONG previousSpacing = 0;
        LONG nextSpacing = 0;
    };

    struct ReferenceBorder {
        LONG type = 0;
        LONG width = 0;
        LONG color = 0;
    };

    struct ReferenceCell {
        std::wstring address;
        LONG listId = 0;
        LONG row = 0;
        LONG column = 0;
        LONG rowSpan = 1;
        LONG columnSpan = 1;
        LONG width = 0;
        LONG height = 0;
        bool active = true;
        std::wstring text;
        ReferenceFormat format;
        std::map<std::wstring, ReferenceBorder> borders;
    };

    static size_t ReferenceTextOffset(
        const std::wstring& text,
        const LONG paragraph,
        const LONG character) noexcept {
        const LONG requestedParagraph = (std::max)(0L, paragraph);
        size_t lineStart = 0;
        LONG currentParagraph = 0;
        while (currentParagraph < requestedParagraph && lineStart < text.size()) {
            const size_t lineBreak = text.find_first_of(L"\r\n", lineStart);
            if (lineBreak == std::wstring::npos) {
                return text.size();
            }
            lineStart = lineBreak + (
                text[lineBreak] == L'\r' && lineBreak + 1 < text.size() &&
                    text[lineBreak + 1] == L'\n'
                ? 2
                : 1);
            ++currentParagraph;
        }
        if (currentParagraph < requestedParagraph) {
            return text.size();
        }
        const size_t lineEnd = text.find_first_of(L"\r\n", lineStart);
        const size_t boundedEnd =
            lineEnd == std::wstring::npos ? text.size() : lineEnd;
        const size_t requestedCharacter =
            static_cast<size_t>((std::max)(0L, character));
        return lineStart + (std::min)(requestedCharacter, boundedEnd - lineStart);
    }

    static void ReferenceTextPositionAtOffset(
        const std::wstring& text,
        const size_t requestedOffset,
        LONG* const paragraph,
        LONG* const character) noexcept {
        const size_t offset = (std::min)(requestedOffset, text.size());
        LONG currentParagraph = 0;
        LONG currentCharacter = 0;
        for (size_t index = 0; index < offset; ++index) {
            if (text[index] == L'\r' &&
                index + 1 < offset &&
                text[index + 1] == L'\n') {
                ++currentParagraph;
                currentCharacter = 0;
                ++index;
            } else if (text[index] == L'\r' || text[index] == L'\n') {
                ++currentParagraph;
                currentCharacter = 0;
            } else {
                ++currentCharacter;
            }
        }
        *paragraph = currentParagraph;
        *character = currentCharacter;
    }

    bool InsertReferenceText(const std::wstring& text) {
        ReferenceCell* const cell = ReferenceCurrentCell();
        if (cell == nullptr) {
            return false;
        }
        size_t start = ReferenceTextOffset(
            cell->text,
            currentParagraph_,
            currentCharacter_);
        size_t end = start;
        if (selectionMode_ == 1 &&
            selectedStartList_ == currentList_ &&
            selectedEndList_ == currentList_) {
            const size_t selectedStart = ReferenceTextOffset(
                cell->text,
                selectedStartParagraph_,
                selectedStartCharacter_);
            const size_t selectedEnd = ReferenceTextOffset(
                cell->text,
                selectedEndParagraph_,
                selectedEndCharacter_);
            start = (std::min)(selectedStart, selectedEnd);
            end = (std::max)(selectedStart, selectedEnd);
        }
        cell->text.replace(start, end - start, text);
        ReferenceTextPositionAtOffset(
            cell->text,
            start + text.size(),
            &currentParagraph_,
            &currentCharacter_);
        selectionMode_ = 0;
        selectedStartList_ = 0;
        selectedStartParagraph_ = 0;
        selectedStartCharacter_ = 0;
        selectedEndList_ = 0;
        selectedEndParagraph_ = 0;
        selectedEndCharacter_ = 0;
        return true;
    }

    static ReferenceFormat BodyReferenceFormat() {
        return ReferenceFormat{};
    }

    static ReferenceFormat HeaderReferenceFormat() {
        ReferenceFormat format;
        format.height = 1100;
        format.bold = true;
        format.textColor = 16777215;
        format.alignment = 3;
        return format;
    }

    static bool ReferenceFormatMatches(
        const ReferenceFormat& left,
        const ReferenceFormat& right) noexcept {
        return left.faceName == right.faceName &&
            left.height == right.height &&
            left.bold == right.bold &&
            left.textColor == right.textColor &&
            left.widthRatio == right.widthRatio &&
            left.letterSpacing == right.letterSpacing &&
            left.alignment == right.alignment &&
            left.lineSpacingType == right.lineSpacingType &&
            left.lineSpacing == right.lineSpacing &&
            left.leftMargin == right.leftMargin &&
            left.rightMargin == right.rightMargin &&
            left.indentation == right.indentation &&
            left.previousSpacing == right.previousSpacing &&
            left.nextSpacing == right.nextSpacing;
    }

    static LONG ReferenceFormatValue(
        const ReferenceFormat& format,
        const DISPID member) noexcept {
        switch (member) {
        case CharacterHeight:
            return format.height;
        case TextColor:
            return format.textColor;
        case RatioHangul:
            return format.widthRatio;
        case SpacingHangul:
            return format.letterSpacing;
        case AlignType:
            return format.alignment;
        case LineSpacingType:
            return format.lineSpacingType;
        case LineSpacing:
            return format.lineSpacing;
        case LeftMargin:
            return format.leftMargin;
        case RightMargin:
            return format.rightMargin;
        case Indentation:
            return format.indentation;
        case PrevSpacing:
            return format.previousSpacing;
        case NextSpacing:
            return format.nextSpacing;
        default:
            return 0;
        }
    }

    static void ApplyReferenceCharacterFormat(
        ReferenceFormat* const target,
        const ReferenceFormat& source) {
        target->faceName = source.faceName;
        target->height = source.height;
        target->bold = source.bold;
        target->textColor = source.textColor;
        target->widthRatio = source.widthRatio;
        target->letterSpacing = source.letterSpacing;
    }

    static void ApplyReferenceParagraphFormat(
        ReferenceFormat* const target,
        const ReferenceFormat& source) noexcept {
        target->alignment = source.alignment;
        target->lineSpacingType = source.lineSpacingType;
        target->lineSpacing = source.lineSpacing;
        target->leftMargin = source.leftMargin;
        target->rightMargin = source.rightMargin;
        target->indentation = source.indentation;
        target->previousSpacing = source.previousSpacing;
        target->nextSpacing = source.nextSpacing;
    }

    void InitializeReferenceCells() {
        const ReferenceFormat body = BodyReferenceFormat();
        referenceCells_ = {
            {L"A1", 648, 1, 1, 1, 1, 19168, 19173, true, L"", body},
            {L"B1", 649, 1, 2, 1, 1, 28752, 19173, true, L"", body},
            {L"A2", 650, 2, 1, 1, 1, 19168, 26842, true, L"", body},
            {L"B2", 651, 2, 2, 1, 1, 28752, 26842, true, L"", body},
            {L"A3", 652, 3, 1, 1, 1, 19168, 30676, true, L"", body},
            {L"B3", 653, 3, 2, 1, 1, 28752, 30676, true, L"", body},
        };
        pendingReferenceFormat_ = body;
    }

    ReferenceCell* ReferenceCellByList(const LONG listId) noexcept {
        const auto found = std::find_if(
            referenceCells_.begin(),
            referenceCells_.end(),
            [&](const ReferenceCell& cell) {
                return cell.active && cell.listId == listId;
            });
        return found == referenceCells_.end() ? nullptr : &*found;
    }

    const ReferenceCell* ReferenceCellByList(const LONG listId) const noexcept {
        const auto found = std::find_if(
            referenceCells_.begin(),
            referenceCells_.end(),
            [&](const ReferenceCell& cell) {
                return cell.active && cell.listId == listId;
            });
        return found == referenceCells_.end() ? nullptr : &*found;
    }

    ReferenceCell* ReferenceCellByAddress(const std::wstring& address) noexcept {
        const auto found = std::find_if(
            referenceCells_.begin(),
            referenceCells_.end(),
            [&](const ReferenceCell& cell) {
                return cell.active && cell.address == address;
            });
        return found == referenceCells_.end() ? nullptr : &*found;
    }

    const ReferenceCell* ReferenceCellByAddress(
        const std::wstring& address) const noexcept {
        const auto found = std::find_if(
            referenceCells_.begin(),
            referenceCells_.end(),
            [&](const ReferenceCell& cell) {
                return cell.active && cell.address == address;
            });
        return found == referenceCells_.end() ? nullptr : &*found;
    }

    ReferenceCell* ReferenceCurrentCell() noexcept {
        return ReferenceCellByList(currentList_);
    }

    const ReferenceCell* ReferenceCurrentCell() const noexcept {
        return ReferenceCellByList(currentList_);
    }

    ReferenceCell* ReferenceFormattingCell() noexcept {
        return selectionMode_ == 3
            ? ReferenceCellByList(referenceSelectionAnchorList_)
            : ReferenceCurrentCell();
    }

    const ReferenceCell* ReferenceFormattingCell() const noexcept {
        return selectionMode_ == 3
            ? ReferenceCellByList(referenceSelectionAnchorList_)
            : ReferenceCurrentCell();
    }

    void ClearReferenceBorders() noexcept {
        for (ReferenceCell& cell : referenceCells_) {
            cell.borders.clear();
        }
    }

    bool ApplyReferenceVisibleEdge() {
        ReferenceCell* const cell = ReferenceFormattingCell();
        if (cell == nullptr) {
            return false;
        }
        for (const std::wstring& side :
             {L"Left", L"Right", L"Top", L"Bottom"}) {
            LONG type = 0;
            if (!parameterSet_->TryGetLong(
                    L"BorderType" + side,
                    &type)) {
                continue;
            }
            LONG width = 0;
            const std::wstring colorName = side == L"Left"
                ? L"BorderCorlorLeft"
                : L"BorderColor" + side;
            LONG color = 0;
            if (!parameterSet_->TryGetLong(
                    L"BorderWidth" + side,
                    &width) ||
                !parameterSet_->TryGetLong(colorName, &color)) {
                return false;
            }
            cell->borders[side] = ReferenceBorder{type, width, color};
            return true;
        }
        return false;
    }

    void PopulateReferenceBorderReadback(
        const ReferenceCell* const cell) {
        for (const std::wstring& side :
             {L"Left", L"Right", L"Top", L"Bottom"}) {
            ReferenceBorder border;
            if (cell != nullptr) {
                const auto found = cell->borders.find(side);
                if (found != cell->borders.end()) {
                    border = found->second;
                }
            }
            parameterSet_->SetGenericLong(
                L"BorderType" + side,
                border.type);
            parameterSet_->SetGenericLong(
                L"BorderWidth" + side,
                border.width);
            parameterSet_->SetGenericLong(
                side == L"Left"
                    ? L"BorderCorlorLeft"
                    : L"BorderColor" + side,
                border.color);
        }
    }

    size_t ReferenceActiveCellCount() const noexcept {
        return static_cast<size_t>(std::count_if(
            referenceCells_.begin(),
            referenceCells_.end(),
            [](const ReferenceCell& cell) { return cell.active; }));
    }

    bool SetReferenceCurrentCell(const std::wstring& address) noexcept {
        const ReferenceCell* const cell = ReferenceCellByAddress(address);
        if (cell == nullptr) {
            return false;
        }
        currentList_ = cell->listId;
        ReferenceTextPositionAtOffset(
            cell->text,
            cell->text.size(),
            &currentParagraph_,
            &currentCharacter_);
        return true;
    }

    bool SetReferenceLastCell() noexcept {
        const ReferenceCell* last = nullptr;
        for (const ReferenceCell& candidate : referenceCells_) {
            if (!candidate.active) {
                continue;
            }
            if (last == nullptr || candidate.row > last->row ||
                (candidate.row == last->row &&
                 candidate.column > last->column)) {
                last = &candidate;
            }
        }
        return last != nullptr && SetReferenceCurrentCell(last->address);
    }

    bool DeleteFirstReferenceRow() {
        bool deleted = false;
        for (ReferenceCell& cell : referenceCells_) {
            if (!cell.active) {
                continue;
            }
            if (cell.row == 1) {
                cell.active = false;
                deleted = true;
                continue;
            }
            --cell.row;
            cell.address =
                std::wstring(
                    1,
                    static_cast<wchar_t>(L'A' + cell.column - 1)) +
                std::to_wstring(cell.row);
        }
        if (!deleted) {
            return false;
        }
        referenceTopologyChanged_ = true;
        referenceRunTopologyMutated_ = true;
        selectionMode_ = 0;
        return SetReferenceCurrentCell(L"A1");
    }

    bool InsertReferenceRightColumn() noexcept {
        bool inserted = false;
        for (ReferenceCell& cell : referenceCells_) {
            if (cell.column == 2 && !cell.active) {
                cell.active = true;
                inserted = true;
            }
        }
        if (!inserted) {
            return false;
        }
        referenceTopologyChanged_ = true;
        referenceActionTopologyMutated_ = true;
        selectionMode_ = 0;
        return SetReferenceCurrentCell(L"A1");
    }

    bool MergeFirstReferenceRow() noexcept {
        ReferenceCell* const a1 = ReferenceCellByAddress(L"A1");
        ReferenceCell* const b1 = ReferenceCellByAddress(L"B1");
        if (a1 == nullptr || b1 == nullptr) {
            return false;
        }
        b1->active = false;
        a1->columnSpan = 2;
        a1->width = 47920;
        a1->format = BodyReferenceFormat();
        referenceMerged_ = true;
        referenceTopologyChanged_ = true;
        selectionMode_ = 0;
        return SetReferenceCurrentCell(L"A1");
    }

    bool MoveReferenceRight() noexcept {
        const ReferenceCell* const current = ReferenceCurrentCell();
        if (current == nullptr) {
            return false;
        }
        const ReferenceCell* next = nullptr;
        for (const ReferenceCell& candidate : referenceCells_) {
            if (!candidate.active || candidate.row != current->row ||
                candidate.column <= current->column) {
                continue;
            }
            if (next == nullptr || candidate.column < next->column) {
                next = &candidate;
            }
        }
        return next == nullptr || SetReferenceCurrentCell(next->address);
    }

    bool MoveReferenceLower() noexcept {
        const ReferenceCell* const current = ReferenceCurrentCell();
        if (current == nullptr) {
            return false;
        }
        const ReferenceCell* next = nullptr;
        for (const ReferenceCell& candidate : referenceCells_) {
            if (!candidate.active || candidate.row <= current->row ||
                candidate.column > current->column ||
                candidate.column + candidate.columnSpan <= current->column) {
                continue;
            }
            if (next == nullptr || candidate.row < next->row) {
                next = &candidate;
            }
        }
        return next == nullptr || SetReferenceCurrentCell(next->address);
    }

    bool RunReferenceLayoutAction(const std::wstring& action) {
        if (action == L"MoveDocEnd") {
            currentList_ = 0;
            currentParagraph_ = 108;
            currentCharacter_ = 0;
            selectionMode_ = 0;
            tailSelection_ = false;
            return true;
        }
        if (action == L"MoveSelDocEnd") {
            tailSelection_ = currentList_ == 0 && currentParagraph_ == 108 &&
                currentCharacter_ == 0;
            return true;
        }
        if (action == L"Delete" && tailSelection_) {
            if (failAtomicTailDelete_) {
                return false;
            }
            atomicTail_.clear();
            pageCount_ = 1;
            tailSelection_ = false;
            ++atomicTailDeletes_;
            return true;
        }
        if (action == L"Cancel") {
            selectionMode_ = 0;
            tailSelection_ = false;
            return true;
        }
        if (!referenceTableExists_) {
            return true;
        }
        if (action == L"TableDeleteRow") {
            return DeleteFirstReferenceRow();
        }
        if (action == L"TableColEnd") {
            ++referenceTopologyInspections_;
        }
        if (action == L"ShapeObjTextBoxEdit" ||
            action == L"TableColPageUp") {
            return SetReferenceCurrentCell(L"A1");
        }
        if (action == L"TableColEnd" ||
            action == L"TableColPageDown") {
            return SetReferenceLastCell();
        }
        if (action == L"TableColBegin") {
            return SetReferenceCurrentCell(L"A1");
        }
        if (action == L"TableCellBlock") {
            if (preflightSelectionFixture_ &&
                !preflightTableControlSelection_ &&
                preflightSelectionControlCaptured_ &&
                preflightSelectionReadOccurred_) {
                preflightSelectionRestoreAttempted_ = true;
                if (failPreflightSelectionRestore_) {
                    return false;
                }
                preflightSelectionRestored_ = true;
            }
            referenceSelectionAnchorList_ = currentList_;
            selectionMode_ = 3;
            return true;
        }
        if (action == L"TableCellBlockExtend") {
            selectionMode_ = 3;
            return true;
        }
        if (action == L"TableRightCell") {
            return MoveReferenceRight();
        }
        if (action == L"TableLowerCell") {
            return MoveReferenceLower();
        }
        if (action == L"TableMergeCell") {
            return MergeFirstReferenceRow();
        }
        if (action == L"MoveParentList") {
            currentList_ = 0;
            currentParagraph_ = 108;
            currentCharacter_ = 0;
            selectionMode_ = 0;
            return true;
        }
        if (action == L"SelectAll") {
            const ReferenceCell* const cell = ReferenceCurrentCell();
            if (cell == nullptr) {
                return false;
            }
            if (emptyCellTextFixture_ && cell->text.empty()) {
                emptyCellSelectAll_ = true;
                selectedStartList_ = 0;
                selectedStartParagraph_ = 0;
                selectedStartCharacter_ = 0;
                selectedEndList_ = 0;
                selectedEndParagraph_ = 0;
                selectedEndCharacter_ = 0;
                selectionMode_ = 0;
                return true;
            }
            selectedStartList_ = currentList_;
            selectedStartParagraph_ = 0;
            selectedStartCharacter_ = 0;
            selectedEndList_ = currentList_;
            ReferenceTextPositionAtOffset(
                cell->text,
                cell->text.size(),
                &selectedEndParagraph_,
                &selectedEndCharacter_);
            selectionMode_ = 1;
            return true;
        }
        if (action == L"BreakLine" || action == L"BreakPara") {
            if (!InsertReferenceText(L"\n")) {
                return false;
            }
            if (action == L"BreakPara") {
                ++referenceParagraphBreaks_;
            }
        }
        return true;
    }

    struct ParagraphProfile {
        LONG styleId = 17;
        LONG alignment = 0;
        LONG lineSpacing = 160;
        LONG leftMargin = 0;
        LONG rightMargin = 0;
        LONG indentation = 0;
        LONG previousSpacing = 1000;
        LONG nextSpacing = 0;

        bool operator==(const ParagraphProfile& other) const noexcept {
            return styleId == other.styleId &&
                alignment == other.alignment &&
                lineSpacing == other.lineSpacing &&
                leftMargin == other.leftMargin &&
                rightMargin == other.rightMargin &&
                indentation == other.indentation &&
                previousSpacing == other.previousSpacing &&
                nextSpacing == other.nextSpacing;
        }
    };

    static LONG ProfileValue(const ParagraphProfile& profile, const DISPID member) noexcept {
        switch (member) {
        case Apply:
            return profile.styleId;
        case AlignType:
            return profile.alignment;
        case LineSpacing:
            return profile.lineSpacing;
        case LeftMargin:
            return profile.leftMargin;
        case RightMargin:
            return profile.rightMargin;
        case Indentation:
            return profile.indentation;
        case PrevSpacing:
            return profile.previousSpacing;
        case NextSpacing:
            return profile.nextSpacing;
        default:
            return 0;
        }
    }

    void UpdateAtomicTailMaximumOccurrences() noexcept {
        constexpr wchar_t marker[] = L"atomic-tail";
        size_t occurrences = 0;
        size_t offset = 0;
        while ((offset = atomicTail_.find(marker, offset)) != std::wstring::npos) {
            ++occurrences;
            offset += std::wcslen(marker);
        }
        atomicTailMaximumOccurrences_ = (std::max)(
            atomicTailMaximumOccurrences_,
            occurrences);
    }

    ~FakeDispatch() {
        static_cast<void>(DeleteFileW(kSaveEvidenceFile));
        static_cast<void>(parameterSet_->Release());
    }
    volatile LONG references_ = 1;
    LONG windowHandle_ = 0;
    LONG documentId_ = 0;
    LONG pendingDocumentId_ = 0;
    FakeParameterArrayDispatch* parameterSet_ = new FakeParameterArrayDispatch();
    bool referenceLayoutFixture_ = false;
    bool emptyCellTextFixture_ = false;
    bool emptyCellSelectAll_ = false;
    bool preflightSelectionFixture_ = false;
    bool preflightTableControlSelection_ = false;
    bool failPreflightSelectionRestore_ = false;
    bool preflightSelectionControlCaptured_ = false;
    bool preflightSelectionReadOccurred_ = false;
    bool preflightSelectionRestoreAttempted_ = false;
    bool preflightSelectionRestored_ = false;
    bool failReferenceLayoutAfterFirstText_ = false;
    bool dropReferenceEdges_ = false;
    bool referenceTableExists_ = false;
    bool referenceMerged_ = false;
    bool tableSelected_ = false;
    LONG referenceSelectionAnchorList_ = 0;
    LONG selectedStartList_ = 0;
    LONG selectedStartParagraph_ = 0;
    LONG selectedStartCharacter_ = 0;
    LONG selectedEndList_ = 0;
    LONG selectedEndParagraph_ = 0;
    LONG selectedEndCharacter_ = 0;
    size_t referenceTextInsertions_ = 0;
    size_t referenceParagraphBreaks_ = 0;
    size_t referenceEdgeExecutions_ = 0;
    size_t referenceEdgeApplications_ = 0;
    size_t referenceBorderReadbacks_ = 0;
    size_t referenceTopologyInspections_ = 0;
    bool referenceTopologyChanged_ = false;
    bool referenceRunTopologyMutated_ = false;
    bool referenceActionTopologyMutated_ = false;
    bool referenceCallTopologyMutated_ = false;
    bool staleTopologyCellPositionAttempted_ = false;
    size_t referenceFillApplications_ = 0;
    size_t referenceControlDeletes_ = 0;
    ReferenceFormat pendingReferenceFormat_;
    std::vector<ReferenceCell> referenceCells_;
    bool anchorRead_ = false;
    bool positionedAfterAnchor_ = false;
    bool frontSelectedAfterPosition_ = false;
    bool copyUsedAnchorSelection_ = false;
    bool copyCompleted_ = false;
    bool pasteUsedSourceAnchorFormat_ = false;
    bool nativeTableRangeSelected_ = false;
    bool nativeTableBlockCopied_ = false;
    bool nativeTableBlockPasted_ = false;
    bool clipboardTableTransferUsed_ = false;
    bool ranBreakPage_ = false;
    bool failSelectCtrlFront_ = false;
    bool atomicAppendFixture_ = false;
    bool failSelectCtrlFrontWithHresult_ = false;
    bool failAtomicTailDelete_ = false;
    bool tailSelection_ = false;
    bool checkpointRestoreFixture_ = false;
    bool checkpointWrongTargetPageCount_ = false;
    std::wstring checkpointDocumentBlock_;
    size_t checkpointSetAttempts_ = 0;
    LONG pageCount_ = 1;
    LONG currentList_ = 0;
    LONG currentParagraph_ = 0;
    LONG currentCharacter_ = 0;
    LONG selectionMode_ = 1;
    bool textDeletionFixture_ = false;
    bool textSelectionActive_ = true;
    size_t textDeletionActions_ = 0;
    size_t insertTextExecutions_ = 0;
    std::wstring atomicTail_;
    size_t atomicTailDeletes_ = 0;
    size_t atomicTailMaximumOccurrences_ = 0;
    bool scanStarted_ = false;
    bool scanReleased_ = false;
    bool actionInputApplied_ = false;
    LONG scanStep_ = 0;
    std::wstring currentAction_;
    std::wstring insertedText_;
    std::vector<std::wstring> runActions_;
    static constexpr LONG kInitialMessageBoxMode = 0x00000020;
    LONG messageBoxMode_ = kInitialMessageBoxMode;
    bool autoYesMessageBoxModeUsed_ = false;
    bool failNextMessageBoxModeRestore_ = false;
    size_t messageBoxModeRestoreAttempts_ = 0;
    DISPID activeParameter_ = DISPID_UNKNOWN;
    const ParagraphProfile sourceProfile_{};
    const ParagraphProfile destinationProfile_{2, 0, 160, 4000, 0, -2880, 0, 0};
    ParagraphProfile currentProfile_{};
    ParagraphProfile pendingProfile_{};
    std::vector<std::wstring> lifecycleCalls_;
    bool saveCalledWithTrue_ = false;
    bool clearCalledWithDiscard_ = false;
    bool openCalledWithArguments_ = false;
    bool lifecycleRecovered_ = false;
    bool saveAsCalledWithArguments_ = false;
    bool lifecycleRecoveryBlockCaptured_ = true;
    bool lifecycleMismatchAfterOpen_ = false;
    bool lifecycleMismatchActive_ = false;
    size_t clearCallCount_ = 0;
    bool saveReturnsTrue_ = true;
    bool saveClearsModified_ = true;
    bool openReturnsTrue_ = true;
    bool modified_ = false;
    bool documentOpen_ = true;
    std::wstring fullName_ = L"C:\\x.hwp";
    std::wstring lifecyclePath_ = L"C:\\\uD55C\uAE00\\x.hwp";
    std::wstring lifecycleReopenedPath_ = L"C:\\\uD55C\uAE00\\X.HWP";
    std::wstring lifecycleFormat_ = L"HWP";
    bool inspectionScopeFixture_ = false;
    std::vector<InspectionControl> inspectionControls_;
    LONG inspectionControlIndex_ = -1;
    bool inspectionAnchorRoot_ = false;
    LONG inspectionCurrentPage_ = 0;
    size_t inspectionHeadReads_ = 0;
    size_t inspectionLastReads_ = 0;
    size_t inspectionNextReads_ = 0;
    size_t inspectionPrevReads_ = 0;
    size_t inspectionIdentityReads_ = 0;
    bool requireActiveDocumentFullName_ = false;
    bool activeDocumentFullNameReady_ = false;
    LONG activeDocumentFullNameReads_ = 0;
    bool unstableSerializationMetadataFixture_ = false;
    bool unstableCaretMetadataFixture_ = false;
    bool diagnosticSectionMismatchFixture_ = false;
    std::wstring diagnosticSectionBeforeHwpml_;
    std::wstring diagnosticSectionAfterHwpml_;
    size_t serializationReads_ = 0;
    inline static constexpr wchar_t kSaveEvidenceFile[] =
        L"HancomLiveBridgeSaveSmoke.hwp";
};

using QueryModule = IHncUserActionModule*(__stdcall*)();
using GetLastResult = HRESULT(__stdcall*)();
using ReleasePublication = HRESULT(__stdcall*)();

IUnknown* GetPublishedObject(const std::wstring& itemName) {
    IRunningObjectTable* table = nullptr;
    if (FAILED(GetRunningObjectTable(0, &table))) {
        return nullptr;
    }
    IMoniker* moniker = nullptr;
    HRESULT result = CreateItemMoniker(L"!", itemName.c_str(), &moniker);
    IUnknown* object = nullptr;
    if (SUCCEEDED(result)) {
        result = table->GetObject(moniker, &object);
        moniker->Release();
    }
    table->Release();
    if (FAILED(result)) {
        return nullptr;
    }
    return object;
}

bool IsPublishedObject(const std::wstring& itemName) {
    IRunningObjectTable* table = nullptr;
    if (FAILED(GetRunningObjectTable(0, &table))) {
        return false;
    }
    IMoniker* moniker = nullptr;
    HRESULT result = CreateItemMoniker(L"!", itemName.c_str(), &moniker);
    if (SUCCEEDED(result)) {
        result = table->IsRunning(moniker);
        moniker->Release();
    }
    table->Release();
    return result == S_OK;
}

bool ReadProtocolVersion(IDispatch* const batch) {
    LPOLESTR name = const_cast<LPOLESTR>(L"ProtocolVersion");
    DISPID member = DISPID_UNKNOWN;
    if (FAILED(batch->GetIDsOfNames(
            IID_NULL, &name, 1, LOCALE_USER_DEFAULT, &member))) {
        return false;
    }
    DISPPARAMS parameters{};
    VARIANT result;
    VariantInit(&result);
    const HRESULT status = batch->Invoke(
        member,
        IID_NULL,
        LOCALE_USER_DEFAULT,
        DISPATCH_PROPERTYGET,
        &parameters,
        &result,
        nullptr,
        nullptr);
    const bool matched = SUCCEEDED(status) && result.vt == VT_I4 && result.lVal == 12;
    VariantClear(&result);
    return matched;
}

bool InvokeString(
    IDispatch* const batch,
    const wchar_t* const method,
    const wchar_t* const argument,
    std::wstring* const returned) {
    LPOLESTR name = const_cast<LPOLESTR>(method);
    DISPID member = DISPID_UNKNOWN;
    if (FAILED(batch->GetIDsOfNames(
            IID_NULL, &name, 1, LOCALE_USER_DEFAULT, &member))) {
        return false;
    }
    VARIANTARG input;
    VariantInit(&input);
    DISPPARAMS parameters{};
    if (argument != nullptr) {
        input.vt = VT_BSTR;
        input.bstrVal = SysAllocString(argument);
        if (input.bstrVal == nullptr) {
            return false;
        }
        parameters.rgvarg = &input;
        parameters.cArgs = 1;
    }
    VARIANT result;
    VariantInit(&result);
    const HRESULT status = batch->Invoke(
        member,
        IID_NULL,
        LOCALE_USER_DEFAULT,
        DISPATCH_METHOD,
        &parameters,
        &result,
        nullptr,
        nullptr);
    VariantClear(&input);
    const bool valid = SUCCEEDED(status) && result.vt == VT_BSTR && result.bstrVal != nullptr;
    if (valid) {
        returned->assign(result.bstrVal, SysStringLen(result.bstrVal));
    }
    VariantClear(&result);
    return valid;
}

bool InvokeLongString(
    IDispatch* const batch,
    const wchar_t* const method,
    const LONG argument,
    std::wstring* const returned) {
    if (returned == nullptr) {
        return false;
    }
    LPOLESTR name = const_cast<LPOLESTR>(method);
    DISPID member = DISPID_UNKNOWN;
    if (FAILED(batch->GetIDsOfNames(
            IID_NULL, &name, 1, LOCALE_USER_DEFAULT, &member))) {
        return false;
    }
    VARIANTARG input;
    VariantInit(&input);
    input.vt = VT_I4;
    input.lVal = argument;
    DISPPARAMS parameters{};
    parameters.rgvarg = &input;
    parameters.cArgs = 1;
    VARIANT result;
    VariantInit(&result);
    const HRESULT status = batch->Invoke(
        member,
        IID_NULL,
        LOCALE_USER_DEFAULT,
        DISPATCH_METHOD,
        &parameters,
        &result,
        nullptr,
        nullptr);
    const bool valid = SUCCEEDED(status) &&
        result.vt == VT_BSTR &&
        result.bstrVal != nullptr;
    if (valid) {
        returned->assign(result.bstrVal, SysStringLen(result.bstrVal));
    }
    VariantClear(&result);
    return valid;
}

bool ActivateDocumentAndWait(
    IDispatch* const batch,
    const LONG documentId) {
    std::wstring token;
    if (!InvokeLongString(
            batch,
            L"ActivateDocument",
            documentId,
            &token)) {
        return false;
    }
    HANDLE const success = OpenEventW(
        SYNCHRONIZE,
        FALSE,
        (token + L".Success").c_str());
    HANDLE const failure = OpenEventW(
        SYNCHRONIZE,
        FALSE,
        (token + L".Failure").c_str());
    if (success == nullptr || failure == nullptr) {
        if (success != nullptr) {
            CloseHandle(success);
        }
        if (failure != nullptr) {
            CloseHandle(failure);
        }
        return false;
    }
    HANDLE handles[2] = {success, failure};
    const DWORD waited = WaitForMultipleObjects(
        2,
        handles,
        FALSE,
        5'000);
    CloseHandle(failure);
    CloseHandle(success);
    return waited == WAIT_OBJECT_0;
}

std::vector<std::wstring> SplitTabs(const std::wstring& value) {
    std::vector<std::wstring> fields;
    size_t start = 0;
    for (;;) {
        const size_t end = value.find(L'\t', start);
        fields.push_back(value.substr(start, end - start));
        if (end == std::wstring::npos) {
            return fields;
        }
        start = end + 1;
    }
}

std::vector<std::wstring> SplitCommas(const std::wstring& value) {
    std::vector<std::wstring> fields;
    size_t start = 0;
    for (;;) {
        const size_t end = value.find(L',', start);
        fields.push_back(value.substr(start, end - start));
        if (end == std::wstring::npos) {
            return fields;
        }
        start = end + 1;
    }
}

bool IsSectionHash(const std::wstring& value) {
    return value.size() == 16 &&
        value.find_first_not_of(L"0123456789abcdef") == std::wstring::npos;
}

bool MatchingFingerprintDiagnostics(
    const std::vector<std::wstring>& fields,
    const size_t start) {
    if (fields.size() < start + 4 ||
        fields[start].empty() ||
        fields[start] != fields[start + 1] ||
        fields[start + 2].empty() ||
        fields[start + 2] != fields[start + 3]) {
        return false;
    }
    const std::vector<std::wstring> sections =
        SplitCommas(fields[start + 2]);
    return std::all_of(sections.begin(), sections.end(), IsSectionHash);
}

bool SingleSectionDiagnosticMismatch(
    const std::wstring& before,
    const std::wstring& after,
    const size_t expectedIndex) {
    const std::vector<std::wstring> beforeSections = SplitCommas(before);
    const std::vector<std::wstring> afterSections = SplitCommas(after);
    if (beforeSections.size() != afterSections.size() ||
        expectedIndex >= beforeSections.size()) {
        return false;
    }
    size_t mismatchCount = 0;
    for (size_t index = 0; index < beforeSections.size(); ++index) {
        if (!IsSectionHash(beforeSections[index]) ||
            !IsSectionHash(afterSections[index])) {
            return false;
        }
        if (beforeSections[index] != afterSections[index]) {
            if (index != expectedIndex) {
                return false;
            }
            ++mismatchCount;
        }
    }
    return mismatchCount == 1;
}

bool AtomicRollbackSucceeded(const std::wstring& response) {
    const std::vector<std::wstring> fields = SplitTabs(response);
    return fields.size() == 11 && fields[0] == L"HCA2" && fields[1] == L"ERROR" &&
        fields[2] == L"COM_METHOD" && fields[3] == L"U2VsZWN0Q3RybEZyb250" &&
        fields[4] == L"UnVuIGZhaWxlZCAoSFJFU1VMVCAweDgwMDA0MDA1KQ==" &&
        fields[5] == L"3" && fields[6] == fields[3] && fields[7] == L"0" &&
        fields[8] == L"1" && !fields[9].empty() && fields[9] == fields[10];
}

bool ReferenceLayoutSucceeded(const std::wstring& response) {
    const std::vector<std::wstring> fields = SplitTabs(response);
    return fields.size() == 12 && fields[0] == L"HCA2" && fields[1] == L"OK" &&
        fields[2] == L"2" && fields[4] == L"5" && !fields[7].empty();
}

bool ReferenceLayoutAtomicRollbackSucceeded(const std::wstring& response) {
    const std::vector<std::wstring> fields = SplitTabs(response);
    return fields.size() == 11 && fields[0] == L"HCA2" && fields[1] == L"ERROR" &&
        !fields[2].empty() && !fields[3].empty() && !fields[4].empty() &&
        fields[5] == L"1" && !fields[6].empty() &&
        fields[7] == L"0" && fields[8] == L"1" && !fields[9].empty() &&
        fields[9] == fields[10];
}

bool ReferenceLayoutDroppedEdgesRejected(const std::wstring& response) {
    const std::vector<std::wstring> fields = SplitTabs(response);
    return fields.size() == 11 && fields[0] == L"HCA2" &&
        fields[1] == L"ERROR" &&
        fields[2] == L"REFERENCE_LAYOUT_VERIFY" &&
        fields[3] == L"QTE6VG9w" &&
        !fields[4].empty();
}

bool SafeStaleCellPreflightFailed(const std::wstring& response) {
    const std::vector<std::wstring> fields = SplitTabs(response);
    return fields.size() == 11 && fields[0] == L"HCA2" && fields[1] == L"ERROR" &&
        fields[2] == L"STALE_CELL_TEXT" && !fields[3].empty() &&
        !fields[4].empty() && !fields[6].empty() &&
        fields[7] == L"0" && fields[8] == L"1" && !fields[9].empty() &&
        fields[9] == fields[10];
}

bool CellTopologyInvalidationFailedSafely(const std::wstring& response) {
    const std::vector<std::wstring> fields = SplitTabs(response);
    return fields.size() == 11 && fields[0] == L"HCA2" &&
        fields[1] == L"ERROR" && fields[2] == L"CELL_NOT_FOUND" &&
        !fields[3].empty() && !fields[4].empty();
}

bool OversizedCellPatchRejectedSafely(const std::wstring& response) {
    const std::vector<std::wstring> fields = SplitTabs(response);
    return fields.size() == 11 && fields[0] == L"HCA2" &&
        fields[1] == L"ERROR" && fields[2] == L"TEXT_PATCH_LIMIT" &&
        !fields[3].empty() && !fields[4].empty() &&
        fields[5] == L"0" && fields[7] == L"0" && fields[8] == L"1";
}

std::wstring OversizedCellPatchPayload() {
    std::wstring payload =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
        L"SELECT_CONTROL\tcmVmZXJlbmNlLXRhYmxl\n"
        L"CAPTURE_TABLE\n"
        L"CELL\tA1\n";
    for (size_t index = 0; index < 101; ++index) {
        payload +=
            L"PATCH_TEXT\tCELL\tcmVmZXJlbmNlLXRhYmxl\tB2\t1\t1\t"
            L"b2xk\tbmV3\t1\n";
    }
    payload += L"END";
    return payload;
}

bool SafeSelectionRestoreFailed(const std::wstring& response) {
    const std::vector<std::wstring> fields = SplitTabs(response);
    return fields.size() == 11 && fields[0] == L"HCA2" && fields[1] == L"ERROR" &&
        fields[2] == L"STATE_RESTORE" && !fields[3].empty() &&
        !fields[4].empty() && fields[5] == L"0" && !fields[6].empty() &&
        fields[7] == L"0" && fields[8] == L"0" && !fields[9].empty() &&
        fields[9] == fields[10];
}

bool AtomicRollbackFailurePreservedOriginal(
    const std::wstring& succeeded,
    const std::wstring& failed) {
    const std::vector<std::wstring> succeededFields = SplitTabs(succeeded);
    const std::vector<std::wstring> failedFields = SplitTabs(failed);
    return succeededFields.size() == 11 && failedFields.size() == 11 &&
        failedFields[0] == L"HCA2" && failedFields[1] == L"ERROR" &&
        failedFields[2] == succeededFields[2] && failedFields[3] == succeededFields[3] &&
        failedFields[4] == succeededFields[4] && failedFields[5] == L"3" &&
        failedFields[6] == succeededFields[6] && failedFields[7] == L"1" &&
        failedFields[8] == L"0" && !failedFields[9].empty() &&
        !failedFields[10].empty() && failedFields[9] != failedFields[10];
}

bool SaveVerifyMatched(const std::wstring& response) {
    const std::vector<std::wstring> fields = SplitTabs(response);
    return fields.size() == 25 &&
        fields[0] == L"HLS1" &&
        fields[1] == L"1" &&
        !fields[2].empty() &&
        fields[3] == L"1" &&
        fields[4] == L"1" &&
        fields[5] == L"1" &&
        !fields[6].empty() &&
        !fields[7].empty() &&
        !fields[8].empty() &&
        fields[9] == L"0" &&
        fields[10] == L"1" &&
        fields[11] == L"0" &&
        fields[12] == fields[3] &&
        fields[13] == L"0" &&
        fields[14] == fields[5] &&
        fields[15] == fields[6] &&
        fields[16] == fields[7] &&
        fields[17] == fields[8] &&
        fields[18] == L"3" &&
        !fields[19].empty() &&
        fields[19].find_first_not_of(L"0123456789") == std::wstring::npos &&
        fields[20].find_first_not_of(L"0123456789") == std::wstring::npos &&
        MatchingFingerprintDiagnostics(fields, 21);
}

bool SaveVerifySectionDiagnosticsMatched(const std::wstring& response) {
    const std::vector<std::wstring> fields = SplitTabs(response);
    return fields.size() == 25 &&
        fields[0] == L"HLS1" &&
        fields[1] == L"0" &&
        fields[7] == fields[16] &&
        fields[8] != fields[17] &&
        fields[21] == fields[22] &&
        SingleSectionDiagnosticMismatch(fields[23], fields[24], 64) &&
        fields[20].find_first_not_of(L"0123456789") == std::wstring::npos;
}

bool LifecycleSuccessMatchedForPath(
    const std::wstring& response,
    const std::wstring& encodedPath) {
    const std::vector<std::wstring> fields = SplitTabs(response);
    const std::wstring pending = std::to_wstring(static_cast<LONG>(E_PENDING));
    return fields.size() == 30 && fields[0] == L"HCL12" && fields[1] == L"1" &&
        fields[2] == encodedPath && fields[3] == L"1" && fields[4] == L"1" &&
        fields[5] == L"1" && !fields[6].empty() && !fields[7].empty() &&
        !fields[8].empty() && fields[9] == L"0" && fields[10] == L"1" &&
        fields[11] == L"0" && fields[12] == L"0" && fields[13] == L"-1" &&
        fields[14] == L"0" && fields[15] == L"1" && fields[16] == L"0" &&
        fields[17] == pending && fields[18] == L"-1" && fields[19] == fields[3] &&
        fields[20] == L"0" && fields[21] == fields[5] && fields[22] == fields[6] &&
        fields[23] == fields[7] && fields[24] == fields[8] &&
        fields[25].find_first_not_of(L"0123456789") == std::wstring::npos &&
        MatchingFingerprintDiagnostics(fields, 26);
}

bool LifecycleSuccessMatched(const std::wstring& response) {
    return LifecycleSuccessMatchedForPath(
        response,
        L"Qzpc7ZWc6riAXFguSFdQ");
}

bool LifecycleCleanNoOpMatched(const std::wstring& response) {
    const std::vector<std::wstring> fields = SplitTabs(response);
    return LifecycleSuccessMatched(response) ||
        (fields.size() == 30 && fields[0] == L"HCL12" && fields[1] == L"1" &&
         fields[4] == L"0" && fields[10] == L"0" && fields[20] == L"0" &&
         fields[22] == fields[6] && fields[23] == fields[7] &&
         fields[24] == fields[8]);
}

bool LifecycleSaveGateMatched(const std::wstring& response) {
    const std::vector<std::wstring> fields = SplitTabs(response);
    const std::wstring pending = std::to_wstring(static_cast<LONG>(E_PENDING));
    return fields.size() == 30 && fields[0] == L"HCL12" && fields[1] == L"0" &&
        fields[4] == L"1" && fields[9] == L"0" && fields[10] == L"1" &&
        fields[11] == L"1" && fields[12] == pending && fields[13] == L"-1" &&
        fields[14] == pending && fields[15] == L"-1" && fields[16] == L"0" &&
        fields[17] == pending && fields[18] == L"-1" && fields[20] == L"1" &&
        fields[22] == fields[6] && fields[23] == fields[7] &&
        fields[24] == fields[8];
}

bool LifecycleSaveReturnGateMatched(const std::wstring& response) {
    const std::vector<std::wstring> fields = SplitTabs(response);
    const std::wstring pending = std::to_wstring(static_cast<LONG>(E_PENDING));
    return fields.size() == 30 && fields[0] == L"HCL12" && fields[1] == L"0" &&
        fields[4] == L"1" && fields[9] == L"0" && fields[10] == L"0" &&
        fields[11] == L"0" && fields[12] == pending && fields[13] == L"-1" &&
        fields[14] == pending && fields[15] == L"-1" && fields[16] == L"0" &&
        fields[17] == pending && fields[18] == L"-1" && fields[20] == L"0" &&
        fields[22] == fields[6] && fields[23] == fields[7] &&
        fields[24] == fields[8];
}

bool LifecycleRecoveryCaptureGateMatched(const std::wstring& response) {
    const std::vector<std::wstring> fields = SplitTabs(response);
    const std::wstring aborted = std::to_wstring(static_cast<LONG>(E_ABORT));
    const std::wstring pending = std::to_wstring(static_cast<LONG>(E_PENDING));
    return fields.size() == 30 && fields[0] == L"HCL12" && fields[1] == L"0" &&
        fields[2] == L"Qzpc7ZWc6riAXHguaHdw" &&
        fields[3] == L"1" && fields[4] == L"1" && fields[5] == L"1" &&
        !fields[6].empty() && !fields[7].empty() && !fields[8].empty() &&
        fields[9] == L"0" && fields[10] == L"1" && fields[11] == L"0" &&
        fields[12] == aborted && fields[13] == L"-1" &&
        fields[14] == pending && fields[15] == L"-1" && fields[16] == L"0" &&
        fields[17] == pending && fields[18] == L"-1" &&
        fields[19] == fields[3] && fields[20] == L"0" &&
        fields[21] == fields[5] && fields[22] == fields[6] &&
        fields[23] == fields[7] && fields[24] == fields[8] &&
        fields[25].find_first_not_of(L"0123456789") == std::wstring::npos;
}

bool LifecycleRecoveryMatchedForPath(
    const std::wstring& response,
    const std::wstring& encodedPath,
    const std::wstring& openReturn) {
    const std::vector<std::wstring> fields = SplitTabs(response);
    return fields.size() == 30 && fields[0] == L"HCL12" && fields[1] == L"0" &&
        fields[2] == encodedPath &&
        fields[9] == L"0" && fields[10] == L"1" &&
        fields[11] == L"0" && fields[12] == L"0" && fields[13] == L"-1" &&
        fields[14] == L"0" && fields[15] == openReturn && fields[16] == L"1" &&
        fields[17] == L"0" && fields[18] == L"1" && fields[19] == fields[3] &&
        fields[20] == L"0" && fields[21] == fields[5] &&
        fields[22] == fields[6] && fields[23] == fields[7] &&
        fields[24] == fields[8];
}

bool LifecycleRecoveryMatched(const std::wstring& response) {
    return LifecycleRecoveryMatchedForPath(
        response,
        L"Qzpc7ZWc6riAXHguaHdw",
        L"0");
}

bool ReadBridgeStatus(
    const DWORD processId,
    bridge_status::Snapshot* const copied) {
    wchar_t name[96] = {};
    if (swprintf_s(
            name,
            L"%s%lu",
            bridge_status::kMappingPrefix,
            static_cast<unsigned long>(processId)) < 0) {
        return false;
    }
    HANDLE const mapping = OpenFileMappingW(FILE_MAP_READ, FALSE, name);
    if (mapping == nullptr) {
        return false;
    }
    const auto* const shared = static_cast<const bridge_status::Snapshot*>(
        MapViewOfFile(
            mapping,
            FILE_MAP_READ,
            0,
            0,
            sizeof(bridge_status::Snapshot)));
    if (shared == nullptr) {
        CloseHandle(mapping);
        return false;
    }

    bool stable = false;
    for (int attempt = 0; attempt < 8; ++attempt) {
        const LONG before = shared->sequence;
        if ((before & 1) != 0) {
            SwitchToThread();
            continue;
        }
        MemoryBarrier();
        std::memcpy(copied, shared, sizeof(*copied));
        MemoryBarrier();
        const LONG after = shared->sequence;
        if (before == after && (after & 1) == 0) {
            stable = true;
            break;
        }
    }
    UnmapViewOfFile(shared);
    CloseHandle(mapping);
    return stable && copied->magic == bridge_status::kMagic &&
        copied->version == bridge_status::kVersion &&
        copied->size == sizeof(bridge_status::Snapshot) &&
        copied->processId == processId;
}

std::wstring IntegrityLevelName(const DWORD processId) {
    HANDLE process = GetCurrentProcess();
    bool closeProcess = false;
    if (processId != GetCurrentProcessId()) {
        process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, processId);
        closeProcess = true;
    }
    if (process == nullptr) {
        return L"unavailable";
    }

    HANDLE token = nullptr;
    if (!OpenProcessToken(process, TOKEN_QUERY, &token)) {
        if (closeProcess) {
            CloseHandle(process);
        }
        return L"unavailable";
    }
    DWORD required = 0;
    static_cast<void>(GetTokenInformation(
        token,
        TokenIntegrityLevel,
        nullptr,
        0,
        &required));
    std::vector<BYTE> storage(required);
    const bool available = required != 0 && GetTokenInformation(
        token,
        TokenIntegrityLevel,
        storage.data(),
        required,
        &required);
    DWORD level = 0;
    if (available) {
        const auto* const label = reinterpret_cast<const TOKEN_MANDATORY_LABEL*>(
            storage.data());
        const UCHAR count = *GetSidSubAuthorityCount(label->Label.Sid);
        if (count != 0) {
            level = *GetSidSubAuthority(label->Label.Sid, count - 1);
        }
    }
    CloseHandle(token);
    if (closeProcess) {
        CloseHandle(process);
    }
    if (!available) {
        return L"unavailable";
    }
    if (level >= SECURITY_MANDATORY_SYSTEM_RID) {
        return L"system";
    }
    if (level >= SECURITY_MANDATORY_HIGH_RID) {
        return L"high";
    }
    if (level > SECURITY_MANDATORY_MEDIUM_RID) {
        return L"medium-plus";
    }
    if (level >= SECURITY_MANDATORY_MEDIUM_RID) {
        return L"medium";
    }
    if (level >= SECURITY_MANDATORY_LOW_RID) {
        return L"low";
    }
    return L"untrusted";
}

bool CanUseCurrentProcessToken(
    HANDLE const targetToken,
    const DWORD targetProcessId) {
    HANDLE currentToken = nullptr;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &currentToken)) {
        return false;
    }

    TOKEN_STATISTICS currentStatistics{};
    TOKEN_STATISTICS targetStatistics{};
    DWORD returned = 0;
    const bool statisticsAvailable =
        GetTokenInformation(
            currentToken,
            TokenStatistics,
            &currentStatistics,
            sizeof(currentStatistics),
            &returned) &&
        GetTokenInformation(
            targetToken,
            TokenStatistics,
            &targetStatistics,
            sizeof(targetStatistics),
            &returned);
    const bool sameLogon = statisticsAvailable &&
        currentStatistics.AuthenticationId.LowPart ==
            targetStatistics.AuthenticationId.LowPart &&
        currentStatistics.AuthenticationId.HighPart ==
            targetStatistics.AuthenticationId.HighPart;
    const bool sameRestriction =
        IsTokenRestricted(currentToken) == IsTokenRestricted(targetToken);
    CloseHandle(currentToken);
    if (!sameLogon || !sameRestriction) {
        return false;
    }

    const std::wstring currentIntegrity = IntegrityLevelName(GetCurrentProcessId());
    const std::wstring targetIntegrity = IntegrityLevelName(targetProcessId);
    return currentIntegrity != L"unavailable" && currentIntegrity == targetIntegrity;
}

int ProbeUsingTargetToken(
    const wchar_t* const processIdArgument,
    const wchar_t* const outputPath) {
    wchar_t* end = nullptr;
    const unsigned long parsed = std::wcstoul(processIdArgument, &end, 10);
    if (processIdArgument[0] == L'\0' || end == nullptr || *end != L'\0' ||
        parsed == 0 || parsed > (std::numeric_limits<DWORD>::max)()) {
        std::wcerr << L"invalid process id: " << processIdArgument << L'\n';
        return 2;
    }

    HANDLE const process = OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION,
        FALSE,
        static_cast<DWORD>(parsed));
    if (process == nullptr) {
        std::wcerr << L"OpenProcess failed: " << GetLastError() << L'\n';
        return 12;
    }
    HANDLE sourceToken = nullptr;
    if (!OpenProcessToken(
            process,
            TOKEN_QUERY | TOKEN_DUPLICATE | TOKEN_ASSIGN_PRIMARY,
            &sourceToken)) {
        std::wcerr << L"OpenProcessToken failed: " << GetLastError() << L'\n';
        CloseHandle(process);
        return 13;
    }
    HANDLE primaryToken = nullptr;
    if (!DuplicateTokenEx(
            sourceToken,
            MAXIMUM_ALLOWED,
            nullptr,
            SecurityImpersonation,
            TokenPrimary,
            &primaryToken)) {
        std::wcerr << L"DuplicateTokenEx failed: " << GetLastError() << L'\n';
        CloseHandle(sourceToken);
        CloseHandle(process);
        return 14;
    }

    wchar_t executable[MAX_PATH] = {};
    const DWORD length = GetModuleFileNameW(nullptr, executable, ARRAYSIZE(executable));
    if (length == 0 || length == ARRAYSIZE(executable)) {
        std::wcerr << L"GetModuleFileNameW failed: " << GetLastError() << L'\n';
        CloseHandle(primaryToken);
        CloseHandle(sourceToken);
        CloseHandle(process);
        return 15;
    }
    std::wstring commandLine = L"\"" + std::wstring(executable) +
        L"\" --probe-output " + processIdArgument + L" \"" + outputPath + L"\"";
    std::vector<wchar_t> mutableCommand(commandLine.begin(), commandLine.end());
    mutableCommand.push_back(L'\0');
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION child{};
    const bool useCurrentProcessToken = CanUseCurrentProcessToken(
        sourceToken,
        static_cast<DWORD>(parsed));
    const BOOL created = useCurrentProcessToken
        ? CreateProcessW(
            executable,
            mutableCommand.data(),
            nullptr,
            nullptr,
            FALSE,
            CREATE_NO_WINDOW,
            nullptr,
            nullptr,
            &startup,
            &child)
        : CreateProcessWithTokenW(
            primaryToken,
            0,
            executable,
            mutableCommand.data(),
            CREATE_NO_WINDOW,
            nullptr,
            nullptr,
            &startup,
            &child);
    CloseHandle(primaryToken);
    CloseHandle(sourceToken);
    CloseHandle(process);
    if (!created) {
        std::wcerr << (useCurrentProcessToken
            ? L"CreateProcessW failed: "
            : L"CreateProcessWithTokenW failed: ") << GetLastError() << L'\n';
        return 16;
    }

    const DWORD waited = WaitForSingleObject(child.hProcess, 30'000);
    DWORD exitCode = 17;
    if (waited == WAIT_OBJECT_0) {
        static_cast<void>(GetExitCodeProcess(child.hProcess, &exitCode));
    }
    CloseHandle(child.hThread);
    CloseHandle(child.hProcess);
    return static_cast<int>(exitCode);
}

std::wstring QuoteCommandArgument(const wchar_t* const argument) {
    const std::wstring value(argument);
    if (!value.empty() && value.find_first_of(L" \t\n\v\"") == std::wstring::npos) {
        return value;
    }
    std::wstring quoted(1, L'\"');
    size_t slashes = 0;
    for (const wchar_t character : value) {
        if (character == L'\\') {
            ++slashes;
            continue;
        }
        if (character == L'\"') {
            quoted.append(slashes * 2 + 1, L'\\');
            quoted.push_back(L'\"');
        } else {
            quoted.append(slashes, L'\\');
            quoted.push_back(character);
        }
        slashes = 0;
    }
    quoted.append(slashes * 2, L'\\');
    quoted.push_back(L'\"');
    return quoted;
}

int RunUsingTargetToken(
    const wchar_t* const processIdArgument,
    const wchar_t* const outputPath,
    const wchar_t* const inputPath,
    const int commandArgumentCount,
    wchar_t** const commandArguments) {
    wchar_t* end = nullptr;
    const unsigned long parsed = std::wcstoul(processIdArgument, &end, 10);
    if (processIdArgument[0] == L'\0' || end == nullptr || *end != L'\0' ||
        parsed == 0 || parsed > (std::numeric_limits<DWORD>::max)()) {
        std::wcerr << L"invalid process id: " << processIdArgument << L'\n';
        return 2;
    }
    if (commandArgumentCount < 1 || commandArguments[0][0] == L'\0') {
        std::wcerr << L"child executable is required\n";
        return 2;
    }

    HANDLE const process = OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION,
        FALSE,
        static_cast<DWORD>(parsed));
    if (process == nullptr) {
        std::wcerr << L"OpenProcess failed: " << GetLastError() << L'\n';
        return 12;
    }
    HANDLE sourceToken = nullptr;
    if (!OpenProcessToken(
            process,
            TOKEN_QUERY | TOKEN_DUPLICATE | TOKEN_ASSIGN_PRIMARY,
            &sourceToken)) {
        std::wcerr << L"OpenProcessToken failed: " << GetLastError() << L'\n';
        CloseHandle(process);
        return 13;
    }
    HANDLE primaryToken = nullptr;
    if (!DuplicateTokenEx(
            sourceToken,
            MAXIMUM_ALLOWED,
            nullptr,
            SecurityImpersonation,
            TokenPrimary,
            &primaryToken)) {
        std::wcerr << L"DuplicateTokenEx failed: " << GetLastError() << L'\n';
        CloseHandle(sourceToken);
        CloseHandle(process);
        return 14;
    }

    SECURITY_ATTRIBUTES security{};
    security.nLength = sizeof(security);
    security.bInheritHandle = TRUE;
    HANDLE const output = CreateFileW(
        outputPath,
        GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        &security,
        CREATE_ALWAYS,
        FILE_ATTRIBUTE_NORMAL,
        nullptr);
    HANDLE const input = CreateFileW(
        inputPath,
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        &security,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        nullptr);
    if (output == INVALID_HANDLE_VALUE || input == INVALID_HANDLE_VALUE) {
        std::wcerr << L"standard handle setup failed: " << GetLastError() << L'\n';
        if (output != INVALID_HANDLE_VALUE) {
            CloseHandle(output);
        }
        if (input != INVALID_HANDLE_VALUE) {
            CloseHandle(input);
        }
        CloseHandle(primaryToken);
        CloseHandle(sourceToken);
        CloseHandle(process);
        return 18;
    }

    std::wstring commandLine;
    for (int index = 0; index < commandArgumentCount; ++index) {
        if (!commandLine.empty()) {
            commandLine.push_back(L' ');
        }
        commandLine += QuoteCommandArgument(commandArguments[index]);
    }
    std::vector<wchar_t> mutableCommand(commandLine.begin(), commandLine.end());
    mutableCommand.push_back(L'\0');
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = input;
    startup.hStdOutput = output;
    startup.hStdError = output;
    PROCESS_INFORMATION child{};
    const bool useCurrentProcessToken = CanUseCurrentProcessToken(
        sourceToken,
        static_cast<DWORD>(parsed));
    const BOOL created = useCurrentProcessToken
        ? CreateProcessW(
            commandArguments[0],
            mutableCommand.data(),
            nullptr,
            nullptr,
            TRUE,
            CREATE_NO_WINDOW,
            nullptr,
            nullptr,
            &startup,
            &child)
        : CreateProcessWithTokenW(
            primaryToken,
            0,
            commandArguments[0],
            mutableCommand.data(),
            CREATE_NO_WINDOW,
            nullptr,
            nullptr,
            &startup,
            &child);
    CloseHandle(input);
    CloseHandle(output);
    CloseHandle(primaryToken);
    CloseHandle(sourceToken);
    CloseHandle(process);
    if (!created) {
        std::wcerr << (useCurrentProcessToken
            ? L"CreateProcessW failed: "
            : L"CreateProcessWithTokenW failed: ") << GetLastError() << L'\n';
        return 16;
    }

    static_cast<void>(WaitForSingleObject(child.hProcess, INFINITE));
    DWORD exitCode = 19;
    static_cast<void>(GetExitCodeProcess(child.hProcess, &exitCode));
    CloseHandle(child.hThread);
    CloseHandle(child.hProcess);
    return static_cast<int>(exitCode);
}

int RunProbeChild(const wchar_t* const processIdArgument) {
    wchar_t executable[MAX_PATH] = {};
    const DWORD length = GetModuleFileNameW(
        nullptr,
        executable,
        ARRAYSIZE(executable));
    if (length == 0 || length == ARRAYSIZE(executable)) {
        std::wcerr << L"GetModuleFileNameW failed: " << GetLastError() << L'\n';
        return 11;
    }

    const std::wstring commandLine =
        QuoteCommandArgument(executable) + L" --probe " +
        QuoteCommandArgument(processIdArgument);
    std::vector<wchar_t> mutableCommand(commandLine.begin(), commandLine.end());
    mutableCommand.push_back(L'\0');

    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION child{};
    if (!CreateProcessW(
            executable,
            mutableCommand.data(),
            nullptr,
            nullptr,
            TRUE,
            0,
            nullptr,
            nullptr,
            &startup,
            &child)) {
        std::wcerr << L"CreateProcessW failed: " << GetLastError() << L'\n';
        return 12;
    }

    const DWORD waited = WaitForSingleObject(child.hProcess, 30'000);
    DWORD exitCode = 13;
    if (waited == WAIT_OBJECT_0 &&
        !GetExitCodeProcess(child.hProcess, &exitCode)) {
        exitCode = 14;
    }
    CloseHandle(child.hThread);
    CloseHandle(child.hProcess);
    return static_cast<int>(exitCode);
}

int ProbePublishedProcessTwice(const wchar_t* const processIdArgument) {
    const int first = RunProbeChild(processIdArgument);
    const int second = RunProbeChild(processIdArgument);
    std::wcout
        << L"PROBE-TWICE first=" << first
        << L" second=" << second << L'\n';
    return first == 0 && second == 0 ? 0 : 10;
}

int ProbePublishedProcess(const wchar_t* const processIdArgument) {
    wchar_t* end = nullptr;
    const unsigned long parsed = std::wcstoul(processIdArgument, &end, 10);
    if (processIdArgument[0] == L'\0' || end == nullptr || *end != L'\0' ||
        parsed == 0 || parsed > (std::numeric_limits<DWORD>::max)()) {
        std::wcerr << L"invalid process id: " << processIdArgument << L'\n';
        return 2;
    }

    const std::wstring processId = std::to_wstring(parsed);
    bridge_status::Snapshot diagnostics{};
    const bool diagnosticPresent = ReadBridgeStatus(
        static_cast<DWORD>(parsed),
        &diagnostics);
    const bool rawPresent =
        IsPublishedObject(L"HancomLiveBridge." + processId);
    IUnknown* const batchUnknown = GetPublishedObject(L"HancomLiveBatch." + processId);

    bool protocolMatched = false;
    bool pingMatched = false;
    if (batchUnknown != nullptr) {
        IDispatch* batch = nullptr;
        if (SUCCEEDED(batchUnknown->QueryInterface(
                IID_IDispatch,
                reinterpret_cast<void**>(&batch))) && batch != nullptr) {
            protocolMatched = ReadProtocolVersion(batch);
            std::wstring ping;
            pingMatched = InvokeString(batch, L"Ping", nullptr, &ping) &&
                ping == L"HCB12\tPONG\t12";
            batch->Release();
        }
    }

    std::wcout
        << L"PROBE pid=" << processId
        << L" raw=" << (rawPresent ? L"present" : L"missing")
        << L" batch=" << (batchUnknown != nullptr ? L"present" : L"missing")
        << L" protocol=" << (protocolMatched ? L"ok" : L"missing")
        << L" ping=" << (pingMatched ? L"ok" : L"missing")
        << L" client-integrity=" << IntegrityLevelName(GetCurrentProcessId())
        << L" target-integrity=" << IntegrityLevelName(static_cast<DWORD>(parsed))
        << L" diagnostics=" << (diagnosticPresent ? L"present" : L"missing");
    if (diagnosticPresent) {
        std::wcout
            << L" query=" << diagnostics.queryCount
            << L" enum=" << diagnostics.enumCount
            << L" update=" << diagnostics.updateUiCount
            << L" action=" << diagnostics.doActionCount
            << L" publish=" << diagnostics.publishCount
            << L" success=" << diagnostics.publishSuccessCount
            << L" hr=0x" << std::hex
            << static_cast<unsigned long>(diagnostics.lastResult)
            << std::dec
            << L" last=" << diagnostics.lastAction;
    }
    std::wcout << L'\n';

    if (batchUnknown != nullptr) {
        batchUnknown->Release();
    }
    return rawPresent && batchUnknown != nullptr && protocolMatched && pingMatched
        ? 0
        : 10;
}

}

int wmain(const int argumentCount, wchar_t** const arguments) {
    const bool probeMode = argumentCount == 3 &&
        std::wcscmp(arguments[1], L"--probe") == 0;
    const bool probeTwiceMode = argumentCount == 3 &&
        std::wcscmp(arguments[1], L"--probe-twice") == 0;
    const bool probeOutputMode = argumentCount == 4 &&
        std::wcscmp(arguments[1], L"--probe-output") == 0;
    const bool probeAsTargetMode = argumentCount == 4 &&
        std::wcscmp(arguments[1], L"--probe-as-target") == 0;
    const bool runAsTargetMode = argumentCount >= 5 &&
        std::wcscmp(arguments[1], L"--run-as-target") == 0;
    const bool runAsTargetStdinMode = argumentCount >= 6 &&
        std::wcscmp(arguments[1], L"--run-as-target-stdin") == 0;
    if ((!probeMode && !probeTwiceMode && !probeOutputMode &&
         !probeAsTargetMode && !runAsTargetMode && !runAsTargetStdinMode &&
         (argumentCount < 2 || argumentCount > 3)) ||
        (probeMode && argumentCount != 3) ||
        (probeTwiceMode && argumentCount != 3) ||
        (probeOutputMode && argumentCount != 4) ||
        (probeAsTargetMode && argumentCount != 4) ||
        (runAsTargetMode && argumentCount < 5) ||
        (runAsTargetStdinMode && argumentCount < 6)) {
        std::wcerr
            << L"usage: BridgeSmoke.exe <HancomLiveBridge.dll> [--hold]\n"
            << L"       BridgeSmoke.exe --probe <process-id>\n"
            << L"       BridgeSmoke.exe --probe-twice <process-id>\n"
            << L"       BridgeSmoke.exe --probe-output <process-id> <path>\n"
            << L"       BridgeSmoke.exe --probe-as-target <process-id> <path>\n"
            << L"       BridgeSmoke.exe --run-as-target <process-id> <output> "
               L"<executable> [arguments...]\n"
            << L"       BridgeSmoke.exe --run-as-target-stdin <process-id> "
               L"<output> <input> <executable> [arguments...]\n";
        return 2;
    }

    if (probeAsTargetMode) {
        return ProbeUsingTargetToken(arguments[2], arguments[3]);
    }
    if (probeTwiceMode) {
        return ProbePublishedProcessTwice(arguments[2]);
    }
    if (runAsTargetMode) {
        return RunUsingTargetToken(
            arguments[2],
            arguments[3],
            L"NUL",
            argumentCount - 4,
            arguments + 4);
    }
    if (runAsTargetStdinMode) {
        return RunUsingTargetToken(
            arguments[2],
            arguments[3],
            arguments[4],
            argumentCount - 5,
            arguments + 5);
    }

    const HRESULT initialized = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (FAILED(initialized)) {
        std::wcerr << L"CoInitializeEx failed: " << initialized << L'\n';
        return 3;
    }

    if (probeMode || probeOutputMode) {
        std::wofstream output;
        std::wstreambuf* previous = nullptr;
        if (probeOutputMode) {
            output.open(arguments[3], std::ios::out | std::ios::trunc);
            if (!output) {
                std::wcerr << L"could not open probe output: " << arguments[3] << L'\n';
                CoUninitialize();
                return 11;
            }
            previous = std::wcout.rdbuf(output.rdbuf());
        }
        const int result = ProbePublishedProcess(arguments[2]);
        std::wcout.flush();
        if (previous != nullptr) {
            static_cast<void>(std::wcout.rdbuf(previous));
        }
        CoUninitialize();
        return result;
    }

    const bool cellTopologyOwnerIndex = CellTopologyOwnerIndexSmoke();
    HMODULE const library = LoadLibraryW(arguments[1]);
    if (library == nullptr) {
        std::wcerr << L"LoadLibraryW failed: " << GetLastError() << L'\n';
        CoUninitialize();
        return 4;
    }

    const auto query = reinterpret_cast<QueryModule>(
        GetProcAddress(library, "QueryUserActionInterface"));
    const auto getLastResult = reinterpret_cast<GetLastResult>(
        GetProcAddress(library, "GetBridgeLastHRESULT"));
    const auto releasePublication = reinterpret_cast<ReleasePublication>(
        GetProcAddress(library, "ReleaseBridgePublication"));
    if (query == nullptr || getLastResult == nullptr || releasePublication == nullptr) {
        std::wcerr << L"required export missing\n";
        FreeLibrary(library);
        CoUninitialize();
        return 5;
    }

    IHncUserActionModule* const module = query();
    if (module == nullptr ||
        std::strcmp(module->EnumAction(0), kOnInitialLoad) != 0 ||
        std::strcmp(module->EnumAction(1), kOnLoad) != 0 ||
        std::strcmp(module->EnumAction(2), kBootstrapAction) != 0 ||
        module->EnumAction(3) != nullptr) {
        std::wcerr << L"UserAction ABI mismatch\n";
        FreeLibrary(library);
        CoUninitialize();
        return 6;
    }

    constexpr LONG primaryWindowHandle = 4242;
    constexpr LONG secondaryWindowHandle = 4343;
    constexpr LONG primaryDocumentId = 17;
    constexpr LONG secondaryDocumentId = 18;
    constexpr LONG otherWindowDocumentId = 19;
    CComPtr<FakeDispatch> dispatchOwner;
    dispatchOwner.Attach(
        new FakeDispatch(primaryWindowHandle, primaryDocumentId));
    FakeDispatch* const dispatch = dispatchOwner;
    const int published = module->DoAction(kOnLoad, dispatch);
    const LONG referencesAfterPublication = dispatch->ReferenceCount();
    const bool publicationDoesNotOwnHwp =
        referencesAfterPublication == 2;
    std::wstring streamedCellText;
    const bool streamingScanWorked = hancom::inspection::ReadCurrentListText(
        dispatch,
        &streamedCellText);
    const std::wstring processId = std::to_wstring(GetCurrentProcessId());
    const std::wstring primaryScope =
        processId + L"." + std::to_wstring(primaryWindowHandle);
    const std::wstring primaryDocumentScope =
        primaryScope + L"." + std::to_wstring(primaryDocumentId);
    IUnknown* const raw = GetPublishedObject(L"HancomLiveBridge." + processId);
    IUnknown* const batchUnknown = GetPublishedObject(L"HancomLiveBatch." + processId);
    IUnknown* const scopedRaw = GetPublishedObject(
        L"HancomLiveBridge." + primaryScope);
    IUnknown* const scopedBatchUnknown = GetPublishedObject(
        L"HancomLiveBatch." + primaryScope);
    IUnknown* const documentRaw = GetPublishedObject(
        L"HancomLiveBridge." + primaryDocumentScope);
    IUnknown* const documentBatchUnknown = GetPublishedObject(
        L"HancomLiveBatch." + primaryDocumentScope);
    bridge_status::Snapshot diagnostics{};
    const bool diagnosticsValid = ReadBridgeStatus(
        GetCurrentProcessId(),
        &diagnostics) &&
        diagnostics.queryCount >= 1 &&
        diagnostics.enumCount >= 4 &&
        diagnostics.doActionCount >= 1 &&
        diagnostics.publishCount >= 1 &&
        diagnostics.publishSuccessCount >= 1;
    if (published == FALSE || FAILED(getLastResult()) || raw == nullptr ||
        batchUnknown == nullptr || scopedRaw == nullptr ||
        scopedBatchUnknown == nullptr || documentRaw == nullptr ||
        documentBatchUnknown == nullptr || !diagnosticsValid ||
        !publicationDoesNotOwnHwp) {
        std::wcerr
            << L"ROT publication failed: " << getLastResult()
            << L" refs-after-publication=" << referencesAfterPublication
            << L" refs-with-proxies=" << dispatch->ReferenceCount()
            << L" raw=" << (raw != nullptr)
            << L" batch=" << (batchUnknown != nullptr)
            << L" scoped-raw=" << (scopedRaw != nullptr)
            << L" scoped-batch=" << (scopedBatchUnknown != nullptr)
            << L" document-raw=" << (documentRaw != nullptr)
            << L" document-batch=" << (documentBatchUnknown != nullptr)
            << L" diagnostics=" << diagnosticsValid << L'\n';
        if (raw != nullptr) {
            raw->Release();
        }
        if (batchUnknown != nullptr) {
            batchUnknown->Release();
        }
        if (scopedRaw != nullptr) {
            scopedRaw->Release();
        }
        if (scopedBatchUnknown != nullptr) {
            scopedBatchUnknown->Release();
        }
        if (documentRaw != nullptr) {
            documentRaw->Release();
        }
        if (documentBatchUnknown != nullptr) {
            documentBatchUnknown->Release();
        }
        FreeLibrary(library);
        CoUninitialize();
        return 7;
    }
    raw->Release();
    scopedRaw->Release();
    documentRaw->Release();

    bool dynamicDocumentActivationWorked = false;
    IDispatch* dynamicBatch = nullptr;
    if (SUCCEEDED(scopedBatchUnknown->QueryInterface(
            IID_IDispatch,
            reinterpret_cast<void**>(&dynamicBatch))) &&
        dynamicBatch != nullptr) {
        dynamicDocumentActivationWorked =
            ActivateDocumentAndWait(
                dynamicBatch,
                secondaryDocumentId) &&
            ActivateDocumentAndWait(
                dynamicBatch,
                primaryDocumentId);
        dynamicBatch->Release();
    }
    IDispatch* batch = nullptr;
    const HRESULT dispatchStatus = documentBatchUnknown->QueryInterface(
        IID_IDispatch,
        reinterpret_cast<void**>(&batch));
    documentBatchUnknown->Release();
    scopedBatchUnknown->Release();
    batchUnknown->Release();
    std::wstring ping;
    std::wstring badRequest;
    std::wstring unsavedBatchRequest;
    std::wstring snapshot;
    std::wstring page;
    std::wstring pageV3;
    std::wstring pageBatch;
    std::wstring pageSummary;
    std::wstring routingContext;
    std::wstring structure;
    std::wstring scopedStructure;
    std::wstring unscopedStructure;
    std::wstring badActionRequest;
    std::wstring unsavedActionRequest;
    std::wstring badArrayIndexRequest;
    std::wstring validArrayRequest;
    std::wstring sizedPictureRequest;
    std::wstring formattedCaptionRequest;
    std::wstring capturedTableRequest;
    std::wstring captionTableRequest;
    std::wstring deleteControlRequest;
    std::wstring invalidPasteOrderRequest;
    std::wstring callReturnRequest;
    std::wstring partialMutationRequest;
    std::wstring atomicRollbackRequest;
    std::wstring atomicRollbackRetryRequest;
    std::wstring atomicRollbackFailureRequest;
    std::wstring checkpointRestoreRequest;
    std::wstring checkpointRollbackRequest;
    std::wstring emptyCellTextRequest;
    std::wstring referenceLayoutSuccessRequest;
    std::wstring referenceLayoutAtomicRollbackRequest;
    std::wstring referenceLayoutDroppedEdgesRequest;
    std::wstring selectedControlPatchRequest;
    std::wstring textDeletionPatchRequest;
    std::wstring staleSelectionRequest;
    std::wstring replaceSelectionRequest;
    std::wstring badOfficialApiRequest;
    std::wstring actionOfficialApiRequest;
    std::wstring actionInputOfficialApiRequest;
    std::wstring fallbackActionOfficialApiRequest;
    std::wstring sehActionOfficialApiRequest;
    std::wstring parameterOfficialApiRequest;
    std::wstring fallbackParameterOfficialApiRequest;
    std::wstring normalizedParameterOfficialApiRequest;
    std::wstring reportedMissingParameterOfficialApiRequest;
    std::wstring automationOfficialApiRequest;
    std::wstring eventOfficialApiRequest;
    std::wstring aliasOfficialApiRequest;
    std::wstring itemAliasOfficialApiRequest;
    std::wstring messageBoxOwnerOfficialApiRequest;
    std::wstring writeOnlyPropertyOfficialApiRequest;
    std::wstring hActionGetDefaultOfficialApiRequest;
    std::wstring hActionExecuteOfficialApiRequest;
    std::wstring setIdAliasOfficialApiRequest;
    const std::filesystem::path checkpointPath =
        std::filesystem::temp_directory_path() /
        (L"HancomLiveBridgeCheckpointSmoke-" +
         std::to_wstring(GetCurrentProcessId()) + L".bin");
    const bool checkpointFixtureWritten =
        WriteCheckpointFixture(checkpointPath, "CHECKPOINT_TARGET");
    const std::wstring encodedCheckpointPath =
        EncodeUtf8Base64(checkpointPath.wstring());
    const std::wstring checkpointRestorePayload =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\nRESTORE_DOCUMENT_FILE\t" +
        encodedCheckpointPath + L"\t4\nEND";
    constexpr wchar_t badArrayIndexPayload[] =
        L"HCA1\nDOC\t0\tQzpceC5od3A=\n"
        L"ACTION\tTableCreate\tHTableCreation\n"
        L"ARRAY\tColWidth\t1\n"
        L"ASET\tColWidth\t1\tMM\t16\n"
        L"ENDACTION\nEND";
    constexpr wchar_t validArrayPayload[] =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
        L"ACTION\tArrayAction\tHArrayFixture\n"
        L"ARRAY\tColWidth\t1\n"
        L"ASET\tColWidth\t0\tI4\t3200\n"
        L"ENDACTION\nEND";
    constexpr wchar_t unsavedBatchPayload[] =
        L"HCB1\nDOC\t17\t \n"
        L"TABLE\t1136771603\n"
        L"TEXT\tA1\tIA==\tdmVyaWZ5\n"
        L"ENDTABLE\nEND";
    constexpr wchar_t unsavedActionPayload[] =
        L"HCA1\nDOC\t17\t \nRUN\tBreakPage\nEND";
    constexpr wchar_t sizedPicturePayload[] =
        L"HCA1\nDOC\t0\tQzpceC5od3A=\n"
        L"MOVE_PAGE\t9\n"
        L"MOVE_POSITION\t0\t21\t0\n"
        L"SELECT_CONTROL\tMTE0MDg3ODQzNg==\n"
        L"INSERT_PICTURE\tQzpceC5wbmc=\t80\t40\nEND";
    constexpr wchar_t formattedCaptionPayload[] =
        L"HCA1\nDOC\t0\tQzpceC5od3A=\n"
        L"CAPTION\t9\tdGl0bGU=\tQXJpYWw=\t1200\t0\t0\t3\t160\t0\t0\t0\t1000\t0\t0\t104\t0\nEND";
    constexpr wchar_t capturedTablePayload[] =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
        L"COPY_CONTROL\tY2FwdHVyZWQtdGFibGU=\n"
        L"APPLY_COPIED_TABLE_ANCHOR\tY2FwdHVyZWQtdGFibGU=\n"
        L"PASTE_TABLE\n"
        L"CAPTURE_TABLE\nEND";
    constexpr wchar_t captionTablePayload[] =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
        L"COPY_CONTROL\tY2FwdHVyZWQtdGFibGU=\n"
        L"APPLY_COPIED_TABLE_ANCHOR\tY2FwdHVyZWQtdGFibGU=\n"
        L"PASTE_TABLE\n"
        L"CAPTURE_TABLE\n"
        L"CAPTION\t9\tdGl0bGU=\nEND";
    constexpr wchar_t deleteControlPayload[] =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
        L"DELETE_CONTROL\tY2FwdHVyZWQtdGFibGU=\nEND";
    constexpr wchar_t invalidPasteOrderPayload[] =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
        L"RUN\tBreakPage\n"
        L"PASTE_TABLE\nEND";
    constexpr wchar_t callReturnPayload[] =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
        L"CALL\tReturnBoolean\nENDCALL\n"
        L"CALL\tReturnInteger\nENDCALL\n"
        L"CALL\tReturnText\nENDCALL\n"
        L"CALL\tReturnVoid\nENDCALL\nEND";
    constexpr wchar_t partialMutationPayload[] =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
        L"RUN\tBreakPage\n"
        L"RUN\tSelectCtrlFront\nEND";
    constexpr wchar_t atomicRollbackPayload[] =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
        L"POLICY\tATOMIC\t1\n"
        L"MOVE_DOC_END\n"
        L"INSERT_TEXT\tYXRvbWljLXRhaWw=\n"
        L"RUN\tBreakPage\n"
        L"RUN\tSelectCtrlFront\nEND";
    constexpr wchar_t emptyCellTextPayload[] =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
        L"CAPTURE_TABLE\n"
        L"SET_CELL_TEXT\tA2\t \tZmlsbGVk\t1\nEND";
    struct MultilineCellTextCase {
        const wchar_t* label;
        const wchar_t* payload;
        bool patchText;
        const wchar_t* address;
        const wchar_t* expected;
    };
    constexpr MultilineCellTextCase multilineCellTextCases[] = {
        {
            L"SET_CELL_TEXT/LF",
            L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
            L"SELECT_CONTROL\tcmVmZXJlbmNlLXRhYmxl\n"
            L"CAPTURE_TABLE\n"
            L"SET_CELL_TEXT\tA2\t \tbGluZTEKbGluZTI=\t1\nEND",
            false,
            L"A2",
            L"line1\nline2",
        },
        {
            L"SET_CELL_TEXT/CRLF",
            L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
            L"SELECT_CONTROL\tcmVmZXJlbmNlLXRhYmxl\n"
            L"CAPTURE_TABLE\n"
            L"SET_CELL_TEXT\tA2\t \tbGluZTENCmxpbmUy\t1\nEND",
            false,
            L"A2",
            L"line1\nline2",
        },
        {
            L"SET_CELL_TEXT/CR",
            L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
            L"SELECT_CONTROL\tcmVmZXJlbmNlLXRhYmxl\n"
            L"CAPTURE_TABLE\n"
            L"SET_CELL_TEXT\tA2\t \tbGluZTENbGluZTI=\t1\nEND",
            false,
            L"A2",
            L"line1\nline2",
        },
        {
            L"PATCH_TEXT CELL/LF",
            L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
            L"SELECT_CONTROL\tcmVmZXJlbmNlLXRhYmxl\n"
            L"CAPTURE_TABLE\n"
            L"PATCH_TEXT\tCELL\tcmVmZXJlbmNlLXRhYmxl\tB2\t1\t1\t"
            L"b2xk\tbGluZTEKbGluZTI=\t1\nEND",
            true,
            L"B2",
            L"line1\nline2",
        },
        {
            L"PATCH_TEXT CELL/CRLF",
            L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
            L"SELECT_CONTROL\tcmVmZXJlbmNlLXRhYmxl\n"
            L"CAPTURE_TABLE\n"
            L"PATCH_TEXT\tCELL\tcmVmZXJlbmNlLXRhYmxl\tB2\t1\t1\t"
            L"b2xk\tbGluZTENCmxpbmUy\t1\nEND",
            true,
            L"B2",
            L"line1\nline2",
        },
        {
            L"PATCH_TEXT CELL/CR",
            L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
            L"SELECT_CONTROL\tcmVmZXJlbmNlLXRhYmxl\n"
            L"CAPTURE_TABLE\n"
            L"PATCH_TEXT\tCELL\tcmVmZXJlbmNlLXRhYmxl\tB2\t1\t1\t"
            L"b2xk\tbGluZTENbGluZTI=\t1\nEND",
            true,
            L"B2",
            L"line1\nline2",
        },
    };
    constexpr wchar_t allTargetCellTextPreflightPayload[] =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
        L"SELECT_CONTROL\tcmVmZXJlbmNlLXRhYmxl\n"
        L"CAPTURE_TABLE\n"
        L"SET_CELL_TEXT\tA2\t \tZmlyc3Q=\t1\n"
        L"SET_CELL_TEXT\tB2\tc3RhbGU=\tZGFuZ2Vy\t1\nEND";
    constexpr wchar_t selectionCompatibleTableTextPayload[] =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
        L"SELECT_CONTROL\tcmVmZXJlbmNlLXRhYmxl\n"
        L"CAPTURE_TABLE\n"
        L"SET_CELL_TEXT\tA2\tb2xk\tbmV3\t1\nEND";
    constexpr wchar_t topologyReusePayload[] =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
        L"SELECT_CONTROL\tcmVmZXJlbmNlLXRhYmxl\n"
        L"CAPTURE_TABLE\n"
        L"PATCH_TEXT\tCELL\tcmVmZXJlbmNlLXRhYmxl\tA2\t1\t1\t"
        L"b2xk\tbmV3LWE=\t1\n"
        L"PATCH_TEXT\tCELL\tcmVmZXJlbmNlLXRhYmxl\tB2\t1\t1\t"
        L"b2xk\tbmV3LWI=\t1\nEND";
    constexpr wchar_t topologyFormattingPreservationPayload[] =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
        L"SELECT_CONTROL\tcmVmZXJlbmNlLXRhYmxl\n"
        L"CAPTURE_TABLE\n"
        L"CELL\tA1\n"
        L"ACTION\tStyle\tHStyle\n"
        L"ENDACTION\n"
        L"ACTION\tCharShape\tHCharShape\n"
        L"ENDACTION\n"
        L"ACTION\tParagraphShape\tHParaShape\n"
        L"ENDACTION\n"
        L"ACTION\tCellFill\tHCellBorderFill\n"
        L"ENDACTION\n"
        L"ACTION\tCellBorder\tHCellBorderFill\n"
        L"ENDACTION\n"
        L"RUN\tTableVAlignTop\n"
        L"RUN\tTableVAlignCenter\n"
        L"RUN\tTableVAlignBottom\n"
        L"CELL\tB1\nEND";
    constexpr wchar_t topologyPaddingPreservationPayload[] =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
        L"SELECT_CONTROL\tcmVmZXJlbmNlLXRhYmxl\n"
        L"CAPTURE_TABLE\n"
        L"CELL\tA1\n"
        L"ACTION\tTablePropertyDialog\tHShapeObject\n"
        L"SET\tHSet/ShapeType\tI4\t3\n"
        L"SET\tHSet/ShapeCellSize\tI4\t0\n"
        L"SET\tShapeTableCell/HasMargin\tI4\t1\n"
        L"SET\tShapeTableCell/MarginLeft\tI4\t100\n"
        L"SET\tShapeTableCell/MarginRight\tI4\t200\n"
        L"SET\tShapeTableCell/MarginTop\tI4\t300\n"
        L"SET\tShapeTableCell/MarginBottom\tI4\t400\n"
        L"ENDACTION\n"
        L"CELL\tB1\nEND";
    constexpr wchar_t topologyCellSizeInvalidationPayload[] =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
        L"SELECT_CONTROL\tcmVmZXJlbmNlLXRhYmxl\n"
        L"CAPTURE_TABLE\n"
        L"CELL\tA1\n"
        L"ACTION\tTablePropertyDialog\tHShapeObject\n"
        L"SET\tHSet/ShapeType\tI4\t3\n"
        L"SET\tHSet/ShapeCellSize\tI4\t1\n"
        L"SET\tShapeTableCell/Width\tI4\t24000\n"
        L"SET\tShapeTableCell/Height\tI4\t12000\n"
        L"ENDACTION\n"
        L"CELL\tB1\nEND";
    constexpr wchar_t topologyUnknownActionInvalidationPayload[] =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
        L"SELECT_CONTROL\tcmVmZXJlbmNlLXRhYmxl\n"
        L"CAPTURE_TABLE\n"
        L"CELL\tA1\n"
        L"ACTION\tFutureCellFormat\tHCellBorderFill\n"
        L"ENDACTION\n"
        L"CELL\tB1\nEND";
    constexpr wchar_t topologyInvalidationPayload[] =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
        L"SELECT_CONTROL\tcmVmZXJlbmNlLXRhYmxl\n"
        L"CAPTURE_TABLE\n"
        L"CELL\tA1\n"
        L"MERGE\tA1\tB1\n"
        L"CELL\tB1\nEND";
    constexpr wchar_t topologyRunInvalidationPayload[] =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
        L"SELECT_CONTROL\tcmVmZXJlbmNlLXRhYmxl\n"
        L"CAPTURE_TABLE\n"
        L"CELL\tA1\n"
        L"RUN\tTableDeleteRow\n"
        L"CELL\tB1\nEND";
    constexpr wchar_t topologyActionInvalidationPayload[] =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
        L"SELECT_CONTROL\tcmVmZXJlbmNlLXRhYmxl\n"
        L"CAPTURE_TABLE\n"
        L"CELL\tA1\n"
        L"ACTION\tTableInsertRightColumn\tHTableInsertLine\n"
        L"ENDACTION\n"
        L"CELL\tB1\nEND";
    constexpr wchar_t topologyCallInvalidationPayload[] =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
        L"SELECT_CONTROL\tcmVmZXJlbmNlLXRhYmxl\n"
        L"CAPTURE_TABLE\n"
        L"CELL\tA1\n"
        L"CALL\tMutateTableTopology\n"
        L"ENDCALL\n"
        L"CELL\tB1\nEND";
    const std::wstring oversizedCellPatchPayload =
        OversizedCellPatchPayload();
    constexpr wchar_t referenceLayoutPayload[] = LR"REF(HCA1
DOC	17	QzpceC5od3A=
MOVE_DOC_END
ACTION	ReferenceLayoutBulk	HTableCreation
SET	Rows	I4	3
SET	Columns	I4	2
SET	BaseStyleId	I4	0
SET	BodyLeft	I4	5804
SET	BodyTop	I4	4387
SET	BodyWidth	I4	47920
SET	BodyHeight	I4	76691
ARRAY	ColumnWidths	2
ARRAY	RowHeights	3
ARRAY	MergeRows	1
ARRAY	MergeColumns	1
ARRAY	MergeRowSpans	1
ARRAY	MergeColumnSpans	1
ARRAY	EdgeOrientations	7
ARRAY	EdgeLines	7
ARRAY	EdgeStarts	7
ARRAY	EdgeEnds	7
ARRAY	EdgeColors	7
ARRAY	StyleFontSizes	2
ARRAY	StyleBold	2
ARRAY	StyleTextColors	2
ARRAY	StyleFillColors	2
ARRAY	StyleAlignments	2
ARRAY	StyleVerticalAlignments	2
ARRAY	StyleWidthRatios	2
ARRAY	StyleLetterSpacings	2
ARRAY	StyleLineSpacingTypes	2
ARRAY	StyleLineSpacings	2
ARRAY	StylePreviousSpacings	2
ARRAY	StyleNextSpacings	2
ARRAY	StylePaddingLeft	2
ARRAY	StylePaddingRight	2
ARRAY	StylePaddingTop	2
ARRAY	StylePaddingBottom	2
ARRAY	RegionTop	2
ARRAY	RegionLeft	2
ARRAY	RegionBottom	2
ARRAY	RegionRight	2
ARRAY	RegionStyleIndexes	2
ARRAY	TextRows	5
ARRAY	TextColumns	5
ARRAY	TextStyleIndexes	5
ARRAY	TextBreakModes	5
ARRAY	EdgeStyles	7
ARRAY	EdgeWidths	7
ARRAY	StyleKeys	2
ARRAY	StyleFontNames	2
ARRAY	TextValues	5
ASET	ColumnWidths	0	I4	19168
ASET	ColumnWidths	1	I4	28752
ASET	RowHeights	0	I4	19173
ASET	RowHeights	1	I4	26842
ASET	RowHeights	2	I4	30676
ASET	MergeRows	0	I4	0
ASET	MergeColumns	0	I4	0
ASET	MergeRowSpans	0	I4	1
ASET	MergeColumnSpans	0	I4	2
ASET	EdgeOrientations	0	I4	0
ASET	EdgeOrientations	1	I4	0
ASET	EdgeOrientations	2	I4	0
ASET	EdgeOrientations	3	I4	0
ASET	EdgeOrientations	4	I4	1
ASET	EdgeOrientations	5	I4	1
ASET	EdgeOrientations	6	I4	1
ASET	EdgeLines	0	I4	0
ASET	EdgeLines	1	I4	1
ASET	EdgeLines	2	I4	2
ASET	EdgeLines	3	I4	3
ASET	EdgeLines	4	I4	0
ASET	EdgeLines	5	I4	1
ASET	EdgeLines	6	I4	2
ASET	EdgeStarts	0	I4	0
ASET	EdgeStarts	1	I4	0
ASET	EdgeStarts	2	I4	0
ASET	EdgeStarts	3	I4	0
ASET	EdgeStarts	4	I4	0
ASET	EdgeStarts	5	I4	1
ASET	EdgeStarts	6	I4	0
ASET	EdgeEnds	0	I4	2
ASET	EdgeEnds	1	I4	2
ASET	EdgeEnds	2	I4	2
ASET	EdgeEnds	3	I4	2
ASET	EdgeEnds	4	I4	3
ASET	EdgeEnds	5	I4	3
ASET	EdgeEnds	6	I4	3
ASET	EdgeColors	0	I4	3943715
ASET	EdgeColors	1	I4	3943715
ASET	EdgeColors	2	I4	3943715
ASET	EdgeColors	3	I4	3943715
ASET	EdgeColors	4	I4	3943715
ASET	EdgeColors	5	I4	3943715
ASET	EdgeColors	6	I4	3943715
ASET	StyleFontSizes	0	I4	900
ASET	StyleFontSizes	1	I4	1100
ASET	StyleBold	0	I4	-1
ASET	StyleBold	1	I4	1
ASET	StyleTextColors	0	I4	1315860
ASET	StyleTextColors	1	I4	16777215
ASET	StyleFillColors	0	I4	-1
ASET	StyleFillColors	1	I4	5125162
ASET	StyleAlignments	0	I4	0
ASET	StyleAlignments	1	I4	1
ASET	StyleVerticalAlignments	0	I4	1
ASET	StyleVerticalAlignments	1	I4	1
ASET	StyleWidthRatios	0	I4	100
ASET	StyleWidthRatios	1	I4	100
ASET	StyleLetterSpacings	0	I4	0
ASET	StyleLetterSpacings	1	I4	0
ASET	StyleLineSpacingTypes	0	I4	0
ASET	StyleLineSpacingTypes	1	I4	0
ASET	StyleLineSpacings	0	I4	100
ASET	StyleLineSpacings	1	I4	100
ASET	StylePreviousSpacings	0	I4	0
ASET	StylePreviousSpacings	1	I4	0
ASET	StyleNextSpacings	0	I4	0
ASET	StyleNextSpacings	1	I4	0
ASET	StylePaddingLeft	0	I4	510
ASET	StylePaddingLeft	1	I4	510
ASET	StylePaddingRight	0	I4	510
ASET	StylePaddingRight	1	I4	510
ASET	StylePaddingTop	0	I4	142
ASET	StylePaddingTop	1	I4	142
ASET	StylePaddingBottom	0	I4	142
ASET	StylePaddingBottom	1	I4	142
ASET	RegionTop	0	I4	0
ASET	RegionTop	1	I4	0
ASET	RegionLeft	0	I4	0
ASET	RegionLeft	1	I4	0
ASET	RegionBottom	0	I4	3
ASET	RegionBottom	1	I4	1
ASET	RegionRight	0	I4	2
ASET	RegionRight	1	I4	2
ASET	RegionStyleIndexes	0	I4	0
ASET	RegionStyleIndexes	1	I4	1
ASET	TextRows	0	I4	0
ASET	TextRows	1	I4	1
ASET	TextRows	2	I4	1
ASET	TextRows	3	I4	2
ASET	TextRows	4	I4	2
ASET	TextColumns	0	I4	0
ASET	TextColumns	1	I4	0
ASET	TextColumns	2	I4	1
ASET	TextColumns	3	I4	0
ASET	TextColumns	4	I4	1
ASET	TextStyleIndexes	0	I4	1
ASET	TextStyleIndexes	1	I4	0
ASET	TextStyleIndexes	2	I4	0
ASET	TextStyleIndexes	3	I4	0
ASET	TextStyleIndexes	4	I4	0
ASET	TextBreakModes	0	I4	0
ASET	TextBreakModes	1	I4	0
ASET	TextBreakModes	2	I4	0
ASET	TextBreakModes	3	I4	0
ASET	TextBreakModes	4	I4	0
ASET	EdgeStyles	0	BSTR	c29saWQ=
ASET	EdgeStyles	1	BSTR	c29saWQ=
ASET	EdgeStyles	2	BSTR	c29saWQ=
ASET	EdgeStyles	3	BSTR	c29saWQ=
ASET	EdgeStyles	4	BSTR	c29saWQ=
ASET	EdgeStyles	5	BSTR	c29saWQ=
ASET	EdgeStyles	6	BSTR	c29saWQ=
ASET	EdgeWidths	0	BSTR	MC4zbW0=
ASET	EdgeWidths	1	BSTR	MC4zbW0=
ASET	EdgeWidths	2	BSTR	MC4zbW0=
ASET	EdgeWidths	3	BSTR	MC4zbW0=
ASET	EdgeWidths	4	BSTR	MC4zbW0=
ASET	EdgeWidths	5	BSTR	MC4zbW0=
ASET	EdgeWidths	6	BSTR	MC4zbW0=
ASET	StyleKeys	0	BSTR	Ym9keQ==
ASET	StyleKeys	1	BSTR	aGVhZGVy
ASET	StyleFontNames	0	BSTR	QXJpYWw=
ASET	StyleFontNames	1	BSTR	QXJpYWw=
ASET	TextValues	0	BSTR	TWVyZ2VkIGhlYWRlcg==
ASET	TextValues	1	BSTR	QTI=
ASET	TextValues	2	BSTR	QjI=
ASET	TextValues	3	BSTR	QTM=
ASET	TextValues	4	BSTR	QjM=
ENDACTION
END)REF";
    const wchar_t* const referenceLayoutAtomicRollbackPayload =
        referenceLayoutPayload;
    constexpr wchar_t staleSelectionPayload[] =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
        L"EXPECT_SELECTION\t1\t0\t108\t0\t0\t108\t0\n"
        L"REPLACE_SELECTION\td3Jvbmc=\tbmV3\nEND";
    constexpr wchar_t replaceSelectionPayload[] =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
        L"EXPECT_SELECTION\t1\t0\t108\t0\t0\t108\t0\n"
        L"REPLACE_SELECTION\tb2xk\tbmV3\nEND";
    constexpr wchar_t selectedControlPatchPayload[] =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
        L"PATCH_TEXT\tCURRENT\t0\t \tZGFuZ2Vy\t0\nEND";
    constexpr wchar_t textDeletionPatchPayload[] =
        L"HCA1\nDOC\t17\tQzpceC5od3A=\n"
        L"PATCH_TEXT\tCURRENT\t1\tb2xk\t \nEND";
    constexpr wchar_t expectedCallResults[] =
        L"QglVbVYwZFhKdVFtOXZiR1ZoYmc9PQkxCkkJVW1WMGRYSnVTVzUwWldkbGNnPT0JLTQyClMJVW1WMGRYSnVWR1Y0ZEE9PQlRem92VkdWdGNDOUNTVTR3TURBeExuQnVadz09ClYJVW1WMGRYSnVWbTlwWkE9PQ==";
    constexpr wchar_t actionOfficialApiPayload[] =
        L"HCV1\nACTION\tCharShapeBold\nEND";
    constexpr wchar_t actionInputOfficialApiPayload[] =
        L"HCV1\nACTION\tSetWithoutDefault\n"
        L"SET\tFileName\tBSTR\tC:\\probe\\fixture.dat\nEND";
    constexpr wchar_t fallbackActionOfficialApiPayload[] =
        L"HCV1\nACTION\tAutoSpellRun\nEND";
    constexpr wchar_t sehActionOfficialApiPayload[] =
        L"HCV1\nACTION\tRaiseStructured\nEND";
    constexpr wchar_t parameterOfficialApiPayload[] =
        L"HCV1\nPARAMETER_SET\tCharShape\tCharShape\n"
        L"ITEM\tHeight\tPIT_I4\t-\nEND";
    constexpr wchar_t fallbackParameterOfficialApiPayload[] =
        L"HCV1\nPARAMETER_SET\tChangeRome\tChangeRome\n"
        L"ITEM\tOption\tPIT_UI1\t-\nEND";
    constexpr wchar_t normalizedParameterOfficialApiPayload[] =
        L"HCV1\nPARAMETER_SET\tConvertCase\tConvertCase\n"
        L"ITEM\tType\tPMT_UINT\t-\nEND";
    constexpr wchar_t reportedMissingParameterOfficialApiPayload[] =
        L"HCV1\nPARAMETER_SET\tDocumentInfo\tDocumentInfo\n"
        L"ITEM\tReportedMissing\tPIT_I4\t-\nEND";
    constexpr wchar_t automationOfficialApiPayload[] =
        L"HCV1\nAUTOMATION\tIHwpObject\tPageCount\tproperty\nEND\n";
    constexpr wchar_t eventOfficialApiPayload[] =
        L"HCV1\nAUTOMATION\tIHwpObjectEvents\tDocumentChange\tevent\nEND\n";
    constexpr wchar_t aliasOfficialApiPayload[] =
        L"HCV1\nAUTOMATION\tIHwpObject\tRenameMetatag\tmethod\n"
        L"ARG\tBSTR64\tY29kZXgtcnVudGltZS1wcm9iZQ==\nEND\n";
    constexpr wchar_t itemAliasOfficialApiPayload[] =
        L"HCV1\nAUTOMATION\tIDHwpParameterSet\tItemExsit\tmethod\n"
        L"ARG\tBSTR64\tY29kZXgtcnVudGltZS1wcm9iZQ==\nEND\n";
    constexpr wchar_t messageBoxOwnerOfficialApiPayload[] =
        L"HCV1\nAUTOMATION\tIXHwpMessageBox\tApplication\tproperty\nEND\n";
    constexpr wchar_t writeOnlyPropertyOfficialApiPayload[] =
        L"HCV1\nAUTOMATION\tIXHwpDocument\tPassword\tproperty\nEND\n";
    constexpr wchar_t hActionGetDefaultOfficialApiPayload[] =
        L"HCV1\nAUTOMATION\tHAction\tGetDefault\tmethod\n"
        L"ARG\tBSTR64\tSW5zZXJ0VGV4dA==\n"
        L"ARG\tACTION_SET\tInsertText\nEND\n";
    constexpr wchar_t hActionExecuteOfficialApiPayload[] =
        L"HCV1\nAUTOMATION\tHAction\tExecute\tmethod\n"
        L"ARG\tBSTR64\tSW5zZXJ0VGV4dA==\n"
        L"ARG\tACTION_SET\tInsertText\nEND\n";
    constexpr wchar_t setIdAliasOfficialApiPayload[] =
        L"HCV1\nAUTOMATION\tIDHwpParameterSet\tGetSetID\tproperty\nEND\n";
    dispatch->PrepareSaveVerifySerializationFixture();
    std::wstring saveVerifyResponse;
    const bool saveVerifyInvoked = SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(batch, L"SaveVerify", nullptr, &saveVerifyResponse);
    const bool saveVerifyMatched =
        SaveVerifyMatched(saveVerifyResponse) && dispatch->StoppedLifecycleAfterSave();
    dispatch->PrepareSaveVerifySectionDiagnosticFixture();
    std::wstring saveVerifySectionDiagnosticsResponse;
    bool saveVerifySectionDiagnosticsInvoked = true;
    bool saveVerifySectionDiagnosticsMatched = true;
    std::uint64_t saveVerifySectionDiagnosticsElapsedMicroseconds = 0;
    constexpr size_t kSectionDiagnosticsIterations = 3;
    for (size_t index = 0; index < kSectionDiagnosticsIterations; ++index) {
        saveVerifySectionDiagnosticsInvoked =
            saveVerifySectionDiagnosticsInvoked &&
            SUCCEEDED(dispatchStatus) &&
            batch != nullptr &&
            InvokeString(
                batch,
                L"SaveVerify",
                nullptr,
                &saveVerifySectionDiagnosticsResponse);
        saveVerifySectionDiagnosticsMatched =
            saveVerifySectionDiagnosticsMatched &&
            SaveVerifySectionDiagnosticsMatched(
                saveVerifySectionDiagnosticsResponse);
        const std::vector<std::wstring> fields =
            SplitTabs(saveVerifySectionDiagnosticsResponse);
        if (fields.size() == 25) {
            saveVerifySectionDiagnosticsElapsedMicroseconds +=
                std::wcstoull(fields[20].c_str(), nullptr, 10);
        }
    }
    dispatch->PrepareLifecycleSuccess();
    std::wstring lifecycleSuccessResponse;
    const bool lifecycleSuccessInvoked = SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(batch, L"SaveReopenVerify", nullptr, &lifecycleSuccessResponse);
    const bool lifecycleSuccessMatched =
        LifecycleSuccessMatched(lifecycleSuccessResponse) && dispatch->CompletedLifecycleSequence();
    dispatch->PrepareLifecycleCleanNoOpSave();
    std::wstring lifecycleCleanNoOpResponse;
    const bool lifecycleCleanNoOpInvoked = SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(batch, L"SaveReopenVerify", nullptr, &lifecycleCleanNoOpResponse);
    const bool lifecycleCleanNoOpMatched =
        LifecycleCleanNoOpMatched(lifecycleCleanNoOpResponse) &&
        dispatch->CompletedLifecycleSequence();
    dispatch->PrepareLifecycleSaveFailure();
    std::wstring lifecycleSaveGateResponse;
    const bool lifecycleSaveGateInvoked = SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(batch, L"SaveReopenVerify", nullptr, &lifecycleSaveGateResponse);
    const bool lifecycleSaveGateMatched =
        LifecycleSaveGateMatched(lifecycleSaveGateResponse) && dispatch->StoppedLifecycleAfterSave();
    dispatch->PrepareLifecycleFalseSaveReturn();
    std::wstring lifecycleSaveReturnGateResponse;
    const bool lifecycleSaveReturnGateInvoked = SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(batch, L"SaveReopenVerify", nullptr, &lifecycleSaveReturnGateResponse);
    const bool lifecycleSaveReturnGateMatched =
        LifecycleSaveReturnGateMatched(lifecycleSaveReturnGateResponse) &&
        dispatch->StoppedLifecycleAfterSave();
    dispatch->PrepareLifecycleRecoveryCaptureFailure();
    std::wstring lifecycleRecoveryCaptureGateResponse;
    const bool lifecycleRecoveryCaptureGateInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"SaveReopenVerify",
            nullptr,
            &lifecycleRecoveryCaptureGateResponse);
    const bool lifecycleRecoveryCaptureGateMatched =
        LifecycleRecoveryCaptureGateMatched(
            lifecycleRecoveryCaptureGateResponse) &&
        dispatch->StoppedLifecycleBeforeDestructiveReopen();
    dispatch->PrepareLifecycleOpenFailure();
    std::wstring lifecycleRecoveryResponse;
    const bool lifecycleRecoveryInvoked = SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(batch, L"SaveReopenVerify", nullptr, &lifecycleRecoveryResponse);
    const bool lifecycleRecoveryMatched =
        LifecycleRecoveryMatched(lifecycleRecoveryResponse) &&
        dispatch->RecoveredLifecycleSession();
    dispatch->PrepareLifecycleHwpxSuccess();
    std::wstring lifecycleHwpxSuccessResponse;
    const bool lifecycleHwpxSuccessInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"SaveReopenVerify",
            nullptr,
            &lifecycleHwpxSuccessResponse);
    const bool lifecycleHwpxSuccessMatched =
        LifecycleSuccessMatchedForPath(
            lifecycleHwpxSuccessResponse,
            L"Qzpc7ZWc6riAXFguSFdQWA==") &&
        dispatch->CompletedLifecycleSequence();
    dispatch->PrepareLifecycleHwpxOpenFailure();
    std::wstring lifecycleHwpxRecoveryResponse;
    const bool lifecycleHwpxRecoveryInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"SaveReopenVerify",
            nullptr,
            &lifecycleHwpxRecoveryResponse);
    const bool lifecycleHwpxRecoveryMatched =
        LifecycleRecoveryMatchedForPath(
            lifecycleHwpxRecoveryResponse,
            L"Qzpc7ZWc6riAXHguSHdQeA==",
            L"0") &&
        dispatch->RecoveredLifecycleSession() &&
        dispatch->UsedExpectedLifecycleRecoveryFormat();
    dispatch->PrepareLifecycleOpenSuccessFingerprintMismatch();
    std::wstring lifecycleFingerprintRecoveryResponse;
    const bool lifecycleFingerprintRecoveryInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"SaveReopenVerify",
            nullptr,
            &lifecycleFingerprintRecoveryResponse);
    const bool lifecycleFingerprintRecoveryMatched =
        LifecycleRecoveryMatchedForPath(
            lifecycleFingerprintRecoveryResponse,
            L"Qzpc7ZWc6riAXHguaHdw",
            L"1") &&
        dispatch->RecoveredLifecycleFingerprintMismatch() &&
        dispatch->UsedExpectedLifecycleRecoveryFormat();
    dispatch->PrepareLifecycleHwpxOpenSuccessFingerprintMismatch();
    std::wstring lifecycleHwpxFingerprintRecoveryResponse;
    const bool lifecycleHwpxFingerprintRecoveryInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"SaveReopenVerify",
            nullptr,
            &lifecycleHwpxFingerprintRecoveryResponse);
    const bool lifecycleHwpxFingerprintRecoveryMatched =
        LifecycleRecoveryMatchedForPath(
            lifecycleHwpxFingerprintRecoveryResponse,
            L"Qzpc7ZWc6riAXHguSHdQeA==",
            L"1") &&
        dispatch->RecoveredLifecycleFingerprintMismatch() &&
        dispatch->UsedExpectedLifecycleRecoveryFormat();
    dispatch->RestoreLifecycleFixture();
    dispatch->PrepareTransientMessageBoxModeRestoreFailure();
    std::wstring messageBoxModeRestoreRetryResponse;
    const bool messageBoxModeRestoreRetryInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"ExecuteActions",
            validArrayPayload,
            &messageBoxModeRestoreRetryResponse);
    const bool messageBoxModeRestoreRetryPassed =
        messageBoxModeRestoreRetryResponse.rfind(
            L"HCA2\tERROR\tMESSAGE_BOX_MODE\t",
            0) == 0 &&
        dispatch->RetriedMessageBoxModeRestore();
    dispatch->PrepareSelectCtrlFrontFailure();
    const bool partialMutationInvoked = SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(batch, L"ExecuteActions", partialMutationPayload, &partialMutationRequest);
    dispatch->RestoreSelectCtrlFrontFixture();
    dispatch->PrepareAtomicAppendFailure(false);
    const bool atomicRollbackInvoked = SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(batch, L"ExecuteActions", atomicRollbackPayload, &atomicRollbackRequest);
    const bool atomicRollbackRetryInvoked = SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"ExecuteActions",
            atomicRollbackPayload,
            &atomicRollbackRetryRequest);
    const bool atomicRollbackRestored = dispatch->AtomicTailRollbackRestored();
    dispatch->PrepareAtomicAppendFailure(true);
    const bool atomicRollbackFailureInvoked = SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"ExecuteActions",
            atomicRollbackPayload,
            &atomicRollbackFailureRequest);
    const bool atomicRollbackFailureRetainedTail =
        dispatch->AtomicTailRollbackFailureRetainedTail();
    dispatch->RestoreAtomicAppendFixture();
    dispatch->PrepareCheckpointRestoreFixture(false);
    const bool checkpointRestoreInvoked =
        checkpointFixtureWritten &&
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"ExecuteActions",
            checkpointRestorePayload.c_str(),
            &checkpointRestoreRequest);
    const bool checkpointRestoreSucceeded =
        checkpointRestoreRequest.rfind(L"HCA2\tOK\t1\t4\t", 0) == 0 &&
        dispatch->CheckpointRestoreSucceeded();
    dispatch->PrepareCheckpointRestoreFixture(true);
    const bool checkpointRollbackInvoked =
        checkpointFixtureWritten &&
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"ExecuteActions",
            checkpointRestorePayload.c_str(),
            &checkpointRollbackRequest);
    const bool checkpointRollbackSucceeded =
        checkpointRollbackRequest.rfind(
            L"HCA2\tERROR\tDOCUMENT_CHECKPOINT_RESTORE\t",
            0) == 0 &&
        dispatch->CheckpointRollbackSucceeded();
    dispatch->RestoreCheckpointRestoreFixture();
    const bool checkpointFixtureDeleted =
        !checkpointFixtureWritten ||
        DeleteFileW(checkpointPath.c_str()) != FALSE;
    dispatch->PrepareEmptyCellTextFixture();
    const bool emptyCellTextFixtureReady =
        dispatch->EmptyCellTextFixtureReady();
    const bool emptyCellTextInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"ExecuteActions",
            emptyCellTextPayload,
            &emptyCellTextRequest);
    const bool emptyCellTextSucceeded =
        emptyCellTextRequest.rfind(L"HCA2\tOK\t2\t0\t1\t0\t", 0) == 0;
    const bool emptyCellTextInsertedExactly =
        dispatch->EmptyCellTextInsertedExactly();
    dispatch->RestoreReferenceLayoutFixture();
    std::vector<std::wstring> multilineCellTextResponses;
    std::vector<bool> multilineCellTextCasePassed;
    bool multilineCellTextInvoked = true;
    bool multilineCellTextPassed = true;
    for (const MultilineCellTextCase& test : multilineCellTextCases) {
        dispatch->PrepareMultilineCellTextFixture(test.patchText);
        std::wstring response;
        const bool invoked =
            SUCCEEDED(dispatchStatus) && batch != nullptr &&
            InvokeString(
                batch,
                L"ExecuteActions",
                test.payload,
                &response);
        const bool passed =
            invoked && response.rfind(L"HCA2\tOK\t3\t", 0) == 0 &&
            dispatch->MultilineCellTextWrittenExactly(
                test.address,
                test.expected);
        multilineCellTextInvoked = multilineCellTextInvoked && invoked;
        multilineCellTextPassed = multilineCellTextPassed && passed;
        multilineCellTextResponses.push_back(response);
        multilineCellTextCasePassed.push_back(passed);
        dispatch->RestoreReferenceLayoutFixture();
    }
    dispatch->PrepareAllTargetCellTextPreflightFixture();
    std::wstring allTargetCellTextPreflightResponse;
    const bool allTargetCellTextPreflightInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"ExecuteActions",
            allTargetCellTextPreflightPayload,
            &allTargetCellTextPreflightResponse);
    const bool allTargetCellTextPreflightPassed =
        SafeStaleCellPreflightFailed(allTargetCellTextPreflightResponse) &&
        dispatch->AllTargetCellTextPreflightPreserved();
    dispatch->RestoreReferenceLayoutFixture();
    dispatch->PrepareTableTextPreflightSelectionFixture(false, false);
    std::wstring strictCellSelectionPreflightResponse;
    const bool strictCellSelectionPreflightInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"ExecuteActions",
            selectionCompatibleTableTextPayload,
            &strictCellSelectionPreflightResponse);
    const bool strictCellSelectionPreflightPassed =
        strictCellSelectionPreflightResponse.rfind(L"HCA2\tOK\t3\t", 0) == 0 &&
        dispatch->TableTextPreflightSelectionBatchSucceeded();
    dispatch->RestoreReferenceLayoutFixture();
    dispatch->PrepareTableTextPreflightSelectionFixture(true, false);
    std::wstring tableControlSelectionPreflightResponse;
    const bool tableControlSelectionPreflightInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"ExecuteActions",
            selectionCompatibleTableTextPayload,
            &tableControlSelectionPreflightResponse);
    const bool tableControlSelectionPreflightPassed =
        tableControlSelectionPreflightResponse.rfind(L"HCA2\tOK\t3\t", 0) == 0 &&
        dispatch->TableTextPreflightSelectionBatchSucceeded();
    dispatch->RestoreReferenceLayoutFixture();
    dispatch->PrepareTableTextPreflightSelectionFixture(false, true);
    std::wstring selectionRestoreFailureResponse;
    const bool selectionRestoreFailureInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"ExecuteActions",
            selectionCompatibleTableTextPayload,
            &selectionRestoreFailureResponse);
    const bool selectionRestoreFailurePassed =
        SafeSelectionRestoreFailed(selectionRestoreFailureResponse) &&
        dispatch->TableTextPreflightRestoreFailurePreservedDocument();
    dispatch->RestoreReferenceLayoutFixture();
    dispatch->PrepareTopologyReuseFixture();
    std::wstring topologyReuseResponse;
    const bool topologyReuseInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"ExecuteActions",
            topologyReusePayload,
            &topologyReuseResponse);
    const bool topologyReusePassed =
        topologyReuseResponse.rfind(L"HCA2\tOK\t4\t", 0) == 0 &&
        dispatch->TopologyWasReusedAcrossTextBatch();
    dispatch->RestoreReferenceLayoutFixture();
    dispatch->PrepareFormattingTopologyPolicyFixture();
    std::wstring topologyFormattingPreservationResponse;
    const bool topologyFormattingPreservationInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"ExecuteActions",
            topologyFormattingPreservationPayload,
            &topologyFormattingPreservationResponse);
    const bool topologyFormattingPreservationPassed =
        topologyFormattingPreservationResponse.rfind(L"HCA2\tOK\t12\t", 0) == 0 &&
        dispatch->TopologyWasPreservedAfterCellFormatting();
    dispatch->RestoreReferenceLayoutFixture();
    dispatch->PrepareFormattingTopologyPolicyFixture();
    std::wstring topologyPaddingPreservationResponse;
    const bool topologyPaddingPreservationInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"ExecuteActions",
            topologyPaddingPreservationPayload,
            &topologyPaddingPreservationResponse);
    const bool topologyPaddingPreservationPassed =
        topologyPaddingPreservationResponse.rfind(L"HCA2\tOK\t5\t", 0) == 0 &&
        dispatch->TopologyWasPreservedAfterCellPadding();
    dispatch->RestoreReferenceLayoutFixture();
    dispatch->PrepareFormattingTopologyPolicyFixture();
    std::wstring topologyCellSizeInvalidationResponse;
    const bool topologyCellSizeInvalidationInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"ExecuteActions",
            topologyCellSizeInvalidationPayload,
            &topologyCellSizeInvalidationResponse);
    const bool topologyCellSizeInvalidationPassed =
        topologyCellSizeInvalidationResponse.rfind(L"HCA2\tOK\t5\t", 0) == 0 &&
        dispatch->TopologyWasInvalidatedAfterCellSizeChange();
    dispatch->RestoreReferenceLayoutFixture();
    dispatch->PrepareFormattingTopologyPolicyFixture();
    std::wstring topologyUnknownActionInvalidationResponse;
    const bool topologyUnknownActionInvalidationInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"ExecuteActions",
            topologyUnknownActionInvalidationPayload,
            &topologyUnknownActionInvalidationResponse);
    const bool topologyUnknownActionInvalidationPassed =
        topologyUnknownActionInvalidationResponse.rfind(L"HCA2\tOK\t5\t", 0) == 0 &&
        dispatch->TopologyWasInvalidatedAfterUnknownAction();
    dispatch->RestoreReferenceLayoutFixture();
    dispatch->PrepareTopologyInvalidationFixture();
    std::wstring topologyInvalidationResponse;
    const bool topologyInvalidationInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"ExecuteActions",
            topologyInvalidationPayload,
            &topologyInvalidationResponse);
    const bool topologyInvalidationPassed =
        CellTopologyInvalidationFailedSafely(topologyInvalidationResponse) &&
        dispatch->TopologyWasInvalidatedAfterMerge();
    dispatch->RestoreReferenceLayoutFixture();
    dispatch->PrepareRunTopologyInvalidationFixture();
    std::wstring topologyRunInvalidationResponse;
    const bool topologyRunInvalidationInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"ExecuteActions",
            topologyRunInvalidationPayload,
            &topologyRunInvalidationResponse);
    const bool topologyRunInvalidationPassed =
        topologyRunInvalidationResponse.rfind(L"HCA2\tOK\t5\t", 0) == 0 &&
        dispatch->TopologyWasInvalidatedAfterRunDeleteRow();
    dispatch->RestoreReferenceLayoutFixture();
    dispatch->PrepareActionTopologyInvalidationFixture();
    std::wstring topologyActionInvalidationResponse;
    const bool topologyActionInvalidationInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"ExecuteActions",
            topologyActionInvalidationPayload,
            &topologyActionInvalidationResponse);
    const bool topologyActionInvalidationPassed =
        topologyActionInvalidationResponse.rfind(L"HCA2\tOK\t5\t", 0) == 0 &&
        dispatch->TopologyWasInvalidatedAfterActionInsertColumn();
    dispatch->RestoreReferenceLayoutFixture();
    dispatch->PrepareCallTopologyInvalidationFixture();
    std::wstring topologyCallInvalidationResponse;
    const bool topologyCallInvalidationInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"ExecuteActions",
            topologyCallInvalidationPayload,
            &topologyCallInvalidationResponse);
    const bool topologyCallInvalidationPassed =
        CellTopologyInvalidationFailedSafely(topologyCallInvalidationResponse) &&
        dispatch->TopologyWasInvalidatedAfterCall();
    dispatch->RestoreReferenceLayoutFixture();
    dispatch->PrepareOversizedCellPatchFixture();
    std::wstring oversizedCellPatchResponse;
    const bool oversizedCellPatchInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"ExecuteActions",
            oversizedCellPatchPayload.c_str(),
            &oversizedCellPatchResponse);
    const bool oversizedCellPatchRejected =
        OversizedCellPatchRejectedSafely(oversizedCellPatchResponse) &&
        dispatch->OversizedCellPatchPreservedDocument();
    dispatch->RestoreReferenceLayoutFixture();
    dispatch->PrepareReferenceLayoutFixture(false);
    const bool referenceLayoutSuccessInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"ExecuteActions",
            referenceLayoutPayload,
            &referenceLayoutSuccessRequest);
    const bool referenceLayoutCreatedExactly =
        dispatch->ReferenceLayoutCreatedExactly();
    const bool referenceLayoutBorderReadbackCovered =
        dispatch->ReferenceBorderReadbackCoveredRequestedEdges();
    dispatch->PrepareReferenceLayoutFixture(true);
    const bool referenceLayoutAtomicRollbackInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"ExecuteActions",
            referenceLayoutAtomicRollbackPayload,
            &referenceLayoutAtomicRollbackRequest);
    const bool referenceLayoutAtomicRollbackRestored =
        dispatch->ReferenceLayoutRollbackRestored();
    dispatch->PrepareReferenceLayoutDroppedEdgesFixture();
    const bool referenceLayoutDroppedEdgesInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"ExecuteActions",
            referenceLayoutPayload,
            &referenceLayoutDroppedEdgesRequest);
    const bool referenceLayoutDroppedEdgesRejected =
        ReferenceLayoutDroppedEdgesRejected(
            referenceLayoutDroppedEdgesRequest) &&
        dispatch->ReferenceLayoutDroppedEdgesObserved();
    dispatch->RestoreReferenceLayoutFixture();
    dispatch->PrepareSelectedControlTextPatch();
    const bool selectedControlPatchInvoked = SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"ExecuteActions",
            selectedControlPatchPayload,
            &selectedControlPatchRequest);
    const bool selectedControlPatchRejected =
        selectedControlPatchRequest.rfind(
            L"HCA2\tERROR\tNON_TEXT_SELECTION\t",
            0) == 0 &&
        dispatch->InsertTextExecutions() == 0;
    const size_t selectedControlPatchInsertions = dispatch->InsertTextExecutions();
    dispatch->RestoreTextPatchSelectionFixture();
    dispatch->PrepareTextDeletionPatch();
    const bool textDeletionPatchInvoked = SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(
            batch,
            L"ExecuteActions",
            textDeletionPatchPayload,
            &textDeletionPatchRequest);
    const bool textDeletionPatchVerified =
        textDeletionPatchRequest.rfind(L"HCA2\tOK\t1\t", 0) == 0 &&
        dispatch->DeletedSelectedText();
    dispatch->RestoreTextDeletionPatch();
    dispatch->PrepareScopedInspectionFixture();
    const bool scopedStructureInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(batch, L"InspectStructure", L"31", &scopedStructure);
    const bool scopedStructureSucceeded =
        scopedStructure.rfind(L"HDS1\n", 0) == 0 &&
        dispatch->UsedBoundedBackwardInspection();
    dispatch->PrepareScopedInspectionFixture();
    const bool unscopedStructureInvoked =
        SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(batch, L"InspectStructure", L"-31", &unscopedStructure);
    const bool unscopedStructureSucceeded =
        unscopedStructure.rfind(L"HDS1\n", 0) == 0 &&
        dispatch->PreservedUnscopedForwardInspection();
    dispatch->RestoreScopedInspectionFixture();
    if (!cellTopologyOwnerIndex ||
        FAILED(dispatchStatus) || !ReadProtocolVersion(batch) ||
        !dynamicDocumentActivationWorked ||
        !saveVerifyInvoked || !saveVerifyMatched ||
        !saveVerifySectionDiagnosticsInvoked ||
        !saveVerifySectionDiagnosticsMatched ||
        !lifecycleSuccessInvoked || !lifecycleSuccessMatched ||
        !lifecycleCleanNoOpInvoked || !lifecycleCleanNoOpMatched ||
        !lifecycleSaveGateInvoked || !lifecycleSaveGateMatched ||
        !lifecycleSaveReturnGateInvoked || !lifecycleSaveReturnGateMatched ||
        !lifecycleRecoveryCaptureGateInvoked ||
        !lifecycleRecoveryCaptureGateMatched ||
        !lifecycleRecoveryInvoked || !lifecycleRecoveryMatched ||
        !lifecycleHwpxSuccessInvoked || !lifecycleHwpxSuccessMatched ||
        !lifecycleHwpxRecoveryInvoked || !lifecycleHwpxRecoveryMatched ||
        !lifecycleFingerprintRecoveryInvoked ||
        !lifecycleFingerprintRecoveryMatched ||
        !lifecycleHwpxFingerprintRecoveryInvoked ||
        !lifecycleHwpxFingerprintRecoveryMatched ||
        !partialMutationInvoked || partialMutationRequest.rfind(
            L"HCA2\tERROR\tACTION_FAILED\tU2VsZWN0Q3RybEZyb250\t"
            L"U2VsZWN0Q3RybEZyb250IHJldHVybmVkIGZhbHNl\t1\t"
            L"U2VsZWN0Q3RybEZyb250\t1\t0\t",
            0) != 0 ||
        !atomicRollbackInvoked || !AtomicRollbackSucceeded(atomicRollbackRequest) ||
        !atomicRollbackRetryInvoked || !AtomicRollbackSucceeded(atomicRollbackRetryRequest) ||
        !atomicRollbackRestored ||
        !atomicRollbackFailureInvoked || !AtomicRollbackFailurePreservedOriginal(
            atomicRollbackRequest,
            atomicRollbackFailureRequest) ||
        !atomicRollbackFailureRetainedTail ||
        !checkpointFixtureWritten ||
        encodedCheckpointPath.empty() ||
        !checkpointRestoreInvoked ||
        !checkpointRestoreSucceeded ||
        !checkpointRollbackInvoked ||
        !checkpointRollbackSucceeded ||
        !checkpointFixtureDeleted ||
        !emptyCellTextFixtureReady ||
        !emptyCellTextInvoked ||
        !emptyCellTextSucceeded ||
        !emptyCellTextInsertedExactly ||
        !multilineCellTextInvoked ||
        !multilineCellTextPassed ||
        !allTargetCellTextPreflightInvoked ||
        !allTargetCellTextPreflightPassed ||
        !strictCellSelectionPreflightInvoked ||
        !strictCellSelectionPreflightPassed ||
        !tableControlSelectionPreflightInvoked ||
        !tableControlSelectionPreflightPassed ||
        !selectionRestoreFailureInvoked ||
        !selectionRestoreFailurePassed ||
        !topologyReuseInvoked ||
        !topologyReusePassed ||
        !topologyFormattingPreservationInvoked ||
        !topologyFormattingPreservationPassed ||
        !topologyPaddingPreservationInvoked ||
        !topologyPaddingPreservationPassed ||
        !topologyCellSizeInvalidationInvoked ||
        !topologyCellSizeInvalidationPassed ||
        !topologyUnknownActionInvalidationInvoked ||
        !topologyUnknownActionInvalidationPassed ||
        !topologyInvalidationInvoked ||
        !topologyInvalidationPassed ||
        !topologyRunInvalidationInvoked ||
        !topologyRunInvalidationPassed ||
        !topologyActionInvalidationInvoked ||
        !topologyActionInvalidationPassed ||
        !topologyCallInvalidationInvoked ||
        !topologyCallInvalidationPassed ||
        !oversizedCellPatchInvoked ||
        !oversizedCellPatchRejected ||
        !messageBoxModeRestoreRetryInvoked ||
        !messageBoxModeRestoreRetryPassed ||
        !referenceLayoutSuccessInvoked ||
        !ReferenceLayoutSucceeded(referenceLayoutSuccessRequest) ||
        !referenceLayoutCreatedExactly ||
        !referenceLayoutBorderReadbackCovered ||
        !referenceLayoutAtomicRollbackInvoked ||
        !ReferenceLayoutAtomicRollbackSucceeded(referenceLayoutAtomicRollbackRequest) ||
        !referenceLayoutAtomicRollbackRestored ||
        !referenceLayoutDroppedEdgesInvoked ||
        !referenceLayoutDroppedEdgesRejected ||
        !selectedControlPatchInvoked || !selectedControlPatchRejected ||
        !textDeletionPatchInvoked || !textDeletionPatchVerified ||
        !scopedStructureInvoked || !scopedStructureSucceeded ||
        !unscopedStructureInvoked || !unscopedStructureSucceeded ||
        !InvokeString(batch, L"Ping", nullptr, &ping) || ping != L"HCB12\tPONG\t12" ||
        !InvokeString(batch, L"Execute", L"not-a-request", &badRequest) ||
        badRequest.rfind(L"HCB1\tERROR\tBAD_REQUEST\t", 0) != 0 ||
        !InvokeString(batch, L"Execute", unsavedBatchPayload, &unsavedBatchRequest) ||
        unsavedBatchRequest.rfind(L"HCB1\tERROR\tSTALE_DOCUMENT\t", 0) != 0 ||
        !InvokeString(batch, L"Snapshot", nullptr, &snapshot) ||
        snapshot.rfind(L"HCI1\tERROR\t", 0) != 0 ||
        !InvokeString(batch, L"InspectPage", L"1", &page) ||
        page.rfind(L"HCI1\tERROR\t", 0) != 0 ||
        !InvokeString(batch, L"InspectPageV3", L"1", &pageV3) ||
        pageV3.rfind(L"HCI1\tERROR\t", 0) != 0 ||
        !InvokeString(batch, L"InspectPagesV3", L"1", &pageBatch) ||
        pageBatch.rfind(L"HCI1\tERROR\t", 0) != 0 ||
        !InvokeString(batch, L"InspectPageSummary", L"1", &pageSummary) ||
        pageSummary.rfind(L"HCI1\tERROR\t", 0) != 0 ||
        !InvokeString(batch, L"InspectRoutingContext", L"0", &routingContext) ||
        routingContext.rfind(L"HCI1\tERROR\t", 0) != 0 ||
        !InvokeString(batch, L"InspectStructure", L"1", &structure) ||
        structure.rfind(L"HCI1\tERROR\t", 0) != 0 ||
        !InvokeString(batch, L"ExecuteActions", L"not-a-request", &badActionRequest) ||
        badActionRequest.rfind(L"HCA1\tERROR\tBAD_REQUEST\t", 0) != 0 ||
        !InvokeString(batch, L"ExecuteActions", unsavedActionPayload, &unsavedActionRequest) ||
        unsavedActionRequest.rfind(L"HCA2\tERROR\tSTALE_DOCUMENT\t", 0) != 0 ||
        !InvokeString(batch, L"ExecuteActions", badArrayIndexPayload, &badArrayIndexRequest) ||
        badArrayIndexRequest.rfind(L"HCA1\tERROR\tBAD_REQUEST\t", 0) != 0 ||
        !InvokeString(batch, L"ExecuteActions", validArrayPayload, &validArrayRequest) ||
        validArrayRequest.rfind(L"HCA2\tOK\t1\t", 0) != 0 ||
        !InvokeString(batch, L"ExecuteActions", sizedPicturePayload, &sizedPictureRequest) ||
        sizedPictureRequest.rfind(L"HCA2\tERROR\tSTALE_DOCUMENT\t", 0) != 0 ||
        !InvokeString(batch, L"ExecuteActions", formattedCaptionPayload, &formattedCaptionRequest) ||
        formattedCaptionRequest.rfind(L"HCA2\tERROR\tSTALE_DOCUMENT\t", 0) != 0 ||
        !InvokeString(batch, L"ExecuteActions", capturedTablePayload, &capturedTableRequest) ||
        capturedTableRequest.rfind(L"HCA2\tOK\t4\t", 0) != 0 ||
        capturedTableRequest.find(L"\tY2FwdHVyZWQtdGFibGU=") == std::wstring::npos ||
        !InvokeString(batch, L"ExecuteActions", deleteControlPayload, &deleteControlRequest) ||
        deleteControlRequest.rfind(L"HCA2\tOK\t1\t1\t", 0) != 0 ||
        !InvokeString(batch, L"ExecuteActions", invalidPasteOrderPayload, &invalidPasteOrderRequest) ||
        invalidPasteOrderRequest.rfind(
            L"HCA2\tERROR\tNO_TABLE_ANCHOR_FORMAT\t", 0) != 0 ||
        !InvokeString(batch, L"ExecuteActions", callReturnPayload, &callReturnRequest) ||
        callReturnRequest.rfind(L"HCA2\tOK\t4\t0\t0\t0\t", 0) != 0 ||
        callReturnRequest.find(std::wstring(L"\t\t") + expectedCallResults) == std::wstring::npos ||
        !InvokeString(
            batch,
            L"ExecuteActions",
            staleSelectionPayload,
            &staleSelectionRequest) ||
        staleSelectionRequest.rfind(
            L"HCA2\tERROR\tSTALE_SELECTION_TEXT\t"
            L"c2VsZWN0aW9u\t"
            L"c2VsZWN0ZWQgdGV4dCBjaGFuZ2VkIGFmdGVyIHRoZSByZXF1ZXN0IHdhcyBwcmVwYXJlZA==\t"
            L"0\tUkVQTEFDRV9TRUxFQ1RJT04=\t0\t1\t",
            0) != 0 ||
        !dispatch->InsertedText().empty() ||
        !InvokeString(
            batch,
            L"ExecuteActions",
            replaceSelectionPayload,
            &replaceSelectionRequest) ||
        replaceSelectionRequest.rfind(L"HCA2\tOK\t1\t0\t1\t0\t", 0) != 0 ||
        dispatch->InsertedText() != L"new" ||
        !InvokeString(batch, L"ProbeOfficialApi", L"not-a-request", &badOfficialApiRequest) ||
        badOfficialApiRequest.rfind(L"HCV1\tERROR\tBAD_REQUEST\t", 0) != 0 ||
        !InvokeString(
            batch,
            L"ProbeOfficialApi",
            actionOfficialApiPayload,
            &actionOfficialApiRequest) ||
        actionOfficialApiRequest.rfind(L"HCV1\tACTION\tCharShapeBold\t", 0) != 0 ||
        !InvokeString(
            batch,
            L"ProbeOfficialApi",
            actionInputOfficialApiPayload,
            &actionInputOfficialApiRequest) ||
        actionInputOfficialApiRequest.rfind(
            L"HCV1\tACTION\tSetWithoutDefault\t0\t0\t0\t0\t"
            L"-2147467259\t-2\t0\t0\t1\t-2147418113\t-2\t",
            0) != 0 ||
        !InvokeString(
            batch,
            L"ProbeOfficialApi",
            fallbackActionOfficialApiPayload,
            &fallbackActionOfficialApiRequest) ||
        fallbackActionOfficialApiRequest.rfind(
            L"HCV1\tACTION\tAutoSpellRun\t0\t-2147352571\t"
            L"-2147418113\t-2147418113\t-2147418113\t-2\t0\t"
            L"-2147418113\t-2\t0\t1\t",
            0) != 0 ||
        !InvokeString(
            batch,
            L"ProbeOfficialApi",
            sehActionOfficialApiPayload,
            &sehActionOfficialApiRequest) ||
        sehActionOfficialApiRequest.rfind(
            L"HCV1\tACTION\tRaiseStructured\t0\t0\t0\t0\t0\t1\t0\t"
            L"-268430796\t-2\t0\t1\t",
            0) != 0 ||
        !InvokeString(
            batch,
            L"ProbeOfficialApi",
            parameterOfficialApiPayload,
            &parameterOfficialApiRequest) ||
        parameterOfficialApiRequest.rfind(
            L"HCV1\tPARAMETER_SET\tCharShape\t",
            0) != 0 ||
        !InvokeString(
            batch,
            L"ProbeOfficialApi",
            fallbackParameterOfficialApiPayload,
            &fallbackParameterOfficialApiRequest) ||
        fallbackParameterOfficialApiRequest.rfind(
            L"HCV1\tPARAMETER_SET\tChangeRome\t-2147352571\t0\t1\t1\t1\t-1\t1\t1\t0\t",
            0) != 0 ||
        !InvokeString(
            batch,
            L"ProbeOfficialApi",
            normalizedParameterOfficialApiPayload,
            &normalizedParameterOfficialApiRequest) ||
        normalizedParameterOfficialApiRequest.rfind(
            L"HCV1\tPARAMETER_SET\tConvertCase\t0\t0\t0\t0\t0\t1\t1\t1\t0\t",
            0) != 0 ||
        !InvokeString(
            batch,
            L"ProbeOfficialApi",
            reportedMissingParameterOfficialApiPayload,
            &reportedMissingParameterOfficialApiRequest) ||
        reportedMissingParameterOfficialApiRequest.find(
            L"\nITEM\tReportedMissing\t0\t0\t0\t0\t0\t1") == std::wstring::npos ||
        !InvokeString(
            batch,
            L"ProbeOfficialApi",
            automationOfficialApiPayload,
            &automationOfficialApiRequest) ||
        automationOfficialApiRequest.rfind(
            L"HCV1\tAUTOMATION\tIHwpObject\tPageCount\tproperty\t",
            0) != 0 ||
        !InvokeString(
            batch,
            L"ProbeOfficialApi",
            eventOfficialApiPayload,
            &eventOfficialApiRequest) ||
        eventOfficialApiRequest.rfind(
            L"HCV1\tAUTOMATION\tIHwpObjectEvents\tDocumentChange\tevent\t"
            L"0\t0\t0\t-2147467263\t3\t11\t",
            0) != 0 ||
        !InvokeString(
            batch,
            L"ProbeOfficialApi",
            aliasOfficialApiPayload,
            &aliasOfficialApiRequest) ||
        aliasOfficialApiRequest.rfind(
            L"HCV1\tAUTOMATION\tIHwpObject\tRenameMetatag\tmethod\t0\t0\t0\t",
            0) != 0 ||
        !InvokeString(
            batch,
            L"ProbeOfficialApi",
            itemAliasOfficialApiPayload,
            &itemAliasOfficialApiRequest) ||
        itemAliasOfficialApiRequest.rfind(
            L"HCV1\tAUTOMATION\tIDHwpParameterSet\tItemExsit\tmethod\t0\t0\t0\t",
            0) != 0 ||
        !InvokeString(
            batch,
            L"ProbeOfficialApi",
            messageBoxOwnerOfficialApiPayload,
            &messageBoxOwnerOfficialApiRequest) ||
        messageBoxOwnerOfficialApiRequest.rfind(
            L"HCV1\tAUTOMATION\tIXHwpMessageBox\tApplication\tproperty\t0\t0\t0\t",
            0) != 0 ||
        !InvokeString(
            batch,
            L"ProbeOfficialApi",
            writeOnlyPropertyOfficialApiPayload,
            &writeOnlyPropertyOfficialApiRequest) ||
        writeOnlyPropertyOfficialApiRequest.rfind(
            L"HCV1\tAUTOMATION\tIXHwpDocument\tPassword\tproperty\t0\t0\t0\t",
            0) != 0 ||
        !InvokeString(
            batch,
            L"ProbeOfficialApi",
            hActionGetDefaultOfficialApiPayload,
            &hActionGetDefaultOfficialApiRequest) ||
        hActionGetDefaultOfficialApiRequest.rfind(
            L"HCV1\tAUTOMATION\tHAction\tGetDefault\tmethod\t0\t0\t0\t",
            0) != 0 ||
        !InvokeString(
            batch,
            L"ProbeOfficialApi",
            hActionExecuteOfficialApiPayload,
            &hActionExecuteOfficialApiRequest) ||
        hActionExecuteOfficialApiRequest.rfind(
            L"HCV1\tAUTOMATION\tHAction\tExecute\tmethod\t0\t0\t0\t",
            0) != 0 ||
        !InvokeString(
            batch,
            L"ProbeOfficialApi",
            setIdAliasOfficialApiPayload,
            &setIdAliasOfficialApiRequest) ||
        setIdAliasOfficialApiRequest.rfind(
            L"HCV1\tAUTOMATION\tIDHwpParameterSet\tGetSetID\tproperty\t0\t0\t0\t",
            0) != 0 ||
        !InvokeString(batch, L"ExecuteActions", captionTablePayload, &captionTableRequest) ||
        captionTableRequest.rfind(L"HCA2\tOK\t5\t4\t1\t0\t", 0) != 0 ||
        dispatch->RanBreakPage() ||
        !dispatch->UsedAutoYesMessageBoxMode() ||
        !dispatch->MessageBoxModeRestored() ||
        !dispatch->UsedNativeTableBlockCopy() ||
        !dispatch->UsedNativeTableBlockPaste() ||
        dispatch->UsedClipboardTableTransfer() ||
        !dispatch->PasteUsedSourceAnchorFormat() ||
        !dispatch->PreservedAttachedCaptionNumber() ||
        !streamingScanWorked || streamedCellText != L"cell-text" ||
        !dispatch->UsedStreamingTextScan()) {
        std::wcerr
            << L"batch automation contract failed"
            << L"\n  lifecycle-success=" << lifecycleSuccessResponse
            << L"\n  save-verify=" << saveVerifyResponse
            << L"\n  save-verify-matched=" << saveVerifyMatched
            << L"\n  save-verify-section-diagnostics="
            << saveVerifySectionDiagnosticsResponse
            << L"\n  save-verify-section-diagnostics-matched="
            << saveVerifySectionDiagnosticsMatched
            << L"\n  lifecycle-success-sequence=" << dispatch->CompletedLifecycleSequence()
            << L"\n  lifecycle-clean-noop=" << lifecycleCleanNoOpResponse
            << L"\n  lifecycle-clean-noop-matched=" << lifecycleCleanNoOpMatched
            << L"\n  lifecycle-save-gate=" << lifecycleSaveGateResponse
            << L"\n  lifecycle-save-stopped=" << dispatch->StoppedLifecycleAfterSave()
            << L"\n  lifecycle-save-return-gate=" << lifecycleSaveReturnGateResponse
            << L"\n  lifecycle-recovery-capture-gate="
            << lifecycleRecoveryCaptureGateResponse
            << L"\n  lifecycle-recovery-capture-gate-matched="
            << lifecycleRecoveryCaptureGateMatched
            << L"\n  lifecycle-recovery=" << lifecycleRecoveryResponse
            << L"\n  lifecycle-recovery-matched=" << lifecycleRecoveryMatched
            << L"\n  lifecycle-hwpx-success=" << lifecycleHwpxSuccessResponse
            << L"\n  lifecycle-hwpx-success-matched="
            << lifecycleHwpxSuccessMatched
            << L"\n  lifecycle-hwpx-recovery=" << lifecycleHwpxRecoveryResponse
            << L"\n  lifecycle-hwpx-recovery-matched="
            << lifecycleHwpxRecoveryMatched
            << L"\n  lifecycle-fingerprint-recovery="
            << lifecycleFingerprintRecoveryResponse
            << L"\n  lifecycle-fingerprint-recovery-matched="
            << lifecycleFingerprintRecoveryMatched
            << L"\n  lifecycle-hwpx-fingerprint-recovery="
            << lifecycleHwpxFingerprintRecoveryResponse
            << L"\n  lifecycle-hwpx-fingerprint-recovery-matched="
            << lifecycleHwpxFingerprintRecoveryMatched
            << L"\n  partial-mutation=" << partialMutationRequest
            << L"\n  atomic-rollback=" << atomicRollbackRequest
            << L"\n  atomic-rollback-retry=" << atomicRollbackRetryRequest
            << L"\n  atomic-rollback-restored=" << atomicRollbackRestored
            << L"\n  atomic-rollback-failure=" << atomicRollbackFailureRequest
            << L"\n  atomic-rollback-failure-retained-tail="
            << atomicRollbackFailureRetainedTail
            << L"\n  empty-cell-text=" << emptyCellTextRequest
            << L"\n  empty-cell-text-fixture-ready=" << emptyCellTextFixtureReady
            << L"\n  empty-cell-text-succeeded=" << emptyCellTextSucceeded
            << L"\n  empty-cell-text-inserted-exactly="
            << emptyCellTextInsertedExactly
            << L"\n  multiline-cell-text-invoked="
            << multilineCellTextInvoked
            << L"\n  multiline-cell-text-passed="
            << multilineCellTextPassed
            << L"\n  all-target-cell-text-preflight="
            << allTargetCellTextPreflightResponse
            << L"\n  all-target-cell-text-preflight-passed="
            << allTargetCellTextPreflightPassed
            << L"\n  strict-cell-selection-preflight="
            << strictCellSelectionPreflightResponse
            << L"\n  strict-cell-selection-preflight-passed="
            << strictCellSelectionPreflightPassed
            << L"\n  table-control-selection-preflight="
            << tableControlSelectionPreflightResponse
            << L"\n  table-control-selection-preflight-passed="
            << tableControlSelectionPreflightPassed
            << L"\n  selection-restore-failure="
            << selectionRestoreFailureResponse
            << L"\n  selection-restore-failure-passed="
            << selectionRestoreFailurePassed
            << L"\n  topology-reuse=" << topologyReuseResponse
            << L"\n  topology-reuse-passed=" << topologyReusePassed
            << L"\n  topology-formatting-preservation="
            << topologyFormattingPreservationResponse
            << L"\n  topology-formatting-preservation-passed="
            << topologyFormattingPreservationPassed
            << L"\n  topology-padding-preservation="
            << topologyPaddingPreservationResponse
            << L"\n  topology-padding-preservation-passed="
            << topologyPaddingPreservationPassed
            << L"\n  topology-cell-size-invalidation="
            << topologyCellSizeInvalidationResponse
            << L"\n  topology-cell-size-invalidation-passed="
            << topologyCellSizeInvalidationPassed
            << L"\n  topology-unknown-action-invalidation="
            << topologyUnknownActionInvalidationResponse
            << L"\n  topology-unknown-action-invalidation-passed="
            << topologyUnknownActionInvalidationPassed
            << L"\n  topology-invalidation=" << topologyInvalidationResponse
            << L"\n  topology-invalidation-passed=" << topologyInvalidationPassed
            << L"\n  topology-run-invalidation="
            << topologyRunInvalidationResponse
            << L"\n  topology-run-invalidation-passed="
            << topologyRunInvalidationPassed
            << L"\n  topology-action-invalidation="
            << topologyActionInvalidationResponse
            << L"\n  topology-action-invalidation-passed="
            << topologyActionInvalidationPassed
            << L"\n  topology-call-invalidation="
            << topologyCallInvalidationResponse
            << L"\n  topology-call-invalidation-passed="
            << topologyCallInvalidationPassed
            << L"\n  oversized-cell-patch=" << oversizedCellPatchResponse
            << L"\n  oversized-cell-patch-rejected="
            << oversizedCellPatchRejected
            << L"\n  reference-layout-success="
            << referenceLayoutSuccessRequest
            << L"\n  reference-layout-created-exactly="
            << referenceLayoutCreatedExactly
            << L"\n  reference-layout-border-readback-covered="
            << referenceLayoutBorderReadbackCovered
            << L"\n  reference-layout-atomic-rollback="
            << referenceLayoutAtomicRollbackRequest
            << L"\n  reference-layout-atomic-rollback-restored="
            << referenceLayoutAtomicRollbackRestored
            << L"\n  reference-layout-dropped-edges="
            << referenceLayoutDroppedEdgesRequest
            << L"\n  reference-layout-dropped-edges-rejected="
            << referenceLayoutDroppedEdgesRejected
            << L"\n  cell-topology-owner-index="
            << cellTopologyOwnerIndex
            << L"\n  selected-control-patch=" << selectedControlPatchRequest
            << L"\n  selected-control-patch-rejected=" << selectedControlPatchRejected
            << L"\n  selected-control-patch-insertions=" << selectedControlPatchInsertions
            << L"\n  text-deletion-patch=" << textDeletionPatchRequest
            << L"\n  text-deletion-patch-verified=" << textDeletionPatchVerified
            << L"\n  scoped-structure=" << scopedStructure
            << L"\n  scoped-structure-succeeded=" << scopedStructureSucceeded
            << L"\n  unscoped-structure=" << unscopedStructure
            << L"\n  unscoped-structure-succeeded=" << unscopedStructureSucceeded
            << L"\n  snapshot=" << snapshot
            << L"\n  page=" << page
            << L"\n  page-v3=" << pageV3
            << L"\n  structure=" << structure
            << L"\n  bad-action=" << badActionRequest
            << L"\n  bad-array=" << badArrayIndexRequest
            << L"\n  valid-array=" << validArrayRequest
            << L"\n  call-return=" << callReturnRequest
            << L"\n  stale-selection=" << staleSelectionRequest
            << L"\n  replace-selection=" << replaceSelectionRequest
            << L"\n  fallback-action=" << fallbackActionOfficialApiRequest
            << L"\n  seh-action=" << sehActionOfficialApiRequest
            << L"\n  event=" << eventOfficialApiRequest
            << L"\n  fallback-parameter=" << fallbackParameterOfficialApiRequest
            << L"\n  normalized-parameter=" << normalizedParameterOfficialApiRequest
            << L"\n  alias=" << aliasOfficialApiRequest
            << L"\n  item-alias=" << itemAliasOfficialApiRequest
            << L"\n  message-box-owner=" << messageBoxOwnerOfficialApiRequest
            << L"\n  write-only-property=" << writeOnlyPropertyOfficialApiRequest
            << L"\n  picture=" << sizedPictureRequest
            << L"\n  caption=" << formattedCaptionRequest
            << L"\n  table=" << capturedTableRequest
            << L"\n  delete-control=" << deleteControlRequest
            << L"\n  invalid-paste-order=" << invalidPasteOrderRequest
            << L"\n  caption-table=" << captionTableRequest
            << L"\n  break-page-before-preflight=" << dispatch->RanBreakPage()
            << L"\n  native-block-copy=" << dispatch->UsedNativeTableBlockCopy()
            << L"\n  native-block-paste=" << dispatch->UsedNativeTableBlockPaste()
            << L"\n  clipboard-transfer=" << dispatch->UsedClipboardTableTransfer()
            << L"\n  source-anchor-format-before-paste="
            << dispatch->PasteUsedSourceAnchorFormat()
            << L"\n  attached-caption-number-preserved="
            << dispatch->PreservedAttachedCaptionNumber()
            << L"\n  streaming-cell-text=" << streamedCellText
            << L"\n  streaming-scan=" << dispatch->UsedStreamingTextScan() << L'\n';
        for (size_t index = 0;
             index < multilineCellTextResponses.size();
             ++index) {
            std::wcerr
                << L"  multiline-case=" << multilineCellTextCases[index].label
                << L" passed=" << multilineCellTextCasePassed[index]
                << L" response=" << multilineCellTextResponses[index] << L'\n';
        }
        if (batch != nullptr) {
            batch->Release();
        }
        static_cast<void>(releasePublication());
        FreeLibrary(library);
        CoUninitialize();
        return 8;
    }
    batch->Release();
    CComPtr<FakeDispatch> secondaryDocumentOwner;
    secondaryDocumentOwner.Attach(
        new FakeDispatch(primaryWindowHandle, secondaryDocumentId));
    FakeDispatch* const secondaryDocumentDispatch = secondaryDocumentOwner;
    const int secondaryDocumentPublished =
        module->DoAction(kOnLoad, secondaryDocumentDispatch);
    const bool secondaryDocumentPublicationDoesNotOwnHwp =
        secondaryDocumentDispatch->ReferenceCount() == 2;
    const std::wstring secondaryDocumentScope =
        primaryScope + L"." + std::to_wstring(secondaryDocumentId);
    IUnknown* const retainedPrimaryDocumentRaw = GetPublishedObject(
        L"HancomLiveBridge." + primaryDocumentScope);
    IUnknown* const retainedPrimaryDocumentBatch = GetPublishedObject(
        L"HancomLiveBatch." + primaryDocumentScope);
    IUnknown* const secondaryDocumentRaw = GetPublishedObject(
        L"HancomLiveBridge." + secondaryDocumentScope);
    IUnknown* const secondaryDocumentBatch = GetPublishedObject(
        L"HancomLiveBatch." + secondaryDocumentScope);
    const bool documentRegistryWorked =
        secondaryDocumentPublished != FALSE &&
        secondaryDocumentPublicationDoesNotOwnHwp &&
        retainedPrimaryDocumentRaw != nullptr &&
        retainedPrimaryDocumentBatch != nullptr &&
        secondaryDocumentRaw != nullptr &&
        secondaryDocumentBatch != nullptr;
    if (retainedPrimaryDocumentRaw != nullptr) {
        retainedPrimaryDocumentRaw->Release();
    }
    if (retainedPrimaryDocumentBatch != nullptr) {
        retainedPrimaryDocumentBatch->Release();
    }
    if (secondaryDocumentRaw != nullptr) {
        secondaryDocumentRaw->Release();
    }
    if (secondaryDocumentBatch != nullptr) {
        secondaryDocumentBatch->Release();
    }
    if (!documentRegistryWorked) {
        std::wcerr << L"document-scoped ROT registry failed\n";
        static_cast<void>(releasePublication());
        FreeLibrary(library);
        CoUninitialize();
        return 20;
    }
    CComPtr<FakeDispatch> secondaryOwner;
    secondaryOwner.Attach(
        new FakeDispatch(secondaryWindowHandle, otherWindowDocumentId));
    FakeDispatch* const secondaryDispatch = secondaryOwner;
    const int secondaryPublished = module->DoAction(kOnLoad, secondaryDispatch);
    const bool secondaryPublicationDoesNotOwnHwp =
        secondaryDispatch->ReferenceCount() == 2;
    const std::wstring secondaryScope =
        processId + L"." + std::to_wstring(secondaryWindowHandle);
    const std::wstring otherWindowDocumentScope =
        secondaryScope + L"." + std::to_wstring(otherWindowDocumentId);
    IUnknown* const retainedPrimaryRaw = GetPublishedObject(
        L"HancomLiveBridge." + primaryScope);
    IUnknown* const retainedPrimaryBatch = GetPublishedObject(
        L"HancomLiveBatch." + primaryScope);
    IUnknown* const secondaryRaw = GetPublishedObject(
        L"HancomLiveBridge." + secondaryScope);
    IUnknown* const secondaryBatch = GetPublishedObject(
        L"HancomLiveBatch." + secondaryScope);
    IUnknown* const otherWindowDocumentRaw = GetPublishedObject(
        L"HancomLiveBridge." + otherWindowDocumentScope);
    IUnknown* const otherWindowDocumentBatch = GetPublishedObject(
        L"HancomLiveBatch." + otherWindowDocumentScope);
    const bool windowRegistryWorked =
        secondaryPublished != FALSE &&
        secondaryPublicationDoesNotOwnHwp &&
        retainedPrimaryRaw != nullptr &&
        retainedPrimaryBatch != nullptr &&
        secondaryRaw != nullptr &&
        secondaryBatch != nullptr &&
        otherWindowDocumentRaw != nullptr &&
        otherWindowDocumentBatch != nullptr;
    if (retainedPrimaryRaw != nullptr) {
        retainedPrimaryRaw->Release();
    }
    if (retainedPrimaryBatch != nullptr) {
        retainedPrimaryBatch->Release();
    }
    if (secondaryRaw != nullptr) {
        secondaryRaw->Release();
    }
    if (secondaryBatch != nullptr) {
        secondaryBatch->Release();
    }
    if (otherWindowDocumentRaw != nullptr) {
        otherWindowDocumentRaw->Release();
    }
    if (otherWindowDocumentBatch != nullptr) {
        otherWindowDocumentBatch->Release();
    }
    if (!windowRegistryWorked) {
        std::wcerr << L"window-scoped ROT registry failed\n";
        static_cast<void>(releasePublication());
        FreeLibrary(library);
        CoUninitialize();
        return 21;
    }
    if (argumentCount == 3 && std::wcscmp(arguments[2], L"--hold") == 0) {
        std::wcout << L"READY " << GetCurrentProcessId() << L'\n' << std::flush;
        const ULONGLONG deadline = GetTickCount64() + 15'000;
        while (GetTickCount64() < deadline) {
            static_cast<void>(MsgWaitForMultipleObjects(
                0,
                nullptr,
                FALSE,
                50,
                QS_ALLINPUT));
            MSG message{};
            while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
                TranslateMessage(&message);
                DispatchMessageW(&message);
            }
        }
    }
    const HRESULT revokeStatus = releasePublication();
    const bool revokePreservedHostOwnership =
        dispatch->ReferenceCount() == 1 &&
        secondaryDocumentDispatch->ReferenceCount() == 1 &&
        secondaryDispatch->ReferenceCount() == 1;
    if (FAILED(revokeStatus) ||
        !revokePreservedHostOwnership ||
        GetPublishedObject(L"HancomLiveBridge." + processId) != nullptr ||
        GetPublishedObject(L"HancomLiveBatch." + processId) != nullptr ||
        GetPublishedObject(L"HancomLiveBridge." + primaryScope) != nullptr ||
        GetPublishedObject(L"HancomLiveBatch." + primaryScope) != nullptr ||
        GetPublishedObject(
            L"HancomLiveBridge." + primaryDocumentScope) != nullptr ||
        GetPublishedObject(
            L"HancomLiveBatch." + primaryDocumentScope) != nullptr ||
        GetPublishedObject(
            L"HancomLiveBridge." + secondaryDocumentScope) != nullptr ||
        GetPublishedObject(
            L"HancomLiveBatch." + secondaryDocumentScope) != nullptr ||
        GetPublishedObject(L"HancomLiveBridge." + secondaryScope) != nullptr ||
        GetPublishedObject(L"HancomLiveBatch." + secondaryScope) != nullptr ||
        GetPublishedObject(
            L"HancomLiveBridge." + otherWindowDocumentScope) != nullptr ||
        GetPublishedObject(
            L"HancomLiveBatch." + otherWindowDocumentScope) != nullptr) {
        std::wcerr << L"ROT revoke failed\n";
        FreeLibrary(library);
        CoUninitialize();
        return 9;
    }

    for (size_t index = 0;
         index < multilineCellTextResponses.size();
         ++index) {
        std::wcout
            << L"MULTILINE_CELL_TEXT " << multilineCellTextCases[index].label
            << L" passed=" << multilineCellTextCasePassed[index]
            << L" response=" << multilineCellTextResponses[index] << L'\n';
    }
    std::wcout
               << L"ALL_TARGET_CELL_TEXT_PREFLIGHT "
               << allTargetCellTextPreflightResponse << L'\n'
               << L"STRICT_CELL_SELECTION_PREFLIGHT "
               << strictCellSelectionPreflightResponse << L'\n'
               << L"TABLE_CONTROL_SELECTION_PREFLIGHT "
               << tableControlSelectionPreflightResponse << L'\n'
               << L"SELECTION_RESTORE_FAILURE "
               << selectionRestoreFailureResponse << L'\n'
               << L"TABLE_TOPOLOGY_REUSE " << topologyReusePassed << L'\n'
               << L"TABLE_TOPOLOGY_FORMATTING_PRESERVATION "
               << topologyFormattingPreservationPassed << L'\n'
               << L"TABLE_TOPOLOGY_PADDING_PRESERVATION "
               << topologyPaddingPreservationPassed << L'\n'
               << L"TABLE_TOPOLOGY_CELL_SIZE_INVALIDATION "
               << topologyCellSizeInvalidationPassed << L'\n'
               << L"TABLE_TOPOLOGY_UNKNOWN_ACTION_INVALIDATION "
               << topologyUnknownActionInvalidationPassed << L'\n'
               << L"TABLE_TOPOLOGY_INVALIDATION "
               << topologyInvalidationPassed << L'\n'
               << L"TABLE_TOPOLOGY_RUN_INVALIDATION "
               << topologyRunInvalidationPassed << L'\n'
               << L"TABLE_TOPOLOGY_ACTION_INVALIDATION "
               << topologyActionInvalidationPassed << L'\n'
               << L"TABLE_TOPOLOGY_CALL_INVALIDATION "
               << topologyCallInvalidationPassed << L'\n'
               << L"OVERSIZED_CELL_PATCH_REJECTED "
               << oversizedCellPatchRejected << L'\n'
               << L"DOCUMENT_SECTION_DIAGNOSTICS "
               << saveVerifySectionDiagnosticsMatched
               << L" normalized_wchars=8388636"
               << L" iterations=" << kSectionDiagnosticsIterations
               << L" total_us="
               << saveVerifySectionDiagnosticsElapsedMicroseconds
               << L" average_us="
               << (saveVerifySectionDiagnosticsElapsedMicroseconds /
                   kSectionDiagnosticsIterations)
               << L'\n'
               << L"LIFECYCLE_SUCCESS " << lifecycleSuccessResponse << L'\n'
               << L"LIFECYCLE_CLEAN_NOOP " << lifecycleCleanNoOpResponse << L'\n'
               << L"LIFECYCLE_SAVE_GATE " << lifecycleSaveGateResponse << L'\n'
               << L"LIFECYCLE_SAVE_RETURN_GATE " << lifecycleSaveReturnGateResponse << L'\n'
               << L"LIFECYCLE_RECOVERY_CAPTURE_GATE "
               << lifecycleRecoveryCaptureGateResponse << L'\n'
               << L"LIFECYCLE_RECOVERY " << lifecycleRecoveryResponse << L'\n'
               << L"LIFECYCLE_HWPX_SUCCESS " << lifecycleHwpxSuccessResponse << L'\n'
               << L"LIFECYCLE_HWPX_RECOVERY " << lifecycleHwpxRecoveryResponse << L'\n'
               << L"LIFECYCLE_FINGERPRINT_RECOVERY "
               << lifecycleFingerprintRecoveryResponse << L'\n'
               << L"LIFECYCLE_HWPX_FINGERPRINT_RECOVERY "
               << lifecycleHwpxFingerprintRecoveryResponse << L'\n'
               << L"ATOMIC_ROLLBACK " << atomicRollbackRequest << L'\n'
               << L"ATOMIC_ROLLBACK_RETRY " << atomicRollbackRetryRequest << L'\n'
               << L"ATOMIC_ROLLBACK_TAIL_RESTORED " << atomicRollbackRestored << L'\n'
               << L"ATOMIC_ROLLBACK_FAILURE " << atomicRollbackFailureRequest << L'\n'
               << L"ATOMIC_ROLLBACK_FAILURE_TAIL_RETAINED "
               << atomicRollbackFailureRetainedTail << L'\n'
               << L"CHECKPOINT_RESTORE " << checkpointRestoreRequest << L'\n'
               << L"CHECKPOINT_ROLLBACK " << checkpointRollbackRequest << L'\n'
               << L"CHECKPOINT_ROLLBACK_RESTORED "
               << checkpointRollbackSucceeded << L'\n'
               << L"EMPTY_CELL_TEXT " << emptyCellTextRequest << L'\n'
               << L"EMPTY_CELL_TEXT_POSITION actual=650:0:0 stale=0:0:0\n"
               << L"EMPTY_CELL_TEXT_INSERTED_EXACTLY "
               << emptyCellTextInsertedExactly << L'\n'
               << L"REFERENCE_LAYOUT_SUCCESS "
               << referenceLayoutSuccessRequest << L'\n'
               << L"REFERENCE_LAYOUT_CREATED_EXACTLY "
               << referenceLayoutCreatedExactly << L'\n'
               << L"REFERENCE_LAYOUT_BORDER_READBACK_COVERED "
               << referenceLayoutBorderReadbackCovered << L'\n'
               << L"REFERENCE_LAYOUT_ATOMIC_ROLLBACK "
               << referenceLayoutAtomicRollbackRequest << L'\n'
               << L"REFERENCE_LAYOUT_ATOMIC_ROLLBACK_TAIL_RESTORED "
               << referenceLayoutAtomicRollbackRestored << L'\n'
               << L"REFERENCE_LAYOUT_DROPPED_EDGES "
               << referenceLayoutDroppedEdgesRequest << L'\n'
               << L"CELL_TOPOLOGY_OWNER_INDEX "
               << cellTopologyOwnerIndex << L'\n'
               << L"SELECTED_CONTROL_PATCH " << selectedControlPatchRequest << L'\n'
               << L"TEXT_DELETION_PATCH " << textDeletionPatchRequest << L'\n'
               << L"SCOPED_STRUCTURE " << scopedStructureSucceeded << L'\n'
               << L"UNSCOPED_STRUCTURE " << unscopedStructureSucceeded << L'\n'
               << L"PASS UserAction ABI, inspection, lifecycle, action batch, "
                  L"window/document-scoped ROT publication, and revoke\n";
    FreeLibrary(library);
    CoUninitialize();
    return 0;
}
