from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import hwp_native_install  # noqa: E402


def test_same_file_content_uses_metadata_fast_path(tmp_path: Path) -> None:
    source = tmp_path / "source.dll"
    destination = tmp_path / "destination.dll"
    payload = b"native-bridge"
    source.write_bytes(payload)
    destination.write_bytes(payload)
    source_stat = source.stat()
    os.utime(
        destination,
        ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
    )

    with patch.object(Path, "open", side_effect=AssertionError("must not hash")):
        assert hwp_native_install._same_file_content(source, destination) is True


def test_same_file_content_hashes_when_metadata_differs(tmp_path: Path) -> None:
    source = tmp_path / "source.dll"
    destination = tmp_path / "destination.dll"
    source.write_bytes(b"same-content")
    destination.write_bytes(b"same-content")
    os.utime(destination, (destination.stat().st_atime, source.stat().st_mtime - 10))

    assert hwp_native_install._same_file_content(source, destination) is True


def test_server_startup_requires_safe_installer_instead_of_copying(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "package"
    source = (
        package_root
        / "addon"
        / "HancomLiveBridgeNative"
        / "bin"
        / hwp_native_install.NATIVE_BRIDGE_VERSION
        / "HancomLiveBridge.dll"
    )
    source.parent.mkdir(parents=True)
    source.write_bytes(b"candidate")
    local_app_data = tmp_path / "local"
    destination = (
        local_app_data
        / "HancomDocumentAutomation"
        / "native"
        / hwp_native_install.NATIVE_BRIDGE_VERSION
        / "HancomLiveBridge.dll"
    )

    with (
        patch.object(hwp_native_install, "_register_native_bridge", create=True) as register,
        pytest.raises(hwp_native_install.HwpLiveError, match="install.ps1 -AcceptChanges"),
    ):
        hwp_native_install.require_native_bridge_registered(
            package_root=package_root,
            local_app_data=local_app_data,
        )

    assert not destination.exists()
    register.assert_not_called()


def test_registration_validation_accepts_matching_safe_install(tmp_path: Path) -> None:
    package_root = tmp_path / "package"
    source = (
        package_root
        / "addon"
        / "HancomLiveBridgeNative"
        / "bin"
        / hwp_native_install.NATIVE_BRIDGE_VERSION
        / "HancomLiveBridge.dll"
    )
    source.parent.mkdir(parents=True)
    source.write_bytes(b"candidate")
    local_app_data = tmp_path / "local"
    destination = (
        local_app_data
        / "HancomDocumentAutomation"
        / "native"
        / hwp_native_install.NATIVE_BRIDGE_VERSION
        / "HancomLiveBridge.dll"
    )
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"candidate")
    module_key = MagicMock()
    uses_key = MagicMock()
    module_key.__enter__.return_value = module_key
    uses_key.__enter__.return_value = uses_key

    with (
        patch.object(
            hwp_native_install,
            "OpenKey",
            side_effect=(module_key, uses_key),
        ),
        patch.object(
            hwp_native_install,
            "QueryValueEx",
            side_effect=(
                (str(destination.resolve()), hwp_native_install.REG_SZ),
                (1, hwp_native_install.REG_DWORD),
            ),
        ),
    ):
        registration = hwp_native_install.require_native_bridge_registered(
            package_root=package_root,
            local_app_data=local_app_data,
        )

    assert registration.path == destination.resolve()
    assert registration.copied is False
