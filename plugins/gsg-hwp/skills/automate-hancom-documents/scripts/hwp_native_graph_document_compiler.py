from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal

from _hwp_native_graph_wire import GraphVersion
from hwp_native_graph_patch import (
    PatchDocument,
    PatchKind,
    PatchOperation,
    encode_patch,
    patch_digest,
)


class CompileErrorCode(StrEnum):
    PAGE_SETUP_REQUIRED = "COMPILE_PAGE_SETUP"
    UNVERIFIED_DEFAULT = "COMPILE_UNVERIFIED_DEFAULT"
    SOURCE_MUTATION = "COMPILE_SOURCE_MUTATION"


class CompileError(ValueError):
    code: CompileErrorCode

    def __init__(self, code: CompileErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PageSetup:
    paper_width_mm: int
    paper_height_mm: int
    orientation: Literal["portrait", "landscape"]
    observed: bool = True


@dataclass(frozen=True, slots=True)
class StyleIntent:
    name: str
    observed: bool = True


@dataclass(frozen=True, slots=True)
class RunIntent:
    text: str
    style: str | None = None


@dataclass(frozen=True, slots=True)
class ParagraphIntent:
    runs: tuple[RunIntent, ...] = ()


@dataclass(frozen=True, slots=True)
class TableIntent:
    rows: int
    columns: int


@dataclass(frozen=True, slots=True)
class ImageIntent:
    asset_digest: str


@dataclass(frozen=True, slots=True)
class ControlIntent:
    kind: str


@dataclass(frozen=True, slots=True)
class StoryIntent:
    role: Literal["body", "header", "footer"]
    paragraphs: tuple[ParagraphIntent, ...] = ()
    tables: tuple[TableIntent, ...] = ()
    images: tuple[ImageIntent, ...] = ()
    controls: tuple[ControlIntent, ...] = ()


@dataclass(frozen=True, slots=True)
class SectionIntent:
    stories: tuple[StoryIntent, ...] = ()


@dataclass(frozen=True, slots=True)
class DocumentIntent:
    page_setup: PageSetup | None = None
    styles: tuple[StyleIntent, ...] = ()
    sections: tuple[SectionIntent, ...] = ()
    template_bytes: bytes | None = None
    preserve_opaque: bool = True


@dataclass(frozen=True, slots=True)
class InternedFact:
    kind: str
    name: str
    observed: bool


@dataclass(frozen=True, slots=True)
class ConstructionReceipt:
    status: Literal["ok"]
    source_sha256: str
    source_bytes: bytes
    result_digest: str
    interned: tuple[InternedFact, ...]
    created: tuple[str, ...]
    patch: PatchDocument
    patch_digest: bytes


_SCHEMA: Final = 1


def _uuid(seed: int) -> bytes:
    value = bytearray(seed.to_bytes(16, "big"))
    value[6] = value[6] & 0x0F | 0x40
    value[8] = value[8] & 0x3F | 0x80
    return bytes(value)


def _version() -> GraphVersion:
    return GraphVersion(
        session=_uuid(1),
        graph=_uuid(2),
        profile_bits=3,
        semantic_revision=0,
        layout_revision=0,
        locator_epoch=1,
        semantic_root=bytes(32),
        layout_root=bytes(32),
        capture_root=bytes(32),
        semantic_certified=True,
        layout_present=False,
    )


def _utf16(value: str) -> bytes:
    raw = value.encode("utf-16le")
    return (len(raw) // 2).to_bytes(8, "little") + raw


def _source_digest(source: bytes) -> str:
    return hashlib.sha256(source).hexdigest()


def compile_document_intent(intent: DocumentIntent) -> ConstructionReceipt:
    source = intent.template_bytes or b""
    source_sha = _source_digest(source)
    if intent.page_setup is None:
        raise CompileError(
            CompileErrorCode.PAGE_SETUP_REQUIRED,
            "required page setup is missing before Hancom mutation",
        )
    if not intent.page_setup.observed:
        raise CompileError(
            CompileErrorCode.UNVERIFIED_DEFAULT,
            "unobserved page setup cannot be invented",
        )
    interned: list[InternedFact] = [
        InternedFact("page", "setup", intent.page_setup.observed)
    ]
    created: list[str] = ["page-setup"]
    operations: list[PatchOperation] = []
    next_id = 20

    def mint() -> bytes:
        nonlocal next_id
        minted = _uuid(next_id)
        next_id += 1
        return minted

    for style in intent.styles:
        if not style.observed and intent.template_bytes is not None:
            raise CompileError(
                CompileErrorCode.UNVERIFIED_DEFAULT,
                f"unobserved style {style.name!r} cannot be invented",
            )
        interned.append(InternedFact("style", style.name, style.observed))
        created.append(f"style:{style.name}")
        operations.append(
            PatchOperation(
                kind=PatchKind.REPLACE_TEXT,
                target=mint(),
                subject=101,
                scalar_tag=4,
                before=_utf16(""),
                after=_utf16(style.name),
            )
        )
    if not intent.sections:
        created.append("empty-body")
    for section_index, section in enumerate(intent.sections):
        created.append(f"section:{section_index}")
        for story in section.stories:
            created.append(f"story:{story.role}")
            for paragraph in story.paragraphs:
                created.append("paragraph")
                for run in paragraph.runs:
                    created.append("run")
                    operations.append(
                        PatchOperation(
                            kind=PatchKind.REPLACE_TEXT,
                            target=mint(),
                            subject=101,
                            scalar_tag=4,
                            before=_utf16(""),
                            after=_utf16(run.text),
                        )
                    )
            for table in story.tables:
                created.append(f"table:{table.rows}x{table.columns}")
                operations.append(
                    PatchOperation(
                        kind=PatchKind.REPLACE_TEXT,
                        target=mint(),
                        subject=101,
                        scalar_tag=4,
                        before=_utf16(""),
                        after=_utf16(f"{table.rows}x{table.columns}"),
                    )
                )
            for image in story.images:
                created.append(f"image:{image.asset_digest}")
                interned.append(InternedFact("asset", image.asset_digest, True))
            for control in story.controls:
                created.append(f"control:{control.kind}")
    if intent.template_bytes is not None and intent.preserve_opaque:
        interned.append(InternedFact("opaque", source_sha, True))
        created.append("opaque-template")
    version = _version()
    patch = PatchDocument(version=version, operations=tuple(operations), schema=_SCHEMA)
    encoded = encode_patch(patch)
    if source != (intent.template_bytes or b""):
        raise CompileError(CompileErrorCode.SOURCE_MUTATION, "source bytes changed")
    return ConstructionReceipt(
        status="ok",
        source_sha256=source_sha,
        source_bytes=source,
        result_digest=hashlib.sha256(encoded).hexdigest(),
        interned=tuple(interned),
        created=tuple(created),
        patch=patch,
        patch_digest=patch_digest(patch),
    )


__all__ = [
    "CompileError",
    "CompileErrorCode",
    "ConstructionReceipt",
    "ControlIntent",
    "DocumentIntent",
    "ImageIntent",
    "InternedFact",
    "PageSetup",
    "ParagraphIntent",
    "RunIntent",
    "SectionIntent",
    "StoryIntent",
    "StyleIntent",
    "TableIntent",
    "compile_document_intent",
]
