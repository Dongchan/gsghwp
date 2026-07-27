from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import cast


_STATE_NAMES = (
    "page_count",
    "modified",
    "list",
    "paragraph",
    "character",
    "control_count",
    "control_hash",
)
_FIXTURE_EVIDENCE = {
    "fixture_scope": "active_document_owner_context",
    "fixture_isolation_guaranteed": False,
    "operation_effect_verified": False,
    "evidence_scope": "invoke_and_state_observation_only",
}


def _integer(value: str) -> int:
    return int(value, 10)


def _state(fields: Sequence[str], start: int) -> dict[str, int]:
    return {
        name: _integer(fields[start + offset])
        for offset, name in enumerate(_STATE_NAMES)
    }


def _valid_document_state(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    state = cast(dict[str, object], value)
    page_count = state.get("page_count")
    modified = state.get("modified")
    position = tuple(state.get(name) for name in ("list", "paragraph", "character"))
    control_count = state.get("control_count")
    return (
        isinstance(page_count, int)
        and page_count > 0
        and modified in {0, 1}
        and all(isinstance(item, int) and item >= 0 for item in position)
        and isinstance(control_count, int)
        and control_count >= 0
    )


def _document_states_valid(parsed: Mapping[str, object]) -> bool:
    return _valid_document_state(parsed.get("before")) and _valid_document_state(
        parsed.get("after")
    )


def _false_automation_result_is_query(
    parsed: Mapping[str, object],
) -> bool:
    if parsed.get("member_kind") != "method":
        return True
    name = parsed.get("name")
    if not isinstance(name, str):
        return False
    matches = cast(
        list[str],
        re.findall(
            r"[A-Z]+(?=[A-Z][a-z]|[0-9]|$)|[A-Z]?[a-z]+|[0-9]+",
            name,
        ),
    )
    tokens = tuple(token.casefold() for token in matches)
    return tokens[:1] in {
        ("get",),
        ("is",),
        ("can",),
        ("has",),
        ("check",),
        ("query",),
        ("find",),
        ("read",),
    } or bool({"exist", "exists", "equivalent"}.intersection(tokens))


def _action(fields: Sequence[str]) -> dict[str, object]:
    if len(fields) != 29:
        raise ValueError("native ACTION response field count is invalid")
    return {
        "kind": "action",
        "name": fields[2],
        "create_action_hresult": _integer(fields[3]),
        "action_dispatch_hresult": _integer(fields[4]),
        "create_set_hresult": _integer(fields[5]),
        "set_dispatch_hresult": _integer(fields[6]),
        "get_default_hresult": _integer(fields[7]),
        "get_default_return": _integer(fields[8]),
        "message_mode_hresult": _integer(fields[9]),
        "execute_hresult": _integer(fields[10]),
        "execute_return": _integer(fields[11]),
        "run_hresult": _integer(fields[12]),
        "run_return": _integer(fields[13]),
        "before": _state(fields, 14),
        "after": _state(fields, 21),
        "elapsed_us": _integer(fields[28]),
    }


def _parameter_set(
    fields: Sequence[str],
    lines: Sequence[str],
) -> dict[str, object]:
    if len(fields) != 27:
        raise ValueError("native PARAMETER_SET response field count is invalid")
    items: list[dict[str, object]] = []
    for line in lines:
        item = line.split("\t")
        if len(item) not in {7, 8} or item[0] != "ITEM":
            raise ValueError("native PARAMETER_SET item response is invalid")
        has_return = len(item) == 8
        items.append(
            {
                "name": item[1],
                "create_hresult": _integer(item[2]),
                "set_hresult": _integer(item[3]),
                "get_hresult": _integer(item[4]),
                "exists_hresult": _integer(item[5]),
                "exists_return": _integer(item[6]) if has_return else None,
                "passed": item[7 if has_return else 6] == "1",
            }
        )
    return {
        "kind": "parameter_set",
        "name": fields[2],
        "create_action_hresult": _integer(fields[3]),
        "create_set_hresult": _integer(fields[4]),
        "get_default_hresult": _integer(fields[5]),
        "message_mode_hresult": _integer(fields[6]),
        "execute_hresult": _integer(fields[7]),
        "execute_return": _integer(fields[8]),
        "item_count": _integer(fields[9]),
        "passed_items": _integer(fields[10]),
        "failed_items": _integer(fields[11]),
        "before": _state(fields, 12),
        "after": _state(fields, 19),
        "elapsed_us": _integer(fields[26]),
        "items": items,
    }


def _automation(
    fields: Sequence[str],
    lines: Sequence[str],
) -> dict[str, object]:
    if len(fields) != 26:
        raise ValueError("native AUTOMATION response field count is invalid")
    outputs: list[dict[str, object]] = []
    for line in lines:
        output = line.split("\t")
        if len(output) != 4 or output[0] != "OUT":
            raise ValueError("native AUTOMATION output response is invalid")
        outputs.append(
            {
                "argument_index": _integer(output[1]),
                "variant_type": _integer(output[2]),
                "value": output[3],
            }
        )
    return {
        "kind": "automation",
        "owner": fields[2],
        "name": fields[3],
        "member_kind": fields[4],
        "owner_hresult": _integer(fields[5]),
        "argument_hresult": _integer(fields[6]),
        "invoke_hresult": _integer(fields[7]),
        "write_hresult": _integer(fields[8]),
        "variant_type": _integer(fields[9]),
        "value": fields[10],
        "before": _state(fields, 11),
        "after": _state(fields, 18),
        "elapsed_us": _integer(fields[25]),
        "outputs": outputs,
    }


def parse_native_response(response: str) -> dict[str, object]:
    lines = response.splitlines()
    if not lines:
        raise ValueError("native response is empty")
    fields = lines[0].split("\t")
    if len(fields) < 2 or fields[0] != "HCV1":
        raise ValueError("native response framing is invalid")
    if fields[1] == "ERROR":
        parsed: dict[str, object] = {
            "kind": "error",
            "code": fields[2] if len(fields) > 2 else "",
            "message": fields[3] if len(fields) > 3 else "",
        }
    elif fields[1] == "ACTION":
        parsed = _action(fields)
    elif fields[1] == "PARAMETER_SET":
        parsed = _parameter_set(fields, lines[1:])
    elif fields[1] == "AUTOMATION":
        parsed = _automation(fields, lines[1:])
    else:
        raise ValueError("native response kind is invalid")
    parsed.update(_FIXTURE_EVIDENCE)
    parsed["document_state_valid"] = _document_states_valid(parsed)
    return parsed


def classify_native_evidence(parsed: Mapping[str, object]) -> str:
    kind = parsed.get("kind")
    if not _document_states_valid(parsed):
        return "failed"
    if kind == "action":
        created = parsed.get("create_action_hresult") == 0
        invoked = (
            parsed.get("execute_hresult") == 0 and parsed.get("execute_return") == 1
        ) or (parsed.get("run_hresult") == 0 and parsed.get("run_return") == 1)
        return "passed" if created and invoked else "failed"
    if kind == "parameter_set":
        created = parsed.get("create_set_hresult") == 0
        item_count = parsed.get("item_count")
        passed_items = parsed.get("passed_items")
        complete = (
            isinstance(item_count, int)
            and item_count >= 0
            and passed_items == item_count
            and parsed.get("failed_items") == 0
        )
        execute_hresult = parsed.get("execute_hresult")
        execute_return = parsed.get("execute_return")
        invoked = (execute_hresult == 0 and execute_return == 1) or (
            execute_hresult == 1 and execute_return == -1
        )
        return "passed" if created and complete and invoked else "failed"
    if kind == "automation":
        resolved = parsed.get("owner_hresult") == 0
        prepared = parsed.get("argument_hresult") == 0
        invoked = parsed.get("invoke_hresult") == 0
        returned_false = parsed.get("variant_type") == 11 and str(
            parsed.get("value")
        ).casefold() in {"false", "0"}
        result_valid = not returned_false or _false_automation_result_is_query(parsed)
        return (
            "passed" if resolved and prepared and invoked and result_valid else "failed"
        )
    return "failed"
