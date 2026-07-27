from __future__ import annotations

# pyright: reportPrivateUsage=false

import ast
import json
import sys
from base64 import b64encode
from pathlib import Path
from typing import final
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "automate-hancom-documents" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import hwp_native_capabilities as capabilities_module  # noqa: E402
from hwp_errors import HwpLiveError  # noqa: E402
from hwp_live_native_batch import execute_native_lifecycle  # noqa: E402
from hwp_live_native_batch import (  # noqa: E402
    _native_dispatch_state,
    clear_native_document_route,
)
from hwp_native_capabilities import (  # noqa: E402
    NativeCapability,
    NativeCapabilityInventory,
    native_capability_inventory,
)


NATIVE_BATCH_SOURCE = SCRIPTS / "hwp_live_native_batch.py"


def _deployed_protocol() -> int:
    manifest = json.loads((ROOT / "compatibility-manifest.json").read_text("utf-8"))
    protocol = manifest["protocol"]
    assert isinstance(protocol, int)
    return protocol


DEPLOYED_PROTOCOL = _deployed_protocol()


def _encode(value: str) -> str:
    return b64encode(value.encode("utf-8")).decode("ascii")


def _hcl12_payload() -> str:
    return "\t".join(
        (
            "HCL12",
            "0",
            _encode("C:/documents/sample.hwp"),
            "5",
            "0",
            "8",
            "111",
            "222",
            "333",
            "0",
            "1",
            "0",
            "0",
            "1",
            "0",
            "1",
            "0",
            "0",
            "1",
            "5",
            "0",
            "8",
            "111",
            "222",
            "333",
            "300",
        )
    )


def _legacy_lifecycle_payload() -> str:
    """A pre-protocol-12 SaveReopenVerify response.

    Protocol 12 is what "separates non-destructive SaveVerify from the explicit
    SaveReopenVerify diagnostic and adds full text/document fingerprints plus
    failed-reopen session recovery" (addon/HancomLiveBridgeNative/README.md).
    An older bridge therefore cannot produce the HCL12 shape the decoder wants.
    """
    return "\t".join(("HCL8", "0", _encode("C:/documents/sample.hwp"), "5", "0", "8"))


@final
class _Moniker:
    def __init__(self, name: str) -> None:
        self.name = name

    def GetDisplayName(self, context: object, moniker: object) -> str:
        _ = (context, moniker)
        return self.name


@final
class _Source:
    def QueryInterface(self, interface_id: object) -> object:
        _ = interface_id
        return self


@final
class _Rot:
    def __init__(self, names: tuple[str, ...]) -> None:
        self.monikers = tuple(_Moniker(name) for name in names)
        self.sources = {moniker.name: _Source() for moniker in self.monikers}

    def EnumRunning(self) -> tuple[_Moniker, ...]:
        return self.monikers

    def GetObject(self, moniker: object) -> _Source:
        assert isinstance(moniker, _Moniker)
        return self.sources[moniker.name]


@final
class _PythonCom:
    IID_IDispatch = object()

    def __init__(self, rot: _Rot) -> None:
        self.rot = rot

    def CoInitialize(self) -> None:
        pass

    def CoUninitialize(self) -> None:
        pass

    def CreateBindCtx(self, reserved: int) -> object:
        _ = reserved
        return object()

    def GetRunningObjectTable(self) -> _Rot:
        return self.rot


@final
class _Process:
    def GetWindowThreadProcessId(self, window_handle: int) -> tuple[int, int]:
        _ = window_handle
        return 7, 1234


@final
class _VersionedBatch:
    """A bridge that answers exactly like the native DLL of its protocol version."""

    def __init__(self, protocol_version: int) -> None:
        self._protocol_version = protocol_version
        self.save_reopen_verify_calls = 0

    @property
    def ProtocolVersion(self) -> int:
        return self._protocol_version

    @property
    def TargetDocumentID(self) -> int:
        return 0

    def SaveReopenVerify(self) -> str:
        self.save_reopen_verify_calls += 1
        if self._protocol_version >= 12:
            return _hcl12_payload()
        return _legacy_lifecycle_payload()


@final
class _BatchClient:
    def __init__(self, batch: _VersionedBatch) -> None:
        self.batch = batch

    def Dispatch(self, source: object) -> _VersionedBatch:
        _ = source
        return self.batch


def _run_lifecycle(protocol_version: int) -> tuple[_VersionedBatch, BaseException | None]:
    clear_native_document_route(5678)
    _native_dispatch_state.entries = {}
    _native_dispatch_state.operation_entries = {}
    batch = _VersionedBatch(protocol_version)
    modules = (
        _PythonCom(_Rot(("!HancomLiveBatch.1234.5678",))),
        _BatchClient(batch),
        _Process(),
    )
    with patch("hwp_live_native_batch._modules", return_value=modules):
        try:
            _ = execute_native_lifecycle(5678)
        except BaseException as error:  # noqa: BLE001 - the test classifies it
            return batch, error
    return batch, None


# --------------------------------------------------------------------------
# Item 1: the lifecycle gate must not admit a bridge the decoder will reject.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("protocol_version", range(1, 13))
def test_lifecycle_gate_never_admits_a_response_the_decoder_rejects(
    protocol_version: int,
) -> None:
    """Core invariant: a request the gate lets through must survive decoding.

    Every bridge version either is refused by the gate for being too old, or
    decodes cleanly. A response-format failure means the gate and the decoder
    disagree about which protocol they are talking to.
    """
    batch, error = _run_lifecycle(protocol_version)

    if error is None:
        assert batch.save_reopen_verify_calls == 1
        return
    assert isinstance(error, HwpLiveError)
    assert "프로토콜 버전이 낮습니다" in str(error), (
        f"protocol {protocol_version}: gate admitted the call but decoding failed"
        f" with {error!r}"
    )
    assert batch.save_reopen_verify_calls == 0


def test_lifecycle_rejects_bridge_one_below_the_decoded_wire_format() -> None:
    """Safety: an 11 bridge has no HCL12 response, so it must be refused."""
    batch, error = _run_lifecycle(11)

    assert isinstance(error, HwpLiveError)
    assert "프로토콜 버전이 낮습니다" in str(error)
    assert batch.save_reopen_verify_calls == 0


def test_lifecycle_succeeds_on_the_deployed_native_protocol() -> None:
    """Safety: nothing that works on the shipped native may start failing."""
    batch, error = _run_lifecycle(DEPLOYED_PROTOCOL)

    assert error is None
    assert batch.save_reopen_verify_calls == 1


# --------------------------------------------------------------------------
# Item 1b: version requirements hidden in positional arguments.
# --------------------------------------------------------------------------


def _gate_call_sites() -> tuple[tuple[int, int | None], ...]:
    """Every `_batch_for_window` call, by AST, so positional gates cannot hide.

    `_batch_for_window(handle, 8, *modules)` passes its minimum version
    positionally; grepping for `minimum_version=` misses it entirely.
    """
    tree = ast.parse(NATIVE_BATCH_SOURCE.read_text("utf-8"))
    sites: list[tuple[int, int | None]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Name) or func.id != "_batch_for_window":
            continue
        version: int | None = None
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            constant = node.args[1].value
            if isinstance(constant, int):
                version = constant
        for keyword in node.keywords:
            if keyword.arg == "minimum_version" and isinstance(
                keyword.value, ast.Constant
            ):
                value = keyword.value.value
                if isinstance(value, int):
                    version = value
        sites.append((node.lineno, version))
    return tuple(sites)


def test_ast_sweep_actually_finds_the_known_positional_gate() -> None:
    """Guard the guard: prove this sweep sees a positional version argument."""
    sites = _gate_call_sites()

    assert sites, "AST sweep found no _batch_for_window calls at all"
    assert any(version is not None for _, version in sites), (
        "AST sweep found calls but resolved no version constant; it is broken"
    )


def test_every_native_gate_is_satisfied_by_the_deployed_native_protocol() -> None:
    """Safety: no gate may demand more than the shipped native provides."""
    too_new = {
        line: version
        for line, version in _gate_call_sites()
        if version is not None and version > DEPLOYED_PROTOCOL
    }

    assert not too_new, (
        f"gates above deployed protocol {DEPLOYED_PROTOCOL}: {too_new}"
    )


def test_lifecycle_gate_matches_the_decoder_wire_format() -> None:
    """The SaveReopenVerify gate must equal the protocol that introduced HCL12."""
    source = NATIVE_BATCH_SOURCE.read_text("utf-8")
    tree = ast.parse(source)
    gates: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != "execute_native_lifecycle":
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "_batch_for_window"
                and len(inner.args) >= 2
                and isinstance(inner.args[1], ast.Constant)
                and isinstance(inner.args[1].value, int)
            ):
                gates.append(inner.args[1].value)

    assert gates == [12], (
        "execute_native_lifecycle decodes an HCL12 response, so its gate must be 12"
    )


# --------------------------------------------------------------------------
# Item 2: capability inventory must model per-capability requirements.
# --------------------------------------------------------------------------


def test_capability_requirements_are_expressed_per_capability() -> None:
    inventory = native_capability_inventory("production")
    requirements = {
        capability.capability_id: capabilities_module.capability_protocol_requirement(
            capability.capability_id
        )
        for capability in inventory.capabilities
    }

    assert len(set(requirements.values())) > 1, (
        "every capability still reports the same requirement; nothing is per-feature"
    )


@pytest.mark.parametrize(
    ("capability_id", "expected"),
    (
        ("save", 12),
        ("save_reopen_verify", 12),
        ("undo", 12),
        ("redo", 12),
        ("patch_text", 11),
        ("append_layout", 10),
        ("insert_layout", 10),
    ),
)
def test_capability_requirement_matches_native_evidence(
    capability_id: str, expected: int
) -> None:
    """Requirements are grounded in the native protocol changelog and the gates."""
    assert (
        capabilities_module.capability_protocol_requirement(capability_id) == expected
    )


def test_advertised_protocol_covers_every_capability() -> None:
    """The one public number must not under-state what the feature set needs."""
    for profile in ("production", "qa"):
        inventory = native_capability_inventory(profile)
        required = max(
            capabilities_module.capability_protocol_requirement(
                capability.capability_id
            )
            for capability in inventory.capabilities
        )
        assert inventory.native_protocol_required == required
        assert inventory.native_protocol_required <= DEPLOYED_PROTOCOL


def test_public_capability_schema_is_unchanged() -> None:
    """The inventory is the hwp_get_capabilities return type; its schema is public."""
    assert set(NativeCapabilityInventory.model_fields) == {
        "native_protocol_required",
        "capabilities",
    }
    assert set(NativeCapability.model_fields) == {
        "capability_id",
        "category",
        "operation",
        "execution_path",
        "tool_names",
        "requires_session",
        "requires_state_token",
    }
    assert isinstance(
        native_capability_inventory("production").native_protocol_required, int
    )


def test_production_capability_count_is_unchanged() -> None:
    manifest = json.loads((ROOT / "compatibility-manifest.json").read_text("utf-8"))
    expected = manifest["tool_catalogs"]["worker_tools"]["count"]

    assert len(native_capability_inventory("production").capabilities) == expected
