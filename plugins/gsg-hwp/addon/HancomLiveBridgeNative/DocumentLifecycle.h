#pragma once

#include <oaidl.h>

#include <string>

namespace hancom::lifecycle {

std::wstring SaveVerify(IDispatch* hwp);
std::wstring SaveReopenVerify(IDispatch* hwp);

}
