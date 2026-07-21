from __future__ import annotations

from collections.abc import Sequence


_STATE_NAMES = (
    "page_count",
    "modified",
    "list",
    "paragraph",
    "character",
    "control_count",
    "control_hash",
)


def _integer(value: str) -> int:
    return int(value, 10)


def _state(fields: Sequence[str], start: int) -> dict[str, int]:
    return {
        name: _integer(fields[start + offset])
        for offset, name in enumerate(_STATE_NAMES)
    }


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
        return {
            "kind": "error",
            "code": fields[2] if len(fields) > 2 else "",
            "message": fields[3] if len(fields) > 3 else "",
        }
    if fields[1] == "ACTION":
        return _action(fields)
    if fields[1] == "PARAMETER_SET":
        return _parameter_set(fields, lines[1:])
    if fields[1] == "AUTOMATION":
        return _automation(fields, lines[1:])
    raise ValueError("native response kind is invalid")


def classify_native_evidence(parsed: dict[str, object]) -> str:
    kind = parsed.get("kind")
    if kind == "action":
        created = parsed.get("create_action_hresult") == 0
        invoked = (
            parsed.get("execute_hresult") == 0 or parsed.get("run_hresult") == 0
        )
        return "passed" if created and invoked else "failed"
    if kind == "parameter_set":
        created = parsed.get("create_set_hresult") == 0
        complete = parsed.get("failed_items") == 0
        return "passed" if created and complete else "failed"
    if kind == "automation":
        resolved = parsed.get("owner_hresult") == 0
        prepared = parsed.get("argument_hresult") == 0
        invoked = parsed.get("invoke_hresult") == 0
        return "passed" if resolved and prepared and invoked else "failed"
    return "failed"
