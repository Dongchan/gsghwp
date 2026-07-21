from __future__ import annotations

import hashlib
from ctypes import POINTER, WinDLL, byref, get_last_error
from ctypes import wintypes
from collections.abc import Callable, Generator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from importlib import import_module
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import ClassVar, Final, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from hwp_errors import DocumentAutomationError
from hwp_hwpx_signature import structural_signature as _structural_signature
from hwp_trusted_paths import input_document, output_document


class CloseableApplication(Protocol):
    def SetMessageBoxMode(self, mode: int) -> int: ...

    def Quit(self) -> None: ...

    @property
    def XHwpWindows(self) -> RebuildWindows: ...


class RebuildApplication(CloseableApplication, Protocol):
    @property
    def PageCount(self) -> int: ...

    def RegisterModule(self, module_type: str, module_data: str) -> bool: ...

    def Open(self, path: str, document_format: str, arguments: str) -> bool: ...

    def SaveAs(self, path: str, document_format: str, arguments: str) -> bool: ...

    def GetTextFile(self, document_format: str, options: str) -> str | None: ...

class RebuildWindow(Protocol):
    @property
    def WindowHandle(self) -> int: ...


class RebuildWindows(Protocol):
    @property
    def Active_XHwpWindow(self) -> RebuildWindow: ...


class ApplicationFactory(Protocol):
    def __call__(self) -> RebuildApplication: ...


@runtime_checkable
class _Win32ClientModule(Protocol):
    def DispatchEx(self, program_id: str) -> RebuildApplication: ...


@runtime_checkable
class _PythonComModule(Protocol):
    def CoInitialize(self) -> None: ...

    def CoUninitialize(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RebuildRuntime:
    create_application: ApplicationFactory
    initialize_com: Callable[[], None]
    uninitialize_com: Callable[[], None]
    close_application: Callable[[RebuildApplication], None]


@dataclass(frozen=True, slots=True)
class _FileSignature:
    size: int
    modified_nanoseconds: int
    sha256: str


class DocumentRebuildResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    source: str
    output: str
    source_page_count: int
    output_page_count: int
    source_sha256: str
    output_sha256: str
    structural_sha256: str
    structural_member_count: int
    structure_exact: bool
    elapsed_seconds: float


_AUTO_YES: Final = 0x00011010
_PROCESS_TERMINATE: Final = 0x0001
_SYNCHRONIZE: Final = 0x00100000
_WAIT_OBJECT_0: Final = 0
_WAIT_TIMEOUT: Final = 258
_QUIT_WAIT_MILLISECONDS: Final = 3_000
_TERMINATE_WAIT_MILLISECONDS: Final = 5_000


@dataclass(frozen=True, slots=True)
class _OwnedProcess:
    process_id: int
    handle: int


def _create_application() -> RebuildApplication:
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        module = import_module("win32com.client")
        if not isinstance(module, _Win32ClientModule):
            raise DocumentAutomationError("win32com.client.DispatchEx를 찾을 수 없습니다")
        return module.DispatchEx("HWPFrame.HwpObject")


def _initialize_com() -> None:
    module = import_module("pythoncom")
    if not isinstance(module, _PythonComModule):
        raise DocumentAutomationError("pythoncom.CoInitialize를 찾을 수 없습니다")
    module.CoInitialize()


def _uninitialize_com() -> None:
    module = import_module("pythoncom")
    if not isinstance(module, _PythonComModule):
        raise DocumentAutomationError("pythoncom.CoUninitialize를 찾을 수 없습니다")
    module.CoUninitialize()


def _capture_owned_process(application: CloseableApplication) -> _OwnedProcess:
    window_handle = int(application.XHwpWindows.Active_XHwpWindow.WindowHandle)
    process_id = wintypes.DWORD()
    user32 = WinDLL("user32", use_last_error=True)
    get_window_process = user32.GetWindowThreadProcessId
    get_window_process.argtypes = (wintypes.HWND, POINTER(wintypes.DWORD))
    get_window_process.restype = wintypes.DWORD
    if not get_window_process(window_handle, byref(process_id)) or process_id.value <= 0:
        raise DocumentAutomationError("재구축 HWP 창의 프로세스 ID를 확인하지 못했습니다")

    kernel32 = WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    handle = open_process(
        _SYNCHRONIZE | _PROCESS_TERMINATE,
        False,
        process_id.value,
    )
    if not handle:
        raise DocumentAutomationError(
            f"재구축 HWP 프로세스 종료 핸들을 열지 못했습니다: {get_last_error()}"
        )
    return _OwnedProcess(process_id.value, int(handle))


def _ensure_owned_process_exited(process: _OwnedProcess) -> None:
    kernel32 = WinDLL("kernel32", use_last_error=True)
    wait_for_process = kernel32.WaitForSingleObject
    wait_for_process.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    wait_for_process.restype = wintypes.DWORD
    terminate_process = kernel32.TerminateProcess
    terminate_process.argtypes = (wintypes.HANDLE, wintypes.UINT)
    terminate_process.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    try:
        wait_result = wait_for_process(process.handle, _QUIT_WAIT_MILLISECONDS)
        if wait_result == _WAIT_TIMEOUT:
            if not terminate_process(process.handle, 1):
                raise DocumentAutomationError(
                    "재구축 HWP 프로세스 강제 종료가 실패했습니다: "
                    + str(get_last_error())
                )
            wait_result = wait_for_process(process.handle, _TERMINATE_WAIT_MILLISECONDS)
        if wait_result != _WAIT_OBJECT_0:
            raise DocumentAutomationError(
                f"재구축 HWP 프로세스 {process.process_id} 종료를 확인하지 못했습니다"
            )
    finally:
        _ = close_handle(process.handle)


def _close_application(application: CloseableApplication) -> None:
    try:
        process = _capture_owned_process(application)
    except DocumentAutomationError:
        _ = application.SetMessageBoxMode(_AUTO_YES)
        application.Quit()
        raise
    try:
        _ = application.SetMessageBoxMode(_AUTO_YES)
        application.Quit()
    finally:
        _ensure_owned_process_exited(process)


_DEFAULT_RUNTIME: Final = RebuildRuntime(
    _create_application,
    _initialize_com,
    _uninitialize_com,
    _close_application,
)


def _file_signature(path: Path) -> _FileSignature:
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return _FileSignature(stat.st_size, stat.st_mtime_ns, digest.hexdigest())


@contextmanager
def _owned_application(runtime: RebuildRuntime) -> Generator[RebuildApplication]:
    application = runtime.create_application()
    try:
        _ = application.SetMessageBoxMode(_AUTO_YES)
        if not application.RegisterModule(
            "FilePathCheckDLL",
            "FilePathCheckerModule",
        ):
            raise DocumentAutomationError("한컴 파일 경로 보안 모듈 등록 실패")
        yield application
    finally:
        runtime.close_application(application)


def _document_text(application: RebuildApplication) -> str:
    value = application.GetTextFile("UNICODE", "")
    if not isinstance(value, str):
        raise DocumentAutomationError("한컴 문서 전체 텍스트를 읽을 수 없습니다")
    return value


def _export_source(
    source: Path,
    intermediate: Path,
    runtime: RebuildRuntime,
) -> tuple[int, str]:
    with _owned_application(runtime) as application:
        document_format = source.suffix[1:].upper()
        if not application.Open(
            str(source),
            document_format,
            "forceopen:true;lock:true",
        ):
            raise DocumentAutomationError(f"원본 한컴 문서 열기 실패: {source}")
        page_count = int(application.PageCount)
        text = _document_text(application)
        if not application.SaveAs(
            str(intermediate),
            "HWPX",
            "lock:false;fullsave:true",
        ):
            raise DocumentAutomationError(f"HWPX 구조 내보내기 실패: {intermediate}")
    return page_count, text


def _build_output(
    intermediate: Path,
    destination: Path,
    runtime: RebuildRuntime,
) -> None:
    with _owned_application(runtime) as application:
        if not application.Open(
            str(intermediate),
            "HWPX",
            "forceopen:true;lock:true",
        ):
            raise DocumentAutomationError(f"HWPX 구조 열기 실패: {intermediate}")
        if not application.SaveAs(
            str(destination),
            "HWP",
            "lock:false;fullsave:true",
        ):
            raise DocumentAutomationError(f"재생성 HWP 저장 실패: {destination}")


def _verify_output(
    destination: Path,
    verification: Path,
    runtime: RebuildRuntime,
) -> tuple[int, str]:
    with _owned_application(runtime) as application:
        if not application.Open(
            str(destination),
            "HWP",
            "forceopen:true;lock:true",
        ):
            raise DocumentAutomationError(f"재생성 HWP 재개방 실패: {destination}")
        page_count = int(application.PageCount)
        text = _document_text(application)
        if not application.SaveAs(
            str(verification),
            "HWPX",
            "lock:false;fullsave:true",
        ):
            raise DocumentAutomationError(f"재생성 HWPX 검증 출력 실패: {verification}")
    return page_count, text


def rebuild_document(
    source: Path,
    output: Path,
    *,
    runtime: RebuildRuntime = _DEFAULT_RUNTIME,
) -> DocumentRebuildResult:
    started = perf_counter()
    source_path = input_document(source)
    destination = output_document(output)
    if destination.suffix.casefold() != ".hwp":
        raise DocumentAutomationError("구조 재생성 출력 확장자는 .hwp여야 합니다")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_before = _file_signature(source_path)
    output_created = False
    completed = False
    runtime.initialize_com()
    try:
        with TemporaryDirectory(
            prefix=".hwp-rebuild-",
            dir=destination.parent,
        ) as temporary:
            directory = Path(temporary)
            intermediate = directory / "source.hwpx"
            verification = directory / "rebuilt.hwpx"
            source_pages, source_text = _export_source(source_path, intermediate, runtime)
            source_structure = _structural_signature(intermediate)
            try:
                _build_output(
                    intermediate,
                    destination,
                    runtime,
                )
            finally:
                output_created = destination.is_file()
            output_pages, output_text = _verify_output(
                destination,
                verification,
                runtime,
            )
            output_structure = _structural_signature(verification)
            if source_pages != output_pages:
                raise DocumentAutomationError(
                    f"쪽 수 검증 실패: 원본 {source_pages}, 출력 {output_pages}"
                )
            if source_structure != output_structure:
                raise DocumentAutomationError(
                    "HWPX 내용·개체·그림 구조 검증 결과가 원본과 다릅니다"
                )
            if source_text != output_text:
                raise DocumentAutomationError("문서 전체 텍스트 검증 결과가 원본과 다릅니다")
        source_after = _file_signature(source_path)
        if source_before != source_after:
            raise DocumentAutomationError("구조 재생성 중 원본 파일이 변경되었습니다")
        output_signature = _file_signature(destination)
        result = DocumentRebuildResult(
            source=str(source_path),
            output=str(destination),
            source_page_count=source_pages,
            output_page_count=output_pages,
            source_sha256=source_before.sha256,
            output_sha256=output_signature.sha256,
            structural_sha256=source_structure.sha256,
            structural_member_count=source_structure.member_count,
            structure_exact=True,
            elapsed_seconds=perf_counter() - started,
        )
        completed = True
        return result
    finally:
        runtime.uninitialize_com()
        if output_created and not completed:
            destination.unlink(missing_ok=True)
