#pragma once

#include <atlbase.h>
#include <atlcomcli.h>

#include <memory>
#include <string>
#include <vector>

namespace hancom::official_api {

class AutomationArgument;

class PreparedAutomationArguments final {
public:
    PreparedAutomationArguments();
    ~PreparedAutomationArguments();
    PreparedAutomationArguments(PreparedAutomationArguments&&) noexcept;
    PreparedAutomationArguments& operator=(PreparedAutomationArguments&&) noexcept;
    PreparedAutomationArguments(const PreparedAutomationArguments&) = delete;
    PreparedAutomationArguments& operator=(const PreparedAutomationArguments&) = delete;

    HRESULT Parse(
        IDispatch* hwp,
        IDispatch* target,
        const std::vector<std::wstring>& lines) noexcept;
    std::vector<CComVariant> Values() const;
    std::wstring Outputs() const;

private:
    std::vector<std::unique_ptr<AutomationArgument>> arguments_;
};

}
