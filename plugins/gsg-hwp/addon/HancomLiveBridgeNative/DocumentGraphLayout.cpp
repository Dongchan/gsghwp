#include "DocumentGraphLayout.h"

#include "DispatchInvoke.h"
#include "ComState.h"
#include "DocumentGraphCodec.h"
#include "DocumentGraphCaptureModel.h"
#include "DocumentGraphImages.h"
#include "TableInspection.h"

#include <atlbase.h>
#include <winspool.h>
#include <winver.h>

#include <algorithm>
#include <limits>
#include <new>
#include <set>
#include <utility>

namespace hancom::graph::layout {
namespace {

using hancom::dispatch::AsLong;
using hancom::dispatch::AsBool;
using hancom::dispatch::AsDispatch;
using hancom::dispatch::Method;
using hancom::dispatch::PropertyGet;

struct MemorySource final {
    const std::vector<std::uint8_t>* bytes = nullptr;
    size_t offset = 0;
};

struct NativeTableContext final {
    IDispatch* hwp = nullptr;
    std::wstring tableInstanceId{};
    const LayoutEnvironmentPlatformV1* environmentPlatform = nullptr;
};

struct StableNativeCaptureContext final {
    RecalculateCallback recalculate = nullptr;
    NativeLayoutObservationCallback observe = nullptr;
    void* callerContext = nullptr;
    const LayoutEnvironmentPlatformV1* environmentPlatform = nullptr;
};

struct FontEnumerationContext final {
    std::vector<LayoutFontTupleV1>* fonts = nullptr;
    bool failed = false;
};

constexpr size_t Index(const GeometryField field) noexcept {
    return static_cast<size_t>(field);
}

bool ReadMemory(
    void* const context,
    std::uint8_t* const buffer,
    const size_t capacity,
    size_t* const count,
    bool* const done) noexcept {
    auto* const source = static_cast<MemorySource*>(context);
    if (source == nullptr || source->bytes == nullptr || buffer == nullptr ||
        count == nullptr || done == nullptr) {
        return false;
    }
    const size_t remaining = source->bytes->size() - source->offset;
    const size_t current = (std::min)(remaining, capacity);
    if (current != 0) {
        std::copy_n(
            source->bytes->data() + source->offset,
            current,
            buffer);
    }
    source->offset += current;
    *count = current;
    *done = source->offset == source->bytes->size();
    return true;
}

void AppendByte(
    std::vector<std::uint8_t>* const output,
    const std::uint8_t value) {
    output->push_back(value);
}

void AppendUnsigned(
    std::vector<std::uint8_t>* const output,
    const std::uint64_t value) {
    for (unsigned shift = 0; shift < 64; shift += 8) {
        output->push_back(
            static_cast<std::uint8_t>((value >> shift) & 0xffU));
    }
}

void AppendSigned(
    std::vector<std::uint8_t>* const output,
    const std::int64_t value) {
    AppendUnsigned(output, static_cast<std::uint64_t>(value));
}

void Append32(
    std::vector<std::uint8_t>* const output,
    const std::uint32_t value) {
    for (unsigned shift = 0; shift < 32; shift += 8) {
        output->push_back(
            static_cast<std::uint8_t>((value >> shift) & 0xffU));
    }
}

void FrameBytes(
    std::vector<std::uint8_t>* const output,
    const std::uint8_t* const bytes,
    const size_t size) {
    AppendUnsigned(output, static_cast<std::uint64_t>(size));
    output->insert(output->end(), bytes, bytes + size);
}

void AppendText(
    std::vector<std::uint8_t>* const output,
    const std::wstring& value) {
    AppendUnsigned(output, value.size());
    for (const wchar_t codeUnit : value) {
        const std::uint16_t encoded = static_cast<std::uint16_t>(codeUnit);
        output->push_back(static_cast<std::uint8_t>(encoded & 0xffU));
        output->push_back(static_cast<std::uint8_t>(encoded >> 8));
    }
}

std::vector<std::uint8_t> Utf16Bytes(const std::wstring& value) {
    std::vector<std::uint8_t> bytes;
    bytes.reserve(value.size() * 2U);
    for (const wchar_t unit : value) {
        const auto encoded = static_cast<std::uint16_t>(unit);
        bytes.push_back(static_cast<std::uint8_t>(encoded & 0xffU));
        bytes.push_back(static_cast<std::uint8_t>(encoded >> 8));
    }
    return bytes;
}

std::vector<std::uint8_t> FontTupleBytes(
    const LayoutFontTupleV1& font) {
    std::vector<std::uint8_t> bytes = Utf16Bytes(font.faceName);
    bytes.push_back(font.charset);
    bytes.push_back(font.pitchFamily);
    Append32(&bytes, static_cast<std::uint32_t>(font.weight));
    bytes.push_back(font.italic);
    return bytes;
}

int CALLBACK CollectFont(
    const LOGFONTW* const logicalFont,
    const TEXTMETRICW*,
    DWORD,
    LPARAM rawContext) noexcept {
    auto* const context = reinterpret_cast<FontEnumerationContext*>(
        rawContext);
    if (context == nullptr || context->fonts == nullptr ||
        logicalFont == nullptr) {
        return 0;
    }
    try {
        LayoutFontTupleV1 font;
        font.faceName = logicalFont->lfFaceName;
        font.charset = logicalFont->lfCharSet;
        font.pitchFamily = logicalFont->lfPitchAndFamily;
        font.weight = logicalFont->lfWeight;
        font.italic = logicalFont->lfItalic;
        context->fonts->push_back(std::move(font));
        return 1;
    } catch (...) {
        context->failed = true;
        return 0;
    }
}

bool ReadProductionHwpFileVersion(
    void*,
    std::array<std::uint16_t, 4>* const output) noexcept {
    if (output == nullptr) {
        return false;
    }
    try {
        std::vector<wchar_t> path(32768U, L'\0');
        const DWORD pathLength = GetModuleFileNameW(
            nullptr, path.data(), static_cast<DWORD>(path.size()));
        if (pathLength == 0 || pathLength >= path.size()) {
            return false;
        }
        DWORD ignored = 0;
        const DWORD versionSize = GetFileVersionInfoSizeW(
            path.data(), &ignored);
        if (versionSize == 0) {
            return false;
        }
        std::vector<std::uint8_t> versionBytes(versionSize, 0);
        if (!GetFileVersionInfoW(
                path.data(), 0, versionSize, versionBytes.data())) {
            return false;
        }
        VS_FIXEDFILEINFO* fixed = nullptr;
        UINT fixedSize = 0;
        if (!VerQueryValueW(
                versionBytes.data(), L"\\", reinterpret_cast<void**>(&fixed),
                &fixedSize) || fixed == nullptr ||
            fixedSize < sizeof(VS_FIXEDFILEINFO) ||
            fixed->dwSignature != 0xfeef04bdU) {
            return false;
        }
        *output = {
            HIWORD(fixed->dwFileVersionMS),
            LOWORD(fixed->dwFileVersionMS),
            HIWORD(fixed->dwFileVersionLS),
            LOWORD(fixed->dwFileVersionLS),
        };
        return true;
    } catch (...) {
        return false;
    }
}

bool ReadProductionPrinterName(
    void*, std::wstring* const output) noexcept {
    if (output == nullptr) {
        return false;
    }
    try {
        DWORD characters = 0;
        SetLastError(ERROR_SUCCESS);
        GetDefaultPrinterW(nullptr, &characters);
        if (characters < 2 || GetLastError() != ERROR_INSUFFICIENT_BUFFER) {
            return false;
        }
        std::vector<wchar_t> name(characters, L'\0');
        if (!GetDefaultPrinterW(name.data(), &characters) ||
            characters < 2 || name[characters - 1] != L'\0') {
            return false;
        }
        output->assign(name.data(), characters - 1U);
        return !output->empty();
    } catch (...) {
        return false;
    }
}

bool ReadProductionPrinterDevMode(
    void*,
    const std::wstring& printerName,
    std::vector<std::uint8_t>* const output) noexcept {
    if (printerName.empty() || output == nullptr) {
        return false;
    }
    HANDLE printer = nullptr;
    if (!OpenPrinterW(
            const_cast<wchar_t*>(printerName.c_str()), &printer, nullptr)) {
        return false;
    }
    const LONG required = DocumentPropertiesW(
        nullptr, printer, const_cast<wchar_t*>(printerName.c_str()),
        nullptr, nullptr, 0);
    const std::uint64_t devmodeHeaderSize =
        FIELD_OFFSET(DEVMODEW, dmFields);
    if (required <= 0 ||
        static_cast<std::uint64_t>(required) < devmodeHeaderSize) {
        ClosePrinter(printer);
        return false;
    }
    bool success = false;
    try {
        std::vector<std::uint8_t> bytes(
            static_cast<size_t>(required), 0);
        auto* const devmode = reinterpret_cast<DEVMODEW*>(bytes.data());
        const LONG status = DocumentPropertiesW(
            nullptr, printer, const_cast<wchar_t*>(printerName.c_str()),
            devmode, nullptr, DM_OUT_BUFFER);
        const std::uint64_t exactSize = status == IDOK
            ? static_cast<std::uint64_t>(devmode->dmSize) +
                devmode->dmDriverExtra
            : 0;
        if (status == IDOK && exactSize >= devmodeHeaderSize &&
            exactSize <= bytes.size()) {
            bytes.resize(static_cast<size_t>(exactSize));
            *output = std::move(bytes);
            success = true;
        }
    } catch (...) {
        success = false;
    }
    ClosePrinter(printer);
    return success;
}

bool ReadProductionFontInventory(
    void*, std::vector<LayoutFontTupleV1>* const output) noexcept {
    if (output == nullptr) {
        return false;
    }
    HDC const device = GetDC(nullptr);
    if (device == nullptr) {
        return false;
    }
    LOGFONTW request{};
    request.lfCharSet = DEFAULT_CHARSET;
    FontEnumerationContext context{output, false};
    EnumFontFamiliesExW(
        device, &request, CollectFont,
        reinterpret_cast<LPARAM>(&context), 0);
    ReleaseDC(nullptr, device);
    return !context.failed && !output->empty();
}

bool ReadProductionDpi(
    void*, std::uint32_t* const dpiX, std::uint32_t* const dpiY) noexcept {
    if (dpiX == nullptr || dpiY == nullptr) {
        return false;
    }
    HDC const device = GetDC(nullptr);
    if (device == nullptr) {
        return false;
    }
    const int x = GetDeviceCaps(device, LOGPIXELSX);
    const int y = GetDeviceCaps(device, LOGPIXELSY);
    ReleaseDC(nullptr, device);
    if (x <= 0 || y <= 0) {
        return false;
    }
    *dpiX = static_cast<std::uint32_t>(x);
    *dpiY = static_cast<std::uint32_t>(y);
    return true;
}

bool ReadProductionSystemLcid(
    void*, std::uint32_t* const output) noexcept {
    if (output == nullptr) {
        return false;
    }
    const LCID lcid = GetSystemDefaultLCID();
    if (lcid == 0) {
        return false;
    }
    *output = lcid;
    return true;
}

bool ValidObservation(const ScalarObservation& value) noexcept {
    const auto state = static_cast<std::uint8_t>(value.state);
    return state <= static_cast<std::uint8_t>(ObservationState::ReadFailed) &&
        (value.state == ObservationState::Value || value.value == 0);
}

void AppendObservation(
    std::vector<std::uint8_t>* const output,
    const ScalarObservation& value) {
    AppendByte(output, static_cast<std::uint8_t>(value.state));
    AppendSigned(output, value.value);
}

CaptureStatus NormalizeAndValidate(
    LayoutSnapshot* const snapshot,
    const bool requireEnvironment) {
    if (snapshot == nullptr ||
        (requireEnvironment &&
         !ValidateLayoutEnvironmentV1(snapshot->environment)) ||
        (!snapshot->environment.empty() &&
         !ValidateLayoutEnvironmentV1(snapshot->environment)) ||
        snapshot->pageCount < 1) {
        return CaptureStatus::InvalidLayout;
    }
    std::sort(
        snapshot->sections.begin(),
        snapshot->sections.end(),
        [](const SectionLayoutObservation& first,
           const SectionLayoutObservation& second) {
            return first.sectionIndex < second.sectionIndex;
        });
    std::set<std::int32_t> sectionIndexes;
    for (const SectionLayoutObservation& section : snapshot->sections) {
        if (section.sectionIndex < 0 ||
            !sectionIndexes.insert(section.sectionIndex).second) {
            return CaptureStatus::InvalidLayout;
        }
        for (const ScalarObservation& value : section.setup) {
            if (!ValidObservation(value)) {
                return CaptureStatus::InvalidLayout;
            }
        }
    }
    std::sort(
        snapshot->nodes.begin(),
        snapshot->nodes.end(),
        [](const LayoutNodeObservation& first,
           const LayoutNodeObservation& second) {
            return first.nodeId < second.nodeId ||
                (first.nodeId == second.nodeId &&
                 first.kind < second.kind);
        });
    std::set<std::wstring> nodeIds;
    for (const LayoutNodeObservation& node : snapshot->nodes) {
        if (node.nodeId.empty() || !nodeIds.insert(node.nodeId).second ||
            !ValidObservation(node.pageStart) ||
            !ValidObservation(node.pageEnd)) {
            return CaptureStatus::InvalidLayout;
        }
        if (node.pageStart.state != node.pageEnd.state) {
            return CaptureStatus::InvalidLayout;
        }
        if (node.pageStart.state == ObservationState::Value &&
            (node.pageStart.value < 1 ||
             node.pageEnd.value < node.pageStart.value ||
             node.pageEnd.value > snapshot->pageCount)) {
            return CaptureStatus::InvalidLayout;
        }
        for (const ScalarObservation& value : node.geometry) {
            if (!ValidObservation(value)) {
                return CaptureStatus::InvalidLayout;
            }
        }
    }
    return CaptureStatus::Complete;
}

void AppendSnapshot(
    const LayoutSnapshot& snapshot,
    std::vector<std::uint8_t>* const output) {
    FrameBytes(
        output,
        snapshot.environment.data(),
        snapshot.environment.size());
    AppendSigned(output, snapshot.pageCount);
    AppendUnsigned(output, snapshot.sections.size());
    for (const SectionLayoutObservation& section : snapshot.sections) {
        AppendSigned(output, section.sectionIndex);
        for (const ScalarObservation& value : section.setup) {
            AppendObservation(output, value);
        }
    }
    AppendUnsigned(output, snapshot.nodes.size());
    for (const LayoutNodeObservation& node : snapshot.nodes) {
        AppendText(output, node.nodeId);
        AppendByte(output, static_cast<std::uint8_t>(node.kind));
        AppendObservation(output, node.pageStart);
        AppendObservation(output, node.pageEnd);
        for (const ScalarObservation& value : node.geometry) {
            AppendObservation(output, value);
        }
    }
}

RecalculationObservation RecalculateNativeTable(
    void* const context) noexcept {
    RecalculationObservation observation;
    auto* const native = static_cast<NativeTableContext*>(context);
    if (native == nullptr || native->hwp == nullptr) {
        observation.direct = RecalculationRouteState::Failed;
        observation.action = RecalculationRouteState::Failed;
        return observation;
    }
    CComVariant ignored;
    const HRESULT directStatus = Method(
            native->hwp,
            L"RecalcPageCount",
            {},
            &ignored);
    observation.direct = SUCCEEDED(directStatus)
        ? RecalculationRouteState::Performed
        : directStatus == DISP_E_UNKNOWNNAME ||
            directStatus == DISP_E_MEMBERNOTFOUND
        ? RecalculationRouteState::MemberNotExposed
        : RecalculationRouteState::Failed;
    CComVariant rawAction;
    CComPtr<IDispatch> action;
    CComVariant ran;
    bool result = false;
    const HRESULT actionPropertyStatus = PropertyGet(
            native->hwp,
            L"HAction",
            &rawAction);
    const HRESULT actionDispatchStatus =
        AsDispatch(rawAction, action);
    const HRESULT actionRunStatus = action == nullptr
        ? E_POINTER
        : Method(
            action,
            L"Run",
            {CComVariant(L"RecalcPageCount")},
            &ran);
    const HRESULT actionResultStatus = AsBool(ran, &result);
    observation.action =
        SUCCEEDED(actionPropertyStatus) &&
        SUCCEEDED(actionDispatchStatus) &&
        SUCCEEDED(actionRunStatus) &&
        SUCCEEDED(actionResultStatus)
        ? result
            ? RecalculationRouteState::Performed
            : RecalculationRouteState::SemanticFalse
        : actionPropertyStatus == DISP_E_UNKNOWNNAME ||
            actionPropertyStatus == DISP_E_MEMBERNOTFOUND ||
            actionRunStatus == DISP_E_UNKNOWNNAME ||
            actionRunStatus == DISP_E_MEMBERNOTFOUND
        ? RecalculationRouteState::MemberNotExposed
        : RecalculationRouteState::Failed;
    observation.aggregate =
        observation.direct == RecalculationRouteState::Performed ||
        observation.action == RecalculationRouteState::Performed
        ? RecalculationState::Performed
        : observation.direct == RecalculationRouteState::Failed ||
            observation.action == RecalculationRouteState::Failed
        ? RecalculationState::Failed
        : RecalculationState::NotExposed;
    return observation;
}

RecalculationObservation RecalculateStableNative(
    void* const context) noexcept {
    auto* const native = static_cast<StableNativeCaptureContext*>(context);
    if (native == nullptr || native->recalculate == nullptr ||
        native->callerContext == nullptr) {
        return {
            RecalculationRouteState::Failed,
            RecalculationRouteState::Failed,
            RecalculationState::Failed,
        };
    }
    return native->recalculate(native->callerContext);
}

bool CaptureStableNative(
    void* const context,
    LayoutSnapshot* const output) noexcept {
    auto* const native = static_cast<StableNativeCaptureContext*>(context);
    if (native == nullptr || native->observe == nullptr ||
        native->callerContext == nullptr || output == nullptr) {
        return false;
    }
    NativeLayoutObservationV1 observation;
    if (!native->observe(native->callerContext, &observation)) {
        return false;
    }
    return native->environmentPlatform == nullptr
        ? BuildProductionNativeLayoutSnapshotV1(observation, output)
        : BuildNativeLayoutSnapshotV1(
            observation, *native->environmentPlatform, output);
}

bool CaptureNativeTable(
    void* const context,
    LayoutSnapshot* const output) noexcept {
    auto* const native = static_cast<NativeTableContext*>(context);
    if (native == nullptr || native->hwp == nullptr || output == nullptr) {
        return false;
    }
    CComVariant rawPageCount;
    LONG pageCount = 0;
    if (FAILED(PropertyGet(
            native->hwp,
            L"PageCount",
            &rawPageCount)) ||
        FAILED(AsLong(rawPageCount, &pageCount)) || pageCount < 1) {
        return false;
    }
    std::vector<hancom::inspection::TableCellRecord> cells;
    std::wstring error;
    if (!hancom::inspection::InspectTableCells(
            native->hwp,
            native->tableInstanceId,
            &cells,
            &error)) {
        return false;
    }
    LayoutSnapshot snapshot;
    snapshot.pageCount = pageCount;
    snapshot.nodes.reserve(cells.size() + 1);
    for (const hancom::inspection::TableCellRecord& cell : cells) {
        LayoutNodeObservation node;
        node.nodeId =
            L"cell:" + native->tableInstanceId + L":" + cell.address;
        node.kind = LayoutNodeKind::Cell;
        node.pageStart = {ObservationState::Value, cell.pageStart};
        node.pageEnd = {ObservationState::Value, cell.pageEnd};
        node.geometry[Index(GeometryField::Width)] = cell.width >= 0
            ? ScalarObservation{ObservationState::Value, cell.width}
            : ScalarObservation{};
        node.geometry[Index(GeometryField::Height)] = cell.height >= 0
            ? ScalarObservation{ObservationState::Value, cell.height}
            : ScalarObservation{};
        node.geometry[Index(GeometryField::AnchorListId)] =
            {ObservationState::Value, cell.listId};
        snapshot.nodes.push_back(std::move(node));
    }
    LayoutNodeObservation table;
    table.nodeId = L"table:" + native->tableInstanceId;
    table.kind = LayoutNodeKind::Table;
    if (UnionPageSpans(
            snapshot.nodes,
            &table.pageStart,
            &table.pageEnd) != CaptureStatus::Complete) {
        return false;
    }
    snapshot.nodes.push_back(std::move(table));
    NativeLayoutObservationV1 observation;
    observation.pageCount = snapshot.pageCount;
    observation.sections.push_back({0, {}});
    observation.nodes = std::move(snapshot.nodes);
    return native->environmentPlatform == nullptr
        ? BuildProductionNativeLayoutSnapshotV1(observation, output)
        : BuildNativeLayoutSnapshotV1(
            observation, *native->environmentPlatform, output);
}

bool CaptureNativeTableRestoringState(
    void* const context,
    LayoutSnapshot* const output) noexcept {
    auto* const native = static_cast<NativeTableContext*>(context);
    if (native == nullptr || native->hwp == nullptr || output == nullptr) {
        return false;
    }
    hancom::com_state::Position cursor;
    hancom::com_state::Selection selection;
    hancom::com_state::SelectionCaptureFailure failure;
    if (FAILED(hancom::com_state::CapturePosition(native->hwp, &cursor)) ||
        !hancom::com_state::CaptureSelection(
            native->hwp, &selection,
            hancom::com_state::SelectionCapturePolicy::RequiredControl,
            &failure) ||
        !hancom::com_state::CanRestoreSelection(selection)) {
        return false;
    }
    const bool captured = CaptureNativeTable(context, output);
    const bool restored = hancom::com_state::RestoreSelection(
        native->hwp, cursor, selection);
    if (!captured || !restored) {
        *output = {};
        return false;
    }
    return true;
}

} // namespace

static bool BuildNativeLayoutSnapshot(
    const NativeLayoutObservationV1& observation,
    const LayoutEnvironmentPlatformV1* const platform,
    LayoutSnapshot* const output) noexcept {
    if (output == nullptr) {
        return false;
    }
    *output = {};
    try {
        LayoutSnapshot snapshot;
        snapshot.pageCount = observation.pageCount;
        snapshot.sections = observation.sections;
        snapshot.nodes = observation.nodes;
        if (snapshot.sections.empty()) {
            return false;
        }
        std::sort(
            snapshot.sections.begin(), snapshot.sections.end(),
            [](const SectionLayoutObservation& first,
               const SectionLayoutObservation& second) {
                return first.sectionIndex < second.sectionIndex;
            });
        std::vector<std::uint8_t> pageSetup;
        AppendUnsigned(&pageSetup, snapshot.sections.size());
        for (const SectionLayoutObservation& section : snapshot.sections) {
            AppendSigned(&pageSetup, section.sectionIndex);
            for (const ScalarObservation& value : section.setup) {
                if (!ValidObservation(value)) {
                    return false;
                }
                AppendObservation(&pageSetup, value);
            }
        }
        const Sha256 pageSetupDigest = codec::Hash(codec::View(pageSetup));
        const bool environmentCollected = platform == nullptr
            ? CollectProductionLayoutEnvironmentV1(
                pageSetupDigest, &snapshot.environment)
            : CollectLayoutEnvironmentV1(
                *platform, pageSetupDigest, &snapshot.environment);
        if (!environmentCollected ||
            NormalizeAndValidate(&snapshot, true) != CaptureStatus::Complete) {
            return false;
        }
        *output = std::move(snapshot);
        return true;
    } catch (...) {
        *output = {};
        return false;
    }
}

bool BuildSectionLayoutObservationsV1(
    const capture::ReaderPayload& referenceClosure,
    std::vector<SectionLayoutObservation>* const output) noexcept {
    if (output == nullptr) {
        return false;
    }
    output->clear();
    try {
        static constexpr std::array<PropertyKeyId, kSectionFieldCount> keys{
            7000, 7001, 7002, 7003, 7004, 7005, 7006, 7007, 7008,
            7009, 7010, 7100, 7101, 7102};
        static constexpr size_t firstColumnField = 11;
        for (const capture::DefinitionReferenceObservation& reference :
             referenceClosure.definitionReferences) {
            if (reference.edge != EdgeKind::PageDefRef) {
                continue;
            }
            if (reference.source != capture::PropertyTarget::Section ||
                reference.ordinal > static_cast<std::uint64_t>(INT_MAX)) {
                output->clear();
                return false;
            }
            const auto definition = std::find_if(
                referenceClosure.definitions.begin(),
                referenceClosure.definitions.end(),
                [&reference](const capture::DefinitionObservation& candidate) {
                    return candidate.kind == DefinitionKind::PageDef &&
                        candidate.identity == reference.definitionIdentity;
                });
            if (definition == referenceClosure.definitions.end()) {
                output->clear();
                return false;
            }
            SectionLayoutObservation section;
            section.sectionIndex = static_cast<std::int32_t>(reference.ordinal);
            const auto bodyState = static_cast<std::uint8_t>(
                definition->bodyState);
            if (bodyState > static_cast<std::uint8_t>(
                    ObservationState::ReadFailed)) {
                output->clear();
                return false;
            }
            if (definition->bodyState !=
                hancom::graph::ObservationState::Value) {
                for (ScalarObservation& field : section.setup) {
                    field.state = static_cast<ObservationState>(bodyState);
                }
            } else {
                for (size_t field = 0; field < firstColumnField; ++field) {
                    const auto property = std::find_if(
                        definition->properties.begin(),
                        definition->properties.end(),
                        [field](const capture::PropertyObservation& candidate) {
                            return candidate.key == keys[field];
                        });
                    if (property == definition->properties.end()) {
                        output->clear();
                        return false;
                    }
                    const auto state = static_cast<std::uint8_t>(
                        property->state);
                    if (state > static_cast<std::uint8_t>(
                            ObservationState::ReadFailed) ||
                        (property->state !=
                             hancom::graph::ObservationState::Value &&
                         property->integerValue != 0)) {
                        output->clear();
                        return false;
                    }
                    section.setup[field] = {
                        static_cast<ObservationState>(state),
                        property->integerValue};
                }
            }
            output->push_back(std::move(section));
        }
        for (SectionLayoutObservation& section : *output) {
            const auto reference = std::find_if(
                referenceClosure.definitionReferences.begin(),
                referenceClosure.definitionReferences.end(),
                [&section](const auto& candidate) {
                    return candidate.edge == EdgeKind::ColumnDefRef &&
                        candidate.source ==
                            capture::PropertyTarget::Section &&
                        candidate.ordinal == static_cast<std::uint64_t>(
                            section.sectionIndex);
                });
            if (reference == referenceClosure.definitionReferences.end()) {
                output->clear();
                return false;
            }
            const auto definition = std::find_if(
                referenceClosure.definitions.begin(),
                referenceClosure.definitions.end(),
                [&reference](const auto& candidate) {
                    return candidate.kind == DefinitionKind::ColumnDef &&
                        candidate.identity == reference->definitionIdentity;
                });
            if (definition == referenceClosure.definitions.end()) {
                output->clear();
                return false;
            }
            const auto bodyState = static_cast<std::uint8_t>(
                definition->bodyState);
            if (bodyState > static_cast<std::uint8_t>(
                    ObservationState::ReadFailed)) {
                output->clear();
                return false;
            }
            if (definition->bodyState !=
                hancom::graph::ObservationState::Value) {
                for (size_t field = firstColumnField;
                     field < keys.size(); ++field) {
                    section.setup[field].state =
                        static_cast<ObservationState>(bodyState);
                }
            } else {
                for (size_t field = firstColumnField;
                     field < keys.size(); ++field) {
                    const auto property = std::find_if(
                        definition->properties.begin(),
                        definition->properties.end(),
                        [field](const auto& candidate) {
                            return candidate.key == keys[field];
                        });
                    if (property == definition->properties.end()) {
                        output->clear();
                        return false;
                    }
                    const auto state = static_cast<std::uint8_t>(
                        property->state);
                    if (state > static_cast<std::uint8_t>(
                            ObservationState::ReadFailed) ||
                        (property->state !=
                             hancom::graph::ObservationState::Value &&
                         property->integerValue != 0)) {
                        output->clear();
                        return false;
                    }
                    section.setup[field] = {
                        static_cast<ObservationState>(state),
                        property->integerValue};
                }
            }
        }
        std::sort(
            output->begin(), output->end(),
            [](const SectionLayoutObservation& first,
               const SectionLayoutObservation& second) {
                return first.sectionIndex < second.sectionIndex;
            });
        return !output->empty() && std::adjacent_find(
            output->begin(), output->end(),
            [](const SectionLayoutObservation& first,
               const SectionLayoutObservation& second) {
                return first.sectionIndex == second.sectionIndex;
            }) == output->end();
    } catch (...) {
        output->clear();
        return false;
    }
}

bool BuildNativeLayoutSnapshotV1(
    const NativeLayoutObservationV1& observation,
    const LayoutEnvironmentPlatformV1& platform,
    LayoutSnapshot* const output) noexcept {
    return BuildNativeLayoutSnapshot(observation, &platform, output);
}

bool BuildProductionNativeLayoutSnapshotV1(
    const NativeLayoutObservationV1& observation,
    LayoutSnapshot* const output) noexcept {
    return BuildNativeLayoutSnapshot(observation, nullptr, output);
}

static CaptureStatus CaptureStableNativeLayout(
    const RecalculateCallback recalculate,
    const NativeLayoutObservationCallback observe,
    void* const context,
    const QualificationMode mode,
    const LayoutEnvironmentPlatformV1* const platform,
    StableLayoutRecord* const output) noexcept {
    if (recalculate == nullptr || observe == nullptr || context == nullptr ||
        output == nullptr) {
        return CaptureStatus::InvalidArgument;
    }
    StableNativeCaptureContext native{
        recalculate, observe, context, platform};
    return CaptureStableLayout(
        RecalculateStableNative,
        CaptureStableNative,
        &native,
        mode,
        output);
}

CaptureStatus CaptureStableNativeLayoutV1(
    const RecalculateCallback recalculate,
    const NativeLayoutObservationCallback observe,
    void* const context,
    const QualificationMode mode,
    StableLayoutRecord* const output) noexcept {
    return CaptureStableNativeLayout(
        recalculate, observe, context, mode, nullptr, output);
}

CaptureStatus CaptureStableNativeLayoutV1(
    const RecalculateCallback recalculate,
    const NativeLayoutObservationCallback observe,
    void* const context,
    const QualificationMode mode,
    const LayoutEnvironmentPlatformV1& platform,
    StableLayoutRecord* const output) noexcept {
    return CaptureStableNativeLayout(
        recalculate, observe, context, mode, &platform, output);
}

bool CollectLayoutEnvironmentV1(
    const LayoutEnvironmentPlatformV1& platform,
    const Sha256& pageSetupDigest,
    std::vector<std::uint8_t>* const output) noexcept {
    if (output == nullptr) {
        return false;
    }
    output->clear();
    if (platform.readHwpFileVersion == nullptr ||
        platform.readPrinterName == nullptr ||
        platform.readPrinterDevMode == nullptr ||
        platform.readFontInventory == nullptr ||
        platform.readDpi == nullptr ||
        platform.readSystemLcid == nullptr) {
        return false;
    }
    try {
        LayoutEnvironmentV1 environment;
        std::vector<std::uint8_t> devmode;
        std::vector<LayoutFontTupleV1> fonts;
        if (!platform.readHwpFileVersion(
                platform.context, &environment.hwpFileVersion) ||
            !platform.readPrinterName(
                platform.context, &environment.printerName) ||
            environment.printerName.empty() ||
            !platform.readPrinterDevMode(
                platform.context, environment.printerName, &devmode) ||
            devmode.empty() ||
            !platform.readFontInventory(platform.context, &fonts) ||
            fonts.empty() ||
            !platform.readDpi(
                platform.context, &environment.dpiX, &environment.dpiY) ||
            environment.dpiX == 0 || environment.dpiY == 0 ||
            !platform.readSystemLcid(
                platform.context, &environment.systemLcid) ||
            environment.systemLcid == 0 ||
            std::all_of(
                environment.hwpFileVersion.begin(),
                environment.hwpFileVersion.end(),
                [](const std::uint16_t value) { return value == 0; })) {
            return false;
        }

        std::vector<std::uint8_t> printerHashInput;
        const std::vector<std::uint8_t> printerBytes =
            Utf16Bytes(environment.printerName);
        FrameBytes(
            &printerHashInput, printerBytes.data(), printerBytes.size());
        printerHashInput.insert(
            printerHashInput.end(), devmode.begin(), devmode.end());
        environment.devmodeDigest = codec::Hash(
            codec::View(printerHashInput));

        std::vector<std::vector<std::uint8_t>> normalizedFonts;
        normalizedFonts.reserve(fonts.size());
        for (const LayoutFontTupleV1& font : fonts) {
            normalizedFonts.push_back(FontTupleBytes(font));
        }
        std::sort(normalizedFonts.begin(), normalizedFonts.end());
        normalizedFonts.erase(
            std::unique(normalizedFonts.begin(), normalizedFonts.end()),
            normalizedFonts.end());
        std::vector<std::uint8_t> fontHashInput;
        AppendUnsigned(&fontHashInput, normalizedFonts.size());
        for (const auto& font : normalizedFonts) {
            FrameBytes(&fontHashInput, font.data(), font.size());
        }
        environment.fontInventoryDigest = codec::Hash(
            codec::View(fontHashInput));
        environment.pageSetupDigest = pageSetupDigest;
        return SerializeLayoutEnvironmentV1(environment, output);
    } catch (...) {
        output->clear();
        return false;
    }
}

bool CollectProductionLayoutEnvironmentV1(
    const Sha256& pageSetupDigest,
    std::vector<std::uint8_t>* const output) noexcept {
    const LayoutEnvironmentPlatformV1 platform{
        nullptr,
        ReadProductionHwpFileVersion,
        ReadProductionPrinterName,
        ReadProductionPrinterDevMode,
        ReadProductionFontInventory,
        ReadProductionDpi,
        ReadProductionSystemLcid,
    };
    return CollectLayoutEnvironmentV1(platform, pageSetupDigest, output);
}

bool SerializeLayoutEnvironmentV1(
    const LayoutEnvironmentV1& environment,
    std::vector<std::uint8_t>* const output) noexcept {
    if (output == nullptr || environment.printerName.empty() ||
        environment.dpiX == 0 || environment.dpiY == 0 ||
        std::all_of(
            environment.hwpFileVersion.begin(),
            environment.hwpFileVersion.end(),
            [](const std::uint16_t value) { return value == 0; })) {
        return false;
    }
    try {
        std::vector<std::uint8_t> serialized;
        std::vector<std::uint8_t> version;
        version.reserve(8);
        for (const std::uint16_t word : environment.hwpFileVersion) {
            version.push_back(static_cast<std::uint8_t>(word & 0xffU));
            version.push_back(static_cast<std::uint8_t>(word >> 8));
        }
        FrameBytes(&serialized, version.data(), version.size());
        std::vector<std::uint8_t> printer;
        printer.reserve(environment.printerName.size() * 2);
        for (const wchar_t unit : environment.printerName) {
            const auto encoded = static_cast<std::uint16_t>(unit);
            printer.push_back(static_cast<std::uint8_t>(encoded & 0xffU));
            printer.push_back(static_cast<std::uint8_t>(encoded >> 8));
        }
        FrameBytes(&serialized, printer.data(), printer.size());
        FrameBytes(
            &serialized,
            environment.devmodeDigest.bytes.data(),
            environment.devmodeDigest.bytes.size());
        FrameBytes(
            &serialized,
            environment.fontInventoryDigest.bytes.data(),
            environment.fontInventoryDigest.bytes.size());
        std::vector<std::uint8_t> scalar;
        scalar.reserve(4);
        Append32(&scalar, environment.dpiX);
        FrameBytes(&serialized, scalar.data(), scalar.size());
        scalar.clear();
        Append32(&scalar, environment.dpiY);
        FrameBytes(&serialized, scalar.data(), scalar.size());
        scalar.clear();
        Append32(&scalar, environment.systemLcid);
        FrameBytes(&serialized, scalar.data(), scalar.size());
        FrameBytes(
            &serialized,
            environment.pageSetupDigest.bytes.data(),
            environment.pageSetupDigest.bytes.size());
        *output = std::move(serialized);
        return true;
    } catch (...) {
        output->clear();
        return false;
    }
}

bool ReplacePageSetupDigestV1(
    const std::vector<std::uint8_t>& serialized,
    const Sha256& pageSetupDigest,
    std::vector<std::uint8_t>* const output) noexcept {
    if (output == nullptr || !ValidateLayoutEnvironmentV1(serialized)) {
        return false;
    }
    try {
        *output = serialized;
        size_t offset = 0;
        for (size_t field = 0; field < 8; ++field) {
            if (offset > output->size() || output->size() - offset < 8) {
                output->clear();
                return false;
            }
            std::uint64_t size = 0;
            for (size_t byte = 0; byte < 8; ++byte) {
                size |= static_cast<std::uint64_t>((*output)[offset + byte])
                    << (byte * 8);
            }
            offset += 8;
            if (size > output->size() - offset) {
                output->clear();
                return false;
            }
            if (field == 7) {
                if (size != pageSetupDigest.bytes.size()) {
                    output->clear();
                    return false;
                }
                std::copy(
                    pageSetupDigest.bytes.begin(),
                    pageSetupDigest.bytes.end(),
                    output->begin() + offset);
                return true;
            }
            offset += static_cast<size_t>(size);
        }
    } catch (...) {
        output->clear();
    }
    return false;
}

bool ValidateLayoutEnvironmentV1(
    const std::vector<std::uint8_t>& serialized) noexcept {
    const auto read64 = [&serialized](size_t* const offset,
                                      std::uint64_t* const value) {
        if (*offset > serialized.size() ||
            serialized.size() - *offset < 8) {
            return false;
        }
        *value = 0;
        for (size_t byte = 0; byte < 8; ++byte) {
            *value |= static_cast<std::uint64_t>(
                serialized[*offset + byte]) << (byte * 8);
        }
        *offset += 8;
        return true;
    };
    size_t offset = 0;
    const std::array<std::uint64_t, 8> expected{8, 0, 32, 32, 4, 4, 4, 32};
    for (size_t field = 0; field < expected.size(); ++field) {
        std::uint64_t size = 0;
        if (!read64(&offset, &size) ||
            size > serialized.size() - offset ||
            (field != 1 && size != expected[field]) ||
            (field == 1 && (size == 0 || size % 2 != 0))) {
            return false;
        }
        if (field == 0 &&
            std::all_of(
                serialized.begin() + offset,
                serialized.begin() + offset + static_cast<size_t>(size),
                [](const std::uint8_t value) { return value == 0; })) {
            return false;
        }
        if ((field == 4 || field == 5) &&
            serialized[offset] == 0 && serialized[offset + 1] == 0 &&
            serialized[offset + 2] == 0 && serialized[offset + 3] == 0) {
            return false;
        }
        offset += static_cast<size_t>(size);
    }
    return offset == serialized.size();
}

CaptureStatus ComputeLayoutRoot(
    const LayoutSnapshot& snapshot,
    std::wstring* const output) noexcept {
    if (output == nullptr) {
        return CaptureStatus::InvalidArgument;
    }
    output->clear();
    try {
        LayoutSnapshot normalized = snapshot;
        const CaptureStatus valid = NormalizeAndValidate(&normalized, true);
        if (valid != CaptureStatus::Complete) {
            return valid;
        }
        std::vector<std::uint8_t> bytes;
        AppendSnapshot(normalized, &bytes);
        MemorySource source{&bytes, 0};
        hancom::graph::images::AssetDigest digest;
        const auto status = hancom::graph::images::DigestAssetStream(
            ReadMemory,
            &source,
            1024U * 1024U,
            &digest);
        if (status != hancom::graph::images::CaptureStatus::Complete ||
            digest.state !=
                hancom::graph::images::ObservationState::Value) {
            return CaptureStatus::SourceFailed;
        }
        *output = std::move(digest.sha256);
        return CaptureStatus::Complete;
    } catch (const std::bad_alloc&) {
        return CaptureStatus::ResourceExhausted;
    } catch (...) {
        return CaptureStatus::SourceFailed;
    }
}

CaptureStatus ComputeLayoutObservationRoot(
    const LayoutSnapshot& snapshot,
    std::wstring* const output) noexcept {
    if (output == nullptr) {
        return CaptureStatus::InvalidArgument;
    }
    output->clear();
    try {
        LayoutSnapshot normalized = snapshot;
        const CaptureStatus valid =
            NormalizeAndValidate(&normalized, false);
        if (valid != CaptureStatus::Complete) {
            return valid;
        }
        std::vector<std::uint8_t> bytes;
        AppendSnapshot(normalized, &bytes);
        MemorySource source{&bytes, 0};
        hancom::graph::images::AssetDigest digest;
        const auto status = hancom::graph::images::DigestAssetStream(
            ReadMemory, &source, 1024U * 1024U, &digest);
        if (status != hancom::graph::images::CaptureStatus::Complete ||
            digest.state !=
                hancom::graph::images::ObservationState::Value) {
            return CaptureStatus::SourceFailed;
        }
        *output = std::move(digest.sha256);
        return CaptureStatus::Complete;
    } catch (const std::bad_alloc&) {
        return CaptureStatus::ResourceExhausted;
    } catch (...) {
        return CaptureStatus::SourceFailed;
    }
}

CaptureStatus CaptureStableLayout(
    const RecalculateCallback recalculate,
    const CaptureCallback capture,
    void* const context,
    const QualificationMode mode,
    StableLayoutRecord* const output) noexcept {
    if (recalculate == nullptr || capture == nullptr || context == nullptr ||
        output == nullptr) {
        return CaptureStatus::InvalidArgument;
    }
    *output = {};
    LayoutSnapshot first;
    LayoutSnapshot second;
    std::wstring firstRoot;
    std::wstring secondRoot;
    const RecalculationObservation firstRecalculation =
        recalculate(context);
    if (firstRecalculation.aggregate == RecalculationState::Failed) {
        return CaptureStatus::SourceFailed;
    }
    if (firstRecalculation.aggregate == RecalculationState::NotExposed &&
        mode == QualificationMode::RequireRecalculation) {
        return CaptureStatus::RecalculationNotExposed;
    }
    if (!capture(context, &first)) {
        return CaptureStatus::SourceFailed;
    }
    CaptureStatus status = first.environment.empty()
        ? ComputeLayoutObservationRoot(first, &firstRoot)
        : ComputeLayoutRoot(first, &firstRoot);
    if (status != CaptureStatus::Complete) {
        return status;
    }
    const RecalculationObservation secondRecalculation =
        recalculate(context);
    if (secondRecalculation.aggregate == RecalculationState::Failed) {
        return CaptureStatus::SourceFailed;
    }
    if (secondRecalculation.aggregate == RecalculationState::NotExposed &&
        mode == QualificationMode::RequireRecalculation) {
        return CaptureStatus::RecalculationNotExposed;
    }
    if (!capture(context, &second)) {
        return CaptureStatus::SourceFailed;
    }
    status = second.environment.empty()
        ? ComputeLayoutObservationRoot(second, &secondRoot)
        : ComputeLayoutRoot(second, &secondRoot);
    if (status != CaptureStatus::Complete) {
        return status;
    }
    if (firstRoot != secondRoot) {
        return CaptureStatus::LayoutUnstable;
    }
    output->snapshot = std::move(second);
    output->layoutRoot = std::move(secondRoot);
    output->settleAttempts = {
        firstRecalculation,
        secondRecalculation,
    };
    return CaptureStatus::Complete;
}

CaptureStatus UnionPageSpans(
    const std::vector<LayoutNodeObservation>& children,
    ScalarObservation* const pageStart,
    ScalarObservation* const pageEnd) noexcept {
    if (children.empty() || pageStart == nullptr || pageEnd == nullptr) {
        return CaptureStatus::InvalidArgument;
    }
    std::int64_t first = (std::numeric_limits<std::int64_t>::max)();
    std::int64_t last = 0;
    for (const LayoutNodeObservation& child : children) {
        if (child.pageStart.state != ObservationState::Value ||
            child.pageEnd.state != ObservationState::Value) {
            *pageStart = {};
            *pageEnd = {};
            return CaptureStatus::Complete;
        }
        if (child.pageStart.value < 1 ||
            child.pageEnd.value < child.pageStart.value) {
            return CaptureStatus::InvalidLayout;
        }
        first = (std::min)(first, child.pageStart.value);
        last = (std::max)(last, child.pageEnd.value);
    }
    *pageStart = {ObservationState::Value, first};
    *pageEnd = {ObservationState::Value, last};
    return CaptureStatus::Complete;
}

CaptureStatus CaptureCurrentTableLayoutFromNative(
    IDispatch* const hwp,
    const std::wstring& tableInstanceId,
    StableLayoutRecord* const output) noexcept {
    if (hwp == nullptr || tableInstanceId.empty() || output == nullptr) {
        return CaptureStatus::InvalidArgument;
    }
    NativeTableContext context{hwp, tableInstanceId, nullptr};
    return CaptureStableLayout(
        RecalculateNativeTable,
        CaptureNativeTableRestoringState,
        &context,
        QualificationMode::AllowStableObservationWhenNotExposed,
        output);
}

CaptureStatus CaptureCurrentTableLayoutFromNative(
    IDispatch* const hwp,
    const std::wstring& tableInstanceId,
    const LayoutEnvironmentPlatformV1& platform,
    StableLayoutRecord* const output) noexcept {
    if (hwp == nullptr || tableInstanceId.empty() || output == nullptr) {
        return CaptureStatus::InvalidArgument;
    }
    NativeTableContext context{hwp, tableInstanceId, &platform};
    return CaptureStableLayout(
        RecalculateNativeTable,
        CaptureNativeTableRestoringState,
        &context,
        QualificationMode::AllowStableObservationWhenNotExposed,
        output);
}

} // namespace hancom::graph::layout
