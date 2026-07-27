#include "ComError.h"

#include <sstream>

namespace hancom::com {

std::wstring FormatHResult(const wchar_t* const operation, const HRESULT status) {
    std::wostringstream text;
    text << operation << L" failed (HRESULT 0x" << std::hex
         << static_cast<unsigned long>(status) << L')';
    return text.str();
}

}
