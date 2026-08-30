# G02 PagePlan / 원본 자산 / 일방향 HTML 목업 계약 v1

이 문서는 G01 관측 결과를 사용해 선택용 A/B 목업을 만드는 규격이다. G02는
G03 분석·스냅, G04 문서 변이, G05 트랜잭션을 구현하지 않는다.

## 헌법

세 입력의 권한은 겹치지 않는다.

| 권한 | 정본 |
| --- | --- |
| 배치·상자·쪽 수 | `PagePlan` |
| 글·표 값과 그림 바이트 | `SourceRegistry`의 원본 source record |
| 글꼴·줄간격·색·정렬 | G01 `StyleRole`/convention observation |
| 사용자에게 보이는 목업 | PagePlan + SourceRegistry의 일방향 HTML |
| HWP compiler 입력 | 구조화 PagePlan과 검증된 SourceRegistry만 |

PagePlan에는 authoritative paragraph text나 table rows가 들어가지 않는다. 문단과
표 블록은 `source_ref`, `content_sha256`, `semantic_role`, `style_role`, `MmBox`만
가진다. renderer가 source registry에서 값을 조회하고 hash를 검증한다.

HTML/SVG/DOM/CSS는 compiler 입력이 아니다. preview validator는 HTML을 해석해
계획을 재구성하지 않고, `<meta name="gsg-compiler-meta">`의 비실행 integrity
payload만 추출·검증한다.

## 입력 억제 규칙

호스트 목업 지시문은 다음을 지킨다.

```text
가이드 안에서만 배치하고 가이드 선은 그리지 않는다.
원본 그림은 비율 유지 contain만 사용한다. 자르거나 늘리지 않는다.
없는 내용·글·도형을 만들지 않는다. 빈 공간은 빈 채로 둔다.
```

생성 그림은 원본 그림·OCR·최종 자산을 대체하지 않는다. capture crop은
`quality="capture_only"`, `final_insertable=false`로만 보관한다. generated preview는
최종 Slot이 될 수 없다.

## 후보

- A: 정확히 1개 PagePlan page와 1개 preview page. 명백히 빽빽해도 내용을 빼지 않는다.
- B: 정확히 2개 PagePlan page와 2개 preview page. 여유가 있는 배치를 보여준다.
- 각 그림에는 관측값 딱지를 붙인다. 예: `그림 폭 약 100.0mm (본문 폭의 1/1.7)`.
- 폭과 body fraction은 G01 body box에서 계산한다. paper, margin, font, style 치수를
  코드에 고정하지 않는다.

## 계약 식별자

```text
gsg.hwp.page-plan.v1
gsg.hwp.imageplan-slot-map.v1
gsg.hwp.source-registry.v1
gsg.hwp.preview.v1
gsg.hwp.preview-meta.v1
```

machine-readable source of truth는 `gsg_hwp_pageplan_g02_v1.schema.json`이며,
`uv run python tools/sync_g02_pageplan_schema.py`가 Pydantic 모델에서 생성한다.
`--check`는 파일이 stale이면 실패한다.

## PagePlan skeleton

```json
{
  "schema": "gsg.hwp.page-plan.v1",
  "candidate": "A",
  "pages": [
    {
      "page": 1,
      "page_setup": "G01 observed PageSetup",
      "body_box": {"left_mm": 18, "top_mm": 25, "width_mm": 174, "height_mm": 247},
      "blocks": [
        {
          "kind": "paragraph",
          "block_id": "body-1",
          "semantic_role": "body",
          "source_ref": "slide11.body[0]",
          "content_sha256": "<source-record hash>",
          "style_role": "body",
          "box": {"left_mm": 25, "top_mm": 30, "width_mm": 150, "height_mm": 15}
        },
        {
          "kind": "image",
          "block_id": "figure-1",
          "slot_id": 1,
          "fit": "contain",
          "box": {"left_mm": 25, "top_mm": 50, "width_mm": 100, "height_mm": 55}
        },
        {
          "kind": "table",
          "block_id": "table-1",
          "semantic_role": "table",
          "source_ref": "slide11.table[0]",
          "content_sha256": "<source-record hash>",
          "style_role": "table",
          "box": {"left_mm": 25, "top_mm": 115, "width_mm": 150, "height_mm": 35}
        }
      ]
    }
  ],
  "preview_count": 1,
  "source_manifest_sha256": "<manifest hash>",
  "style_roles_sha256": "<canonical style-role hash>",
  "slot_map": "gsg.hwp.imageplan-slot-map.v1",
  "g01_observation": "observed revision/setup/convention/body hashes",
  "observed_width_labels": "G01-derived numeric labels",
  "_compiler_meta": {
    "canonical_plan_sha256": "<hash excluding _compiler_meta>",
    "ordered_source_refs": ["slide11.body[0]", "ppt/media/image3.png", "slide11.table[0]"],
    "figure_slot_order": [1],
    "figure_slot_count": 1,
    "page_count": 1,
    "block_count": 3
  }
}
```

`paragraph`에 `text`, `table`에 `rows`, `image`에 `src`, `html`, `css`, `dom`,
component 또는 임의 속성을 추가하면 `extra="forbid"`에서 거부한다.

## SlotMap와 SourceRegistry

Slot ID는 1부터 시작하는 unique contiguous sequence다. `slot_order_sha256`와
`slot_map_sha256`는 canonical JSON으로 다시 계산한다.

```json
{
  "schema": "gsg.hwp.imageplan-slot-map.v1",
  "candidate": "A",
  "slots": [
    {
      "slot_id": 1,
      "kind": "image",
      "source": "ppt/media/image3.png",
      "source_kind": "ppt_media",
      "asset_sha256": "<raw member SHA-256>",
      "quality": "original",
      "final_insertable": true
    },
    {"slot_id": 2, "kind": "text", "source_ref": "slide11.body[0]"},
    {"slot_id": 3, "kind": "table", "source_ref": "slide11.table[0]"}
  ]
}
```

PPTX는 zip의 `ppt/media/...` member bytes를 그대로 추출한다. decode, resize,
re-encode하지 않는다. 추출 직전과 직후 bytes 및 SHA-256이 같아야 한다. 일반 그림도
원본 파일 bytes/hash를 사용한다. 픽셀 유사도나 OCR로 원본을 역추론하지 않는다.

텍스트와 표 값은 SourceRegistry에만 있다. registry의 source record hash와 PagePlan
hash가 다르면 preview/compiler 모두 fail closed한다. convention profile, style role,
raw asset provenance의 canonical hash도 trust boundary에서 재계산·재검증한다.
G04가 구현되기 전까지 이 계약은 최종 HWP를 변경하지 않는다.

## 일방향 preview

`render_pageplan_preview(page_plan, source_registry)`는 다음을 보장한다.

- source image raw bytes를 allowlisted MIME data URI로 embed한다.
- source text/table 값을 renderer-owned escaping으로 출력한다.
- source registry와 plan에는 raw markup/style/script/external URL이 없다.
- CSS mm 위치·크기는 PagePlan/G01 관측값에서 계산하고 style 값은 registered role에서만 온다.
- browser fit은 약 95% 상한의 참고값이며 HWP acceptance truth가 아니다.
- 동일한 canonical PagePlan과 동일한 source bytes는 동일한 HTML bytes/hash를 만든다.
- capture-only 그림은 `캡처 전용 - 원본 자산 필요`를 표시한다.
- `data-fit-policy="contain-original-ratio-no-crop-no-stretch"`를 기록한다.

compiler는 preview artifact의 HTML 문자열, DOM, CSS를 받지 않는다. compiler seam은
`PagePlan + SourceRegistry`만 받고 구조화 입력이면 G03 image analysis를 호출하지
않는다.

## Integrity meta

HTML의 meta payload는 base64 canonical JSON인 비실행 데이터다. payload에는 plan hash,
source manifest hash, style-role hash, ordered source refs, figure slot order/count,
page/block totals, G01 revision/setup/convention/body hashes, HTML content hash가 있다.
Preview renderer는 PagePlan과 SourceRegistry를 다시 canonical dump로 검증한 뒤에만
HTML을 만든다.

HTML hash는 meta attribute 전체를 고정 normalization token으로 치환한 HTML의
SHA-256이다. 따라서 meta 안의 hash와 HTML hash가 순환하지 않는다. meta tag가
두 개이거나, bytes/hash/order/count/plan/source가 바뀌면 검증이 실패한다.

## 실행 명령

```text
uv run pytest -q tests/test_hwp_pageplan_contract.py tests/test_hwp_pageplan_assets.py tests/test_hwp_pageplan_preview.py tests/test_hwp_pageplan_compiler_boundary.py tests/test_hwp_pageplan_real_source.py
uv run python tools/sync_g02_pageplan_schema.py --check
uv run ruff check skills/automate-hancom-documents/scripts/hwp_pageplan_contract.py skills/automate-hancom-documents/scripts/hwp_pageplan_assets.py skills/automate-hancom-documents/scripts/hwp_pageplan_preview.py tools/sync_g02_pageplan_schema.py
uv run basedpyright skills/automate-hancom-documents/scripts/hwp_pageplan_contract.py skills/automate-hancom-documents/scripts/hwp_pageplan_assets.py skills/automate-hancom-documents/scripts/hwp_pageplan_preview.py tools/sync_g02_pageplan_schema.py
```

이 G02 산출물은 MCP resource/catalog, `compatibility-manifest.json`, native code를
등록하거나 수정하지 않는다.