#pragma once

#include <Windows.h>
#include <oaidl.h>

namespace hancom::formatting {

struct ParagraphFormat {
    LONG styleId = -1;
    LONG alignment = 0;
    LONG lineSpacing = 0;
    LONG leftMargin = 0;
    LONG rightMargin = 0;
    LONG indentation = 0;
    LONG previousSpacing = 0;
    LONG nextSpacing = 0;

    bool operator==(const ParagraphFormat& other) const noexcept;
};

HRESULT ReadParagraphFormat(
    IDispatch* hwp,
    ParagraphFormat* format) noexcept;

HRESULT ApplyParagraphFormat(
    IDispatch* hwp,
    const ParagraphFormat& format) noexcept;

}
