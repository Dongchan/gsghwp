from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal, Protocol, assert_never

from typing_extensions import TypeIs

from pydantic import Field

from hwp_public_cell_selector import SeriesImageCell, SeriesTextCell
from hwp_live_values import ContractModel
from hwp_operation_contract import (
    OperationResult,
    OperationStatus,
    WorkflowTargetCandidate,
)
from hwp_runtime_identity import RuntimeBuildInfo


type PublicActionStatus = Literal[
    "succeeded",
    "needs_input",
    "needs_target",
    "unsupported",
    "failed",
    "partial_failure",
]
type FailedOperationStatus = Literal[
    "resolved",
    "not_found",
    "needs_guard",
    "confirmation_required",
    "blocked",
    "known_failure",
    "schema_conflict",
    "operation_in_progress",
    "operation_stale",
    "operation_failed",
    "operation_aborted",
    "operation_reconciled",
    "request_id_conflict",
]
type TerminalPublicActionStatus = Literal[
    "succeeded",
    "unsupported",
    "failed",
    "partial_failure",
]

_EMPTY_INPUT_ALIASES: Final[Mapping[str, str]] = MappingProxyType({})


class PublicTableTarget(ContractModel):
    document_path: str | None = Field(default=None, max_length=32_767)
    page: int | None = Field(default=None, ge=1)
    caption: str | None = Field(default=None, min_length=1, max_length=500)
    headers: tuple[str, ...] = Field(default=(), max_length=100)
    table_index: int | None = Field(default=None, ge=1)
    target_id: str | None = Field(default=None, min_length=1, max_length=128)


class PublicTargetCandidate(ContractModel):
    target_id: str = Field(min_length=1, max_length=128)
    page: int = Field(ge=1)
    table_index: int | None = Field(default=None, ge=1)
    rows: int | None = Field(default=None, ge=1)
    columns: int | None = Field(default=None, ge=1)
    caption: str | None = Field(default=None, max_length=2_000)
    headers: tuple[str, ...] = Field(default=(), max_length=100)


class SeriesItem(ContractModel):
    values: dict[str, str] = Field(
        default_factory=dict,
        max_length=500,
        description="고유한 현재 셀 텍스트 또는 셀 주소를 새 텍스트에 대응시킵니다.",
    )
    images: dict[str, Path] = Field(
        default_factory=dict,
        max_length=200,
        description="고유한 현재 셀 텍스트 또는 셀 주소를 그림 경로에 대응시킵니다.",
    )
    text_cells: tuple[SeriesTextCell, ...] = Field(
        default=(),
        max_length=500,
        description="중복 라벨이나 상대 위치가 필요한 텍스트 셀 선택 목록입니다.",
    )
    image_cells: tuple[SeriesImageCell, ...] = Field(
        default=(),
        max_length=200,
        description="중복 라벨이나 상대 위치가 필요한 그림 셀 선택 목록입니다.",
    )
    caption: str | None = Field(default=None, min_length=1, max_length=2_000)


class PublicTargetStore(Protocol):
    def remember(
        self,
        candidates: tuple[WorkflowTargetCandidate, ...],
        document_path: str | None,
    ) -> tuple[str, ...]: ...

    def clear(self) -> None: ...


class PublicActionResult(ContractModel):
    status: PublicActionStatus
    message: str = Field(max_length=4_000)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    runtime: RuntimeBuildInfo
    verified: bool
    modified: bool
    required_inputs: tuple[str, ...] = Field(default=(), max_length=20)
    target_candidates: tuple[PublicTargetCandidate, ...] = Field(
        default=(),
        max_length=3,
    )
    recipe_id: str | None = Field(default=None, max_length=100)
    commands_executed: int = Field(default=0, ge=0)
    updated_addresses: tuple[str, ...] = Field(default=(), max_length=20_000)
    created_target_ids: tuple[str, ...] = Field(default=(), max_length=500)
    retry_safe: bool


def _is_failed_status(status: OperationStatus) -> TypeIs[FailedOperationStatus]:
    return status in {
        "resolved",
        "not_found",
        "needs_guard",
        "confirmation_required",
        "blocked",
        "known_failure",
        "schema_conflict",
        "operation_in_progress",
        "operation_stale",
        "operation_failed",
        "operation_aborted",
        "operation_reconciled",
        "request_id_conflict",
    }


def _is_terminal_status(
    status: PublicActionStatus,
) -> TypeIs[TerminalPublicActionStatus]:
    return status in {"succeeded", "unsupported", "failed", "partial_failure"}


def _public_status(result: OperationResult) -> PublicActionStatus:
    match result.status:
        case "executed":
            return "succeeded"
        case "needs_input":
            return "needs_input"
        case "ambiguous":
            return "needs_target"
        case "unsupported":
            return "unsupported"
        case "partial_change":
            return "partial_failure"
        case "transport_error":
            changed = (
                result.changed
                or result.modified is True
                or result.partial_change
                or result.partial_mutation is True
            )
            return "partial_failure" if changed else "failed"
        case _ as unreachable if not _is_failed_status(unreachable):
            assert_never(unreachable)
        case _:
            return "failed"


def _required_inputs(
    status: PublicActionStatus,
    result: OperationResult,
    input_aliases: Mapping[str, str],
) -> tuple[str, ...]:
    match status:
        case "needs_target":
            return (input_aliases.get("inputs.target", "target.target_id"),)
        case "needs_input":
            aliases = {
                "inputs.data": "records | cells | rows",
                "inputs.data.start_cell": "start_cell",
                "inputs.target": "target",
                **input_aliases,
            }
            return tuple(
                aliases.get(value, value.removeprefix("inputs."))
                for value in result.required_inputs
            )
        case _ as unreachable if not _is_terminal_status(unreachable):
            assert_never(unreachable)
        case _:
            return ()


def _verified(result: OperationResult) -> bool:
    if result.verified is not None:
        return result.verified
    return result.status == "executed" and result.verification is not None


def refresh_public_target_ids(
    store: PublicTargetStore,
    result: OperationResult,
    document_path: str | None,
) -> tuple[str, ...]:
    if result.target_candidates:
        return store.remember(result.target_candidates, document_path)
    if (
        result.status != "needs_input"
        or result.changed
        or result.modified is True
        or result.partial_change
        or result.partial_mutation is True
    ):
        store.clear()
    return ()


def to_public_action_result(
    result: OperationResult,
    target_ids: tuple[str, ...],
    input_aliases: Mapping[str, str] = _EMPTY_INPUT_ALIASES,
) -> PublicActionResult:
    candidates = result.target_candidates[:3]
    public_candidates = tuple(
        PublicTargetCandidate(
            target_id=target_id,
            page=candidate.page,
            table_index=candidate.table_index,
            rows=candidate.rows,
            columns=candidate.columns,
            caption=candidate.caption,
            headers=candidate.header_preview,
        )
        for candidate, target_id in zip(candidates, target_ids, strict=True)
    )
    status = _public_status(result)
    modified = result.changed if result.modified is None else result.modified
    retry_safe = (
        status in {"needs_input", "needs_target"}
        if result.retry_safe is None
        else result.retry_safe
    )
    return PublicActionResult(
        status=status,
        message=result.message,
        request_id=result.request_id,
        runtime=result.runtime,
        verified=_verified(result),
        modified=modified,
        required_inputs=_required_inputs(status, result, input_aliases),
        target_candidates=public_candidates,
        recipe_id=result.recipe_id,
        commands_executed=result.commands_executed or 0,
        updated_addresses=result.updated_addresses,
        created_target_ids=result.created_control_ids,
        retry_safe=retry_safe,
    )
