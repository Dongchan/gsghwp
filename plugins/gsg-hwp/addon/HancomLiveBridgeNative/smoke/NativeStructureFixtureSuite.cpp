#include "../ComState.h"
#include "../DispatchInvoke.h"
#include "../DocumentGraphEffectiveProperties.h"
#include "../DocumentGraphControls.h"
#include "../DocumentGraphImages.h"
#include "../DocumentGraphLayout.h"
#include "../LiveInspection.h"
#include "../DocumentGraphStories.h"
#include "../DocumentGraphTables.h"
#include "../DocumentGraphText.h"

#include <Windows.h>
#include <TlHelp32.h>
#include <atlcomcli.h>

#include <algorithm>
#include <array>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <new>
#include <set>
#include <sstream>
#include <string>
#include <vector>

namespace {

using hancom::dispatch::AsBool;
using hancom::dispatch::AsDispatch;
using hancom::dispatch::AsLong;
using hancom::dispatch::AsString;
using hancom::dispatch::Method;
using hancom::dispatch::PropertyGet;

constexpr std::array<const wchar_t*, 4> kFixtureNames{
    L"empty.hwp", L"full-spectrum.hwp", L"full-spectrum.hwpx",
    L"linked-image.png"};
constexpr std::array<const wchar_t*, 8> kReceiptNames{
    L"real-observation", L"deterministic-drift",
    L"fixture-idispatch-query-counts", L"synthetic-failure-injection",
    L"route-caret-selection-modified-restoration",
    L"save-clean-restoration-injections", L"owner-control-parity-injections",
    L"process-module-temp-fixture-hash-cleanup"};
constexpr wchar_t kFailureEnvironment[] =
    L"BRIDGE_SMOKE_NATIVE_STRUCTURE_FAILURE";

bool FailureRequested(const wchar_t* const stage) noexcept {
    std::array<wchar_t, 64> value{};
    const DWORD length = GetEnvironmentVariableW(
        kFailureEnvironment, value.data(), static_cast<DWORD>(value.size()));
    return length != 0 && length < value.size() &&
        wcscmp(value.data(), stage) == 0;
}

bool CanonicalPath(
    const std::filesystem::path& input,
    std::filesystem::path* const output) {
    if (output == nullptr || input.empty()) return false;
    std::error_code error;
    *output = std::filesystem::weakly_canonical(
        std::filesystem::absolute(input, error), error);
    return !error && !output->empty();
}

bool SamePathComponent(
    const std::filesystem::path& left,
    const std::filesystem::path& right) noexcept {
    return _wcsicmp(left.c_str(), right.c_str()) == 0;
}

bool IsSameOrAncestor(
    const std::filesystem::path& possibleAncestor,
    const std::filesystem::path& possibleDescendant) noexcept {
    auto ancestor = possibleAncestor.begin();
    auto descendant = possibleDescendant.begin();
    for (; ancestor != possibleAncestor.end(); ++ancestor, ++descendant) {
        if (descendant == possibleDescendant.end() ||
            !SamePathComponent(*ancestor, *descendant)) return false;
    }
    return true;
}

std::filesystem::path ReceiptPath(
    const std::filesystem::path& root,
    const wchar_t* const name) {
    return root / (std::wstring(name) + L".json");
}

std::filesystem::path ReceiptTemporaryPath(
    const std::filesystem::path& root,
    const wchar_t* const name) {
    return root / (std::wstring(name) + L".json.BridgeSmokeNativeStructure-" +
        std::to_wstring(GetCurrentProcessId()) + L".tmp");
}

bool IsOwnedReceiptTemporaryName(const std::filesystem::path& filename) {
    const std::wstring value = filename.native();
    constexpr wchar_t marker[] = L".json.BridgeSmokeNativeStructure-";
    constexpr wchar_t suffix[] = L".tmp";
    for (const wchar_t* const name : kReceiptNames) {
        const std::wstring prefix = std::wstring(name) + marker;
        if (value.size() > prefix.size() + std::size(suffix) - 1 &&
            value.compare(0, prefix.size(), prefix) == 0 &&
            value.compare(
                value.size() - (std::size(suffix) - 1),
                std::size(suffix) - 1, suffix) == 0) return true;
    }
    return false;
}

bool InvalidateOwnedReceipts(const std::filesystem::path& root) {
    std::error_code error;
    for (const wchar_t* const name : kReceiptNames) {
        const std::filesystem::path path = ReceiptPath(root, name);
        if (std::filesystem::exists(path, error)) {
            if (error || !std::filesystem::remove(path, error) || error) {
                return false;
            }
        } else if (error) {
            return false;
        }
    }
    std::vector<std::filesystem::path> ownedTemporaryFiles;
    std::filesystem::directory_iterator iterator(root, error);
    const std::filesystem::directory_iterator end;
    while (!error && iterator != end) {
        const std::filesystem::file_status status =
            iterator->symlink_status(error);
        if (!error &&
            (std::filesystem::is_regular_file(status) ||
             std::filesystem::is_symlink(status)) &&
            IsOwnedReceiptTemporaryName(iterator->path().filename())) {
            ownedTemporaryFiles.push_back(iterator->path());
        }
        iterator.increment(error);
    }
    if (error) return false;
    for (const std::filesystem::path& path : ownedTemporaryFiles) {
        if (!std::filesystem::remove(path, error) || error) return false;
    }
    return true;
}

std::string Utf8(const std::wstring& value) {
    if (value.empty()) return {};
    const int size = WideCharToMultiByte(
        CP_UTF8, WC_ERR_INVALID_CHARS, value.data(),
        static_cast<int>(value.size()), nullptr, 0, nullptr, nullptr);
    if (size <= 0) return {};
    std::string result(static_cast<size_t>(size), '\0');
    if (WideCharToMultiByte(
            CP_UTF8, WC_ERR_INVALID_CHARS, value.data(),
            static_cast<int>(value.size()), result.data(), size, nullptr,
            nullptr) != size) return {};
    return result;
}

std::string Json(const std::wstring& value) {
    const std::string utf8 = Utf8(value);
    std::ostringstream output;
    output << '"';
    for (const unsigned char character : utf8) {
        switch (character) {
        case '"': output << "\\\""; break;
        case '\\': output << "\\\\"; break;
        case '\b': output << "\\b"; break;
        case '\f': output << "\\f"; break;
        case '\n': output << "\\n"; break;
        case '\r': output << "\\r"; break;
        case '\t': output << "\\t"; break;
        default:
            if (character < 0x20) {
                output << "\\u" << std::hex << std::setw(4)
                       << std::setfill('0') << static_cast<unsigned>(character)
                       << std::dec;
            } else {
                output << character;
            }
        }
    }
    output << '"';
    return output.str();
}

std::wstring Sha256(const std::filesystem::path& path) {
    HANDLE file = CreateFileW(
        path.c_str(), GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
        OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return {};
    HCRYPTPROV provider = 0;
    HCRYPTHASH hash = 0;
    bool ok = CryptAcquireContextW(
                  &provider, nullptr, nullptr, PROV_RSA_AES,
                  CRYPT_VERIFYCONTEXT | CRYPT_SILENT) != FALSE &&
        CryptCreateHash(provider, CALG_SHA_256, 0, 0, &hash) != FALSE;
    std::array<BYTE, 64 * 1024> buffer{};
    while (ok) {
        DWORD count = 0;
        if (!ReadFile(file, buffer.data(), static_cast<DWORD>(buffer.size()),
                      &count, nullptr)) {
            ok = false;
            break;
        }
        if (count == 0) break;
        ok = CryptHashData(hash, buffer.data(), count, 0) != FALSE;
    }
    std::array<BYTE, 32> digest{};
    DWORD digestSize = static_cast<DWORD>(digest.size());
    ok = ok && CryptGetHashParam(
        hash, HP_HASHVAL, digest.data(), &digestSize, 0) != FALSE &&
        digestSize == digest.size();
    if (hash != 0) CryptDestroyHash(hash);
    if (provider != 0) CryptReleaseContext(provider, 0);
    CloseHandle(file);
    if (!ok) return {};
    constexpr wchar_t digits[] = L"0123456789abcdef";
    std::wstring result;
    result.reserve(64);
    for (const BYTE byte : digest) {
        result.push_back(digits[byte >> 4]);
        result.push_back(digits[byte & 0x0f]);
    }
    return result;
}

std::string ReadBytes(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary);
    return std::string(
        std::istreambuf_iterator<char>(input),
        std::istreambuf_iterator<char>());
}

bool ManifestFixtureProvenanceMatch(
    const std::string& manifest,
    const std::wstring& fixture,
    const std::uintmax_t byteCount,
    const std::wstring& sha256) {
    const std::string fixtureToken = "\"name\": " + Json(fixture);
    const size_t fixtureStart = manifest.find(fixtureToken);
    if (fixtureStart == std::string::npos) return false;
    const size_t fixtureEnd = manifest.find(
        "\"name\":", fixtureStart + fixtureToken.size());
    const auto isInFixture = [&](const std::string& token) {
        const size_t position = manifest.find(token, fixtureStart);
        return position != std::string::npos &&
            (fixtureEnd == std::string::npos || position < fixtureEnd);
    };
    return isInFixture(
               "\"bytes\": " + std::to_string(byteCount) + ",") &&
        isInFixture("\"sha256\": " + Json(sha256));
}

bool ManifestDescriptorMatch(const std::filesystem::path& fixtureRoot) {
    std::istringstream descriptor(
        ReadBytes(fixtureRoot / L"manifest.provenance"));
    std::string magic;
    std::string asset;
    std::string byteCountText;
    std::string expectedSha256;
    std::string extra;
    if (!std::getline(descriptor, magic) ||
        !std::getline(descriptor, asset) ||
        !std::getline(descriptor, byteCountText) ||
        !std::getline(descriptor, expectedSha256) ||
        std::getline(descriptor, extra) ||
        magic != "HWP_NATIVE_STRUCTURE_MANIFEST_V1" ||
        asset != "manifest.json" || expectedSha256.size() != 64) return false;

    std::uintmax_t parsedByteCount = 0;
    std::istringstream byteCount(byteCountText);
    if (!(byteCount >> parsedByteCount) || byteCount.get() != EOF ||
        std::to_string(parsedByteCount) != byteCountText) return false;

    const std::filesystem::path manifestPath = fixtureRoot / L"manifest.json";
    std::error_code error;
    const std::uintmax_t actualByteCount =
        std::filesystem::file_size(manifestPath, error);
    const std::wstring actualSha256 = error ? std::wstring{} : Sha256(manifestPath);
    return !error && actualByteCount == parsedByteCount &&
        !actualSha256.empty() && Utf8(actualSha256) == expectedSha256;
}

bool WriteReceipt(
    const std::filesystem::path& root,
    const wchar_t* name,
    const std::string& body) {
    const std::filesystem::path temporary = ReceiptTemporaryPath(root, name);
    const std::filesystem::path final = ReceiptPath(root, name);
    std::error_code error;
    std::filesystem::remove(temporary, error);
    error.clear();
    {
        std::ofstream output(
            temporary, std::ios::binary | std::ios::trunc);
        output << body << '\n';
        output.close();
        if (!output.good()) {
            std::filesystem::remove(temporary, error);
            return false;
        }
    }
    if (FailureRequested(L"receipt-publication") ||
        !MoveFileExW(
            temporary.c_str(), final.c_str(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        std::filesystem::remove(temporary, error);
        return false;
    }
    return true;
}

bool ResultBool(const HRESULT status, const CComVariant& value) {
    bool result = false;
    return SUCCEEDED(status) && SUCCEEDED(AsBool(value, &result)) && result;
}

class QueryCountingDispatch;
DWORD HwpProcessId(IDispatch* hwp);
constexpr IID kQueryCountingDispatchIid{
    0x5f7e4c1d, 0x8f74, 0x4c1f,
    {0xa5, 0x6b, 0x1e, 0x96, 0xc7, 0x2f, 0x4a, 0x31}};

struct DispatchQuery final {
    std::wstring lane;
    std::uint64_t objectId = 0;
    std::wstring member;
    DISPID dispid = DISPID_UNKNOWN;
    HRESULT getIdsStatus = E_UNEXPECTED;
    bool runtimeProvenance = false;
    std::uint64_t documentSessionId = 0;
    bool invoked = false;
    WORD flags = 0;
    HRESULT invokeStatus = E_UNEXPECTED;
};

struct DispatchTraceSnapshot final {
    std::vector<DispatchQuery> queries;
    bool routingMismatch = false;

    bool Valid() const noexcept {
        if (routingMismatch || queries.empty()) return false;
        for (const DispatchQuery& query : queries) {
            if (query.lane.empty() || query.objectId == 0 ||
                query.member.empty() || !query.runtimeProvenance ||
                query.documentSessionId == 0 ||
                (SUCCEEDED(query.getIdsStatus) != query.invoked)) return false;
        }
        return true;
    }

    std::wstring Canonical() const {
        std::map<std::wstring, std::map<std::uint64_t, std::uint64_t>> ordinals;
        std::wstring output;
        for (const DispatchQuery& query : queries) {
            auto& laneOrdinals = ordinals[query.lane];
            auto found = laneOrdinals.find(query.objectId);
            if (found == laneOrdinals.end()) {
                found = laneOrdinals.emplace(
                    query.objectId, laneOrdinals.size() + 1).first;
            }
            output += query.lane + L"|" + std::to_wstring(found->second) +
                L"|" + query.member + L"|" +
                std::to_wstring(query.dispid) + L"|" +
                std::to_wstring(static_cast<std::int32_t>(query.getIdsStatus)) +
                L"|" + std::to_wstring(query.invoked) + L"|" +
                std::to_wstring(query.flags) + L"|" +
                std::to_wstring(static_cast<std::int32_t>(query.invokeStatus)) +
                L"\n";
        }
        return output;
    }

    std::uint64_t InvokeCount() const noexcept {
        return static_cast<std::uint64_t>(std::count_if(
            queries.begin(), queries.end(),
            [](const DispatchQuery& query) { return query.invoked; }));
    }

    bool HasLane(const std::wstring& lane) const noexcept {
        return std::any_of(
            queries.begin(), queries.end(),
            [&lane](const DispatchQuery& query) { return query.lane == lane; });
    }

    bool HasCalledLane(const std::wstring& lane) const noexcept {
        return std::any_of(
            queries.begin(), queries.end(),
            [&lane](const DispatchQuery& query) {
                return query.invoked && query.lane == lane;
            });
    }
};

class DispatchQueryCounter final
    : public std::enable_shared_from_this<DispatchQueryCounter> {
public:
    void BeginPass() noexcept {
        queries_.clear();
        activeLane_.clear();
        routingMismatch_ = false;
    }

    void EnterLane(const std::wstring& lane) noexcept {
        if (!activeLane_.empty() || lane.empty() || !SessionStillActive()) {
            routingMismatch_ = true;
            return;
        }
        try {
            activeLane_ = lane;
        } catch (...) {
            routingMismatch_ = true;
        }
    }

    void LeaveLane() noexcept {
        if (activeLane_.empty()) routingMismatch_ = true;
        activeLane_.clear();
    }

    void RecordGetIds(
        const std::uint64_t objectId,
        REFIID iid,
        LPOLESTR* const names,
        const UINT count,
        const LCID,
        DISPID* const members,
        const HRESULT status) noexcept {
        if (activeLane_.empty()) return;
        if (iid != IID_NULL || names == nullptr || count != 1 ||
            names[0] == nullptr || members == nullptr) {
            routingMismatch_ = true;
            return;
        }
        try {
            DispatchQuery query;
            query.lane = activeLane_;
            query.objectId = objectId;
            query.member = names[0];
            query.dispid = SUCCEEDED(status) ? members[0] : DISPID_UNKNOWN;
            query.getIdsStatus = status;
            query.runtimeProvenance = HasRuntimeProvenance(objectId);
            query.documentSessionId = query.runtimeProvenance
                ? activeDocumentSessionId_ : 0;
            if (!query.runtimeProvenance) ++untrustedDispatchCount_;
            queries_.push_back(std::move(query));
        } catch (...) {
            routingMismatch_ = true;
        }
    }

    void RecordInvoke(
        const std::uint64_t objectId,
        const DISPID member,
        REFIID iid,
        const WORD flags,
        const HRESULT status) noexcept {
        if (activeLane_.empty()) return;
        if (iid != IID_NULL || queries_.empty()) {
            routingMismatch_ = true;
            return;
        }
        DispatchQuery& query = queries_.back();
        if (query.lane != activeLane_ || query.objectId != objectId ||
            query.dispid != member || FAILED(query.getIdsStatus) ||
            query.invoked) {
            routingMismatch_ = true;
            return;
        }
        query.invoked = true;
        query.flags = flags;
        query.invokeStatus = status;
    }

    DispatchTraceSnapshot Snapshot() const {
        return {queries_, routingMismatch_ || !activeLane_.empty()};
    }

    size_t WrapperCount() const noexcept { return wrappers_.size(); }
    bool RuntimeRootActivated() const noexcept {
        return runtimeRootObjectId_ != 0 && runtimeProcessId_ != 0;
    }
    std::uint64_t UntrustedDispatchCount() const noexcept {
        return untrustedDispatchCount_;
    }
    std::uint64_t ActiveDocumentSessionId() const noexcept {
        return activeDocumentSessionId_;
    }

    HRESULT ActivateRuntimeRoot(
        IDispatch* value,
        DWORD processId,
        HANDLE process,
        IDispatch** output) noexcept;
    bool ActivateDocumentSession(
        IDispatch* runtimeRoot,
        const std::filesystem::path& expectedPath) noexcept;
    void EndDocumentSession() noexcept;
    bool ValidateDispatchForActiveSession(IDispatch* value) const noexcept;
    bool ResolveCurrentRuntimeDocument(IDispatch** document) const noexcept;
    bool ValidateObservedDocumentForActiveSession(
        IDispatch* document) const noexcept;
    bool ValidateDispatchForObservedSession(
        IDispatch* value,
        IDispatch* observedDocument) const noexcept;
    HRESULT WrapReturned(
        IDispatch* value,
        const std::uint64_t parentObjectId,
        IDispatch** output) noexcept {
        return Wrap(value, false, parentObjectId, output);
    }
    void RemoveWrapper(
        std::uintptr_t identity,
        QueryCountingDispatch* wrapper) noexcept;

private:
    struct DispatchOrigin final {
        std::uint64_t parentObjectId = 0;
        std::uint64_t documentSessionId = 0;
        bool activatedRoot = false;
    };

    bool SessionStillActive() const noexcept;

    bool HasRuntimeProvenance(std::uint64_t objectId) const noexcept {
        for (size_t depth = 0; objectId != 0 && depth <= origins_.size();
             ++depth) {
            const auto found = origins_.find(objectId);
            if (found == origins_.end() || activeDocumentSessionId_ == 0 ||
                found->second.documentSessionId != activeDocumentSessionId_) {
                return false;
            }
            if (found->second.activatedRoot) {
                return objectId == runtimeRootObjectId_ &&
                    runtimeProcessId_ != 0;
            }
            objectId = found->second.parentObjectId;
        }
        return false;
    }

    HRESULT Wrap(
        IDispatch* value,
        bool activatedRoot,
        std::uint64_t parentObjectId,
        IDispatch** output) noexcept;

    std::wstring activeLane_;
    std::vector<DispatchQuery> queries_;
    bool routingMismatch_ = false;
    std::map<std::uintptr_t, QueryCountingDispatch*> wrappers_;
    std::map<std::uint64_t, DispatchOrigin> origins_;
    std::uint64_t nextObjectId_ = 1;
    std::uint64_t runtimeRootObjectId_ = 0;
    std::uint64_t nextDocumentSessionId_ = 1;
    std::uint64_t activeDocumentSessionId_ = 0;
    CComPtr<IUnknown> activeDocumentIdentity_;
    std::filesystem::path activeDocumentPath_;
    DWORD runtimeProcessId_ = 0;
    std::uint64_t untrustedDispatchCount_ = 0;
};

class QueryLane final {
public:
    QueryLane(
        const std::shared_ptr<DispatchQueryCounter>& counter,
        std::wstring lane) noexcept
        : counter_(counter) {
        if (counter_ != nullptr) counter_->EnterLane(lane);
    }

    ~QueryLane() noexcept {
        if (counter_ != nullptr) counter_->LeaveLane();
    }

private:
    std::shared_ptr<DispatchQueryCounter> counter_;
};

class QueryCountingDispatch final : public IDispatch {
public:
    QueryCountingDispatch(
        IDispatch* const value,
        std::shared_ptr<DispatchQueryCounter> counter,
        const std::uintptr_t identity,
        const std::uint64_t objectId) noexcept
        : value_(value), counter_(std::move(counter)), identity_(identity),
          objectId_(objectId) {}

    ~QueryCountingDispatch() noexcept {
        counter_->RemoveWrapper(identity_, this);
    }

    HRESULT STDMETHODCALLTYPE QueryInterface(
        REFIID iid,
        void** output) noexcept override {
        if (output == nullptr) return E_POINTER;
        *output = nullptr;
        if (iid != IID_IUnknown && iid != IID_IDispatch &&
            iid != kQueryCountingDispatchIid) return E_NOINTERFACE;
        *output = static_cast<IDispatch*>(this);
        AddRef();
        return S_OK;
    }

    ULONG STDMETHODCALLTYPE AddRef() noexcept override {
        return static_cast<ULONG>(InterlockedIncrement(&references_));
    }

    ULONG STDMETHODCALLTYPE Release() noexcept override {
        const LONG remaining = InterlockedDecrement(&references_);
        if (remaining == 0) delete this;
        return static_cast<ULONG>(remaining);
    }

    HRESULT STDMETHODCALLTYPE GetTypeInfoCount(UINT* count) noexcept override {
        return value_->GetTypeInfoCount(count);
    }

    HRESULT STDMETHODCALLTYPE GetTypeInfo(
        UINT index,
        LCID lcid,
        ITypeInfo** info) noexcept override {
        return value_->GetTypeInfo(index, lcid, info);
    }

    HRESULT STDMETHODCALLTYPE GetIDsOfNames(
        REFIID iid,
        LPOLESTR* names,
        UINT count,
        LCID lcid,
        DISPID* members) noexcept override {
        const HRESULT status = value_->GetIDsOfNames(
            iid, names, count, lcid, members);
        counter_->RecordGetIds(
            objectId_, iid, names, count, lcid, members, status);
        return status;
    }

    HRESULT STDMETHODCALLTYPE Invoke(
        DISPID member,
        REFIID iid,
        LCID lcid,
        WORD flags,
        DISPPARAMS* parameters,
        VARIANT* result,
        EXCEPINFO* exception,
        UINT* argumentError) noexcept override {
        std::vector<std::pair<VARIANTARG*, IDispatch*>> replaced;
        try {
            if (parameters != nullptr) {
                replaced.reserve(parameters->cArgs);
                for (UINT index = 0; index < parameters->cArgs; ++index) {
                    VARIANTARG* const argument = &parameters->rgvarg[index];
                    if (argument->vt != VT_DISPATCH ||
                        argument->pdispVal == nullptr) continue;
                    auto* const wrapper = dynamic_cast<QueryCountingDispatch*>(
                        argument->pdispVal);
                    if (wrapper == nullptr) continue;
                    replaced.emplace_back(argument, argument->pdispVal);
                    argument->pdispVal = wrapper->value_;
                }
            }
        } catch (...) {
            for (auto iterator = replaced.rbegin(); iterator != replaced.rend();
                 ++iterator) iterator->first->pdispVal = iterator->second;
            counter_->RecordInvoke(objectId_, member, iid, flags, E_OUTOFMEMORY);
            return E_OUTOFMEMORY;
        }
        HRESULT status = value_->Invoke(
            member, iid, lcid, flags, parameters, result, exception,
            argumentError);
        for (auto iterator = replaced.rbegin(); iterator != replaced.rend();
             ++iterator) iterator->first->pdispVal = iterator->second;
        IDispatch* returnedDispatch = nullptr;
        CComPtr<IDispatch> unknownDispatch;
        if (SUCCEEDED(status) && result != nullptr) {
            if (result->vt == VT_DISPATCH && result->pdispVal != nullptr) {
                returnedDispatch = result->pdispVal;
            } else if (result->vt == VT_UNKNOWN && result->punkVal != nullptr &&
                SUCCEEDED(result->punkVal->QueryInterface(
                    IID_IDispatch,
                    reinterpret_cast<void**>(&unknownDispatch)))) {
                returnedDispatch = unknownDispatch;
            }
        }
        if (returnedDispatch != nullptr) {
            IDispatch* wrapped = nullptr;
            const HRESULT wrapStatus = counter_->WrapReturned(
                returnedDispatch, objectId_, &wrapped);
            if (SUCCEEDED(wrapStatus)) {
                if (result->vt == VT_UNKNOWN) {
                    result->punkVal->Release();
                    result->vt = VT_DISPATCH;
                } else {
                    result->pdispVal->Release();
                }
                result->pdispVal = wrapped;
            } else {
                status = wrapStatus;
            }
        }
        counter_->RecordInvoke(objectId_, member, iid, flags, status);
        return status;
    }

private:
    friend class DispatchQueryCounter;
    LONG references_ = 1;
    CComPtr<IDispatch> value_;
    std::shared_ptr<DispatchQueryCounter> counter_;
    std::uintptr_t identity_ = 0;
    std::uint64_t objectId_ = 0;
};

HRESULT DispatchQueryCounter::Wrap(
    IDispatch* const value,
    const bool activatedRoot,
    const std::uint64_t parentObjectId,
    IDispatch** const output) noexcept {
    if (value == nullptr || output == nullptr) return E_POINTER;
    *output = nullptr;
    CComPtr<IUnknown> identity;
    const HRESULT identityStatus = value->QueryInterface(
        IID_IUnknown, reinterpret_cast<void**>(&identity));
    if (FAILED(identityStatus) || identity == nullptr) return identityStatus;
    const std::uintptr_t key = reinterpret_cast<std::uintptr_t>(identity.p);
    const auto found = wrappers_.find(key);
    if (found != wrappers_.end()) {
        if (activatedRoot || !HasRuntimeProvenance(found->second->objectId_)) {
            return E_UNEXPECTED;
        }
        found->second->AddRef();
        *output = found->second;
        return S_OK;
    }
    if (nextObjectId_ == (std::numeric_limits<std::uint64_t>::max)() ||
        (!activatedRoot &&
         (parentObjectId == 0 || !HasRuntimeProvenance(parentObjectId))) ||
        (activatedRoot && runtimeRootObjectId_ != 0)) {
        return E_UNEXPECTED;
    }
    const std::uint64_t objectId = nextObjectId_++;
    auto* const wrapper = new (std::nothrow) QueryCountingDispatch(
        value, shared_from_this(), key, objectId);
    if (wrapper == nullptr) return E_OUTOFMEMORY;
    try {
        origins_.emplace(
            objectId,
            DispatchOrigin{
                parentObjectId,
                activatedRoot ? 0 : activeDocumentSessionId_,
                activatedRoot});
        wrappers_.emplace(key, wrapper);
    } catch (...) {
        origins_.erase(objectId);
        delete wrapper;
        return E_OUTOFMEMORY;
    }
    if (activatedRoot) runtimeRootObjectId_ = objectId;
    *output = wrapper;
    return S_OK;
}

HRESULT DispatchQueryCounter::ActivateRuntimeRoot(
    IDispatch* const value,
    const DWORD processId,
    HANDLE const process,
    IDispatch** const output) noexcept {
    if (value == nullptr || output == nullptr || processId == 0 ||
        process == nullptr || GetProcessId(process) != processId ||
        WaitForSingleObject(process, 0) != WAIT_TIMEOUT ||
        HwpProcessId(value) != processId) {
        return E_ACCESSDENIED;
    }
    const HRESULT status = Wrap(value, true, 0, output);
    if (SUCCEEDED(status)) runtimeProcessId_ = processId;
    return status;
}

bool DispatchQueryCounter::ActivateDocumentSession(
    IDispatch* const runtimeRoot,
    const std::filesystem::path& expectedPath) noexcept {
    QueryCountingDispatch* wrapper = nullptr;
    if (runtimeRoot == nullptr || FAILED(runtimeRoot->QueryInterface(
            kQueryCountingDispatchIid,
            reinterpret_cast<void**>(&wrapper))) || wrapper == nullptr) {
        return false;
    }
    const auto release = [&wrapper]() noexcept { wrapper->Release(); };
    if (wrapper->counter_.get() != this ||
        wrapper->objectId_ != runtimeRootObjectId_ || expectedPath.empty() ||
        runtimeProcessId_ == 0 || activeDocumentSessionId_ != 0) {
        release();
        return false;
    }
    CComVariant documentsRaw, documentRaw, fullNameRaw;
    CComPtr<IDispatch> documents, document;
    std::wstring fullName;
    if (FAILED(PropertyGet(wrapper->value_, L"XHwpDocuments", &documentsRaw)) ||
        FAILED(AsDispatch(documentsRaw, documents)) ||
        FAILED(PropertyGet(documents, L"Active_XHwpDocument", &documentRaw)) ||
        FAILED(AsDispatch(documentRaw, document)) ||
        FAILED(PropertyGet(document, L"FullName", &fullNameRaw)) ||
        FAILED(AsString(fullNameRaw, &fullName)) || fullName.empty()) {
        release();
        return false;
    }
    std::error_code error;
    const std::filesystem::path actual =
        std::filesystem::weakly_canonical(fullName, error);
    if (error) {
        release();
        return false;
    }
    const std::filesystem::path expected =
        std::filesystem::weakly_canonical(expectedPath, error);
    if (error || _wcsicmp(actual.c_str(), expected.c_str()) != 0) {
        release();
        return false;
    }
    CComPtr<IUnknown> identity;
    if (FAILED(document->QueryInterface(
            IID_IUnknown, reinterpret_cast<void**>(&identity))) ||
        identity == nullptr ||
        nextDocumentSessionId_ ==
            (std::numeric_limits<std::uint64_t>::max)()) {
        release();
        return false;
    }
    activeDocumentIdentity_ = identity;
    activeDocumentPath_ = actual;
    activeDocumentSessionId_ = nextDocumentSessionId_++;
    origins_[runtimeRootObjectId_].documentSessionId = activeDocumentSessionId_;
    release();
    return true;
}

void DispatchQueryCounter::EndDocumentSession() noexcept {
    activeDocumentSessionId_ = 0;
    activeDocumentIdentity_.Release();
    activeDocumentPath_.clear();
    const auto root = origins_.find(runtimeRootObjectId_);
    if (root != origins_.end()) root->second.documentSessionId = 0;
}

bool DispatchQueryCounter::ResolveCurrentRuntimeDocument(
    IDispatch** const output) const noexcept {
    if (output == nullptr) return false;
    *output = nullptr;
    QueryCountingDispatch* root = nullptr;
    for (const auto& entry : wrappers_) {
        if (entry.second != nullptr &&
            entry.second->objectId_ == runtimeRootObjectId_) {
            root = entry.second;
            break;
        }
    }
    CComVariant documentsRaw, documentRaw;
    CComPtr<IDispatch> documents, document;
    if (root == nullptr || FAILED(PropertyGet(
            root->value_, L"XHwpDocuments", &documentsRaw)) ||
        FAILED(AsDispatch(documentsRaw, documents)) ||
        FAILED(PropertyGet(
            documents, L"Active_XHwpDocument", &documentRaw)) ||
        FAILED(AsDispatch(documentRaw, document))) {
        return false;
    }
    *output = document.Detach();
    return true;
}

bool DispatchQueryCounter::ValidateObservedDocumentForActiveSession(
    IDispatch* const document) const noexcept {
    if (document == nullptr || activeDocumentSessionId_ == 0 ||
        activeDocumentIdentity_ == nullptr || activeDocumentPath_.empty()) {
        return false;
    }
    CComPtr<IUnknown> identity;
    CComVariant fullNameRaw;
    std::wstring fullName;
    if (FAILED(document->QueryInterface(
            IID_IUnknown, reinterpret_cast<void**>(&identity))) ||
        identity == nullptr || identity.p != activeDocumentIdentity_.p ||
        FAILED(PropertyGet(document, L"FullName", &fullNameRaw)) ||
        FAILED(AsString(fullNameRaw, &fullName))) return false;
    std::error_code error;
    const std::filesystem::path current =
        std::filesystem::weakly_canonical(fullName, error);
    return !error &&
        _wcsicmp(current.c_str(), activeDocumentPath_.c_str()) == 0;
}

bool DispatchQueryCounter::SessionStillActive() const noexcept {
    if (activeDocumentSessionId_ == 0 || activeDocumentIdentity_ == nullptr ||
        runtimeProcessId_ == 0) return false;
    QueryCountingDispatch* root = nullptr;
    for (const auto& entry : wrappers_) {
        if (entry.second != nullptr &&
            entry.second->objectId_ == runtimeRootObjectId_) {
            root = entry.second;
            break;
        }
    }
    if (root == nullptr || HwpProcessId(root->value_) != runtimeProcessId_) {
        return false;
    }
    CComPtr<IDispatch> document;
    IDispatch* raw = nullptr;
    if (!ResolveCurrentRuntimeDocument(&raw)) return false;
    document.Attach(raw);
    return ValidateObservedDocumentForActiveSession(document);
}

bool DispatchQueryCounter::ValidateDispatchForActiveSession(
    IDispatch* const value) const noexcept {
    QueryCountingDispatch* wrapper = nullptr;
    if (value == nullptr || FAILED(value->QueryInterface(
            kQueryCountingDispatchIid,
            reinterpret_cast<void**>(&wrapper))) || wrapper == nullptr) {
        return false;
    }
    const bool valid = wrapper->counter_.get() == this &&
        SessionStillActive() && HasRuntimeProvenance(wrapper->objectId_);
    wrapper->Release();
    return valid;
}

bool DispatchQueryCounter::ValidateDispatchForObservedSession(
    IDispatch* const value,
    IDispatch* const observedDocument) const noexcept {
    QueryCountingDispatch* wrapper = nullptr;
    if (!ValidateObservedDocumentForActiveSession(observedDocument) ||
        value == nullptr || FAILED(value->QueryInterface(
            kQueryCountingDispatchIid,
            reinterpret_cast<void**>(&wrapper))) || wrapper == nullptr) {
        return false;
    }
    const bool valid = wrapper->counter_.get() == this &&
        HasRuntimeProvenance(wrapper->objectId_);
    wrapper->Release();
    return valid;
}

void DispatchQueryCounter::RemoveWrapper(
    const std::uintptr_t identity,
    QueryCountingDispatch* const wrapper) noexcept {
    const auto found = wrappers_.find(identity);
    if (found != wrappers_.end() && found->second == wrapper) {
        wrappers_.erase(found);
    }
}

struct StorySink final
    : hancom::graph::stories::DocumentGraphStorySink {
    std::vector<hancom::graph::stories::NativeControlRecord> controls;
    hancom::graph::ObservationV1<std::int64_t> bodyList{};
    std::uint64_t sections = 0;
    bool committed = false;
    bool BeginBodyStory() noexcept override { return true; }
    bool BeginBodyStory(
        const hancom::graph::ObservationV1<std::int64_t>& value)
        noexcept override {
        bodyList = value;
        return true;
    }
    bool AppendSection(
        const hancom::graph::stories::NativeSectionRecord&) noexcept override {
        ++sections;
        return true;
    }
    bool AppendControl(
        const hancom::graph::stories::NativeControlRecord& value) noexcept override {
        try { controls.push_back(value); } catch (...) { return false; }
        return true;
    }
    bool MarkChildContainmentNotExposed(
        const hancom::graph::stories::NativeControlRecord&) noexcept override {
        return true;
    }
    bool Commit() noexcept override { committed = true; return true; }
    void Abort() noexcept override { committed = false; }
};

struct TextSink final : hancom::graph::text::DocumentGraphTextSink {
    std::uint64_t paragraphs = 0;
    std::uint64_t atoms = 0;
    std::uint64_t runs = 0;
    std::uint64_t textCodeUnits = 0;
    std::vector<hancom::graph::text::NativeTextRunRecord> records;
    bool committed = false;
    bool BeginParagraph(
        const hancom::graph::text::NativeParagraphRecord&) noexcept override {
        return true;
    }
    bool AppendAtom(
        const hancom::graph::text::NativeTextAtomRecord& value) noexcept override {
        ++atoms; textCodeUnits += value.text.size(); return true;
    }
    bool AppendRun(
        const hancom::graph::text::NativeTextRunRecord& value) noexcept override {
        try {
            records.push_back(value);
        } catch (...) {
            return false;
        }
        ++runs; textCodeUnits += value.text.size(); return true;
    }
    bool MarkRunShapeNotExposed(
        const hancom::graph::text::NativeTextRunRecord&) noexcept override {
        return true;
    }
    bool MarkParagraphCoverage(
        const hancom::graph::text::NativeParagraphCoverage&) noexcept override {
        ++paragraphs; return true;
    }
    bool Commit() noexcept override { committed = true; return true; }
    void Abort() noexcept override { committed = false; }
};

struct PropertySink final
    : hancom::graph::properties::EffectivePropertySink {
    std::uint64_t values = 0;
    std::uint64_t observations = 0;
    bool committed = false;
    bool Append(
        const hancom::graph::properties::PropertyObservation& value) noexcept override {
        ++observations;
        if (value.status == hancom::graph::properties::ReadStatus::Value) ++values;
        return true;
    }
    bool MarkCatalogCoverage(
        const hancom::graph::properties::CatalogCoverage&) noexcept override {
        return true;
    }
    bool Commit() noexcept override { committed = true; return true; }
    void Abort() noexcept override { committed = false; }
};

struct ReadState final {
    hancom::com_state::DocumentRoute route;
    hancom::com_state::Position cursor;
    hancom::com_state::Selection selection;
    bool modified = false;
};

bool CaptureState(IDispatch* hwp, ReadState* state) {
    CComVariant modified;
    hancom::com_state::SelectionCaptureFailure failure;
    return state != nullptr &&
        hancom::com_state::CaptureDocumentRoute(hwp, &state->route) &&
        SUCCEEDED(hancom::com_state::CapturePosition(hwp, &state->cursor)) &&
        hancom::com_state::CaptureSelection(
            hwp, &state->selection,
            hancom::com_state::SelectionCapturePolicy::BestEffortControl,
            &failure) &&
        hancom::com_state::CanRestoreSelection(state->selection) &&
        SUCCEEDED(PropertyGet(hwp, L"IsModified", &modified)) &&
        SUCCEEDED(AsBool(modified, &state->modified));
}

bool RestoreAndVerifyState(IDispatch* hwp, const ReadState& state) {
    if (!hancom::com_state::RestoreDocumentRoute(hwp, state.route) ||
        !hancom::com_state::RestoreSelection(hwp, state.cursor, state.selection) ||
        !hancom::com_state::VerifyDocumentRoute(hwp, state.route)) return false;
    ReadState restored;
    return CaptureState(hwp, &restored) &&
        restored.route.documentId == state.route.documentId &&
        hancom::com_state::SamePosition(restored.cursor, state.cursor) &&
        hancom::com_state::SameSelection(restored.selection, state.selection) &&
        !state.modified && !restored.modified;
}

enum class SaveCleanRestorationInjection : std::uint8_t {
    None = 0,
    AfterStateCapture,
    AfterStories,
    AfterBodyText,
    AfterEffectiveProperties,
    AfterOwnerControlBinding,
    AfterTableReader,
    AfterLayoutReader,
    AfterImageReader,
    AfterChildReaders,
};

class ReadStateRestoreGuard final {
public:
    ReadStateRestoreGuard(IDispatch* const hwp, const ReadState& state) noexcept
        : hwp_(hwp), state_(state) {}

    ~ReadStateRestoreGuard() noexcept {
        if (active_) static_cast<void>(RestoreAndVerifyState(hwp_, state_));
    }

    bool Restore() noexcept {
        active_ = false;
        return RestoreAndVerifyState(hwp_, state_);
    }

private:
    IDispatch* const hwp_;
    ReadState state_;
    bool active_ = true;
};

struct ImageRelationshipObservation final {
    unsigned storageState = 0;
    unsigned storage = 0;
    unsigned sourceNameState = 0;
    std::wstring sourceName;
    unsigned binaryContentState = 0;
    std::uint64_t byteLength = 0;
    std::wstring sha256;
    std::wstring ctrlId;
    std::wstring stableIdentity;

    std::wstring Canonical() const {
        return std::to_wstring(storageState) + L":" +
            std::to_wstring(storage) + L":" +
            std::to_wstring(sourceNameState) + L":" + sourceName + L":" +
            std::to_wstring(binaryContentState) + L":" +
            std::to_wstring(byteLength) + L":" + sha256 + L":" + ctrlId +
            L":" + stableIdentity;
    }
};

enum class ChildOwnerKind : std::uint8_t {
    None = 0,
    Table,
    Image,
};

struct OwnerControlParityObservation final {
    std::uint64_t headCtrlOrdinal = 0;
    std::wstring nativeTypeId{};
    std::wstring sessionInstanceId{};
    ChildOwnerKind childKind = ChildOwnerKind::None;
    std::wstring childNativeTypeId{};
    std::wstring childSessionInstanceId{};
};

bool VerifyOwnerControlParity(
    const std::vector<hancom::graph::stories::NativeControlRecord>& owners,
    const std::vector<OwnerControlParityObservation>& observations) noexcept {
    if (owners.size() != observations.size()) return false;
    try {
        for (size_t index = 0; index < owners.size(); ++index) {
            hancom::graph::controls::AdaptedControlRecord owner;
            if (!hancom::graph::controls::AdaptNativeControl(
                    owners[index], &owner)) return false;
            const OwnerControlParityObservation& observed = observations[index];
            if (owner.headCtrlOrdinal != observed.headCtrlOrdinal ||
                owner.nativeTypeId != observed.nativeTypeId ||
                owner.sessionInstanceId != observed.sessionInstanceId) {
                return false;
            }
            if (observed.childKind == ChildOwnerKind::Table) {
                if (owner.kind != hancom::graph::controls::ControlKind::Table ||
                    !observed.childNativeTypeId.empty() ||
                    observed.childSessionInstanceId !=
                        owner.sessionInstanceId) return false;
            } else if (observed.childKind == ChildOwnerKind::Image) {
                if (owner.kind !=
                        hancom::graph::controls::ControlKind::GeneralShape ||
                    observed.childNativeTypeId != owner.nativeTypeId ||
                    observed.childSessionInstanceId !=
                        owner.sessionInstanceId) return false;
            } else if (observed.childKind != ChildOwnerKind::None) {
                return false;
            }
        }
        return true;
    } catch (...) {
        return false;
    }
}

struct OwnerControlParityInjectionSummary final {
    bool baselineAccepted = false;
    bool realTableControlPresent = false;
    bool realImageControlPresent = false;
    std::array<bool, 6> mismatchRejected{};
    std::uint64_t rejectedMismatchCount = 0;
    std::uint64_t expectedMismatchCount = mismatchRejected.size();

    bool Passed() const noexcept {
        return baselineAccepted && realTableControlPresent &&
            realImageControlPresent &&
            rejectedMismatchCount == expectedMismatchCount;
    }
};

enum class OwnerControlParityInjection : std::uint8_t {
    OwnerControlCount = 0,
    HeadCtrlOrdinal,
    NativeType,
    SessionInstanceId,
    TableChildOwner,
    ImageChildOwner,
};

OwnerControlParityInjectionSummary
RunOwnerControlParityMismatchInjections() noexcept {
    OwnerControlParityInjectionSummary summary;
    try {
        std::vector<hancom::graph::stories::NativeControlRecord> owners(2);
        owners[0].ctrlId = L"tbl";
        owners[0].instanceId = L"table-owner";
        owners[0].headCtrlOrdinal = 0;
        owners[1].ctrlId = L"gso";
        owners[1].instanceId = L"image-owner";
        owners[1].headCtrlOrdinal = 1;
        std::vector<OwnerControlParityObservation> baseline(2);
        baseline[0] = {
            0, L"tbl", L"table-owner", ChildOwnerKind::Table, {},
            L"table-owner"};
        baseline[1] = {
            1, L"gso", L"image-owner", ChildOwnerKind::Image, L"gso",
            L"image-owner"};
        summary.baselineAccepted = VerifyOwnerControlParity(owners, baseline);
        constexpr std::array<OwnerControlParityInjection, 6> injections{
            OwnerControlParityInjection::OwnerControlCount,
            OwnerControlParityInjection::HeadCtrlOrdinal,
            OwnerControlParityInjection::NativeType,
            OwnerControlParityInjection::SessionInstanceId,
            OwnerControlParityInjection::TableChildOwner,
            OwnerControlParityInjection::ImageChildOwner,
        };
        for (const OwnerControlParityInjection injection : injections) {
            std::vector<OwnerControlParityObservation> mutated = baseline;
            switch (injection) {
            case OwnerControlParityInjection::OwnerControlCount:
                mutated.pop_back();
                break;
            case OwnerControlParityInjection::HeadCtrlOrdinal:
                mutated[0].headCtrlOrdinal = 1;
                break;
            case OwnerControlParityInjection::NativeType:
                mutated[0].nativeTypeId = L"gso";
                break;
            case OwnerControlParityInjection::SessionInstanceId:
                mutated[0].sessionInstanceId = L"different-owner";
                break;
            case OwnerControlParityInjection::TableChildOwner:
                mutated[0].childSessionInstanceId = L"different-owner";
                break;
            case OwnerControlParityInjection::ImageChildOwner:
                mutated[1].childSessionInstanceId = L"different-owner";
                break;
            }
            const bool rejected =
                !VerifyOwnerControlParity(owners, mutated);
            summary.mismatchRejected[static_cast<size_t>(injection)] = rejected;
            if (rejected) ++summary.rejectedMismatchCount;
        }
    } catch (...) {
        return summary;
    }
    return summary;
}

struct Observation final {
    std::wstring label;
    std::uint64_t sections = 0;
    std::uint64_t controls = 0;
    std::uint64_t paragraphs = 0;
    std::uint64_t atoms = 0;
    std::uint64_t runs = 0;
    std::uint64_t textCodeUnits = 0;
    std::uint64_t propertyValues = 0;
    std::uint64_t characterShapeReferences = 0;
    std::uint64_t characterShapeDefinitions = 0;
    std::uint64_t observedCharacterShapeBodies = 0;
    std::wstring characterShapeDigest;
    std::uint64_t adaptedControls = 0;
    bool ownerControlParity = false;
    std::uint64_t bodyStoryNativeLists = 0;
    std::uint64_t tableAttempts = 0;
    std::uint64_t tables = 0;
    std::uint64_t cellStoryNativeLists = 0;
    std::uint64_t imageAttempts = 0;
    std::uint64_t images = 0;
    std::uint64_t captionStoryNativeLists = 0;
    std::uint64_t tableCaptionsPresent = 0;
    std::uint64_t tableCaptionsAbsent = 0;
    std::uint64_t tableCaptionsUnavailable = 0;
    std::uint64_t tableCells = 0;
    std::uint64_t legacyPagesInspected = 0;
    std::uint64_t legacyControls = 0;
    std::uint64_t controlsWithoutLegacyPageObservation = 0;
    std::uint64_t legacyCells = 0;
    std::uint64_t legacyCaptions = 0;
    std::uint64_t legacyParagraphs = 0;
    bool legacyGraphAllPageEquivalent = false;
    std::uint64_t linkedAssets = 0;
    std::vector<ImageRelationshipObservation> imageRelationships;
    std::uint64_t layoutAttempts = 0;
    std::uint64_t stableLayouts = 0;
    bool stateRestored = false;
    bool expectationMatched = false;

    std::wstring Canonical() const {
        std::wstring result =
            std::to_wstring(sections) + L":" + std::to_wstring(controls) +
            L":" + std::to_wstring(paragraphs) + L":" +
            std::to_wstring(atoms) + L":" + std::to_wstring(runs) + L":" +
            std::to_wstring(textCodeUnits) + L":" +
            std::to_wstring(propertyValues) + L":" +
            std::to_wstring(characterShapeReferences) + L":" +
            std::to_wstring(characterShapeDefinitions) + L":" +
            std::to_wstring(observedCharacterShapeBodies) + L":" +
            characterShapeDigest + L":" +
            std::to_wstring(adaptedControls) + L":" +
            std::to_wstring(ownerControlParity) + L":" +
            std::to_wstring(bodyStoryNativeLists) + L":" +
            std::to_wstring(tableAttempts) + L":" + std::to_wstring(tables) +
            L":" + std::to_wstring(cellStoryNativeLists) + L":" +
            std::to_wstring(imageAttempts) + L":" +
            std::to_wstring(captionStoryNativeLists) + L":" +
            std::to_wstring(tableCaptionsPresent) + L":" +
            std::to_wstring(tableCaptionsAbsent) + L":" +
            std::to_wstring(tableCaptionsUnavailable) + L":" +
            std::to_wstring(tableCells) + L":" +
            std::to_wstring(legacyPagesInspected) + L":" +
            std::to_wstring(legacyControls) + L":" +
            std::to_wstring(controlsWithoutLegacyPageObservation) + L":" +
            std::to_wstring(legacyCells) + L":" +
            std::to_wstring(legacyCaptions) + L":" +
            std::to_wstring(legacyParagraphs) + L":" +
            std::to_wstring(legacyGraphAllPageEquivalent) + L":" +
            std::to_wstring(images) + L":" + std::to_wstring(linkedAssets) +
            L":" + std::to_wstring(layoutAttempts) + L":" +
            std::to_wstring(stableLayouts);
        for (const ImageRelationshipObservation& image : imageRelationships) {
            result += L":" + image.Canonical();
        }
        return result;
    }
};

bool FamilyPresent(const Observation& value, const std::string& family) {
    if (family == "stories") return value.sections != 0;
    if (family == "body-text") return value.paragraphs != 0 && value.atoms != 0;
    if (family == "effective-properties") return value.propertyValues != 0;
    if (family == "controls") return value.controls != 0;
    if (family == "tables") return value.tables != 0;
    if (family == "images") return value.images != 0;
    if (family == "linked-assets") return value.linkedAssets != 0;
    if (family == "stable-layout") return value.stableLayouts != 0;
    return false;
}

bool ManifestExpectationMatch(
    const std::string& manifest,
    const std::wstring& fixture,
    const Observation& value) {
    const std::string fixtureToken = "\"name\": " + Json(fixture);
    const size_t fixtureStart = manifest.find(fixtureToken);
    if (fixtureStart == std::string::npos) return false;
    const size_t nextFixture = manifest.find("\"name\":", fixtureStart + fixtureToken.size());
    const size_t fixtureEnd = nextFixture == std::string::npos ? manifest.size() : nextFixture;
    const auto familyArray = [&](const char* key) {
        const size_t keyAt = manifest.find(key, fixtureStart);
        if (keyAt == std::string::npos || keyAt >= fixtureEnd) return std::string{};
        const size_t begin = manifest.find('[', keyAt);
        const size_t end = begin == std::string::npos ? std::string::npos : manifest.find(']', begin);
        return begin == std::string::npos || end == std::string::npos || end >= fixtureEnd
            ? std::string{}
            : manifest.substr(begin, end - begin + 1);
    };
    const std::string required = familyArray("\"expected_families\"");
    if (required.empty()) return false;
    constexpr std::array<const char*, 8> nativeFamilies{
        "stories", "body-text", "effective-properties", "controls",
        "tables", "images", "linked-assets", "stable-layout"};
    for (const char* family : nativeFamilies) {
        if (required.find(std::string("\"") + family + "\"") != std::string::npos &&
            !FamilyPresent(value, family)) return false;
    }
    const std::string absent = familyArray("\"expected_absent_families\"");
    for (const char* family : nativeFamilies) {
        if (absent.find(std::string("\"") + family + "\"") != std::string::npos &&
            FamilyPresent(value, family)) return false;
    }
    return true;
}

bool ControlDispatches(IDispatch* hwp, std::vector<CComPtr<IDispatch>>* output) {
    CComVariant currentRaw;
    CComPtr<IDispatch> current;
    if (FAILED(PropertyGet(hwp, L"HeadCtrl", &currentRaw))) return false;
    if (currentRaw.vt == VT_EMPTY || currentRaw.vt == VT_NULL ||
        currentRaw.vt == VT_DISPATCH && currentRaw.pdispVal == nullptr) return true;
    if (FAILED(AsDispatch(currentRaw, current))) return false;
    for (std::uint64_t ordinal = 0; current != nullptr && ordinal < 1'000'000;
         ++ordinal) {
        output->push_back(current);
        CComVariant nextRaw;
        if (FAILED(PropertyGet(current, L"Next", &nextRaw))) return false;
        CComPtr<IDispatch> next;
        if (nextRaw.vt != VT_EMPTY && nextRaw.vt != VT_NULL &&
            !(nextRaw.vt == VT_DISPATCH && nextRaw.pdispVal == nullptr) &&
            FAILED(AsDispatch(nextRaw, next))) return false;
        current = next;
    }
    return current == nullptr;
}

std::wstring InstanceId(IDispatch*, IDispatch* control) {
    CComVariant result;
    std::wstring value;
    return SUCCEEDED(Method(control, L"GetCtrlInstID", {}, &result)) &&
        SUCCEEDED(AsString(result, &value)) ? value : std::wstring{};
}

std::wstring ControlId(IDispatch* control) {
    CComVariant value;
    std::wstring result;
    return SUCCEEDED(PropertyGet(control, L"CtrlID", &value)) &&
        SUCCEEDED(AsString(value, &result)) ? result : std::wstring{};
}

bool BindOwnerControls(
    IDispatch* const hwp,
    const StorySink& stories,
    const std::vector<CComPtr<IDispatch>>& dispatches,
    std::vector<hancom::graph::controls::AdaptedControlRecord>* const owners,
    std::vector<OwnerControlParityObservation>* const observations) {
    if (hwp == nullptr || owners == nullptr || observations == nullptr ||
        stories.controls.size() != dispatches.size()) return false;
    try {
        owners->clear();
        observations->clear();
        owners->reserve(stories.controls.size());
        observations->reserve(stories.controls.size());
        for (size_t index = 0; index < stories.controls.size(); ++index) {
            hancom::graph::controls::AdaptedControlRecord owner;
            if (!hancom::graph::controls::AdaptNativeControl(
                    stories.controls[index], &owner)) return false;
            owners->push_back(owner);
            observations->push_back({
                index, ControlId(dispatches[index]),
                InstanceId(hwp, dispatches[index])});
        }
        return VerifyOwnerControlParity(stories.controls, *observations);
    } catch (...) {
        owners->clear();
        observations->clear();
        return false;
    }
}

bool Observe(
    IDispatch* hwp,
    const std::wstring& label,
    Observation* observation,
    const SaveCleanRestorationInjection injection =
        SaveCleanRestorationInjection::None,
    const std::shared_ptr<DispatchQueryCounter>& queryCounter = nullptr) {
    ReadState state;
    if (!CaptureState(hwp, &state) || state.modified) {
        std::wcerr << L"OBSERVE_FAIL fixture=" << label
                   << L" stage=capture-save-clean-state\n";
        return false;
    }
    ReadStateRestoreGuard stateGuard(hwp, state);
    const auto finish = [&](const bool operationSucceeded) {
        observation->stateRestored = stateGuard.Restore();
        return operationSucceeded && observation->stateRestored;
    };
    const auto inLane = [&queryCounter](
        std::wstring lane,
        const auto& operation) {
        QueryLane scope(queryCounter, std::move(lane));
        return operation();
    };
    if (injection == SaveCleanRestorationInjection::AfterStateCapture) {
        return finish(false);
    }
    StorySink stories;
    hancom::graph::stories::CaptureDiagnostics storyDiagnostics;
    if (inLane(L"stories", [&] {
            return hancom::graph::stories::CaptureNativeStructureStories(
                hwp, stories, &storyDiagnostics);
        }) != hancom::graph::stories::CaptureStatus::Complete) {
        std::wcerr << L"OBSERVE_FAIL fixture=" << label << L" stage=stories status="
                   << static_cast<unsigned>(storyDiagnostics.status) << L'\n';
        return finish(false);
    }
    if (injection == SaveCleanRestorationInjection::AfterStories) {
        return finish(false);
    }
    TextSink text;
    hancom::graph::text::CaptureDiagnostics textDiagnostics;
    if (inLane(L"body-text", [&] {
            return hancom::graph::text::CaptureNativeBodyText(
                hwp, text, &textDiagnostics);
        }) != hancom::graph::text::CaptureStatus::Complete) {
        std::wcerr << L"OBSERVE_FAIL fixture=" << label << L" stage=body-text status="
                   << static_cast<unsigned>(textDiagnostics.status) << L'\n';
        return finish(false);
    }
    if (injection == SaveCleanRestorationInjection::AfterBodyText) {
        return finish(false);
    }
    std::vector<hancom::graph::properties::ReferenceSite> runSites;
    for (const auto& run : text.records) {
        runSites.push_back({
            hancom::graph::capture::PropertyTarget::Run,
            std::to_wstring(run.start.list) + L":" +
                std::to_wstring(run.start.paragraph) + L":" +
                std::to_wstring(run.start.character) + L":" +
                std::to_wstring(run.end.character),
            {run.start.list, run.start.paragraph, run.start.character}, 0});
    }
    hancom::graph::capture::ReaderPayload characterClosure;
    hancom::graph::properties::ReferenceClosureDiagnostics
        characterDiagnostics;
    if (runSites.empty() || inLane(L"character-shape-closure", [&] {
            return hancom::graph::properties::CaptureCurrentReferenceClosure(
                hwp, runSites, &characterClosure, &characterDiagnostics);
        }) != hancom::graph::properties::CaptureStatus::Complete ||
        characterDiagnostics.visitedSites != runSites.size()) {
        std::wcerr << L"OBSERVE_FAIL fixture=" << label
                   << L" stage=character-shape-closure\n";
        return finish(false);
    }
    std::set<std::wstring> referencedDefinitions;
    for (const auto& reference : characterClosure.definitionReferences) {
        if (reference.source !=
                hancom::graph::capture::PropertyTarget::Run ||
            reference.edge != hancom::graph::EdgeKind::CharacterShapeRef) {
            return finish(false);
        }
        referencedDefinitions.insert(reference.definitionIdentity);
        ++observation->characterShapeReferences;
    }
    hancom::graph::codec::Bytes characterDigestBytes;
    for (const auto& definition : characterClosure.definitions) {
        if (definition.kind !=
                hancom::graph::DefinitionKind::CharacterShape ||
            referencedDefinitions.find(definition.identity) ==
                referencedDefinitions.end()) {
            return finish(false);
        }
        ++observation->characterShapeDefinitions;
        const bool observedBody = definition.bodyState ==
                hancom::graph::ObservationState::Value &&
            std::any_of(
                definition.properties.begin(), definition.properties.end(),
                [](const auto& property) {
                    return property.state ==
                        hancom::graph::ObservationState::Value;
                });
        if (observedBody) ++observation->observedCharacterShapeBodies;
        characterDigestBytes.insert(
            characterDigestBytes.end(), definition.propertyDigest.bytes.begin(),
            definition.propertyDigest.bytes.end());
    }
    if (observation->characterShapeReferences != text.runs ||
        observation->characterShapeDefinitions == 0 ||
        observation->observedCharacterShapeBodies !=
            observation->characterShapeDefinitions ||
        referencedDefinitions.size() !=
            observation->characterShapeDefinitions) {
        return finish(false);
    }
    const auto characterDigest = hancom::graph::codec::DomainHash(
        "HWPGRAPH\0FIXTURE\0CHARACTERSHAPES\0V1",
        hancom::graph::codec::View(characterDigestBytes));
    constexpr wchar_t digestDigits[] = L"0123456789abcdef";
    for (const std::uint8_t byte : characterDigest.bytes) {
        observation->characterShapeDigest.push_back(digestDigits[byte >> 4]);
        observation->characterShapeDigest.push_back(
            digestDigits[byte & 0x0f]);
    }
    PropertySink properties;
    hancom::graph::properties::CaptureDiagnostics propertyDiagnostics;
    hancom::graph::properties::EffectivePropertyContext context;
    context.target = hancom::graph::capture::PropertyTarget::Run;
    context.targetIdentity = L"active-run";
    if (inLane(L"effective-properties", [&] {
            return hancom::graph::properties::CaptureCurrentEffectiveProperties(
                hwp, context, properties, &propertyDiagnostics);
        }) != hancom::graph::properties::CaptureStatus::Complete) {
        std::wcerr << L"OBSERVE_FAIL fixture=" << label
                   << L" stage=effective-properties status="
                   << static_cast<unsigned>(propertyDiagnostics.status) << L'\n';
        return finish(false);
    }
    if (injection == SaveCleanRestorationInjection::AfterEffectiveProperties) {
        return finish(false);
    }

    std::vector<CComPtr<IDispatch>> controls;
    std::vector<hancom::graph::controls::AdaptedControlRecord> owners;
    std::vector<OwnerControlParityObservation> ownerObservations;
    if (!inLane(L"controls", [&] {
            return ControlDispatches(hwp, &controls) &&
                BindOwnerControls(
                    hwp, stories, controls, &owners, &ownerObservations);
        })) {
        std::wcerr << L"OBSERVE_FAIL fixture=" << label
                   << L" stage=owner-control-parity\n";
        return finish(false);
    }
    if (injection == SaveCleanRestorationInjection::AfterOwnerControlBinding) {
        return finish(false);
    }
    const std::uint64_t bodyStoryNativeLists =
        stories.bodyList.state == hancom::graph::ObservationState::Value &&
                stories.bodyList.valuePresent
            ? 1
            : 0;
    std::uint64_t tableAttempts = 0;
    std::uint64_t tables = 0;
    std::uint64_t cellStoryNativeLists = 0;
    std::uint64_t imageAttempts = 0;
    std::uint64_t images = 0;
    std::uint64_t captionStoryNativeLists = 0;
    std::uint64_t tableCaptionsPresent = 0;
    std::uint64_t tableCaptionsAbsent = 0;
    std::uint64_t tableCaptionsUnavailable = 0;
    std::uint64_t tableCells = 0;
    std::uint64_t linkedAssets = 0;
    std::vector<ImageRelationshipObservation> imageRelationships;
    std::uint64_t layoutAttempts = 0;
    std::uint64_t layouts = 0;
    for (size_t index = 0; index < controls.size(); ++index) {
        IDispatch* const control = controls[index];
        const hancom::graph::controls::AdaptedControlRecord& owner = owners[index];
        if (owner.kind == hancom::graph::controls::ControlKind::Table &&
            !owner.sessionInstanceId.empty()) {
            ++tableAttempts;
            hancom::graph::tables::TableGraphRecord table;
            hancom::inspection::TableCaptionObservation caption;
            std::wstring error;
            if (inLane(L"table/" + std::to_wstring(index), [&] {
                    return hancom::graph::tables::CaptureTableGraphFromNative(
                               hwp, owner.sessionInstanceId, false, false, {},
                               &table, &error) ==
                               hancom::graph::tables::BuildStatus::Complete &&
                        hancom::inspection::InspectTableCaption(
                            hwp, owner.sessionInstanceId, &caption, &error);
                })) {
                ownerObservations[index].childKind = ChildOwnerKind::Table;
                ownerObservations[index].childSessionInstanceId =
                    table.sessionInstanceId;
                ++tables;
                if (caption.presenceState ==
                    hancom::inspection::NativeObservationState::Value) {
                    if (caption.exists) {
                        ++tableCaptionsPresent;
                        if (caption.listId > 0) ++captionStoryNativeLists;
                    } else {
                        ++tableCaptionsAbsent;
                    }
                } else {
                    ++tableCaptionsUnavailable;
                }
                tableCells += static_cast<std::uint64_t>(table.physicalCells.size());
                cellStoryNativeLists += static_cast<std::uint64_t>(
                    std::count_if(
                        table.physicalCells.begin(),
                        table.physicalCells.end(),
                        [](const auto& cell) { return cell.listId > 0; }));
                if (injection ==
                    SaveCleanRestorationInjection::AfterTableReader) {
                    return finish(false);
                }
                ++layoutAttempts;
                hancom::graph::layout::StableLayoutRecord layout;
                if (inLane(L"layout/" + std::to_wstring(index), [&] {
                        return hancom::graph::layout::CaptureCurrentTableLayoutFromNative(
                            hwp, owner.sessionInstanceId, &layout);
                    }) == hancom::graph::layout::CaptureStatus::Complete) ++layouts;
                if (injection ==
                    SaveCleanRestorationInjection::AfterLayoutReader) {
                    return finish(false);
                }
            }
        }
        if (owner.kind == hancom::graph::controls::ControlKind::GeneralShape &&
            !owner.sessionInstanceId.empty()) {
            ++imageAttempts;
            hancom::graph::images::ImageGraphRecord image;
            if (inLane(L"image/" + std::to_wstring(index), [&] {
                    return hancom::graph::images::CaptureImageGraphFromNative(
                        control, owner.sessionInstanceId, &image);
                }) == hancom::graph::images::CaptureStatus::Complete) {
                ownerObservations[index].childKind = ChildOwnerKind::Image;
                ownerObservations[index].childNativeTypeId = image.ctrlId;
                ownerObservations[index].childSessionInstanceId =
                    image.sessionInstanceId;
                ++images;
                if (injection ==
                    SaveCleanRestorationInjection::AfterImageReader) {
                    return finish(false);
                }
                imageRelationships.push_back({
                    static_cast<unsigned>(image.asset.storageState),
                    static_cast<unsigned>(image.asset.storage),
                    static_cast<unsigned>(image.asset.sourceName.state),
                    image.asset.sourceName.value,
                    static_cast<unsigned>(image.asset.binaryContentState),
                    image.asset.byteLength,
                    image.asset.sha256,
                    image.ctrlId,
                    image.sessionInstanceId,
                });
                if (image.asset.storageState ==
                        hancom::graph::images::ObservationState::Value &&
                    image.asset.storage ==
                        hancom::graph::images::AssetStorage::Linked &&
                    image.asset.sourceName.state ==
                        hancom::graph::images::ObservationState::Value &&
                    !image.asset.sourceName.value.empty()) {
                    ++linkedAssets;
                }
            }
        }
    }
    const bool ownerControlParity =
        VerifyOwnerControlParity(stories.controls, ownerObservations);
    if (!ownerControlParity) {
        std::wcerr << L"OBSERVE_FAIL fixture=" << label
                   << L" stage=child-owner-parity\n";
        return finish(false);
    }
    if (injection == SaveCleanRestorationInjection::AfterChildReaders) {
        return finish(false);
    }

    LONG pageCount = 0;
    CComVariant pageCountValue;
    if (FAILED(PropertyGet(hwp, L"PageCount", &pageCountValue)) ||
        FAILED(AsLong(pageCountValue, &pageCount)) || pageCount < 1) {
        std::wcerr << L"OBSERVE_FAIL fixture=" << label
                   << L" stage=legacy-page-count\n";
        return finish(false);
    }
    std::set<std::wstring> legacyControls;
    std::set<std::wstring> legacyCells;
    std::set<std::wstring> legacyCaptions;
    std::set<std::wstring> legacyParagraphs;
    for (LONG page = 1; page <= pageCount; ++page) {
        const std::wstring wire = hancom::inspection::InspectStructure(hwp, page);
        if (wire.rfind(L"HDS1\n", 0) != 0) {
            std::wcerr << L"OBSERVE_FAIL fixture=" << label
                       << L" stage=legacy-page-inspection page=" << page << L'\n';
            return finish(false);
        }
        std::wistringstream lines(wire);
        std::wstring line;
        while (std::getline(lines, line)) {
            const size_t tab = line.find(L'\t');
            if (tab == std::wstring::npos) continue;
            const std::wstring kind = line.substr(0, tab);
            if (kind == L"CTRL") legacyControls.insert(line.substr(tab + 1));
            else if (kind == L"CELL") legacyCells.insert(line.substr(tab + 1));
            else if (kind == L"CAPTION") legacyCaptions.insert(line.substr(tab + 1));
            else if (kind == L"PARA") {
                size_t end = tab;
                for (int field = 0; field < 3; ++field) {
                    end = line.find(L'\t', end + 1);
                    if (end == std::wstring::npos) break;
                }
                legacyParagraphs.insert(line.substr(tab + 1, end - tab - 1));
            }
        }
    }
    const std::uint64_t controlsWithoutLegacyPageObservation =
        storyDiagnostics.controlCount >= legacyControls.size()
            ? storyDiagnostics.controlCount - legacyControls.size()
            : 0;
    const bool legacyGraphAllPageEquivalent =
        legacyControls.size() + controlsWithoutLegacyPageObservation ==
            storyDiagnostics.controlCount &&
        legacyCells.size() == tableCells &&
        legacyCaptions.size() == tableCaptionsPresent &&
        legacyParagraphs.size() == text.paragraphs;

    observation->label = label;
    observation->sections = storyDiagnostics.sectionCount;
    observation->controls = storyDiagnostics.controlCount;
    observation->paragraphs = text.paragraphs;
    observation->atoms = textDiagnostics.atomCount;
    observation->runs = textDiagnostics.runCount;
    observation->textCodeUnits = text.textCodeUnits;
    observation->propertyValues = propertyDiagnostics.valueCount;
    observation->adaptedControls = owners.size();
    observation->ownerControlParity = ownerControlParity;
    observation->bodyStoryNativeLists = bodyStoryNativeLists;
    observation->tableAttempts = tableAttempts;
    observation->tables = tables;
    observation->cellStoryNativeLists = cellStoryNativeLists;
    observation->imageAttempts = imageAttempts;
    observation->images = images;
    observation->captionStoryNativeLists = captionStoryNativeLists;
    observation->tableCaptionsPresent = tableCaptionsPresent;
    observation->tableCaptionsAbsent = tableCaptionsAbsent;
    observation->tableCaptionsUnavailable = tableCaptionsUnavailable;
    observation->tableCells = tableCells;
    observation->legacyPagesInspected = static_cast<std::uint64_t>(pageCount);
    observation->legacyControls = legacyControls.size();
    observation->controlsWithoutLegacyPageObservation =
        controlsWithoutLegacyPageObservation;
    observation->legacyCells = legacyCells.size();
    observation->legacyCaptions = legacyCaptions.size();
    observation->legacyParagraphs = legacyParagraphs.size();
    observation->legacyGraphAllPageEquivalent = legacyGraphAllPageEquivalent;
    observation->linkedAssets = linkedAssets;
    observation->imageRelationships = std::move(imageRelationships);
    observation->layoutAttempts = layoutAttempts;
    observation->stableLayouts = layouts;
    return finish(stories.committed && text.committed && properties.committed);
}

struct SaveCleanRestorationInjectionSummary final {
    std::array<bool, 9> restored{};
    std::uint64_t restoredCount = 0;
    std::uint64_t expectedCount = restored.size();

    bool Passed() const noexcept { return restoredCount == expectedCount; }
};

SaveCleanRestorationInjectionSummary RunSaveCleanRestorationInjections(
    IDispatch* const hwp,
    const std::wstring& label) {
    SaveCleanRestorationInjectionSummary summary;
    constexpr std::array<SaveCleanRestorationInjection, 9> injections{
        SaveCleanRestorationInjection::AfterStateCapture,
        SaveCleanRestorationInjection::AfterStories,
        SaveCleanRestorationInjection::AfterBodyText,
        SaveCleanRestorationInjection::AfterEffectiveProperties,
        SaveCleanRestorationInjection::AfterOwnerControlBinding,
        SaveCleanRestorationInjection::AfterTableReader,
        SaveCleanRestorationInjection::AfterLayoutReader,
        SaveCleanRestorationInjection::AfterImageReader,
        SaveCleanRestorationInjection::AfterChildReaders,
    };
    for (size_t index = 0; index < injections.size(); ++index) {
        Observation ignored;
        ReadState after;
        const bool rejected = !Observe(hwp, label, &ignored, injections[index]);
        const bool saveClean = CaptureState(hwp, &after) && !after.modified;
        summary.restored[index] = rejected && ignored.stateRestored && saveClean;
        if (summary.restored[index]) ++summary.restoredCount;
    }
    return summary;
}

bool PngDimensions(const std::filesystem::path& path, std::uint32_t* width,
                   std::uint32_t* height) {
    std::ifstream input(path, std::ios::binary);
    std::array<unsigned char, 24> bytes{};
    input.read(reinterpret_cast<char*>(bytes.data()), bytes.size());
    const std::array<unsigned char, 8> signature{137,80,78,71,13,10,26,10};
    if (input.gcount() != bytes.size() ||
        !std::equal(signature.begin(), signature.end(), bytes.begin())) return false;
    const auto read = [&](const size_t offset) {
        return static_cast<std::uint32_t>(bytes[offset]) << 24 |
            static_cast<std::uint32_t>(bytes[offset + 1]) << 16 |
            static_cast<std::uint32_t>(bytes[offset + 2]) << 8 |
            static_cast<std::uint32_t>(bytes[offset + 3]);
    };
    *width = read(16); *height = read(20);
    return *width != 0 && *height != 0;
}

DWORD HwpProcessId(IDispatch* hwp) {
    CComVariant windowsRaw, windowRaw, handleRaw;
    CComPtr<IDispatch> windows, window;
    LONG handle = 0;
    DWORD process = 0;
    if (SUCCEEDED(PropertyGet(hwp, L"XHwpWindows", &windowsRaw)) &&
        SUCCEEDED(AsDispatch(windowsRaw, windows)) &&
        SUCCEEDED(PropertyGet(windows, L"Active_XHwpWindow", &windowRaw)) &&
        SUCCEEDED(AsDispatch(windowRaw, window)) &&
        SUCCEEDED(PropertyGet(window, L"WindowHandle", &handleRaw)) &&
        SUCCEEDED(AsLong(handleRaw, &handle))) {
        GetWindowThreadProcessId(reinterpret_cast<HWND>(
            static_cast<INT_PTR>(handle)), &process);
    }
    return process;
}

class FixtureLifecycle final {
public:
    ~FixtureLifecycle() noexcept { static_cast<void>(Cleanup()); }

    void OwnRuntimeRoot(std::filesystem::path path) {
        runtimeRoot_ = std::move(path);
        ownsRuntimeRoot_ = true;
    }

    void MarkComInitialized() noexcept { comInitialized_ = true; }

    CComPtr<IDispatch>& Hwp() noexcept { return hwp_; }

    void OwnProcess(HANDLE process) noexcept { process_ = process; }

    HANDLE Process() const noexcept { return process_; }

    bool Cleanup() noexcept {
        if (cleaned_) return processExited_ && tempRemoved_;
        cleaned_ = true;
        if (hwp_ != nullptr) {
            CComVariant ignored;
            static_cast<void>(Method(hwp_, L"Quit", {}, &ignored));
        }
        hwp_.Release();
        if (process_ != nullptr) {
            processExited_ =
                WaitForSingleObject(process_, 30'000) == WAIT_OBJECT_0;
            CloseHandle(process_);
            process_ = nullptr;
        } else {
            processExited_ = true;
        }
        if (comInitialized_) {
            CoUninitialize();
            comInitialized_ = false;
        }
        if (ownsRuntimeRoot_) {
            std::error_code error;
            std::filesystem::remove_all(runtimeRoot_, error);
            tempRemoved_ = !error &&
                !std::filesystem::exists(runtimeRoot_, error) && !error;
            ownsRuntimeRoot_ = false;
        } else {
            tempRemoved_ = true;
        }
        return processExited_ && tempRemoved_;
    }

    bool ProcessExited() const noexcept { return processExited_; }
    bool TempRemoved() const noexcept { return tempRemoved_; }

private:
    std::filesystem::path runtimeRoot_;
    CComPtr<IDispatch> hwp_;
    HANDLE process_ = nullptr;
    bool ownsRuntimeRoot_ = false;
    bool comInitialized_ = false;
    bool cleaned_ = false;
    bool processExited_ = false;
    bool tempRemoved_ = false;
};

class UntrustedFixtureDispatch final : public IDispatch {
public:
    HRESULT STDMETHODCALLTYPE QueryInterface(
        REFIID iid, void** output) noexcept override {
        if (output == nullptr) return E_POINTER;
        *output = nullptr;
        if (iid != IID_IUnknown && iid != IID_IDispatch) return E_NOINTERFACE;
        *output = static_cast<IDispatch*>(this);
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() noexcept override {
        return static_cast<ULONG>(InterlockedIncrement(&references_));
    }
    ULONG STDMETHODCALLTYPE Release() noexcept override {
        return static_cast<ULONG>(InterlockedDecrement(&references_));
    }
    HRESULT STDMETHODCALLTYPE GetTypeInfoCount(UINT*) noexcept override {
        return E_NOTIMPL;
    }
    HRESULT STDMETHODCALLTYPE GetTypeInfo(
        UINT, LCID, ITypeInfo**) noexcept override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetIDsOfNames(
        REFIID, LPOLESTR*, UINT, LCID, DISPID*) noexcept override {
        return DISP_E_UNKNOWNNAME;
    }
    HRESULT STDMETHODCALLTYPE Invoke(
        DISPID, REFIID, LCID, WORD, DISPPARAMS*, VARIANT*, EXCEPINFO*,
        UINT*) noexcept override { return DISP_E_MEMBERNOTFOUND; }
private:
    LONG references_ = 1;
};

struct RuntimeDispatchProvenanceInjectionSummary final {
    bool realRootAccepted = false;
    bool realReturnedAccepted = false;
    bool foreignRuntimeRejected = false;
    bool staleSessionRejected = false;
    bool wrongSessionRejected = false;
    bool wrongObjectLineageRejected = false;
    bool samePathDocumentReplacementRejected = false;
    bool staleDispatchAfterSamePathReplacementRejected = false;
    bool trustedCurrentDocumentAccepted = false;
    bool sameRuntimeReplacementPerformed = false;
    bool oldSessionRejectedBeforeReactivation = false;
    bool replacementSessionAdvanced = false;
    bool staleDispatchRejectedAfterReactivation = false;
    bool newDocumentTrustedAfterReactivation = false;
    bool replacementPredicatesIndependent = false;
    bool foreignRuntimeReleased = false;

    bool Passed() const noexcept {
        return realRootAccepted && realReturnedAccepted &&
            foreignRuntimeRejected && staleSessionRejected &&
            wrongSessionRejected && wrongObjectLineageRejected &&
            samePathDocumentReplacementRejected &&
            staleDispatchAfterSamePathReplacementRejected &&
            trustedCurrentDocumentAccepted && sameRuntimeReplacementPerformed &&
            oldSessionRejectedBeforeReactivation &&
            replacementSessionAdvanced &&
            staleDispatchRejectedAfterReactivation &&
            newDocumentTrustedAfterReactivation &&
            replacementPredicatesIndependent && foreignRuntimeReleased;
    }
};

RuntimeDispatchProvenanceInjectionSummary
RunRuntimeDispatchProvenanceInjections(
    const std::shared_ptr<DispatchQueryCounter>& counter,
    IDispatch* const countedHwp,
    REFCLSID classId,
    const DWORD processId,
    const std::filesystem::path& activePath) {
    RuntimeDispatchProvenanceInjectionSummary summary;
    summary.realRootAccepted =
        counter->ValidateDispatchForActiveSession(countedHwp);

    CComVariant documentsRaw, documentRaw;
    CComPtr<IDispatch> documents, document;
    summary.realReturnedAccepted =
        SUCCEEDED(PropertyGet(countedHwp, L"XHwpDocuments", &documentsRaw)) &&
        SUCCEEDED(AsDispatch(documentsRaw, documents)) &&
        SUCCEEDED(PropertyGet(
            documents, L"Active_XHwpDocument", &documentRaw)) &&
        SUCCEEDED(AsDispatch(documentRaw, document)) &&
        counter->ValidateDispatchForActiveSession(document);
    summary.trustedCurrentDocumentAccepted =
        summary.realRootAccepted && summary.realReturnedAccepted;

    UntrustedFixtureDispatch wrongObject;
    summary.wrongObjectLineageRejected =
        !counter->ValidateDispatchForActiveSession(&wrongObject);

    const std::uint64_t oldSessionId = counter->ActiveDocumentSessionId();
    CComVariant result, addedRaw;
    result.Clear();
    const bool oldDocumentCleared = SUCCEEDED(Method(
        countedHwp, L"Clear", {CComVariant(1L)}, &result));
    result.Clear();
    CComPtr<IDispatch> addedDocument;
    const bool newDocumentAdded = oldDocumentCleared &&
        SUCCEEDED(Method(
            documents, L"Add", {CComVariant(0L)}, &addedRaw)) &&
        SUCCEEDED(AsDispatch(addedRaw, addedDocument));
    result.Clear();
    const wchar_t* const format =
        activePath.extension() == L".hwpx" ? L"HWPX" : L"HWP";
    const bool samePathOpened = newDocumentAdded && ResultBool(
        Method(countedHwp, L"Open",
               {CComVariant(activePath.c_str()), CComVariant(format),
                CComVariant(L"lock:FALSE")},
               &result),
        result);
    CComPtr<IDispatch> replacementDocument;
    IDispatch* replacementRaw = nullptr;
    const bool replacementResolved = samePathOpened &&
        counter->ResolveCurrentRuntimeDocument(&replacementRaw);
    if (replacementResolved) replacementDocument.Attach(replacementRaw);
    CComVariant replacementNameRaw;
    std::wstring replacementName;
    std::error_code pathError;
    const bool replacementHasSamePath = replacementResolved &&
        SUCCEEDED(PropertyGet(
            replacementDocument, L"FullName", &replacementNameRaw)) &&
        SUCCEEDED(AsString(replacementNameRaw, &replacementName)) &&
        _wcsicmp(
            std::filesystem::weakly_canonical(
                replacementName, pathError).c_str(),
            std::filesystem::weakly_canonical(
                activePath, pathError).c_str()) == 0 && !pathError;
    summary.sameRuntimeReplacementPerformed =
        oldDocumentCleared && newDocumentAdded && samePathOpened &&
        replacementHasSamePath;
    summary.samePathDocumentReplacementRejected =
        summary.sameRuntimeReplacementPerformed &&
        !counter->ValidateObservedDocumentForActiveSession(
            replacementDocument);
    summary.oldSessionRejectedBeforeReactivation =
        summary.sameRuntimeReplacementPerformed &&
        !counter->ValidateDispatchForActiveSession(countedHwp);

    documentsRaw.Clear();
    documentRaw.Clear();
    documents.Release();
    addedRaw.Clear();
    addedDocument.Release();
    counter->EndDocumentSession();
    summary.wrongSessionRejected = !counter->ActivateDocumentSession(
        countedHwp, activePath.parent_path() / L"wrong-session.hwp");
    const bool reactivated = summary.sameRuntimeReplacementPerformed &&
        counter->ActivateDocumentSession(countedHwp, activePath);
    summary.replacementSessionAdvanced = reactivated &&
        counter->ActiveDocumentSessionId() != 0 &&
        counter->ActiveDocumentSessionId() != oldSessionId;
    summary.staleDispatchRejectedAfterReactivation =
        summary.replacementSessionAdvanced && document != nullptr &&
        !counter->ValidateDispatchForActiveSession(document);
    summary.staleDispatchAfterSamePathReplacementRejected =
        summary.staleDispatchRejectedAfterReactivation;
    summary.staleSessionRejected =
        summary.staleDispatchRejectedAfterReactivation;

    CComVariant newDocumentsRaw, newDocumentRaw;
    CComPtr<IDispatch> newDocuments, newDocument;
    summary.newDocumentTrustedAfterReactivation =
        summary.replacementSessionAdvanced &&
        counter->ValidateDispatchForActiveSession(countedHwp) &&
        SUCCEEDED(PropertyGet(
            countedHwp, L"XHwpDocuments", &newDocumentsRaw)) &&
        SUCCEEDED(AsDispatch(newDocumentsRaw, newDocuments)) &&
        SUCCEEDED(PropertyGet(
            newDocuments, L"Active_XHwpDocument", &newDocumentRaw)) &&
        SUCCEEDED(AsDispatch(newDocumentRaw, newDocument)) &&
        counter->ValidateDispatchForActiveSession(newDocument);
    summary.replacementPredicatesIndependent =
        summary.oldSessionRejectedBeforeReactivation &&
        summary.replacementSessionAdvanced &&
        summary.staleDispatchRejectedAfterReactivation &&
        summary.newDocumentTrustedAfterReactivation;

    CComPtr<IDispatch> foreign;
    const HRESULT foreignStatus = CoCreateInstance(
        classId, nullptr, CLSCTX_LOCAL_SERVER, IID_IDispatch,
        reinterpret_cast<void**>(&foreign));
    result.Clear();
    if (SUCCEEDED(foreignStatus) && foreign != nullptr) {
        static_cast<void>(Method(
            foreign, L"SetMessageBoxMode", {CComVariant(0x00011110L)},
            &result));
        const DWORD foreignProcessId = HwpProcessId(foreign);
        HANDLE foreignProcess = foreignProcessId == 0 ? nullptr : OpenProcess(
            SYNCHRONIZE, FALSE, foreignProcessId);
        summary.foreignRuntimeRejected = foreignProcessId != 0 &&
            foreignProcessId != processId &&
            !counter->ValidateDispatchForActiveSession(foreign);
        result.Clear();
        static_cast<void>(Method(foreign, L"Quit", {}, &result));
        foreign.Release();
        summary.foreignRuntimeReleased = foreignProcess != nullptr &&
            WaitForSingleObject(foreignProcess, 30'000) == WAIT_OBJECT_0;
        if (foreignProcess != nullptr) CloseHandle(foreignProcess);
    }
    return summary;
}

bool CandidateModules(
    const DWORD processId,
    std::vector<std::wstring>* modules) {
    if (processId == 0 || modules == nullptr) return false;
    HANDLE snapshot = CreateToolhelp32Snapshot(
        TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, processId);
    if (snapshot == INVALID_HANDLE_VALUE) return false;
    MODULEENTRY32W entry{};
    entry.dwSize = sizeof(entry);
    bool enumerated = Module32FirstW(snapshot, &entry) != FALSE;
    if (enumerated) {
        do {
            std::wstring name(entry.szModule);
            std::transform(name.begin(), name.end(), name.begin(), towlower);
            if (name.find(L"hancomlivebridge") != std::wstring::npos) {
                modules->push_back(name + L":" + Sha256(entry.szExePath));
            }
        } while (Module32NextW(snapshot, &entry));
    }
    CloseHandle(snapshot);
    std::sort(modules->begin(), modules->end());
    return enumerated;
}

std::string ObservationJson(const Observation& value) {
    std::ostringstream output;
    output << "{\"fixture\":" << Json(value.label)
           << ",\"families\":[";
    bool separator = false;
    const auto appendFamily = [&](const char* family) {
        if (separator) output << ',';
        output << '\"' << family << '\"';
        separator = true;
    };
    if (value.sections != 0) appendFamily("stories");
    if (value.paragraphs != 0 && value.atoms != 0) appendFamily("body-text");
    if (value.propertyValues != 0) appendFamily("effective-properties");
    if (value.controls != 0) appendFamily("controls");
    if (value.tables != 0) appendFamily("tables");
    if (value.images != 0) appendFamily("images");
    if (value.linkedAssets != 0) appendFamily("linked-assets");
    if (value.stableLayouts != 0) appendFamily("stable-layout");
    output << "],\"sections\":" << value.sections
           << ",\"controls\":" << value.controls
           << ",\"paragraphs\":" << value.paragraphs
           << ",\"atoms\":" << value.atoms
           << ",\"runs\":" << value.runs
           << ",\"text_code_units\":" << value.textCodeUnits
           << ",\"property_values\":" << value.propertyValues
           << ",\"character_shape_references\":"
           << value.characterShapeReferences
           << ",\"character_shape_definitions\":"
           << value.characterShapeDefinitions
           << ",\"observed_character_shape_bodies\":"
           << value.observedCharacterShapeBodies
           << ",\"character_shape_digest\":"
           << Json(value.characterShapeDigest)
           << ",\"adapted_controls\":" << value.adaptedControls
           << ",\"owner_control_parity\":"
           << (value.ownerControlParity ? "true" : "false")
           << ",\"body_story_native_lists\":"
           << value.bodyStoryNativeLists
           << ",\"table_attempts\":" << value.tableAttempts
           << ",\"tables\":" << value.tables
           << ",\"cell_story_native_lists\":"
           << value.cellStoryNativeLists
           << ",\"image_attempts\":" << value.imageAttempts
           << ",\"images\":" << value.images
           << ",\"caption_story_native_lists\":"
           << value.captionStoryNativeLists
           << ",\"table_captions_present\":"
           << value.tableCaptionsPresent
           << ",\"table_captions_absent\":"
           << value.tableCaptionsAbsent
           << ",\"table_captions_unavailable\":"
           << value.tableCaptionsUnavailable
           << ",\"table_cells\":" << value.tableCells
           << ",\"legacy_pages_inspected\":" << value.legacyPagesInspected
           << ",\"legacy_controls\":" << value.legacyControls
           << ",\"controls_without_legacy_page_observation\":"
           << value.controlsWithoutLegacyPageObservation
           << ",\"legacy_cells\":" << value.legacyCells
           << ",\"legacy_captions\":" << value.legacyCaptions
           << ",\"legacy_paragraphs\":" << value.legacyParagraphs
           << ",\"legacy_graph_all_page_equivalent\":"
           << (value.legacyGraphAllPageEquivalent ? "true" : "false")
           << ",\"image_relationships\":[";
    for (size_t index = 0; index < value.imageRelationships.size(); ++index) {
        if (index != 0) output << ',';
        const ImageRelationshipObservation& image =
            value.imageRelationships[index];
        output << "{\"storageState\":" << image.storageState
               << ",\"storage\":" << image.storage
               << ",\"sourceNameState\":" << image.sourceNameState
               << ",\"sourceName\":" << Json(image.sourceName)
               << ",\"binaryContentState\":" << image.binaryContentState
               << ",\"byteLength\":" << image.byteLength
               << ",\"sha256\":" << Json(image.sha256)
               << ",\"ctrlId\":" << Json(image.ctrlId)
               << ",\"stableIdentity\":" << Json(image.stableIdentity)
               << '}';
    }
    output << "],\"linked_assets\":" << value.linkedAssets
           << ",\"layout_attempts\":" << value.layoutAttempts
           << ",\"stable_layouts\":" << value.stableLayouts
           << ",\"state_restored\":"
           << (value.stateRestored ? "true" : "false")
           << ",\"expectation_match\":"
           << (value.expectationMatched ? "true" : "false") << '}';
    return output.str();
}

std::uint64_t CalledLanePrefixCount(
    const DispatchTraceSnapshot& trace,
    const std::wstring& prefix) {
    std::map<std::wstring, bool> lanes;
    for (const DispatchQuery& query : trace.queries) {
        if (query.invoked && query.lane.rfind(prefix, 0) == 0) {
            lanes.emplace(query.lane, true);
        }
    }
    return lanes.size();
}

bool ExpectedReaderQueryRoutes(
    const DispatchTraceSnapshot& trace,
    const Observation& observation) {
    return trace.Valid() && trace.InvokeCount() != 0 &&
        trace.HasCalledLane(L"stories") &&
        trace.HasCalledLane(L"body-text") &&
        trace.HasCalledLane(L"effective-properties") &&
        trace.HasCalledLane(L"controls") &&
        CalledLanePrefixCount(trace, L"table/") == observation.tableAttempts &&
        CalledLanePrefixCount(trace, L"layout/") == observation.layoutAttempts &&
        CalledLanePrefixCount(trace, L"image/") == observation.imageAttempts;
}

struct DispatchMismatchInjectionSummary final {
    bool countMismatchRejected = false;
    bool routingMismatchRejected = false;

    bool Passed() const noexcept {
        return countMismatchRejected && routingMismatchRejected;
    }
};

bool CalledQueriesHaveRuntimeProvenance(
    const DispatchTraceSnapshot& trace) noexcept {
    bool called = false;
    for (const DispatchQuery& query : trace.queries) {
        if (!query.invoked) continue;
        called = true;
        if (!query.runtimeProvenance || query.documentSessionId == 0 ||
            query.objectId == 0 || query.member.empty()) return false;
    }
    return called;
}

bool RejectCalledProvenanceMismatch() {
    DispatchTraceSnapshot injected;
    DispatchQuery query;
    query.lane = L"called-provenance-injection";
    query.objectId = 1;
    query.member = L"InjectedMember";
    query.dispid = 7;
    query.getIdsStatus = S_OK;
    query.invoked = true;
    query.flags = DISPATCH_METHOD;
    query.invokeStatus = S_OK;
    injected.queries.push_back(std::move(query));
    return !CalledQueriesHaveRuntimeProvenance(injected);
}

DispatchMismatchInjectionSummary RunDispatchMismatchInjections() {
    DispatchMismatchInjectionSummary summary;
    auto counter = std::make_shared<DispatchQueryCounter>();
    LPOLESTR name = const_cast<LPOLESTR>(L"InjectedMember");
    DISPID member = 7;
    counter->BeginPass();
    counter->EnterLane(L"count-injection");
    counter->RecordGetIds(1, IID_NULL, &name, 1, LOCALE_USER_DEFAULT,
                          &member, S_OK);
    counter->LeaveLane();
    summary.countMismatchRejected = !counter->Snapshot().Valid();

    counter->BeginPass();
    counter->EnterLane(L"routing-injection");
    counter->RecordGetIds(1, IID_NULL, &name, 1, LOCALE_USER_DEFAULT,
                          &member, S_OK);
    counter->RecordInvoke(
        1, member + 1, IID_NULL, DISPATCH_METHOD, S_OK);
    counter->LeaveLane();
    const DispatchTraceSnapshot routing = counter->Snapshot();
    summary.routingMismatchRejected =
        routing.routingMismatch && !routing.Valid();
    return summary;
}

std::string DispatchQueryReceipt(
    const std::array<DispatchTraceSnapshot, 3>& first,
    const std::array<DispatchTraceSnapshot, 3>& second,
    const std::array<Observation, 3>& observations,
    const bool wrappersReleased,
    const bool runtimeRootActivated,
    const std::uint64_t untrustedDispatchCount,
    const RuntimeDispatchProvenanceInjectionSummary& provenanceInjections,
    bool* const passed) {
    const bool routingMismatch = std::any_of(
        first.begin(), first.end(),
        [](const DispatchTraceSnapshot& trace) {
            return trace.routingMismatch;
        }) || std::any_of(
        second.begin(), second.end(),
        [](const DispatchTraceSnapshot& trace) {
            return trace.routingMismatch;
        });
    const DispatchMismatchInjectionSummary injections =
        RunDispatchMismatchInjections();
    const bool calledProvenanceMismatchRejected =
        RejectCalledProvenanceMismatch();
    bool valid = wrappersReleased && runtimeRootActivated &&
        untrustedDispatchCount == 0 && calledProvenanceMismatchRejected &&
        provenanceInjections.Passed() && !routingMismatch &&
        injections.Passed();
    std::uint64_t getIdsCount = 0;
    std::uint64_t invokeCount = 0;
    std::ostringstream output;
    output << "{\"schema\":1,\"lane\":\"fixture-idispatch-query-counts\","
              "\"runtime_root_activated\":"
           << (runtimeRootActivated ? "true" : "false")
           << ",\"untrusted_dispatch_count\":" << untrustedDispatchCount
           << ",\"called_provenance_mismatch_rejected\":"
           << (calledProvenanceMismatchRejected ? "true" : "false")
           << ",\"real_runtime_root_accepted\":"
           << (provenanceInjections.realRootAccepted ? "true" : "false")
           << ",\"real_returned_dispatch_accepted\":"
           << (provenanceInjections.realReturnedAccepted ? "true" : "false")
           << ",\"foreign_runtime_rejected\":"
           << (provenanceInjections.foreignRuntimeRejected ? "true" : "false")
           << ",\"stale_session_rejected\":"
           << (provenanceInjections.staleSessionRejected ? "true" : "false")
           << ",\"wrong_session_rejected\":"
           << (provenanceInjections.wrongSessionRejected ? "true" : "false")
           << ",\"wrong_object_lineage_rejected\":"
           << (provenanceInjections.wrongObjectLineageRejected ? "true" : "false")
           << ",\"same_path_document_replacement_rejected\":"
           << (provenanceInjections.samePathDocumentReplacementRejected ? "true" : "false")
           << ",\"stale_dispatch_after_same_path_replacement_rejected\":"
           << (provenanceInjections.staleDispatchAfterSamePathReplacementRejected ? "true" : "false")
           << ",\"trusted_current_document_accepted\":"
           << (provenanceInjections.trustedCurrentDocumentAccepted ? "true" : "false")
           << ",\"same_runtime_replacement_performed\":"
           << (provenanceInjections.sameRuntimeReplacementPerformed ? "true" : "false")
           << ",\"old_session_rejected_before_reactivation\":"
           << (provenanceInjections.oldSessionRejectedBeforeReactivation ? "true" : "false")
           << ",\"replacement_session_advanced\":"
           << (provenanceInjections.replacementSessionAdvanced ? "true" : "false")
           << ",\"stale_dispatch_rejected_after_reactivation\":"
           << (provenanceInjections.staleDispatchRejectedAfterReactivation ? "true" : "false")
           << ",\"new_document_trusted_after_reactivation\":"
           << (provenanceInjections.newDocumentTrustedAfterReactivation ? "true" : "false")
           << ",\"replacement_predicates_independent\":"
           << (provenanceInjections.replacementPredicatesIndependent ? "true" : "false")
           << ",\"foreign_runtime_released\":"
           << (provenanceInjections.foreignRuntimeReleased ? "true" : "false")
           << ",\"routing_mismatch\":"
           << (routingMismatch ? "true" : "false") << ",\"fixtures\":[";
    for (size_t fixture = 0; fixture < first.size(); ++fixture) {
        if (fixture != 0) output << ',';
        const bool firstValid = ExpectedReaderQueryRoutes(
            first[fixture], observations[fixture]);
        const bool secondValid = ExpectedReaderQueryRoutes(
            second[fixture], observations[fixture]);
        const bool equal = first[fixture].Canonical() ==
            second[fixture].Canonical();
        const bool firstCalledRuntime = CalledQueriesHaveRuntimeProvenance(
            first[fixture]);
        const bool secondCalledRuntime = CalledQueriesHaveRuntimeProvenance(
            second[fixture]);
        valid = valid && firstValid && secondValid && equal &&
            firstCalledRuntime && secondCalledRuntime;
        getIdsCount += first[fixture].queries.size();
        invokeCount += first[fixture].InvokeCount();
        output << "{\"fixture\":" << Json(observations[fixture].label)
               << ",\"first_valid\":" << (firstValid ? "true" : "false")
               << ",\"second_valid\":" << (secondValid ? "true" : "false")
               << ",\"two_passes_equal\":" << (equal ? "true" : "false")
               << ",\"called_runtime_dispatch\":"
               << (firstCalledRuntime && secondCalledRuntime ? "true" : "false")
               << ",\"get_ids_of_names\":" << first[fixture].queries.size()
               << ",\"invoke\":" << first[fixture].InvokeCount() << '}';
    }
    output << "],\"get_ids_of_names\":" << getIdsCount
           << ",\"invoke\":" << invokeCount
           << ",\"count_mismatch_rejected\":"
           << (injections.countMismatchRejected ? "true" : "false")
           << ",\"routing_mismatch_rejected\":"
           << (injections.routingMismatchRejected ? "true" : "false")
           << ",\"wrappers_released\":"
           << (wrappersReleased ? "true" : "false")
           << ",\"called_dispatch\":[";
    bool separator = false;
    for (size_t fixture = 0; fixture < first.size(); ++fixture) {
        const DispatchTraceSnapshot& trace = first[fixture];
        std::map<std::wstring, std::map<std::uint64_t, std::uint64_t>> ordinals;
        struct Aggregate final {
            std::wstring lane;
            std::uint64_t object = 0;
            std::wstring member;
            WORD flags = 0;
            HRESULT getIdsStatus = E_UNEXPECTED;
            HRESULT invokeStatus = E_UNEXPECTED;
            std::uint64_t count = 0;
        };
        std::vector<Aggregate> aggregates;
        for (const DispatchQuery& query : trace.queries) {
            if (!query.invoked) continue;
            auto& laneOrdinals = ordinals[query.lane];
            auto object = laneOrdinals.find(query.objectId);
            if (object == laneOrdinals.end()) {
                object = laneOrdinals.emplace(
                    query.objectId, laneOrdinals.size() + 1).first;
            }
            const auto match = std::find_if(
                aggregates.begin(), aggregates.end(),
                [&](const Aggregate& value) {
                    return value.lane == query.lane &&
                        value.object == object->second &&
                        value.member == query.member &&
                        value.flags == query.flags &&
                        value.getIdsStatus == query.getIdsStatus &&
                        value.invokeStatus == query.invokeStatus;
                });
            if (match == aggregates.end()) {
                aggregates.push_back({
                    query.lane, object->second, query.member, query.flags,
                    query.getIdsStatus, query.invokeStatus, 1});
            } else {
                ++match->count;
            }
        }
        for (const Aggregate& value : aggregates) {
            if (separator) output << ',';
            separator = true;
            output << "{\"fixture\":" << Json(observations[fixture].label)
                   << ",\"branch\":" << Json(value.lane)
                   << ",\"object\":" << value.object
                   << ",\"member\":" << Json(value.member)
                   << ",\"flags\":" << value.flags
                   << ",\"get_ids_status\":"
                   << static_cast<std::int32_t>(value.getIdsStatus)
                   << ",\"invoke_status\":"
                   << static_cast<std::int32_t>(value.invokeStatus)
                   << ",\"count\":" << value.count << '}';
        }
    }
    output << "],\"status\":\"" << (valid ? "green" : "red")
           << "\"}";
    if (passed != nullptr) *passed = valid;
    return output.str();
}

} // namespace

int RunNativeStructureFixtures(
    const wchar_t* fixtureRootArgument,
    const wchar_t* receiptRootArgument) {
    std::filesystem::path fixtureRoot;
    std::filesystem::path receiptRoot;
    std::error_code error;
    if (!CanonicalPath(fixtureRootArgument, &fixtureRoot) ||
        !CanonicalPath(receiptRootArgument, &receiptRoot) ||
        IsSameOrAncestor(fixtureRoot, receiptRoot) ||
        IsSameOrAncestor(receiptRoot, fixtureRoot) ||
        !std::filesystem::is_directory(fixtureRoot, error) || error ||
        (std::filesystem::exists(receiptRoot, error) &&
         !std::filesystem::is_directory(receiptRoot, error))) {
        std::wcerr << L"NATIVE_STRUCTURE_FIXTURES bad input\n";
        return 2;
    }
    if (!ManifestDescriptorMatch(fixtureRoot)) {
        std::wcerr << L"NATIVE_STRUCTURE_FIXTURES manifest provenance mismatch\n";
        return 2;
    }
    const std::string manifest = ReadBytes(fixtureRoot / L"manifest.json");
    if (manifest.empty()) {
        std::wcerr << L"NATIVE_STRUCTURE_FIXTURES missing manifest\n";
        return 2;
    }
    for (const wchar_t* name : kFixtureNames) {
        if (!std::filesystem::is_regular_file(fixtureRoot / name, error) || error) {
            std::wcerr << L"NATIVE_STRUCTURE_FIXTURES missing fixture: " << name << L'\n';
            return 2;
        }
    }

    std::array<std::wstring, 4> sourceHashes{};
    for (size_t index = 0; index < kFixtureNames.size(); ++index) {
        const auto source = fixtureRoot / kFixtureNames[index];
        const std::uintmax_t byteCount = std::filesystem::file_size(source, error);
        sourceHashes[index] = error ? std::wstring{} : Sha256(source);
        if (error || sourceHashes[index].empty() ||
            !ManifestFixtureProvenanceMatch(
                manifest, kFixtureNames[index], byteCount,
                sourceHashes[index])) {
            std::wcerr << L"NATIVE_STRUCTURE_FIXTURES provenance mismatch: "
                       << kFixtureNames[index] << L'\n';
            return 2;
        }
    }

    std::filesystem::create_directories(receiptRoot, error);
    if (error || !InvalidateOwnedReceipts(receiptRoot)) return 3;

    FixtureLifecycle lifecycle;
    const std::filesystem::path runtimeRoot =
        std::filesystem::temp_directory_path(error) /
        (L"BridgeSmokeNativeStructure-" + std::to_wstring(GetCurrentProcessId()));
    if (error || std::filesystem::exists(runtimeRoot, error) || error ||
        !std::filesystem::create_directory(runtimeRoot, error) || error) return 3;
    lifecycle.OwnRuntimeRoot(runtimeRoot);
    if (FailureRequested(L"runtime-root-created")) return 10;
    bool copiesValid = true;
    for (size_t index = 0; index < kFixtureNames.size(); ++index) {
        const auto source = fixtureRoot / kFixtureNames[index];
        const auto copy = runtimeRoot / kFixtureNames[index];
        std::filesystem::copy_file(
            source, copy, std::filesystem::copy_options::none, error);
        copiesValid = copiesValid && !error && !sourceHashes[index].empty() &&
            Sha256(copy) == sourceHashes[index];
        error.clear();
    }
    if (!copiesValid) return 4;
    std::uint32_t pngWidth = 0, pngHeight = 0;
    if (!PngDimensions(runtimeRoot / kFixtureNames[3], &pngWidth, &pngHeight)) {
        return 4;
    }

    const HRESULT comStatus = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    if (FAILED(comStatus)) return 5;
    lifecycle.MarkComInitialized();
    CLSID classId{};
    CComPtr<IDispatch>& hwp = lifecycle.Hwp();
    HRESULT status = CLSIDFromProgID(L"HWPFrame.HwpObject", &classId);
    if (SUCCEEDED(status)) status = CoCreateInstance(
        classId, nullptr, CLSCTX_LOCAL_SERVER, IID_IDispatch,
        reinterpret_cast<void**>(&hwp));
    if (FAILED(status) || hwp == nullptr) return 5;
    const DWORD processId = HwpProcessId(hwp);
    HANDLE process = OpenProcess(
        SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, processId);
    lifecycle.OwnProcess(process);
    if (process == nullptr) return 5;
    if (FailureRequested(L"com-activated")) return 10;
    CComVariant result;
    if (FAILED(Method(
            hwp, L"SetMessageBoxMode", {CComVariant(0x00011110L)},
            &result))) return 5;
    if (FailureRequested(L"message-box-mode-set")) return 10;
    result.Clear();
    const bool fileAccessRegistered = ResultBool(
        Method(hwp, L"RegisterModule",
               {CComVariant(L"FilePathCheckDLL"),
                CComVariant(L"FilePathCheckerModule")}, &result), result);
    if (!fileAccessRegistered) return 5;
    if (FailureRequested(L"module-registered")) return 10;
    std::vector<std::wstring> candidateModulesBefore;
    const bool modulesEnumeratedBefore = CandidateModules(
        processId, &candidateModulesBefore);

    auto queryCounter = std::make_shared<DispatchQueryCounter>();
    CComPtr<IDispatch> countedHwp;
    IDispatch* countedRaw = nullptr;
    if (FAILED(queryCounter->ActivateRuntimeRoot(
            hwp, processId, process, &countedRaw))) return 5;
    countedHwp.Attach(countedRaw);
    if (FailureRequested(L"runtime-root-activated")) return 10;
    std::array<Observation, 3> first{};
    std::array<Observation, 3> second{};
    std::array<DispatchTraceSnapshot, 3> firstQueries{};
    std::array<DispatchTraceSnapshot, 3> secondQueries{};
    SaveCleanRestorationInjectionSummary saveCleanInjections;
    bool saveCleanFixtureOpened = false;
    bool saveCleanFixtureClosed = false;
    bool observed = true;
    for (size_t index = 0; index < first.size() && observed; ++index) {
        const std::filesystem::path path = runtimeRoot / kFixtureNames[index];
        const wchar_t* format = path.extension() == L".hwpx" ? L"HWPX" : L"HWP";
        result.Clear();
        observed = ResultBool(
            Method(hwp, L"Open",
                   {CComVariant(path.c_str()), CComVariant(format),
                    CComVariant(L"lock:FALSE")}, &result), result);
        if (observed) {
            observed = queryCounter->ActivateDocumentSession(
                countedHwp, path);
        }
        if (observed) {
            queryCounter->BeginPass();
            const bool firstObserved = Observe(
                countedHwp, kFixtureNames[index], &first[index],
                SaveCleanRestorationInjection::None, queryCounter);
            firstQueries[index] = queryCounter->Snapshot();
            queryCounter->BeginPass();
            const bool secondObserved = firstObserved && Observe(
                countedHwp, kFixtureNames[index], &second[index],
                SaveCleanRestorationInjection::None, queryCounter);
            secondQueries[index] = queryCounter->Snapshot();
            observed = firstObserved && secondObserved;
            if (observed && index == 1) {
                saveCleanFixtureOpened = true;
                saveCleanInjections = RunSaveCleanRestorationInjections(
                    hwp, kFixtureNames[index]);
            }
        }
        queryCounter->EndDocumentSession();
        result.Clear();
        const HRESULT clearStatus = Method(hwp, L"Clear", {CComVariant(1L)}, &result);
        if (index == 1 && saveCleanFixtureOpened) {
            saveCleanFixtureClosed = SUCCEEDED(clearStatus);
        }
        observed = observed && SUCCEEDED(clearStatus);
    }
    result.Clear();
    const std::filesystem::path injectionFixture =
        runtimeRoot / kFixtureNames[2];
    const bool injectionFixtureOpened = ResultBool(
        Method(hwp, L"Open",
               {CComVariant(injectionFixture.c_str()), CComVariant(L"HWPX"),
                CComVariant(L"lock:FALSE")}, &result), result);
    RuntimeDispatchProvenanceInjectionSummary provenanceInjections;
    if (injectionFixtureOpened && queryCounter->ActivateDocumentSession(
            countedHwp, injectionFixture)) {
        provenanceInjections = RunRuntimeDispatchProvenanceInjections(
            queryCounter, countedHwp, classId, processId, injectionFixture);
        queryCounter->EndDocumentSession();
    }
    result.Clear();
    const bool injectionFixtureClosed = SUCCEEDED(
        Method(hwp, L"Clear", {CComVariant(1L)}, &result));

    bool deterministic = observed;
    bool stateRestored = observed;
    bool expectationsMatched = observed;
    for (size_t index = 0; index < first.size(); ++index) {
        first[index].expectationMatched = ManifestExpectationMatch(
            manifest, kFixtureNames[index], first[index]);
        second[index].expectationMatched = ManifestExpectationMatch(
            manifest, kFixtureNames[index], second[index]);
        deterministic = deterministic && first[index].Canonical() == second[index].Canonical();
        stateRestored = stateRestored && first[index].stateRestored && second[index].stateRestored;
        expectationsMatched = expectationsMatched &&
            first[index].expectationMatched && second[index].expectationMatched;
    }
    const std::uint64_t linkedAssetMatches = first[0].linkedAssets +
        first[1].linkedAssets + first[2].linkedAssets;
    const bool linkedAssetExpectation =
        manifest.find("\"native-link-record\"") != std::string::npos &&
        linkedAssetMatches != 0;
    expectationsMatched = expectationsMatched && linkedAssetExpectation;
    observed = observed && expectationsMatched;

    StorySink failStories;
    TextSink failText;
    PropertySink failProperties;
    hancom::graph::stories::CaptureDiagnostics failStoryDiagnostics;
    hancom::graph::text::CaptureDiagnostics failTextDiagnostics;
    hancom::graph::properties::CaptureDiagnostics failPropertyDiagnostics;
    hancom::graph::properties::EffectivePropertyContext failContext;
    hancom::graph::tables::TableGraphRecord failTable;
    std::wstring failTableError;
    hancom::graph::layout::StableLayoutRecord failLayout;
    const bool syntheticFailures =
        hancom::graph::stories::CaptureNativeStructureStories(
            nullptr, failStories, &failStoryDiagnostics) ==
            hancom::graph::stories::CaptureStatus::InvalidArgument &&
        hancom::graph::text::CaptureNativeBodyText(
            nullptr, failText, &failTextDiagnostics) ==
            hancom::graph::text::CaptureStatus::InvalidArgument &&
        hancom::graph::properties::CaptureCurrentEffectiveProperties(
            nullptr, failContext, failProperties, &failPropertyDiagnostics) ==
            hancom::graph::properties::CaptureStatus::InvalidArgument &&
        hancom::graph::tables::CaptureTableGraphFromNative(
            nullptr, L"synthetic", false, false, {}, &failTable,
            &failTableError) == hancom::graph::tables::BuildStatus::InvalidArgument &&
        hancom::graph::layout::CaptureCurrentTableLayoutFromNative(
            nullptr, L"synthetic", &failLayout) ==
            hancom::graph::layout::CaptureStatus::InvalidArgument;
    OwnerControlParityInjectionSummary parityInjections =
        RunOwnerControlParityMismatchInjections();
    parityInjections.realTableControlPresent =
        first[0].tables + first[1].tables + first[2].tables != 0;
    parityInjections.realImageControlPresent =
        first[0].images + first[1].images + first[2].images != 0;

    bool hashesPreserved = true;
    for (size_t index = 0; index < kFixtureNames.size(); ++index) {
        hashesPreserved = hashesPreserved &&
            Sha256(fixtureRoot / kFixtureNames[index]) == sourceHashes[index] &&
            Sha256(runtimeRoot / kFixtureNames[index]) == sourceHashes[index];
    }
    std::vector<std::wstring> candidateModulesAfter;
    const bool modulesEnumeratedAfter = CandidateModules(
        processId, &candidateModulesAfter);
    const bool moduleSetUnchanged = modulesEnumeratedBefore &&
        modulesEnumeratedAfter && candidateModulesBefore == candidateModulesAfter;
    countedHwp.Release();
    const bool queryWrappersReleased = queryCounter->WrapperCount() == 0;
    bool queryCountsPassed = false;
    const std::string queryReceipt = DispatchQueryReceipt(
        firstQueries, secondQueries, first, queryWrappersReleased,
        queryCounter->RuntimeRootActivated(),
        queryCounter->UntrustedDispatchCount(), provenanceInjections,
        &queryCountsPassed);

    std::ostringstream real;
    real << "{\"schema\":1,\"lane\":\"real-observation\",\"status\":\""
         << (observed ? "green" : "red") << "\",\"png\":{\"width\":"
         << pngWidth << ",\"height\":" << pngHeight << "},\"observations\":[";
    for (size_t index = 0; index < first.size(); ++index) {
        if (index != 0) real << ',';
        real << ObservationJson(first[index]);
    }
    real << "],\"asset_observation\":{\"fixture\":\"linked-image.png\","
            "\"families\":[\"raster-asset\"],\"sha256\":"
         << Json(sourceHashes[3]) << ",\"width\":" << pngWidth
         << ",\"height\":" << pngHeight
         << ",\"native_link_records\":" << linkedAssetMatches
         << ",\"expectation_match\":"
         << (linkedAssetExpectation ? "true" : "false") << "}}";
    const bool realWritten = WriteReceipt(
        receiptRoot, L"real-observation", real.str());
    const bool driftWritten = WriteReceipt(
        receiptRoot, L"deterministic-drift",
        std::string("{\"schema\":1,\"lane\":\"deterministic-drift\",\"status\":\"") +
        (deterministic ? "green" : "red") +
        "\",\"two_passes_equal\":" + (deterministic ? "true" : "false") + "}");
    const bool queryCountsWritten = WriteReceipt(
        receiptRoot, L"fixture-idispatch-query-counts", queryReceipt);
    const bool failureWritten = WriteReceipt(
        receiptRoot, L"synthetic-failure-injection",
        std::string("{\"schema\":1,\"lane\":\"synthetic-failure-injection\",\"status\":\"") +
        (syntheticFailures ? "green" : "red") +
        "\",\"null_reader_boundaries_rejected\":" +
        (syntheticFailures ? "true" : "false") + "}");
    const bool stateWritten = WriteReceipt(
        receiptRoot, L"route-caret-selection-modified-restoration",
        std::string("{\"schema\":1,\"lane\":\"route-caret-selection-modified-restoration\",\"status\":\"") +
        (stateRestored ? "green" : "red") +
        "\",\"all_passes_restored\":" + (stateRestored ? "true" : "false") +
        ",\"all_passes_save_clean\":" + (stateRestored ? "true" : "false") + "}");
    const bool saveCleanWritten = WriteReceipt(
        receiptRoot, L"save-clean-restoration-injections",
        std::string("{\"schema\":1,\"lane\":\"save-clean-restoration-injections\",\"status\":\"") +
        (saveCleanInjections.Passed() && saveCleanFixtureOpened &&
             saveCleanFixtureClosed ? "green" : "red") +
        "\",\"fixture\":\"full-spectrum.hwp\"" +
        ",\"fixture_opened\":" +
        (saveCleanFixtureOpened ? "true" : "false") +
        ",\"fixture_closed\":" +
        (saveCleanFixtureClosed ? "true" : "false") +
        ",\"after_state_capture\":" +
        (saveCleanInjections.restored[0] ? "true" : "false") +
        ",\"after_stories\":" +
        (saveCleanInjections.restored[1] ? "true" : "false") +
        ",\"after_body_text\":" +
        (saveCleanInjections.restored[2] ? "true" : "false") +
        ",\"after_effective_properties\":" +
        (saveCleanInjections.restored[3] ? "true" : "false") +
        ",\"after_owner_control_binding\":" +
        (saveCleanInjections.restored[4] ? "true" : "false") +
        ",\"after_table_reader\":" +
        (saveCleanInjections.restored[5] ? "true" : "false") +
        ",\"after_layout_reader\":" +
        (saveCleanInjections.restored[6] ? "true" : "false") +
        ",\"after_image_reader\":" +
        (saveCleanInjections.restored[7] ? "true" : "false") +
        ",\"after_child_readers\":" +
        (saveCleanInjections.restored[8] ? "true" : "false") +
        ",\"restored_count\":" +
        std::to_string(saveCleanInjections.restoredCount) +
        ",\"expected_count\":" +
        std::to_string(saveCleanInjections.expectedCount) + "}");
    const bool parityWritten = WriteReceipt(
        receiptRoot, L"owner-control-parity-injections",
        std::string("{\"schema\":1,\"lane\":\"owner-control-parity-injections\",\"status\":\"") +
        (parityInjections.Passed() ? "green" : "red") +
        "\",\"baseline_accepted\":" +
        (parityInjections.baselineAccepted ? "true" : "false") +
        ",\"real_table_control_present\":" +
        (parityInjections.realTableControlPresent ? "true" : "false") +
        ",\"real_image_control_present\":" +
        (parityInjections.realImageControlPresent ? "true" : "false") +
        ",\"owner_control_count_rejected\":" +
        (parityInjections.mismatchRejected[0] ? "true" : "false") +
        ",\"head_ctrl_ordinal_rejected\":" +
        (parityInjections.mismatchRejected[1] ? "true" : "false") +
        ",\"native_type_rejected\":" +
        (parityInjections.mismatchRejected[2] ? "true" : "false") +
        ",\"session_instance_id_rejected\":" +
        (parityInjections.mismatchRejected[3] ? "true" : "false") +
        ",\"table_child_owner_rejected\":" +
        (parityInjections.mismatchRejected[4] ? "true" : "false") +
        ",\"image_child_owner_rejected\":" +
        (parityInjections.mismatchRejected[5] ? "true" : "false") +
        ",\"rejected_mismatch_count\":" +
        std::to_string(parityInjections.rejectedMismatchCount) +
        ",\"expected_mismatch_count\":" +
        std::to_string(parityInjections.expectedMismatchCount) + "}");

    queryCounter.reset();
    static_cast<void>(lifecycle.Cleanup());
    const bool processExited = lifecycle.ProcessExited();
    const bool tempClean = lifecycle.TempRemoved();
    const bool cleanup = hashesPreserved && tempClean && processExited &&
        moduleSetUnchanged;
    const bool cleanupWritten = WriteReceipt(
        receiptRoot, L"process-module-temp-fixture-hash-cleanup",
        std::string("{\"schema\":1,\"lane\":\"process-module-temp-fixture-hash-cleanup\",\"status\":\"") +
        (cleanup ? "green" : "red") +
        "\",\"source_and_copy_hashes_preserved\":" +
        (hashesPreserved ? "true" : "false") +
        ",\"candidate_module_set_unchanged\":" +
        (moduleSetUnchanged ? "true" : "false") +
        ",\"candidate_module_count_before\":" +
        std::to_string(candidateModulesBefore.size()) +
        ",\"candidate_module_count_after\":" +
        std::to_string(candidateModulesAfter.size()) +
        ",\"owned_process_exited\":" + (processExited ? "true" : "false") +
        ",\"temp_removed\":" + (tempClean ? "true" : "false") + "}");

    const bool passed = observed && expectationsMatched && deterministic &&
        syntheticFailures && parityInjections.Passed() && stateRestored &&
        injectionFixtureOpened && injectionFixtureClosed &&
        saveCleanFixtureOpened && saveCleanFixtureClosed &&
        saveCleanInjections.Passed() && queryCountsPassed && cleanup &&
        realWritten && driftWritten && queryCountsWritten && failureWritten &&
        stateWritten && saveCleanWritten && parityWritten && cleanupWritten;
    std::wcout << L"NATIVE_STRUCTURE_FIXTURES " << (passed ? 1 : 0)
               << L" process=" << processId
               << L" observations=" << first.size()
               << L" tables=" << (first[0].tables + first[1].tables + first[2].tables)
               << L" images=" << (first[0].images + first[1].images + first[2].images)
               << L" body_story_lists="
               << (first[0].bodyStoryNativeLists +
                   first[1].bodyStoryNativeLists +
                   first[2].bodyStoryNativeLists)
               << L" cell_story_lists="
               << (first[0].cellStoryNativeLists +
                   first[1].cellStoryNativeLists +
                   first[2].cellStoryNativeLists)
               << L" caption_story_lists="
               << (first[0].captionStoryNativeLists +
                   first[1].captionStoryNativeLists +
                   first[2].captionStoryNativeLists)
               << L" linked_assets="
               << (first[0].linkedAssets + first[1].linkedAssets + first[2].linkedAssets)
               << L" owner_parity_injections="
               << parityInjections.rejectedMismatchCount << L"/"
               << parityInjections.expectedMismatchCount
               << L" save_clean_injections="
               << saveCleanInjections.restoredCount << L"/"
               << saveCleanInjections.expectedCount
               << L" dispatch_queries="
               << (queryCountsPassed ? L"matched" : L"mismatched")
               << L" layouts="
               << (first[0].stableLayouts + first[1].stableLayouts + first[2].stableLayouts)
               << L'\n';
    return passed ? 0 : 10;
}
