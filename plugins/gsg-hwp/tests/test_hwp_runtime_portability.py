from __future__ import annotations

# pyright: reportPrivateUsage=false

import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    ROOT
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_runtime_preflight import (  # noqa: E402
    RuntimePreflightFailure,
    _exception_summary,
    verify_runtime,
)


def _portable_root(tmp_path: Path) -> Path:
    root = tmp_path / "다른 사용자 이름" / "한컴 플러그인"
    native = (
        root
        / "addon"
        / "HancomLiveBridgeNative"
        / "bin"
        / "9.9.9"
        / "HancomLiveBridge.dll"
    )
    launcher = (
        root
        / "addon"
        / "HancomMcpLauncher"
        / "bin"
        / "Release"
        / "HancomMcpLauncher.exe"
    )
    native.parent.mkdir(parents=True)
    launcher.parent.mkdir(parents=True)
    native_bytes = b"native-runtime"
    launcher_bytes = b"launcher-runtime"
    _ = native.write_bytes(native_bytes)
    _ = launcher.write_bytes(launcher_bytes)
    manifest = {
        "native_bridge": "9.9.9",
        "native_sha256": sha256(native_bytes).hexdigest(),
        "launcher_sha256": sha256(launcher_bytes).hexdigest(),
        "protocol": 12,
    }
    _ = (root / "compatibility-manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    return root


def _module_loader(_: str) -> object:
    return object()


def test_runtime_preflight_accepts_a_different_user_and_korean_space_path(
    tmp_path: Path,
) -> None:
    root = _portable_root(tmp_path)

    report = verify_runtime(
        root,
        version_info=(3, 12, 7),
        module_loader=_module_loader,
    )

    assert report.python_version == "3.12.7"
    assert report.protocol == 12
    assert report.native_sha256 == (
        json.loads(
            (root / "compatibility-manifest.json").read_text(encoding="utf-8")
        )["native_sha256"]
    )


def test_runtime_preflight_rejects_python_311_with_bootstrap_guidance(
    tmp_path: Path,
) -> None:
    root = _portable_root(tmp_path)

    with pytest.raises(RuntimePreflightFailure) as failure:
        _ = verify_runtime(
            root,
            version_info=(3, 11, 9),
            module_loader=_module_loader,
        )

    assert failure.value.code == "PYTHON_VERSION_UNSUPPORTED"
    assert "3.12" in failure.value.message
    assert "bootstrap_runtime.ps1" in failure.value.recovery


def test_runtime_preflight_reports_the_missing_required_module(
    tmp_path: Path,
) -> None:
    root = _portable_root(tmp_path)

    def missing_module(name: str) -> object:
        if name == "pythoncom":
            raise ModuleNotFoundError(name)
        return object()

    with pytest.raises(RuntimePreflightFailure) as failure:
        _ = verify_runtime(
            root,
            version_info=(3, 12, 7),
            module_loader=missing_module,
        )

    assert failure.value.code == "REQUIRED_MODULE_MISSING"
    assert "pythoncom" in failure.value.message
    assert "bootstrap_runtime.ps1" in failure.value.recovery


def test_runtime_preflight_rejects_native_checksum_mismatch(
    tmp_path: Path,
) -> None:
    root = _portable_root(tmp_path)
    native = (
        root
        / "addon"
        / "HancomLiveBridgeNative"
        / "bin"
        / "9.9.9"
        / "HancomLiveBridge.dll"
    )
    _ = native.write_bytes(b"tampered-native-runtime")

    with pytest.raises(RuntimePreflightFailure) as failure:
        _ = verify_runtime(
            root,
            version_info=(3, 12, 7),
            module_loader=_module_loader,
        )

    assert failure.value.code == "NATIVE_CHECKSUM_MISMATCH"
    assert "bootstrap_runtime.ps1" in failure.value.recovery


def test_runtime_preflight_rejects_protocol_mismatch(tmp_path: Path) -> None:
    root = _portable_root(tmp_path)
    manifest_path = root / "compatibility-manifest.json"
    manifest = cast(
        dict[str, object],
        json.loads(manifest_path.read_text(encoding="utf-8")),
    )
    manifest["protocol"] = 11
    _ = manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RuntimePreflightFailure) as failure:
        _ = verify_runtime(
            root,
            version_info=(3, 12, 7),
            module_loader=_module_loader,
        )

    assert failure.value.code == "PROTOCOL_MISMATCH"


def test_mcp_configuration_uses_runtime_preflight_wrapper() -> None:
    configuration = cast(
        dict[str, object],
        json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8")),
    )
    servers = cast(dict[str, object], configuration["mcpServers"])
    server = cast(dict[str, object], servers["gsg-hwp-beta-live"])
    args = cast(list[str], server["args"])

    assert args == [
        "./.venv/Scripts/python.exe",
        "-X",
        "utf8",
        "-B",
        "./skills/automate-hancom-documents/scripts/hwp_runtime_preflight.py",
    ]


def test_launcher_missing_python_reports_the_bootstrap_command() -> None:
    launcher = (
        ROOT
        / "addon"
        / "HancomMcpLauncher"
        / "bin"
        / "Release"
        / "HancomMcpLauncher.exe"
    )

    result = subprocess.run(
        [str(launcher), ".\\runtime\\missing-python.exe"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert "bootstrap_runtime.ps1" in result.stderr


def test_bootstrap_is_staged_and_never_rewrites_pyvenv_cfg() -> None:
    bootstrap = (ROOT / "runtime" / "bootstrap_runtime.ps1").read_text(
        encoding="utf-8"
    )

    assert ".venv.staging-" in bootstrap
    assert "pyvenv.cfg" not in bootstrap
    assert "3.12" in bootstrap
    assert "hwp_runtime_preflight.py" in bootstrap
    assert "Administrator" not in bootstrap
    assert "data['project']['dependencies']" in bootstrap
    assert "$installArguments += $pluginRoot" not in bootstrap


def test_preflight_flattens_nested_startup_errors_without_a_traceback() -> None:
    error = ExceptionGroup(
        "startup",
        (
            RuntimeError("worker pipe closed"),
            ExceptionGroup("nested", (ValueError("protocol mismatch"),)),
        ),
    )

    summary = _exception_summary(error)

    assert summary == (
        "RuntimeError: worker pipe closed; ValueError: protocol mismatch"
    )
    assert "Traceback" not in summary
