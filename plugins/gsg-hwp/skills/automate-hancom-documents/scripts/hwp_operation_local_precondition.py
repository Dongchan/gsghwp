"""쓰기 도구가 공유하는 대상 국소 전제조건 계약.

공동 편집 -- 사용자가 한/글에서 손으로 고치는 동안 에이전트도 같은 문서를
고친다 -- 에서 쓰기 게이트는 문서 전체가 그대로인지가 아니라 "내가 고치려던
그 대상이 아직 그 자리에 있는가"여야 한다. 전체 시그니처 게이트는 이미 일반
쓰기에 없다(hwp_live_native_batch.execute_native_actions 의
expected_content_signature 기본값은 "" 이고, 비어 있지 않게 넣는 곳은 체크포인트
복원 하나뿐이다).

대상 해석은 도구마다 이미 있고, 실행 시점의 재확인도 각 네이티브 경로가 이미
한다:

* 표 서식/병합/분할 -- hwp_live_native_format_target.resolve_native_table_target
* 표 채우기 -- hwp_live_workflow_table_resolver.resolve_workflow_table
* 그림/개체 -- hwp_priority_object_inputs.resolve_control_target

셋 다 target.control_instance_id 를 먼저 믿고, 그 식별자가 문서의 어떤 개체와도
맞지 않으면 status="not_found" 로 돌려준다. 여기서 표준화하는 것은 그 다음
한 걸음이다: 그 실패가 "대상이 그 자리에 없다"뿐이고 문서를 건드리지 않았다면,
휘발성 식별자(control_instance_id)를 버리고 호출자가 함께 준 안정 서술어
(table_index / caption_contains / header_signature)로 **정확히 한 번** 다시
해석해 재시도한다. 다시 해석해도 없거나 모호하면 그때 구조화된 에러를
표면화한다.

왜 식별자만 버리는가: instance id 는 사용자가 표를 지우고 다시 만들면 바뀌지만
"3쪽의 두 번째 표"나 "이 캡션을 가진 표"는 그대로다. 재해석은 호출자가 이미
말한 것을 다시 읽는 것이지 새로 추측하는 것이 아니다.

남는 위험과 그 위험을 막는 것: 호출자가 오래된 조회에서 얻은 target_id 를
그대로 들고 왔고 그 사이 앞쪽에 표가 하나 끼어들었다면, table_index 로 다시
해석한 대상은 호출자가 뜻한 표가 아닐 수 있다. 표 채우기에서는 셀마다 실리는
expected_text 가 이 경우를 잡는다 -- preserve_display_format 과
preserve_character_style 이 둘 다 기본값 True 이고, 그때 prepare_table_fill 은
셀의 현재 원문을 함께 싣는다(hwp_live_workflow_table.py:283-323). 다른 표에
쓰려 하면 네이티브가 STALE_CELL_TEXT 로 편집 전에 멈춘다. 서식 변경에는 그런
원문 대조가 없으므로, 이 재해석은 서식에서만 "쪽·순번이 가리키는 대상"을 그대로
믿는다.

재시도는 같은 operation_id 를 쓴다. 저널 티켓을 새로 뽑지 않고 첫 시도와 같은
티켓 안에서 한 번 더 실행하므로 멱등성은 그대로다.
"""

from __future__ import annotations

from typing import Final

from hwp_live_native_action_contract import NativeActionFailure
from hwp_live_text_patch_contract import TextPatchRequest, TextPatchTarget
from hwp_operation_contract import (
    HwpOperateInputs,
    HwpOperateTarget,
    OperationResult,
)
from hwp_operation_verification import classify_native_failure_mutation


#: 계약이 허용하는 자동 재시도 횟수. 두 번째부터는 호출자에게 돌려준다.
LOCAL_PRECONDITION_RETRY_LIMIT: Final = 1

#: 대상을 하나로 확정하지 못했다는 뜻의 상태. 다른 상태는 대상 문제가 아니다.
_TARGET_PRECONDITION_STATUSES: Final = frozenset({"not_found", "ambiguous"})

#: 지목한 자리의 기대 상태가 어긋났다는 네이티브 코드. 셋 다 편집 전 검사에서
#: 나오므로 문서는 그대로다(ActionTextPatch.cpp 의 PatchSelectedText·
#: SelectCellTextPatchMatch 는 모두 선택만 한 상태에서 SetError 로 끝난다).
_TEXT_PRECONDITION_CODES: Final = frozenset(
    {
        "STALE_SELECTION_TEXT",
        "TEXT_NOT_FOUND",
        "TEXT_OCCURRENCE_NOT_FOUND",
    }
)

_RETRY_NOTICE: Final = (
    "지목한 대상 식별자가 문서와 맞지 않아 요청의 대상 조건으로 한 번 다시 "
    "해석해 실행했습니다"
)
_EXHAUSTED_NOTICE: Final = (
    "요청의 대상 조건으로 한 번 다시 해석했지만 대상을 확정하지 못했습니다"
)


def _left_document_unchanged(result: OperationResult) -> bool:
    """이 실패가 문서를 건드리지 않았다는 증거가 모두 있는가.

    하나라도 변경 흔적이 있으면 재시도는 두 번 쓰는 일이 된다.

    ``partial_mutation`` 이 ``None`` 인 것은 그 자체로는 흔적이 아니다. 대상
    해석 단계에서 만들어지는 결과(workflow_result, 서식 recipe 의 _result)는
    이 칸을 아예 채우지 않고 기본값 ``None`` 으로 둔다. 변경 여부가 정말
    불확실한 실패는 native_action_failure_result 가 ``partial_change=True`` 를
    함께 실으므로 바로 위 검사에서 걸린다.
    """
    return (
        not result.changed
        and not result.partial_change
        and result.partial_mutation is not True
        and not result.reconcile_required
        and result.retry_safe is not False
        and not result.commands_completed
        and not result.commands_executed
    )


def is_local_target_precondition_failure(result: OperationResult) -> bool:
    """ "대상이 그 자리에 없다"뿐인 실패인가.

    상태가 대상 확정 실패이고, 문서를 건드리지 않았을 때만 참이다.
    """
    return result.status in _TARGET_PRECONDITION_STATUSES and _left_document_unchanged(
        result
    )


def native_failure_left_document_untouched(error: BaseException | None) -> bool:
    """네이티브가 "이 실패는 문서를 한 글자도 건드리지 않았다"고 증언했는가.

    실패 코드로 판정하지 않는다. 브리지는 실패마다 다섯 가지 증거를 함께 싣고
    (`hwp_live_native_action_contract._native_error` 의 HCA2 해독), 그 다섯을
    문서 상태에 대한 판정으로 바꾸는 것은 이 저장소에 이미 하나뿐이다 --
    `classify_native_failure_mutation`. 여기서 하는 일은 그 판정 중
    "unchanged" 만 골라 쓰는 것이고, 코드 목록으로 맞히는 것보다 넓으면서도
    구조 지문이 어긋난 실패는 알아서 "uncertain" 으로 떨어진다.

    ``NativeActionFailure`` 가 아닌 예외에는 이 증거가 아예 없으므로 거짓이다.
    모른다는 것과 안 건드렸다는 것은 다르고, 이 답을 쓰는 쪽은 "안 건드렸다"에만
    복구를 건너뛴다.
    """
    if not isinstance(error, NativeActionFailure):
        return False
    return (
        classify_native_failure_mutation(
            commands_completed=error.commands_completed,
            partial_mutation=error.partial_mutation,
            retry_safe=error.retry_safe,
            structure_digest_before=error.structure_digest_before,
            structure_digest_after=error.structure_digest_after,
        )
        == "unchanged"
    )


def is_local_text_precondition_failure(failure: NativeActionFailure) -> bool:
    """본문 패치가 지목한 자리의 기대 원문과 어긋나서 멈춘 것인가.

    "문서를 안 건드렸다"는 절반은 브리지 증거가 말하고
    (`native_failure_left_document_untouched`), 여기서 더하는 것은 "그 실패가
    *다시 해석해 볼 만한* 지목 실패인가"라는 나머지 절반뿐이다. 코드 목록이
    좁은 것은 그래서다 -- 목록에 없는 실패도 문서는 그대로일 수 있지만, 그때
    재해석은 같은 요청을 한 번 더 보내는 일이 된다.
    """
    return failure.code in _TEXT_PRECONDITION_CODES and (
        native_failure_left_document_untouched(failure)
    )


def reinterpreted_operate_target(
    target: HwpOperateTarget | None,
) -> HwpOperateTarget | None:
    """휘발성 식별자를 버리고 호출자의 안정 서술어만 남긴 대상.

    다시 해석할 근거가 없으면 ``None`` -- 그때는 재시도가 첫 시도와 똑같은
    요청이 되므로 하지 않는다.

    page_hint 만 있는 대상은 재해석하지 않는다. 식별자를 빼면 해석기가 "그 쪽의
    유일한 표" 같은 다른 규칙으로 내려가 호출자가 지목한 적 없는 개체를 고를 수
    있다. 어느 것인지를 실제로 말하는 서술어 -- table_index, caption_contains,
    header_signature -- 가 하나라도 있어야 재해석이 성립한다.
    """
    if target is None:
        return None
    if target.control_instance_id is None and not target.control_instance_ids:
        return None
    if (
        target.table_index is None
        and target.caption_contains is None
        and not target.header_signature
    ):
        return None
    return target.model_copy(
        update={"control_instance_id": None, "control_instance_ids": ()}
    )


def reinterpreted_operate_inputs(
    inputs: HwpOperateInputs,
) -> HwpOperateInputs | None:
    """재해석한 대상으로 바꾼 같은 요청. 재해석할 수 없으면 ``None``."""
    target = reinterpreted_operate_target(inputs.target)
    if target is None:
        return None
    return inputs.model_copy(update={"target": target})


def reinterpreted_text_patch(request: TextPatchRequest) -> TextPatchRequest | None:
    """위치 대신 원문으로 다시 해석한 같은 본문 패치.

    ``range`` 는 (list, paragraph, character) 좌표다. 사용자가 그 위에 한 문단을
    끼워 넣으면 좌표는 다른 글자를 가리키고, 네이티브는 STALE_SELECTION_TEXT 로
    편집 전에 멈춘다. 그때 호출자가 함께 준 expected_text 는 여전히 유효한
    지목이므로 문서 전체에서 그 원문을 찾는 ``find`` 로 다시 해석한다.
    occurrence 를 싣지 않으므로 같은 원문이 여러 곳이면 네이티브가
    AMBIGUOUS_TEXT_MATCH 로 거부한다 -- 엉뚱한 자리를 고치는 대신 모호하다고
    답한다.

    ``table_cell`` 은 재해석하지 않는다. 그 대상의 휘발성 식별자는 표
    instance id 인데, 본문 패치 요청은 그 표를 다시 찾을 안정 서술어(쪽·순번·
    캡션)를 들고 있지 않다. ``find``·``current`` 도 마찬가지로 이미 가장 넓은
    해석이라 더 넓힐 곳이 없다.
    """
    target = request.target
    if target.kind != "range" or request.expected_text is None:
        return None
    if target.start is None or target.end is None:
        return None
    return TextPatchRequest(
        target=TextPatchTarget(kind="find", match_case=True),
        expected_text=request.expected_text,
        replacement=request.replacement,
        formatting=request.formatting,
        post_selection=request.post_selection,
    )


def _with_notice(result: OperationResult, notice: str) -> OperationResult:
    message = result.message
    return result.model_copy(
        update={"message": f"{message}. {notice}" if message else notice}
    )


def local_precondition_retry_result(
    first: OperationResult,
    retried: OperationResult,
) -> OperationResult:
    """자동 재해석 재시도 뒤에 호출자에게 돌려줄 결과.

    재시도가 대상 문제로 끝나지 않았으면 -- 성공했든 다른 이유로 실패했든 --
    그 결과가 답이다. 지금 문서에서 실제로 일어난 일이기 때문이다.

    둘 다 대상 문제로 끝났으면 **첫 시도**의 결과를 유지하고 재해석까지
    해봤다는 사실만 메시지에 덧붙인다. 첫 시도가 호출자가 실제로 보낸 요청이고,
    그 진단은 호출자가 준 식별자를 이름으로 지목한다 -- "그 target_id 를 다시
    조회하라"는 다음 행동이 거기서 나온다. 재시도의 진단은 계약이 스스로 만든
    다른 요청("3쪽의 두 번째 표")을 설명하므로, target_id 를 준 호출자에게는
    자기가 보낸 적 없는 조건에 대한 답으로 읽힌다.
    """
    if not is_local_target_precondition_failure(retried):
        return _with_notice(retried, _RETRY_NOTICE)
    return _with_notice(first, _EXHAUSTED_NOTICE)
