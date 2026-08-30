from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, ClassVar, Final, Literal, cast

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema


class ContractModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


Alignment = Literal["inherit", "left", "center", "right", "justify"]

# 절대 배치 계획(PagePlan)의 블록이 자기 앞에 비워 둬야 하는 세로 간격이다.
# PagePlan 은 블록마다 box.top_mm 로 절대 위치를 주지만 LayoutPlan 은 블록을
# 순서대로 쌓기만 해서, 낮추는 과정에서 계획된 세로 간격이 통째로 사라지고
# 본문 위쪽으로 붙어 버렸다(실측: 첫 그림 -4.2mm, 마지막 그림 -117.9mm).
# 흐름 레이아웃이 가진 유일한 세로 조절 수단이 문단 위 여백이라, 계획된 간격을
# 이 값으로 실어 PrevSpacing 에 더한다. 간격을 정하는 산술은
# hwp_pageplan_g04._advance 에 있다 — 앞 블록이 실제로 끝나는 자리에서 재며,
# 그 자리는 블록 종류마다 다르게 예측한다.
#
# 공개 JSON 스키마에서는 감춘다(SkipJsonSchema). hwp_append_layout·
# hwp_insert_layout·hwp_preflight_layout 은 compatibility-manifest.json 의
# legacy_tool_schema_hashes 가 "각 이름의 해시가 규범"이라고 못 박은 레거시
# 도구고, 이 값은 도구 호출자가 쓰는 입력이 아니라 G04 컴파일러가 자기 결과에
# 붙여 보내는 내부 배치 채널이다. 그래서 모델에는 있고 공개 계약은 그대로다.
#
# 감추기만 하면 공개 스키마가 거짓말을 한다 — additionalProperties:false 라고
# 해 놓고 이 이름만 조용히 받아 주기 때문이다. 그래서 매핑으로 들어오는 입력
# (도구 인자 파싱이 쓰는 길)에서는 오탈자 필드와 똑같이 거부하고, 내부에서는
# 검증을 타지 않는 model_copy 로만 넣는다. 공개 계약과 실수용이 일치한다.
PlanLeadMm = SkipJsonSchema[Annotated[float, Field(ge=0, le=500)] | None]

# 같은 간격을 앞 블록의 아래 여백으로 대신 실어야 할 때가 있다. 표를 담은
# 문단은 위 여백이 커지면 한/글이 그 문단을 통째로 다음 쪽으로 넘긴다(실측:
# 50mm 는 제자리, 70mm 는 쪽이 늘어남). 그림·문단은 148~175mm 위 여백에서도
# 멀쩡하므로, 표 앞의 간격은 표가 아니라 앞 블록의 아래 여백에 싣는다.
PlanTrailMm = PlanLeadMm

PLAN_LEAD_FIELD: Final = "plan_lead_mm"
PLAN_TRAIL_FIELD: Final = "plan_trail_mm"
_PLACEMENT_FIELDS: Final = (PLAN_LEAD_FIELD, PLAN_TRAIL_FIELD)


def reject_plan_lead_input(data: object) -> object:
    """Refuse the internal placement channels when they arrive as tool input.

    ``model_copy(update=...)`` skips validation, so the lowering can still set
    them programmatically; every mapping-shaped input -- which is what MCP
    argument parsing hands the block models -- is refused here.
    """
    if not isinstance(data, Mapping):
        return data
    keys = cast("Mapping[str, object]", data)
    for field in _PLACEMENT_FIELDS:
        # A round-tripped model_dump() carries the key with ``None``; that is the
        # absence of a placement, not a request for one, and refusing it would
        # break every internal dump/validate pair.
        if keys.get(field) is not None:
            raise ValueError(
                f"{field}은 내부 배치 채널이라 도구 입력으로 받지 않습니다"
            )
    return keys


def set_plan_lead[BlockT: BaseModel](block: BlockT, lead_mm: float) -> BlockT:
    """Put a plan lead on a block the way the internal path is allowed to."""
    return block.model_copy(update={PLAN_LEAD_FIELD: lead_mm})


def set_plan_trail[BlockT: BaseModel](block: BlockT, trail_mm: float) -> BlockT:
    """Put a plan trail on a block the way the internal path is allowed to."""
    return block.model_copy(update={PLAN_TRAIL_FIELD: trail_mm})

# What HWP's HParaShape/AlignType reads back as. MEASURED, 2026-07-28: one
# paragraph, four round trips, each written through hwp_format_text and read
# back through hwp_inspect on Hangul 2024 (13.0) with native bridge 0.5.131.
#
#     left -> 1    center -> 3    right -> 2    justify -> 0
#
# That is the official catalog exactly (hancom_official_api_catalog_v1.json,
# ParaShape/AlignType, source_page 104: 0 = 양쪽, 1 = 왼쪽, 2 = 오른쪽,
# 3 = 가운데, 4 = 배분, 5 = 나눔). It is also what this repository's own C++
# already believed: ActionTextPatch.cpp ReferenceHwpAlignment() has mapped the
# wire enumeration to 1/3/2/0 since before the measurement. The table that used
# to sit here said left=0/center=1/right=2/justify=3 and contradicted both --
# see docs/alignment-raw-value.md.
#
# What the wrong table cost: no write path consults ALIGNMENT_VALUES, so no
# paragraph was ever mis-aligned. (Three paths do send AlignType as a number --
# hwp_reference_layout_style_payload.StyleAlignments, ParagraphFormatting.cpp
# table anchor restore, and the CAPTION direct-format path -- but each carries
# its own enumeration and its own translation, and none reads this table.)
# The readback comparison in
# hwp_live_text_format_verification.verify_requested_text_format did consult
# it, so three alignments out of four were reported as partial_failure with
# commands_executed 0 after the write had already landed. The tool said it had
# done nothing to a document it had just changed. Only right (2) agreed, which
# is why this survived so long.
#
# 배분(4) and 나눔(5) have no name in ``Alignment`` and so cannot be requested;
# a document already using them reads back as those numbers and is reported
# unchanged.
#
# ``inherit: -1`` is not a measured value and is not a number HWP reports. It
# exists so the mapping is total over ``Alignment``; the one reader short-
# circuits on ``requested.alignment != "inherit"`` before indexing, so nothing
# ever looks it up.
#
# There is still deliberately no inverse (int -> name) table. An inverse table
# is only ever used to turn an observed raw value back into a write, and even a
# correct table makes that a different decision than matching a document;
# hwp_document_style_profile._replicated_updates explains where it was removed.
ALIGNMENT_VALUES: Final[Mapping[Alignment, int]] = MappingProxyType(
    {
        "inherit": -1,
        "left": 1,
        "center": 3,
        "right": 2,
        "justify": 0,
    }
)
RgbChannel = Annotated[int, Field(ge=0, le=255)]
Rgb = tuple[RgbChannel, RgbChannel, RgbChannel]


class RgbObject(ContractModel):
    r: RgbChannel
    g: RgbChannel
    b: RgbChannel
