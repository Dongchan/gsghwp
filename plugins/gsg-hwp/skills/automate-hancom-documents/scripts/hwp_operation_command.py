from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from hwp_live_native_action_models import (
    BooleanValue,
    IntegerValue,
    NativeSetter,
    ParameterActionCommand,
    RunCommand,
    TextValue,
)
from hwp_operation_contract import (
    OperationCandidate,
    OperationCommandPlan,
    OperationInputSpec,
    OperationInputValue,
)


_SCALAR_TEXT_TYPES: Final = frozenset({"PIT_BSTR"})
_SCALAR_BOOLEAN_TYPES: Final = frozenset({"PMT_BOOL"})
_SCALAR_INTEGER_TYPES: Final = frozenset(
    {
        "PIT_I",
        "PIT_I1",
        "PIT_I4",
        "PIT_U",
        "PIT_UI",
        "PIT_UI1",
        "PIT_UI2",
        "PIT_UI4",
        "PMT_INT",
        "PMT_UINT",
        "PMT_UINT32",
    }
)
SCALAR_PARAMETER_TYPES: Final = (
    _SCALAR_TEXT_TYPES | _SCALAR_BOOLEAN_TYPES | _SCALAR_INTEGER_TYPES
)


def _integer_value(spec: OperationInputSpec, value: OperationInputValue) -> IntegerValue:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{spec.name}에는 정수가 필요합니다")
    limits = {
        "PIT_I1": (-128, 127),
        "PIT_UI1": (0, 255),
        "PIT_UI2": (0, 65_535),
    }
    lower, upper = limits.get(spec.value_type, (-2_147_483_648, 2_147_483_647))
    if spec.value_type in {
        "PIT_U",
        "PIT_UI",
        "PIT_UI4",
        "PMT_UINT",
        "PMT_UINT32",
    }:
        lower = 0
    if value < lower or value > upper:
        raise ValueError(f"{spec.name} 값은 {lower}~{upper} 범위여야 합니다")
    return IntegerValue(value)


def _native_value(
    spec: OperationInputSpec,
    value: OperationInputValue,
) -> IntegerValue | BooleanValue | TextValue:
    if spec.value_type in _SCALAR_INTEGER_TYPES:
        return _integer_value(spec, value)
    if spec.value_type in _SCALAR_BOOLEAN_TYPES:
        if not isinstance(value, bool):
            raise ValueError(f"{spec.name}에는 true 또는 false가 필요합니다")
        return BooleanValue(value)
    if spec.value_type in _SCALAR_TEXT_TYPES:
        if not isinstance(value, str):
            raise ValueError(f"{spec.name}에는 문자열이 필요합니다")
        if len(value) > 1_000_000:
            raise ValueError(f"{spec.name} 문자열이 너무 큽니다")
        return TextValue(value)
    raise ValueError(f"{spec.name}의 {spec.value_type} 타입은 아직 지원하지 않습니다")


def plan_operation_command(
    operation: OperationCandidate,
    inputs: Mapping[str, OperationInputValue],
    *,
    use_defaults: bool,
) -> OperationCommandPlan:
    if operation.native_execution == "blocked":
        return OperationCommandPlan(
            "unsupported",
            None,
            "생산 경로에서 차단된 공식 API입니다",
        )
    if operation.native_execution == "catalog_only":
        return OperationCommandPlan(
            "unsupported",
            None,
            "카탈로그에서 찾았지만 현재 생산 네이티브 단일 실행 계약으로는 지원하지 않습니다",
        )
    if operation.native_execution == "run_action":
        if inputs:
            return OperationCommandPlan(
                "needs_input",
                None,
                "이 액션은 입력값을 받지 않습니다",
            )
        return OperationCommandPlan("ready", RunCommand(operation.name), "")

    if operation.parameter_set is None:
        return OperationCommandPlan(
            "unsupported",
            None,
            "공식 ParameterSet 이름을 확정할 수 없습니다",
        )
    if not inputs and not use_defaults:
        return OperationCommandPlan(
            "needs_input",
            None,
            "ParameterSet 입력을 주거나 use_defaults=true를 명시해야 합니다",
        )

    by_name = {spec.name.casefold(): spec for spec in operation.inputs}
    setters: list[NativeSetter] = []
    used: set[str] = set()
    try:
        for supplied_name, supplied_value in inputs.items():
            key = supplied_name.casefold()
            if key in used:
                raise ValueError(f"중복 입력입니다: {supplied_name}")
            used.add(key)
            spec = by_name.get(key)
            if spec is None:
                raise ValueError(f"공식 ParameterSet에 없는 입력입니다: {supplied_name}")
            setters.append(NativeSetter(spec.name, _native_value(spec, supplied_value)))
    except ValueError as error:
        return OperationCommandPlan("needs_input", None, str(error))

    return OperationCommandPlan(
        "ready",
        ParameterActionCommand(
            action=operation.name,
            parameter_set=operation.parameter_set,
            setters=tuple(setters),
        ),
        "",
    )
