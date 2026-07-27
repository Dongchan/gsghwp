#include "ActionExecutorInternal.h"

#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

namespace hancom::actions::detail {

constexpr std::uintmax_t kMaximumBlockFileBytes = 256ULL * 1024ULL * 1024ULL;
constexpr char kEncodedBlockMagic[] = "GSG_HWP_ENCODED_BLOCK_V1\n";

bool ReadTail(
    Context* const context,
    const Position& start,
    std::wstring* const selected) {
    if (!SetPosition(context->hwp, start, context->result, L"DELETE_TAIL") ||
        !RunAction(context->action, L"MoveSelDocEnd", context->result, L"DELETE_TAIL")) {
        return false;
    }
    CComVariant raw;
    HRESULT status = Method(
        context->hwp,
        L"GetTextFile",
        {CComVariant(L"UNICODE"), CComVariant(L"saveblock:true")},
        &raw);
    if (SUCCEEDED(status)) {
        status = AsString(raw, selected);
    }
    if (FAILED(status)) {
        return SetError(context->result, L"DELETE_TAIL", L"", FormatHResult(L"GetTextFile", status));
    }
    return true;
}

bool DeleteTail(Context* const context, const Command& command) {
    const Position start{command.list, command.paragraph, command.character};
    std::wstring selected;
    if (!ReadTail(context, start, &selected)) {
        return false;
    }
    if (selected.rfind(command.first, 0) != 0) {
        static_cast<void>(RunAction(context->action, L"Cancel", context->result, L"DELETE_TAIL"));
        return SetError(context->result, L"STALE_TAIL", L"", L"tail prefix does not match the request");
    }
    return RunAction(context->action, L"Delete", context->result, L"DELETE_TAIL");
}

bool RollbackAppendTail(Context* const context, const Position& start) {
    ExecutionResult rollbackResult;
    Context rollback;
    rollback.hwp = context->hwp;
    rollback.action = context->action;
    rollback.result = &rollbackResult;
    static_cast<void>(RunAction(
        rollback.action,
        L"Cancel",
        rollback.result,
        L"ROLLBACK"));
    bool controlsDeleted = true;
    for (auto control = context->result->createdControlIds.rbegin();
         control != context->result->createdControlIds.rend();
         ++control) {
        if (!DeleteControl(&rollback, *control)) {
            controlsDeleted = false;
        }
    }
    std::wstring expected;
    if (!ReadTail(&rollback, start, &expected)) {
        return false;
    }
    Command command;
    command.kind = CommandKind::DeleteTail;
    command.list = start.list;
    command.paragraph = start.paragraph;
    command.character = start.character;
    command.first = std::move(expected);
    return DeleteTail(&rollback, command) && controlsDeleted;
}

bool CaptureCallResult(
    ExecutionResult* const result,
    const std::wstring& method,
    const CComVariant& raw) {
    CComVariant value;
    const HRESULT status = VariantCopyInd(&value, &raw);
    if (FAILED(status)) {
        return SetError(result, L"CALL_RETURN", method, FormatHResult(method.c_str(), status));
    }
    CallResult captured;
    captured.method = method;
    switch (value.vt) {
    case VT_EMPTY:
    case VT_NULL:
        captured.value = std::monostate{};
        break;
    case VT_BOOL:
        captured.value = value.boolVal != VARIANT_FALSE;
        break;
    case VT_I1:
        captured.value = static_cast<std::int64_t>(value.cVal);
        break;
    case VT_I2:
        captured.value = static_cast<std::int64_t>(value.iVal);
        break;
    case VT_I4:
        captured.value = static_cast<std::int64_t>(value.lVal);
        break;
    case VT_INT:
        captured.value = static_cast<std::int64_t>(value.intVal);
        break;
    case VT_I8:
        captured.value = static_cast<std::int64_t>(value.llVal);
        break;
    case VT_UI1:
        captured.value = static_cast<std::uint64_t>(value.bVal);
        break;
    case VT_UI2:
        captured.value = static_cast<std::uint64_t>(value.uiVal);
        break;
    case VT_UI4:
        captured.value = static_cast<std::uint64_t>(value.ulVal);
        break;
    case VT_UINT:
        captured.value = static_cast<std::uint64_t>(value.uintVal);
        break;
    case VT_UI8:
        captured.value = static_cast<std::uint64_t>(value.ullVal);
        break;
    case VT_BSTR:
        captured.value = value.bstrVal == nullptr
            ? std::wstring()
            : std::wstring(value.bstrVal, SysStringLen(value.bstrVal));
        break;
    default:
        return SetError(
            result,
            L"CALL_RETURN_TYPE",
            method,
            L"Automation method returned an unsupported VARIANT type " +
                std::to_wstring(value.vt));
    }
    result->callResults.push_back(std::move(captured));
    return true;
}

bool ExecuteCall(Context* const context, const Command& command) {
    std::vector<CComVariant> arguments;
    arguments.reserve(command.arguments.size());
    for (const Value& value : command.arguments) {
        CComVariant converted;
        if (!ConvertValue(
                context->hwp,
                value,
                &converted,
                context->result,
                command.name)) {
            return false;
        }
        arguments.push_back(converted);
    }
    CComVariant returned;
    const HRESULT status = Method(
        context->hwp,
        command.name.c_str(),
        arguments,
        &returned);
    if (FAILED(status)) {
        return SetError(context->result, L"COM_METHOD", command.name, FormatHResult(command.name.c_str(), status));
    }
    return CaptureCallResult(context->result, command.name, returned);
}

bool SaveEncodedBlockFile(
    Context* const context,
    const std::wstring& pathText,
    const std::wstring& encodedBlock) {
    const size_t magicLength = std::char_traits<char>::length(kEncodedBlockMagic);
    if (encodedBlock.empty() || encodedBlock.size() > kMaximumBlockFileBytes - magicLength) {
        return SetError(context->result, L"BLOCK_FILE_SIZE", pathText, L"encoded HWP block text is empty or exceeds 256 MiB");
    }
    std::vector<BYTE> bytes;
    bytes.reserve(magicLength + encodedBlock.size());
    bytes.insert(bytes.end(), kEncodedBlockMagic, kEncodedBlockMagic + magicLength);
    for (const wchar_t character : encodedBlock) {
        if (character > 0x7f) {
            return SetError(context->result, L"BLOCK_FILE_ENCODING", pathText, L"encoded HWP block text contains a non-ASCII character");
        }
        bytes.push_back(static_cast<BYTE>(character));
    }
    const HANDLE output = CreateFileW(
        pathText.c_str(),
        GENERIC_WRITE,
        0,
        nullptr,
        CREATE_NEW,
        FILE_ATTRIBUTE_TEMPORARY,
        nullptr);
    if (output == INVALID_HANDLE_VALUE) {
        return SetError(context->result, L"BLOCK_FILE_CREATE", pathText, L"block output file could not be created exclusively");
    }
    DWORD written = 0;
    const BOOL writeSucceeded = WriteFile(
        output,
        bytes.data(),
        static_cast<DWORD>(bytes.size()),
        &written,
        nullptr);
    const BOOL closeSucceeded = CloseHandle(output);
    if (!writeSucceeded || written != bytes.size() || !closeSucceeded) {
        static_cast<void>(DeleteFileW(pathText.c_str()));
        return SetError(context->result, L"BLOCK_FILE_WRITE", pathText, L"encoded HWP block could not be written completely");
    }
    return true;
}

bool SaveDocumentFile(Context* const context, const std::wstring& pathText) {
    CComVariant rawDocument;
    std::wstring documentBlock;
    HRESULT status = Method(
        context->hwp,
        L"GetTextFile",
        {CComVariant(L"HWP"), CComVariant(L"")},
        &rawDocument);
    if (SUCCEEDED(status)) {
        status = AsString(rawDocument, &documentBlock);
    }
    if (FAILED(status) || documentBlock.empty()) {
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_CAPTURE",
            pathText,
            FAILED(status)
                ? FormatHResult(L"GetTextFile HWP document", status)
                : L"GetTextFile returned an empty HWP document");
    }
    return SaveEncodedBlockFile(context, pathText, documentBlock);
}

bool ReadEncodedBlockFile(
    ExecutionResult* const result,
    const std::wstring& pathText,
    std::wstring* const encodedBlock) {
    if (encodedBlock == nullptr ||
        !std::filesystem::path(pathText).is_absolute()) {
        return SetError(
            result,
            L"BLOCK_INPUT_PATH",
            pathText,
            L"block input path must be absolute");
    }
    const HANDLE input = CreateFileW(
        pathText.c_str(),
        GENERIC_READ,
        FILE_SHARE_READ,
        nullptr,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        nullptr);
    if (input == INVALID_HANDLE_VALUE) {
        return SetError(
            result,
            L"BLOCK_FILE_OPEN",
            pathText,
            L"encoded HWP block file could not be opened");
    }
    LARGE_INTEGER fileSize{};
    const BOOL sizeSucceeded = GetFileSizeEx(input, &fileSize);
    const size_t magicLength = std::char_traits<char>::length(kEncodedBlockMagic);
    if (!sizeSucceeded ||
        fileSize.QuadPart <= static_cast<LONGLONG>(magicLength) ||
        fileSize.QuadPart > static_cast<LONGLONG>(kMaximumBlockFileBytes)) {
        static_cast<void>(CloseHandle(input));
        return SetError(
            result,
            L"BLOCK_FILE_SIZE",
            pathText,
            L"encoded HWP block file is empty, truncated, or exceeds 256 MiB");
    }
    std::vector<BYTE> bytes(static_cast<size_t>(fileSize.QuadPart));
    DWORD bytesRead = 0;
    const BOOL readSucceeded = ReadFile(
        input,
        bytes.data(),
        static_cast<DWORD>(bytes.size()),
        &bytesRead,
        nullptr);
    const BOOL closeSucceeded = CloseHandle(input);
    if (!readSucceeded || bytesRead != bytes.size() || !closeSucceeded) {
        return SetError(
            result,
            L"BLOCK_FILE_READ",
            pathText,
            L"encoded HWP block file could not be read completely");
    }
    if (!std::equal(
            kEncodedBlockMagic,
            kEncodedBlockMagic + magicLength,
            bytes.begin())) {
        return SetError(
            result,
            L"BLOCK_FILE_MAGIC",
            pathText,
            L"encoded HWP block file header is invalid");
    }
    encodedBlock->clear();
    encodedBlock->reserve(bytes.size() - magicLength);
    for (size_t index = magicLength; index < bytes.size(); ++index) {
        if (bytes[index] == 0 || bytes[index] > 0x7f) {
            encodedBlock->clear();
            return SetError(
                result,
                L"BLOCK_FILE_ENCODING",
                pathText,
                L"encoded HWP block contains a non-ASCII or NUL byte");
        }
        encodedBlock->push_back(static_cast<wchar_t>(bytes[index]));
    }
    if (encodedBlock->empty()) {
        return SetError(
            result,
            L"BLOCK_FILE_SIZE",
            pathText,
            L"encoded HWP block is empty");
    }
    return true;
}

bool CaptureDocumentBlock(
    Context* const context,
    const std::wstring& location,
    std::wstring* const documentBlock) {
    CComVariant rawDocument;
    HRESULT status = Method(
        context->hwp,
        L"GetTextFile",
        {CComVariant(L"HWP"), CComVariant(L"")},
        &rawDocument);
    if (SUCCEEDED(status)) {
        status = AsString(rawDocument, documentBlock);
    }
    if (FAILED(status) || documentBlock->empty()) {
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_CAPTURE",
            location,
            FAILED(status)
                ? FormatHResult(L"GetTextFile HWP document", status)
                : L"GetTextFile returned an empty HWP document");
    }
    return true;
}

bool ReadPageCount(
    Context* const context,
    const std::wstring& location,
    LONG* const pageCount) {
    CComVariant rawPageCount;
    HRESULT status = PropertyGet(context->hwp, L"PageCount", &rawPageCount);
    if (SUCCEEDED(status)) {
        status = AsLong(rawPageCount, pageCount);
    }
    if (FAILED(status) || *pageCount < 1) {
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_PAGE_COUNT",
            location,
            FAILED(status)
                ? FormatHResult(L"PageCount", status)
                : L"PageCount returned a value below one");
    }
    return true;
}

bool RunCheckpointAction(
    Context* const context,
    const wchar_t* const action,
    const std::wstring& location) {
    if (!RunAction(context->action, action, context->result, location)) {
        return false;
    }
    ++context->result->actionsExecuted;
    return true;
}

bool ReplaceDocumentBlock(
    Context* const context,
    const std::wstring& encodedBlock,
    const LONG expectedPageCount,
    const std::wstring& location) {
    if (!RunCheckpointAction(context, L"MoveDocBegin", location) ||
        !RunCheckpointAction(context, L"SelectAll", location) ||
        !RunCheckpointAction(context, L"Delete", location)) {
        return false;
    }
    CComVariant inserted;
    HRESULT status = Method(
        context->hwp,
        L"SetTextFile",
        {
            CComVariant(encodedBlock.c_str()),
            CComVariant(L"HWP"),
            CComVariant(L"insertfile"),
        },
        &inserted);
    bool insertedBlock = false;
    if (SUCCEEDED(status)) {
        status = AsBool(inserted, &insertedBlock);
    }
    if (FAILED(status) || !insertedBlock) {
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_INSERT",
            location,
            FAILED(status)
                ? FormatHResult(L"SetTextFile HWP insertfile", status)
                : L"SetTextFile returned false");
    }
    if (!RunCheckpointAction(context, L"MoveDocBegin", location)) {
        return false;
    }
    LONG pageCount = 0;
    if (!ReadPageCount(context, location, &pageCount)) {
        return false;
    }
    if (pageCount != expectedPageCount) {
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_PAGE_COUNT",
            location,
            L"restored document page count does not match the checkpoint");
    }
    return true;
}

bool RestoreDocumentFile(
    Context* const context,
    const std::wstring& pathText,
    const LONG expectedPageCount) {
    std::wstring targetBlock;
    if (!ReadEncodedBlockFile(context->result, pathText, &targetBlock)) {
        return false;
    }
    std::wstring rollbackBlock;
    LONG rollbackPageCount = 0;
    if (!CaptureDocumentBlock(context, pathText, &rollbackBlock) ||
        !ReadPageCount(context, pathText, &rollbackPageCount)) {
        return false;
    }

    ExecutionResult restoreResult;
    Context restore;
    restore.hwp = context->hwp;
    restore.action = context->action;
    restore.result = &restoreResult;
    if (ReplaceDocumentBlock(
            &restore,
            targetBlock,
            expectedPageCount,
            pathText)) {
        context->result->actionsExecuted += restoreResult.actionsExecuted;
        return true;
    }

    ExecutionResult rollbackResult;
    Context rollback;
    rollback.hwp = context->hwp;
    rollback.action = context->action;
    rollback.result = &rollbackResult;
    if (!ReplaceDocumentBlock(
            &rollback,
            rollbackBlock,
            rollbackPageCount,
            pathText)) {
        context->result->partialMutation = true;
        context->result->retrySafe = false;
        return SetError(
            context->result,
            L"DOCUMENT_CHECKPOINT_ROLLBACK",
            pathText,
            L"checkpoint restore failed and the previous document could not be restored: " +
                rollbackResult.error.message);
    }
    context->result->retrySafe = true;
    return SetError(
        context->result,
        L"DOCUMENT_CHECKPOINT_RESTORE",
        pathText,
        L"checkpoint restore failed and the previous document was restored: " +
            restoreResult.error.message);
}

}
