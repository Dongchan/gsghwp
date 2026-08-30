from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Annotated, Literal
from zipfile import BadZipFile, ZipFile

from pydantic import BeforeValidator, Field, field_validator, model_validator

from hwp_live_values import ContractModel
from hwp_office_pptx import PptxSlideContent, PptxTextShapeData

SHA256_RE = r"^[0-9a-f]{64}$"
Sha256 = Annotated[str, Field(pattern=SHA256_RE)]
CandidateId = Literal["A", "B"]
ImageSourceKind = Literal[
    "ppt_media",
    "original_file",
    "capture_crop",
    "generated_preview",
]
ImageQuality = Literal["original", "capture_only", "generated_preview"]
MimeType = Literal[
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/bmp",
]


def _logical_ref(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("logical source reference must be non-empty")
    if any(char in value for char in ("\\", "<", ">", '"', "'", "\n", "\r")):
        raise ValueError(
            "logical source reference contains unsafe markup or path syntax"
        )
    if value.startswith(("http:", "https:", "file:", "/")):
        raise ValueError("logical source reference cannot be external or absolute")
    return value


LogicalRef = Annotated[str, BeforeValidator(_logical_ref)]


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _content_hash(content: str) -> str:
    return _sha256(content.encode("utf-8"))


def _table_hash(rows: tuple[tuple[str, ...], ...]) -> str:
    return _sha256(_canonical_json_bytes(rows))


def _style_roles_payload(style_roles: tuple[StyleRole, ...]) -> list[dict[str, object]]:
    return [style.model_dump(mode="json") for style in style_roles]


def _style_roles_hash(style_roles: tuple[StyleRole, ...]) -> str:
    return _sha256(_canonical_json_bytes(_style_roles_payload(style_roles)))


def _manifest_hash(
    sources: tuple[SourceRecord, ...],
    style_roles_sha256: str,
    convention_profile_hash: str,
) -> str:
    return _sha256(
        _canonical_json_bytes(
            {
                "sources": [_source_payload(source) for source in sources],
                "style_roles_sha256": style_roles_sha256,
                "convention_profile_hash": convention_profile_hash,
            }
        )
    )


class StyleRole(ContractModel):
    role: LogicalRef
    font_family: str = Field(min_length=1, max_length=100)
    font_size_pt: float = Field(gt=0, le=96)
    color_hex: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    line_height_percent: float = Field(gt=50, le=500)
    align: Literal["left", "center", "right", "justify"]
    bold: bool = False
    fill_hex: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    border_hex: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")

    @field_validator("font_family")
    @classmethod
    def reject_css_in_font_name(cls, value: str) -> str:
        if any(
            char in value
            for char in ('"', "'", "\\", ";", "{", "}", "<", ">", "(", ")")
        ):
            raise ValueError("font_family cannot contain CSS syntax")
        return value


class TextSourceRecord(ContractModel):
    kind: Literal["text"]
    source_ref: LogicalRef
    content: str = Field(max_length=100_000)
    content_sha256: Sha256

    @classmethod
    def from_text(cls, *, source_ref: str, content: str) -> TextSourceRecord:
        return cls(
            kind="text",
            source_ref=source_ref,
            content=content,
            content_sha256=_content_hash(content),
        )

    @model_validator(mode="after")
    def validate_content_hash(self) -> TextSourceRecord:
        if self.content_sha256 != _content_hash(self.content):
            raise ValueError("text source content_sha256 mismatch")
        return self


def text_source_records_from_slide(
    slide: PptxSlideContent,
) -> tuple[TextSourceRecord, ...]:
    text_shapes = tuple(
        element for element in slide.elements if isinstance(element, PptxTextShapeData)
    )
    return tuple(
        TextSourceRecord.from_text(
            source_ref=f"slide{slide.slide_number}.text[{index}]",
            content="\n".join(paragraph.text for paragraph in shape.paragraphs),
        )
        for index, shape in enumerate(text_shapes)
    )


class TableSourceRecord(ContractModel):
    kind: Literal["table"]
    source_ref: LogicalRef
    rows: tuple[tuple[str, ...], ...] = Field(min_length=1, max_length=500)
    content_sha256: Sha256

    @classmethod
    def from_rows(
        cls,
        *,
        source_ref: str,
        rows: tuple[tuple[str, ...], ...],
    ) -> TableSourceRecord:
        return cls(
            kind="table",
            source_ref=source_ref,
            rows=rows,
            content_sha256=_table_hash(rows),
        )

    @model_validator(mode="after")
    def validate_table(self) -> TableSourceRecord:
        columns = len(self.rows[0])
        if columns == 0 or any(len(row) != columns for row in self.rows):
            raise ValueError("source table rows must be rectangular and non-empty")
        if self.content_sha256 != _table_hash(self.rows):
            raise ValueError("table source content_sha256 mismatch")
        return self


class ImageAssetRecord(ContractModel):
    kind: Literal["image"]
    source_ref: LogicalRef
    path: Path
    source_kind: ImageSourceKind
    quality: ImageQuality
    final_insertable: bool
    mime_type: MimeType
    byte_sha256: Sha256
    pptx_member: LogicalRef | None = None

    @classmethod
    def from_file(
        cls,
        *,
        source_ref: str,
        path: Path,
        source_kind: ImageSourceKind,
        quality: ImageQuality,
        final_insertable: bool,
        pptx_member: str | None = None,
    ) -> ImageAssetRecord:
        mime_type = _mime_type(path.name)
        if mime_type is None:
            raise ValueError(f"unsupported image MIME type: {path}")
        return cls(
            kind="image",
            source_ref=source_ref,
            path=path,
            source_kind=source_kind,
            quality=quality,
            final_insertable=final_insertable,
            mime_type=mime_type,
            byte_sha256=_sha256(path.read_bytes()),
            pptx_member=pptx_member,
        )

    @model_validator(mode="after")
    def validate_provenance(self) -> ImageAssetRecord:
        if self.source_kind == "ppt_media":
            if not self.source_ref.startswith("ppt/media/"):
                raise ValueError(
                    "ppt_media source_ref must retain ppt/media member path"
                )
            if self.pptx_member != self.source_ref:
                raise ValueError("ppt_media asset requires matching pptx_member")
        elif self.pptx_member is not None:
            raise ValueError("only ppt_media assets may carry pptx_member")
        if self.source_kind in {"ppt_media", "original_file"}:
            if self.quality != "original" or not self.final_insertable:
                raise ValueError("original source assets must be final-insertable")
        elif self.source_kind == "capture_crop":
            if self.quality != "capture_only" or self.final_insertable:
                raise ValueError("capture crop must be capture_only and non-final")
        elif self.quality != "generated_preview" or self.final_insertable:
            raise ValueError("generated preview must be non-final and labeled")
        return self


SourceRecord = Annotated[
    TextSourceRecord | TableSourceRecord | ImageAssetRecord,
    Field(discriminator="kind"),
]


class Slot(ContractModel):
    slot_id: int = Field(ge=1)
    kind: Literal["image", "text", "table"]
    source: LogicalRef | None = None
    source_ref: LogicalRef | None = None
    source_kind: Literal["ppt_media", "original_file", "capture_crop"] | None = None
    asset_sha256: Sha256 | None = None
    quality: Literal["original", "capture_only"] | None = None
    final_insertable: bool = True

    @model_validator(mode="after")
    def validate_slot_role(self) -> Slot:
        if self.kind == "image":
            if self.source is None or self.source_ref is not None:
                raise ValueError("image slot requires exactly one source")
            if (
                self.source_kind is None
                or self.asset_sha256 is None
                or self.quality is None
            ):
                raise ValueError("image slot requires source provenance and hash")
            if self.source_kind == "capture_crop":
                if self.quality != "capture_only" or self.final_insertable:
                    raise ValueError(
                        "capture-only image slot cannot be final-insertable"
                    )
            elif self.quality != "original" or not self.final_insertable:
                raise ValueError("original image slot must be final-insertable")
        else:
            if self.source_ref is None or self.source is not None:
                raise ValueError("text/table slot requires source_ref only")
            if (
                any(
                    value is not None
                    for value in (self.source_kind, self.asset_sha256, self.quality)
                )
                or not self.final_insertable
            ):
                raise ValueError("text/table slot cannot carry image provenance")
        return self


def _slot_payload(slot: Slot) -> dict[str, object]:
    return slot.model_dump(mode="json")


class SlotMap(ContractModel):
    schema_id: Literal["gsg.hwp.imageplan-slot-map.v1"] = Field(alias="schema")
    candidate: CandidateId
    slots: tuple[Slot, ...] = Field(min_length=1, max_length=10_000)
    slot_count: int = Field(ge=1)
    slot_order: tuple[int, ...] = Field(min_length=1)
    slot_order_sha256: Sha256
    slot_map_sha256: Sha256

    @classmethod
    def create(cls, *, candidate: CandidateId, slots: tuple[Slot, ...]) -> SlotMap:
        actual = tuple(slot.slot_id for slot in slots)
        expected = tuple(range(1, len(slots) + 1))
        if actual != expected:
            raise ValueError("slot IDs must be unique, ordered, and contiguous")
        order_hash = _sha256(
            _canonical_json_bytes({"candidate": candidate, "slot_order": list(actual)})
        )
        payload = {
            "schema": "gsg.hwp.imageplan-slot-map.v1",
            "candidate": candidate,
            "slots": [_slot_payload(slot) for slot in slots],
            "slot_count": len(slots),
            "slot_order": list(actual),
            "slot_order_sha256": order_hash,
        }
        return cls(
            schema="gsg.hwp.imageplan-slot-map.v1",
            candidate=candidate,
            slots=slots,
            slot_count=len(slots),
            slot_order=actual,
            slot_order_sha256=order_hash,
            slot_map_sha256=_sha256(_canonical_json_bytes(payload)),
        )

    @model_validator(mode="after")
    def validate_hashes(self) -> SlotMap:
        actual = tuple(slot.slot_id for slot in self.slots)
        expected = tuple(range(1, len(self.slots) + 1))
        if actual != expected:
            raise ValueError("slot IDs must be unique, ordered, and contiguous")
        if self.slot_count != len(self.slots) or self.slot_order != actual:
            raise ValueError("slot count/order does not match slot list")
        expected_order_hash = _sha256(
            _canonical_json_bytes(
                {"candidate": self.candidate, "slot_order": list(actual)}
            )
        )
        if self.slot_order_sha256 != expected_order_hash:
            raise ValueError("slot order SHA-256 mismatch")
        payload = {
            "schema": self.schema_id,
            "candidate": self.candidate,
            "slots": [_slot_payload(slot) for slot in self.slots],
            "slot_count": self.slot_count,
            "slot_order": list(self.slot_order),
            "slot_order_sha256": self.slot_order_sha256,
        }
        if self.slot_map_sha256 != _sha256(_canonical_json_bytes(payload)):
            raise ValueError("slot map SHA-256 mismatch")
        return self

    def require(self, slot_id: int) -> Slot:
        for slot in self.slots:
            if slot.slot_id == slot_id:
                return slot
        raise ValueError(f"slot {slot_id} is not present in the slot map")


def _source_payload(record: SourceRecord) -> dict[str, object]:
    payload = record.model_dump(mode="json")
    if isinstance(record, ImageAssetRecord):
        payload.pop("path", None)
    return payload


class SourceRegistry(ContractModel):
    schema_id: Literal["gsg.hwp.source-registry.v1"] = Field(alias="schema")
    sources: tuple[SourceRecord, ...] = Field(min_length=1, max_length=10_000)
    style_roles: tuple[StyleRole, ...] = Field(min_length=1, max_length=256)
    style_roles_sha256: Sha256
    convention_profile_hash: Sha256
    manifest_sha256: Sha256

    @classmethod
    def create(
        cls,
        *,
        sources: tuple[SourceRecord, ...],
        style_roles: tuple[StyleRole, ...],
        convention_profile_hash: str,
    ) -> SourceRegistry:
        ordered_sources = tuple(sorted(sources, key=lambda source: source.source_ref))
        ordered_styles = tuple(sorted(style_roles, key=lambda style: style.role))
        style_hash = _style_roles_hash(ordered_styles)
        return cls(
            schema="gsg.hwp.source-registry.v1",
            sources=ordered_sources,
            style_roles=ordered_styles,
            style_roles_sha256=style_hash,
            convention_profile_hash=convention_profile_hash,
            manifest_sha256=_manifest_hash(
                ordered_sources,
                style_hash,
                convention_profile_hash,
            ),
        )

    @model_validator(mode="after")
    def validate_registry(self) -> SourceRegistry:
        refs = [source.source_ref for source in self.sources]
        if len(set(refs)) != len(refs):
            raise ValueError("source registry references must be unique")
        roles = [style.role for style in self.style_roles]
        if len(set(roles)) != len(roles):
            raise ValueError("style roles must be unique")
        if tuple(refs) != tuple(sorted(refs)):
            raise ValueError("source registry sources must be canonically ordered")
        if tuple(roles) != tuple(sorted(roles)):
            raise ValueError("style roles must be canonically ordered")
        expected_style_hash = _style_roles_hash(self.style_roles)
        if self.style_roles_sha256 != expected_style_hash:
            raise ValueError("style roles SHA-256 mismatch")
        if self.manifest_sha256 != _manifest_hash(
            self.sources,
            expected_style_hash,
            self.convention_profile_hash,
        ):
            raise ValueError("source manifest SHA-256 mismatch")
        return self

    def require(self, source_ref: str) -> SourceRecord:
        for source in self.sources:
            if source.source_ref == source_ref:
                return source
        raise ValueError(f"source reference is not registered: {source_ref}")

    def require_text(self, source_ref: str) -> TextSourceRecord:
        source = self.require(source_ref)
        if not isinstance(source, TextSourceRecord):
            raise ValueError(f"source reference is not text: {source_ref}")
        return source

    def require_table(self, source_ref: str) -> TableSourceRecord:
        source = self.require(source_ref)
        if not isinstance(source, TableSourceRecord):
            raise ValueError(f"source reference is not table: {source_ref}")
        return source

    def require_image(self, source_ref: str) -> ImageAssetRecord:
        source = self.require(source_ref)
        if not isinstance(source, ImageAssetRecord):
            raise ValueError(f"source reference is not image: {source_ref}")
        return source

    def style(self, role: str) -> StyleRole:
        for style in self.style_roles:
            if style.role == role:
                return style
        raise ValueError(f"style role is not registered in G01 convention: {role}")


def _mime_type(name: str) -> MimeType | None:
    suffix = Path(name).suffix.lower()
    values: dict[str, MimeType] = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }
    return values.get(suffix)


def extract_pptx_media_assets(
    pptx_path: Path,
    output_dir: Path,
) -> tuple[ImageAssetRecord, ...]:
    """Extract supported ``ppt/media`` members without decoding or re-encoding."""
    assets: list[ImageAssetRecord] = []
    try:
        with ZipFile(pptx_path) as archive:
            members = tuple(
                sorted(
                    name
                    for name in archive.namelist()
                    if re.fullmatch(r"ppt/media/[^/]+", name)
                    and _mime_type(name) is not None
                )
            )
            output_dir.mkdir(parents=True, exist_ok=True)
            for member in members:
                raw_before = archive.read(member)
                output_path = output_dir / member.replace("/", "__")
                _ = output_path.write_bytes(raw_before)
                raw_after = output_path.read_bytes()
                if raw_before != raw_after:
                    raise ValueError(
                        f"raw PPTX member changed during extraction: {member}"
                    )
                mime_type = _mime_type(member)
                if mime_type is None:
                    continue
                assets.append(
                    ImageAssetRecord(
                        kind="image",
                        source_ref=member,
                        path=output_path,
                        source_kind="ppt_media",
                        quality="original",
                        final_insertable=True,
                        mime_type=mime_type,
                        byte_sha256=_sha256(raw_before),
                        pptx_member=member,
                    )
                )
    except (BadZipFile, KeyError, OSError) as error:
        raise ValueError(f"could not extract PPTX media assets: {pptx_path}") from error
    return tuple(assets)


def verify_source_registry(
    source_registry: SourceRegistry,
    *,
    require_final_assets: bool,
) -> None:
    expected_style_hash = _style_roles_hash(source_registry.style_roles)
    if source_registry.style_roles_sha256 != expected_style_hash:
        raise ValueError("style roles SHA-256 mismatch")
    if source_registry.manifest_sha256 != _manifest_hash(
        source_registry.sources,
        expected_style_hash,
        source_registry.convention_profile_hash,
    ):
        raise ValueError("source manifest SHA-256 mismatch")
    for source in source_registry.sources:
        if isinstance(source, TextSourceRecord):
            if source.content_sha256 != _content_hash(source.content):
                raise ValueError(f"text source SHA-256 mismatch: {source.source_ref}")
        elif isinstance(source, TableSourceRecord):
            if source.content_sha256 != _table_hash(source.rows):
                raise ValueError(f"table source SHA-256 mismatch: {source.source_ref}")
        else:
            try:
                actual = _sha256(source.path.read_bytes())
            except OSError as error:
                raise ValueError(
                    f"source asset is unavailable: {source.source_ref}"
                ) from error
            if actual != source.byte_sha256:
                raise ValueError(f"source asset SHA-256 mismatch: {source.source_ref}")
            if require_final_assets and (
                not source.final_insertable
                or source.quality != "original"
                or source.source_kind not in {"ppt_media", "original_file"}
            ):
                raise ValueError(
                    f"source asset is not final-insertable: {source.source_ref}"
                )
