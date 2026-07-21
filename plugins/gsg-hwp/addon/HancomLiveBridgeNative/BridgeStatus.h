#pragma once

#include <Windows.h>

namespace bridge_status {

constexpr DWORD kMagic = 0x31534248;
constexpr DWORD kVersion = 1;
constexpr wchar_t kMappingPrefix[] = L"Local\\HancomLiveBridgeStatus.";

struct Snapshot {
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
};

void NoteQuery() noexcept;
void NoteEnum(const char* action) noexcept;
void NoteUpdateUi(const char* action) noexcept;
void NoteDoAction(const char* action) noexcept;
void NotePublish(
    HRESULT result,
    DWORD rawCookie,
    DWORD batchCookie) noexcept;
void NoteRevoke(HRESULT result) noexcept;

}
