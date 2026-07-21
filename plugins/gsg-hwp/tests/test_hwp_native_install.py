from __future__ import annotations

import os
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
