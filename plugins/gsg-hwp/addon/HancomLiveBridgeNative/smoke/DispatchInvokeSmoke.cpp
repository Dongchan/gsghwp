#include <Windows.h>
#include <OleAuto.h>
#include <atlcomcli.h>

#include "../DispatchInvoke.h"

#include <array>
#include <atomic>
#include <cstdint>
#include <cwchar>
#include <iostream>
#include <thread>
#include <vector>

namespace {

class FakeTypeInfo final : public ITypeInfo {
public:
    FakeTypeInfo(
        const TYPEATTR& attribute,
        const DISPID alpha,
        const DISPID beta) noexcept
        : attribute_(attribute), alpha_(alpha), beta_(beta) {}

    LONG ReferenceCount() const noexcept { return references_.load(); }

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** object) override {
        if (object == nullptr) return E_POINTER;
        *object = nullptr;
        if (iid == IID_IUnknown || iid == IID_ITypeInfo) {
            *object = static_cast<ITypeInfo*>(this);
            AddRef();
            return S_OK;
        }
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override {
        return static_cast<ULONG>(++references_);
    }
    ULONG STDMETHODCALLTYPE Release() override {
        return static_cast<ULONG>(--references_);
    }
    HRESULT STDMETHODCALLTYPE GetTypeAttr(TYPEATTR** value) override {
        if (value == nullptr) return E_POINTER;
        *value = &attribute_;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetTypeComp(ITypeComp**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetFuncDesc(UINT, FUNCDESC**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetVarDesc(UINT, VARDESC**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetNames(MEMBERID, BSTR*, UINT, UINT*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetRefTypeOfImplType(UINT, HREFTYPE*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetImplTypeFlags(UINT, INT*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetIDsOfNames(
        LPOLESTR* names, UINT count, MEMBERID* members) override {
        if (names == nullptr || members == nullptr || count != 1U) return E_INVALIDARG;
        if (std::wcscmp(names[0], L"Alpha") == 0) {
            members[0] = alpha_;
            return S_OK;
        }
        if (std::wcscmp(names[0], L"Beta") == 0) {
            members[0] = beta_;
            return S_OK;
        }
        return DISP_E_UNKNOWNNAME;
    }
    HRESULT STDMETHODCALLTYPE Invoke(PVOID, MEMBERID, WORD, DISPPARAMS*, VARIANT*, EXCEPINFO*, UINT*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetDocumentation(MEMBERID, BSTR*, BSTR*, DWORD*, BSTR*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetDllEntry(MEMBERID, INVOKEKIND, BSTR*, BSTR*, WORD*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetRefTypeInfo(HREFTYPE, ITypeInfo**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE AddressOfMember(MEMBERID, INVOKEKIND, PVOID*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE CreateInstance(IUnknown*, REFIID, PVOID*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetMops(MEMBERID, BSTR*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetContainingTypeLib(ITypeLib**, UINT*) override { return E_NOTIMPL; }
    void STDMETHODCALLTYPE ReleaseTypeAttr(TYPEATTR*) override {}
    void STDMETHODCALLTYPE ReleaseFuncDesc(FUNCDESC*) override {}
    void STDMETHODCALLTYPE ReleaseVarDesc(VARDESC*) override {}

private:
    std::atomic<LONG> references_{1};
    TYPEATTR attribute_{};
    DISPID alpha_ = DISPID_UNKNOWN;
    DISPID beta_ = DISPID_UNKNOWN;
};

class FakeDispatch final : public IDispatch {
public:
    FakeDispatch(FakeTypeInfo* typeInfo, LONG objectTag, DISPID alpha, DISPID beta) noexcept
        : typeInfo_(typeInfo), objectTag_(objectTag), alpha_(alpha), beta_(beta) {}

    void SetAvailable(bool value) noexcept { available_.store(value); }
    LONG TypeInfoCount() const noexcept { return typeInfoCount_.load(); }
    LONG LookupCount() const noexcept { return lookupCount_.load(); }
    LONG InvokeCount() const noexcept { return invokeCount_.load(); }

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** object) override {
        if (object == nullptr) return E_POINTER;
        *object = nullptr;
        if (iid == IID_IUnknown || iid == IID_IDispatch) {
            *object = static_cast<IDispatch*>(this);
            AddRef();
            return S_OK;
        }
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return static_cast<ULONG>(++references_); }
    ULONG STDMETHODCALLTYPE Release() override { return static_cast<ULONG>(--references_); }
    HRESULT STDMETHODCALLTYPE GetTypeInfoCount(UINT* count) override {
        if (count == nullptr) return E_POINTER;
        *count = typeInfo_ == nullptr ? 0U : 1U;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetTypeInfo(UINT index, LCID, ITypeInfo** value) override {
        ++typeInfoCount_;
        if (value == nullptr) return E_POINTER;
        *value = nullptr;
        if (typeInfo_ == nullptr || index != 0U) return DISP_E_BADINDEX;
        typeInfo_->AddRef();
        *value = typeInfo_;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetIDsOfNames(
        REFIID, LPOLESTR* names, UINT count, LCID, DISPID* members) override {
        ++lookupCount_;
        if (names == nullptr || members == nullptr || count != 1U) return E_INVALIDARG;
        if (!available_.load()) return DISP_E_UNKNOWNNAME;
        if (std::wcscmp(names[0], L"Alpha") == 0) {
            members[0] = alpha_;
            return S_OK;
        }
        if (std::wcscmp(names[0], L"Beta") == 0) {
            members[0] = beta_;
            return S_OK;
        }
        return DISP_E_UNKNOWNNAME;
    }
    HRESULT STDMETHODCALLTYPE Invoke(
        DISPID member, REFIID, LCID, WORD, DISPPARAMS*, VARIANT* result,
        EXCEPINFO*, UINT*) override {
        ++invokeCount_;
        if (member != alpha_ && member != beta_) return DISP_E_MEMBERNOTFOUND;
        if (result != nullptr) {
            VariantInit(result);
            result->vt = VT_I8;
            result->llVal = static_cast<LONGLONG>(objectTag_) * 100000LL + member;
        }
        return S_OK;
    }

private:
    std::atomic<LONG> references_{1};
    FakeTypeInfo* typeInfo_ = nullptr;
    LONG objectTag_ = 0;
    DISPID alpha_ = DISPID_UNKNOWN;
    DISPID beta_ = DISPID_UNKNOWN;
    std::atomic<bool> available_{true};
    std::atomic<LONG> typeInfoCount_{0};
    std::atomic<LONG> lookupCount_{0};
    std::atomic<LONG> invokeCount_{0};
};

TYPEATTR MakeIdentity(
    const GUID& guid, WORD major = 1, WORD minor = 0,
    LCID lcid = MAKELCID(MAKELANGID(LANG_ENGLISH, SUBLANG_ENGLISH_US), SORT_DEFAULT)) noexcept {
    TYPEATTR value{};
    value.guid = guid;
    value.lcid = lcid;
    value.dwReserved = 0;
    value.wMajorVerNum = major;
    value.wMinorVerNum = minor;
    value.typekind = TKIND_DISPATCH;
    value.wTypeFlags = TYPEFLAG_FDUAL | TYPEFLAG_FOLEAUTOMATION;
    return value;
}

GUID NewGuid() noexcept {
    GUID value{};
    static_cast<void>(CoCreateGuid(&value));
    return value;
}

hancom::dispatch::TypeIdentityToken Token(const TYPEATTR& type) noexcept {
    return {
        type.guid,
        type.lcid,
        type.wMajorVerNum,
        type.wMinorVerNum,
        type.typekind,
        type.wTypeFlags,
    };
}

bool ReadValue(
    FakeDispatch* object,
    const hancom::dispatch::TypeIdentityToken* type,
    const wchar_t* name,
    LONGLONG expected) {
    CComVariant value;
    const HRESULT status = type == nullptr
        ? hancom::dispatch::PropertyGet(object, name, &value)
        : hancom::dispatch::PropertyGetQualified(object, *type, name, &value);
    return status == S_OK && value.vt == VT_I8 && value.llVal == expected;
}

bool SharedQualifiedIdentityReusesDispidOnCurrentObjects() {
    const TYPEATTR identity = MakeIdentity(NewGuid());
    FakeTypeInfo firstInfo(identity, 41, 42);
    FakeTypeInfo secondInfo(identity, 41, 42);
    FakeDispatch first(&firstInfo, 1, 41, 42);
    FakeDispatch second(&secondInfo, 2, 41, 42);
    const auto token = Token(identity);
    const bool passed =
        ReadValue(&first, &token, L"Alpha", 100041) &&
        ReadValue(&second, &token, L"Alpha", 200041) &&
        first.LookupCount() + second.LookupCount() == 1 &&
        first.InvokeCount() == 1 && second.InvokeCount() == 1;
    return passed && firstInfo.ReferenceCount() == 1 && secondInfo.ReferenceCount() == 1;
}

bool QualifiedIdentityFieldsDoNotCollide() {
    const GUID baseGuid = NewGuid();
    TYPEATTR base = MakeIdentity(baseGuid, 3, 7, 1033);
    TYPEATTR otherGuid = base;
    otherGuid.guid = NewGuid();
    TYPEATTR otherVersion = base;
    otherVersion.wMinorVerNum = 8;
    TYPEATTR otherLcid = base;
    otherLcid.lcid = 1042;
    FakeTypeInfo baseInfo(base, 101, 102);
    FakeTypeInfo guidInfo(otherGuid, 201, 202);
    FakeTypeInfo versionInfo(otherVersion, 301, 302);
    FakeTypeInfo lcidInfo(otherLcid, 401, 402);
    FakeDispatch a(&baseInfo, 1, 101, 102);
    FakeDispatch b(&guidInfo, 2, 201, 202);
    FakeDispatch c(&versionInfo, 3, 301, 302);
    FakeDispatch d(&lcidInfo, 4, 401, 402);
    const auto aToken = Token(base);
    const auto bToken = Token(otherGuid);
    const auto cToken = Token(otherVersion);
    const auto dToken = Token(otherLcid);
    return ReadValue(&a, &aToken, L"Alpha", 100101) &&
        ReadValue(&b, &bToken, L"Alpha", 200201) &&
        ReadValue(&c, &cToken, L"Alpha", 300301) &&
        ReadValue(&d, &dToken, L"Alpha", 400401) &&
        a.LookupCount() == 1 && b.LookupCount() == 1 &&
        c.LookupCount() == 1 && d.LookupCount() == 1;
}

bool NoTypeInfoAlwaysLooksUp() {
    FakeDispatch object(nullptr, 5, 51, 52);
    return ReadValue(&object, nullptr, L"Alpha", 500051) &&
        ReadValue(&object, nullptr, L"Alpha", 500051) &&
        object.TypeInfoCount() == 0 &&
        object.LookupCount() == 2 && object.InvokeCount() == 2;
}

bool FailedLookupIsNotCached() {
    const TYPEATTR identity = MakeIdentity(NewGuid());
    FakeTypeInfo info(identity, 61, 62);
    FakeDispatch object(&info, 6, 61, 62);
    object.SetAvailable(false);
    CComVariant ignored;
    const auto token = Token(identity);
    const HRESULT failed = hancom::dispatch::PropertyGetQualified(
        &object, token, L"Alpha", &ignored);
    object.SetAvailable(true);
    return failed == DISP_E_UNKNOWNNAME &&
        ReadValue(&object, &token, L"Alpha", 600061) &&
        object.LookupCount() == 2 && object.InvokeCount() == 1;
}

bool MemberNamesDoNotCollide() {
    const TYPEATTR identity = MakeIdentity(NewGuid());
    FakeTypeInfo info(identity, 71, 72);
    FakeDispatch object(&info, 7, 71, 72);
    const auto token = Token(identity);
    return ReadValue(&object, &token, L"Alpha", 700071) &&
        ReadValue(&object, &token, L"Beta", 700072) &&
        ReadValue(&object, &token, L"Alpha", 700071) &&
        ReadValue(&object, &token, L"Beta", 700072) &&
        object.LookupCount() == 2 && object.InvokeCount() == 4;
}

bool CallerWithoutPrevalidatedTokenIsNotQualified() {
    FakeTypeInfo info(MakeIdentity(NewGuid()), 73, 74);
    FakeDispatch object(&info, 7, 73, 74);
    return ReadValue(&object, nullptr, L"Alpha", 700073) &&
        ReadValue(&object, nullptr, L"Alpha", 700073) &&
        object.TypeInfoCount() == 0 &&
        object.LookupCount() == 2 && object.InvokeCount() == 2;
}

bool ThreadLifetimeIsIsolatedAndSafe() {
    const TYPEATTR identity = MakeIdentity(NewGuid());
    FakeTypeInfo firstInfo(identity, 81, 82), secondInfo(identity, 81, 82);
    FakeDispatch first(&firstInfo, 8, 81, 82);
    FakeDispatch second(&secondInfo, 9, 81, 82);
    std::atomic<bool> firstPassed{false};
    std::atomic<bool> secondPassed{false};
    const auto token = Token(identity);
    std::thread firstThread([&] {
        firstPassed = ReadValue(&first, &token, L"Alpha", 800081) &&
            ReadValue(&first, &token, L"Alpha", 800081);
    });
    std::thread secondThread([&] {
        secondPassed = ReadValue(&second, &token, L"Alpha", 900081) &&
            ReadValue(&second, &token, L"Alpha", 900081);
    });
    firstThread.join();
    secondThread.join();
    return firstPassed && secondPassed &&
        first.LookupCount() == 1 && second.LookupCount() == 1 &&
        first.InvokeCount() == 2 && second.InvokeCount() == 2 &&
        firstInfo.ReferenceCount() == 1 && secondInfo.ReferenceCount() == 1;
}

bool ProductionReceiptCountRegression() {
    constexpr std::uint64_t baselineLookups = 112080;
    constexpr std::uint64_t successfulInvokes = 88405;
    constexpr std::uint64_t positiveLookupCeiling = 24187;
    constexpr std::size_t objectCount = 32;
    const TYPEATTR identity = MakeIdentity(NewGuid(), 5, 2, 1042);
    FakeTypeInfo info(identity, 501, 502);
    const auto token = Token(identity);
    std::array<FakeDispatch*, objectCount> objects{};
    for (std::size_t index = 0; index != objectCount; ++index) {
        objects[index] = new FakeDispatch(
            &info, static_cast<LONG>(index + 1), 501, 502);
    }
    std::uint64_t semanticChecksum = 0;
    std::uint64_t expectedChecksum = 0;
    bool valuesMatched = true;
    for (std::uint64_t index = 0; index != successfulInvokes; ++index) {
        const std::size_t objectIndex = static_cast<std::size_t>(index % objectCount);
        CComVariant value;
        const HRESULT status = hancom::dispatch::PropertyGetQualified(
            objects[objectIndex], token, L"Alpha", &value);
        const LONGLONG expected =
            static_cast<LONGLONG>(objectIndex + 1) * 100000LL + 501;
        valuesMatched = valuesMatched && status == S_OK &&
            value.vt == VT_I8 && value.llVal == expected;
        semanticChecksum += static_cast<std::uint64_t>(value.llVal);
        expectedChecksum += static_cast<std::uint64_t>(expected);
    }
    std::uint64_t typeInfoCalls = 0;
    std::uint64_t lookups = 0;
    std::uint64_t invokes = 0;
    for (FakeDispatch* object : objects) {
        typeInfoCalls += static_cast<std::uint64_t>(object->TypeInfoCount());
        lookups += static_cast<std::uint64_t>(object->LookupCount());
        invokes += static_cast<std::uint64_t>(object->InvokeCount());
        delete object;
    }
    const std::uint64_t projectedLookups =
        baselineLookups - successfulInvokes + lookups;
    std::wcout << L"DISPID_CACHE_RECEIPT baseline_getids=" << baselineLookups
               << L" baseline_invoke=" << successfulInvokes
               << L" simulated_invoke=" << invokes
               << L" typeinfo=" << typeInfoCalls
               << L" cached_path_getids=" << lookups
               << L" projected_getids=" << projectedLookups
               << L" positive_ceiling=" << positiveLookupCeiling
               << L" semantic_checksum=" << semanticChecksum << L'\n';
    return valuesMatched && semanticChecksum == expectedChecksum &&
        invokes == successfulInvokes && typeInfoCalls == 0 && lookups > 0 &&
        projectedLookups <= positiveLookupCeiling &&
        info.ReferenceCount() == 1;
}

} // namespace

int wmain() {
    const HRESULT initialized = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (FAILED(initialized)) return 2;
    const std::array<std::pair<const wchar_t*, bool(*)()>, 8> tests{{
        {L"shared-qualified-current-objects", SharedQualifiedIdentityReusesDispidOnCurrentObjects},
        {L"qualified-fields-no-collision", QualifiedIdentityFieldsDoNotCollide},
        {L"no-type-info-uncached", NoTypeInfoAlwaysLooksUp},
        {L"failed-lookup-not-cached", FailedLookupIsNotCached},
        {L"member-names-no-collision", MemberNamesDoNotCollide},
        {L"caller-without-token-uncached", CallerWithoutPrevalidatedTokenIsNotQualified},
        {L"thread-lifetime-isolated", ThreadLifetimeIsIsolatedAndSafe},
        {L"production-receipt-count", ProductionReceiptCountRegression},
    }};
    bool passed = true;
    for (const auto& test : tests) {
        const bool current = test.second();
        std::wcout << L"DISPATCH_INVOKE_TEST " << test.first << L' ' << current << L'\n';
        passed = passed && current;
    }
    CoUninitialize();
    std::wcout << L"DISPATCH_INVOKE_CACHE " << passed << L'\n';
    return passed ? 0 : 1;
}
