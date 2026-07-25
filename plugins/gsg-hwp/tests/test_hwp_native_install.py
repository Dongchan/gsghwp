from __future__ import annotations

from hashlib import sha256
import json
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import hwp_native_install  # noqa: E402
from hwp_runtime_identity import runtime_source_hash  # noqa: E402


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


def test_registration_skips_unchanged_registry_values(tmp_path: Path) -> None:
    path = tmp_path / "HancomLiveBridge.dll"
    module_key = MagicMock()
    uses_key = MagicMock()
    module_key.__enter__.return_value = module_key
    uses_key.__enter__.return_value = uses_key

    with (
        patch.object(
            hwp_native_install,
            "CreateKeyEx",
            side_effect=(module_key, uses_key),
        ),
        patch.object(
            hwp_native_install,
            "QueryValueEx",
            side_effect=((str(path), hwp_native_install.REG_SZ), (1, hwp_native_install.REG_DWORD)),
        ),
        patch.object(hwp_native_install, "SetValueEx") as set_value,
    ):
        hwp_native_install._register_native_bridge(path)

    set_value.assert_not_called()


def test_registration_uses_content_addressed_dll_when_destination_is_locked(
    tmp_path: Path,
) -> None:
    root = tmp_path / "package"
    source = (
        root
        / "addon"
        / "HancomLiveBridgeNative"
        / "bin"
        / hwp_native_install.NATIVE_BRIDGE_VERSION
        / "HancomLiveBridge.dll"
    )
    source.parent.mkdir(parents=True)
    payload = b"new-native-bridge"
    source.write_bytes(payload)
    local_app_data = tmp_path / "local"
    base_destination = (
        local_app_data
        / "HancomDocumentAutomation"
        / "native"
        / hwp_native_install.NATIVE_BRIDGE_VERSION
        / "HancomLiveBridge.dll"
    ).resolve()
    real_copy2 = shutil.copy2

    def copy_with_locked_base(source_path: Path, destination_path: Path) -> Path:
        if Path(destination_path) == base_destination:
            error = PermissionError(32, "destination is in use", str(destination_path))
            error.winerror = 32
            raise error
        return Path(real_copy2(source_path, destination_path))

    with (
        patch.object(
            hwp_native_install.shutil,
            "copy2",
            side_effect=copy_with_locked_base,
        ),
        patch.object(hwp_native_install, "_register_native_bridge") as register,
    ):
        registration = hwp_native_install.ensure_native_bridge_registered(
            package_root=root,
            local_app_data=local_app_data,
        )

    expected = base_destination.with_name(
        f"HancomLiveBridge-{sha256(payload).hexdigest()[:16]}.dll"
    )
    assert registration.path == expected
    assert registration.copied is True
    assert expected.read_bytes() == payload
    register.assert_called_once_with(expected)


def test_release_manifest_matches_packaged_native_and_recipe_bundle() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "compatibility-manifest.json").read_text(encoding="utf-8")
    )
    native = (
        root
        / "addon"
        / "HancomLiveBridgeNative"
        / "bin"
        / hwp_native_install.NATIVE_BRIDGE_VERSION
        / "HancomLiveBridge.dll"
    )

    assert manifest["native_bridge"] == hwp_native_install.NATIVE_BRIDGE_VERSION
    assert manifest["native_sha256"] == sha256(native.read_bytes()).hexdigest()
    assert manifest["recipe_bundle_hash"] == runtime_source_hash((SCRIPTS,))
