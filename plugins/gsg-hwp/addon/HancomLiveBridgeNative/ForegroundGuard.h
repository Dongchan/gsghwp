#pragma once

#include <Windows.h>

#include <new>

// Keeps Hangul from jumping in front of whatever the user is doing while the
// bridge drives it.
//
// This code runs inside the Hangul process, so an activation Hangul starts is
// an in-process event and a thread local WH_CBT hook can refuse it before it
// happens. Refusing is the whole point: handing the foreground back after the
// fact still leaves a window of milliseconds in which the user's keystrokes
// land in Hangul. The hand-back below is a second layer for the paths the hook
// turns out not to cover; it is not older code being kept around, nothing in
// the addon called SetForegroundWindow before this guard existed.
//
// The guard must never stand between the user and Hangul. Every way the user
// has of asking for Hangul - clicking its window, clicking its taskbar button,
// Alt-Tab, task view - is passed through, and the pass through is recorded so
// the hand-back cannot pull back down the window the user just asked for.
//
// Everything here is Win32. COM can be parked behind a modal dialog while this
// runs, so the guard must never call into it.

namespace hancom::foreground {

// Upper bound on how long one armed scope may keep refusing activation.
// A scope leaks when the worker that opened an explicit begin/end bracket dies
// before closing it, and a leaked refusal must not lock Hangul out forever.
// Mouse driven activation is never refused, so even inside this window a click
// on Hangul always works; the deadline only bounds the programmatic case.
constexpr DWORD kBlockBudgetMilliseconds = 120000;

// How long the activation worker keeps watching after its COM call returns.
// SetActive_XHwpDocument is marshalled to the Hangul UI thread, so the window
// can come up slightly after the call that triggered it returns.
constexpr DWORD kActivationTailMilliseconds = 400;
constexpr DWORD kActivationPollMilliseconds = 20;

// Backoff between attempts to locate the Hangul frame thread. The lookup walks
// every top level window in the session, so a failed lookup must not repeat on
// every single call.
constexpr DWORD kFrameScanRetryMilliseconds = 1000;

namespace detail {

// Deadline for refusing activation, as a GetTickCount value. Compared with
// wrapped subtraction so the 49 day rollover is harmless.
inline volatile LONG gBlockDeadline = 0;

// Diagnostics only. Never consulted by the policy.
inline volatile LONG gBlockedActivations = 0;

inline bool BlockingExpired() noexcept {
    const DWORD deadline = static_cast<DWORD>(
        InterlockedCompareExchange(&gBlockDeadline, 0, 0));
    return static_cast<LONG>(deadline - GetTickCount()) <= 0;
}

inline void ExtendBlocking() noexcept {
    static_cast<void>(InterlockedExchange(
        &gBlockDeadline,
        static_cast<LONG>(GetTickCount() + kBlockBudgetMilliseconds)));
}

// True when taking the foreground would not cost anybody anything: either no
// window owns it, or Hangul already does because the user is working in it.
inline bool NothingToProtect() noexcept {
    HWND const foreground = GetForegroundWindow();
    if (foreground == nullptr) {
        return true;
    }
    DWORD processId = 0;
    static_cast<void>(GetWindowThreadProcessId(foreground, &processId));
    return processId == GetCurrentProcessId();
}

// Tick of the last activation that was let through because the user asked for
// it, plus a flag telling "never happened" apart from "happened at tick 0".
// Written by the hook, read by the hand-back.
inline volatile LONG gUserActivationTick = 0;
inline volatile LONG gUserActivationSeen = 0;

inline void NoteUserActivation() noexcept {
    static_cast<void>(InterlockedExchange(
        &gUserActivationTick,
        static_cast<LONG>(GetTickCount())));
    static_cast<void>(InterlockedExchange(&gUserActivationSeen, 1));
}

// True when the user raised Hangul at or after the given arming tick. Same
// wrapped subtraction as the deadline: correct across the GetTickCount rollover
// as long as the two ticks are less than 2^31 ms (about 24.8 days) apart, which
// is a different and smaller bound than the 49.7 day rollover period itself.
// Further apart than that and the sign flips, so this would read a stale
// activation as being in the future. gUserActivationSeen is never cleared
// either, so one activation keeps this answering for the life of the process
// once the tick comparison agrees. Both only matter for a bracket armed weeks
// after the last user activation, and a bracket lives for one call.
// Ties count as "after": if the two land on the same millisecond, leaving the
// window where the user put it is the safer answer.
inline bool UserActivationSince(const DWORD armedAt) noexcept {
    if (InterlockedCompareExchange(&gUserActivationSeen, 0, 0) == 0) {
        return false;
    }
    const DWORD noted = static_cast<DWORD>(
        InterlockedCompareExchange(&gUserActivationTick, 0, 0));
    return static_cast<LONG>(noted - armedAt) >= 0;
}

// Top level window classes the shell puts up while the user is picking a
// window: both taskbars, the taskbar thumbnail popup, and the Alt-Tab and task
// view surfaces on Windows 10 and 11.
inline bool IsShellChooserClass(HWND const window) noexcept {
    static const wchar_t* const kClasses[] = {
        L"Shell_TrayWnd",
        L"Shell_SecondaryTrayWnd",
        L"TaskListThumbnailWnd",
        L"TaskSwitcherWnd",
        L"TaskSwitcherOverlayWnd",
        L"MultitaskingViewFrame",
        L"XamlExplorerHostIslandWindow",
        L"ForegroundStaging",
    };
    wchar_t name[64]{};
    if (GetClassNameW(window, name, static_cast<int>(ARRAYSIZE(name))) == 0) {
        return false;
    }
    for (const wchar_t* const candidate : kClasses) {
        if (lstrcmpiW(name, candidate) == 0) {
            return true;
        }
    }
    return false;
}

// Those class names are not unique to the shell, so pair them with the process
// that owns the desktop window. When that cannot be resolved the class match
// has to stand on its own; erring toward letting the user through is the
// deliberate direction here.
inline bool IsShellProcessWindow(HWND const window) noexcept {
    HWND const shell = GetShellWindow();
    if (shell == nullptr) {
        return true;
    }
    DWORD shellProcess = 0;
    static_cast<void>(GetWindowThreadProcessId(shell, &shellProcess));
    if (shellProcess == 0) {
        return true;
    }
    DWORD windowProcess = 0;
    static_cast<void>(GetWindowThreadProcessId(window, &windowProcess));
    return windowProcess == shellProcess;
}

// CBTACTIVATESTRUCT::fMouse is TRUE only when the user clicked the very window
// being activated. Clicking a taskbar button, Alt-Tab and task view all arrive
// with fMouse FALSE, because the shell requests the activation on the user's
// behalf, so the click flag alone would trap the user outside Hangul for as
// long as the operation runs, and a render runs for seconds.
//
// Two Win32 signals stand in for "the shell asked on the user's behalf":
//   1. a shell chooser window owns the foreground at the moment of the hand
//      over, which is what Alt-Tab and task view look like, and what a taskbar
//      click looks like when the taskbar takes activation first; or
//   2. the cursor is over a shell chooser window with the left button still
//      physically down, which is what a taskbar button click looks like at the
//      instant the activation is requested.
// The button state is required in the second branch on purpose: without it a
// cursor merely parked over the taskbar while the user types elsewhere would
// disable the guard, which is the exact failure this whole file exists to
// prevent.
//
// NOT VERIFIED against a running Hangul. If neither signal fires, the refusal
// still expires on kBlockBudgetMilliseconds and a direct click on the Hangul
// window always works, so a miss costs the user a click, not their session.
inline bool ShellRequestedActivation() noexcept {
    HWND const foreground = GetForegroundWindow();
    if (foreground != nullptr && IsShellChooserClass(foreground) &&
        IsShellProcessWindow(foreground)) {
        return true;
    }
    if ((GetAsyncKeyState(VK_LBUTTON) & 0x8000) == 0) {
        return false;
    }
    POINT cursor{};
    if (GetCursorPos(&cursor) == FALSE) {
        return false;
    }
    HWND const under = WindowFromPoint(cursor);
    if (under == nullptr) {
        return false;
    }
    HWND const root = GetAncestor(under, GA_ROOT);
    HWND const top = root == nullptr ? under : root;
    return IsShellChooserClass(top) && IsShellProcessWindow(top);
}

struct WindowThreadSearch {
    DWORD processId;
    DWORD threadId;
};

inline BOOL CALLBACK CollectWindowThread(
    HWND const window,
    LPARAM const context) noexcept {
    WindowThreadSearch* const search =
        reinterpret_cast<WindowThreadSearch*>(context);
    DWORD processId = 0;
    const DWORD threadId = GetWindowThreadProcessId(window, &processId);
    if (threadId == 0 || processId != search->processId ||
        IsWindowVisible(window) == FALSE ||
        GetWindow(window, GW_OWNER) != nullptr) {
        return TRUE;
    }
    search->threadId = threadId;
    return FALSE;
}

// HCBT_ACTIVATE is delivered on the thread that owns the window being
// activated, which is not necessarily the STA thread the bridge is called on.
// Resolve the thread owning this process's visible top level frame once, and
// hook that thread as well. EnumWindows walks every top level window in the
// session, so the answer is cached after the first success.
inline DWORD FrameThreadId() noexcept {
    static volatile LONG cached = 0;
    static volatile LONG lastScan = 0;
    const LONG known = InterlockedCompareExchange(&cached, 0, 0);
    if (known != 0) {
        return static_cast<DWORD>(known);
    }
    // Zero means never scanned. Losing one extra scan on the millisecond where
    // GetTickCount itself reads zero is not worth guarding against.
    const DWORD now = GetTickCount();
    const DWORD previous =
        static_cast<DWORD>(InterlockedCompareExchange(&lastScan, 0, 0));
    if (previous != 0 &&
        static_cast<LONG>(now - previous) <
            static_cast<LONG>(kFrameScanRetryMilliseconds)) {
        return 0;
    }
    static_cast<void>(InterlockedExchange(&lastScan, static_cast<LONG>(now)));
    WindowThreadSearch search{GetCurrentProcessId(), 0};
    static_cast<void>(EnumWindows(
        &CollectWindowThread,
        reinterpret_cast<LPARAM>(&search)));
    if (search.threadId != 0) {
        static_cast<void>(InterlockedExchange(
            &cached,
            static_cast<LONG>(search.threadId)));
    }
    return search.threadId;
}

inline LRESULT CALLBACK CbtProc(
    const int code,
    const WPARAM wParam,
    const LPARAM lParam) noexcept {
    if (code != HCBT_ACTIVATE || BlockingExpired()) {
        return CallNextHookEx(nullptr, code, wParam, lParam);
    }
    const CBTACTIVATESTRUCT* const activation =
        reinterpret_cast<const CBTACTIVATESTRUCT*>(lParam);
    if (activation == nullptr) {
        return CallNextHookEx(nullptr, code, wParam, lParam);
    }
    // A click on Hangul is the user asking for it. Never refuse that. This is
    // also what makes a leaked scope survivable: the user can always click
    // their way back into Hangul.
    //
    // The taskbar, Alt-Tab and task view are the user asking for it just as
    // much, and they do not set fMouse. Recording either kind stops the
    // hand-back from undoing what the user just did.
    if (activation->fMouse != FALSE || ShellRequestedActivation()) {
        NoteUserActivation();
        return CallNextHookEx(nullptr, code, wParam, lParam);
    }
    HWND const target = reinterpret_cast<HWND>(wParam);
    if (target != nullptr) {
        // Dialogs and popups have an owner. If Hangul needs an answer from the
        // user, that window has to be able to take focus or the operation
        // stalls behind a prompt nobody can reach.
        if (GetWindow(target, GW_OWNER) != nullptr) {
            return CallNextHookEx(nullptr, code, wParam, lParam);
        }
        // Child windows include the MDI document views that a tab switch
        // activates. Those never take the foreground away from another
        // process, and refusing them would break switching documents.
        if ((GetWindowLongW(target, GWL_STYLE) & WS_CHILD) != 0) {
            return CallNextHookEx(nullptr, code, wParam, lParam);
        }
    }
    // Only refuse when this would take the foreground away from another
    // process. Activation inside Hangul while Hangul is already in front is
    // ordinary UI work and must pass through untouched.
    if (NothingToProtect()) {
        return CallNextHookEx(nullptr, code, wParam, lParam);
    }
    static_cast<void>(InterlockedIncrement(&gBlockedActivations));
    return 1;
}

}

// Number of activations refused since the module loaded. Diagnostic only.
inline LONG BlockedActivationCount() noexcept {
    return InterlockedCompareExchange(&detail::gBlockedActivations, 0, 0);
}

class ForegroundGuard final {
public:
    ForegroundGuard() noexcept = default;
    ForegroundGuard(const ForegroundGuard&) = delete;
    ForegroundGuard& operator=(const ForegroundGuard&) = delete;
    ForegroundGuard(ForegroundGuard&&) = delete;
    ForegroundGuard& operator=(ForegroundGuard&&) = delete;
    ~ForegroundGuard() noexcept { static_cast<void>(Release()); }

    // Starts refusing Hangul activation and remembers who owned the foreground.
    // ownerThreadId is the STA thread driving the operation; 0 means the
    // calling thread. Failing to arm is never an error: the operation must run
    // either way, focus is not a reason to refuse an edit.
    void Arm(const DWORD ownerThreadId = 0) noexcept {
        Disarm();
        HWND const foreground = GetForegroundWindow();
        if (foreground == nullptr) {
            return;                 // nobody owns it, nothing to protect
        }
        DWORD processId = 0;
        static_cast<void>(GetWindowThreadProcessId(foreground, &processId));
        if (processId == GetCurrentProcessId()) {
            return;                 // the user is working in Hangul, stay out
        }
        if (IsIconic(foreground) != FALSE ||
            IsWindowVisible(foreground) == FALSE) {
            return;                 // already lowered, not a restore target
        }
        previous_ = foreground;
        armedAt_ = GetTickCount();
        detail::ExtendBlocking();
        const DWORD owner =
            ownerThreadId == 0 ? GetCurrentThreadId() : ownerThreadId;
        hooks_[0] = SetWindowsHookExW(WH_CBT, &detail::CbtProc, nullptr, owner);
        const DWORD frame = detail::FrameThreadId();
        if (frame != 0 && frame != owner) {
            hooks_[1] =
                SetWindowsHookExW(WH_CBT, &detail::CbtProc, nullptr, frame);
        }
    }

    [[nodiscard]] bool Armed() const noexcept { return previous_ != nullptr; }

    [[nodiscard]] bool Blocking() const noexcept {
        return hooks_[0] != nullptr || hooks_[1] != nullptr;
    }

    // Drops the refusal and the pending hand-back without touching the
    // foreground. For operations that are supposed to bring Hangul forward.
    void Disarm() noexcept {
        Unhook();
        previous_ = nullptr;
        armedAt_ = 0;
    }

    // Drops the refusal, then hands the foreground back if Hangul took it
    // anyway. Returns true only when a hand-back actually happened.
    bool Release() noexcept {
        Unhook();
        HWND const previous = previous_;
        const DWORD armedAt = armedAt_;
        previous_ = nullptr;
        armedAt_ = 0;
        if (previous == nullptr) {
            return false;
        }
        if (Stale(armedAt)) {
            return false;
        }
        // Hangul may be in front because the user put it there. The hook is
        // the only place that can tell that apart from Hangul raising itself,
        // and it left a note; TryRestore has no way to work it out on its own.
        if (detail::UserActivationSince(armedAt)) {
            return false;
        }
        return TryRestore(previous) == Attempt::Restored;
    }

    // Same as Release, but keeps refusing while it waits for a late takeover.
    // A marshalled call can return before the window it triggered comes up, so
    // one check right after the call can miss it. Only safe on a worker thread
    // that is about to exit; never call this on the Hangul UI thread.
    bool ReleaseWithin(
        const DWORD budgetMilliseconds,
        const DWORD pollMilliseconds) noexcept {
        HWND const previous = previous_;
        const DWORD armedAt = armedAt_;
        if (previous == nullptr || Stale(armedAt) ||
            detail::UserActivationSince(armedAt)) {
            Disarm();
            return false;
        }
        const DWORD deadline = GetTickCount() + budgetMilliseconds;
        Attempt attempt = TryRestore(previous);
        while (attempt == Attempt::NotTaken &&
               static_cast<LONG>(deadline - GetTickCount()) > 0) {
            Sleep(pollMilliseconds);
            // The whole point of this loop is that the window can come up
            // late. So can the user's click on it; checking once at the top
            // would miss a click that lands inside the tail.
            if (detail::UserActivationSince(armedAt)) {
                break;
            }
            attempt = TryRestore(previous);
        }
        Unhook();
        previous_ = nullptr;
        armedAt_ = 0;
        return attempt == Attempt::Restored;
    }

private:
    enum class Attempt {
        NotTaken,
        Blocked,
        Restored,
    };

    // A window that closes during a long operation frees its handle for reuse,
    // and the liveness checks below cannot tell the new owner of that handle
    // from the old one. Bound the risk by refusing to act on a stale capture.
    static bool Stale(const DWORD armedAt) noexcept {
        return static_cast<LONG>(GetTickCount() - armedAt) >
            static_cast<LONG>(kBlockBudgetMilliseconds);
    }

    static Attempt TryRestore(HWND const previous) noexcept {
        HWND const current = GetForegroundWindow();
        if (current == nullptr || current == previous) {
            return Attempt::NotTaken;   // the refusal held: no z-order change
        }
        DWORD currentProcess = 0;
        static_cast<void>(GetWindowThreadProcessId(current, &currentProcess));
        if (currentProcess != GetCurrentProcessId()) {
            return Attempt::Blocked;    // a third app came up, not our business
        }
        // An owned window in front is a dialog or popup. A modal parks COM
        // waiting for the user, so pushing it behind would hide the very
        // prompt that has to be answered.
        if (GetWindow(current, GW_OWNER) != nullptr) {
            return Attempt::Blocked;
        }
        // A disabled main frame means a modal is up somewhere else.
        if (IsWindowEnabled(current) == FALSE) {
            return Attempt::Blocked;
        }
        // The original owner is gone, or the user lowered it in the meantime.
        // Doing nothing is always the right answer there.
        if (IsWindow(previous) == FALSE || IsWindowVisible(previous) == FALSE ||
            IsIconic(previous) != FALSE) {
            return Attempt::Blocked;
        }
        return SetForegroundWindow(previous) != FALSE
            ? Attempt::Restored
            : Attempt::Blocked;
    }

    void Unhook() noexcept {
        for (HHOOK& hook : hooks_) {
            if (hook != nullptr) {
                static_cast<void>(UnhookWindowsHookEx(hook));
                hook = nullptr;
            }
        }
    }

    HWND previous_ = nullptr;
    DWORD armedAt_ = 0;
    HHOOK hooks_[2] = {nullptr, nullptr};
};

namespace detail {

// One bracket per process, for the callers that drive Hangul COM from outside
// Invoke: the render loop, opening a document, and switching the active tab.
// Those call Hangul directly from Python, so the RAII scope inside Invoke
// never covers them and they have to open and close a scope explicitly.
//
// The guard cannot live on a BatchAutomation object. The bridge publishes one
// per window and one per document plus a legacy one, and the Python caller
// binds to whichever moniker its current route points at, so opening on one
// object and closing on another is normal - the document route changes in the
// middle of a tab switch, which is exactly one of the paths that needs this.
// A per object guard would leak its refusal on whichever object opened it.
//
// Allocated once and deliberately never freed. A global object with a
// destructor would run Unhook and SetForegroundWindow at DLL detach under the
// loader lock, which is not a safe place for user32 calls.
inline SRWLOCK gBracketLock = SRWLOCK_INIT;
inline ForegroundGuard* gSharedGuard = nullptr;
inline LONG gBracketDepth = 0;
inline DWORD gBracketOpenedAt = 0;

}

// Opens the shared bracket. Nested opens are counted, not stacked: only the
// outermost one arms and only the outermost close releases. The returned flag
// says whether the guard armed, which is not a success condition - not arming
// only means there was nothing in front worth protecting.
inline bool BeginSharedGuard() noexcept {
    // Everything below is a plain Win32 call on window handles. None of it
    // pumps the calling thread's message queue, so holding the lock across
    // Arm and Release cannot be reentered by another COM call on this STA.
    AcquireSRWLockExclusive(&detail::gBracketLock);
    // A caller that dies between open and close would otherwise leave the
    // depth above zero and disable the guard for the rest of the session.
    // Bound that the same way a leaked refusal is bounded.
    if (detail::gBracketDepth > 0 &&
        static_cast<LONG>(GetTickCount() - detail::gBracketOpenedAt) >
            static_cast<LONG>(kBlockBudgetMilliseconds)) {
        detail::gBracketDepth = 0;
        if (detail::gSharedGuard != nullptr) {
            detail::gSharedGuard->Disarm();
        }
    }
    bool armed = false;
    if (detail::gBracketDepth == 0) {
        if (detail::gSharedGuard == nullptr) {
            detail::gSharedGuard = new (std::nothrow) ForegroundGuard();
        }
        if (detail::gSharedGuard != nullptr) {
            detail::gSharedGuard->Arm();
            armed = detail::gSharedGuard->Armed();
        }
        detail::gBracketOpenedAt = GetTickCount();
    } else if (detail::gSharedGuard != nullptr) {
        armed = detail::gSharedGuard->Armed();
    }
    ++detail::gBracketDepth;
    ReleaseSRWLockExclusive(&detail::gBracketLock);
    return armed;
}

// Closes the shared bracket. Returns true only when the outermost close
// actually handed the foreground back. A close with nothing open is ignored:
// the bracket exists to protect focus, not to police its own callers.
inline bool EndSharedGuard() noexcept {
    AcquireSRWLockExclusive(&detail::gBracketLock);
    bool restored = false;
    if (detail::gBracketDepth > 0) {
        --detail::gBracketDepth;
        if (detail::gBracketDepth == 0 && detail::gSharedGuard != nullptr) {
            restored = detail::gSharedGuard->Release();
        }
    }
    ReleaseSRWLockExclusive(&detail::gBracketLock);
    return restored;
}

}
