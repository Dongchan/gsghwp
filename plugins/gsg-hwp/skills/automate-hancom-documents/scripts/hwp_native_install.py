from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, cast
from winreg import (
    CreateKeyEx,
    HKEY_CURRENT_USER,
    HKEYType,
    KEY_QUERY_VALUE,
    KEY_SET_VALUE,
    QueryValueEx,
    REG_DWORD,
    REG_SZ,
    SetValueEx,
)

from hwp_errors import HwpLiveError


NATIVE_BRIDGE_VERSION = "0.5.55"
MODULE_NAME = "한컴브릿지"
MODULES_KEY = r"Software\HNC\HwpUserAction\Modules"
USES_KEY = rf"{MODULES_KEY}\Uses"


@dataclass(frozen=True, slots=True)
class NativeBridgeRegistration:
    path: Path
    copied: bool


def _same_file_content(left: Path, right: Path) -> bool:
    if not right.is_file():
        return False
    left_stat = left.stat()
    right_stat = right.stat()
    if (
        left_stat.st_size == right_stat.st_size
        and left_stat.st_mtime_ns == right_stat.st_mtime_ns
    ):
        return True
    if left_stat.st_size != right_stat.st_size:
        return False

    def digest(path: Path) -> bytes:
        checksum = sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                checksum.update(chunk)
        return checksum.digest()

    return digest(left) == digest(right)


def _set_registry_value_if_changed(
    key: int | HKEYType,
    name: str,
    value: str | int,
    value_type: int,
) -> None:
    try:
        current_value, current_type = QueryValueEx(key, name)
    except FileNotFoundError:
        current_value, current_type = None, None
    if current_value != value or current_type != value_type:
        SetValueEx(key, name, 0, value_type, cast(Any, value))


def _register_native_bridge(path: Path) -> None:
    access = KEY_QUERY_VALUE | KEY_SET_VALUE
    with CreateKeyEx(HKEY_CURRENT_USER, MODULES_KEY, 0, access) as modules:
        _set_registry_value_if_changed(modules, MODULE_NAME, str(path), REG_SZ)
    with CreateKeyEx(HKEY_CURRENT_USER, USES_KEY, 0, access) as uses:
        _set_registry_value_if_changed(uses, MODULE_NAME, 1, REG_DWORD)


def ensure_native_bridge_registered(
    *,
    package_root: Path | None = None,
    local_app_data: Path | None = None,
) -> NativeBridgeRegistration:
    root = Path(__file__).parents[3] if package_root is None else package_root
    source = (
        root
        / "addon"
        / "HancomLiveBridgeNative"
        / "bin"
        / NATIVE_BRIDGE_VERSION
        / "HancomLiveBridge.dll"
    ).resolve()
    if not source.is_file():
        raise HwpLiveError(f"패키지 네이티브 한컴 브리지 DLL이 없습니다: {source}")
    if local_app_data is None:
        configured = os.environ.get("LOCALAPPDATA")
        if not configured:
            raise HwpLiveError("Windows LOCALAPPDATA 경로를 확인할 수 없습니다")
        local_app_data = Path(configured)
    destination = (
        local_app_data
        / "HancomDocumentAutomation"
        / "native"
        / NATIVE_BRIDGE_VERSION
        / "HancomLiveBridge.dll"
    ).resolve()
    copied = not _same_file_content(source, destination)
    if copied:
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.copy2(source, destination)
    _register_native_bridge(destination)
    return NativeBridgeRegistration(path=destination, copied=copied)
