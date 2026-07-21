#include "BridgeStatus.h"

#include <cstring>
#include <cwchar>

namespace bridge_status {
namespace {

INIT_ONCE initialization = INIT_ONCE_STATIC_INIT;
HANDLE mapping = nullptr;
Snapshot* status = nullptr;

BOOL CALLBACK InitializeStatus(
    PINIT_ONCE,
    PVOID,
    PVOID*) noexcept {
    wchar_t name[96] = {};
    if (swprintf_s(
            name,
            L"%s%lu",
            kMappingPrefix,
            static_cast<unsigned long>(GetCurrentProcessId())) < 0) {
        return TRUE;
    }

    mapping = CreateFileMappingW(
        INVALID_HANDLE_VALUE,
        nullptr,
        PAGE_READWRITE,
        0,
        sizeof(Snapshot),
        name);
    if (mapping == nullptr) {
        return TRUE;
    }

    status = static_cast<Snapshot*>(MapViewOfFile(
        mapping,
        FILE_MAP_ALL_ACCESS,
        0,
        0,
        sizeof(Snapshot)));
    if (status == nullptr) {
        CloseHandle(mapping);
        mapping = nullptr;
        return TRUE;
    }

    SecureZeroMemory(status, sizeof(Snapshot));
    status->magic = kMagic;
    status->version = kVersion;
    status->size = sizeof(Snapshot);
    status->processId = GetCurrentProcessId();
    status->lastResult = E_UNEXPECTED;
    return TRUE;
}

Snapshot* GetStatus() noexcept {
    static_cast<void>(InitOnceExecuteOnce(
        &initialization,
        InitializeStatus,
        nullptr,
        nullptr));
    return status;
}

void BeginWrite(Snapshot* const shared) noexcept {
    static_cast<void>(InterlockedIncrement(&shared->sequence));
}

void EndWrite(Snapshot* const shared) noexcept {
    static_cast<void>(InterlockedIncrement(&shared->sequence));
}

void SetLastAction(
    Snapshot* const shared,
    const char* const action) noexcept {
    if (action == nullptr) {
        shared->lastAction[0] = '\0';
        return;
    }
    static_cast<void>(strncpy_s(
        shared->lastAction,
        action,
        _TRUNCATE));
}

template <typename Counter>
void NoteAction(
    Counter counter,
    const char* const action) noexcept {
    Snapshot* const shared = GetStatus();
    if (shared == nullptr) {
        return;
    }
    BeginWrite(shared);
    static_cast<void>(InterlockedIncrement(&(shared->*counter)));
    SetLastAction(shared, action);
    EndWrite(shared);
}

}

void NoteQuery() noexcept {
    Snapshot* const shared = GetStatus();
    if (shared != nullptr) {
        static_cast<void>(InterlockedIncrement(&shared->queryCount));
    }
}

void NoteEnum(const char* const action) noexcept {
    NoteAction(&Snapshot::enumCount, action);
}

void NoteUpdateUi(const char* const action) noexcept {
    NoteAction(&Snapshot::updateUiCount, action);
}

void NoteDoAction(const char* const action) noexcept {
    NoteAction(&Snapshot::doActionCount, action);
}

void NotePublish(
    const HRESULT result,
    const DWORD rawCookie,
    const DWORD batchCookie) noexcept {
    Snapshot* const shared = GetStatus();
    if (shared == nullptr) {
        return;
    }
    BeginWrite(shared);
    static_cast<void>(InterlockedIncrement(&shared->publishCount));
    if (SUCCEEDED(result)) {
        static_cast<void>(InterlockedIncrement(&shared->publishSuccessCount));
    }
    static_cast<void>(InterlockedExchange(&shared->lastResult, result));
    static_cast<void>(InterlockedExchange(
        &shared->rawCookie,
        static_cast<LONG>(rawCookie)));
    static_cast<void>(InterlockedExchange(
        &shared->batchCookie,
        static_cast<LONG>(batchCookie)));
    EndWrite(shared);
}

void NoteRevoke(const HRESULT result) noexcept {
    Snapshot* const shared = GetStatus();
    if (shared == nullptr) {
        return;
    }
    BeginWrite(shared);
    static_cast<void>(InterlockedIncrement(&shared->revokeCount));
    static_cast<void>(InterlockedExchange(&shared->lastResult, result));
    static_cast<void>(InterlockedExchange(&shared->rawCookie, 0));
    static_cast<void>(InterlockedExchange(&shared->batchCookie, 0));
    EndWrite(shared);
}

}
