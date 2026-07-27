from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "addon" / "HancomLiveBridgeNative"


def _function_source(source: str, signature: str) -> str:
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unterminated function: {signature}")


def _compact(source: str) -> str:
    return "".join(source.split())


def test_native_rot_keeps_only_batch_publications_alive_and_revokes_them() -> None:
    bridge = (NATIVE / "Bridge.cpp").read_text(encoding="utf-8")
    batch_header = (NATIVE / "BatchAutomation.h").read_text(encoding="utf-8")
    register_pair = _compact(_function_source(bridge, "static HRESULT RegisterPair"))
    register_object = _compact(
        _function_source(bridge, "static HRESULT RegisterObject")
    )
    publish = _compact(_function_source(bridge, "HRESULT Publish"))
    revoke = _compact(_function_source(bridge, "HRESULT Revoke()"))
    revoke_cookie = _compact(
        _function_source(bridge, "static HRESULT RevokeCookie")
    )

    assert "constDWORDflags" in register_object
    assert "table->Register(flags,object,moniker,cookie)" in register_object
    assert (
        "kMonikerPrefix,windowHandle,documentId,0,object,registrationCookie"
        in register_pair
    )
    assert (
        "kBatchMonikerPrefix,windowHandle,documentId,"
        "ROTFLAGS_REGISTRATIONKEEPSALIVE,batch,batchRegistrationCookie"
        in register_pair
    )
    assert bridge.count("ROTFLAGS_REGISTRATIONKEEPSALIVE") == 1

    revoke_process_pair = (
        "RevokePair(table,registrationCookie_,batchRegistrationCookie_,batch_)"
    )
    assert revoke_process_pair in publish
    assert revoke_process_pair in revoke
    assert "returnModule().Revoke();" in _compact(bridge)
    assert "if(SUCCEEDED(status)){cookie=0;}" in revoke_cookie
    assert "windowPublications_.push_back" not in publish
    assert "documentPublications_.push_back" not in publish
    assert "ReservePublicationSlot(windowPublications_)" in publish
    assert "ReservePublicationSlot(documentPublications_)" in publish
    assert publish.index("ReservePublicationSlot(windowPublications_)") < publish.index(
        revoke_process_pair
    )

    assert "CComPtr<IDispatch> hwp_" not in batch_header
    assert "IDispatch* hwp_" in batch_header
    assert "BatchAutomation* batch" in bridge


def test_native_smoke_runs_two_independent_cross_process_probes() -> None:
    smoke = (NATIVE / "smoke" / "BridgeSmoke.cpp").read_text(encoding="utf-8")
    probe_twice = _compact(
        _function_source(smoke, "int ProbePublishedProcessTwice")
    )
    probe = _compact(_function_source(smoke, "int ProbePublishedProcess("))

    assert "--probe-twice" in smoke
    assert probe_twice.count("RunProbeChild(processIdArgument)") == 2
    assert "returnfirst==0&&second==0?0:10;" in probe_twice
    assert (
        'IsPublishedObject(L"HancomLiveBridge."+processId)' in probe
    )
    assert (
        'GetPublishedObject(L"HancomLiveBridge."+processId)' not in probe
    )
