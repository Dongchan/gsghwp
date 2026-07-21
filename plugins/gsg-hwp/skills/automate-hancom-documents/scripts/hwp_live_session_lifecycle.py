from __future__ import annotations

from hwp_live_native_batch_contract import NativeLifecycleResult
from hwp_operation_contract import OperationResult
from hwp_operation_registry import operation_registry


def lifecycle_preflight_result(
    query: str,
    *,
    resolve_only: bool,
    allow_document_change: bool,
) -> OperationResult | None:
    if resolve_only:
        return OperationResult(
            status="resolved",
            query=query,
            registry_entries=operation_registry().count,
            lookup_microseconds=0,
            message="저장·재개방 검증 operation을 확정했습니다",
        )
    if not allow_document_change:
        return OperationResult(
            status="confirmation_required",
            query=query,
            registry_entries=operation_registry().count,
            lookup_microseconds=0,
            message="저장·재개방 검증은 문서 변경을 포함하므로 확인이 필요합니다",
        )
    return None


def lifecycle_result(
    query: str,
    native: NativeLifecycleResult,
) -> OperationResult:
    return OperationResult(
        status="executed" if native.verified else "operation_failed",
        query=query,
        registry_entries=operation_registry().count,
        lookup_microseconds=0,
        message=(
            "프로토콜 9 C++/ATL에서 저장 후 같은 경로를 재개방하고 구조를 검증했습니다"
            if native.verified
            else "프로토콜 9 C++/ATL 저장·재개방 구조 검증이 완료되지 않았습니다"
        ),
        execution_mode="native_in_process",
        native_protocol=9,
        verification="native_save_reopen_result",
        verified=native.verified,
        native_elapsed_microseconds=native.elapsed_microseconds,
        page_count=native.after_page_count,
        modified=native.after_modified,
        reopened_path=native.reopened_path,
        before_page_count=native.before_page_count,
        before_modified=native.before_modified,
        before_control_count=native.before_control_count,
        before_control_hash=native.before_control_hash,
        save_hresult=native.save_hresult,
        save_return=native.save_return,
        post_save_modified=native.post_save_modified,
        clear_hresult=native.clear_hresult,
        clear_return=native.clear_return,
        open_hresult=native.open_hresult,
        open_return=native.open_return,
        after_page_count=native.after_page_count,
        after_modified=native.after_modified,
        after_control_count=native.after_control_count,
        after_control_hash=native.after_control_hash,
    )
