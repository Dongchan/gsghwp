#include "HarnessCom.h"
#include "HarnessIo.h"

#include <Windows.h>

#include <iostream>
#include <string>

int wmain(const int argumentCount, wchar_t** const arguments) {
    if (argumentCount != 4) {
        std::wcerr << L"usage: HancomApiHarness.exe <request> <result> <pid>\n";
        return 2;
    }
    std::wstring request;
    if (!hancom::api_harness::ReadUtf8File(arguments[1], &request)) {
        std::wcerr << L"request file could not be read\n";
        return 3;
    }
    const hancom::api_harness::HarnessResult result =
        hancom::api_harness::RunOfficialApiCase(request, arguments[3]);
    if (FAILED(result.status)) {
        std::wcerr << L"official API case failed before a response: 0x"
                   << std::hex << static_cast<unsigned long>(result.status) << L'\n';
        return 4;
    }
    if (!hancom::api_harness::WriteUtf8File(arguments[2], result.response)) {
        std::wcerr << L"result file could not be written\n";
        return 5;
    }
    return 0;
}
