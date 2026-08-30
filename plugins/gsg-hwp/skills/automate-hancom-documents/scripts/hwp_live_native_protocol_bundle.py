from __future__ import annotations

from base64 import b64decode, b64encode
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from hwp_live_values import ContractModel


class NativeBundleOperation(StrEnum):
    OBSERVE = "OBSERVE"
    MUTATE = "MUTATE"
    HISTORY = "HISTORY"
    SAVE = "SAVE"
    SAVE_REOPEN = "SAVE_REOPEN"
    RENDER = "RENDER"
    CAPABILITIES = "CAPABILITIES"
    DIAGNOSE = "DIAGNOSE"


class NativeBundleStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


class NativeBundleStage(StrEnum):
    PARSE = "parse"
    ROUTE = "route"
    CAPABILITY = "capability"
    OBSERVE = "observe"
    MUTATION = "mutation"
    HISTORY = "history"
    SAVE = "save"
    REOPEN = "reopen"
    RENDER = "render"
    LIFECYCLE = "lifecycle"
    EVENT = "event"
    VERIFY = "verify"


class NativeBundleRequest(ContractModel):
    request_id: Annotated[str, Field(min_length=1, max_length=128)]
    operation_id: Annotated[str, Field(min_length=1, max_length=128)]
    operation: NativeBundleOperation
    document_id: Annotated[int, Field(ge=0)] = 0
    document_path: Annotated[str, Field(max_length=32_767)] = ""
    expected_signature: Annotated[str, Field(max_length=4_096)] = ""
    arguments: dict[str, Annotated[str, Field(max_length=32_767)]] = Field(
        default_factory=dict,
        max_length=64,
    )
    payload: Annotated[str, Field(max_length=8 * 1024 * 1024)] = ""


class NativeBundleReceipt(ContractModel):
    bundle_version: Literal["HLB1"] = "HLB1"
    protocol_version: Literal[14]
    request_id: str
    operation_id: str
    operation: NativeBundleOperation
    status: NativeBundleStatus
    stage: NativeBundleStage
    elapsed_microseconds: Annotated[int, Field(ge=0)]
    process_id: Annotated[int, Field(ge=0)]
    window_handle: Annotated[int, Field(ge=0)]
    document_id: Annotated[int, Field(ge=0)]
    document_path: str
    route_generation: Annotated[int, Field(ge=0)]
    before_signature: str
    after_signature: str
    commands_requested: Annotated[int, Field(ge=0)]
    commands_completed: Annotated[int, Field(ge=0)]
    failed_command_index: int | None
    partial_mutation: bool
    retry_safe: bool
    reconcile_required: bool
    rollback_attempted: bool
    rollback_succeeded: bool | None
    undo_available: bool
    result_wire_format: str
    result_payload: str
    artifact_path: str
    artifact_sha256: str
    event_sequence_before: Annotated[int, Field(ge=0)]
    event_sequence_after: Annotated[int, Field(ge=0)]
    capabilities_revision: str
    error_code: str
    error_message: str
    native_hresult: int


def _encode(value: str) -> str:
    return b64encode(value.encode("utf-8")).decode("ascii")


def _decode(value: str, *, field: str) -> str:
    try:
        return b64decode(value, validate=True).decode("utf-8")
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError(f"HLB1_INVALID_BASE64:{field}") from error


def encode_bundle_request(request: NativeBundleRequest) -> str:
    lines = [
        "HLB1",
        f"REQUEST\t{_encode(request.request_id)}",
        f"OPERATION_ID\t{_encode(request.operation_id)}",
        f"OP\t{request.operation.value}",
        f"DOC\t{request.document_id}\t{_encode(request.document_path)}",
        f"EXPECTED\t{_encode(request.expected_signature)}",
    ]
    lines.extend(
        f"ARG\t{_encode(key)}\t{_encode(value)}"
        for key, value in sorted(request.arguments.items())
    )
    lines.extend((f"PAYLOAD\t{_encode(request.payload)}", "END"))
    return "\n".join(lines)


def _records(raw: str) -> dict[str, tuple[str, ...]]:
    lines = raw.splitlines()
    if not lines or lines[0] != "HLB1" or lines[-1] != "END":
        raise ValueError("HLB1_INVALID_ENVELOPE")
    records: dict[str, tuple[str, ...]] = {}
    for line in lines[1:-1]:
        fields = tuple(line.split("\t"))
        if len(fields) < 2 or fields[0] in records:
            raise ValueError("HLB1_INVALID_RECORD")
        records[fields[0]] = fields[1:]
    return records


def _required(
    records: dict[str, tuple[str, ...]],
    name: str,
    count: int,
) -> tuple[str, ...]:
    fields = records.get(name)
    if fields is None or len(fields) != count:
        raise ValueError(f"HLB1_INVALID_{name}")
    return fields


def _boolean(value: str, *, field: str) -> bool:
    if value not in {"0", "1"}:
        raise ValueError(f"HLB1_INVALID_BOOLEAN:{field}")
    return value == "1"


def decode_bundle_receipt(raw: str) -> NativeBundleReceipt:
    records = _records(raw)
    meta = _required(records, "META", 7)
    route = _required(records, "ROUTE", 5)
    signatures = _required(records, "SIGNATURE", 2)
    mutation = _required(records, "MUTATION", 9)
    result = _required(records, "RESULT", 2)
    artifact = _required(records, "ARTIFACT", 2)
    events = _required(records, "EVENTS", 2)
    capabilities = _required(records, "CAPABILITIES", 1)
    error = _required(records, "ERROR", 3)
    failed_index = int(mutation[2])
    rollback_succeeded = int(mutation[7])
    protocol_version = int(meta[0])
    if protocol_version != 14:
        raise ValueError("HLB1_UNSUPPORTED_PROTOCOL")
    return NativeBundleReceipt(
        protocol_version=14,
        request_id=_decode(meta[1], field="request_id"),
        operation_id=_decode(meta[2], field="operation_id"),
        operation=NativeBundleOperation(meta[3]),
        status=NativeBundleStatus(meta[4]),
        stage=NativeBundleStage(meta[5]),
        elapsed_microseconds=int(meta[6]),
        process_id=int(route[0]),
        window_handle=int(route[1]),
        document_id=int(route[2]),
        document_path=_decode(route[3], field="document_path"),
        route_generation=int(route[4]),
        before_signature=_decode(signatures[0], field="before_signature"),
        after_signature=_decode(signatures[1], field="after_signature"),
        commands_requested=int(mutation[0]),
        commands_completed=int(mutation[1]),
        failed_command_index=None if failed_index < 0 else failed_index,
        partial_mutation=_boolean(mutation[3], field="partial_mutation"),
        retry_safe=_boolean(mutation[4], field="retry_safe"),
        reconcile_required=_boolean(mutation[5], field="reconcile_required"),
        rollback_attempted=_boolean(mutation[6], field="rollback_attempted"),
        rollback_succeeded=(
            None if rollback_succeeded < 0 else rollback_succeeded == 1
        ),
        undo_available=_boolean(mutation[8], field="undo_available"),
        result_wire_format=_decode(result[0], field="result_wire_format"),
        result_payload=_decode(result[1], field="result_payload"),
        artifact_path=_decode(artifact[0], field="artifact_path"),
        artifact_sha256=_decode(artifact[1], field="artifact_sha256"),
        event_sequence_before=int(events[0]),
        event_sequence_after=int(events[1]),
        capabilities_revision=_decode(
            capabilities[0],
            field="capabilities_revision",
        ),
        error_code=_decode(error[0], field="error_code"),
        error_message=_decode(error[1], field="error_message"),
        native_hresult=int(error[2], 0),
    )


__all__ = [
    "NativeBundleOperation",
    "NativeBundleReceipt",
    "NativeBundleRequest",
    "NativeBundleStage",
    "NativeBundleStatus",
    "decode_bundle_receipt",
    "encode_bundle_request",
]
