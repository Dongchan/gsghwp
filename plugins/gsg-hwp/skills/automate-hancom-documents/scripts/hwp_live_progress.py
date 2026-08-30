from __future__ import annotations

import threading
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from time import monotonic
from typing import final

# 한컴 COM 호출을 기다리는 스레드는 작업 스레드가 지금 살아 있는지 알 방법이
# 벽시계밖에 없었다. 그래서 오래 걸리지만 멀쩡히 진행 중인 작업과, 첫 명령에서
# 멈춰 버린 진짜 행(hang)이 똑같이 "제한시간 초과"로 죽었다.
#
# 여기서 파는 것은 세 가지 사실뿐이다.
#   1) 작업 스레드가 레인에서 실제로 돌기 시작했다 (큐·락 대기가 끝난 시점).
#   2) 작업 스레드가 방금 무언가를 끝냈다 — 무엇을, 얼마나 끝냈는지 이름표와
#      누적 수치로 말한다.
#   3) 지금부터 돌릴 구간이 얼마나 큰지 (네이티브 호출 한 번은 안에서 진행을
#      알릴 수 없으므로, 들어가기 전에 크기를 선언해 둔다).
#
# 그 사실을 어떻게 쓸지 — 마감을 언제부터 재는지, 작업량을 몇 초로 환산하는지,
# 상한을 얼마로 두는지, 워치독을 다시 무장할지 — 는 전부 브리지가 판단한다.
# 이 모듈에는 정책이 한 줄도 없다.


@dataclass(frozen=True, slots=True)
class NativeWorkDeclaration:
    """지금부터 돌릴 네이티브 구간의 크기.

    ``topology_units`` 는 레이아웃 분할기가 쓰는 표 위상 재구축 비용 단위이고,
    ``commands`` 는 그 구간의 명령 수다. 둘 다 계획에서 나온 값이며 초가
    아니다 — 초로 바꾸는 환산은 이 모듈의 일이 아니다.
    """

    topology_units: int
    commands: int
    label: str


@dataclass(frozen=True, slots=True)
class NativeProgressMark:
    """대기 스레드가 읽는 진행 표식."""

    count: int
    at: float
    label: str
    completed_units: int
    started_at: float
    declaration: NativeWorkDeclaration | None


@final
class NativeProgressClock:
    """작업 스레드가 알린 진행을 대기 스레드가 읽는 창구.

    쓰는 쪽(레인 작업 스레드)과 읽는 쪽(호출을 기다리는 스레드)이 다르므로
    갱신은 자물쇠 아래에서 한 묶음으로 한다. 값들이 서로 어긋난 표식을 읽으면
    "어디까지 갔는지"를 잘못 보고하게 된다.
    """

    __slots__ = (
        "_at",
        "_completed_units",
        "_count",
        "_declaration",
        "_label",
        "_lock",
        "_started_at",
    )

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count = 0
        self._at = 0.0
        self._label = ""
        self._completed_units = 0
        self._started_at = 0.0
        self._declaration: NativeWorkDeclaration | None = None

    def mark_execution_start(self) -> None:
        with self._lock:
            if self._started_at == 0.0:
                self._started_at = monotonic()

    def declare(self, declaration: NativeWorkDeclaration) -> None:
        with self._lock:
            self._declaration = declaration

    def beat(self, label: str, completed_units: int = 0) -> None:
        with self._lock:
            self._count += 1
            self._at = monotonic()
            self._label = label
            if completed_units > self._completed_units:
                self._completed_units = completed_units
            # 끝난 구간의 선언은 더 이상 유효하지 않다. 지우지 않으면 그
            # 허용치가 뒤따르는 구간(스냅샷 재관측·꼬리 정리·검증)까지
            # 물려져, 아무것도 선언하지 않은 그 구간이 최대 천장까지
            # 기다리게 된다. 다음 구간이 자기 크기를 새로 선언하기 전까지는
            # 기본 마감으로 돌아간다.
            self._declaration = None

    def mark(self) -> NativeProgressMark:
        with self._lock:
            return NativeProgressMark(
                self._count,
                self._at,
                self._label,
                self._completed_units,
                self._started_at,
                self._declaration,
            )


_sink_state = threading.local()


@contextmanager
def native_progress_sink(clock: NativeProgressClock) -> Generator[None, None, None]:
    """이 스레드에서 알리는 진행을 ``clock`` 으로 보낸다.

    중첩될 수 있다(레인 작업이 다시 브리지를 부르는 경우). 빠져나갈 때 바깥
    창구를 그대로 돌려놓는다.
    """

    previous: NativeProgressClock | None = getattr(_sink_state, "clock", None)
    _sink_state.clock = clock
    try:
        yield
    finally:
        _sink_state.clock = previous


def _current_clock() -> NativeProgressClock | None:
    return getattr(_sink_state, "clock", None)


def declare_native_work(
    *,
    topology_units: int,
    commands: int,
    label: str,
) -> None:
    """지금부터 들어갈 네이티브 구간의 크기를 미리 알린다.

    네이티브 호출 한 번은 그 안에서 진행을 알릴 수 없다. 들어가기 전에 크기를
    말해 두어야, 기다리는 쪽이 "이 정도면 아직 일하는 중"과 "멈췄다"를 구분할
    근거를 갖는다.
    """

    clock = _current_clock()
    if clock is not None:
        clock.declare(NativeWorkDeclaration(topology_units, commands, label))


def report_native_progress(label: str, completed_units: int = 0) -> None:
    """지금 스레드의 창구에 "여기까지 끝냈다"를 알린다.

    창구가 없으면(브리지 밖에서 도는 코드) 조용히 아무것도 하지 않는다.
    진행 보고 때문에 실행이 실패해서는 안 된다.
    """

    clock = _current_clock()
    if clock is not None:
        clock.beat(label, completed_units)
