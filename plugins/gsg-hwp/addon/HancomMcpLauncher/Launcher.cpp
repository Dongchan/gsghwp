#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <tlhelp32.h>

#include <cstdio>
#include <cwchar>
#include <string>
#include <utility>
#include <vector>

namespace {

class UniqueHandle final {
public:
    UniqueHandle() noexcept = default;

    explicit UniqueHandle(HANDLE value) noexcept : value_(value) {}

    ~UniqueHandle() noexcept {
        Close();
    }

    UniqueHandle(const UniqueHandle&) = delete;
    UniqueHandle& operator=(const UniqueHandle&) = delete;

    UniqueHandle(UniqueHandle&& other) noexcept : value_(other.Release()) {}

    UniqueHandle& operator=(UniqueHandle&& other) noexcept {
        if (this != &other) {
            Close();
            value_ = other.Release();
        }
        return *this;
    }

    [[nodiscard]] HANDLE Get() const noexcept {
        return value_;
    }

    [[nodiscard]] bool IsValid() const noexcept {
        return value_ != nullptr && value_ != INVALID_HANDLE_VALUE;
    }

    [[nodiscard]] HANDLE Release() noexcept {
        const HANDLE value = value_;
        value_ = nullptr;
        return value;
    }

    void Reset(HANDLE value = nullptr) noexcept {
        Close();
        value_ = value;
    }

private:
    void Close() noexcept {
        if (IsValid()) {
            CloseHandle(value_);
        }
        value_ = nullptr;
    }

    HANDLE value_ = nullptr;
};

int Fail(const wchar_t* operation) {
    const DWORD error = GetLastError();
    std::fwprintf(stderr, L"HancomMcpLauncher: %ls failed (Win32 %lu)\n", operation, error);
    return 1;
}

bool GetTokenIntegrityRid(HANDLE token, DWORD* integrity_rid) {
    DWORD byte_count = 0;
    if (GetTokenInformation(token, TokenIntegrityLevel, nullptr, 0, &byte_count) != FALSE) {
        SetLastError(ERROR_INVALID_DATA);
        return false;
    }
    if (GetLastError() != ERROR_INSUFFICIENT_BUFFER || byte_count == 0) {
        return false;
    }

    std::vector<BYTE> buffer(byte_count);
    if (GetTokenInformation(
            token,
            TokenIntegrityLevel,
            buffer.data(),
            byte_count,
            &byte_count) == FALSE) {
        return false;
    }

    const auto* label = reinterpret_cast<const TOKEN_MANDATORY_LABEL*>(buffer.data());
    const PSID sid = label->Label.Sid;
    if (IsValidSid(sid) == FALSE) {
        SetLastError(ERROR_INVALID_SID);
        return false;
    }

    const UCHAR sub_authority_count = *GetSidSubAuthorityCount(sid);
    if (sub_authority_count == 0) {
        SetLastError(ERROR_INVALID_SID);
        return false;
    }

    *integrity_rid = *GetSidSubAuthority(
        sid,
        static_cast<DWORD>(sub_authority_count - 1));
    return true;
}

bool FindDesktopToken(DWORD session_id, UniqueHandle* token, DWORD* integrity_rid) {
    UniqueHandle snapshot(CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0));
    if (!snapshot.IsValid()) {
        return false;
    }

    PROCESSENTRY32W entry{};
    entry.dwSize = static_cast<DWORD>(sizeof(entry));
    if (Process32FirstW(snapshot.Get(), &entry) == FALSE) {
        return false;
    }

    do {
        if (_wcsicmp(entry.szExeFile, L"explorer.exe") != 0) {
            continue;
        }

        DWORD candidate_session_id = 0;
        if (ProcessIdToSessionId(entry.th32ProcessID, &candidate_session_id) == FALSE ||
            candidate_session_id != session_id) {
            continue;
        }

        UniqueHandle process(OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION,
            FALSE,
            entry.th32ProcessID));
        if (!process.IsValid()) {
            continue;
        }

        HANDLE raw_token = nullptr;
        if (OpenProcessToken(
                process.Get(),
                TOKEN_QUERY | TOKEN_DUPLICATE | TOKEN_ASSIGN_PRIMARY,
                &raw_token) == FALSE) {
            continue;
        }
        UniqueHandle candidate_token(raw_token);

        DWORD candidate_integrity_rid = 0;
        if (!GetTokenIntegrityRid(candidate_token.Get(), &candidate_integrity_rid)) {
            continue;
        }

        HANDLE raw_primary_token = nullptr;
        if (DuplicateTokenEx(
                candidate_token.Get(),
                MAXIMUM_ALLOWED,
                nullptr,
                SecurityImpersonation,
                TokenPrimary,
                &raw_primary_token) == FALSE) {
            continue;
        }

        token->Reset(raw_primary_token);
        *integrity_rid = candidate_integrity_rid;
        return true;
    } while (Process32NextW(snapshot.Get(), &entry) != FALSE);

    SetLastError(ERROR_NOT_FOUND);
    return false;
}

bool ResolveExecutable(const wchar_t* executable, std::wstring* resolved) {
    const DWORD required = SearchPathW(nullptr, executable, L".exe", 0, nullptr, nullptr);
    if (required == 0) {
        return false;
    }

    std::vector<wchar_t> buffer(required);
    const DWORD written = SearchPathW(
        nullptr,
        executable,
        L".exe",
        required,
        buffer.data(),
        nullptr);
    if (written == 0 || written >= required) {
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return false;
    }

    *resolved = std::wstring(buffer.data(), written);
    return true;
}

std::wstring QuoteArgument(const std::wstring& argument) {
    if (!argument.empty() && argument.find_first_of(L" \t\n\v\"") == std::wstring::npos) {
        return argument;
    }

    std::wstring quoted(1, L'"');
    std::size_t backslash_count = 0;
    for (const wchar_t character : argument) {
        if (character == L'\\') {
            ++backslash_count;
            continue;
        }
        if (character == L'"') {
            quoted.append(backslash_count * 2 + 1, L'\\');
            quoted.push_back(character);
            backslash_count = 0;
            continue;
        }
        quoted.append(backslash_count, L'\\');
        backslash_count = 0;
        quoted.push_back(character);
    }
    quoted.append(backslash_count * 2, L'\\');
    quoted.push_back(L'"');
    return quoted;
}

std::wstring BuildCommandLine(
    const std::wstring& executable,
    int argument_count,
    wchar_t* arguments[]) {
    std::wstring command_line = QuoteArgument(executable);
    for (int index = 2; index < argument_count; ++index) {
        command_line.push_back(L' ');
        command_line.append(QuoteArgument(arguments[index]));
    }
    return command_line;
}

bool StartChild(
    const std::wstring& executable,
    std::wstring* command_line,
    const std::wstring& current_directory,
    HANDLE desktop_token,
    PROCESS_INFORMATION* process_information) {
    STARTUPINFOW startup_information{};
    startup_information.cb = static_cast<DWORD>(sizeof(startup_information));
    startup_information.dwFlags = STARTF_USESTDHANDLES;
    startup_information.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    startup_information.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
    startup_information.hStdError = GetStdHandle(STD_ERROR_HANDLE);

    constexpr DWORD creation_flags = CREATE_NO_WINDOW | CREATE_SUSPENDED;
    if (desktop_token == nullptr) {
        return CreateProcessW(
                   executable.c_str(),
                   command_line->data(),
                   nullptr,
                   nullptr,
                   TRUE,
                   creation_flags,
                   nullptr,
                   current_directory.c_str(),
                   &startup_information,
                   process_information) != FALSE;
    }

    return CreateProcessWithTokenW(
               desktop_token,
               0,
               executable.c_str(),
               command_line->data(),
               creation_flags,
               nullptr,
               current_directory.c_str(),
               &startup_information,
               process_information) != FALSE;
}

bool ConfigureKillOnCloseJob(HANDLE job) {
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION information{};
    information.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    return SetInformationJobObject(
               job,
               JobObjectExtendedLimitInformation,
               &information,
               sizeof(information)) != FALSE;
}

}

int wmain(int argc, wchar_t* argv[]) {
    if (argc < 2) {
        std::fwprintf(stderr, L"Usage: HancomMcpLauncher.exe <program> [arguments...]\n");
        return 2;
    }

    std::wstring executable;
    if (!ResolveExecutable(argv[1], &executable)) {
        return Fail(L"SearchPathW");
    }

    DWORD session_id = 0;
    if (ProcessIdToSessionId(GetCurrentProcessId(), &session_id) == FALSE) {
        return Fail(L"ProcessIdToSessionId");
    }

    HANDLE raw_current_token = nullptr;
    if (OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &raw_current_token) == FALSE) {
        return Fail(L"OpenProcessToken(current)");
    }
    UniqueHandle current_token(raw_current_token);

    DWORD current_integrity_rid = 0;
    if (!GetTokenIntegrityRid(current_token.Get(), &current_integrity_rid)) {
        return Fail(L"GetTokenInformation(current)");
    }

    UniqueHandle desktop_token;
    DWORD desktop_integrity_rid = 0;
    if (!FindDesktopToken(session_id, &desktop_token, &desktop_integrity_rid)) {
        return Fail(L"FindDesktopToken");
    }

    UniqueHandle job(CreateJobObjectW(nullptr, nullptr));
    if (!job.IsValid()) {
        return Fail(L"CreateJobObjectW");
    }
    if (!ConfigureKillOnCloseJob(job.Get())) {
        return Fail(L"SetInformationJobObject");
    }

    std::wstring command_line = BuildCommandLine(executable, argc, argv);
    const DWORD current_directory_length = GetCurrentDirectoryW(0, nullptr);
    if (current_directory_length == 0) {
        return Fail(L"GetCurrentDirectoryW");
    }
    std::vector<wchar_t> current_directory_buffer(current_directory_length);
    const DWORD current_directory_written = GetCurrentDirectoryW(
        current_directory_length,
        current_directory_buffer.data());
    if (current_directory_written == 0 ||
        current_directory_written >= current_directory_length) {
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return Fail(L"GetCurrentDirectoryW");
    }
    const std::wstring current_directory(
        current_directory_buffer.data(),
        current_directory_written);
    PROCESS_INFORMATION process_information{};
    const HANDLE launch_token = current_integrity_rid == desktop_integrity_rid
        ? nullptr
        : desktop_token.Get();
    if (!StartChild(
            executable,
            &command_line,
            current_directory,
            launch_token,
            &process_information)) {
        return Fail(L"CreateProcess");
    }

    UniqueHandle child_process(process_information.hProcess);
    UniqueHandle child_thread(process_information.hThread);
    if (AssignProcessToJobObject(job.Get(), child_process.Get()) == FALSE) {
        const DWORD error = GetLastError();
        TerminateProcess(child_process.Get(), error);
        WaitForSingleObject(child_process.Get(), INFINITE);
        SetLastError(error);
        return Fail(L"AssignProcessToJobObject");
    }
    if (ResumeThread(child_thread.Get()) == static_cast<DWORD>(-1)) {
        const DWORD error = GetLastError();
        TerminateProcess(child_process.Get(), error);
        WaitForSingleObject(child_process.Get(), INFINITE);
        SetLastError(error);
        return Fail(L"ResumeThread");
    }

    if (WaitForSingleObject(child_process.Get(), INFINITE) != WAIT_OBJECT_0) {
        return Fail(L"WaitForSingleObject");
    }

    DWORD child_exit_code = 1;
    if (GetExitCodeProcess(child_process.Get(), &child_exit_code) == FALSE) {
        return Fail(L"GetExitCodeProcess");
    }
    return static_cast<int>(child_exit_code);
}
