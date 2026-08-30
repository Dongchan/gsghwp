from __future__ import annotations

from dataclasses import dataclass, replace
from enum import IntEnum
from typing import Callable, Final

from _hwp_native_graph_wire import GraphVersion
from hwp_native_graph_patch import PatchDocument, canonicalize_patch, patch_digest
from hwp_native_graph_resolve import RemapPlan

PATCH_APPLY_DISPID: Final = 36
PATCH_APPLY_MESSAGE_KIND: Final = 12


class ApplyStep(IntEnum):
    VALIDATE = 0
    CHECKPOINT = 1
    EXECUTE = 2
    SETTLE_LAYOUT = 3
    READBACK = 4
    PUBLISH = 5
    HISTORY = 6
    RESTORE = 7


class ApplyState(IntEnum):
    APPLIED = 2
    NO_OP = 3
    ROLLED_BACK = 4
    RECONCILE_REQUIRED = 5


class PatchApplyError(ValueError):
    code: str

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class GraphWorld:
    version: GraphVersion
    semantic_root: bytes
    source_bytes: bytes
    revision: int
    layout_settled: bool = False
    published: bool = False
    mutate_allowed: bool = False
    reconcile_required: bool = False
    history: tuple[bytes, ...] = ()

    def bind_version(self) -> GraphVersion:
        return replace(
            self.version,
            semantic_root=self.semantic_root,
            semantic_revision=self.revision,
        )


@dataclass(frozen=True, slots=True)
class ApplyReceipt:
    upload_id: bytes
    state: ApplyState
    version: GraphVersion
    affected: tuple[bytes, ...]
    remaps: tuple[tuple[bytes, bytes], ...]
    inverse_digest: bytes
    forward_digest: bytes
    steps: tuple[ApplyStep, ...]
    hancom_mutations: int


Injector = Callable[[ApplyStep, GraphWorld], GraphWorld]


def _same_version(expected: GraphVersion, current: GraphVersion) -> bool:
    return expected.serialized == current.serialized


def apply_validated_patch(
    world: GraphWorld,
    patch: PatchDocument,
    *,
    upload_id: bytes,
    expected_version: GraphVersion,
    validated: bool,
    sealed: bool,
    remap: RemapPlan | None = None,
    injector: Injector | None = None,
    no_op: bool = False,
) -> tuple[GraphWorld, ApplyReceipt]:
    if world.reconcile_required:
        raise PatchApplyError("APPLY_RECONCILE_REQUIRED")
    if sealed and not validated:
        raise PatchApplyError("APPLY_NOT_VALIDATED")
    if not validated:
        raise PatchApplyError("APPLY_NOT_VALIDATED")
    steps: list[ApplyStep] = []
    mutations = 0

    def run(step: ApplyStep, current: GraphWorld) -> GraphWorld:
        nxt = injector(step, current) if injector is not None else current
        steps.append(step)
        return nxt

    current = run(ApplyStep.VALIDATE, world)
    if not _same_version(expected_version, current.bind_version()):
        raise PatchApplyError("APPLY_STALE")
    canonical = canonicalize_patch(patch)
    if canonical.version.serialized != expected_version.serialized:
        raise PatchApplyError("APPLY_STALE")
    if no_op or not canonical.operations:
        receipt = ApplyReceipt(
            upload_id=upload_id,
            state=ApplyState.NO_OP,
            version=current.bind_version(),
            affected=(),
            remaps=(),
            inverse_digest=b"\x00" * 32,
            forward_digest=patch_digest(canonical),
            steps=tuple(steps),
            hancom_mutations=0,
        )
        return current, receipt

    checkpoint = current
    current = run(ApplyStep.CHECKPOINT, current)
    try:
        current = replace(current, mutate_allowed=True)
        current = run(ApplyStep.EXECUTE, current)
        if not current.mutate_allowed:
            raise PatchApplyError("APPLY_EXECUTE")
        mutations += 1
        current = replace(
            current,
            semantic_root=bytes((current.semantic_root[0] ^ 1,))
            + current.semantic_root[1:],
            source_bytes=current.source_bytes + b"\x01",
            revision=current.revision + 1,
        )
        current = run(ApplyStep.SETTLE_LAYOUT, replace(current, layout_settled=True))
        if not current.layout_settled:
            raise PatchApplyError("APPLY_LAYOUT")
        current = run(ApplyStep.READBACK, current)
        if current.semantic_root == checkpoint.semantic_root:
            raise PatchApplyError("APPLY_READBACK")
        current = run(ApplyStep.PUBLISH, replace(current, published=True))
        if not current.published:
            raise PatchApplyError("APPLY_PUBLISH")
        mapping = ()
        if remap is not None:
            mapping = tuple(remap.mapping.items())
        history_blob = patch_digest(canonical) + upload_id
        current = run(
            ApplyStep.HISTORY,
            replace(current, history=current.history + (history_blob,)),
        )
        if history_blob not in current.history:
            raise PatchApplyError("APPLY_HISTORY")
        receipt = ApplyReceipt(
            upload_id=upload_id,
            state=ApplyState.APPLIED,
            version=current.bind_version(),
            affected=tuple(operation.target for operation in canonical.operations),
            remaps=mapping,
            inverse_digest=patch_digest(canonical),
            forward_digest=patch_digest(canonical),
            steps=tuple(steps),
            hancom_mutations=mutations,
        )
        return replace(current, mutate_allowed=False), receipt
    except PatchApplyError:
        restored = run(ApplyStep.RESTORE, checkpoint)
        if (
            restored.semantic_root != checkpoint.semantic_root
            or restored.source_bytes != checkpoint.source_bytes
            or restored.revision != checkpoint.revision
        ):
            blocked = replace(checkpoint, reconcile_required=True, mutate_allowed=False)
            receipt = ApplyReceipt(
                upload_id=upload_id,
                state=ApplyState.RECONCILE_REQUIRED,
                version=blocked.bind_version(),
                affected=(),
                remaps=(),
                inverse_digest=b"\x00" * 32,
                forward_digest=patch_digest(canonical),
                steps=tuple(steps),
                hancom_mutations=mutations,
            )
            return blocked, receipt
        receipt = ApplyReceipt(
            upload_id=upload_id,
            state=ApplyState.ROLLED_BACK,
            version=restored.bind_version(),
            affected=(),
            remaps=(),
            inverse_digest=b"\x00" * 32,
            forward_digest=patch_digest(canonical),
            steps=tuple(steps),
            hancom_mutations=0,
        )
        return replace(restored, mutate_allowed=False), receipt


__all__ = [
    "ApplyReceipt",
    "ApplyState",
    "ApplyStep",
    "GraphWorld",
    "PATCH_APPLY_DISPID",
    "PATCH_APPLY_MESSAGE_KIND",
    "PatchApplyError",
    "apply_validated_patch",
]
