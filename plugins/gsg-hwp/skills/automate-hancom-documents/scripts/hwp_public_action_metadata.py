from typing import Final


INSERT_IMAGE_INTENT: Final = "현재 선택 위치 또는 문서 끝에 그림 삽입"
REPLACE_IMAGE_INTENT: Final = "현재 선택 그림 교체"
ADD_CAPTION_INTENT: Final = "현재 선택 표 또는 그림에 캡션 추가"
APPLY_STYLE_INTENT: Final = "현재 선택 영역에 스타일 적용"
FORMAT_TEXT_INTENT: Final = "현재 선택 영역 글자 서식"
APPEND_LAYOUT_INTENT: Final = "문서 끝 레이아웃 추가"
INSERT_LAYOUT_INTENT: Final = "현재 커서 또는 지정 쪽 다음 레이아웃 삽입"
SAVE_REOPEN_VERIFY_INTENT: Final = "문서 저장 재개방 검증"
DELETE_PAGE_INTENT: Final = "지정한 한컴 쪽 삭제"
DELETE_CONTROL_INTENT: Final = "지정한 기존 한컴 개체 삭제"
UNDO_INTENT: Final = "직전 한컴 작업 실행 취소 롤백"
REDO_INTENT: Final = "취소한 한컴 작업 다시 실행"

HWP_FILL_TABLE_DESCRIPTION: Final = (
    "기존 HWP 표에 데이터를 입력하며 행은 추가하지 않습니다. "
    "값이 머리글에 대응하면 처음부터 끝까지 records를 사용하고, "
    "cells는 검증된 셀 주소가 있을 때 사용합니다. rows는 start_cell을 명시하거나, 생략하고 현재 한컴 표 셀·연속 셀 블록·선택 표를 시작 기준으로 사용할 수 있습니다. "
    "headers는 대상 표에 실제 표시된 머리글만 받는 선택 필터이며 records 키 목록이 아닙니다. "
    "쪽으로 대상이 충분히 좁혀지면 추측한 headers 없이 page만 사용합니다. "
    "needs_target 응답의 ID는 이 도구의 직접 target_id 필드에 넣고 입력 형식은 바꾸지 않습니다. "
    "대상 표가 여러 개면 실제 후보를 반환합니다."
)
HWP_INSERT_IMAGE_DESCRIPTION: Final = (
    "현재 선택 위치 또는 문서 끝에 그림 파일을 1mm 이상 지정 크기로 삽입합니다. "
    "기존 그림을 바꾸는 요청은 hwp_replace_image를 사용합니다."
)
HWP_REPLACE_IMAGE_DESCRIPTION: Final = (
    "현재 선택했거나 hwp_inspect_page_fast가 반환한 instance_id 또는 앞선 후보 target_id로 그림을 교체합니다. "
    "대상 그림이 여러 개면 실제 후보를 반환합니다."
)
HWP_ADD_CAPTION_DESCRIPTION: Final = (
    "현재 선택했거나 hwp_inspect_page_fast가 반환한 instance_id 또는 앞선 후보 target_id로 "
    "표·그림의 캡션을 추가하거나 기존 캡션을 같은 문서 서식으로 갱신합니다. "
    "대상이 여러 개면 실제 후보를 반환합니다."
)
HWP_APPLY_STYLE_DESCRIPTION: Final = (
    "현재 선택 영역에 문서 스타일 ID를 적용합니다. "
    "개별 글자·문단 속성을 지정하는 요청은 hwp_format_text를 사용합니다."
)
HWP_FORMAT_TEXT_DESCRIPTION: Final = (
    "현재 선택 영역에 지정한 글자·문단 서식을 적용합니다. "
    "표 셀 서식은 hwp_format_table을 사용합니다."
)
HWP_REPLACE_SELECTED_TEXT_DESCRIPTION: Final = (
    "현재 사용자가 드래그한 선택 영역의 전체 텍스트를 지정한 문자열로 교체합니다. "
    "replacement에 빈 문자열을 전달하면 선택한 글을 삭제합니다. "
    "선택 좌표와 원문은 도구 내부에서 즉시 읽고 C++/ATL이 실행 직전에 다시 검증합니다."
)
HWP_INSERT_LAYOUT_DESCRIPTION: Final = (
    "현재 열린 HWP의 본문 중간에 편집 가능한 문단·표·개별 그림을 삽입합니다. "
    "layout.target=current는 사용자가 둔 현재 커서에 삽입하고, "
    "target=after_page와 page=N은 N쪽 끝에서 쪽 나누기를 실행해 N쪽 다음 새 쪽에 삽입합니다. "
    "target=document_end도 지원하며 after_page는 내용을 앞뒤 쪽 나누기로 격리하므로 별도 page_break 블록을 넣지 않습니다. "
    "이미지를 편집 가능한 표로 재구성하기 전에는 MCP 리소스 "
    "gsg-hwp-beta://reference/native-layout을 읽고, 사진·지도·도면만 그림으로 유지합니다. "
    "표는 border_mode=explicit와 명시 행 높이를 사용해 원본에 없는 테두리와 자동 행 확장을 막습니다."
)
HWP_APPEND_LAYOUT_DESCRIPTION: Final = (
    "현재 열린 HWP의 문서 끝에 편집 가능한 문단·표·개별 그림 레이아웃을 추가합니다. "
    "문서의 기존 본문·제목·표타이틀·표내용·그림타이틀 스타일과 실제 본문 폭을 자동으로 따릅니다. "
    "실제 1mm 표 셀은 해당 셀에 font_size_pt=1, line_spacing_percent=50, 0mm padding을 함께 지정합니다. "
    "이미지 속 양식을 재생성할 때 사진·지도·도면만 개별 그림으로 두고 나머지는 적응형 네이티브 표로 구성합니다. "
    "시각 격자는 border_mode=explicit로 지정해 빈 셀 테두리를 없애고, 연결선은 채운 셀이 아니라 실제 셀 경계로 선언합니다. "
    "네모 표 안 그림은 image.container=table_cell로 지정합니다."
    " 본문 중간이나 특정 쪽 다음 삽입은 hwp_insert_layout을 사용합니다."
)
HWP_APPEND_REPORT_DESCRIPTION: Final = (
    "텍스트·항목·레코드·그림을 편집 가능한 한컴 문단·표·표 안 그림으로 재구성해 보고서 끝에 추가합니다. "
    "열별 최소 가독 폭과 행 높이를 계산하고 넓은 표는 식별 열을 반복해 의미 단위로 나눕니다. "
    "figures는 기본적으로 테두리가 있는 1×1 표 셀 안에 배치하며 기존 문서 스타일과 본문 폭을 따릅니다."
)
HWP_APPEND_EXCEL_TABLE_DESCRIPTION: Final = (
    "Excel 범위의 바깥쪽 빈 행·열만 제거하고 원본 병합·채움·테두리·행 높이·열 비율을 존중해 "
    "편집 가능한 한컴 표로 정리합니다. 열별 최소 가독 폭을 확보할 수 없는 병합 표는 "
    "억지로 압축하지 않고 의미 단위 범위 분할이 필요하다고 반환합니다."
)
HWP_SAVE_REOPEN_VERIFY_DESCRIPTION: Final = (
    "현재 문서를 저장하고 같은 경로로 다시 열어 구조를 검증합니다. "
    "명시적인 저장·재개방 검증 요청에만 사용합니다."
)
HWP_DELETE_PAGE_DESCRIPTION: Final = (
    "현재 열린 HWP에서 지정한 실제 쪽 하나를 삭제하고 페이지 수가 하나 줄었는지 검증합니다. "
    "빈 페이지 제거·쪽 삭제 요청에 바로 사용합니다."
)
HWP_DELETE_CONTROL_DESCRIPTION: Final = (
    "hwp_inspect_page_fast가 반환한 ID로 기존 표·그림·도형·텍스트 상자 개체를 하나 이상 삭제하고 "
    "빠른 구조에서 제거를 검증합니다. 잘못 만든 독립 표들을 지운 뒤 한 표로 재구성할 때 사용합니다."
)
HWP_UNDO_DESCRIPTION: Final = "현재 열린 HWP의 직전 편집을 1~20단계 실행 취소합니다. 되돌리기·롤백·방금 작업 취소 요청에 바로 사용합니다."
HWP_REDO_DESCRIPTION: Final = "현재 열린 HWP에서 취소한 편집을 1~20단계 다시 실행합니다. 재실행·되살리기 요청에 바로 사용합니다."
