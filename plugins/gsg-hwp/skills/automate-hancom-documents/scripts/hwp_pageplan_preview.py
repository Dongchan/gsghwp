from __future__ import annotations

import base64
import html
import json
import re
from typing import Annotated, Literal, cast

from pydantic import Field, model_validator

from hwp_live_values import ContractModel
from hwp_pageplan_assets import (
    ImageAssetRecord,
    SourceRegistry,
)
from hwp_pageplan_contract import (
    ImagePlanBlock,
    MmBox,
    PagePlan,
    PagePlanBlock,
    PagePlanPage,
    ParagraphPlanBlock,
    TablePlanBlock,
    canonical_json_bytes,
    revalidate_page_plan,
    revalidate_source_registry,
    sha256_bytes,
    verify_page_plan_sources,
)


class PreviewCompilerMeta(ContractModel):
    schema_id: Literal["gsg.hwp.preview-meta.v1"] = Field(alias="schema")
    page_plan_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source_manifest_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    style_roles_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    ordered_source_refs: tuple[str, ...]
    figure_slot_order: tuple[int, ...]
    figure_slot_count: int = Field(ge=0)
    page_count: int = Field(ge=1, le=2)
    block_count: int = Field(ge=1)
    g01_document_revision_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    g01_page_setup_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    g01_convention_profile_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    g01_body_geometry_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    html_content_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class PreviewArtifact(ContractModel):
    schema_id: Literal["gsg.hwp.preview.v1"] = Field(alias="schema")
    candidate: Literal["A", "B"]
    page_count: int = Field(ge=1, le=2)
    html: str = Field(min_length=1)
    artifact_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @classmethod
    def from_html(
        cls,
        html_text: str,
        *,
        candidate: Literal["A", "B"] = "A",
        page_count: int = 1,
    ) -> PreviewArtifact:
        return cls(
            schema="gsg.hwp.preview.v1",
            candidate=candidate,
            page_count=page_count,
            html=html_text,
            artifact_sha256=sha256_bytes(html_text.encode("utf-8")),
        )

    @model_validator(mode="after")
    def validate_artifact_hash(self) -> PreviewArtifact:
        expected = sha256_bytes(self.html.encode("utf-8"))
        if self.artifact_sha256 != expected:
            raise ValueError("preview artifact SHA-256 mismatch")
        expected_pages = 1 if self.candidate == "A" else 2
        if self.page_count != expected_pages:
            raise ValueError("preview candidate page count mismatch")
        return self


_META_RE = re.compile(
    r'<meta name="gsg-compiler-meta" content="([A-Za-z0-9+/=]+)">',
)
_META_NORMALIZATION_TOKEN = "GSG_PAGEPLAN_META_CONTENT_NORMALIZED"


def _encode_meta(meta: PreviewCompilerMeta) -> str:
    raw = canonical_json_bytes(meta.model_dump(mode="json", by_alias=True))
    return base64.b64encode(raw).decode("ascii")


def _meta_match(html_text: str) -> re.Match[str]:
    match = _META_RE.search(html_text)
    if match is None or len(_META_RE.findall(html_text)) != 1:
        raise ValueError(
            "preview must contain exactly one canonical compiler metadata tag"
        )
    return match


def _normalized_html(html_text: str) -> str:
    match = _meta_match(html_text)
    start, end = match.span(1)
    return html_text[:start] + _META_NORMALIZATION_TOKEN + html_text[end:]


def _extract_meta(html_text: str) -> PreviewCompilerMeta:
    match = _meta_match(html_text)
    try:
        raw = base64.b64decode(match.group(1), validate=True)
        value = cast(dict[str, object], json.loads(raw.decode("utf-8")))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(
            "preview compiler metadata is not valid canonical base64 JSON"
        ) from error
    return PreviewCompilerMeta.model_validate(value)


def _number(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _css_quote(value: str) -> str:
    if any(
        char in value for char in ('"', "'", "\\", ";", "{", "}", "<", ">", "(", ")")
    ):
        raise ValueError("font family contains CSS injection syntax")
    return f"'{value}'"


def _style_attr(value: str) -> str:
    return html.escape(value, quote=True)


def _style_css(registry: SourceRegistry, role: str) -> str:
    style = registry.style(role)
    values = [
        f"font-family:{_css_quote(style.font_family)}",
        f"font-size:{_number(style.font_size_pt)}pt",
        f"line-height:{_number(style.line_height_percent)}%",
        f"text-align:{style.align}",
        f"color:{style.color_hex}",
        f"font-weight:{'700' if style.bold else '400'}",
    ]
    if style.fill_hex is not None:
        values.append(f"background-color:{style.fill_hex}")
    if style.border_hex is not None:
        values.append(f"border:0.2mm solid {style.border_hex}")
    return ";".join(values)


def _box_css(block_box: MmBox, body_box: MmBox) -> str:
    left = block_box.left_mm - body_box.left_mm
    top = block_box.top_mm - body_box.top_mm
    width = block_box.width_mm
    height = block_box.height_mm
    return ";".join(
        (
            "position:absolute",
            f"left:{_number(left)}mm",
            f"top:{_number(top)}mm",
            f"width:{_number(width)}mm",
            f"height:{_number(height)}mm",
            "overflow:hidden",
            "box-sizing:border-box",
        )
    )


def _data_uri(source: ImageAssetRecord) -> str:
    raw = source.path.read_bytes()
    if sha256_bytes(raw) != source.byte_sha256:
        raise ValueError(f"image source changed during preview: {source.source_ref}")
    return f"data:{source.mime_type};base64,{base64.b64encode(raw).decode('ascii')}"


def _width_label(page_number: int, slot_id: int, page_plan: PagePlan) -> str:
    for label in page_plan.observed_width_labels:
        if label.page == page_number and label.slot_id == slot_id:
            denominator = _number(1 / label.body_fraction)
            return f"그림 폭 약 {label.width_mm:.1f}mm (본문 폭의 1/{denominator})"
    raise ValueError(
        f"missing observed width label for page={page_number}, slot={slot_id}"
    )


def _render_paragraph(
    block: ParagraphPlanBlock,
    page_body_box: MmBox,
    registry: SourceRegistry,
) -> str:
    source = registry.require_text(block.source_ref)
    style = _style_attr(
        _box_css(block.box, page_body_box)
        + ";"
        + _style_css(registry, block.style_role)
    )
    return (
        f'<div class="plan-block paragraph" id="{html.escape(block.block_id, quote=True)}" '
        f'data-source-ref="{html.escape(block.source_ref, quote=True)}" '
        f'style="{style}">'
        f"{html.escape(source.content)}"
        "</div>"
    )


def _render_table(
    block: TablePlanBlock,
    page_body_box: MmBox,
    registry: SourceRegistry,
) -> str:
    source = registry.require_table(block.source_ref)
    cells = "".join(
        "<tr>"
        + "".join(
            f'<td style="{_style_attr(_style_css(registry, block.style_role))}">'
            + f"{html.escape(value)}"
            + "</td>"
            for value in row
        )
        + "</tr>"
        for row in source.rows
    )
    table_style = (
        _box_css(block.box, page_body_box)
        + ";border-collapse:collapse;width:100%;height:100%"
    )
    return (
        f'<table class="plan-block source-table" id="{html.escape(block.block_id, quote=True)}" '
        f'data-source-ref="{html.escape(block.source_ref, quote=True)}" '
        f'style="{_style_attr(table_style)}"><tbody>{cells}</tbody></table>'
    )


def _render_image(
    block: ImagePlanBlock,
    page_number: int,
    page_body_box: MmBox,
    page_plan: PagePlan,
    registry: SourceRegistry,
) -> str:
    slot = page_plan.slot_map.require(block.slot_id)
    source = registry.require_image(slot.source or "")
    image_style = _box_css(block.box, page_body_box).replace(
        ";overflow:hidden", ";overflow:visible"
    )
    quality_label = ""
    if source.quality == "capture_only":
        quality_label = '<div class="quality-label">캡처 전용 - 원본 자산 필요</div>'
    caption = ""
    if block.caption_source_ref is not None:
        caption_source = registry.require_text(block.caption_source_ref)
        caption_style = "body"
        if any(style.role == "caption" for style in registry.style_roles):
            caption_style = "caption"
        caption = (
            f'<div class="source-caption" style="{_style_attr(_style_css(registry, caption_style))}">'
            f"{html.escape(caption_source.content)}"
            "</div>"
        )
    label = _width_label(page_number, block.slot_id, page_plan)
    figure_open = (
        f'<figure class="plan-block source-figure" id="{html.escape(block.block_id, quote=True)}" '
        f'data-source-ref="{html.escape(source.source_ref, quote=True)}" '
        f'data-fit-policy="contain-original-ratio-no-crop-no-stretch" '
        f'style="{_style_attr(image_style)}">'
    )
    image_tag = (
        f'<img src="{_data_uri(source)}" alt="{html.escape(source.source_ref, quote=True)}" '
        + 'style="display:block;width:100%;height:100%;object-fit:contain;object-position:center">'
    )
    return (
        figure_open
        + image_tag
        + f'<div class="observation-label">{html.escape(label)}</div>'
        + f"{quality_label}{caption}"
        + "</figure>"
    )


def _render_block(
    block: PagePlanBlock,
    page_number: int,
    page_body_box: MmBox,
    page_plan: PagePlan,
    registry: SourceRegistry,
) -> str:
    if isinstance(block, ParagraphPlanBlock):
        return _render_paragraph(block, page_body_box, registry)
    if isinstance(block, TablePlanBlock):
        return _render_table(block, page_body_box, registry)
    if isinstance(block, ImagePlanBlock):
        return _render_image(block, page_number, page_body_box, page_plan, registry)
    return f'<div class="plan-page-break" id="{html.escape(block.block_id)}"></div>'


def _render_page(
    page: PagePlanPage,
    page_plan: PagePlan,
    registry: SourceRegistry,
) -> str:
    page_number = page.page
    setup = page.page_setup
    body_box = page.body_box
    blocks = "".join(
        _render_block(block, page_number, body_box, page_plan, registry)
        for block in page.blocks
    )
    body_style = ";".join(
        (
            "position:absolute",
            f"left:{_number(body_box.left_mm)}mm",
            f"top:{_number(body_box.top_mm)}mm",
            f"width:{_number(body_box.width_mm)}mm",
            f"height:{_number(body_box.height_mm)}mm",
        )
    )
    return (
        f'<section class="preview-page" data-page="{page_number}" '
        f'style="width:{_number(setup.paper_width_mm)}mm;'
        f'height:{_number(setup.paper_height_mm)}mm">'
        f'<div class="page-body" style="{_style_attr(body_style)}">{blocks}</div>'
        "</section>"
    )


def _render_document(
    page_plan: PagePlan,
    registry: SourceRegistry,
    metadata_content: str,
) -> str:
    pages = "".join(_render_page(page, page_plan, registry) for page in page_plan.pages)
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="gsg-compiler-meta" content="{metadata_content}">'
        "<style>"
        "*{box-sizing:border-box}"
        "html,body{margin:0;padding:0}"
        "body{background:#eef0f2;padding:12mm;font-family:sans-serif}"
        ".preview-note{max-width:210mm;margin:0 auto 4mm;padding:2mm 3mm;"
        "background:#fff;color:#30343a;font:10pt sans-serif}"
        ".preview-pages{display:flex;flex-direction:column;gap:8mm;align-items:center}"
        ".preview-page{position:relative;background:#fff;overflow:hidden;"
        "box-shadow:0 1mm 4mm rgba(0,0,0,.16)}"
        ".page-body{overflow:visible}"
        ".paragraph{white-space:pre-wrap;overflow:hidden}"
        ".source-table{table-layout:fixed}"
        ".source-table td{padding:1mm;vertical-align:top;overflow:hidden;"
        "word-break:keep-all;white-space:pre-wrap}"
        ".source-figure{margin:0;overflow:visible}"
        ".observation-label{position:absolute;left:0;top:100%;margin-top:1mm;"
        "font:8pt sans-serif;color:#263238;background:#fff;padding:.5mm 1mm;"
        "white-space:nowrap;border:0.2mm solid #78909c}"
        ".quality-label{position:absolute;left:0;bottom:0;right:0;padding:1mm;"
        "background:rgba(255,247,214,.95);color:#7a4e00;font:8pt sans-serif;"
        "text-align:center}"
        ".source-caption{position:absolute;left:0;right:0;top:100%;margin-top:7mm;"
        "white-space:pre-wrap}"
        ".plan-page-break{display:block;height:0}"
        "</style></head><body>"
        '<div class="preview-note">'
        "브라우저 미리보기: 조판 근사값이며 채움 상한은 약 95%입니다. "
        "최종 판정은 HWP preflight/render에서 합니다."
        "</div>"
        f'<main class="preview-pages" data-candidate="{page_plan.candidate}">'
        f"{pages}</main></body></html>"
    )


def _preview_meta(
    page_plan: PagePlan, source_registry: SourceRegistry
) -> PreviewCompilerMeta:
    plan_meta = page_plan.compiler_meta
    return PreviewCompilerMeta(
        schema="gsg.hwp.preview-meta.v1",
        page_plan_sha256=plan_meta.canonical_plan_sha256,
        source_manifest_sha256=source_registry.manifest_sha256,
        style_roles_sha256=source_registry.style_roles_sha256,
        ordered_source_refs=plan_meta.ordered_source_refs,
        figure_slot_order=plan_meta.figure_slot_order,
        figure_slot_count=plan_meta.figure_slot_count,
        page_count=plan_meta.page_count,
        block_count=plan_meta.block_count,
        g01_document_revision_hash=plan_meta.g01_document_revision_hash,
        g01_page_setup_hash=plan_meta.g01_page_setup_hash,
        g01_convention_profile_hash=plan_meta.g01_convention_profile_hash,
        g01_body_geometry_hash=plan_meta.g01_body_geometry_hash,
        html_content_sha256="0" * 64,
    )


def render_pageplan_preview(
    page_plan: PagePlan,
    source_registry: SourceRegistry,
) -> PreviewArtifact:
    """Render a self-contained one-way preview; this result is never compiler input."""
    page_plan = revalidate_page_plan(page_plan)
    source_registry = cast(SourceRegistry, revalidate_source_registry(source_registry))
    verify_page_plan_sources(
        page_plan,
        source_registry,
        require_final_assets=False,
    )
    metadata = _preview_meta(page_plan, source_registry)
    placeholder_html = _render_document(
        page_plan,
        source_registry,
        _encode_meta(metadata),
    )
    content_hash = sha256_bytes(_normalized_html(placeholder_html).encode("utf-8"))
    metadata_payload = metadata.model_dump(mode="json", by_alias=True)
    metadata_payload["html_content_sha256"] = content_hash
    metadata = PreviewCompilerMeta.model_validate(metadata_payload)
    final_html = _render_document(
        page_plan,
        source_registry,
        _encode_meta(metadata),
    )
    if sha256_bytes(_normalized_html(final_html).encode("utf-8")) != content_hash:
        raise ValueError("non-circular preview content hash is not stable")
    return PreviewArtifact.from_html(
        final_html,
        candidate=page_plan.candidate,
        page_count=len(page_plan.pages),
    )


def normalized_preview_sha256(html_text: str) -> str:
    return sha256_bytes(_normalized_html(html_text).encode("utf-8"))


def validate_preview_metadata(
    artifact: PreviewArtifact,
    page_plan: PagePlan,
    source_registry: SourceRegistry,
) -> PreviewCompilerMeta:
    """Validate only the non-executable metadata tag, never reconstruct HTML layout."""
    artifact = PreviewArtifact.model_validate(
        artifact.model_dump(mode="json", by_alias=True)
    )
    page_plan = revalidate_page_plan(page_plan)
    source_registry = cast(SourceRegistry, revalidate_source_registry(source_registry))
    if artifact.candidate != page_plan.candidate or artifact.page_count != len(
        page_plan.pages
    ):
        raise ValueError("preview candidate or page count mismatch")
    verify_page_plan_sources(
        page_plan,
        source_registry,
        require_final_assets=False,
    )
    metadata = _extract_meta(artifact.html)
    if metadata.html_content_sha256 != sha256_bytes(
        _normalized_html(artifact.html).encode("utf-8")
    ):
        raise ValueError("preview HTML content hash mismatch")
    expected = _preview_meta(page_plan, source_registry)
    metadata_payload = metadata.model_dump(mode="json", by_alias=True)
    metadata_payload["html_content_sha256"] = "0" * 64
    normalized_metadata = PreviewCompilerMeta.model_validate(metadata_payload)
    if normalized_metadata != expected:
        raise ValueError(
            "preview compiler metadata does not match PagePlan/source registry"
        )
    return metadata
