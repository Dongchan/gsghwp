# HWP 적용 데이터 형식

이 플러그인의 `apply` 입력은 `text_fields`와 양식에 미리 정의된 한컴 네이티브 필드를 사용하며 최상위 `fields` 키를 받지 않는다. 본문에 적힌 `{{사업명}}` 같은 자리표시자를 네이티브 필드로 자동 변환하지 않으므로, 적용 전에 대상 HWP/HWPX 양식에 네이티브 필드를 만들어야 한다.

```json
{
  "repeat_pages": [
    {"page": 13, "copies": 2}
  ],
  "text_fields": {
    "사업명": "○○구역 재개발정비사업",
    "sim_01_title": ["조망점 1", "조망점 2", "조망점 3"],
    "sim_01_location": ["조망점 1", "조망점 2", "조망점 3"],
    "sim_01_result": ["분석 1", "분석 2", "분석 3"]
  },
  "image_fields": {
    "sim_01_map": [
      {"path": "images/map-01.png", "mode": "cell-fill", "filloption": 5},
      {"path": "images/map-02.png", "mode": "cell-fill", "filloption": 5},
      {"path": "images/map-03.png", "mode": "cell-fill", "filloption": 5}
    ],
    "표지사진": {
      "path": "images/cover.png",
      "mode": "inline",
      "width_mm": 160,
      "height_mm": 90
    }
  }
}
```

`copies`는 원본 페이지 외에 추가할 복사본 수다. 위 예시는 13쪽을 두 번 더 복제하여 같은 필드가 총 세 세트가 되게 한다.

같은 필드가 여러 개면 값도 같은 개수의 배열이어야 한다. 단일 값을 반복 필드 전체에 묵시적으로 복사하지 않는다.

`image_fields`의 `mode`:

- `cell-fill`: 필드가 들어 있는 표 셀의 배경을 이미지로 채운다. 표 양식에 권장한다.
- `inline`: 필드 위치에 글자처럼 취급되는 그림을 넣는다. 셀 밖이나 표지 이미지에 사용한다.

`filloption`은 한컴 셀 배경 그림 옵션이며 기본값 5는 셀 크기에 맞춤이다. 원본 비율이 중요하면 삽입 전에 대상 셀 비율로 이미지를 크롭한다.

현재 가시권·시뮬레이션 작성본에서 사용할 수 있는 이미지 필드 예시는 다음과 같다.

- `vis_01_analysis_image`, `vis_01_photo`, `vis_02_analysis_image`, `vis_02_photo`
- `sim_01_map`, `sim_01_before`, `sim_01_after`

플러그인용 HWP 사본에는 반복 페이지의 제목 번호를 바꿀 수 있도록 `sim_01_title`, `vis_01_label`, `vis_02_label` 필드도 추가되어 있다.
