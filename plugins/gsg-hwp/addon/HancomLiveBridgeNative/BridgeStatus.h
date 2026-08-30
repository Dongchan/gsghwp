#pragma once

#include <Windows.h>

#include <cstddef>

namespace bridge_status {

constexpr DWORD kMagic = 0x31534248;
// Version 2 appends the Stage 2 slot-click ring and the worker heartbeat *after*
// `lastAction`. Every version-1 field keeps its byte offset, so a version-1
// reader that maps the first 124 bytes reads exactly what it read before -- the
// static_asserts below are the contract, not a comment.
constexpr DWORD kVersion = 2;
constexpr DWORD kVersion1Size = 124;
constexpr wchar_t kMappingPrefix[] = L"Local\\HancomLiveBridgeStatus.";
// Auto-reset event the DLL sets on every slot click so the worker does not have
// to poll. Losing it is not fatal: the click is already in the ring, and a
// polling worker still finds it by sequence.
constexpr wchar_t kSlotClickEventPrefix[] = L"Local\\HancomLiveBridgeSlotClick.";

// Slot AID pool size. Fixed forever -- the pool is advertised by EnumAction, and
// growing it means another DLL version bump. Mirrors
// hwp_custom_action_store.CUSTOM_ACTION_SLOT_AIDS.
constexpr LONG kSlotCount = 32;
// Clicks are consumed by a worker in another process. The ring only has to
// cover the gap between a click and the worker's next wake-up.
constexpr LONG kClickRingCapacity = 16;

// Heartbeat freshness window. The worker declares its own window (it knows its
// beat interval); we clamp whatever it declares and fall back to the default
// when it declares nothing.
constexpr DWORD kDefaultHeartbeatStaleMilliseconds = 6000;
constexpr DWORD kMinimumHeartbeatStaleMilliseconds = 1000;
constexpr DWORD kMaximumHeartbeatStaleMilliseconds = 60000;

// UpdateUI's `*state` bit meanings are **not measured** -- no Hancom header for
// IHncUserActionModule exists in this repository or in the installed product.
// 0 is the known-good value (the three lifecycle actions have always returned
// it). So the enabled path stays byte-identical to what shipped, and only the
// disabled path guesses. The guess is fail-safe: if bit 0 does not mean
// "greyed out", the worst case is a button that looks live but does nothing --
// which is exactly what it did before this change, because a dead worker
// consumes no clicks.
constexpr UINT kSlotStateEnabled = 0;
constexpr UINT kSlotStateDisabled = 1;

struct SlotClickRecord {
    volatile LONG sequence;
    volatile LONG slotIndex;
    volatile LONG tick;
};

struct Snapshot {
    // --- version 1: offsets frozen ---
    DWORD magic;
    DWORD version;
    DWORD size;
    DWORD processId;
    volatile LONG sequence;
    volatile LONG queryCount;
    volatile LONG enumCount;
    volatile LONG updateUiCount;
    volatile LONG doActionCount;
    volatile LONG publishCount;
    volatile LONG publishSuccessCount;
    volatile LONG revokeCount;
    volatile LONG lastResult;
    volatile LONG rawCookie;
    volatile LONG batchCookie;
    char lastAction[64];
    // --- version 2: appended ---
    volatile LONG slotCount;
    volatile LONG slotClickCount;
    volatile LONG lastSlotIndex;
    volatile LONG lastSlotSequence;
    // Written by the worker, read by this DLL. Everything above is the other
    // way round.
    volatile LONG heartbeatWorkerPid;
    // GetTickCount() truncated to 32 bits on purpose: a 32-bit aligned store is
    // atomic on both sides, and unsigned wrap-around subtraction stays correct
    // for any interval shorter than 49.7 days. A 64-bit tick would tear when
    // this 32-bit DLL reads what a 64-bit worker wrote.
    volatile LONG heartbeatTick;
    volatile LONG heartbeatStaleAfterMs;
    // What UpdateUI last decided. Diagnostics only -- it lets a live check see
    // the decision even when the visual result is unreadable.
    volatile LONG slotUiEnabled;
    volatile LONG clickRingCapacity;
    SlotClickRecord clickRing[kClickRingCapacity];
    volatile LONG reserved[8];
};

// The version-1 offsets are load-bearing: the Stage 0/Stage 1 readers hard-code
// them (doActionCount at 32, lastAction at 60, total 124).
static_assert(offsetof(Snapshot, doActionCount) == 32);
static_assert(offsetof(Snapshot, lastAction) == 60);
static_assert(offsetof(Snapshot, slotCount) == kVersion1Size);
static_assert(offsetof(Snapshot, slotClickCount) == 128);
static_assert(offsetof(Snapshot, lastSlotIndex) == 132);
static_assert(offsetof(Snapshot, lastSlotSequence) == 136);
static_assert(offsetof(Snapshot, heartbeatWorkerPid) == 140);
static_assert(offsetof(Snapshot, heartbeatTick) == 144);
static_assert(offsetof(Snapshot, heartbeatStaleAfterMs) == 148);
static_assert(offsetof(Snapshot, slotUiEnabled) == 152);
static_assert(offsetof(Snapshot, clickRingCapacity) == 156);
static_assert(offsetof(Snapshot, clickRing) == 160);
static_assert(sizeof(SlotClickRecord) == 12);
static_assert(offsetof(Snapshot, reserved) == 352);
static_assert(sizeof(Snapshot) == 384);

void NoteQuery() noexcept;
void NoteEnum(const char* action) noexcept;
void NoteUpdateUi(const char* action) noexcept;
void NoteDoAction(const char* action) noexcept;
void NotePublish(
    HRESULT result,
    DWORD rawCookie,
    DWORD batchCookie) noexcept;
void NoteRevoke(HRESULT result) noexcept;

// Stage 2. Records the click in the ring and wakes the worker. Called from the
// Hancom UI thread, so it does no COM and no allocation.
void NoteSlotClick(LONG slotIndex) noexcept;
// True when the worker's heartbeat is still fresh. Also records the decision in
// `slotUiEnabled`.
bool SlotsEnabled() noexcept;

}
