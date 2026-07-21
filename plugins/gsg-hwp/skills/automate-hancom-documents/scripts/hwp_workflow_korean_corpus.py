from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from hwp_operation_contract import HwpWorkflowId


type KoreanWorkflowCaseCategory = Literal[
    "baseline",
    "numeral",
    "classifier",
    "target",
    "negation",
    "range",
    "preservation",
    "completion",
    "composite",
    "authority",
    "ambiguous",
]


@dataclass(frozen=True, slots=True)
class KoreanWorkflowNormalization:
    target: str | None = None
    payload: tuple[str, ...] = ()
    policy: tuple[str, ...] = ()
    postconditions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class KoreanWorkflowExample:
    query: str
    expected: HwpWorkflowId | None
    category: KoreanWorkflowCaseCategory = "baseline"
    explicit: HwpWorkflowId | None = None
    normalization: KoreanWorkflowNormalization = KoreanWorkflowNormalization()


_FILL_HINTS: Final = KoreanWorkflowNormalization(
    target="table",
    payload=("records",),
    policy=("preserve_style",),
    postconditions=("verify_structure",),
)
_BLANK_FILL_HINTS: Final = KoreanWorkflowNormalization(
    target="table",
    payload=("records",),
    policy=("fill_blanks_only", "preserve_existing_images"),
    postconditions=("verify_structure",),
)
_COUNTED_FILL_HINTS: Final = KoreanWorkflowNormalization(
    target="table",
    payload=("records=19",),
    policy=("preserve_existing_images",),
    postconditions=("record_count=19", "verify_structure"),
)
_SECOND_TABLE_HINTS: Final = KoreanWorkflowNormalization(
    target="page:9/table:second",
    payload=("records",),
    postconditions=("verify_structure",),
)
_SELECTED_TABLE_HINTS: Final = KoreanWorkflowNormalization(
    target="selection/table",
    payload=("records",),
    postconditions=("verify_structure",),
)
_BELOW_SELECTION_HINTS: Final = KoreanWorkflowNormalization(
    target="below_selection/table",
    payload=("records",),
    postconditions=("verify_structure",),
)
_PRESERVE_PAGE_HINTS: Final = KoreanWorkflowNormalization(
    target="table",
    payload=("records",),
    policy=("preserve_existing_images",),
    postconditions=("preserve_page_count", "verify_structure"),
)
_SERIES_HINTS: Final = KoreanWorkflowNormalization(
    target="table",
    payload=("records", "images"),
    policy=("preserve_style",),
    postconditions=("record_count", "verify_structure"),
)


KOREAN_WORKFLOW_EXAMPLES: Final[tuple[KoreanWorkflowExample, ...]] = (
    KoreanWorkflowExample("이 표 양식을 다섯 장 복제해", "table.repeat_template"),
    KoreanWorkflowExample("현재 표를 같은 모양으로 5세트 뽑아줘", "table.repeat_template"),
    KoreanWorkflowExample("기준표 서식을 유지해서 필요한 개수만큼 반복해", "table.repeat_template"),
    KoreanWorkflowExample("선택한 표 틀을 열아홉 개로 늘려", "table.repeat_template"),
    KoreanWorkflowExample("표 양식을 반복하고 항목과 사진을 함께 채워", "table.build_series"),
    KoreanWorkflowExample("기준표를 레코드 수만큼 만들고 글과 그림을 넣어", "table.build_series"),
    KoreanWorkflowExample("예비조망점마다 같은 표와 내용과 사진을 구성해", "table.build_series"),
    KoreanWorkflowExample("이 표를 여러 세트 복제해서 데이터와 이미지를 채워", "table.build_series"),
    KoreanWorkflowExample("이 표 양식을 그대로 두 장 더 만들고 각 장에 글과 사진을 채워", "table.build_series"),
    KoreanWorkflowExample("행 수가 부족하면 늘린 뒤 목록을 채워", "table.expand_and_fill"),
    KoreanWorkflowExample("레코드 수만큼 표 행을 확장해서 입력해", "table.expand_and_fill"),
    KoreanWorkflowExample("마지막 행 서식을 복제하고 빈 셀을 채워", "table.expand_and_fill"),
    KoreanWorkflowExample("항목 개수에 맞춰 줄을 늘리고 내용을 반영해", "table.expand_and_fill"),
    KoreanWorkflowExample("표 사진칸에 폴더 이미지를 넣어", "table.insert_images"),
    KoreanWorkflowExample("기존 표 셀마다 대응 사진을 배치해", "table.insert_images"),
    KoreanWorkflowExample("현황사진 경로의 그림을 표에 채워", "table.insert_images"),
    KoreanWorkflowExample("가시권분석 이미지를 각 표 칸에 삽입해", "table.insert_images"),
    KoreanWorkflowExample("표의 사진칸 한 곳에 사진 한 장을 채워", "table.insert_images"),
    KoreanWorkflowExample("현재 위치에 그림 파일 하나 넣어", "image.insert"),
    KoreanWorkflowExample("이 페이지에 새 이미지를 삽입해", "image.insert"),
    KoreanWorkflowExample("문서 커서 자리에 사진을 배치해", "image.insert"),
    KoreanWorkflowExample("선택한 그림을 다른 파일로 교체해", "image.replace"),
    KoreanWorkflowExample("골라둔 사진을 새 이미지로 갈아 끼워", "image.replace"),
    KoreanWorkflowExample("지정 그림 파일만 바꿔", "image.replace"),
    KoreanWorkflowExample("표 아래에 캡션을 추가해", "caption.add"),
    KoreanWorkflowExample("선택 그림에 설명 문구를 달아", "caption.add"),
    KoreanWorkflowExample("기존 표 제목을 새 이름으로 변경해", "caption.add"),
    KoreanWorkflowExample("사진 설명 캡션을 입력해", "caption.add"),
    KoreanWorkflowExample("첫 문단 서식을 둘째 문단에 복사해", "style.copy"),
    KoreanWorkflowExample("기준 문단 모양을 대상 문단에 그대로 입혀", "style.copy"),
    KoreanWorkflowExample("원본 스타일을 선택 영역으로 옮겨", "style.copy"),
    KoreanWorkflowExample("선택 문단에 지정 스타일을 적용해", "style.apply"),
    KoreanWorkflowExample("이 문단에 보고서 본문 스타일을 입혀", "style.apply"),
    KoreanWorkflowExample("대상 글에 기존 스타일을 적용해", "style.apply"),
    KoreanWorkflowExample("문서 맨 끝에 표가 있는 새 페이지를 추가해", "document.append_layout"),
    KoreanWorkflowExample("보고서 마지막에 같은 양식 섹션을 이어 붙여", "document.append_layout"),
    KoreanWorkflowExample("문서 뒤쪽에 표와 그림 레이아웃을 덧붙여", "document.append_layout"),
    KoreanWorkflowExample("끝부분에 템플릿 페이지 하나 더 만들어", "document.append_layout"),
    KoreanWorkflowExample("현재 문서 구조와 머리글을 조회해", "document.inspect_structure"),
    KoreanWorkflowExample("문서의 마지막 페이지로 이동해", "document.navigate"),
    KoreanWorkflowExample("문서 구조를 재구성해", "document.rebuild"),
    KoreanWorkflowExample("선택 영역의 글을 다른 문구로 교체해", "document.replace_selection"),
    KoreanWorkflowExample("현재 위치에서 쪽 나누기를 실행해", "document.page_break"),
    KoreanWorkflowExample("새 페이지 하나 추가해", "document.insert_page"),
    KoreanWorkflowExample("9쪽을 삭제해", "document.delete_page"),
    KoreanWorkflowExample("현재 위치에 보고서 제목을 입력해", "text.insert"),
    KoreanWorkflowExample("문서에서 오래된 문구를 새 문구로 바꿔", "text.replace"),
    KoreanWorkflowExample("문단 글자 서식을 적용해", "text.format"),
    KoreanWorkflowExample("현재 쪽의 표 위치를 찾아", "table.inspect"),
    KoreanWorkflowExample("현재 위치에 새 표를 생성해", "table.create"),
    KoreanWorkflowExample("표 테두리와 배경 서식을 적용해", "table.format"),
    KoreanWorkflowExample("표 열 너비를 조절해", "table.resize"),
    KoreanWorkflowExample("선택한 표의 셀을 합쳐", "table.merge_cells"),
    KoreanWorkflowExample("선택한 표 셀을 나눠", "table.split_cells"),
    KoreanWorkflowExample("기준표의 셀 값을 대상 표에 전파해", "table.propagate"),
    KoreanWorkflowExample("CSV 데이터를 기존 표로 가져와", "table.import_data"),
    KoreanWorkflowExample("선택한 그림의 너비를 조절해", "image.resize"),
    KoreanWorkflowExample("선택한 하이퍼링크 주소를 변경해", "hyperlink.modify"),
    KoreanWorkflowExample("표 양식을 3장 더 복제해", "table.repeat_template", "numeral", normalization=KoreanWorkflowNormalization(target="table", payload=("quantity=3", "template"), postconditions=("verify_structure",))),
    KoreanWorkflowExample("표 양식을 세 장 더 복제해", "table.repeat_template", "classifier", normalization=KoreanWorkflowNormalization(target="table", payload=("quantity=3", "template"), postconditions=("verify_structure",))),
    KoreanWorkflowExample("기준표를 두 세트로 반복해", "table.repeat_template", "classifier", normalization=KoreanWorkflowNormalization(target="table", payload=("quantity=2", "template"), postconditions=("verify_structure",))),
    KoreanWorkflowExample("9쪽 표에 19개 항목을 입력해", "table.fill_existing", "numeral", normalization=_COUNTED_FILL_HINTS),
    KoreanWorkflowExample("9쪽 표에 열아홉 건을 입력해", "table.fill_existing", "classifier", normalization=_COUNTED_FILL_HINTS),
    KoreanWorkflowExample("9페이지 표에 두 건의 내용을 채워", "table.fill_existing", "classifier", normalization=KoreanWorkflowNormalization(target="page:9/table", payload=("records=2",), postconditions=("record_count=2", "verify_structure"))),
    KoreanWorkflowExample("선택한 표의 빈칸만 채워", "table.fill_existing", "target", normalization=_SELECTED_TABLE_HINTS),
    KoreanWorkflowExample("9쪽 두 번째 표에 내용을 채워", "table.fill_existing", "target", normalization=_SECOND_TABLE_HINTS),
    KoreanWorkflowExample("선택 영역 아래 표에 내용을 채워", "table.fill_existing", "target", normalization=_BELOW_SELECTION_HINTS),
    KoreanWorkflowExample("선택한 사진을 새 파일로 교체해", "image.replace", "target", normalization=KoreanWorkflowNormalization(target="selection/picture", payload=("asset=image",), postconditions=("verify_structure",))),
    KoreanWorkflowExample("9쪽 두 번째 그림에 캡션을 추가해", "caption.add", "target", normalization=KoreanWorkflowNormalization(target="page:9/picture:second", payload=("caption_text",), postconditions=("verify_structure",))),
    KoreanWorkflowExample("선택 영역 아래 문단에 위 문단 스타일을 복사해", "style.copy", "target", normalization=KoreanWorkflowNormalization(target="below_selection/paragraph", payload=("source_style",), postconditions=("verify_structure",))),
    KoreanWorkflowExample("9쪽 표에서 빈칸만 채워", "table.fill_existing", "negation", normalization=_BLANK_FILL_HINTS),
    KoreanWorkflowExample("기존 사진은 유지하고 표 빈 셀만 채워", "table.fill_existing", "preservation", normalization=_BLANK_FILL_HINTS),
    KoreanWorkflowExample("페이지 수를 늘리지 않고 표를 채워", "table.fill_existing", "negation", normalization=_PRESERVE_PAGE_HINTS),
    KoreanWorkflowExample("행을 늘리지 말고 기존 표의 빈칸만 채워", "table.fill_existing", "negation", normalization=KoreanWorkflowNormalization(target="table", payload=("records",), policy=("fill_blanks_only",), postconditions=("verify_structure",))),
    KoreanWorkflowExample("표 서식은 유지하고 빈칸만 채워", "table.fill_existing", "preservation", normalization=_BLANK_FILL_HINTS),
    KoreanWorkflowExample("기존 그림은 바꾸지 말고 표 글만 채워", "table.fill_existing", "preservation", normalization=KoreanWorkflowNormalization(target="table", payload=("records",), policy=("preserve_existing_images",), postconditions=("verify_structure",))),
    KoreanWorkflowExample("9쪽부터 11쪽까지 표 내용을 채워", "table.fill_existing", "range", normalization=KoreanWorkflowNormalization(target="pages:9-11/table", payload=("records",), postconditions=("verify_structure",))),
    KoreanWorkflowExample("9페이지 표 1번부터 3번 행까지 입력해", "table.fill_existing", "range", normalization=KoreanWorkflowNormalization(target="page:9/table/rows:1-3", payload=("records",), postconditions=("verify_structure",))),
    KoreanWorkflowExample("표 19건을 입력하고 구조 검증을 완료해", "table.fill_existing", "completion", normalization=_COUNTED_FILL_HINTS),
    KoreanWorkflowExample("표 양식을 세 세트 반복하고 개수를 검증해", "table.repeat_template", "completion", normalization=KoreanWorkflowNormalization(target="table", payload=("quantity=3", "template"), postconditions=("record_count=3", "verify_structure"))),
    KoreanWorkflowExample("문서 맨 끝에 새 페이지를 만들고 구조 검증까지 해", "document.append_layout", "completion", normalization=KoreanWorkflowNormalization(target="document/end", payload=("page",), postconditions=("verify_structure",))),
    KoreanWorkflowExample("표 양식을 3세트 만들고 글과 사진을 채워", "table.build_series", "composite", normalization=_SERIES_HINTS),
    KoreanWorkflowExample("9쪽 표의 행을 늘리고 빈칸만 채워", "table.expand_and_fill", "composite", normalization=KoreanWorkflowNormalization(target="page:9/table", payload=("records",), policy=("allow_row_expansion", "fill_blanks_only"), postconditions=("verify_structure",))),
    KoreanWorkflowExample("표 사진칸에 이미지 목록을 채우고 구조를 검증해", "table.insert_images", "composite", normalization=KoreanWorkflowNormalization(target="table", payload=("images",), postconditions=("verify_structure",))),
    KoreanWorkflowExample("문서 맨 끝에 새 페이지를 만들고 표와 캡션을 넣어", "document.append_layout", "composite", normalization=KoreanWorkflowNormalization(target="document/end", payload=("page", "table", "caption"), postconditions=("verify_structure",))),
    KoreanWorkflowExample("선택한 셀을 병합하고 표 서식을 적용해", "table.merge_cells", "composite", normalization=KoreanWorkflowNormalization(target="selection/table/cells", payload=("merge", "format"), postconditions=("verify_structure",))),
    KoreanWorkflowExample("선택한 셀을 나누고 테두리를 적용해", "table.split_cells", "composite", normalization=KoreanWorkflowNormalization(target="selection/table/cells", payload=("split", "format"), postconditions=("verify_structure",))),
    KoreanWorkflowExample("표를 19개 복제하고 내용을 채워", "table.fill_existing", "authority", "table.fill_existing", _COUNTED_FILL_HINTS),
    KoreanWorkflowExample("9쪽 표의 행을 늘리고 빈칸을 채워", "table.fill_existing", "authority", "table.fill_existing", _FILL_HINTS),
    KoreanWorkflowExample("문서 맨 끝에 페이지 하나 추가하고 표를 넣어", "document.append_layout", "authority", "document.append_layout", KoreanWorkflowNormalization(target="document/end", payload=("page", "table"), postconditions=("verify_structure",))),
    KoreanWorkflowExample("문단 서식을 복사하지 말고 적용해", "style.copy", "authority", "style.copy", KoreanWorkflowNormalization(target="paragraph", payload=("style",), postconditions=("verify_structure",))),
    KoreanWorkflowExample("선택한 표 셀을 나누고 모양도 바꿔", "table.merge_cells", "authority", "table.merge_cells", KoreanWorkflowNormalization(target="selection/table/cells", payload=("merge", "format"), postconditions=("verify_structure",))),
    KoreanWorkflowExample("이걸 처리해줘", None, category="ambiguous"),
    KoreanWorkflowExample("아까 그 작업을 계속해", None, category="ambiguous"),
    KoreanWorkflowExample("표 작업해", None, category="ambiguous"),
    KoreanWorkflowExample("그림 작업해", None, category="ambiguous"),
    KoreanWorkflowExample("표와 사진을 처리해", None, category="ambiguous"),
    KoreanWorkflowExample("페이지 작업해", None, category="ambiguous"),
)


def korean_workflow_corpus_text(workflow_id: HwpWorkflowId) -> str:
    return " ".join(
        example.query
        for example in KOREAN_WORKFLOW_EXAMPLES
        if example.expected == workflow_id
    )
