#pragma once

#include <oaidl.h>

#include <cstdint>
#include <iosfwd>

namespace hancom::official_api {

struct DocumentState {
    LONG pageCount = -1;
    LONG modified = -1;
    LONG list = -1;
    LONG paragraph = -1;
    LONG character = -1;
    LONG controlCount = -1;
    std::uint64_t controlHash = 0;
};

struct DocumentFingerprint {
    DocumentState state;
    bool captured = false;
    std::uint64_t textHash = 0;
    std::uint64_t documentHash = 0;
    std::uint64_t textLength = 0;
    std::uint64_t documentLength = 0;
};

DocumentState CaptureDocumentState(IDispatch* hwp) noexcept;
DocumentFingerprint CaptureDocumentFingerprint(IDispatch* hwp) noexcept;
void AppendDocumentState(std::wostream& output, const DocumentState& state);

}
