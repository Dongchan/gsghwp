"""레퍼런스 이미지 분석의 시간 예산과 사전 비용 예측.

이 모듈은 캔버스도 PIL도 모르는 순수 계산·시계 계층이다. 지배 루프가 있는
모듈(hwp_reference_image_segments·segment_filter·edge_drawing·fills·objects·
gaps·text)이 여기를 import 하므로, 반대 방향 import 는 순환이 된다 — 픽셀을
만지는 코드는 두지 않는다.

== 왜 예산이 필요한가 ==
MCP 작업자 호출은 hwp_mcp_worker_deadline.DEFAULT_CALL_TIMEOUT_SECONDS = 240초
에서 잘린다. 분석이 그 마감을 넘기면 호출자는 결과를 하나도 못 받고, 실측에서
전체 실행 5분 30초 중 240초(73%)를 아무 산출 없이 태운 사례가 있다. 분석이
스스로 마감을 지키고 "여기까지가 싼 증거이며 나머지는 모델이 직접 이미지를
읽는 편이 낫다"고 정직하게 말하는 편이 언제나 낫다.

== 상수 산출 근거 (2026-08-29 재교정, 이 저장소 .venv, 합성 픽스처 15종 실측) ==
픽셀 재측정 경로를 배열 연산으로 바꾸면서 비용 구조 자체가 달라졌다. 옛 상수를
그대로 두면 이제 몇 초에 끝나는 이미지를 "예산 초과"로 잘못 끊는다 — 실측으로
1600x1200 잔모자이크(원시 선분 124,511개)가 옛 식에서 237.2초로 예측되는데 실제
완주 시간은 8.7초다. 그래서 아래 값들을 새 구현 기준으로 다시 맞췄다.

DEFAULT_ANALYSIS_TIME_BUDGET_SECONDS = 180초 (2026-08-29 재산정)
  - 옛 150초는 "스캔이 끝난 뒤에 남는 뒷일"의 여유를 어림으로 90초 잡은 값이다.
    그 뒷일 가운데 refine·detect_* 는 실제로는 예산이 묻지 않는 구간이었고,
    실측으로 전체 시간의 39~58%만 예산 안에 있었다(1600x1200 잔모자이크 45.5%,
    1400x1000 빗금 39.0%, 1240x1754 선화 58.1%). 예산 4.0초를 주면 총 벽시계가
    5.5~7.0초(1.24~1.75배)까지 갔다. 이제 그 단계들이 예산 안으로 들어왔고,
    마감 계산에 남는 것은 마지막 체크 뒤의 고정비뿐이다.
  - 그 고정비를 분석 상한인 8MP에서 쟀다: compile_* 계약 변환과 타일·콘택트
    시트·오버레이 저장 0.28~0.74초, compact 요약과 산출물 sha256 0.01~0.05초,
    캐시 정리와 이름 바꾸기 0.03초 — 합쳐 0.9초를 넘지 않는다.
  - 폴링 사이의 최대 비분할 구간도 같은 8MP에서 쟀다: 텍스트 마스크 0.49~0.68초,
    edge-drawing 선 검출 0.34~0.38초, 사전 게이트 0.15~0.16초, 축 배열 준비와
    색 양자화 각 0.04초. 예산이 초과를 알아채는 지연은 그 최대값이다.
  - 그래서 240초 마감에서 실제로 필요한 여유는 1.6초다. 60초를 남기는 180초는
    그 37배이고, 이 기계보다 한참 느린 호스트에서도 뒷일이 마감을 먹지 않는다.
    150초를 그대로 두는 것은 근거 없는 90초를 능력에서 깎는 일이다.
  - 하한은 그대로 "완주가 실측된 부하를 통과시킬 것": 예전에 90.6초 걸리던
    1240x1754 격자 이미지가 이제 5.3초에 끝나고, 예산이 전 구간을 묻게 된 뒤에도
    분석 상한인 8MP 잔모자이크가 18.8초, 7.9MP 선화가 13.8초로 완주한다.
    180초는 그 9.6배다.
ANALYSIS_SCAN_TAIL_MULTIPLIER = 1.30, ANALYSIS_SEGMENT_SECONDS = 2.0e-5
  - 전체 시간 = 1.30 x 선분 스캔 시간 + 2.0e-5 x (원시 선분 수) 로 맞췄다.
  - 뒷항이 제곱에서 1차로 바뀐 것은 알고리즘이 바뀌었기 때문이다.
    filter_layout_segments 가 위치값 통을 쓰면서 선분 수에 대해 제곱이 아니게
    됐고(실측 선분 47,840개에서 1.37초 -> 0.05초), 남은 선분 의존 비용은
    edge-drawing 다듬기와 물체 검출이라 대체로 1차다. 실측으로 선분 43,627개의
    초과분이 3.5초, 124,511개의 초과분도 3.5초로 사실상 평평하다. 제곱 항을
    남기면 선분이 많은 그림마다 없는 비용을 지어내 완주할 작업을 끊는다.
  - 픽스처별 1차 계수는 2.8e-5 ~ 8.0e-5 로 흩어졌다. 그 아래쪽을 고른 이유는
    백스톱이 있기 때문이다. 과소예측은 (b) 협조 예산이 받아내므로 손해가
    "느림"에서 끝나지만, 과대예측은 완주할 작업을 멈춰 능력을 깎는다.
  - 15개 픽스처 교차검증: 사진·격자·선화처럼 실제로 쓰이는 부하에서 오차는
    -9.0% ~ -0.9% 다. 가장 크게 과대예측하는 지점은 선분만 많고 남는 것은 없는
    1600x1200 빗금(+10.1%)이다. 앞항을 1.35 로 올리면 그 지점이 +14.0% 로
    커져서 1.30 을 골랐다. 선분이 많은 부하(-41%)와 잔모자이크 텍스트 폭주
    (-82%)는 과소예측이지만 실측 6.5~47.3초로 예산에서 한참 멀다.
SCAN_ESTIMATE_SAMPLE_LINES = 24 (변경 없음)
  - 후보 선(가로·세로)에서 24개씩 균등 추출해 실제 스캔 코드를 돌리고 전체로
    선형 외삽한다. 이 표본으로 스캔 시간 예측 오차는 15개 픽스처에서 -5.3% ~
    +8.6%(대부분 +-5% 안)였고, 게이트 자체 비용은 0.014~0.143초다.
  - 표본을 재기 전에 축 배열을 미리 세운다. 그 준비는 선 수와 무관한 한 번짜리
    비용이라 표본 안에서 재면 배율만큼 뻥튀기된다 — 실측으로 8MP에서 0.024초가
    배율 136 을 타고 3.3초의 허수가 되어 예측이 +34% 로 부풀었다.
  - 픽셀 수 같은 정적 지표로는 가를 수 없다는 것이 여전히 실측이다: 같은 8MP
    에서도 사진은 14.3초, 잔모자이크는 20.1초다. 그래서 게이트는 그 이미지에서
    실제 스캔 코드를 조금 돌려 재는 방식을 쓴다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal, final


ReferenceAnalysisStopReason = Literal[
    "predicted_over_deadline",
    "time_budget_exhausted",
]

DEFAULT_ANALYSIS_TIME_BUDGET_SECONDS: Final = 180.0
ANALYSIS_SCAN_TAIL_MULTIPLIER: Final = 1.30
ANALYSIS_SEGMENT_SECONDS: Final = 2.0e-5
SCAN_ESTIMATE_SAMPLE_LINES: Final = 24


@dataclass(frozen=True, slots=True)
class ReferenceScanEstimate:
    """후보 선 표본을 실제로 스캔해 얻은 전체 비용 예측."""

    horizontal_candidates: int
    vertical_candidates: int
    sampled_lines: int
    sample_seconds: float
    scan_seconds: float
    segment_count: float

    @property
    def predicted_seconds(self) -> float:
        return predicted_analysis_seconds(self.scan_seconds, self.segment_count)


def predicted_analysis_seconds(scan_seconds: float, segment_count: float) -> float:
    return (
        ANALYSIS_SCAN_TAIL_MULTIPLIER * scan_seconds
        + ANALYSIS_SEGMENT_SECONDS * segment_count
    )


def sample_positions(
    positions: tuple[int, ...],
    sample_lines: int,
) -> tuple[int, ...]:
    total = len(positions)
    if total <= max(2, sample_lines):
        return positions
    last = total - 1
    picked = sample_lines - 1
    return tuple(
        positions[round(index * last / picked)] for index in range(sample_lines)
    )


@final
class ReferenceAnalysisTimeBudget:
    """협조적 소프트 예산. 지배 루프가 바깥 회전마다 물어본다.

    강제 취소가 아니라 협조 방식인 이유는, 분석이 중간에 죽으면 부분 산출물이
    캐시에 남아 다음 호출이 그것을 완전한 증거로 착각할 수 있기 때문이다.
    루프가 스스로 빠져나오면 호출자가 "미완"이라고 표시된 결과를 만들어
    캐시에 쓰지 않고 돌려줄 수 있다.
    """

    __slots__ = ("_seconds", "_clock", "_started", "_stop_reason")

    def __init__(
        self,
        seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if seconds <= 0:
            raise ValueError("reference image analysis time budget must be positive")
        self._seconds = seconds
        self._clock = clock
        self._started = clock()
        self._stop_reason: ReferenceAnalysisStopReason | None = None

    @property
    def seconds(self) -> float:
        return self._seconds

    @property
    def stop_reason(self) -> ReferenceAnalysisStopReason | None:
        return self._stop_reason

    @property
    def stopped(self) -> bool:
        return self._stop_reason is not None

    def elapsed_seconds(self) -> float:
        return self._clock() - self._started

    def stop(self, reason: ReferenceAnalysisStopReason) -> None:
        if self._stop_reason is None:
            self._stop_reason = reason

    def exhausted(self) -> bool:
        if self._stop_reason is not None:
            return True
        if self.elapsed_seconds() < self._seconds:
            return False
        self._stop_reason = "time_budget_exhausted"
        return True
