#include <Windows.h>
#include <ObjIdl.h>
#include <Ocidl.h>
#include <Ole2.h>
#include <atlcomcli.h>

#include "../BridgeStatus.h"
#include "../TableInspection.h"
#include "FakeParameterArrayDispatch.h"

#include <algorithm>
#include <cstring>
#include <cwchar>
#include <fstream>
#include <iostream>
#include <limits>
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

    bool RecoveredLifecycleSession() const noexcept {
        return lifecycleRecovered_ && documentOpen_ && modified_;
    }

    void PrepareLifecycleSuccess() {
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
        fullName_ = L"C:\\\uD55C\uAE00\\x.hwp";
        requireActiveDocumentFullName_ = true;
        activeDocumentFullNameReady_ = false;
        activeDocumentFullNameReads_ = 0;
        unstableSerializationMetadataFixture_ = false;
        unstableCaretMetadataFixture_ = true;
        serializationReads_ = 0;
    }

    void PrepareSaveVerifySerializationFixture() {
        PrepareLifecycleSuccess();
        unstableSerializationMetadataFixture_ = true;
        unstableCaretMetadataFixture_ = false;
    }

    void PrepareLifecycleSaveFailure() {
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
        fullName_ = L"C:\\\uD55C\uAE00\\x.hwp";
        requireActiveDocumentFullName_ = true;
        activeDocumentFullNameReady_ = false;
        activeDocumentFullNameReads_ = 0;
    }

    void PrepareLifecycleFalseSaveReturn() {
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
        fullName_ = L"C:\\\uD55C\uAE00\\x.hwp";
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

    bool AtomicTailRollbackFailureRetainedTail() const noexcept {
        return !atomicTail_.empty() && pageCount_ == 2 && atomicTailDeletes_ == 0;
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

    bool MessageBoxModeRestored() const noexcept {
        return messageBoxMode_ == kInitialMessageBoxMode;
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
        } else if (name == L"CtrlID") {
            members[0] = CtrlID;
        } else if (name == L"Next") {
            members[0] = Next;
        } else if (name == L"GetPos") {
            members[0] = GetPos;
        } else if (name == L"Save") {
            members[0] = Save;
        } else if (name == L"Clear") {
            members[0] = Clear;
        } else if (name == L"Open") {
            members[0] = Open;
        } else if (name == L"DeleteCtrl") {
            members[0] = DeleteCtrl;
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
             member == ReleaseScan || member == GetPos)) {
            result = &ignoredResult;
        }
        if (result == nullptr) {
            return E_POINTER;
        }
        VariantInit(result);
        if ((flags & DISPATCH_PROPERTYGET) != 0) {
            if (member == HSet) {
                result->vt = VT_DISPATCH;
                result->pdispVal = parameterSet_;
                static_cast<void>(parameterSet_->AddRef());
                return S_OK;
            }
            if (member == XHwpDocuments || member == ActiveDocument ||
                member == XHwpWindows || member == ActiveWindow || member == HeadCtrl ||
                member == HAction || member == CurSelectedCtrl ||
                member == HParameterSet ||
                member == XHwpMessageBox || member == Application) {
                if (member == HeadCtrl && !documentOpen_) {
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
            if (member == HStyle || member == HParaShape || member == HInsertText) {
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
                result->vt = VT_BSTR;
                result->bstrVal = SysAllocString(L"tbl");
                return result->bstrVal != nullptr ? S_OK : E_OUTOFMEMORY;
            }
            if (member == Next) {
                result->vt = VT_EMPTY;
                return S_OK;
            }
            if (member == SetID) {
                result->vt = VT_BSTR;
                result->bstrVal = SysAllocString(L"TableCreation");
                return result->bstrVal != nullptr ? S_OK : E_OUTOFMEMORY;
            }
            if (member >= Apply && member <= NextSpacing) {
                result->vt = VT_I4;
                result->lVal = ProfileValue(currentProfile_, member);
                return S_OK;
            }
        }
        if ((flags & DISPATCH_METHOD) != 0) {
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
                *parameters->rgvarg[2].plVal = currentList_;
                *parameters->rgvarg[1].plVal = currentParagraph_;
                *parameters->rgvarg[0].plVal = currentCharacter_;
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
            if (member == Clear) {
                if (parameters == nullptr || parameters->cArgs != 1 ||
                    parameters->rgvarg[0].vt != VT_I2) {
                    return DISP_E_TYPEMISMATCH;
                }
                clearCalledWithDiscard_ = parameters->rgvarg[0].iVal == 1;
                lifecycleCalls_.push_back(L"Clear");
                documentOpen_ = false;
                modified_ = false;
                fullName_.clear();
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
                openCalledWithArguments_ = path == L"C:\\\uD55C\uAE00\\x.hwp" &&
                    format == L"HWP" && options == L"lock:FALSE";
                lifecycleCalls_.push_back(L"Open");
                if (openReturnsTrue_) {
                    documentOpen_ = true;
                    modified_ = false;
                    fullName_ = L"C:\\\uD55C\uAE00\\X.HWP";
                }
                result->vt = VT_BOOL;
                result->boolVal =
                    openReturnsTrue_ ? VARIANT_TRUE : VARIANT_FALSE;
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
                const LONG previous = messageBoxMode_;
                messageBoxMode_ = parameters->rgvarg[0].lVal;
                autoYesMessageBoxModeUsed_ = autoYesMessageBoxModeUsed_ ||
                    messageBoxMode_ == 0x00011010;
                result->vt = VT_I4;
                result->lVal = previous;
                return S_OK;
            }
            if (member == SelectCtrl) {
                result->vt = VT_BOOL;
                result->boolVal = VARIANT_FALSE;
                return S_OK;
            }
            if (member == DeleteCtrl) {
                if (parameters == nullptr || parameters->cArgs != 1 ||
                    parameters->rgvarg[0].vt != VT_DISPATCH) {
                    return DISP_E_TYPEMISMATCH;
                }
                result->vt = VT_BOOL;
                result->boolVal = VARIANT_TRUE;
                return S_OK;
            }
            if (member == GetAnchorPos) {
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
                *parameters->rgvarg[0].pbstrVal = SysAllocString(
                    content ? L"cell-text" : L"");
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
                    unstableHwpml &&
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
                    : nativeTableBlockCopied_
                    ? L"SFdQX05BVElWRV9UQUJMRQ=="
                    : (format == L"TEXT" && options.empty()
                        ? L"body-text\tcell-text"
                        : (format == L"HWPML2X" && options.empty()
                            ? hwpml
                        : (format == L"HWP" && options.empty()
                            ? L"SERIALIZED_BODY_TABLE_RED_STYLE"
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
                if (block == L"SERIALIZED_BODY_TABLE_RED_STYLE" &&
                    format == L"HWP" &&
                    options.empty()) {
                    lifecycleRecovered_ = true;
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
                pendingProfile_ = currentProfile_;
                result->vt = VT_BOOL;
                result->boolVal = VARIANT_TRUE;
                return S_OK;
            }
            if (member == Execute) {
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
                result->bstrVal = SysAllocString(L"captured-table");
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
        static_cast<void>(parameterSet_->Release());
    }
    volatile LONG references_ = 1;
    LONG windowHandle_ = 0;
    LONG documentId_ = 0;
    LONG pendingDocumentId_ = 0;
    FakeParameterArrayDispatch* parameterSet_ = new FakeParameterArrayDispatch();
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
    bool saveReturnsTrue_ = true;
    bool saveClearsModified_ = true;
    bool openReturnsTrue_ = true;
    bool modified_ = false;
    bool documentOpen_ = true;
    std::wstring fullName_ = L"C:\\x.hwp";
    bool requireActiveDocumentFullName_ = false;
    bool activeDocumentFullNameReady_ = false;
    LONG activeDocumentFullNameReads_ = 0;
    bool unstableSerializationMetadataFixture_ = false;
    bool unstableCaretMetadataFixture_ = false;
    size_t serializationReads_ = 0;
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

bool AtomicRollbackSucceeded(const std::wstring& response) {
    const std::vector<std::wstring> fields = SplitTabs(response);
    return fields.size() == 11 && fields[0] == L"HCA2" && fields[1] == L"ERROR" &&
        fields[2] == L"COM_METHOD" && fields[3] == L"U2VsZWN0Q3RybEZyb250" &&
        fields[4] == L"UnVuIGZhaWxlZCAoSFJFU1VMVCAweDgwMDA0MDA1KQ==" &&
        fields[5] == L"3" && fields[6] == fields[3] && fields[7] == L"0" &&
        fields[8] == L"1" && !fields[9].empty() && fields[9] == fields[10];
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
    return fields.size() == 20 &&
        fields[0] == L"HLS1" &&
        fields[1] == L"1" &&
        fields[2] == L"Qzpc7ZWc6riAXHguaHdw" &&
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
        fields[19].find_first_not_of(L"0123456789") == std::wstring::npos;
}

bool LifecycleSuccessMatched(const std::wstring& response) {
    const std::vector<std::wstring> fields = SplitTabs(response);
    const std::wstring pending = std::to_wstring(static_cast<LONG>(E_PENDING));
    return fields.size() == 26 && fields[0] == L"HCL12" && fields[1] == L"1" &&
        fields[2] == L"Qzpc7ZWc6riAXFguSFdQ" && fields[3] == L"1" && fields[4] == L"1" &&
        fields[5] == L"1" && !fields[6].empty() && !fields[7].empty() &&
        !fields[8].empty() && fields[9] == L"0" && fields[10] == L"1" &&
        fields[11] == L"0" && fields[12] == L"0" && fields[13] == L"-1" &&
        fields[14] == L"0" && fields[15] == L"1" && fields[16] == L"0" &&
        fields[17] == pending && fields[18] == L"-1" && fields[19] == fields[3] &&
        fields[20] == L"0" && fields[21] == fields[5] && fields[22] == fields[6] &&
        fields[23] == fields[7] && fields[24] == fields[8] &&
        fields[25].find_first_not_of(L"0123456789") == std::wstring::npos;
}

bool LifecycleCleanNoOpMatched(const std::wstring& response) {
    const std::vector<std::wstring> fields = SplitTabs(response);
    return LifecycleSuccessMatched(response) ||
        (fields.size() == 26 && fields[0] == L"HCL12" && fields[1] == L"1" &&
         fields[4] == L"0" && fields[10] == L"0" && fields[20] == L"0" &&
         fields[22] == fields[6] && fields[23] == fields[7] &&
         fields[24] == fields[8]);
}

bool LifecycleSaveGateMatched(const std::wstring& response) {
    const std::vector<std::wstring> fields = SplitTabs(response);
    const std::wstring pending = std::to_wstring(static_cast<LONG>(E_PENDING));
    return fields.size() == 26 && fields[0] == L"HCL12" && fields[1] == L"0" &&
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
    return fields.size() == 26 && fields[0] == L"HCL12" && fields[1] == L"0" &&
        fields[4] == L"1" && fields[9] == L"0" && fields[10] == L"0" &&
        fields[11] == L"0" && fields[12] == pending && fields[13] == L"-1" &&
        fields[14] == pending && fields[15] == L"-1" && fields[16] == L"0" &&
        fields[17] == pending && fields[18] == L"-1" && fields[20] == L"0" &&
        fields[22] == fields[6] && fields[23] == fields[7] &&
        fields[24] == fields[8];
}

bool LifecycleRecoveryMatched(const std::wstring& response) {
    const std::vector<std::wstring> fields = SplitTabs(response);
    return fields.size() == 26 && fields[0] == L"HCL12" && fields[1] == L"0" &&
        fields[2].empty() && fields[9] == L"0" && fields[10] == L"1" &&
        fields[11] == L"0" && fields[12] == L"0" && fields[13] == L"-1" &&
        fields[14] == L"0" && fields[15] == L"0" && fields[16] == L"1" &&
        fields[17] == L"0" && fields[18] == L"1" && fields[19] == fields[3] &&
        fields[20] == L"1" && fields[21] == fields[5] &&
        fields[22] == fields[6] && fields[23] == fields[7] &&
        fields[24] == fields[8];
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
    IUnknown* const raw = GetPublishedObject(L"HancomLiveBridge." + processId);
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
        << L" raw=" << (raw != nullptr ? L"present" : L"missing")
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

    if (raw != nullptr) {
        raw->Release();
    }
    if (batchUnknown != nullptr) {
        batchUnknown->Release();
    }
    return raw != nullptr && batchUnknown != nullptr && protocolMatched && pingMatched
        ? 0
        : 10;
}

}

int wmain(const int argumentCount, wchar_t** const arguments) {
    const bool probeMode = argumentCount == 3 &&
        std::wcscmp(arguments[1], L"--probe") == 0;
    const bool probeOutputMode = argumentCount == 4 &&
        std::wcscmp(arguments[1], L"--probe-output") == 0;
    const bool probeAsTargetMode = argumentCount == 4 &&
        std::wcscmp(arguments[1], L"--probe-as-target") == 0;
    const bool runAsTargetMode = argumentCount >= 5 &&
        std::wcscmp(arguments[1], L"--run-as-target") == 0;
    const bool runAsTargetStdinMode = argumentCount >= 6 &&
        std::wcscmp(arguments[1], L"--run-as-target-stdin") == 0;
    if ((!probeMode && !probeOutputMode &&
         !probeAsTargetMode && !runAsTargetMode && !runAsTargetStdinMode &&
         (argumentCount < 2 || argumentCount > 3)) ||
        (probeMode && argumentCount != 3) ||
        (probeOutputMode && argumentCount != 4) ||
        (probeAsTargetMode && argumentCount != 4) ||
        (runAsTargetMode && argumentCount < 5) ||
        (runAsTargetStdinMode && argumentCount < 6)) {
        std::wcerr
            << L"usage: BridgeSmoke.exe <HancomLiveBridge.dll> [--hold]\n"
            << L"       BridgeSmoke.exe --probe <process-id>\n"
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
    FakeDispatch* const dispatch =
        new FakeDispatch(primaryWindowHandle, primaryDocumentId);
    const int published = module->DoAction(kOnLoad, dispatch);
    std::wstring streamedCellText;
    const bool streamingScanWorked = hancom::inspection::ReadCurrentListText(
        dispatch,
        &streamedCellText);
    static_cast<void>(dispatch->Release());
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
        documentBatchUnknown == nullptr || !diagnosticsValid) {
        std::wcerr << L"ROT publication failed: " << getLastResult() << L'\n';
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
    dispatch->PrepareLifecycleOpenFailure();
    std::wstring lifecycleRecoveryResponse;
    const bool lifecycleRecoveryInvoked = SUCCEEDED(dispatchStatus) && batch != nullptr &&
        InvokeString(batch, L"SaveReopenVerify", nullptr, &lifecycleRecoveryResponse);
    const bool lifecycleRecoveryMatched =
        LifecycleRecoveryMatched(lifecycleRecoveryResponse) &&
        dispatch->RecoveredLifecycleSession();
    dispatch->RestoreLifecycleFixture();
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
    if (FAILED(dispatchStatus) || !ReadProtocolVersion(batch) ||
        !dynamicDocumentActivationWorked ||
        !saveVerifyInvoked || !saveVerifyMatched ||
        !lifecycleSuccessInvoked || !lifecycleSuccessMatched ||
        !lifecycleCleanNoOpInvoked || !lifecycleCleanNoOpMatched ||
        !lifecycleSaveGateInvoked || !lifecycleSaveGateMatched ||
        !lifecycleSaveReturnGateInvoked || !lifecycleSaveReturnGateMatched ||
        !lifecycleRecoveryInvoked || !lifecycleRecoveryMatched ||
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
        !selectedControlPatchInvoked || !selectedControlPatchRejected ||
        !textDeletionPatchInvoked || !textDeletionPatchVerified ||
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
            << L"\n  lifecycle-success-sequence=" << dispatch->CompletedLifecycleSequence()
            << L"\n  lifecycle-clean-noop=" << lifecycleCleanNoOpResponse
            << L"\n  lifecycle-clean-noop-matched=" << lifecycleCleanNoOpMatched
            << L"\n  lifecycle-save-gate=" << lifecycleSaveGateResponse
            << L"\n  lifecycle-save-stopped=" << dispatch->StoppedLifecycleAfterSave()
            << L"\n  lifecycle-save-return-gate=" << lifecycleSaveReturnGateResponse
            << L"\n  lifecycle-recovery=" << lifecycleRecoveryResponse
            << L"\n  lifecycle-recovery-matched=" << lifecycleRecoveryMatched
            << L"\n  partial-mutation=" << partialMutationRequest
            << L"\n  atomic-rollback=" << atomicRollbackRequest
            << L"\n  atomic-rollback-retry=" << atomicRollbackRetryRequest
            << L"\n  atomic-rollback-restored=" << atomicRollbackRestored
            << L"\n  atomic-rollback-failure=" << atomicRollbackFailureRequest
            << L"\n  atomic-rollback-failure-retained-tail="
            << atomicRollbackFailureRetainedTail
            << L"\n  selected-control-patch=" << selectedControlPatchRequest
            << L"\n  selected-control-patch-rejected=" << selectedControlPatchRejected
            << L"\n  selected-control-patch-insertions=" << selectedControlPatchInsertions
            << L"\n  text-deletion-patch=" << textDeletionPatchRequest
            << L"\n  text-deletion-patch-verified=" << textDeletionPatchVerified
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
        if (batch != nullptr) {
            batch->Release();
        }
        static_cast<void>(releasePublication());
        FreeLibrary(library);
        CoUninitialize();
        return 8;
    }
    batch->Release();
    FakeDispatch* const secondaryDocumentDispatch =
        new FakeDispatch(primaryWindowHandle, secondaryDocumentId);
    const int secondaryDocumentPublished =
        module->DoAction(kOnLoad, secondaryDocumentDispatch);
    static_cast<void>(secondaryDocumentDispatch->Release());
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
    FakeDispatch* const secondaryDispatch =
        new FakeDispatch(secondaryWindowHandle, otherWindowDocumentId);
    const int secondaryPublished = module->DoAction(kOnLoad, secondaryDispatch);
    static_cast<void>(secondaryDispatch->Release());
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
    if (FAILED(releasePublication()) ||
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

    std::wcout << L"LIFECYCLE_SUCCESS " << lifecycleSuccessResponse << L'\n'
               << L"LIFECYCLE_CLEAN_NOOP " << lifecycleCleanNoOpResponse << L'\n'
               << L"LIFECYCLE_SAVE_GATE " << lifecycleSaveGateResponse << L'\n'
               << L"LIFECYCLE_SAVE_RETURN_GATE " << lifecycleSaveReturnGateResponse << L'\n'
               << L"ATOMIC_ROLLBACK " << atomicRollbackRequest << L'\n'
               << L"ATOMIC_ROLLBACK_RETRY " << atomicRollbackRetryRequest << L'\n'
               << L"ATOMIC_ROLLBACK_TAIL_RESTORED " << atomicRollbackRestored << L'\n'
               << L"ATOMIC_ROLLBACK_FAILURE " << atomicRollbackFailureRequest << L'\n'
               << L"ATOMIC_ROLLBACK_FAILURE_TAIL_RETAINED "
               << atomicRollbackFailureRetainedTail << L'\n'
               << L"SELECTED_CONTROL_PATCH " << selectedControlPatchRequest << L'\n'
               << L"TEXT_DELETION_PATCH " << textDeletionPatchRequest << L'\n'
               << L"PASS UserAction ABI, inspection, lifecycle, action batch, "
                  L"window/document-scoped ROT publication, and revoke\n";
    FreeLibrary(library);
    CoUninitialize();
    return 0;
}
