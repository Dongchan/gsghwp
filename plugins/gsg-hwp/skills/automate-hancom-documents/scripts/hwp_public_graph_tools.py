from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from threading import Condition, Lock, RLock
from types import MappingProxyType
from typing import Annotated, ClassVar, Literal, Protocol, cast, final

from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter
from pydantic_core import to_json

from hwp_live_preview_lock import PreviewFileLock
from hwp_live_values import ContractModel
from hwp_native_graph_cache import NativeGraphSnapshot
from hwp_native_graph_models import (
    NativeAssetChunkRecord,
    NativeGraphQuery,
    NativeGraphUnavailable,
    NativeNodeRecord,
    NativePropertyRecord,
    NativeSpooledBytes,
    RecordKind,
    TypedNativeRecord,
    UnavailableReason,
)
from hwp_native_graph_patch import (
    PatchDocument,
    canonicalize_patch,
    decode_patch,
    encode_patch,
    invert_patch,
    patch_digest,
    reassemble_sealed_patch_chunks,
)
from hwp_native_graph_patch_executor import (
    ApplyState,
    ApplyStep,
    GraphWorld,
    Injector,
    PatchApplyError,
    apply_validated_patch,
)
from hwp_native_graph_protocol import GraphProtocolError, decode_frame
from _hwp_native_graph_wire import GraphVersion


_MAX_COMPACT_BYTES = 32 * 1024
_NODE_ID = re.compile(r"^[0-9a-f]{32}$")
_CONTINUATION_ADAPTER: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(
    dict[str, JsonValue]
)
_ARTIFACT_URI = re.compile(
    r"^hwp-graph://artifact/([0-9a-f]{64})/([0-9a-f]{64})/([0-9a-f]{64})$"
)


class PublicGraphClient(Protocol):
    def query(self, request: NativeGraphQuery) -> NativeGraphSnapshot: ...


class PublicGraphPatchHost(Protocol):
    def world(self) -> GraphWorld: ...
    def commit(self, world: GraphWorld) -> None: ...


class _PublicModel(ContractModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )


class GraphError(_PublicModel):
    status: Literal["error"] = "error"
    code: Literal[
        "GRAPH_UNAVAILABLE",
        "WRONG_DOCUMENT",
        "STALE_VERSION",
        "INVALID_NODE_ID",
        "NOT_EXPOSED",
        "UNAVAILABLE",
        "INVALID_CONTINUATION",
        "ARTIFACT_URI_INVALID",
        "ARTIFACT_NOT_FOUND",
        "ARTIFACT_DIGEST_MISMATCH",
        "ARTIFACT_RANGE_INVALID",
        "ARTIFACT_UNAUTHORIZED",
        "ARTIFACT_STALE",
        "GRAPH_PROTOCOL_ERROR",
        "PATCH_UNAVAILABLE",
        "PATCH_PARTIAL",
        "PATCH_UNSUPPORTED_PROPERTY",
        "PATCH_CONFLICT",
        "APPLY_NOT_VALIDATED",
        "APPLY_STALE",
        "APPLY_EXECUTE",
        "APPLY_LAYOUT",
        "APPLY_READBACK",
        "APPLY_PUBLISH",
        "APPLY_HISTORY",
        "APPLY_RECONCILE_REQUIRED",
    ]
    message: str
    reason: str | None = None
    hresult: int | None = None


class GraphArtifactRef(_PublicModel):
    uri: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)


class GraphRecordReceipt(_PublicModel):
    record_id: int = Field(ge=0)
    kind: str
    node_id: str | None = None
    owner_node_id: str | None = None
    property_key: int | None = None
    artifact_uri: str
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_bytes: int = Field(ge=0)


class GraphManifestResponse(_PublicModel):
    status: Literal["ok"] = "ok"
    complete: Literal[True] = True
    document_id: str
    graph_id: str
    version: str
    generation: str
    profile_bits: int
    semantic_revision: int
    layout_revision: int
    locator_epoch: int
    semantic_certified: bool
    layout_present: bool
    frame_count: int
    record_count: int
    source_byte_count: int
    source_sha256: str
    frame_index_uri: str
    frame_index_sha256: str
    frame_index_bytes: int


class GraphQueryResponse(_PublicModel):
    status: Literal["ok"] = "ok"
    complete: bool
    document_id: str
    graph_id: str
    version: str
    generation: str
    items: tuple[GraphRecordReceipt, ...]
    continuation: str | None
    total_records: int


class GraphItemResponse(_PublicModel):
    status: Literal["ok"] = "ok"
    document_id: str
    graph_id: str
    version: str
    generation: str
    record_id: int
    kind: str
    node_id: str | None = None
    property_key: int | None = None
    artifact_uri: str
    artifact_sha256: str
    artifact_bytes: int


class GraphArtifactResponse(_PublicModel):
    status: Literal["ok"] = "ok"
    uri: str
    sha256: str
    total_bytes: int
    offset: int
    bytes: int
    content_base64: str
    complete: bool
    continuation_offset: int | None


class GraphResponse(_PublicModel):
    """Single transport envelope so typed failures stay structured in FastMCP."""

    status: Literal["ok", "error"]
    code: str | None = None
    message: str | None = None
    reason: str | None = None
    hresult: int | None = None
    complete: bool | None = None
    document_id: str | None = None
    graph_id: str | None = None
    version: str | None = None
    generation: str | None = None
    profile_bits: int | None = None
    semantic_revision: int | None = None
    layout_revision: int | None = None
    locator_epoch: int | None = None
    semantic_certified: bool | None = None
    layout_present: bool | None = None
    frame_count: int | None = None
    record_count: int | None = None
    source_byte_count: int | None = None
    source_sha256: str | None = None
    frame_index_uri: str | None = None
    frame_index_sha256: str | None = None
    frame_index_bytes: int | None = None
    items: list[GraphRecordReceipt] | None = None
    continuation: str | None = None
    total_records: int | None = None
    record_id: int | None = None
    kind: str | None = None
    node_id: str | None = None
    property_key: int | None = None
    artifact_uri: str | None = None
    artifact_sha256: str | None = None
    artifact_bytes: int | None = None
    uri: str | None = None
    sha256: str | None = None
    total_bytes: int | None = None
    offset: int | None = None
    bytes: int | None = None
    content_base64: str | None = None
    continuation_offset: int | None = None


class GraphPatchResponse(_PublicModel):
    status: Literal["ok", "error"]
    code: str | None = None
    message: str | None = None
    reason: str | None = None
    hresult: int | None = None
    complete: bool | None = None
    document_id: str | None = None
    graph_id: str | None = None
    version: str | None = None
    generation: str | None = None
    semantic_revision: int | None = None
    state: str | None = None
    upload_id: str | None = None
    operation_id: str | None = None
    idempotent: bool | None = None
    validated: bool | None = None
    operation_count: int | None = None
    hancom_mutations: int | None = None
    forward_digest: str | None = None
    inverse_digest: str | None = None
    history_count: int | None = None
    artifact_uri: str | None = None
    artifact_sha256: str | None = None
    artifact_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class _ArtifactOwner:
    document_id: str
    session_id: str
    store_id: str
    lineage: str
    generation: str
    version: str


class _ArtifactMetadata(_PublicModel):
    schema_name: Literal["HWP-GRAPH-ARTIFACT-V1"] = Field(
        default="HWP-GRAPH-ARTIFACT-V1", alias="schema"
    )
    scope: str = Field(pattern=r"^[0-9a-f]{64}$")
    token: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_id: str
    session_id: str
    store_id: str
    lineage: str = Field(pattern=r"^[0-9a-f]{64}$")
    generation: str
    version: str = Field(pattern=r"^[0-9a-f]{64}$")
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)
    media_type: str
    kind: str
    mac: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class _PlannedArtifact:
    content: bytes
    metadata: _ArtifactMetadata
    ref: GraphArtifactRef


class _ArtifactStoreState(IntEnum):
    CLOSED = 0
    ACTIVE = 1
    CLOSING = 2


@dataclass(frozen=True, slots=True)
class _ArtifactRegistry:
    paths: Mapping[str, Path]
    active: Mapping[str, tuple[str, str]]
    lineage_tokens: Mapping[str, frozenset[str]]
    retired_tokens: frozenset[str]


@dataclass(frozen=True, slots=True)
class GraphArtifactPublicationTestState:
    identity: int
    paths: tuple[Path, ...]
    active: tuple[tuple[str, tuple[str, str]], ...]
    lineages: tuple[tuple[str, frozenset[str]], ...]
    retired: frozenset[str]
    leases: tuple[tuple[Path, int], ...]


@final
class _ArtifactBatch:
    __slots__ = ("_commit", "_entries", "_plan", "_response_ready")

    def __init__(
        self,
        plan: Callable[[bytes, str, str], _PlannedArtifact],
        response_ready: Callable[[], None],
        commit: Callable[[tuple[_PlannedArtifact, ...]], None],
    ) -> None:
        self._plan = plan
        self._response_ready = response_ready
        self._commit = commit
        self._entries: dict[str, _PlannedArtifact] = {}

    def put(self, content: bytes, *, media_type: str, kind: str) -> GraphArtifactRef:
        entry = self._plan(content, media_type, kind)
        _ = self._entries.setdefault(entry.metadata.token, entry)
        return entry.ref

    def response_ready(self) -> None:
        self._response_ready()

    def checkpoint(self) -> frozenset[str]:
        return frozenset(self._entries)

    def restore(self, checkpoint: frozenset[str]) -> None:
        self._entries = {
            token: entry
            for token, entry in self._entries.items()
            if token in checkpoint
        }

    def commit(self) -> None:
        self._commit(tuple(self._entries.values()))


@final
class GraphArtifactStore:
    __slots__ = (
        "_gate",
        "_lease",
        "_pending_retirement",
        "_publication_gate",
        "_reader_condition",
        "_reader_leases",
        "_refs",
        "_registry",
        "_root",
        "_scope",
        "_scope_root",
        "_secret",
        "_state",
    )

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._gate = RLock()
        self._reader_condition = Condition(self._gate)
        self._publication_gate = Lock()
        self._state = _ArtifactStoreState.CLOSED
        self._scope = ""
        self._scope_root: Path | None = None
        self._secret = b""
        self._lease: PreviewFileLock | None = None
        self._refs = 0
        self._registry = self._registry_state({}, {}, {}, frozenset())
        self._pending_retirement: set[Path] = set()
        self._reader_leases: dict[Path, int] = {}

    @staticmethod
    def _registry_state(
        paths: Mapping[str, Path],
        active: Mapping[str, tuple[str, str]],
        lineage_tokens: Mapping[str, frozenset[str] | set[str]],
        retired_tokens: frozenset[str],
    ) -> _ArtifactRegistry:
        return _ArtifactRegistry(
            paths=MappingProxyType(dict(paths)),
            active=MappingProxyType(dict(active)),
            lineage_tokens=MappingProxyType(
                {key: frozenset(tokens) for key, tokens in lineage_tokens.items()}
            ),
            retired_tokens=retired_tokens,
        )

    def start(self) -> None:
        with self._publication_gate, self._gate:
            self._start_locked()

    def _start_locked(self) -> None:
        if self._state is _ArtifactStoreState.CLOSING:
            raise GraphProtocolError("ARTIFACT_STORE_CLOSED")
        if self._refs:
            self._refs += 1
            return
        self._root.mkdir(parents=True, exist_ok=True)
        for candidate in tuple(self._root.iterdir()):
            if (
                not candidate.is_dir()
                or re.fullmatch(r"[0-9a-f]{64}", candidate.name) is None
            ):
                continue
            marker = candidate / "owner.json"
            try:
                if json.loads(marker.read_text(encoding="utf-8")) != {
                    "schema": "HWP-GRAPH-ARTIFACT-SCOPE-V1"
                }:
                    continue
            except (OSError, ValueError):
                continue
            recovered = PreviewFileLock.try_acquire(candidate / ".lease")
            if recovered is None:
                continue
            recovered.close()
            shutil.rmtree(candidate, ignore_errors=True)
        self._scope = os.urandom(32).hex()
        self._secret = os.urandom(32)
        self._scope_root = self._root / self._scope
        self._scope_root.mkdir()
        self._lease = PreviewFileLock.acquire(self._scope_root / ".lease")
        marker = self._scope_root / "owner.json"
        _ = marker.write_text(
            '{"schema":"HWP-GRAPH-ARTIFACT-SCOPE-V1"}', encoding="utf-8"
        )
        self._state = _ArtifactStoreState.ACTIVE
        self._refs = 1

    def close(self) -> None:
        with self._publication_gate, self._gate:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._state is _ArtifactStoreState.CLOSED:
            return
        if self._state is _ArtifactStoreState.ACTIVE:
            self._refs -= 1
            if self._refs:
                return
            self._state = _ArtifactStoreState.CLOSING
        while self._reader_leases:
            self._publication_boundary("shutdown-waiting-for-reader-leases")
            _ = self._reader_condition.wait()
        scope_root = self._scope_root
        lease = self._lease
        if lease is not None:
            lease.close()
            self._lease = None
        if scope_root is not None:
            shutil.rmtree(scope_root, ignore_errors=False)
        self._registry = self._registry_state({}, {}, {}, frozenset())
        self._pending_retirement.clear()
        self._reader_leases.clear()
        self._scope = ""
        self._scope_root = None
        self._secret = b""
        self._state = _ArtifactStoreState.CLOSED
        try:
            self._root.rmdir()
        except OSError:
            pass

    def _require_active(self) -> Path:
        if self._state is not _ArtifactStoreState.ACTIVE or self._scope_root is None:
            raise GraphProtocolError("ARTIFACT_STORE_CLOSED")
        return self._scope_root

    @staticmethod
    def _claims(
        owner: _ArtifactOwner,
        digest: str,
        byte_count: int,
        media_type: str,
        kind: str,
    ) -> bytes:
        return json.dumps(
            {
                "document_id": owner.document_id,
                "session_id": owner.session_id,
                "store_id": owner.store_id,
                "lineage": owner.lineage,
                "generation": owner.generation,
                "version": owner.version,
                "digest": digest,
                "bytes": byte_count,
                "media_type": media_type,
                "kind": kind,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @staticmethod
    def _metadata_mac_for(secret: bytes, metadata: _ArtifactMetadata) -> str:
        values = metadata.model_dump(mode="json", by_alias=True, exclude={"mac"})
        encoded = json.dumps(values, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return hmac.new(secret, encoded, hashlib.sha256).hexdigest()

    def _metadata_mac(self, metadata: _ArtifactMetadata) -> str:
        return self._metadata_mac_for(self._secret, metadata)

    def begin_batch(self, owner: _ArtifactOwner) -> _ArtifactBatch:
        with self._gate:
            _ = self._require_active()

        def plan(content: bytes, media_type: str, kind: str) -> _PlannedArtifact:
            return self._plan_artifact(
                content, owner=owner, media_type=media_type, kind=kind
            )

        def commit(entries: tuple[_PlannedArtifact, ...]) -> None:
            self._commit_batch(owner, entries)

        return _ArtifactBatch(plan, self._response_ready, commit)

    def _plan_artifact(
        self,
        content: bytes,
        *,
        owner: _ArtifactOwner,
        media_type: str,
        kind: str,
    ) -> _PlannedArtifact:
        with self._gate:
            _ = self._require_active()
            digest = hashlib.sha256(content).hexdigest()
            claims = self._claims(owner, digest, len(content), media_type, kind)
            token = hmac.new(self._secret, claims, hashlib.sha256).hexdigest()
            metadata = _ArtifactMetadata(
                scope=self._scope,
                token=token,
                document_id=owner.document_id,
                session_id=owner.session_id,
                store_id=owner.store_id,
                lineage=owner.lineage,
                generation=owner.generation,
                version=owner.version,
                digest=digest,
                bytes=len(content),
                media_type=media_type,
                kind=kind,
                mac="0" * 64,
            )
            metadata = metadata.model_copy(update={"mac": self._metadata_mac(metadata)})
            ref = GraphArtifactRef(
                uri=f"hwp-graph://artifact/{self._scope}/{token}/{digest}",
                sha256=digest,
                bytes=len(content),
            )
            return _PlannedArtifact(content=content, metadata=metadata, ref=ref)

    def publication_boundary_for_testing(self, name: str) -> None:
        """Narrow deterministic checkpoint seam; production leaves it unobserved."""
        del name

    def publication_state_for_testing(self) -> GraphArtifactPublicationTestState:
        """Return an immutable ownership snapshot without exposing mutable internals."""
        with self._gate:
            registry = self._registry
            return GraphArtifactPublicationTestState(
                identity=id(registry),
                paths=tuple(registry.paths.values()),
                active=tuple(sorted(registry.active.items())),
                lineages=tuple(sorted(registry.lineage_tokens.items())),
                retired=registry.retired_tokens,
                leases=tuple(sorted(self._reader_leases.items())),
            )

    def _publication_boundary(self, name: str) -> None:
        self.publication_boundary_for_testing(name)

    def _response_ready(self) -> None:
        with self._gate:
            _ = self._require_active()
            self._publication_boundary("response-uri-serialized")

    def _drain_retirement(self) -> None:
        for path in tuple(self._pending_retirement):
            try:
                with self._gate:
                    reader_active = bool(self._reader_leases.get(path, 0))
                if reader_active:
                    self._publication_boundary("retirement-blocked-by-reader-lease")
                    continue
                self._publication_boundary("prior-generation-cleanup")
                shutil.rmtree(path, ignore_errors=False)
                self._pending_retirement.remove(path)
            except Exception:
                continue

    def _commit_batch(
        self, owner: _ArtifactOwner, entries: tuple[_PlannedArtifact, ...]
    ) -> None:
        with self._publication_gate:
            with self._gate:
                scope_root = self._require_active()
                registry = self._registry
            self._drain_retirement()
            if not entries:
                return
            expected_owner = (owner.generation, owner.version)
            if registry.active.get(owner.lineage) == expected_owner and all(
                entry.metadata.token in registry.paths for entry in entries
            ):
                return
            batch_key = hashlib.sha256(
                b"\0".join(
                    bytes.fromhex(entry.metadata.token)
                    for entry in sorted(entries, key=lambda item: item.metadata.token)
                )
            ).hexdigest()
            candidates = scope_root / ".candidates"
            generations = scope_root / "generations"
            candidates.mkdir(exist_ok=True)
            generations.mkdir(exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=f".{batch_key}.", dir=candidates))
            final = generations / batch_key
            filesystem_published = False
            registry_swapped = False
            retirement_paths: set[Path] = set()
            try:
                self._publication_boundary("candidate-batch-created")
                for ordinal, entry in enumerate(entries):
                    artifact_dir = staging / entry.metadata.token
                    artifact_dir.mkdir()
                    with (artifact_dir / "content.bin").open("wb") as output:
                        _ = output.write(entry.content)
                        output.flush()
                        os.fsync(output.fileno())
                    self._publication_boundary(
                        f"artifact:{ordinal}:{entry.metadata.kind}:content"
                    )
                    with (artifact_dir / "metadata.json").open("wb") as output:
                        _ = output.write(
                            entry.metadata.model_dump_json(by_alias=True).encode(
                                "utf-8"
                            )
                        )
                        output.flush()
                        os.fsync(output.fileno())
                    self._publication_boundary(
                        f"artifact:{ordinal}:{entry.metadata.kind}:metadata"
                    )
                for entry in entries:
                    artifact_dir = staging / entry.metadata.token
                    staged_metadata = _ArtifactMetadata.model_validate_json(
                        (artifact_dir / "metadata.json").read_bytes(), strict=True
                    )
                    staged_content = (artifact_dir / "content.bin").read_bytes()
                    if (
                        staged_metadata != entry.metadata
                        or not hmac.compare_digest(
                            staged_metadata.mac, self._metadata_mac(staged_metadata)
                        )
                        or len(staged_content) != staged_metadata.bytes
                        or not hmac.compare_digest(
                            hashlib.sha256(staged_content).hexdigest(),
                            staged_metadata.digest,
                        )
                    ):
                        raise GraphProtocolError("ARTIFACT_STAGING_INVALID")
                self._publication_boundary("staged-batch-validated")

                old_tokens = registry.lineage_tokens.get(owner.lineage, frozenset())
                replacing = registry.active.get(owner.lineage) not in (
                    None,
                    expected_owner,
                )
                new_paths = dict(registry.paths)
                new_lineage_tokens = {
                    key: set(tokens) for key, tokens in registry.lineage_tokens.items()
                }
                new_retired_tokens = set(registry.retired_tokens)
                if replacing:
                    for token in old_tokens:
                        old_path = new_paths.pop(token, None)
                        new_retired_tokens.add(token)
                        if old_path is not None:
                            retirement_paths.add(old_path.parent)
                    new_lineage_tokens[owner.lineage] = set()
                tokens = new_lineage_tokens.setdefault(owner.lineage, set())
                for entry in entries:
                    token = entry.metadata.token
                    new_paths[token] = final / token
                    tokens.add(token)
                new_active = dict(registry.active)
                new_active[owner.lineage] = expected_owner
                next_registry = self._registry_state(
                    new_paths,
                    new_active,
                    new_lineage_tokens,
                    frozenset(new_retired_tokens),
                )
                self._publication_boundary("registry-paths-ready")
                self._publication_boundary("registry-lineage-ready")
                self._publication_boundary("registry-active-ready")
                self._publication_boundary("reader-before-filesystem-rename")
                os.replace(staging, final)
                filesystem_published = True
                self._publication_boundary("filesystem-generation-published")
                self._publication_boundary("reader-after-filesystem-rename")
                with self._gate:
                    if self._registry is not registry:
                        raise GraphProtocolError("ARTIFACT_REGISTRY_CONFLICT")
                    self._registry = next_registry
                    registry_swapped = True
                    self._pending_retirement.update(retirement_paths)
                try:
                    self._publication_boundary("active-generation-swap")
                    self._publication_boundary("reader-after-state-swap")
                    self._publication_boundary("writer-swap-before-retire")
                    if retirement_paths:
                        self._publication_boundary(
                            "prior-generation-retirement-scheduled"
                        )
                        self._publication_boundary(
                            "prior-generation-retirement-handoff"
                        )
                        self._drain_retirement()
                except Exception:
                    return
            finally:

                def clean(
                    action: Callable[[], object],
                    *,
                    missing_or_nonempty_ok: bool = False,
                ) -> None:
                    try:
                        _ = action()
                    except OSError:
                        if not missing_or_nonempty_ok and not registry_swapped:
                            raise
                    except Exception:
                        if not registry_swapped:
                            raise

                clean(lambda: shutil.rmtree(staging, ignore_errors=True))
                if filesystem_published and not registry_swapped:
                    clean(lambda: shutil.rmtree(final, ignore_errors=True))
                clean(candidates.rmdir, missing_or_nonempty_ok=True)
                clean(generations.rmdir, missing_or_nonempty_ok=True)

    def read(
        self,
        uri: str,
        *,
        document_id: str | None = None,
        store_id: str | None = None,
        expected_version: str | None = None,
    ) -> tuple[bytes, str] | GraphResponse:
        match = _ARTIFACT_URI.fullmatch(uri)
        if match is None:
            return _error(
                "ARTIFACT_URI_INVALID", "그래프 artifact URI 형식이 올바르지 않습니다."
            )
        scope, token, digest = match.groups()
        with self._gate:
            if self._state is not _ArtifactStoreState.ACTIVE:
                return _error(
                    "ARTIFACT_UNAUTHORIZED", "artifact 소유자가 종료 중입니다."
                )
            if not hmac.compare_digest(scope, self._scope):
                return _error(
                    "ARTIFACT_UNAUTHORIZED", "이 서버가 소유한 artifact가 아닙니다."
                )
            registry = self._registry
            path = registry.paths.get(token)
            if path is None:
                code = (
                    "ARTIFACT_STALE"
                    if token in registry.retired_tokens
                    else "ARTIFACT_UNAUTHORIZED"
                )
                return _error(
                    code, "artifact 권한 또는 활성 세대 결속이 유효하지 않습니다."
                )
            self._publication_boundary("reader-lookup-before-lease")
            secret = self._secret
            lease_path = path.parent
            self._reader_leases[lease_path] = self._reader_leases.get(lease_path, 0) + 1
        self._publication_boundary("reader-lease-before-read")
        try:
            return self._read_snapshot(
                scope,
                token,
                digest,
                path,
                registry,
                secret,
                document_id=document_id,
                store_id=store_id,
                expected_version=expected_version,
            )
        finally:
            try:
                self._publication_boundary("reader-release-before-cleanup")
            finally:
                with self._gate:
                    remaining = self._reader_leases[lease_path] - 1
                    if remaining:
                        self._reader_leases[lease_path] = remaining
                    else:
                        del self._reader_leases[lease_path]
                        if not self._reader_leases:
                            self._reader_condition.notify_all()

    def _read_snapshot(
        self,
        scope: str,
        token: str,
        digest: str,
        path: Path,
        registry: _ArtifactRegistry,
        secret: bytes,
        *,
        document_id: str | None,
        store_id: str | None,
        expected_version: str | None,
    ) -> tuple[bytes, str] | GraphResponse:
        try:
            metadata = _ArtifactMetadata.model_validate_json(
                (path / "metadata.json").read_bytes(), strict=True
            )
        except (OSError, ValueError):
            return _error(
                "ARTIFACT_DIGEST_MISMATCH", "artifact metadata 검증에 실패했습니다."
            )
        if (
            metadata.scope != scope
            or metadata.token != token
            or not hmac.compare_digest(
                metadata.mac, self._metadata_mac_for(secret, metadata)
            )
        ):
            return _error(
                "ARTIFACT_UNAUTHORIZED", "artifact 소유권 서명 검증에 실패했습니다."
            )
        if not hmac.compare_digest(metadata.digest, digest):
            return _error(
                "ARTIFACT_DIGEST_MISMATCH", "그래프 artifact digest 결속이 다릅니다."
            )
        if registry.active.get(metadata.lineage) != (
            metadata.generation,
            metadata.version,
        ):
            return _error("ARTIFACT_STALE", "artifact 세대가 이미 폐기되었습니다.")
        if document_id is not None and (
            metadata.document_id != document_id
            or metadata.session_id != document_id
            or metadata.store_id != store_id
            or metadata.version != expected_version
        ):
            return _error(
                "ARTIFACT_UNAUTHORIZED",
                "artifact 문서, 저장소 또는 버전 권한이 다릅니다.",
            )
        try:
            content = (path / "content.bin").read_bytes()
        except OSError:
            return _error("ARTIFACT_NOT_FOUND", "요청한 artifact를 찾을 수 없습니다.")
        actual = hashlib.sha256(content).hexdigest()
        if len(content) != metadata.bytes or not hmac.compare_digest(
            actual, metadata.digest
        ):
            return _error(
                "ARTIFACT_DIGEST_MISMATCH",
                "그래프 artifact digest 검증에 실패했습니다.",
            )
        return content, digest

    def continuation(self, payload: Mapping[str, object]) -> str:
        with self._gate:
            return self._continuation_locked(payload)

    def _continuation_locked(self, payload: Mapping[str, object]) -> str:
        _ = self._require_active()
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        signature = hmac.new(self._secret, encoded, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(encoded + signature).decode("ascii").rstrip("=")

    def parse_continuation(self, token: str) -> dict[str, object] | None:
        with self._gate:
            return self._parse_continuation_locked(token)

    def _parse_continuation_locked(self, token: str) -> dict[str, object] | None:
        try:
            _ = self._require_active()
            padded = token + "=" * (-len(token) % 4)
            raw = base64.urlsafe_b64decode(padded.encode("ascii"))
            encoded, signature = raw[:-32], raw[-32:]
            expected = hmac.new(self._secret, encoded, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                return None
            value = _CONTINUATION_ADAPTER.validate_json(encoded)
        except (GraphProtocolError, ValueError, UnicodeError):
            return None
        return cast(dict[str, object], value)


def graph_call_tool_result(tool_name: str, payload: BaseModel) -> CallToolResult:
    """Build the exact public graph MCP envelope used on the wire."""
    structured = payload.model_dump(mode="json", by_alias=True, exclude_none=True)
    if tool_name == "hwp_query_graph" and structured.get("status") == "ok":
        _ = structured.setdefault("continuation", None)
    if tool_name == "hwp_fetch_graph_artifact" and structured.get("status") == "ok":
        _ = structured.setdefault("continuation_offset", None)
    return CallToolResult(
        content=[TextContent(type="text", text=f"{tool_name} structured result")],
        structuredContent=structured,
    )


def _serialized_graph_result_bytes(tool_name: str, payload: BaseModel) -> bytes:
    return to_json(graph_call_tool_result(tool_name, payload))


def _error(
    code: str,
    message: str,
    *,
    reason: str | None = None,
    hresult: int | None = None,
) -> GraphResponse:
    return GraphResponse(
        status="error",
        code=code,
        message=message,
        reason=reason,
        hresult=hresult,
    )


def _patch_error(
    code: str,
    message: str,
    *,
    reason: str | None = None,
) -> GraphPatchResponse:
    return GraphPatchResponse(
        status="error",
        code=code,
        message=message,
        reason=reason,
    )


def _query_digest(operation: str, values: Mapping[str, object]) -> str:
    body = json.dumps(
        {"operation": operation, **values},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _native_query(
    *,
    operation: str,
    document_id: str,
    store_id: str,
    projection_bits: int,
    expected_version: str,
    values: Mapping[str, object],
) -> NativeGraphQuery:
    return NativeGraphQuery(
        store_id=store_id,
        projection_bits=projection_bits,
        query_digest=_query_digest(operation, values),
        session_id=document_id,
        expected_version_digest=expected_version,
    )


def _artifact_owner(
    *,
    document_id: str,
    store_id: str,
    projection_bits: int,
    query_digest: str,
    generation: str,
    version: str,
) -> _ArtifactOwner:
    lineage = hashlib.sha256(
        json.dumps(
            {
                "document_id": document_id,
                "store_id": store_id,
                "projection_bits": projection_bits,
                "query_digest": query_digest,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return _ArtifactOwner(
        document_id=document_id,
        session_id=document_id,
        store_id=store_id,
        lineage=lineage,
        generation=generation,
        version=version,
    )


def _read_spooled(value: NativeSpooledBytes) -> bytes:
    return b"".join(value.chunks())


def _json_value(
    value: object,
    store: _ArtifactBatch,
    *,
    owner: _ArtifactOwner,
) -> object:
    if isinstance(value, NativeSpooledBytes):
        return store.put(
            _read_spooled(value),
            media_type="application/octet-stream",
            kind="record-field",
        ).model_dump(mode="json")
    if isinstance(value, bytes):
        return store.put(
            value,
            media_type="application/octet-stream",
            kind="record-field",
        ).model_dump(mode="json")
    if isinstance(value, IntEnum):
        return {"value": int(value), "name": value.name}
    if isinstance(value, BaseModel):
        return {
            name: _json_value(
                cast(object, getattr(value, name)),
                store,
                owner=owner,
            )
            for name in type(value).model_fields
        }
    if isinstance(value, tuple | list):
        items = cast(tuple[object, ...] | list[object], value)
        return [_json_value(item, store, owner=owner) for item in items]
    if isinstance(value, Path):
        raise GraphProtocolError("PUBLIC_PATH_EXPOSURE")
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise GraphProtocolError("PUBLIC_VALUE_TYPE", type(value).__name__)


def _record_receipt(
    record: TypedNativeRecord,
    store: _ArtifactBatch,
    *,
    owner: _ArtifactOwner,
) -> GraphRecordReceipt:
    encoded = json.dumps(
        _json_value(record, store, owner=owner),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    artifact = store.put(
        encoded,
        media_type="application/json",
        kind=f"record-{RecordKind(record.kind).name.lower()}",
    )
    return GraphRecordReceipt(
        record_id=record.record_id,
        kind=RecordKind(record.kind).name,
        node_id=record.node_id.hex() if isinstance(record, NativeNodeRecord) else None,
        owner_node_id=(
            record.owner_node_id.hex()
            if isinstance(record, NativePropertyRecord)
            else None
        ),
        property_key=(
            record.property_key if isinstance(record, NativePropertyRecord) else None
        ),
        artifact_uri=artifact.uri,
        artifact_sha256=artifact.sha256,
        artifact_bytes=artifact.bytes,
    )


@final
class HwpPublicGraphTools:
    __slots__ = ("_artifacts", "_client", "_patch_host", "_replays")

    def __init__(
        self,
        client: PublicGraphClient | None,
        artifact_root: Path,
        patch_host: PublicGraphPatchHost | None = None,
    ) -> None:
        self._client = client
        self._artifacts = GraphArtifactStore(artifact_root)
        self._patch_host = patch_host
        self._replays: dict[str, GraphPatchResponse] = {}

    def start(self) -> None:
        self._artifacts.start()

    def close(self) -> None:
        self._artifacts.close()

    def _snapshot(
        self,
        *,
        operation: str,
        document_id: str,
        store_id: str,
        projection_bits: int,
        expected_version: str,
        values: Mapping[str, object],
    ) -> tuple[NativeGraphSnapshot, bytes, str, str, str] | GraphResponse:
        if self._client is None:
            return _error(
                "GRAPH_UNAVAILABLE", "문서 그래프 transport를 사용할 수 없습니다."
            )
        query = _native_query(
            operation=operation,
            document_id=document_id,
            store_id=store_id,
            projection_bits=projection_bits,
            expected_version=expected_version,
            values=values,
        )
        try:
            snapshot = self._client.query(query)
            first_frame = next(snapshot.frame_bytes(), None)
            if first_frame is None:
                snapshot.close()
                return _error(
                    "GRAPH_PROTOCOL_ERROR",
                    "문서 그래프가 빈 프레임 스트림을 반환했습니다.",
                )
            frame = decode_frame(first_frame)
            actual_document = frame.version.session.hex()
            version = frame.version.digest
        except GraphProtocolError as error:
            if error.code == "GRAPH_UNAVAILABLE":
                # 창 핸들을 질의 시점에 푸는 클라이언트가 연결된 문서를 못 찾은
                # 경우다. transport 가 없는 것과 같은 뜻이므로 같은 코드로 답한다
                # -- 프로토콜 위반으로 보고하면 원인을 엉뚱한 데서 찾게 된다.
                return _error("GRAPH_UNAVAILABLE", str(error))
            if error.code == "GRAPH_SESSION_MISMATCH":
                return _error(
                    "WRONG_DOCUMENT", "요청 문서와 그래프 문서 신원이 다릅니다."
                )
            if error.code == "GRAPH_VERSION_MISMATCH" and expected_version:
                return _error(
                    "STALE_VERSION",
                    "요청한 그래프 버전이 현재 불변 버전과 다릅니다.",
                )
            return _error("GRAPH_PROTOCOL_ERROR", f"문서 그래프 검증 실패: {error}")
        if actual_document != document_id:
            snapshot.close()
            return _error("WRONG_DOCUMENT", "요청 문서와 그래프 문서 신원이 다릅니다.")
        if expected_version and expected_version != version:
            snapshot.close()
            return _error(
                "STALE_VERSION", "요청한 그래프 버전이 현재 불변 버전과 다릅니다."
            )
        return (
            snapshot,
            first_frame,
            version,
            frame.version.graph.hex(),
            query.query_digest,
        )

    async def hwp_get_graph_manifest(
        self,
        *,
        document_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")],
        store_id: Annotated[str, Field(min_length=1, max_length=256)],
        projection_bits: Annotated[int, Field(ge=0)] = 0,
        expected_version: Annotated[str, Field(pattern=r"^(?:[0-9a-f]{64})?$")] = "",
    ) -> GraphResponse:
        loaded = self._snapshot(
            operation="manifest",
            document_id=document_id,
            store_id=store_id,
            projection_bits=projection_bits,
            expected_version=expected_version,
            values={},
        )
        if isinstance(loaded, GraphResponse):
            return loaded
        snapshot, first_frame, version, graph_id, query_digest = loaded
        try:
            generation = snapshot.metadata.generation
            owner = _artifact_owner(
                document_id=document_id,
                store_id=store_id,
                projection_bits=projection_bits,
                query_digest=query_digest,
                generation=generation,
                version=version,
            )
            batch = self._artifacts.begin_batch(owner)
            frame_entries: list[dict[str, JsonValue]] = []
            source_digest = hashlib.sha256()
            source_byte_count = 0
            for sequence, frame_bytes in enumerate(snapshot.frame_bytes()):
                source_digest.update(frame_bytes)
                source_byte_count += len(frame_bytes)
                artifact = batch.put(
                    frame_bytes,
                    media_type="application/octet-stream",
                    kind="native-frame",
                )
                frame_entries.append(
                    {"sequence": sequence, **artifact.model_dump(mode="json")}
                )
            index_bytes = json.dumps(
                frame_entries, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            index = batch.put(
                index_bytes,
                media_type="application/json",
                kind="frame-index",
            )
            identity = decode_frame(first_frame).version
            response = GraphResponse(
                status="ok",
                complete=True,
                document_id=document_id,
                graph_id=graph_id,
                version=version,
                generation=generation,
                profile_bits=identity.profile_bits,
                semantic_revision=identity.semantic_revision,
                layout_revision=identity.layout_revision,
                locator_epoch=identity.locator_epoch,
                semantic_certified=identity.semantic_certified,
                layout_present=identity.layout_present,
                frame_count=snapshot.metadata.frame_count,
                record_count=snapshot.metadata.record_count,
                source_byte_count=source_byte_count,
                source_sha256=source_digest.hexdigest(),
                frame_index_uri=index.uri,
                frame_index_sha256=index.sha256,
                frame_index_bytes=index.bytes,
            )
            if (
                len(response.model_dump_json(exclude_none=True).encode("utf-8"))
                > _MAX_COMPACT_BYTES
            ):
                raise GraphProtocolError("PUBLIC_MANIFEST_BUDGET")
            batch.response_ready()
            batch.commit()
            return response
        finally:
            snapshot.close()

    async def hwp_query_graph(
        self,
        *,
        document_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")],
        store_id: Annotated[str, Field(min_length=1, max_length=256)],
        projection_bits: Annotated[int, Field(ge=0)] = 0,
        kinds: tuple[RecordKind, ...] = (),
        page_size: Annotated[int, Field(ge=1, le=100)] = 25,
        continuation: Annotated[str | None, Field(max_length=4096)] = None,
        expected_version: Annotated[str, Field(pattern=r"^(?:[0-9a-f]{64})?$")] = "",
    ) -> GraphResponse:
        values: dict[str, object] = {"kinds": [int(kind) for kind in kinds]}
        offset = 0
        parsed: dict[str, object] | None = None
        if continuation is not None:
            parsed = self._artifacts.parse_continuation(continuation)
            if (
                parsed is None
                or parsed.get("document_id") != document_id
                or parsed.get("store_id") != store_id
                or parsed.get("kinds") != values["kinds"]
                or parsed.get("projection_bits") != projection_bits
                or not isinstance(parsed.get("offset"), int)
                or isinstance(parsed.get("offset"), bool)
            ):
                return _error(
                    "INVALID_CONTINUATION", "그래프 continuation 검증에 실패했습니다."
                )
            offset = cast(int, parsed["offset"])
        loaded = self._snapshot(
            operation="query",
            document_id=document_id,
            store_id=store_id,
            projection_bits=projection_bits,
            expected_version=expected_version,
            values=values,
        )
        if isinstance(loaded, GraphResponse):
            return loaded
        snapshot, _first_frame, version, graph_id, query_digest = loaded
        try:
            generation = snapshot.metadata.generation
            owner = _artifact_owner(
                document_id=document_id,
                store_id=store_id,
                projection_bits=projection_bits,
                query_digest=query_digest,
                generation=generation,
                version=version,
            )
            if parsed is not None and (
                parsed.get("generation") != generation
                or parsed.get("version") != version
            ):
                return _error(
                    "INVALID_CONTINUATION",
                    "그래프 continuation의 불변 세대 또는 버전 결속이 다릅니다.",
                )
            selected_iterator, total_records = snapshot.indexed_records(
                kinds, offset, page_size
            )
            if parsed is not None and (offset < 0 or offset >= total_records):
                return _error(
                    "INVALID_CONTINUATION",
                    "그래프 continuation의 레코드 위치가 유효하지 않습니다.",
                )
            selected = tuple(selected_iterator)
            batch = self._artifacts.begin_batch(owner)
            items: list[GraphRecordReceipt] = []
            checkpoints = [batch.checkpoint()]
            response = (
                GraphResponse(
                    status="ok",
                    complete=True,
                    document_id=document_id,
                    graph_id=graph_id,
                    version=version,
                    generation=generation,
                    items=[],
                    continuation=None,
                    total_records=0,
                )
                if total_records == 0
                else None
            )
            selected_checkpoint = checkpoints[0]
            for record in selected:
                items.append(_record_receipt(record, batch, owner=owner))
                checkpoints.append(batch.checkpoint())
                next_offset = offset + len(items)
                token = None
                if next_offset < total_records:
                    token = self._artifacts.continuation(
                        {
                            "document_id": document_id,
                            "store_id": store_id,
                            "projection_bits": projection_bits,
                            "kinds": values["kinds"],
                            "offset": next_offset,
                            "generation": generation,
                            "version": version,
                        }
                    )
                candidate = GraphResponse(
                    status="ok",
                    complete=token is None,
                    document_id=document_id,
                    graph_id=graph_id,
                    version=version,
                    generation=generation,
                    items=list(items),
                    continuation=token,
                    total_records=total_records,
                )
                if (
                    len(_serialized_graph_result_bytes("hwp_query_graph", candidate))
                    <= _MAX_COMPACT_BYTES
                ):
                    response = candidate
                    selected_checkpoint = checkpoints[-1]
            batch.restore(selected_checkpoint)
            if response is None:
                raise GraphProtocolError("PUBLIC_QUERY_BUDGET")
            batch.response_ready()
            batch.commit()
            return response
        finally:
            snapshot.close()

    async def hwp_get_graph_node(
        self,
        *,
        document_id: str,
        store_id: str,
        node_id: str,
        projection_bits: int = 0,
        expected_version: str = "",
    ) -> GraphResponse:
        if _NODE_ID.fullmatch(node_id) is None:
            return _error("INVALID_NODE_ID", "NodeId 형식이 올바르지 않습니다.")
        return self._find_item(
            operation="node",
            document_id=document_id,
            store_id=store_id,
            projection_bits=projection_bits,
            expected_version=expected_version,
            node_id=node_id,
            property_key=None,
            occurrence=1,
            record_id=None,
        )

    async def hwp_get_graph_property(
        self,
        *,
        document_id: str,
        store_id: str,
        node_id: str,
        property_key: Annotated[int, Field(ge=0, le=0xFFFF_FFFF)],
        occurrence: Annotated[int, Field(ge=1)] = 1,
        projection_bits: int = 0,
        expected_version: str = "",
    ) -> GraphResponse:
        if _NODE_ID.fullmatch(node_id) is None:
            return _error("INVALID_NODE_ID", "NodeId 형식이 올바르지 않습니다.")
        return self._find_item(
            operation="property",
            document_id=document_id,
            store_id=store_id,
            projection_bits=projection_bits,
            expected_version=expected_version,
            node_id=node_id,
            property_key=property_key,
            occurrence=occurrence,
            record_id=None,
        )

    async def hwp_get_graph_asset(
        self,
        *,
        document_id: str,
        store_id: str,
        record_id: Annotated[int, Field(ge=0)],
        projection_bits: int = 0,
        expected_version: str = "",
    ) -> GraphResponse:
        return self._find_item(
            operation="asset",
            document_id=document_id,
            store_id=store_id,
            projection_bits=projection_bits,
            expected_version=expected_version,
            node_id=None,
            property_key=None,
            occurrence=1,
            record_id=record_id,
        )

    def _find_item(
        self,
        *,
        operation: str,
        document_id: str,
        store_id: str,
        projection_bits: int,
        expected_version: str,
        node_id: str | None,
        property_key: int | None,
        occurrence: int,
        record_id: int | None,
    ) -> GraphResponse:
        loaded = self._snapshot(
            operation=operation,
            document_id=document_id,
            store_id=store_id,
            projection_bits=projection_bits,
            expected_version=expected_version,
            values={
                "node_id": node_id,
                "property_key": property_key,
                "occurrence": occurrence,
                "record_id": record_id,
            },
        )
        if isinstance(loaded, GraphResponse):
            return loaded
        snapshot, _first_frame, version, graph_id, query_digest = loaded
        try:
            matched: TypedNativeRecord | None
            if operation == "node" and node_id is not None:
                matched = snapshot.node_record(bytes.fromhex(node_id))
            elif (
                operation == "property"
                and node_id is not None
                and property_key is not None
            ):
                matched = snapshot.property_record(
                    bytes.fromhex(node_id), property_key, occurrence
                )
            elif operation == "asset" and record_id is not None:
                candidate = snapshot.record(record_id)
                matched = (
                    candidate if isinstance(candidate, NativeAssetChunkRecord) else None
                )
            else:
                matched = None
            if matched is None:
                return _error(
                    "NOT_EXPOSED", "요청한 그래프 항목은 노출되지 않았습니다."
                )
            if isinstance(matched, NativePropertyRecord) and isinstance(
                matched.observation, NativeGraphUnavailable
            ):
                reason = UnavailableReason(matched.observation.reason).name
                if matched.observation.reason == UnavailableReason.NOT_EXPOSED:
                    return _error(
                        "NOT_EXPOSED",
                        "요청한 속성은 문서 그래프에 노출되지 않았습니다.",
                        reason=reason,
                        hresult=matched.observation.hresult,
                    )
                return _error(
                    "UNAVAILABLE",
                    "요청한 속성 값을 사용할 수 없습니다.",
                    reason=reason,
                    hresult=matched.observation.hresult,
                )
            generation = snapshot.metadata.generation
            owner = _artifact_owner(
                document_id=document_id,
                store_id=store_id,
                projection_bits=projection_bits,
                query_digest=query_digest,
                generation=generation,
                version=version,
            )
            batch = self._artifacts.begin_batch(owner)
            receipt = _record_receipt(matched, batch, owner=owner)
            response = GraphResponse(
                status="ok",
                document_id=document_id,
                graph_id=graph_id,
                version=version,
                generation=generation,
                record_id=receipt.record_id,
                kind=receipt.kind,
                node_id=receipt.node_id or receipt.owner_node_id,
                property_key=receipt.property_key,
                artifact_uri=receipt.artifact_uri,
                artifact_sha256=receipt.artifact_sha256,
                artifact_bytes=receipt.artifact_bytes,
            )
            batch.response_ready()
            batch.commit()
            return response
        finally:
            snapshot.close()

    def read_artifact_resource(
        self,
        binding: str,
        version: str,
        digest: str,
    ) -> bytes:
        uri = f"hwp-graph://artifact/{binding}/{version}/{digest}"
        loaded = self._artifacts.read(uri)
        if isinstance(loaded, GraphResponse):
            raise ValueError(loaded.code)
        return loaded[0]

    async def hwp_fetch_graph_artifact(
        self,
        *,
        uri: Annotated[str, Field(max_length=1024)],
        document_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")],
        store_id: Annotated[str, Field(min_length=1, max_length=256)],
        expected_version: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")],
        offset: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=1, le=20_000)] = 20_000,
    ) -> GraphResponse:
        loaded = self._artifacts.read(
            uri,
            document_id=document_id,
            store_id=store_id,
            expected_version=expected_version,
        )
        if isinstance(loaded, GraphResponse):
            return loaded
        content, digest = loaded
        if offset > len(content):
            return _error(
                "ARTIFACT_RANGE_INVALID",
                "artifact offset이 전체 바이트 범위를 벗어났습니다.",
            )
        chunk = content[offset : offset + limit]
        next_offset = offset + len(chunk)
        return GraphResponse(
            status="ok",
            uri=uri,
            sha256=digest,
            total_bytes=len(content),
            offset=offset,
            bytes=len(chunk),
            content_base64=base64.b64encode(chunk).decode("ascii"),
            complete=next_offset == len(content),
            continuation_offset=None if next_offset == len(content) else next_offset,
        )

    def _patch_owner(self, document_id: str, version: str) -> _ArtifactOwner:
        digest = hashlib.sha256(f"{document_id}:{version}".encode("utf-8")).hexdigest()
        return _ArtifactOwner(
            document_id=document_id,
            session_id=document_id,
            store_id="graph-patch",
            lineage=digest,
            generation=digest,
            version=version if len(version) == 64 else digest,
        )

    def _publish_bytes(
        self, document_id: str, version: str, payload: bytes, kind: str
    ) -> GraphArtifactRef:
        batch = self._artifacts.begin_batch(self._patch_owner(document_id, version))
        artifact = batch.put(payload, media_type="application/octet-stream", kind=kind)
        batch.response_ready()
        batch.commit()
        return artifact

    def _decode_chunks(
        self, chunks: tuple[tuple[int, int, int, str], ...]
    ) -> PatchDocument | GraphPatchResponse:
        if not chunks:
            return _patch_error("PATCH_PARTIAL", "부분 스트림 패치는 거부합니다.")
        decoded_chunks: list[tuple[int, int, int, bytes]] = []
        for offset, total, more, payload in chunks:
            try:
                decoded_chunks.append(
                    (offset, total, more, base64.b64decode(payload, validate=True))
                )
            except ValueError:
                return _patch_error("PATCH_PARTIAL", "부분 스트림 패치는 거부합니다.")
        try:
            return decode_patch(reassemble_sealed_patch_chunks(tuple(decoded_chunks)))
        except ValueError as error:
            code = str(error)
            if code == "PATCH_OUT_OF_ORDER":
                return _patch_error("PATCH_PARTIAL", "부분 스트림 패치는 거부합니다.")
            if code == "PATCH_PROPERTY_READ_ONLY":
                return _patch_error(
                    "PATCH_UNSUPPORTED_PROPERTY",
                    "쓰기 불가능한 속성은 숨기지 않고 거부합니다.",
                    reason=code,
                )
            return _patch_error("GRAPH_PROTOCOL_ERROR", code)

    def _canonical_or_error(
        self, patch: PatchDocument
    ) -> PatchDocument | GraphPatchResponse:
        try:
            return canonicalize_patch(patch)
        except ValueError as error:
            code = str(error)
            if code == "PATCH_PROPERTY_READ_ONLY":
                return _patch_error(
                    "PATCH_UNSUPPORTED_PROPERTY",
                    "쓰기 불가능한 속성은 숨기지 않고 거부합니다.",
                    reason=code,
                )
            if code in {"PATCH_SEQUENTIAL_CAS", "PATCH_MISSING_BEFORE"}:
                return _patch_error("PATCH_CONFLICT", code, reason=code)
            return _patch_error("GRAPH_PROTOCOL_ERROR", code, reason=code)

    def _receipt(
        self,
        *,
        document_id: str,
        version: GraphVersion,
        upload_id: bytes,
        patch: PatchDocument,
        state: str,
        operation_id: str,
        idempotent: bool,
        validated: bool,
        mutations: int,
        history_count: int,
        extra: bytes,
        kind: str,
    ) -> GraphPatchResponse:
        artifact = self._publish_bytes(document_id, version.digest, extra, kind)
        return GraphPatchResponse(
            status="ok",
            complete=True,
            document_id=document_id,
            graph_id=version.graph.hex(),
            version=version.digest,
            generation=version.digest,
            semantic_revision=version.semantic_revision,
            state=state,
            upload_id=upload_id.hex(),
            operation_id=operation_id,
            idempotent=idempotent,
            validated=validated,
            operation_count=len(patch.operations),
            hancom_mutations=mutations,
            forward_digest=patch_digest(patch).hex(),
            inverse_digest=patch_digest(invert_patch(patch)).hex(),
            history_count=history_count,
            artifact_uri=artifact.uri,
            artifact_sha256=artifact.sha256,
            artifact_bytes=artifact.bytes,
        )

    async def hwp_validate_graph_patch(
        self,
        *,
        document_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")],
        store_id: Annotated[str, Field(min_length=1, max_length=256)],
        upload_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")],
        expected_version: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")],
        chunks: tuple[tuple[int, int, int, str], ...],
        operation_id: Annotated[str, Field(min_length=1, max_length=128)] = "",
    ) -> GraphPatchResponse:
        del store_id
        decoded = self._decode_chunks(chunks)
        if isinstance(decoded, GraphPatchResponse):
            return decoded
        canonical = self._canonical_or_error(decoded)
        if isinstance(canonical, GraphPatchResponse):
            return canonical
        if canonical.version.digest != expected_version:
            return _patch_error("STALE_VERSION", "패치 버전이 기대 버전과 다릅니다.")
        if canonical.version.session.hex() != document_id:
            return _patch_error("WRONG_DOCUMENT", "패치 문서가 대상 문서와 다릅니다.")
        return self._receipt(
            document_id=document_id,
            version=canonical.version,
            upload_id=bytes.fromhex(upload_id),
            patch=canonical,
            state="validated",
            operation_id=operation_id,
            idempotent=False,
            validated=True,
            mutations=0,
            history_count=0,
            extra=encode_patch(canonical),
            kind="validated-patch",
        )

    async def hwp_diff_graph_patch(
        self,
        *,
        document_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")],
        store_id: Annotated[str, Field(min_length=1, max_length=256)],
        upload_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")],
        expected_version: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")],
        chunks: tuple[tuple[int, int, int, str], ...],
        operation_id: Annotated[str, Field(min_length=1, max_length=128)] = "",
    ) -> GraphPatchResponse:
        validated = await self.hwp_validate_graph_patch(
            document_id=document_id,
            store_id=store_id,
            upload_id=upload_id,
            expected_version=expected_version,
            chunks=chunks,
            operation_id=operation_id,
        )
        if validated.status != "ok" or validated.version is None:
            return validated
        decoded = self._decode_chunks(chunks)
        if isinstance(decoded, GraphPatchResponse):
            return decoded
        canonical = self._canonical_or_error(decoded)
        if isinstance(canonical, GraphPatchResponse):
            return canonical
        return self._receipt(
            document_id=document_id,
            version=canonical.version,
            upload_id=bytes.fromhex(upload_id),
            patch=canonical,
            state="diff",
            operation_id=operation_id,
            idempotent=False,
            validated=True,
            mutations=0,
            history_count=0,
            extra=encode_patch(invert_patch(canonical)),
            kind="diff-patch",
        )

    async def hwp_invert_graph_patch(
        self,
        *,
        document_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")],
        store_id: Annotated[str, Field(min_length=1, max_length=256)],
        upload_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")],
        expected_version: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")],
        chunks: tuple[tuple[int, int, int, str], ...],
        operation_id: Annotated[str, Field(min_length=1, max_length=128)] = "",
    ) -> GraphPatchResponse:
        decoded = self._decode_chunks(chunks)
        if isinstance(decoded, GraphPatchResponse):
            return decoded
        canonical = self._canonical_or_error(decoded)
        if isinstance(canonical, GraphPatchResponse):
            return canonical
        if canonical.version.digest != expected_version:
            return _patch_error("STALE_VERSION", "패치 버전이 기대 버전과 다릅니다.")
        if canonical.version.session.hex() != document_id:
            return _patch_error("WRONG_DOCUMENT", "패치 문서가 대상 문서와 다릅니다.")
        inverse = invert_patch(canonical)
        return self._receipt(
            document_id=document_id,
            version=canonical.version,
            upload_id=bytes.fromhex(upload_id),
            patch=inverse,
            state="inverted",
            operation_id=operation_id,
            idempotent=False,
            validated=True,
            mutations=0,
            history_count=0,
            extra=encode_patch(inverse),
            kind="inverse-patch",
        )

    async def hwp_graph_patch_history(
        self,
        *,
        document_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")],
        store_id: Annotated[str, Field(min_length=1, max_length=256)],
        expected_version: Annotated[str, Field(pattern=r"^(?:[0-9a-f]{64})?$")] = "",
        operation_id: Annotated[str, Field(min_length=1, max_length=128)] = "",
    ) -> GraphPatchResponse:
        del store_id, expected_version
        if self._patch_host is None:
            return _patch_error(
                "PATCH_UNAVAILABLE", "그래프 패치 host를 사용할 수 없습니다."
            )
        world = self._patch_host.world()
        if world.version.session.hex() != document_id:
            return _patch_error("WRONG_DOCUMENT", "패치 문서가 대상 문서와 다릅니다.")
        payload = b"".join(world.history)
        artifact = self._publish_bytes(
            document_id, world.bind_version().digest, payload, "patch-history"
        )
        return GraphPatchResponse(
            status="ok",
            complete=True,
            document_id=document_id,
            graph_id=world.version.graph.hex(),
            version=world.bind_version().digest,
            generation=world.bind_version().digest,
            semantic_revision=world.revision,
            state="history",
            operation_id=operation_id,
            history_count=len(world.history),
            artifact_uri=artifact.uri,
            artifact_sha256=artifact.sha256,
            artifact_bytes=artifact.bytes,
        )

    async def hwp_apply_graph_patch(
        self,
        *,
        document_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")],
        store_id: Annotated[str, Field(min_length=1, max_length=256)],
        upload_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")],
        expected_version: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")],
        chunks: tuple[tuple[int, int, int, str], ...],
        operation_id: Annotated[str, Field(min_length=1, max_length=128)],
        validated: bool = True,
    ) -> GraphPatchResponse:
        del store_id
        replayed = self._replays.get(operation_id)
        if replayed is not None:
            return replayed.model_copy(update={"idempotent": True})
        if self._patch_host is None:
            return _patch_error(
                "PATCH_UNAVAILABLE", "그래프 패치 host를 사용할 수 없습니다."
            )
        decoded = self._decode_chunks(chunks)
        if isinstance(decoded, GraphPatchResponse):
            return decoded
        canonical = self._canonical_or_error(decoded)
        if isinstance(canonical, GraphPatchResponse):
            return canonical
        if canonical.version.digest != expected_version:
            return _patch_error("STALE_VERSION", "패치 버전이 기대 버전과 다릅니다.")
        if canonical.version.session.hex() != document_id:
            return _patch_error("WRONG_DOCUMENT", "패치 문서가 대상 문서와 다릅니다.")
        world = self._patch_host.world()
        if world.version.session.hex() != document_id:
            return _patch_error("WRONG_DOCUMENT", "패치 문서가 대상 문서와 다릅니다.")

        host_injector = getattr(self._patch_host, "injector", None)
        injector: Injector | None = None
        if callable(host_injector):

            def bound(step: ApplyStep, current: GraphWorld) -> GraphWorld:
                return cast(GraphWorld, host_injector(step, current))

            injector = bound

        try:
            nxt, receipt = apply_validated_patch(
                world,
                canonical,
                upload_id=bytes.fromhex(upload_id),
                expected_version=canonical.version,
                validated=validated,
                sealed=True,
                injector=injector,
            )
        except PatchApplyError as error:
            return _patch_error(error.code, error.code)
        self._patch_host.commit(nxt)
        response = self._receipt(
            document_id=document_id,
            version=receipt.version,
            upload_id=receipt.upload_id,
            patch=canonical,
            state=receipt.state.name.lower(),
            operation_id=operation_id,
            idempotent=False,
            validated=True,
            mutations=receipt.hancom_mutations,
            history_count=len(nxt.history),
            extra=encode_patch(canonical),
            kind="applied-patch",
        )
        if receipt.state is ApplyState.APPLIED:
            self._replays[operation_id] = response
        return response

    async def hwp_reconcile_graph_patch(
        self,
        *,
        document_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")],
        store_id: Annotated[str, Field(min_length=1, max_length=256)],
        upload_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")],
        expected_version: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")],
        chunks: tuple[tuple[int, int, int, str], ...],
        operation_id: Annotated[str, Field(min_length=1, max_length=128)],
    ) -> GraphPatchResponse:
        return await self.hwp_apply_graph_patch(
            document_id=document_id,
            store_id=store_id,
            upload_id=upload_id,
            expected_version=expected_version,
            chunks=chunks,
            operation_id=operation_id,
        )


__all__ = [
    "GraphArtifactStore",
    "GraphError",
    "GraphPatchResponse",
    "GraphResponse",
    "HwpPublicGraphTools",
    "PublicGraphPatchHost",
    "graph_call_tool_result",
    "PublicGraphClient",
]
