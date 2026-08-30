#include "ActionExecutorInternal.h"

#include <wincodec.h>

#include <algorithm>
#include <cmath>
#include <string>

#pragma comment(lib, "windowscodecs.lib")

namespace hancom::actions::detail {

bool ReadImagePixelSize(
    const std::wstring& path,
    UINT* const width,
    UINT* const height,
    ExecutionResult* const result) {
    CComPtr<IWICImagingFactory> factory;
    HRESULT status = factory.CoCreateInstance(CLSID_WICImagingFactory);
    CComPtr<IWICBitmapDecoder> decoder;
    if (SUCCEEDED(status)) {
        status = factory->CreateDecoderFromFilename(
            path.c_str(),
            nullptr,
            GENERIC_READ,
            WICDecodeMetadataCacheOnDemand,
            &decoder);
    }
    CComPtr<IWICBitmapFrameDecode> frame;
    if (SUCCEEDED(status)) {
        status = decoder->GetFrame(0, &frame);
    }
    if (SUCCEEDED(status)) {
        status = frame->GetSize(width, height);
    }
    if (FAILED(status)) {
        return SetError(result, L"IMAGE_SIZE", path, FormatHResult(L"read image size", status));
    }
    if (*width == 0 || *height == 0) {
        return SetError(result, L"IMAGE_SIZE", path, L"image has a zero pixel dimension");
    }

    CComPtr<IWICMetadataQueryReader> metadata;
    PROPVARIANT orientation;
    PropVariantInit(&orientation);
    unsigned long value = 1;
    if (SUCCEEDED(frame->GetMetadataQueryReader(&metadata)) &&
        SUCCEEDED(metadata->GetMetadataByName(L"/app1/ifd/{ushort=274}", &orientation))) {
        if (orientation.vt == VT_UI2) {
            value = orientation.uiVal;
        } else if (orientation.vt == VT_UI4) {
            value = orientation.ulVal;
        }
    }
    PropVariantClear(&orientation);
    if (value >= 5 && value <= 8) {
        std::swap(*width, *height);
    }
    return true;
}

// Only the part Hancom still shows is fitted into the box. With no crop the
// visible part is the whole file, which is the letterboxing behaviour every
// caller had before crop existed.
bool FitImageInBox(
    const std::wstring& path,
    const double boxWidthMm,
    const double boxHeightMm,
    const PictureCrop& crop,
    double* const widthMm,
    double* const heightMm,
    ExecutionResult* const result) {
    UINT pixelWidth = 0;
    UINT pixelHeight = 0;
    if (!ReadImagePixelSize(path, &pixelWidth, &pixelHeight, result)) {
        return false;
    }
    const double visibleWidth =
        static_cast<double>(pixelWidth) * (1.0 - crop.left - crop.right);
    const double visibleHeight =
        static_cast<double>(pixelHeight) * (1.0 - crop.top - crop.bottom);
    if (visibleWidth <= 0.0 || visibleHeight <= 0.0) {
        return SetError(result, L"IMAGE_SIZE", path, L"image crop leaves nothing visible");
    }
    const double scale = (std::min)(boxWidthMm / visibleWidth, boxHeightMm / visibleHeight);
    *widthMm = visibleWidth * scale;
    *heightMm = visibleHeight * scale;
    return std::isfinite(*widthMm) && std::isfinite(*heightMm) &&
        *widthMm > 0.0 && *heightMm > 0.0;
}

bool ReadOptionalItemLong(
    IDispatch* const set,
    const wchar_t* const name,
    LONG* const value) {
    CComVariant raw;
    HRESULT status = Method(set, L"Item", {CComVariant(name)}, &raw);
    if (SUCCEEDED(status)) {
        status = AsLong(raw, value);
    }
    return SUCCEEDED(status);
}

LONG CropSkipUnits(const double fraction, const LONG original) {
    if (fraction <= 0.0 || original <= 0) {
        return 0;
    }
    const double units = std::floor(fraction * static_cast<double>(original));
    if (units <= 0.0) {
        return 0;
    }
    const double limit = static_cast<double>(original);
    return static_cast<LONG>(units < limit ? units : limit);
}

// Hancom's own crop, applied to the picture control that was just inserted.
// DrawImageAttr (ParameterSetTable_2504.pdf p41-42) carries both the trim
// amounts SkipLeft/SkipTop/SkipRight/SkipBottom and OriginalSizeX/OriginalSizeY
// for the same source image. The file on disk is only read, never rewritten.
//
// Two assumptions stand behind this, and neither is proven in this repository.
//   Unit -- the catalogue does not state what Skip* counts in. This scales the
//     caller's unitless fractions by OriginalSizeX/Y read from the same set at
//     run time, which assumes the two live in one coordinate space.
//   Propagation -- SetItem here writes into the nested ShapeDrawImageAttr set
//     obtained from the control's Properties, and the caller then puts
//     Properties back on the control. That assumes the nested set is the
//     parent's own storage rather than a copy. The only other use of
//     ShapeDrawImageAttr in this repository is read-only (BatchExecutor.cpp,
//     telling a picture from any other gso), so there is no write precedent
//     here to lean on.
//
// How each assumption fails is not the same, and only one is caught. If this
// build hands back no ShapeDrawImageAttr, no OriginalSizeX/Y, or refuses the
// SetItem, false is returned: the caller then sizes the picture the way it
// always did and verify_inserted_picture measures that uncropped box and
// reports cropped=false. If instead SetItem succeeds but does not reach the
// control, this returns true, the box is sized for the surviving part, and the
// size-based verification cannot tell that apart from a crop that landed. Live
// confirmation is what settles the propagation assumption; nothing here does.
bool TryApplyPictureCrop(IDispatch* const properties, const PictureCrop& crop) {
    CComVariant rawAttributes;
    HRESULT status = Method(
        properties,
        L"Item",
        {CComVariant(L"ShapeDrawImageAttr")},
        &rawAttributes);
    if (FAILED(status)) {
        return false;
    }
    CComPtr<IDispatch> imageAttributes;
    status = AsDispatch(rawAttributes, imageAttributes);
    if (FAILED(status) || imageAttributes == nullptr) {
        return false;
    }
    LONG originalWidth = 0;
    LONG originalHeight = 0;
    if (!ReadOptionalItemLong(imageAttributes, L"OriginalSizeX", &originalWidth) ||
        !ReadOptionalItemLong(imageAttributes, L"OriginalSizeY", &originalHeight) ||
        originalWidth <= 0 || originalHeight <= 0) {
        return false;
    }
    const std::pair<const wchar_t*, LONG> skips[] = {
        {L"SkipLeft", CropSkipUnits(crop.left, originalWidth)},
        {L"SkipRight", CropSkipUnits(crop.right, originalWidth)},
        {L"SkipTop", CropSkipUnits(crop.top, originalHeight)},
        {L"SkipBottom", CropSkipUnits(crop.bottom, originalHeight)},
    };
    CComVariant ignored;
    for (const auto& [name, value] : skips) {
        status = Method(
            imageAttributes,
            L"SetItem",
            {CComVariant(name), CComVariant(value)},
            &ignored);
        if (FAILED(status)) {
            return false;
        }
    }
    return true;
}

bool ConfigureImageCell(Context* const context, const std::wstring& location) {
    CComPtr<IDispatch> parameterSets;
    CComPtr<IDispatch> shape;
    CComPtr<IDispatch> shapeSet;
    CComPtr<IDispatch> cell;
    if (!GetDispatchProperty(context->hwp, L"HParameterSet", parameterSets, context->result, location) ||
        !GetDispatchProperty(parameterSets, L"HShapeObject", shape, context->result, location) ||
        !GetDispatchProperty(shape, L"HSet", shapeSet, context->result, location)) {
        return false;
    }
    CComVariant ignored;
    HRESULT status = Method(
        context->action,
        L"GetDefault",
        {CComVariant(L"TablePropertyDialog"), CComVariant(shapeSet)},
        &ignored);
    if (FAILED(status) ||
        !PutItemOrProperty(shapeSet, L"ShapeType", CComVariant(3L), context->result, location) ||
        !PutItemOrProperty(shapeSet, L"ShapeCellSize", CComVariant(0L), context->result, location) ||
        !GetDispatchProperty(shape, L"ShapeTableCell", cell, context->result, location)) {
        return false;
    }
    const std::pair<const wchar_t*, LONG> margins[] = {
        {L"HasMargin", 1L},
        {L"MarginLeft", 0L},
        {L"MarginRight", 0L},
        {L"MarginTop", 0L},
        {L"MarginBottom", 0L},
    };
    for (const auto& [name, value] : margins) {
        status = PropertyPut(cell, name, CComVariant(value));
        if (FAILED(status)) {
            return SetError(context->result, L"IMAGE_CELL_FORMAT", location, FormatHResult(name, status));
        }
    }
    bool executed = false;
    if (!CallBooleanMethod(
            context->action,
            L"Execute",
            {CComVariant(L"TablePropertyDialog"), CComVariant(shapeSet)},
            &executed,
            context->result,
            location) ||
        !executed) {
        return executed ? false : SetError(
            context->result,
            L"IMAGE_CELL_FORMAT",
            location,
            L"TablePropertyDialog returned false");
    }
    CComPtr<IDispatch> paragraph;
    CComPtr<IDispatch> paragraphSet;
    if (!GetDispatchProperty(parameterSets, L"HParaShape", paragraph, context->result, location) ||
        !GetDispatchProperty(paragraph, L"HSet", paragraphSet, context->result, location)) {
        return false;
    }
    status = Method(
        context->action,
        L"GetDefault",
        {CComVariant(L"ParagraphShape"), CComVariant(paragraphSet)},
        &ignored);
    if (FAILED(status)) {
        return SetError(context->result, L"IMAGE_CELL_FORMAT", location, FormatHResult(L"GetDefault", status));
    }
    const wchar_t* const fields[] = {
        L"LeftMargin",
        L"RightMargin",
        L"Indentation",
        L"PrevSpacing",
        L"NextSpacing",
    };
    for (const wchar_t* const field : fields) {
        status = PropertyPut(paragraph, field, CComVariant(0L));
        if (FAILED(status)) {
            return SetError(context->result, L"IMAGE_CELL_FORMAT", location, FormatHResult(field, status));
        }
    }
    if (!CallBooleanMethod(
            context->action,
            L"Execute",
            {CComVariant(L"ParagraphShape"), CComVariant(paragraphSet)},
            &executed,
            context->result,
            location) ||
        !executed) {
        return executed ? false : SetError(
            context->result,
            L"IMAGE_CELL_FORMAT",
            location,
            L"ParagraphShape returned false");
    }
    return true;
}

bool InsertPicture(Context* const context, const Command& command) {
    const std::wstring& path = command.first;
    const std::wstring address = GetCellAddress(context->hwp);
    const bool inCell = !address.empty();
    const std::wstring location = inCell ? address : path;
    if (inCell && !ConfigureImageCell(context, location)) {
        return false;
    }
    CComVariant raw;
    HRESULT status = Method(
        context->hwp,
        L"InsertPicture",
        {
            CComVariant(path.c_str()),
            BooleanVariant(true),
            CComVariant(3L),
            BooleanVariant(false),
            BooleanVariant(false),
            CComVariant(0L),
            CComVariant(0L),
            CComVariant(0L),
        },
        &raw);
    CComPtr<IDispatch> picture;
    if (SUCCEEDED(status)) {
        status = AsDispatch(raw, picture);
    }
    if (FAILED(status)) {
        return SetError(context->result, L"INSERT_IMAGE", location, FormatHResult(L"InsertPicture", status));
    }
    CComPtr<IDispatch> properties;
    if (!GetDispatchProperty(picture, L"Properties", properties, context->result, location)) {
        return false;
    }
    CComVariant ignored;
    // Crop first, size second: the object rectangle has to describe the part
    // that survived the crop, or Hancom stretches what is left to fill it.
    const PictureCrop crop = command.hasPictureCrop &&
            TryApplyPictureCrop(properties, command.pictureCrop)
        ? command.pictureCrop
        : PictureCrop{};
    if (command.hasPictureBox) {
        double widthMm = 0.0;
        double heightMm = 0.0;
        if (!FitImageInBox(
                path,
                command.pictureWidthMm,
                command.pictureHeightMm,
                crop,
                &widthMm,
                &heightMm,
                context->result)) {
            return false;
        }
        Value widthValue;
        widthValue.kind = ValueKind::Millimeter;
        widthValue.millimeter = widthMm;
        Value heightValue;
        heightValue.kind = ValueKind::Millimeter;
        heightValue.millimeter = heightMm;
        CComVariant width;
        CComVariant height;
        if (!ConvertValue(context->hwp, widthValue, &width, context->result, location) ||
            !ConvertValue(context->hwp, heightValue, &height, context->result, location)) {
            return false;
        }
        status = Method(
            properties,
            L"SetItem",
            {CComVariant(L"Width"), width},
            &ignored);
        if (SUCCEEDED(status)) {
            status = Method(
                properties,
                L"SetItem",
                {CComVariant(L"Height"), height},
                &ignored);
        }
    }
    if (SUCCEEDED(status)) {
        status = Method(
            properties,
            L"SetItem",
            {CComVariant(L"TreatAsChar"), BooleanVariant(true)},
            &ignored);
    }
    if (SUCCEEDED(status)) {
        status = PropertyPut(picture, L"Properties", CComVariant(properties));
    }
    if (FAILED(status)) {
        return SetError(context->result, L"INSERT_IMAGE", location, FormatHResult(L"TreatAsChar", status));
    }
    ++context->result->imageInsertions;
    return true;
}

bool ApplyStyle(Context* const context, const LONG styleId, const std::wstring& location) {
    CComPtr<IDispatch> parameterSets;
    CComPtr<IDispatch> style;
    CComPtr<IDispatch> set;
    if (!GetDispatchProperty(context->hwp, L"HParameterSet", parameterSets, context->result, location) ||
        !GetDispatchProperty(parameterSets, L"HStyle", style, context->result, location) ||
        !GetDispatchProperty(style, L"HSet", set, context->result, location)) {
        return false;
    }
    CComVariant ignored;
    HRESULT status = Method(
        context->action,
        L"GetDefault",
        {CComVariant(L"Style"), CComVariant(set)},
        &ignored);
    if (SUCCEEDED(status)) {
        status = PropertyPut(style, L"Apply", CComVariant(styleId));
    }
    if (FAILED(status)) {
        return SetError(context->result, L"STYLE", location, FormatHResult(L"Style", status));
    }
    bool executed = false;
    return CallBooleanMethod(
               context->action,
               L"Execute",
               {CComVariant(L"Style"), CComVariant(set)},
               &executed,
               context->result,
               location) &&
        (executed || SetError(context->result, L"STYLE", location, L"Style returned false"));
}

Setter IntegerSetter(const std::wstring& path, const LONG value) {
    Setter setter;
    setter.path = path;
    setter.value.kind = ValueKind::Integer;
    setter.value.integer = value;
    return setter;
}

Setter BooleanSetter(const std::wstring& path, const bool value) {
    Setter setter;
    setter.path = path;
    setter.value.kind = ValueKind::Boolean;
    setter.value.boolean = value;
    return setter;
}

Setter TextSetter(const std::wstring& path, const std::wstring& value) {
    Setter setter;
    setter.path = path;
    setter.value.kind = ValueKind::Text;
    setter.value.text = value;
    return setter;
}

bool ApplyCaptionFormat(Context* const context, const Command& caption) {
    Command character;
    character.kind = CommandKind::Action;
    character.name = L"CharShape";
    character.parameterSet = L"HCharShape";
    character.setters = {
        BooleanSetter(L"Bold", caption.captionBold),
        IntegerSetter(L"Height", caption.captionHeight),
        IntegerSetter(L"TextColor", caption.captionTextColor),
    };
    const wchar_t* const languages[] = {
        L"Hangul",
        L"Latin",
        L"Hanja",
        L"Japanese",
        L"Other",
        L"Symbol",
        L"User",
    };
    for (const wchar_t* const language : languages) {
        character.setters.push_back(
            TextSetter(L"FaceName" + std::wstring(language), caption.captionFaceName));
        character.setters.push_back(
            IntegerSetter(L"FontType" + std::wstring(language), 1L));
    }
    if (!ExecuteParameterAction(context, character)) {
        return false;
    }

    Command paragraph;
    paragraph.kind = CommandKind::Action;
    paragraph.name = L"ParagraphShape";
    paragraph.parameterSet = L"HParaShape";
    paragraph.setters = {
        IntegerSetter(L"AlignType", caption.captionAlignment),
        IntegerSetter(L"LineSpacing", caption.captionLineSpacing),
        IntegerSetter(L"LeftMargin", caption.captionLeftMargin),
        IntegerSetter(L"RightMargin", caption.captionRightMargin),
        IntegerSetter(L"Indentation", caption.captionIndentation),
        IntegerSetter(L"PrevSpacing", caption.captionPreviousSpacing),
        IntegerSetter(L"NextSpacing", caption.captionNextSpacing),
    };
    return ExecuteParameterAction(context, paragraph);
}

bool CopyPasteCaptionFormat(Context* const context) {
    Command shape;
    shape.kind = CommandKind::Action;
    shape.name = L"ShapeCopyPaste";
    shape.parameterSet = L"HShapeCopyPaste";
    shape.setters = {IntegerSetter(L"Type", 2L)};
    return ExecuteParameterAction(context, shape);
}

bool AttachCaption(Context* const context, const Command& command) {
    if (context->table == nullptr || context->tableId.empty()) {
        return SetError(context->result, L"NO_TABLE", L"caption", L"current table is unavailable");
    }
    if (command.hasCaptionFormatSource &&
        (!SetPosition(
             context->hwp,
             Position{
                 command.captionFormatSourceList,
                 command.captionFormatSourceParagraph,
                 command.captionFormatSourceCharacter,
             },
             context->result,
             L"caption format source") ||
         !CopyPasteCaptionFormat(context))) {
        return false;
    }
    if (!SelectControl(context, context->tableId)) {
        return false;
    }
    bool detached = false;
    if (!CallBooleanMethod(
            context->action,
            L"Run",
            {CComVariant(L"ShapeObjDetachCaption")},
            &detached,
            context->result,
            L"caption reset")) {
        return false;
    }
    if (detached) {
        ++context->result->actionsExecuted;
    }
    if (!SelectControl(context, context->tableId) ||
        !RunAction(context->action, L"ShapeObjAttachCaption", context->result, L"caption") ||
        !RunAction(context->action, L"MoveParaEnd", context->result, L"caption") ||
        !InsertText(context, command.first, L"caption")) {
        return false;
    }
    if (!RunAction(context->action, L"SelectAll", context->result, L"caption") ||
        !ApplyStyle(context, command.styleId, L"caption")) {
        return false;
    }
    if (command.hasCaptionFormatSource || command.hasCaptionFormat) {
        if (command.hasCaptionFormatSource) {
            if (!CopyPasteCaptionFormat(context)) {
                return false;
            }
        } else if (!ApplyCaptionFormat(context, command)) {
            return false;
        }
    }
    if (!RunAction(context->action, L"CloseEx", context->result, L"caption")) {
        return false;
    }
    return SelectControl(context, context->tableId);
}

bool AnchorPosition(
    IDispatch* const control,
    Position* const position,
    ExecutionResult* const result) {
    CComVariant raw;
    CComPtr<IDispatch> anchor;
    HRESULT status = Method(control, L"GetAnchorPos", {CComVariant(0L)}, &raw);
    if (SUCCEEDED(status)) {
        status = AsDispatch(raw, anchor);
    }
    if (FAILED(status)) {
        return SetError(result, L"TABLE_ANCHOR", L"", FormatHResult(L"GetAnchorPos", status));
    }
    return ItemLong(anchor, L"List", &position->list, result) &&
        ItemLong(anchor, L"Para", &position->paragraph, result) &&
        ItemLong(anchor, L"Pos", &position->character, result);
}

bool LeaveTable(Context* const context, const bool appendParagraph) {
    if (context->table == nullptr) {
        return SetError(context->result, L"NO_TABLE", L"", L"current table is unavailable");
    }
    Position anchor;
    if (!AnchorPosition(context->table, &anchor, context->result)) {
        return false;
    }
    for (size_t attempt = 0; attempt < 16; ++attempt) {
        Position current;
        if (!GetPosition(context->hwp, &current, context->result)) {
            return false;
        }
        if (current.list == anchor.list) {
            break;
        }
        if (!RunAction(context->action, L"MoveParentList", context->result, L"table")) {
            return false;
        }
    }
    Position current;
    if (!GetPosition(context->hwp, &current, context->result) || current.list != anchor.list) {
        return SetError(context->result, L"TABLE_PARENT", L"", L"table parent list was not reached");
    }
    if (!SetPosition(
            context->hwp,
            Position{anchor.list, anchor.paragraph, anchor.character + 1},
            context->result,
            L"table")) {
        return false;
    }
    if (appendParagraph &&
        !RunAction(context->action, L"BreakPara", context->result, L"table")) {
        return false;
    }
    context->table.Release();
    context->tableId.clear();
    context->topology.Clear();
    return true;
}

}
