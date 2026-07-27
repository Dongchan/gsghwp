from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Final

from hwp_errors import HwpLiveError
from hwp_official_api_catalog import load_official_api_catalog
from hwp_official_api_requests import (
    OfficialApiNativeRequest,
    OfficialApiSafety,
    build_official_api_requests,
)


DISABLED_OFFICIAL_API_CASE_IDS: Final[frozenset[str]] = frozenset(
    (
        "action:0608:SaveHistoryItem",
        "automation:0067:IHwpObject.ExportStyle",
        "automation:0068:IHwpObject.ImportStyle",
        "automation:0365:IDHwpParameterArray.Clone",
    )
)


@dataclass(frozen=True, slots=True)
class OfficialApiPolicyDecision:
    allowed: bool
    code: str
    reason: str


@dataclass(frozen=True, slots=True)
class OfficialApiBatchSelection:
    category: str
    start: int
    requested: int
    total_in_category: int
    requests: tuple[OfficialApiNativeRequest, ...]


@dataclass(frozen=True, slots=True)
class OfficialApiPayloadAssessment:
    request: OfficialApiNativeRequest
    forwarded_payload: str
    destructive_opt_in: bool


def enabled_official_api_requests(
    requests: tuple[OfficialApiNativeRequest, ...],
) -> tuple[OfficialApiNativeRequest, ...]:
    return tuple(
        request
        for request in requests
        if request.case_id not in DISABLED_OFFICIAL_API_CASE_IDS
    )


@lru_cache(maxsize=1)
def official_api_requests() -> tuple[OfficialApiNativeRequest, ...]:
    return enabled_official_api_requests(
        build_official_api_requests(load_official_api_catalog())
    )


def select_official_api_requests(
    category: str,
    start: int,
    limit: int,
) -> OfficialApiBatchSelection:
    if category not in {"action", "parameter_set", "automation"}:
        raise HwpLiveError("공식 API 분류가 올바르지 않습니다")
    if start < 1:
        raise HwpLiveError("공식 API 시작 순번은 1 이상이어야 합니다")
    if limit < 1 or limit > 100:
        raise HwpLiveError("공식 API 배치 크기는 1~100이어야 합니다")
    category_requests = tuple(
        request for request in official_api_requests() if request.category == category
    )
    if start > len(category_requests):
        raise HwpLiveError("공식 API 시작 순번이 해당 분류 범위를 벗어났습니다")
    return OfficialApiBatchSelection(
        category=category,
        start=start,
        requested=limit,
        total_in_category=len(category_requests),
        requests=category_requests[start - 1 : start - 1 + limit],
    )


def _resets_dialog_policy(request: OfficialApiNativeRequest) -> bool:
    arguments = tuple(
        line.split("\t") for line in request.input_lines if line.startswith("ARG\t")
    )
    if len(arguments) != 1:
        return False
    fields = arguments[0]
    if len(fields) != 3 or fields[1] not in {"I2", "I4", "UI4"}:
        return False
    try:
        return int(fields[2], 10) == 0
    except ValueError:
        return False


def _action_routing_is_catalog_safe(request: OfficialApiNativeRequest) -> bool:
    arguments = tuple(
        line.split("\t") for line in request.input_lines if line.startswith("ARG\t")
    )
    if len(arguments) != 2:
        return False
    decoded: list[str] = []
    for fields in arguments:
        if len(fields) != 3 or fields[:2] != ["ARG", "BSTR64"]:
            return False
        try:
            decoded.append(base64.b64decode(fields[2], validate=True).decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError):
            return False
    actions = _all_request_indexes()[0]
    routed = tuple(actions.get(name) for name in decoded)
    return all(
        candidate is not None
        and candidate.case_id not in DISABLED_OFFICIAL_API_CASE_IDS
        and not _requires_policy_preflight(candidate.safety)
        for candidate in routed
    )


def official_api_policy_decision(
    request: OfficialApiNativeRequest,
    *,
    destructive_opt_in: bool = False,
    owned_isolation_verified: bool = False,
) -> OfficialApiPolicyDecision:
    if request.case_id in DISABLED_OFFICIAL_API_CASE_IDS:
        return OfficialApiPolicyDecision(
            allowed=False,
            code="official_api_disabled",
            reason="호환성 정책상 실행하지 않는 공식 API case입니다",
        )
    safety = request.safety
    if safety.classification == "unclassified":
        return OfficialApiPolicyDecision(
            allowed=False,
            code="official_api_unclassified",
            reason="공식 카탈로그 메타데이터로 호출의 안전성을 분류하지 못했습니다",
        )
    if safety.indirect_execution:
        return OfficialApiPolicyDecision(
            allowed=False,
            code="indirect_execution_blocked",
            reason=(
                "호출 대상 코드를 사전 분류할 수 없는 매크로 실행은 live QA 문서에서 "
                "허용하지 않습니다"
            ),
        )
    if safety.action_routing_control and not _action_routing_is_catalog_safe(request):
        return OfficialApiPolicyDecision(
            allowed=False,
            code="action_routing_blocked",
            reason=(
                "후속 Action ID의 실제 의미를 바꾸는 라우팅 변경은 live QA 문서에서 "
                "허용하지 않습니다"
            ),
        )
    if safety.dialog_policy_control and not _resets_dialog_policy(request):
        return OfficialApiPolicyDecision(
            allowed=False,
            code="auto_confirmation_blocked",
            reason=(
                "대화상자 버튼을 지속적으로 자동 선택하는 모드는 QA live 문서에서 "
                "허용하지 않습니다"
            ),
        )
    if not safety.destructive:
        return OfficialApiPolicyDecision(
            allowed=True,
            code="allowed",
            reason="카탈로그 메타데이터상 문서 수명주기 파괴 호출이 아닙니다",
        )
    if not destructive_opt_in:
        return OfficialApiPolicyDecision(
            allowed=False,
            code="destructive_api_blocked",
            reason=(
                "카탈로그 메타데이터상 저장·닫기·종료·폐기 호출이므로 기본 차단했습니다"
            ),
        )
    if not safety.arguments_fail_safe:
        # One outward policy decision covers distinct argument-level causes,
        # while the reason preserves the actionable cause for callers.
        if (
            safety.argument_safety_issue
            == "destructive_bool_fail_safe_unverified"
        ):
            reason = (
                "카탈로그에서 파괴적 BOOL 인자의 fail-safe 값을 증명하지 못해 "
                "opt-in 호출도 차단했습니다"
            )
        elif (
            safety.argument_safety_issue
            == "raw_arguments_differ_from_catalog_fail_safe"
        ):
            reason = (
                "제공된 파괴성 인자가 카탈로그 기반 fail-safe 요청과 달라 "
                "opt-in 호출도 차단했습니다"
            )
        else:
            reason = (
                "파괴성 인자가 카탈로그 기반 fail-safe 요청과 일치한다고 "
                "검증하지 못해 opt-in 호출도 차단했습니다"
            )
        return OfficialApiPolicyDecision(
            allowed=False,
            code="destructive_argument_unverified",
            reason=reason,
        )
    if (
        not owned_isolation_verified
        or not safety.fixture_isolation_guaranteed
        or safety.fixture_scope != "server_owned_process_document"
    ):
        return OfficialApiPolicyDecision(
            allowed=False,
            code="owned_isolation_required",
            reason=(
                "파괴 호출 opt-in은 서버가 생성하고 소유권을 검증한 별도 "
                "프로세스와 문서에서만 허용됩니다"
            ),
        )
    return OfficialApiPolicyDecision(
        allowed=True,
        code="allowed_owned_isolation",
        reason="명시적 opt-in과 서버 소유 문서 격리가 모두 검증되었습니다",
    )


@lru_cache(maxsize=1)
def _all_request_indexes() -> tuple[
    dict[str, OfficialApiNativeRequest],
    dict[str, OfficialApiNativeRequest],
    dict[tuple[str, str, str], OfficialApiNativeRequest],
]:
    requests = build_official_api_requests(load_official_api_catalog())
    actions = {
        request.name: request for request in requests if request.category == "action"
    }
    parameter_sets = {
        request.name: request
        for request in requests
        if request.category == "parameter_set"
    }
    automation = {
        (request.owner or "", request.name, request.member_kind or ""): request
        for request in requests
        if request.category == "automation"
    }
    return actions, parameter_sets, automation


def _unclassified_request(
    *,
    category: str,
    name: str,
    owner: str | None = None,
    member_kind: str | None = None,
    payload: str,
    reason: str,
) -> OfficialApiNativeRequest:
    return OfficialApiNativeRequest(
        case_id=f"{category}:9999:{owner + '.' if owner else ''}{name}",
        category=category,
        name=name or "unclassified",
        owner=owner,
        member_kind=member_kind,
        source_page=1,
        payload=payload,
        input_lines=(),
        safety=OfficialApiSafety(
            effect="none",
            target_scope="unknown",
            classification="unclassified",
            rationale=reason,
        ),
    )


def _requires_policy_preflight(safety: OfficialApiSafety) -> bool:
    return (
        safety.classification == "unclassified"
        or safety.destructive
        or safety.dialog_policy_control
        or safety.indirect_execution
        or safety.action_routing_control
        or not safety.arguments_fail_safe
    )


def _lines(payload: str) -> tuple[str, ...]:
    normalized = payload.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if len(lines) > 1 and lines[-1] == "":
        _ = lines.pop()
    return tuple(lines)


def _delegated_action_name(
    request: OfficialApiNativeRequest,
    input_lines: tuple[str, ...],
) -> tuple[bool, str | None]:
    catalog = load_official_api_catalog()
    member = next(
        (
            item
            for item in catalog.automation_members
            if item.owner == request.owner
            and item.name == request.name
            and item.member_kind == request.member_kind
        ),
        None,
    )
    if member is None:
        return False, None
    description = member.description
    non_invoking_context = any(
        marker in description for marker in ("않도록", "가능", "상태", "생성")
    )
    delegates = (
        member.member_kind == "method"
        and "BSTR" in member.declaration.upper()
        and not non_invoking_context
        and (
            ("액션" in description and ("실행" in description or "수행" in description))
            or "Execute" in description
        )
    )
    if not delegates:
        return False, None
    arguments = tuple(line for line in input_lines if line.startswith("ARG\t"))
    if not arguments:
        return True, None
    fields = arguments[0].split("\t")
    if len(fields) != 3 or fields[:2] != ["ARG", "BSTR64"]:
        return True, None
    try:
        decoded = base64.b64decode(fields[2], validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return True, None
    return True, decoded


def assess_official_api_payload(payload: str) -> OfficialApiPayloadAssessment:
    lines = _lines(payload)
    if len(lines) < 3 or lines[0] != "HCV1" or lines[-1] != "END":
        request = _unclassified_request(
            category="action",
            name="invalid",
            payload=payload,
            reason="HCV1 request framing is invalid",
        )
        return OfficialApiPayloadAssessment(request, payload, False)

    command = lines[1].split("\t")
    input_lines = lines[2:-1]
    policy_lines = tuple(line for line in input_lines if line.startswith("POLICY\t"))
    destructive_opt_in = policy_lines == ("POLICY\tDESTRUCTIVE_OPT_IN",)
    unknown_policy = bool(policy_lines) and not destructive_opt_in
    forwarded_lines = (
        lines[:2]
        + tuple(line for line in input_lines if not line.startswith("POLICY\t"))
        + ("END",)
    )
    forwarded_payload = "\n".join(forwarded_lines)
    actions, parameter_sets, automation = _all_request_indexes()

    if unknown_policy:
        request = _unclassified_request(
            category="action",
            name="invalid_policy",
            payload=forwarded_payload,
            reason="HCV1 policy directive is invalid",
        )
        return OfficialApiPayloadAssessment(
            request,
            forwarded_payload,
            destructive_opt_in,
        )

    request: OfficialApiNativeRequest | None = None
    if len(command) == 2 and command[0] == "ACTION":
        request = actions.get(command[1])
    elif len(command) == 3 and command[0] == "PARAMETER_SET":
        parameter_request = parameter_sets.get(command[1])
        linked_action = command[2]
        if parameter_request is not None and linked_action == "-":
            request = parameter_request
        elif parameter_request is not None:
            action_request = actions.get(linked_action)
            catalog_action = next(
                (
                    action
                    for action in load_official_api_catalog().actions
                    if action.name == linked_action
                ),
                None,
            )
            normalized_set = (
                None
                if catalog_action is None or catalog_action.parameter_set is None
                else catalog_action.parameter_set.rstrip("*")
            )
            if action_request is not None and normalized_set == command[1]:
                request = replace(
                    parameter_request,
                    safety=replace(
                        action_request.safety,
                        classification="inherited_catalog_metadata",
                        rationale=(
                            "raw parameter-set invocation inherits the linked "
                            f"catalog action effect: {linked_action}"
                        ),
                    ),
                )
    elif len(command) == 4 and command[0] == "AUTOMATION":
        request = automation.get((command[1], command[2], command[3]))
        if request is not None:
            delegates, delegated_name = _delegated_action_name(
                request,
                tuple(
                    line
                    for line in input_lines
                    if not line.startswith(("POLICY\t", "FIXTURE\t"))
                ),
            )
            if delegates:
                delegated = (
                    None if delegated_name is None else actions.get(delegated_name)
                )
                if delegated is None:
                    request = None
                elif (
                    delegated.case_id in DISABLED_OFFICIAL_API_CASE_IDS
                    or _requires_policy_preflight(delegated.safety)
                ):
                    request = replace(
                        delegated,
                        payload=forwarded_payload,
                        input_lines=tuple(input_lines),
                    )

    if request is None:
        category = (
            command[0].casefold()
            if command and command[0] in {"ACTION", "PARAMETER_SET", "AUTOMATION"}
            else "action"
        )
        owner = command[1] if len(command) == 4 and command[0] == "AUTOMATION" else None
        name_index = 2 if owner is not None else 1
        name = command[name_index] if len(command) > name_index else "unclassified"
        member_kind = command[3] if owner is not None else None
        request = _unclassified_request(
            category=category,
            name=name,
            owner=owner,
            member_kind=member_kind,
            payload=forwarded_payload,
            reason="payload does not exactly match catalog execution metadata",
        )
    else:
        forwarded_input_lines = tuple(
            line
            for line in input_lines
            if not line.startswith(("POLICY\t", "FIXTURE\t"))
        )
        safety = request.safety
        if safety.destructive and forwarded_input_lines != request.input_lines:
            safety = replace(
                safety,
                arguments_fail_safe=False,
                argument_safety_issue=(
                    "raw_arguments_differ_from_catalog_fail_safe"
                ),
                rationale=(
                    safety.rationale
                    + "; raw arguments differ from the catalog-derived fail-safe request"
                ),
            )
        request = replace(
            request,
            payload=forwarded_payload,
            input_lines=forwarded_input_lines,
            safety=safety,
        )
    return OfficialApiPayloadAssessment(
        request=request,
        forwarded_payload=forwarded_payload,
        destructive_opt_in=destructive_opt_in,
    )


def official_api_policy_error_response(
    decision: OfficialApiPolicyDecision,
) -> str:
    message = decision.reason.replace("\t", " ").replace("\r", " ").replace("\n", " ")
    return f"HCV1\tERROR\t{decision.code.upper()}\t{message}"
