"""참고 이미지 분석 결과의 후처리 파생 축.

이 모듈은 아무것도 검출하지 않는다. 이미 검출이 끝난
`ReferenceImageAnalysis` 의 objects·text_regions 를 읽어서 묶고, 세고,
주석만 단다. 픽셀을 다시 보지 않으므로 여기서 무엇을 바꿔도 검출 결과
(objects/text_regions/segments/gaps)는 한 톨도 달라지지 않는다.

파생 축을 따로 둔 이유는 compact 응답의 토큰 예산 때문이다. 254개 object 를
그대로 실으면 응답이 잘려 나가 모델에 아무것도 닿지 않는다(실측: 43,585
토큰이 10,000 토큰 상한에 잘려 objects 254개와 text_regions 89개가 통째로
사라졌다). 그래서 "무엇을 실을지" 를 정하는 순위와 군집을 여기서 만든다.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from pydantic import Field

from hwp_live_values import ContractModel, Rgb
from hwp_reference_image_contract import (
    NormalizedBox,
    ReferenceImageObject,
    ReferenceTextRegion,
)


# 두 색을 같은 색으로 볼 거리(RGB 유클리드). 안티에일리어싱이 만든 계조를
# 접는 것이 목적이라 지각 임계보다 넉넉하게 잡았다.
#
# 실측 근거(ria-574527d96dd1f8a8, 2613x1488): 관측된 채움색 149종 가운데
# 상위권이 (253,253,253)·(255,255,255)·(242,242,242)·(240,240,240)·
# (242,244,244) 처럼 서로 3.5 이하로 붙은 흰색·회색 계조로 채워져 있었고,
# 정작 디자인이 쓴 먼셀 스와치 6색은 17위 밖으로 밀려 응답에 실리지 못했다.
# 이 거리로 접으면 149종이 45종으로 줄고 먼셀 6색이 저마다 제 줄로 상위 19위
# 안에 들어온다.
#
# 아래쪽 한계는 계조 잡티의 간격 3.5 이고, 위쪽 한계는 스와치끼리 붙어 있는
# 가장 좁은 자리다. 20 까지 올려 보면 (228,230,232) 스와치가 (240,240,240)
# 배경 회색에 17.5 거리로 흡수돼 팔레트 한 칸이 다른 색으로 보고된다. 12 는
# 그 사고를 피하면서 계조는 여전히 접는 자리다.
FILL_MERGE_DISTANCE: Final = 12.0

# 스와치 묶음으로 인정할 최소 크기(±10%)와 최소 개수. 개수를 4로 둔 것은
# 2x2 가 사람이 팔레트로 읽는 가장 작은 배열이기 때문이다.
_SWATCH_SIZE_TOLERANCE: Final = 0.10
_SWATCH_MINIMUM_MEMBERS: Final = 4
# 격자가 실제로 차 있는지 보는 값. 흩어진 회색 얼룩 여섯 개가 우연히 크기만
# 닮으면 rows x columns 가 5x5=25 칸으로 벌어지는데(실측), 그런 것은 배열이
# 아니다. 6개가 3x2=6칸을 채우는 진짜 묶음과 이 값으로 갈린다.
_SWATCH_GRID_FILL_RATIO: Final = 0.6
# 글자가 상자 안에 들어왔다고 볼 여유. 검출 좌표가 1~2px 어긋나는 것을 봐준다.
_CONTAINMENT_TOLERANCE: Final = 0.002

# 한 라벨이 여러 조각으로 끊긴 것을 다시 붙일 때 쓰는 값들이다. 검출은 손대지
# 않는다 — 이미 나온 text_regions 의 좌표만 보고 "이건 한 줄이었다" 고 묶는다.
#
# 실측 근거(ria-d2397ba31511fce3, 2613x1488): 소제목 "그래픽요소 적용" 이 같은
# y밴드(400~586px)에서 x1338-1459 · x1418-1521 · x1503-1656 세 조각으로 끊겼다.
# 세 조각의 crop 을 나란히 놓으면 "그래픽" · "래픽요소" · "소 적용" 으로 서로
# 겹쳐 읽힌다 — 글자 사이 여백이 아니라 겹침이라 한 라벨이 확실하다.
#
# 임계도 그 실사례에서 잰다. 같은 밴드 안에서 한 라벨에 속한 조각끼리의 간격은
# -41 · -28 · -18 · -11px 로 전부 음수(겹침)이고, 남의 라벨과의 가장 좁은 간격은
# +24px 다. 밴드 높이 186px 로 나누면 -0.22~-0.06 대 +0.13 이라, 0.08 은 그
# 사이에 든다. 겹침만 붙이는 것에 가깝되 검출 좌표가 1~2px 어긋나는 것은 봐주는
# 자리다.
_TEXT_MERGE_GAP_RATIO: Final = 0.08
# 같은 줄로 볼 중심 y 차이와 높이 차이의 비율. 실사례에서 한 줄인 조각들은
# y587-622 · y588-620 · y589-619 처럼 중심이 1px 안에 모였고, 다른 줄은 밴드가
# 통째로 달랐다(400-586 대 588-620). 0.25 는 그 사이를 넉넉히 가른다.
_TEXT_BAND_CENTER_RATIO: Final = 0.25
_TEXT_HEIGHT_RATIO: Final = 0.25

# 런을 가로지르는 글자 띠로 인정할 최소 너비(런 bbox 폭 대비). 실사례에서
# 소제목은 런 폭 0.1802 가운데 0.1041(57.8%)을 가로질렀고, 같은 밴드에서 런에
# 걸치는 남의 조각은 한 개도 이 값을 넘지 못한다. 0.35 는 그 사이다.
_RUN_INTERRUPT_WIDTH_RATIO: Final = 0.35
# 끊어진 조각이 그래도 배열로 남을 최소 칸 수. 런을 처음 세울 때의 최소 4칸과
# 다른 값인 것이 핵심이다 — 4칸은 "우연히 닮은 얼룩을 배열로 착각하지 말라"는
# 문지방이고, 여기는 이미 배열로 인정된 묶음을 글자 띠가 갈라 놓은 자리다.
# 실사례의 "바탕색 적용" 선반이 설계상 1행 2칸이라, 2를 밑으로 두면 그 선반이
# 통째로 사라진다.
_RUN_SPLIT_MINIMUM_MEMBERS: Final = 2


class ReferenceObjectSample(ReferenceImageObject):
    """인라인으로 실어 보낸 object 하나에 후처리 주석을 붙인 표본.

    주석은 전부 이미 있는 관측(objects 의 bbox 와 text_regions 의 bbox)의
    포함 관계에서 나온다 — 픽셀을 다시 보지 않는다.
    """

    contains_text: bool = False
    text_regions_inside: tuple[str, ...] = ()
    crop_candidate: bool = False
    """이 응답의 숫자만으로 생김새를 되살릴 수 없으니 픽셀을 보라는 표시.

    관측으로 정한 판정 기준은 두 가지이고, 둘 다 만족해야 True 다.

      - `fill is None`: 분석기가 이 영역을 단색 하나로 줄이지 못했다는 뜻이다.
        실사례(ria-574527d96dd1f8a8)에서 254개 중 6개가 여기 해당했고 6개
        모두 `outlined_box` 였다(220x6~13px). 나머지 248개는 단색 fill 을
        갖고 있어 이 응답이 그 색을 그대로 실어 보낸다 — 되살리는 데 크롭이
        필요 없다.
      - `contains_text is False`: 안에 글자가 있으면 그 글자는 이미
        text_regions 의 `crop_path` 가 따로 서비스한다. 같은 자리를 두 번
        크롭하라고 시키지 않는다.

    "사진이냐 도면이냐" 를 직접 묻지 않는 이유는 검출 계약에 그 축이 없기
    때문이다. `ReferenceObjectKind` 는 `filled_region` 과 `outlined_box`
    둘뿐이라 "사진 kind" 를 읽을 자리가 아예 없다. 없는 축을 추측으로 채우는
    대신 실제로 관측된 두 값으로만 판정한다.
    """


class ReferenceTextAnchorSample(ContractModel):
    """text_regions 를 좌표 앵커로만 줄인 표본.

    원본 `ReferenceTextRegion` 은 `crop_hash`(64자)와 `crop_path`(긴 절대
    경로)를 달고 다녀서 한 건이 pretty-print 기준 500바이트를 넘는다. 배치를
    읽는 데 필요한 것은 "글자 덩어리가 어디에 얼마만 한 크기로 앉아 있나"
    뿐이라, 인라인 표본은 중심과 크기만 싣고 해시와 경로는
    `hwp_get_reference_image_analysis_section(section="text_regions")` 로
    넘긴다.
    """

    region_id: str = Field(pattern=r"^txt-[0-9a-f]{12}$")
    center_x: float = Field(ge=0, le=1)
    center_y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)
    line_count: int = Field(ge=1)
    merged_from_count: int = Field(default=1, ge=1)
    """이 앵커가 도로 붙인 검출 조각 수(자기 자신 포함).

    1보다 크면 검출이 한 라벨을 여러 조각으로 끊어 놓은 것을 다시 이어 붙였다는
    뜻이다. 검출 결과(text_regions)는 그대로다 — 조각들은 section 에 그대로
    남아 있고, 이 값은 이 응답이 그것을 어떻게 읽었는지만 말한다. region_id 는
    조각 가운데 가장 넓은 것의 것이라 section 에서 그대로 되집을 수 있다.
    """
    crop_paths: tuple[Path, ...] = ()
    """이 앵커를 이루는 조각들의 크롭 그림 경로.

    비어 있는 것이 보통이다 — 97개 앵커에 경로를 다 실으면 한 건에 110바이트씩
    1만 바이트를 먹어 정작 좌표가 밀려난다(그래서 원래 앵커에서 경로를 벗겨
    냈다). 그런데 벗겨 놓으면 소제목처럼 "글자를 읽어야 뜻이 통하는" 라벨이
    디스크에만 남고 모델에는 닿지 않는다(실사례: 97개가 전부 디스크에만).
    그래서 라벨급 앵커 몇 개에만 골라 싣는다.

    이름이 복수형인 것은 병합 앵커의 픽셀이 여러 크롭에 나뉘어 있기 때문이다 —
    실사례의 소제목은 세 조각이라 세 경로를 다 줘야 "그래픽요소 적용" 이 온전히
    읽힌다. 한 조각짜리 앵커면 경로도 하나다.
    """


class ReferenceSwatchRunMember(ContractModel):
    region_id: str = Field(pattern=r"^obj-[0-9a-f]{12}$")
    bbox: NormalizedBox
    fill: Rgb


class ReferenceSwatchRun(ContractModel):
    """크기가 같고 줄 맞춰 놓인, 서로 다른 색 칸들의 묶음.

    색 견본표(팔레트)를 옮겨 그릴 때 필요한 것은 색 목록이 아니라 "몇 칸이
    몇 행 몇 열로 놓였나" 다. `fills` 는 색을 세지만 배열을 말하지 못하고,
    `objects` 는 배열을 담을 수 있지만 면적 순위에서 밀려 잘린다 — 실사례에서
    먼셀 스와치 6칸은 개별 면적이 0.0018 밖에 안 돼 상위 목록에 들지 못했다.
    이 축은 그 여섯 칸을 하나의 관측으로 묶어 순위 경쟁을 통과시킨다.
    """

    run_id: str = Field(pattern=r"^run-[0-9a-f]{12}$")
    member_count: int = Field(ge=2)
    rows: int = Field(ge=1)
    columns: int = Field(ge=1)
    cell_width_px: int = Field(ge=1)
    cell_height_px: int = Field(ge=1)
    bbox: NormalizedBox
    members: tuple[ReferenceSwatchRunMember, ...]
    interrupted_by: tuple[str, ...] = ()
    """이 묶음을 이웃 묶음과 갈라 놓은 글자 띠의 region_id 들.

    비어 있으면 위아래로 끊긴 자리가 없다는 뜻이다. 값이 있으면 그 글자 띠가
    이 배열의 경계이고, 띠 건너편 칸들은 다른 run_id 로 따로 나간다.

    이 축이 없던 시절에는 크기만 같으면 페이지 절반 떨어진 칸도 한 배열로
    묶였다(실사례 ria-d2397ba31511fce3: "바탕색 적용" 2칸과 "그래픽요소 적용"
    4칸이 3x2 한 묶음이 되고, bbox 가 그 사이의 소제목을 통째로 삼켰다).
    묶음의 bbox 를 크롭 경계로 읽는 소비자에게는 그 한 줄이 곧 오독이다.
    """


def color_distance(left: Rgb, right: Rgb) -> float:
    """두 색의 RGB 유클리드 거리.

    정확한 CIE ΔE 가 아니라 의존성 없이 계산하는 근사다. 쓰임이 "안티에일리어싱
    계조를 접되 디자인 색은 가른다" 하나뿐이고, 그 둘은 실측에서 3.5 대 33.7 로
    열 배 가까이 벌어져 있어 근사로 충분히 갈린다.
    """
    return math.sqrt(
        sum((int(a) - int(b)) ** 2 for a, b in zip(left, right, strict=True))
    )


def _band_groups(values: Sequence[float], tolerance: float) -> list[list[int]]:
    """값을 오름차순으로 훑으며 tolerance 보다 벌어진 자리에서 끊는다.

    반환은 각 띠에 속한 원소의 색인 목록이라, 띠의 개수만 세는 `_bands` 와
    달리 "몇 번째 칸이 몇째 줄인가" 를 그대로 쓸 수 있다.
    """
    if not values:
        return []
    order = sorted(range(len(values)), key=lambda index: values[index])
    groups: list[list[int]] = [[order[0]]]
    for index in order[1:]:
        if values[index] - values[groups[-1][-1]] > tolerance:
            groups.append([index])
        else:
            groups[-1].append(index)
    return groups


def _bands(values: Sequence[float], tolerance: float) -> int:
    return len(_band_groups(values, tolerance))


def _box_area(box: NormalizedBox) -> float:
    return (box.right - box.left) * (box.bottom - box.top)


def _center_y(region: ReferenceTextRegion) -> float:
    return (region.bbox.top + region.bbox.bottom) / 2


def _height(region: ReferenceTextRegion) -> float:
    return region.bbox.bottom - region.bbox.top


def _same_label(
    left: ReferenceTextRegion,
    right: ReferenceTextRegion,
    *,
    image_width: int,
    image_height: int,
) -> bool:
    """두 조각이 한 라벨의 일부인가 — 같은 줄에, 같은 크기로, 붙어 있는가.

    가로 간격과 세로 높이를 견주므로 픽셀로 환산해서 잰다. 정규 좌표는 축마다
    자가 달라(실사례는 2613 대 1488) 그대로 견주면 가로 임계가 1.76배로 부푼다.
    """
    left_height = _height(left) * image_height
    right_height = _height(right) * image_height
    if abs(_center_y(left) - _center_y(right)) * image_height > (
        _TEXT_BAND_CENTER_RATIO * min(left_height, right_height)
    ):
        return False
    if abs(left_height - right_height) > _TEXT_HEIGHT_RATIO * max(
        left_height, right_height
    ):
        return False
    gap = (
        max(left.bbox.left, right.bbox.left) - min(left.bbox.right, right.bbox.right)
    ) * image_width
    return gap <= _TEXT_MERGE_GAP_RATIO * max(left_height, right_height)


def merge_text_fragments(
    regions: Sequence[ReferenceTextRegion],
    *,
    image_width: int,
    image_height: int,
) -> tuple[tuple[ReferenceTextRegion, ...], ...]:
    """끊긴 조각을 한 라벨로 도로 묶는다 — 검출은 한 톨도 달라지지 않는다.

    입력은 이미 검출이 끝난 text_regions 뿐이고 픽셀은 보지 않는다. 반환은
    "이 조각들이 한 라벨" 이라는 묶음 목록이고, 원본 조각은 그대로 들어 있다.
    검출 결과를 지우지 않으므로 section 으로 받는 text_regions 는 예전과 똑같다.

    묶는 이유는 응답의 순위 경쟁이 조각 단위로 벌어지기 때문이다. 실사례에서
    소제목 하나가 세로 스트립 세 조각으로 쪼개져 저마다 면적 0.005~0.007 로
    97개 중 하위권에 앉았고, 상위 18개만 실리는 예산에서 79개가 잘려 나갔다.
    붙이면 그 라벨이 면적 0.023 짜리 한 줄이 되어 제 순위로 올라온다.
    """
    if not regions:
        return ()
    order = sorted(
        range(len(regions)),
        key=lambda index: (_center_y(regions[index]), regions[index].bbox.left),
    )
    parents = list(range(len(regions)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for position, index in enumerate(order):
        for other in order[position + 1 :]:
            # 중심 y 로 정렬해 두었으므로, 이 조각의 높이로 잰 허용치를 넘어선
            # 순간 뒤쪽은 전부 다른 줄이다(허용치는 두 높이의 작은 쪽에서 나와
            # 이보다 커질 수 없다).
            if _center_y(regions[other]) - _center_y(regions[index]) > (
                _TEXT_BAND_CENTER_RATIO * _height(regions[index])
            ):
                break
            if _same_label(
                regions[index],
                regions[other],
                image_width=image_width,
                image_height=image_height,
            ):
                parents[find(other)] = find(index)
    grouped: dict[int, list[ReferenceTextRegion]] = {}
    for index in range(len(regions)):
        grouped.setdefault(find(index), []).append(regions[index])
    labels = [
        tuple(sorted(members, key=lambda item: item.bbox.left))
        for members in grouped.values()
    ]
    return tuple(
        sorted(
            labels,
            key=lambda members: (
                min(item.bbox.top for item in members),
                min(item.bbox.left for item in members),
            ),
        )
    )


def merged_text_box(members: Sequence[ReferenceTextRegion]) -> NormalizedBox:
    return NormalizedBox(
        left=min(item.bbox.left for item in members),
        top=min(item.bbox.top for item in members),
        right=max(item.bbox.right for item in members),
        bottom=max(item.bbox.bottom for item in members),
    )


def _run_id(members: Sequence[ReferenceImageObject]) -> str:
    digest = hashlib.sha256(
        "|".join(sorted(item.region_id for item in members)).encode("utf-8")
    )
    return f"run-{digest.hexdigest()[:12]}"


def _split_at_text_bands(
    members: Sequence[ReferenceImageObject],
    labels: Sequence[tuple[ReferenceTextRegion, ...]],
    *,
    row_tolerance: float,
) -> tuple[tuple[tuple[ReferenceImageObject, ...], tuple[str, ...]], ...]:
    """묶음을 가로지르는 글자 띠에서 끊는다.

    끊는 자리는 줄과 줄 사이의 빈 자리다. 그 빈 자리의 한가운데를 지나면서
    묶음의 폭을 유의미하게(`_RUN_INTERRUPT_WIDTH_RATIO`) 가로지르는 글자 띠가
    있으면, 그 위와 아래는 다른 배열이다 — 사람이 소제목으로 갈라 놓은 자리를
    기계가 한 덩어리로 읽지 않게 한다.

    "가운데를 지나는가" 로 묻는 이유는 칸에 겹쳐 있는 글자(칸 안의 라벨)와
    칸 사이를 가르는 글자를 이 한 가지로 갈라낼 수 있어서다. 실사례에서
    소제목의 세로 범위는 위 줄과 아래 줄에 조금씩 걸쳐 있었지만 빈 자리의
    한가운데(y0.3505)를 품고 있었다.
    """
    bands = _band_groups([item.bbox.top for item in members], row_tolerance)
    whole = ((tuple(members), ()),)
    if len(bands) < 2 or not labels:
        return whole
    left = min(item.bbox.left for item in members)
    right = max(item.bbox.right for item in members)
    width = right - left
    cuts: dict[int, tuple[str, ...]] = {}
    for index in range(len(bands) - 1):
        gap_top = max(members[position].bbox.bottom for position in bands[index])
        gap_bottom = min(members[position].bbox.top for position in bands[index + 1])
        if gap_bottom <= gap_top:
            continue
        middle = (gap_top + gap_bottom) / 2
        crossing: list[str] = []
        for label in labels:
            box = merged_text_box(label)
            if not box.top <= middle <= box.bottom:
                continue
            if min(box.right, right) - max(box.left, left) < (
                _RUN_INTERRUPT_WIDTH_RATIO * width
            ):
                continue
            crossing.extend(item.region_id for item in label)
        if crossing:
            cuts[index] = tuple(crossing)
    if not cuts:
        return whole
    parts: list[tuple[tuple[ReferenceImageObject, ...], tuple[str, ...]]] = []
    current: list[ReferenceImageObject] = []
    carried: tuple[str, ...] = ()
    for index, band in enumerate(bands):
        current.extend(members[position] for position in band)
        if index in cuts:
            parts.append((tuple(current), tuple(dict.fromkeys(carried + cuts[index]))))
            current = []
            carried = cuts[index]
    parts.append((tuple(current), carried))
    return tuple(
        (part, interrupters)
        for part, interrupters in parts
        if len(part) >= _RUN_SPLIT_MINIMUM_MEMBERS
    )


def detect_swatch_runs(
    objects: Sequence[ReferenceImageObject],
    *,
    image_width: int,
    image_height: int,
    text_regions: Sequence[ReferenceTextRegion] = (),
    minimum_distance: float = FILL_MERGE_DISTANCE,
) -> tuple[ReferenceSwatchRun, ...]:
    """크기·정렬·색이 스와치 배열을 이루는 object 묶음을 찾는다.

    검출이 아니라 군집이다 — 입력은 이미 검출된 objects·text_regions 뿐이고
    픽셀은 보지 않는다. 묶는 조건은 네 가지다.

      1. 채움색이 있는 `filled_region` 일 것. 테두리만 있는 상자는 색 견본이
         아니다.
      2. 크기가 씨앗 칸의 ±10% 안일 것.
      3. 서로의 색이 `minimum_distance` 보다 멀 것 — 같은 회색 계조가 다섯 칸
         늘어선 것은 팔레트가 아니라 잡티다(실측: 바닥 띠 5칸이 서로 3.2 이내).
      4. 행·열 격자가 실제로 차 있을 것 — 흩어진 얼룩은 6개가 5x5 로 벌어진다.

    묶은 다음에는 끊는다. 위 네 조건에는 "여기서 배열이 끝난다" 는 개념이 없어
    소제목 하나를 사이에 두고 떨어진 두 선반이 한 묶음이 된다(실사례
    ria-d2397ba31511fce3). `text_regions` 를 주면 그런 자리를 찾아 갈라 놓고
    `interrupted_by` 로 근거를 적는다. 주지 않으면 예전과 똑같이 동작한다.
    """
    labels = merge_text_fragments(
        text_regions,
        image_width=image_width,
        image_height=image_height,
    )
    candidates = [
        item
        for item in objects
        if item.kind == "filled_region" and item.fill is not None
    ]
    geometry: dict[str, tuple[float, float, float, float]] = {
        item.region_id: (
            item.bbox.left * image_width,
            item.bbox.top * image_height,
            (item.bbox.right - item.bbox.left) * image_width,
            (item.bbox.bottom - item.bbox.top) * image_height,
        )
        for item in candidates
    }
    ordered = sorted(
        candidates,
        key=lambda item: _box_area(item.bbox),
        reverse=True,
    )
    grouped: set[str] = set()
    # (끊기기 전 묶음의 칸 수, 그 묶음의 넓이, 조각) — 순위는 조각이 아니라
    # 끊기기 전 묶음으로 매긴다. 아래 정렬 설명 참고.
    runs: list[tuple[int, float, ReferenceSwatchRun]] = []
    for seed in ordered:
        if seed.region_id in grouped:
            continue
        _, _, seed_width, seed_height = geometry[seed.region_id]
        members: list[ReferenceImageObject] = []
        fills: list[Rgb] = []
        for item in ordered:
            if item.region_id in grouped:
                continue
            _, _, width, height = geometry[item.region_id]
            if abs(width - seed_width) > _SWATCH_SIZE_TOLERANCE * max(
                width, seed_width
            ):
                continue
            if abs(height - seed_height) > _SWATCH_SIZE_TOLERANCE * max(
                height, seed_height
            ):
                continue
            fill = item.fill
            if fill is None:
                continue
            if any(
                color_distance(fill, other) <= minimum_distance for other in fills
            ):
                continue
            fills.append(fill)
            members.append(item)
        if len(members) < _SWATCH_MINIMUM_MEMBERS:
            continue
        lefts = [geometry[item.region_id][0] for item in members]
        tops = [geometry[item.region_id][1] for item in members]
        columns = _bands(lefts, max(3.0, seed_width * 0.25))
        rows = _bands(tops, max(3.0, seed_height * 0.25))
        if len(members) < rows * columns * _SWATCH_GRID_FILL_RATIO:
            continue
        for item in members:
            grouped.add(item.region_id)
        placed = sorted(
            members,
            key=lambda item: (
                geometry[item.region_id][1],
                geometry[item.region_id][0],
            ),
        )
        whole_box = NormalizedBox(
            left=min(item.bbox.left for item in placed),
            top=min(item.bbox.top for item in placed),
            right=max(item.bbox.right for item in placed),
            bottom=max(item.bbox.bottom for item in placed),
        )
        for part, interrupters in _split_at_text_bands(
            placed,
            labels,
            row_tolerance=max(3.0, seed_height * 0.25) / image_height,
        ):
            run = ReferenceSwatchRun(
                run_id=_run_id(part),
                member_count=len(part),
                # 행·열은 조각마다 다시 센다. 끊긴 조각에 통짜 묶음의 3x2 를
                # 그대로 붙이면 2칸짜리 선반이 여섯 칸으로 읽힌다.
                rows=_bands(
                    [geometry[item.region_id][1] for item in part],
                    max(3.0, seed_height * 0.25),
                ),
                columns=_bands(
                    [geometry[item.region_id][0] for item in part],
                    max(3.0, seed_width * 0.25),
                ),
                cell_width_px=max(1, round(seed_width)),
                cell_height_px=max(1, round(seed_height)),
                bbox=NormalizedBox(
                    left=min(item.bbox.left for item in part),
                    top=min(item.bbox.top for item in part),
                    right=max(item.bbox.right for item in part),
                    bottom=max(item.bbox.bottom for item in part),
                ),
                members=tuple(
                    ReferenceSwatchRunMember(
                        region_id=item.region_id,
                        bbox=item.bbox,
                        # fill 은 위에서 None 을 걸러 낸 값이라 항상 있다.
                        fill=item.fill if item.fill is not None else (0, 0, 0),
                    )
                    for item in part
                ),
                interrupted_by=interrupters,
            )
            runs.append((len(placed), _box_area(whole_box), run))
    # 칸이 많은 배열이 팔레트일 확률이 높고, 같은 개수면 넓은 쪽을 먼저 낸다.
    #
    # 세는 단위는 조각이 아니라 끊기기 전 묶음이다. 조각으로 세면 소제목이
    # 갈라 놓은 선반 하나가 저 혼자 꼴찌로 밀려 예산에서 잘린다 — 실사례의
    # "바탕색 적용" 2칸이 엉뚱한 얼룩 3칸에 밀리는 자리다. 한 묶음에서 나온
    # 조각들은 같은 열쇠를 갖고, 파이썬 정렬이 안정적이라 위에서 아래 차례로
    # 붙어 나간다.
    return tuple(
        run
        for _, _, run in sorted(
            runs,
            key=lambda item: (item[0], item[1]),
            reverse=True,
        )
    )


def _contains(outer: NormalizedBox, inner: NormalizedBox) -> bool:
    return (
        outer.left - _CONTAINMENT_TOLERANCE <= inner.left
        and outer.top - _CONTAINMENT_TOLERANCE <= inner.top
        and outer.right + _CONTAINMENT_TOLERANCE >= inner.right
        and outer.bottom + _CONTAINMENT_TOLERANCE >= inner.bottom
    )


def annotate_object(
    item: ReferenceImageObject,
    text_regions: Sequence[ReferenceTextRegion],
) -> ReferenceObjectSample:
    """object 하나에 글자 포함 관계와 크롭 후보 표시를 붙인다."""
    inside = tuple(
        region.region_id
        for region in text_regions
        if _contains(item.bbox, region.bbox)
    )
    return ReferenceObjectSample(
        region_id=item.region_id,
        kind=item.kind,
        bbox=item.bbox,
        fill=item.fill,
        edges=item.edges,
        contains_text=bool(inside),
        text_regions_inside=inside,
        crop_candidate=item.fill is None and not inside,
    )


def merged_text_anchor(
    members: Sequence[ReferenceTextRegion],
    *,
    with_crop_paths: bool = False,
) -> ReferenceTextAnchorSample:
    """한 라벨(조각 하나 또는 여럿)을 중심·크기 앵커로 줄인다.

    region_id 는 조각 가운데 가장 넓은 것의 것을 쓴다. 새 id 를 지어내면
    section 에서 되집을 수 없는 이름이 되고, 첫 조각을 쓰면 라벨의 머리 글자
    한 조각만 가리키는 일이 잦다.
    """
    box = merged_text_box(members)
    primary = max(members, key=lambda item: _box_area(item.bbox))
    return ReferenceTextAnchorSample(
        region_id=primary.region_id,
        center_x=round((box.left + box.right) / 2, 6),
        center_y=round((box.top + box.bottom) / 2, 6),
        width=round(box.right - box.left, 6),
        height=round(box.bottom - box.top, 6),
        line_count=max(item.line_count for item in members),
        merged_from_count=len(members),
        crop_paths=(
            tuple(item.crop_path for item in members) if with_crop_paths else ()
        ),
    )


def text_anchor(region: ReferenceTextRegion) -> ReferenceTextAnchorSample:
    """text_region 을 중심·크기 앵커로 줄인다."""
    return merged_text_anchor((region,))
