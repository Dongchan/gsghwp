from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never

from typing_extensions import TypeIs

from hwp_operation_contract import HwpOperateInputs, OperationResult
from hwp_public_table_plan import (
    PublicTableResolutionRequest,
    ResolvedPublicTable,
    resolve_public_table,
)
from hwp_public_table_target import PublicTableTargetStore
from hwp_public_table_tools import PublicTableExecutor


@dataclass(frozen=True, slots=True)
class PublicTableEditExecutionRequest:
    intent: str
    inputs: HwpOperateInputs


def _is_resolved_public_table(
    result: ResolvedPublicTable | OperationResult,
) -> TypeIs[ResolvedPublicTable]:
    return isinstance(result, ResolvedPublicTable)


async def execute_public_table_edit(
    executor: PublicTableExecutor,
    targets: PublicTableTargetStore,
    request: PublicTableEditExecutionRequest,
) -> OperationResult:
    result = await executor.execute(request.intent, request.inputs, None)
    target = request.inputs.target
    if (
        result.status == "ambiguous"
        and not result.target_candidates
        and target is not None
        and target.binding == "selection"
    ):
        assert request.inputs.request_id is not None
        candidate_resolution = await resolve_public_table(
            executor,
            targets,
            PublicTableResolutionRequest(
                target=None,
                request_id=request.inputs.request_id,
                query=request.intent,
            ),
        )
        match candidate_resolution:
            case OperationResult() as candidate_result:
                if candidate_result.target_candidates:
                    result = result.model_copy(
                        update={
                            "target_candidates": candidate_result.target_candidates,
                        }
                    )
            case _ as unreachable if not _is_resolved_public_table(unreachable):
                assert_never(unreachable)
            case _:
                return result
    return result
