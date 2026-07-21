from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

from hwp_errors import DocumentAutomationError


class DriveTypeReader(Protocol):
    def __call__(self, root: str) -> int: ...


@runtime_checkable
class _Win32FileModule(Protocol):
    def GetDriveType(self, root_path_name: str) -> int: ...


_DRIVE_REMOTE: Final = 4
_LOCAL_DRIVE_TYPES: Final = frozenset({2, 3, 5, 6})
_IMAGE_EXTENSIONS: Final = frozenset(
    {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
)


def _read_drive_type(root: str) -> int:
    module = import_module("win32file")
    if not isinstance(module, _Win32FileModule):
        raise DocumentAutomationError(
            "드라이브 종류를 확인할 수 없어 한컴 문서를 열지 않습니다"
        )
    return module.GetDriveType(root)


def _reject_network_namespace(path: Path) -> None:
    normalized = str(path).replace("/", "\\")
    if normalized.startswith("\\\\") or normalized.startswith("\\??\\"):
        raise DocumentAutomationError("네트워크 경로의 한컴 문서는 열지 않습니다")


def _classify_drive(path: Path, reader: DriveTypeReader) -> bool:
    if not path.drive:
        return False
    root = f"{path.drive}\\"
    try:
        drive_type = reader(root)
    except (ImportError, OSError, UnicodeError) as error:
        raise DocumentAutomationError(
            "드라이브 종류를 확인할 수 없어 한컴 문서를 열지 않습니다"
        ) from error
    if drive_type == _DRIVE_REMOTE:
        raise DocumentAutomationError(
            "네트워크 드라이브의 한컴 문서는 열지 않습니다"
        )
    if drive_type in _LOCAL_DRIVE_TYPES:
        return True
    raise DocumentAutomationError(
        "드라이브 종류를 확인할 수 없어 한컴 문서를 열지 않습니다"
    )


def input_document(
    path: Path,
    *,
    drive_type_reader: DriveTypeReader = _read_drive_type,
) -> Path:
    _reject_network_namespace(path)
    expanded = path.expanduser()
    _reject_network_namespace(expanded)
    classified = _classify_drive(expanded, drive_type_reader)
    absolute = expanded.absolute()
    _reject_network_namespace(absolute)
    if not classified:
        classified = _classify_drive(absolute, drive_type_reader)
    if not classified:
        raise DocumentAutomationError(
            "드라이브 종류를 확인할 수 없어 한컴 문서를 열지 않습니다"
        )
    resolved = absolute.resolve()
    _reject_network_namespace(resolved)
    if resolved.suffix.lower() not in {".hwp", ".hwpx"} or not resolved.is_file():
        raise DocumentAutomationError(f"HWP/HWPX 입력 파일이 없습니다: {resolved}")
    zone_path = Path(f"{resolved}:Zone.Identifier")
    try:
        zone = zone_path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return resolved
    except OSError as error:
        raise DocumentAutomationError(
            f"파일 신뢰 정보를 읽을 수 없습니다: {resolved}"
        ) from error
    if "ZoneId=3" in zone or "ZoneId=4" in zone:
        raise DocumentAutomationError("인터넷에서 받은 문서는 차단 해제 후 사용하세요")
    return resolved


def output_document(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.suffix.lower() not in {".hwp", ".hwpx"}:
        raise DocumentAutomationError("출력 확장자는 .hwp 또는 .hwpx여야 합니다")
    if resolved.exists():
        raise DocumentAutomationError(f"출력 파일이 이미 있습니다: {resolved}")
    return resolved


def input_local_image(
    path: Path,
    *,
    drive_type_reader: DriveTypeReader = _read_drive_type,
) -> Path:
    _ = drive_type_reader
    expanded = path.expanduser()
    absolute = expanded if expanded.is_absolute() else expanded.absolute()
    if absolute.suffix.lower() not in _IMAGE_EXTENSIONS:
        raise DocumentAutomationError(f"지원하지 않는 그림 확장자입니다: {absolute}")
    return absolute
