from __future__ import annotations

import hashlib
import ntpath
import os
from pathlib import Path
from typing import Literal

from pydantic import Field

from hwp_live_values import ContractModel
from hwp_operation_contract import OperationResult
from hwp_operation_registry import operation_registry


type SaveFingerprintDisposition = Literal[
    "confirmed",
    "likely",
    "unchanged",
    "unavailable",
]

type SaveFingerprintCaptureStatus = Literal[
    "complete",
    "file_missing",
    "access_failed",
    "unstable",
]

type SaveBaselineDiagnosticReason = Literal[
    "file_missing",
    "access_failed",
    "unstable",
    "incomplete",
    "not_attached",
]


class SaveFileFingerprint(ContractModel):
    path: str = Field(max_length=32_767)
    exists: bool | None = None
    size: int | None = Field(default=None, ge=0)
    mtime_ns: int | None = Field(default=None, ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    stable: bool = False
    capture_status: SaveFingerprintCaptureStatus | None = None


def normalized_save_path(value: str) -> str:
    return ntpath.normcase(ntpath.normpath(value))


def capture_save_file_fingerprint(value: str) -> SaveFileFingerprint:
    path = Path(value)
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            digest = hashlib.sha256()
            bytes_read = 0
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                bytes_read += len(chunk)
            after = os.fstat(stream.fileno())
        current = path.stat()
    except FileNotFoundError:
        return SaveFileFingerprint(
            path=str(path),
            exists=False,
            stable=True,
            capture_status="file_missing",
        )
    except OSError:
        return SaveFileFingerprint(
            path=str(path),
            exists=None,
            stable=False,
            capture_status="access_failed",
        )
    stable = (
        before.st_size == after.st_size == current.st_size == bytes_read
        and before.st_mtime_ns == after.st_mtime_ns == current.st_mtime_ns
    )
    return SaveFileFingerprint(
        path=str(path),
        exists=True,
        size=current.st_size,
        mtime_ns=current.st_mtime_ns,
        sha256=digest.hexdigest(),
        stable=stable,
        capture_status="complete" if stable else "unstable",
    )


def save_baseline_diagnostic_reason(
    before: SaveFileFingerprint | None,
    *,
    attached: bool,
) -> SaveBaselineDiagnosticReason | None:
    if before is None:
        return "not_attached"
    if before.capture_status == "file_missing":
        return "file_missing"
    if before.capture_status == "access_failed":
        return "access_failed"
    if before.capture_status == "unstable":
        return "unstable"
    # Derive the same reasons for fingerprints read from journals written
    # before capture_status was added.
    if before.exists is False:
        return "file_missing"
    if before.exists is None:
        return "access_failed"
    if not before.stable:
        return "unstable"
    if before.size is None or before.mtime_ns is None or before.sha256 is None:
        return "incomplete"
    return None if attached else "not_attached"


def _fingerprint_changed(
    before: SaveFileFingerprint | None,
    after: SaveFileFingerprint,
) -> bool | None:
    if (
        before is None
        or not before.stable
        or not after.stable
        or before.exists is None
        or after.exists is None
    ):
        return None
    if before.exists != after.exists:
        return True
    if before.exists is False:
        return False
    if before.sha256 is None or after.sha256 is None:
        return None
    return before.sha256 != after.sha256


def _native_filetime_100ns(mtime_ns: int) -> int:
    return mtime_ns // 100 + 116_444_736_000_000_000


def _native_save_completion_matches(
    result: OperationResult | None,
    before: SaveFileFingerprint | None,
    after: SaveFileFingerprint,
    changed: bool | None,
) -> bool:
    if (
        result is None
        or before is None
        or result.save_hresult is None
        or result.save_hresult < 0
        or result.post_save_modified is not False
        or before.exists is not True
        or not before.stable
        or before.size is None
        or before.mtime_ns is None
        or before.sha256 is None
        or after.exists is not True
        or not after.stable
        or after.size is None
        or after.mtime_ns is None
        or after.sha256 is None
        or result.saved_file_size is None
        or result.saved_file_write_time_100ns is None
    ):
        return False
    completed = result.save_return == 1 or (
        result.before_modified is False and result.save_return == 0
    )
    if not completed:
        return False
    if normalized_save_path(before.path) != normalized_save_path(after.path):
        return False
    saved_path = result.saved_path or result.reopened_path
    if saved_path is None or normalized_save_path(saved_path) != normalized_save_path(
        after.path
    ):
        return False
    if result.saved_file_size != after.size:
        return False
    if result.saved_file_write_time_100ns != _native_filetime_100ns(after.mtime_ns):
        return False
    return result.before_modified is not True or changed is True


def _fingerprint_message(
    before: SaveFileFingerprint | None,
    after: SaveFileFingerprint,
) -> str:
    baseline_reason = save_baseline_diagnostic_reason(before, attached=True)
    diagnostic = (
        "" if baseline_reason is None else f" [save_baseline_reason={baseline_reason}]"
    )
    return (
        "저장 전 파일 지문("
        f"size={None if before is None else before.size}, "
        f"mtime_ns={None if before is None else before.mtime_ns}, "
        f"sha256={None if before is None else before.sha256})과 "
        "현재 안정 파일 지문("
        f"size={after.size}, mtime_ns={after.mtime_ns}, sha256={after.sha256})"
        f"{diagnostic}"
    )


def reconcile_save_fingerprint(
    result: OperationResult | None,
    *,
    before: SaveFileFingerprint | None,
    after: SaveFileFingerprint,
) -> tuple[OperationResult, SaveFingerprintDisposition]:
    changed = _fingerprint_changed(before, after)
    stable = (
        before is not None
        and before.stable
        and after.stable
        and after.exists is True
        and after.sha256 is not None
    )
    base = (
        OperationResult(
            status="operation_failed",
            query="operation status",
            registry_entries=operation_registry().count,
            lookup_microseconds=0,
            message="저장 작업의 네이티브 결과를 받지 못했습니다",
            verified=False,
            retry_safe=False,
            reconcile_required=True,
        )
        if result is None
        else result
    )
    evidence = {
        "saved_path": base.saved_path or after.path,
        "save_baseline_file_size": None if before is None else before.size,
        "save_baseline_file_mtime_ns": None if before is None else before.mtime_ns,
        "save_baseline_sha256": None if before is None else before.sha256,
        "saved_file_size": after.size,
        "saved_file_mtime_ns": after.mtime_ns,
        "saved_file_sha256": after.sha256,
        "save_fingerprint_stable": stable,
        "save_fingerprint_changed": changed,
    }
    baseline_reason = save_baseline_diagnostic_reason(before, attached=True)
    evidence["save_baseline_diagnostic_reason"] = baseline_reason
    if baseline_reason is not None and base.failure_stage is None:
        evidence["failure_stage"] = f"save_baseline_{baseline_reason}"
    if _native_save_completion_matches(base, before, after, changed):
        return (
            base.model_copy(
                update={
                    **evidence,
                    "status": "executed",
                    "changed": changed is True,
                    "query": "operation status",
                    "message": (
                        "COM 재연결 없이 네이티브 Save 완료값·대상 경로·파일 "
                        "크기·mtime과 현재 SHA-256 지문을 교차 확인해 저장 완료를 "
                        f"확정했습니다. {_fingerprint_message(before, after)}"
                    ),
                    "verified": True,
                    "modified": False,
                    "partial_change": False,
                    "partial_mutation": False,
                    "retry_safe": True,
                    "reconcile_required": False,
                    "save_fingerprint_verified": True,
                    "disk_persistence_verified": True,
                }
            ),
            "confirmed",
        )
    if stable and changed is True:
        return (
            base.model_copy(
                update={
                    **evidence,
                    "status": "partial_change",
                    "changed": True,
                    "query": "operation status",
                    "message": (
                        "저장된 것으로 보이나 미확정입니다. "
                        f"{_fingerprint_message(before, after)}이 서로 다르고 현재 "
                        "파일은 해시 계산 중 안정적이었습니다. 그러나 네이티브 Save "
                        "완료값이 없어 부분 저장이나 같은 시각의 다른 파일 변경을 "
                        "배제할 수 없습니다"
                    ),
                    "verified": False,
                    "partial_change": True,
                    "partial_mutation": True,
                    "retry_safe": False,
                    "reconcile_required": True,
                    "save_fingerprint_verified": False,
                    "disk_persistence_verified": False,
                }
            ),
            "likely",
        )
    disposition: SaveFingerprintDisposition = (
        "unchanged" if changed is False else "unavailable"
    )
    return (
        base.model_copy(
            update={
                **evidence,
                "query": "operation status",
                "message": (
                    f"{base.message}. {_fingerprint_message(before, after)}을 "
                    + (
                        "비교했지만 내용 변경이 없어 저장 완료로 판정하지 않았습니다"
                        if disposition == "unchanged"
                        else "안정적으로 비교할 수 없어 저장 완료로 판정하지 않았습니다"
                    )
                ),
                "verified": False,
                "retry_safe": False,
                "reconcile_required": True,
                "save_fingerprint_verified": False,
                "disk_persistence_verified": False,
            }
        ),
        disposition,
    )
