#include "BridgeStatus.h"

#include <cstring>
#include <cwchar>

namespace bridge_status {
namespace {

INIT_ONCE initialization = INIT_ONCE_STATIC_INIT;
HANDLE mapping = nullptr;
HANDLE slotClickEvent = nullptr;
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
    status->slotCount = kSlotCount;
    status->clickRingCapacity = kClickRingCapacity;
    // -1, not 0, so "no slot has been clicked yet" is distinguishable from
    // "slot 0 was clicked".
    status->lastSlotIndex = -1;

    wchar_t eventName[96] = {};
    if (swprintf_s(
            eventName,
            L"%s%lu",
            kSlotClickEventPrefix,
            static_cast<unsigned long>(GetCurrentProcessId())) >= 0) {
        // Auto-reset: one worker waits. Clicks that arrive while nobody waits
        // are not lost -- they are in the ring, and the single pending signal
        // wakes the worker into a scan that drains every one of them.
        slotClickEvent = CreateEventW(nullptr, FALSE, FALSE, eventName);
    }
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

void NoteSlotClick(const LONG slotIndex) noexcept {
    if (slotIndex < 0 || slotIndex >= kSlotCount) {
        return;
    }
    Snapshot* const shared = GetStatus();
    if (shared == nullptr) {
        return;
    }
    // Runs on the Hancom UI thread. No COM, no allocation, no blocking call --
    // the whole point of the queue is that DoAction returns immediately.
    const LONG tick = static_cast<LONG>(GetTickCount());
    BeginWrite(shared);
    const LONG sequence = InterlockedIncrement(&shared->slotClickCount);
    static_cast<void>(InterlockedExchange(&shared->lastSlotIndex, slotIndex));
    static_cast<void>(InterlockedExchange(&shared->lastSlotSequence, sequence));
    SlotClickRecord& record =
        shared->clickRing[(sequence - 1) % kClickRingCapacity];
    // slotIndex and tick land before sequence so a reader that sees the
    // sequence sees a complete record. The outer seqlock covers the rest.
    static_cast<void>(InterlockedExchange(&record.slotIndex, slotIndex));
    static_cast<void>(InterlockedExchange(&record.tick, tick));
    static_cast<void>(InterlockedExchange(&record.sequence, sequence));
    EndWrite(shared);
    if (slotClickEvent != nullptr) {
        static_cast<void>(SetEvent(slotClickEvent));
    }
}

bool SlotsEnabled() noexcept {
    Snapshot* const shared = GetStatus();
    if (shared == nullptr) {
        return false;
    }
    const DWORD beat =
        static_cast<DWORD>(InterlockedCompareExchange(&shared->heartbeatTick, 0, 0));
    const DWORD declared = static_cast<DWORD>(
        InterlockedCompareExchange(&shared->heartbeatStaleAfterMs, 0, 0));
    DWORD window = declared == 0 ? kDefaultHeartbeatStaleMilliseconds : declared;
    if (window < kMinimumHeartbeatStaleMilliseconds) {
        window = kMinimumHeartbeatStaleMilliseconds;
    }
    if (window > kMaximumHeartbeatStaleMilliseconds) {
        window = kMaximumHeartbeatStaleMilliseconds;
    }
    // A worker that has never written leaves 0 here. Treat that as "no worker"
    // rather than "beat at boot", which would be true only in the first
    // `window` milliseconds after a reboot.
    const bool published = InterlockedCompareExchange(&shared->heartbeatWorkerPid, 0, 0) != 0;
    // Unsigned wrap-around subtraction: correct across the 49.7-day GetTickCount
    // rollover for any elapsed interval shorter than that.
    const DWORD elapsed = GetTickCount() - beat;
    const bool enabled = published && elapsed <= window;
    static_cast<void>(
        InterlockedExchange(&shared->slotUiEnabled, enabled ? 1 : 0));
    return enabled;
}

}
