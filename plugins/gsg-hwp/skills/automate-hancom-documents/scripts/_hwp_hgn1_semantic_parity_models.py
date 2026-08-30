"""Typed result boundary for HGN1 semantic parity classification."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict


class _StrictModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class ArtifactIntegrity(_StrictModel):
    path: str
    bytes: int
    sha256: str
    frame_count: int
    frame_integrity: Literal["authenticated"]
    record_integrity: Literal["authenticated"]
    index_integrity: Literal["authenticated", "not_supplied"]


class Inventory(_StrictModel):
    count: int
    sha256: str


class SemanticInventories(_StrictModel):
    record_kind_counts: dict[str, int]
    stable_nodes: Inventory
    properties: Inventory
    edges: Inventory
    controls: Inventory
    tables: Inventory
    owner_address_mode_matrix: Inventory
    caption_table_topology: Inventory
    blob_references: Inventory


class FirstDifference(_StrictModel):
    path: str
    left: str
    right: str
    reason: Literal[
        "SEMANTIC_VALUE_MISMATCH",
        "NODE_ID_REFERENCE_MISMATCH",
        "NODE_ID_REFERENCE_DOMAIN_MISMATCH",
        "NODE_ID_ALPHA_LEFT_CONFLICT",
        "NODE_ID_ALPHA_RIGHT_CONFLICT",
    ] = "SEMANTIC_VALUE_MISMATCH"


class NodeIdAlphaBijection(_StrictModel):
    count: int
    sha256: str
    first_mismatch: FirstDifference | None


class ParityReport(_StrictModel):
    schema_name: Literal["HGN1-SEMANTIC-PARITY-1"] = "HGN1-SEMANTIC-PARITY-1"
    left: ArtifactIntegrity
    right: ArtifactIntegrity
    left_inventory: SemanticInventories
    right_inventory: SemanticInventories
    raw_digests_equal: bool
    normalized_stream_digest_left: str
    normalized_stream_digest_right: str
    semantic_digest_left: str
    semantic_digest_right: str
    normalized_fields: tuple[str, ...]
    normalization_sources: tuple[str, ...]
    node_id_alpha_bijection: NodeIdAlphaBijection
    first_difference: FirstDifference | None
    equivalent: bool
    reason: Literal["SEMANTIC_EQUIVALENT", "SEMANTIC_DELTA"]

    def to_json(self) -> str:
        return self.model_dump_json(indent=2) + "\n"


class ClassificationError(Exception):
    code: str
    path: Path
    detail: str

    def __init__(self, code: str, path: Path, detail: str) -> None:
        self.code, self.path, self.detail = code, path, detail
        super().__init__(f"{code}:{path}:{detail}")
