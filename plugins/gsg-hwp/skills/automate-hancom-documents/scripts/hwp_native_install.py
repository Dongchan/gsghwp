from __future__ import annotations

import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from winreg import (
    HKEY_CURRENT_USER,
    HKEYType,
    KEY_QUERY_VALUE,
    OpenKey,
    QueryValueEx,
    REG_DWORD,
    REG_SZ,
)
from typing import Protocol, cast

from hwp_errors import HwpLiveError


NATIVE_BRIDGE_VERSION = "0.5.51"
MODULE_NAME = "한컴브릿지"
MODULES_KEY = r"Software\HNC\HwpUserAction\Modules"
USES_KEY = rf"{MODULES_KEY}\Uses"


class _RegistryValueQuery(Protocol):
    def __call__(self, key: HKEYType, name: str, /) -> tuple[str | int, int]: ...


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


def _read_registry_value(key_path: str) -> tuple[str | int, int] | None:
    try:
        with OpenKey(HKEY_CURRENT_USER, key_path, 0, KEY_QUERY_VALUE) as key:
            return cast(_RegistryValueQuery, QueryValueEx)(key, MODULE_NAME)
    except FileNotFoundError:
        return None


def _registration_matches(path: Path) -> bool:
    return _read_registry_value(MODULES_KEY) == (str(path), REG_SZ) and (
        _read_registry_value(USES_KEY) == (1, REG_DWORD)
    )


def require_native_bridge_registered(
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
    if not _same_file_content(source, destination) or not _registration_matches(destination):
        raise HwpLiveError(
            "네이티브 한컴 브리지의 안전 설치가 완료되지 않았습니다. 저장소 루트에서 "
            + "install.ps1 -AcceptChanges를 실행하세요."
        )
    return NativeBridgeRegistration(path=destination, copied=False)
