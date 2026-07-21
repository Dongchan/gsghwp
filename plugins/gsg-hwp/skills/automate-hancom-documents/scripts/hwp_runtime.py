from __future__ import annotations

import csv
import subprocess
import sys
from dataclasses import dataclass
from importlib import import_module
from io import StringIO
from types import TracebackType
from typing import Final, Literal, Protocol, final, runtime_checkable

from hwp_errors import (
    DocumentAutomationError as DocumentAutomationError,
    HwpRuntimeSafetyError as HwpRuntimeSafetyError,
    ProcessScanError as ProcessScanError,
)
from hwp_trusted_paths import (
    input_document as input_document,
    output_document as output_document,
)


@dataclass(frozen=True, slots=True)
class WindowsProcess:
    name: str
    pid: int


class ProcessListReader(Protocol):
    def __call__(self) -> str: ...


class ProcessScanner(Protocol):
    def __call__(self) -> tuple[WindowsProcess, ...]: ...


class PictureControl(Protocol):
    pass


class FilePathCheckerRegistrar(Protocol):
    def RegisterModule(self, *, ModuleType: str, ModuleData: str) -> bool: ...


class HwpApplication(Protocol):
    @property
    def hwp(self) -> FilePathCheckerRegistrar: ...

    @property
    def PageCount(self) -> int: ...

    def open(self, filename: str, format: str = "", arg: str = "") -> bool: ...

    def save_as(
        self,
        path: str,
        format: str = "HWP",
        arg: str = "",
        split_page: bool = False,
    ) -> bool: ...

    def quit(self, save: bool = False) -> None: ...

    def field_exist(self, field: str) -> bool: ...

    def move_to_field(
        self,
        field: str,
        idx: int = 0,
        text: bool = True,
        start: bool = True,
        select: bool = False,
    ) -> bool: ...

    def put_field_text(
        self, field: str = "", text: str = "", idx: int | None = None
    ) -> None: ...

    def goto_page(self, page_index: int | str = 1) -> tuple[int, int]: ...

    def CopyPage(self) -> bool: ...

    def PastePage(self) -> bool: ...

    def RecalcPageCount(self) -> None: ...

    def is_cell(self) -> bool: ...

    def TableCellBlock(self) -> bool: ...

    def insert_background_picture(
        self,
        path: str,
        border_type: Literal["SelectedCell", "SelectedCellDelete"] = "SelectedCell",
        embedded: bool = True,
        filloption: int = 5,
        effect: int = 0,
        watermark: bool = False,
        brightness: int = 0,
        contrast: int = 0,
    ) -> bool: ...

    def Cancel(self) -> bool: ...

    def insert_picture(
        self,
        path: str,
        treat_as_char: bool = True,
        embedded: bool = True,
        sizeoption: int = 0,
        reverse: bool = False,
        watermark: bool = False,
        effect: int = 0,
        width: int = 0,
        height: int = 0,
    ) -> PictureControl | None: ...

    def BreakPage(self) -> bool: ...

    def ParagraphShapeAlignLeft(self) -> bool: ...

    def insert_text(self, text: str) -> bool: ...


class HwpFactory(Protocol):
    def __call__(
        self,
        *,
        new: bool,
        visible: bool,
        register_module: bool,
    ) -> HwpApplication: ...


@runtime_checkable
class _PyhwpxModule(Protocol):
    @property
    def Hwp(self) -> HwpFactory: ...


@runtime_checkable
class _PyWinTypesModule(Protocol):
    @property
    def com_error(self) -> type[Exception]: ...


def _load_com_error_type() -> type[Exception]:
    module = import_module("pywintypes")
    if not isinstance(module, _PyWinTypesModule):
        raise DocumentAutomationError("pywintypes.com_error를 찾을 수 없습니다")
    return module.com_error


_HWP_PROCESS_NAMES: Final = frozenset({"hwp.exe", "hwpapi.exe"})
_COM_ERROR_TYPE: Final = _load_com_error_type()
_CLEANUP_NOTE: Final = "HWP 세션 정리 중 COM 오류가 발생했습니다"


def _read_windows_process_list() -> str:
    completed = subprocess.run(
        ["tasklist.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        check=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return completed.stdout


def scan_hwp_processes(
    *, read_process_list: ProcessListReader = _read_windows_process_list
) -> tuple[WindowsProcess, ...]:
    try:
        raw = read_process_list()
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        raise ProcessScanError(f"Windows 프로세스 목록 조회 실패: {error}") from error
    if not raw.strip():
        raise ProcessScanError("Windows 프로세스 목록이 비어 있습니다")

    processes: list[WindowsProcess] = []
    try:
        for row in csv.reader(StringIO(raw)):
            if not row:
                continue
            if len(row) < 2:
                raise ProcessScanError("Windows 프로세스 목록 형식을 확인할 수 없습니다")
            name = row[0].strip()
            if name.casefold() not in _HWP_PROCESS_NAMES:
                continue
            pid = int(row[1].replace(",", "").strip())
            processes.append(WindowsProcess(name=name, pid=pid))
    except (csv.Error, ValueError) as error:
        raise ProcessScanError(f"Windows 프로세스 목록 해석 실패: {error}") from error
    return tuple(processes)


def guard_hwp_runtime(
    *, process_scanner: ProcessScanner = scan_hwp_processes
) -> None:
    try:
        processes = process_scanner()
    except (ProcessScanError, OSError, subprocess.SubprocessError, UnicodeError) as error:
        raise HwpRuntimeSafetyError(
            f"HWP 실행 상태를 확인할 수 없어 새 세션을 만들지 않습니다: {error}"
        ) from error

    active = tuple(
        process
        for process in processes
        if process.name.casefold() in _HWP_PROCESS_NAMES
    )
    if active:
        identities = ", ".join(
            f"{process.name} PID {process.pid}" for process in active
        )
        raise HwpRuntimeSafetyError(
            f"실행 중인 한컴 프로세스가 있어 새 세션을 만들지 않습니다: {identities}"
        )


def _create_hwp(
    *, new: bool, visible: bool, register_module: bool
) -> HwpApplication:
    module = import_module("pyhwpx")
    if not isinstance(module, _PyhwpxModule):
        raise DocumentAutomationError("pyhwpx.Hwp 생성기를 찾을 수 없습니다")

    return module.Hwp(new=new, visible=visible, register_module=register_module)


def _register_file_path_checker(hwp: HwpApplication) -> None:
    try:
        registered = hwp.hwp.RegisterModule(
            ModuleType="FilePathCheckDLL",
            ModuleData="FilePathCheckerModule",
        )
    except _COM_ERROR_TYPE as error:
        raise HwpRuntimeSafetyError(
            "파일 경로 보안 모듈을 등록할 수 없어 문서를 열지 않습니다"
        ) from error
    if not registered:
        raise HwpRuntimeSafetyError(
            "파일 경로 보안 모듈 등록이 거부되어 문서를 열지 않습니다"
        )


@final
class HwpSession:
    __slots__ = ("_hwp", "_hwp_factory", "_process_scanner", "_visible")

    def __init__(
        self,
        *,
        visible: bool,
        process_scanner: ProcessScanner = scan_hwp_processes,
        hwp_factory: HwpFactory = _create_hwp,
    ) -> None:
        self._visible = visible
        self._process_scanner = process_scanner
        self._hwp_factory = hwp_factory
        self._hwp: HwpApplication | None = None

    def __enter__(self) -> HwpApplication:
        guard_hwp_runtime(process_scanner=self._process_scanner)
        hwp = self._hwp_factory(
            new=True,
            visible=self._visible,
            register_module=False,
        )
        self._hwp = hwp
        registered = False
        try:
            _register_file_path_checker(hwp)
            registered = True
        finally:
            if not registered:
                primary = sys.exception()
                self._hwp = None
                try:
                    hwp.quit(save=False)
                except _COM_ERROR_TYPE:
                    if primary is None:
                        raise
                    primary.add_note(_CLEANUP_NOTE)
        return hwp

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        owned = self._hwp
        self._hwp = None
        if owned is None:
            return
        try:
            owned.quit(save=False)
        except _COM_ERROR_TYPE:
            if exc_value is None:
                raise
            exc_value.add_note(_CLEANUP_NOTE)
