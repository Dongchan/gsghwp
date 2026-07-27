from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import runpy
import subprocess
import sys
from typing import Final, Never, cast


_SUPPORTED_PYTHON: Final = (3, 12)
_PROTOCOL_VERSION: Final = 12
_REQUIRED_MODULES: Final = (
    "mcp",
    "pdfplumber",
    "pydantic",
    "pyhwpx",
    "pythoncom",
    "win32com.client",
    "PIL",
    "cv2",
    "typer",
    "rich",
    "reportlab",
    "pypdf",
)
_RECOVERY: Final = (
    "PowerShell에서 플러그인 루트를 현재 폴더로 연 뒤 "
    r"`powershell -ExecutionPolicy Bypass -File .\runtime\bootstrap_runtime.ps1`"
    "를 실행하세요."
)


@dataclass(frozen=True, slots=True)
class RuntimePreflightReport:
    python_version: str
    protocol: int
    native_sha256: str
    launcher_sha256: str
    required_modules: tuple[str, ...]


class RuntimePreflightFailure(RuntimeError):
    code: str
    message: str
    recovery: str

    def __init__(self, code: str, message: str, recovery: str = _RECOVERY) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.recovery = recovery

    def as_json(self) -> str:
        return json.dumps(
            {
                "event": "hwp_mcp_preflight_failed",
                "code": self.code,
                "message": self.message,
                "recovery": self.recovery,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def _fail(code: str, message: str) -> Never:
    raise RuntimePreflightFailure(code, message)


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest(root: Path) -> dict[str, object]:
    path = root / "compatibility-manifest.json"
    try:
        loaded = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        _fail("MANIFEST_UNREADABLE", f"호환성 manifest를 읽을 수 없습니다: {error}")
    if not isinstance(loaded, dict):
        _fail("MANIFEST_INVALID", "호환성 manifest의 최상위 값이 객체가 아닙니다")
    return cast(dict[str, object], loaded)


def _manifest_text(manifest: dict[str, object], key: str) -> str:
    value = manifest.get(key)
    if not isinstance(value, str) or not value:
        _fail("MANIFEST_INVALID", f"호환성 manifest에 {key} 값이 없습니다")
    return value


def _verify_checksum(
    path: Path,
    expected: str,
    *,
    missing_code: str,
    mismatch_code: str,
    label: str,
) -> str:
    if not path.is_file():
        _fail(missing_code, f"{label} 파일이 없습니다: {path}")
    actual = _file_sha256(path)
    if actual.casefold() != expected.casefold():
        _fail(
            mismatch_code,
            f"{label} SHA-256이 manifest와 다릅니다 "
            + f"(expected={expected}, actual={actual})",
        )
    return actual


def _verify_required_modules_isolated() -> None:
    script = (
        "import importlib,json,sys\n"
        "for name in json.loads(sys.argv[1]):\n"
        " try:\n"
        "  importlib.import_module(name)\n"
        " except BaseException as error:\n"
        "  print(json.dumps({'module':name,'type':type(error).__name__,"
        "'message':str(error)},ensure_ascii=False),file=sys.stderr)\n"
        "  raise SystemExit(1)\n"
    )
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                script,
                json.dumps(_REQUIRED_MODULES),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
    except (OSError, subprocess.SubprocessError) as error:
        _fail(
            "REQUIRED_MODULE_CHECK_FAILED",
            "필수 Python 모듈 격리 검사 실행에 실패했습니다: "
            + f"{type(error).__name__}: {error}",
        )
    if result.returncode == 0:
        return
    details = next(
        (
            line
            for line in reversed(result.stderr.splitlines())
            if line.lstrip().startswith("{")
        ),
        "",
    )
    try:
        failure = cast(object, json.loads(details))
    except json.JSONDecodeError:
        failure = None
    if isinstance(failure, dict):
        failure_object = cast(dict[str, object], failure)
        module_name = str(failure_object.get("module", "unknown"))
        error_type = str(failure_object.get("type", "ImportError"))
        message = str(failure_object.get("message", ""))
        _fail(
            "REQUIRED_MODULE_MISSING",
            f"필수 Python 모듈 {module_name}을 불러오지 못했습니다: "
            + f"{error_type}: {message}",
        )
    _fail(
        "REQUIRED_MODULE_CHECK_FAILED",
        "필수 Python 모듈 격리 검사가 실패했지만 진단을 해석하지 못했습니다",
    )


def verify_runtime(
    plugin_root: Path,
    *,
    version_info: Sequence[int] | None = None,
    module_loader: Callable[[str], object] | None = None,
) -> RuntimePreflightReport:
    root = plugin_root.resolve()
    version = tuple(
        sys.version_info[:3] if version_info is None else version_info[:3]
    )
    if len(version) < 2 or version[:2] != _SUPPORTED_PYTHON:
        observed = ".".join(str(value) for value in version) or "unknown"
        _fail(
            "PYTHON_VERSION_UNSUPPORTED",
            f"Python 3.12.x가 필요하지만 {observed}가 실행되었습니다",
        )
    executable = Path(sys.executable)
    if not executable.is_file():
        _fail(
            "PYTHON_EXECUTABLE_MISSING",
            f"실행 중인 Python 경로가 파일이 아닙니다: {executable}",
        )

    if module_loader is None:
        _verify_required_modules_isolated()
    else:
        for module_name in _REQUIRED_MODULES:
            try:
                _ = module_loader(module_name)
            except Exception as error:
                _fail(
                    "REQUIRED_MODULE_MISSING",
                    f"필수 Python 모듈 {module_name}을 불러오지 못했습니다: "
                    + f"{type(error).__name__}: {error}",
                )

    manifest = _manifest(root)
    protocol = manifest.get("protocol")
    if protocol != _PROTOCOL_VERSION:
        _fail(
            "PROTOCOL_MISMATCH",
            f"protocol {_PROTOCOL_VERSION}가 필요하지만 manifest 값은 "
            + f"{protocol!r}입니다",
        )
    native_version = _manifest_text(manifest, "native_bridge")
    native_expected = _manifest_text(manifest, "native_sha256")
    launcher_expected = _manifest_text(manifest, "launcher_sha256")
    native_path = (
        root
        / "addon"
        / "HancomLiveBridgeNative"
        / "bin"
        / native_version
        / "HancomLiveBridge.dll"
    )
    launcher_path = (
        root
        / "addon"
        / "HancomMcpLauncher"
        / "bin"
        / "Release"
        / "HancomMcpLauncher.exe"
    )
    native_actual = _verify_checksum(
        native_path,
        native_expected,
        missing_code="NATIVE_DLL_MISSING",
        mismatch_code="NATIVE_CHECKSUM_MISMATCH",
        label="native bridge DLL",
    )
    launcher_actual = _verify_checksum(
        launcher_path,
        launcher_expected,
        missing_code="LAUNCHER_MISSING",
        mismatch_code="LAUNCHER_CHECKSUM_MISMATCH",
        label="MCP launcher",
    )
    return RuntimePreflightReport(
        python_version=".".join(str(value) for value in version),
        protocol=_PROTOCOL_VERSION,
        native_sha256=native_actual,
        launcher_sha256=launcher_actual,
        required_modules=_REQUIRED_MODULES,
    )


def _write_failure(failure: RuntimePreflightFailure) -> int:
    _ = sys.stderr.write(f"{failure.as_json()}\n")
    return 78


def _exception_summary(error: BaseException) -> str:
    pending: list[BaseException] = [error]
    leaves: list[str] = []
    while pending and len(leaves) < 8:
        current = pending.pop(0)
        if isinstance(current, BaseExceptionGroup):
            pending[0:0] = current.exceptions
            continue
        message = str(current).strip()
        leaves.append(
            type(current).__name__
            if not message
            else f"{type(current).__name__}: {message}"
        )
    summary = "; ".join(leaves) or type(error).__name__
    return summary[:2_000]


def main(arguments: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if arguments is None else arguments)
    root = Path(__file__).resolve().parents[3]
    try:
        report = verify_runtime(root)
    except RuntimePreflightFailure as failure:
        return _write_failure(failure)
    if argv == ["--check-only"]:
        _ = sys.stdout.write(
            json.dumps(
                {
                    "event": "hwp_mcp_preflight_ok",
                    "python_version": report.python_version,
                    "protocol": report.protocol,
                    "native_sha256": report.native_sha256,
                    "launcher_sha256": report.launcher_sha256,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
        return 0
    target = Path(__file__).with_name("hwp_mcp_hot_reload.py")
    try:
        sys.argv = [str(target), *argv]
        _ = runpy.run_path(str(target), run_name="__main__")
    except Exception as error:
        return _write_failure(
            RuntimePreflightFailure(
                "MCP_START_FAILED",
                f"HWP MCP 시작에 실패했습니다: {_exception_summary(error)}",
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
