from __future__ import annotations

import hashlib
import json

from hwp_live_structure_contract import (
    ControlKind,
    DocumentStructure,
    StructurePosition,
)


def structure_position(raw: tuple[int, int, int]) -> StructurePosition:
    return StructurePosition(
        list_id=raw[0],
        paragraph=raw[1],
        character=raw[2],
    )


def control_ref(
    document_id: int,
    ctrl_id: str,
    instance_id: str,
) -> str:
    raw = f"{document_id}|{ctrl_id}|{instance_id}"
    return "ctrl:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def table_ref(control_reference: str) -> str:
    digest = hashlib.sha256(control_reference.encode("ascii")).hexdigest()[:32]
    return "table:" + digest


def control_kind(ctrl_id: str, description: str) -> ControlKind:
    if ctrl_id == "tbl":
        return "table"
    if ctrl_id == "gso" and "그림" in description:
        return "picture"
    if ctrl_id == "gso":
        return "shape"
    return "unknown"


def structure_token(structure: DocumentStructure) -> str:
    payload = structure.model_dump(mode="json", exclude={"state_token"})
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def is_matching_structure_snapshot(
    snapshot: DocumentStructure,
    selector: str,
    document_id: int,
    full_name: str,
    window_handle: int,
    state_token: str,
) -> bool:
    return snapshot.state_token == state_token and (
        snapshot.selector,
        snapshot.document_id,
        snapshot.full_name,
        snapshot.window_handle,
    ) == (selector, document_id, full_name, window_handle)
