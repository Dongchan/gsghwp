"""슬롯 클릭 실행기와 워커 하트비트 (한컴MCP 탭 Stage 2).

두 방향이 한 루프 안에 있습니다.

- **하트비트(워커 → DLL)**: 살아 있는 동안 주기적으로 각 한/글의 공유메모리에 tick을
  씁니다. `UpdateUI`가 그 tick의 나이로 슬롯 버튼의 활성/비활성을 정합니다. 워커가
  죽거나 멈추면 tick이 늙고 버튼이 회색이 됩니다 — 오류 팝업도 안내 문구도 필요
  없습니다.
- **클릭(DLL → 워커)**: `DoAction`이 슬롯 AID를 받으면 링 버퍼에 적고 이벤트를
  셋합니다. 이 루프가 그 이벤트에서 깨어나 링을 훑고, 슬롯 번호로 레시피를 찾아
  **기존 공개 도구 실행 경로**에 그대로 태웁니다.

발견 주기는 리본 탭의 방아쇠이기도 합니다. 새 한/글에 채널을 연 직후 `on_attach`가
불리고, 그 훅이 레지스트리 내용대로 탭을 세웁니다 — 그래서 등록된 액션이 0개여도
탭이 서고, 한/글을 다시 켜면 새 PID를 발견하는 즉시 탭이 되살아납니다. 훅은 잠금
밖에서 불리고, 터져도 이 루프를 멈추지 않습니다.

실행 경로를 새로 만들지 않는 것이 핵심입니다. 레시피 스텝은 결국 공개 MCP 도구
호출이고, 그 경로에는 이미 세션 스코프·검증 델리게이트·오퍼레이션 저널·부분 변이
포렌식이 붙어 있습니다. 버튼 클릭이 그 계층을 우회하면 안 됩니다. 그래서 실행기는
도구 이름과 인자를 `ForwardingFastMCP.call_unconverted_tool`에 넘길 뿐이고, 넘기기
전에 저장 시점과 **같은 허용목록**으로 한 번 더 겁니다(저장 이후에 도구가 사라지거나
파일이 변조됐을 수 있으므로).

Windows 핸들 대기는 블로킹이라 전용 스레드에서 돕니다. 실행만 이벤트 루프로
돌아옵니다.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Final, Literal, Protocol, final

from anyio import CancelScope, create_task_group, to_thread
from pydantic import BaseModel, ConfigDict, JsonValue

from hwp_custom_action_bridge import (
    BridgeSnapshot,
    BridgeStatusChannel,
    clamp_stale_window,
    close_handle,
    create_stop_event,
    elapsed_ticks,
    set_event,
    tick_count,
    wait_for_any,
)
from hwp_custom_action_binding import ClickClaimLedger, SlotBindingStore, locked as _locked
from hwp_custom_action_store import (
    CUSTOM_ACTION_SLOT_COUNT,
    CustomAction,
    CustomActionStore,
    CustomActionStoreError,
)
from hwp_official_api_processes import capture_hwp_process_ids


# 하트비트는 신선도 창의 1/4쯤으로 뛴다. 한 번 걸러도, 두 번 걸러도 버튼은 살아
# 있는다 — 잠깐의 GC나 긴 도구 호출로 버튼이 깜빡이면 못 쓴다.
DEFAULT_BEAT_INTERVAL_MS: Final = 2000
DEFAULT_STALE_AFTER_MS: Final = 8000
# tasklist는 프로세스를 띄운다. 매 박동마다 부를 일이 아니다.
DEFAULT_DISCOVERY_INTERVAL_MS: Final = 10000
MAX_RECENT_OUTCOMES: Final = 20
JOURNAL_FILE_NAME: Final = "click-journal.jsonl"
MAX_JOURNAL_BYTES: Final = 256 * 1024
JOURNAL_TRIM_LINES: Final = 200
_NO_BINDING_HINT: Final = (
    " (배치 기록 없음 — 리본이 아직 구성되지 않았습니다)."
)

type ClickStatus = Literal[
    # 저널에만 남는다. 실행 직전에 적어, 워커가 도중에 죽어도 "무엇을 하다 죽었나"가
    # 남게 한다 — started만 있고 종결 줄이 없으면 그 클릭에서 죽은 것이다.
    "started",
    "executed",
    "unmapped",
    "unauthorized",
    "failed",
    "dropped",
    "registry_unreadable",
    # 클레임 원장을 쓰지 못해 실행권을 확정할 수 없었다. 실행하지 않는 것이 옳지만
    # 조용히 넘기면 사용자가 누른 클릭이 흔적 없이 사라진다 — 그래서 결과로 남긴다.
    "claim_unavailable",
]

class BridgeChannelLike(Protocol):
    """`BridgeStatusChannel`이 실물이고, 시험은 같은 모양의 대역을 넣는다."""

    @property
    def click_event(self) -> int: ...

    def open(self) -> bool: ...

    def close(self) -> None: ...

    def read(self) -> BridgeSnapshot | None: ...

    def write_heartbeat(self, *, worker_pid: int, stale_after_ms: int) -> bool: ...

    def clear_heartbeat(self) -> bool: ...


type StepDispatch = Callable[[str, Mapping[str, JsonValue]], Awaitable[JsonValue]]
type ProcessScanner = Callable[[], frozenset[int]]
type ChannelFactory = Callable[[int], BridgeChannelLike]
# 한/글 하나에 붙었다/떨어졌다. 붙는 순간이 리본 탭을 세울 자리다.
type ProcessHook = Callable[[int], None]


class _RuntimeModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class SlotClickSignal(_RuntimeModel):
    process_id: int
    slot_index: int
    sequence: int
    tick: int
    # 이 클릭 앞에서 링이 덮어써 잃어버린 클릭 수. 0이 정상이다.
    dropped_before: int = 0
    # 클레임 원장을 쓰지 못했다. 실행하지 않고 결과만 남긴다.
    claim_failed: bool = False
    # claim_failed일 때 확정하지 못한 클릭 수.
    unclaimed: int = 0


class ClickOutcome(_RuntimeModel):
    """클릭 하나의 결말. 저널 한 줄이자 보고 한 줄이다."""

    process_id: int
    slot_index: int
    sequence: int
    status: ClickStatus
    action_id: str | None = None
    label: str | None = None
    step_count: int = 0
    completed_steps: int = 0
    started_at: datetime
    finished_at: datetime
    message: str | None = None


class BridgeTargetState(_RuntimeModel):
    process_id: int
    attached: bool
    bridge_version: int = 0
    supports_slots: bool = False
    slot_count: int = 0
    slot_click_count: int = 0
    last_slot_index: int = -1
    # DLL이 마지막 UpdateUI에서 내린 판정. 회색화가 실제로 걸렸는지 보는 창이다.
    slot_ui_enabled: bool = False
    heartbeat_age_ms: int | None = None
    processed_sequence: int = 0


class CustomActionRuntimeState(_RuntimeModel):
    running: bool
    worker_pid: int
    beat_interval_ms: int
    stale_after_ms: int
    slot_count: int = CUSTOM_ACTION_SLOT_COUNT
    targets: tuple[BridgeTargetState, ...] = ()
    recent_outcomes: tuple[ClickOutcome, ...] = ()
    journal_path: str | None = None


def _now() -> datetime:
    return datetime.now(UTC)


@final
class CustomActionClickRuntime:
    """슬롯 클릭을 레시피 실행으로 바꾸고, 살아 있음을 알리는 루프."""

    __slots__ = (
        "_beat_interval_ms",
        "_bindings",
        "_channel_factory",
        "_channels",
        "_claims",
        "_cursors",
        "_discovery_interval_ms",
        "_dispatch",
        "_heartbeat_stop",
        "_heartbeat_thread",
        "_journal_lock_path",
        "_journal_path",
        "_last_discovery_tick",
        "_lock",
        "_on_attach",
        "_on_detach",
        "_process_scanner",
        "_recent",
        "_running",
        "_snapshots",
        "_stale_after_ms",
        "_stop_event",
        "_stopped",
        "_store",
        "_worker_pid",
    )

    def __init__(
        self,
        store: CustomActionStore,
        dispatch: StepDispatch,
        *,
        beat_interval_ms: int = DEFAULT_BEAT_INTERVAL_MS,
        stale_after_ms: int = DEFAULT_STALE_AFTER_MS,
        discovery_interval_ms: int = DEFAULT_DISCOVERY_INTERVAL_MS,
        process_scanner: ProcessScanner = capture_hwp_process_ids,
        channel_factory: ChannelFactory = BridgeStatusChannel,
        journal_path: Path | None = None,
        on_attach: ProcessHook | None = None,
        on_detach: ProcessHook | None = None,
    ) -> None:
        self._store = store
        self._dispatch = dispatch
        self._on_attach = on_attach
        self._on_detach = on_detach
        self._beat_interval_ms = max(100, beat_interval_ms)
        self._stale_after_ms = clamp_stale_window(stale_after_ms)
        self._discovery_interval_ms = max(0, discovery_interval_ms)
        self._process_scanner = process_scanner
        self._channel_factory = channel_factory
        self._journal_path = (
            store.path.with_name(JOURNAL_FILE_NAME)
            if journal_path is None
            else journal_path
        )
        root = store.path.parent
        self._bindings = SlotBindingStore(root)
        self._claims = ClickClaimLedger(root)
        self._journal_lock_path = self._journal_path.with_name(
            f".{self._journal_path.name}.lock"
        )
        self._worker_pid = os.getpid()
        self._heartbeat_thread: threading.Thread | None = None
        self._heartbeat_stop = threading.Event()
        self._channels: dict[int, BridgeChannelLike] = {}
        self._cursors: dict[int, int] = {}
        self._snapshots: dict[int, BridgeSnapshot] = {}
        self._recent: list[ClickOutcome] = []
        self._lock = threading.Lock()
        self._stop_event = 0
        self._stopped = False
        self._running = False
        self._last_discovery_tick: int | None = None

    # --- 상태 보고 ---------------------------------------------------------

    def state(self) -> CustomActionRuntimeState:
        with self._lock:
            snapshots = dict(self._snapshots)
            cursors = dict(self._cursors)
            attached = set(self._channels)
            recent = tuple(self._recent[-MAX_RECENT_OUTCOMES:])
            running = self._running
        now = tick_count()
        targets = tuple(
            BridgeTargetState(
                process_id=process_id,
                attached=process_id in attached,
                bridge_version=snapshot.version,
                supports_slots=snapshot.supports_slots,
                slot_count=snapshot.slot_count,
                slot_click_count=snapshot.slot_click_count,
                last_slot_index=snapshot.last_slot_index,
                slot_ui_enabled=bool(snapshot.slot_ui_enabled),
                heartbeat_age_ms=(
                    None
                    if snapshot.heartbeat_tick == 0
                    else elapsed_ticks(now, snapshot.heartbeat_tick)
                ),
                processed_sequence=cursors.get(process_id, 0),
            )
            for process_id, snapshot in sorted(snapshots.items())
        )
        return CustomActionRuntimeState(
            running=running,
            worker_pid=self._worker_pid,
            beat_interval_ms=self._beat_interval_ms,
            stale_after_ms=self._stale_after_ms,
            targets=targets,
            recent_outcomes=recent,
            journal_path=str(self._journal_path),
        )

    # --- 루프 --------------------------------------------------------------

    async def run_once(self) -> tuple[ClickOutcome, ...]:
        """한 주기: 대상 갱신 → 하트비트 → 클릭 수집 → 실행.

        시험이 루프를 돌리지 않고 한 걸음씩 몰 수 있게 공개해 둔다.
        """
        signals = await to_thread.run_sync(self._cycle, abandon_on_cancel=False)
        outcomes: list[ClickOutcome] = []
        for signal in signals:
            if self._stopped:
                break
            outcome = await self._execute(signal)
            self._record(outcome)
            outcomes.append(outcome)
        return tuple(outcomes)

    async def serve(self) -> None:
        with self._lock:
            self._running = True
            self._stopped = False
            if self._stop_event == 0:
                self._stop_event = create_stop_event()
        self.start_heartbeat()
        try:
            while not self._stopped:
                _ = await self.run_once()
        finally:
            with self._lock:
                self._running = False

    # --- 하트비트: 실행과 독립된 스레드 -------------------------------------
    # 예전에는 박동이 클릭 루프 안에 있었다. 레시피 한 번이 5초 걸리자 그 사이 박동이
    # 굶어 신선도 창(4초)을 넘겼고, **실행 도중에 버튼이 회색이 됐다**(실증 5047ms).
    # 이 파일의 설계 주석이 금지한 바로 그 동작이다. 이제 박동은 자기 스레드에서
    # 도구 호출과 무관하게 뛴다.

    def start_heartbeat(self) -> None:
        with self._lock:
            if self._heartbeat_thread is not None:
                return
            self._heartbeat_stop.clear()
            thread = threading.Thread(
                target=self._heartbeat_loop,
                name="hwp-custom-action-heartbeat",
                daemon=True,
            )
            self._heartbeat_thread = thread
        thread.start()

    def stop_heartbeat(self) -> None:
        with self._lock:
            thread = self._heartbeat_thread
            self._heartbeat_thread = None
        self._heartbeat_stop.set()
        if thread is not None:
            thread.join(timeout=5.0)

    def _heartbeat_loop(self) -> None:
        interval = self._beat_interval_ms / 1000.0
        while not self._heartbeat_stop.is_set():
            self._beat()
            if self._heartbeat_stop.wait(interval):
                return

    def request_stop(self) -> None:
        """루프에 멈추라고 알린다. 대기 중이면 즉시 깬다."""
        self._stopped = True
        self._heartbeat_stop.set()
        with self._lock:
            handle = self._stop_event
        if handle:
            _ = set_event(handle)

    def shutdown(self) -> None:
        """채널을 닫는다. 닫기 전에 하트비트를 지워 버튼을 바로 회색으로 만든다."""
        self._stopped = True
        self.stop_heartbeat()
        with self._lock:
            channels = list(self._channels.values())
            self._channels = {}
            handle = self._stop_event
            self._stop_event = 0
        for channel in channels:
            _ = channel.clear_heartbeat()
            channel.close()
        if handle:
            _ = close_handle(handle)

    # --- 스레드에서 도는 한 주기 -------------------------------------------

    def _cycle(self) -> tuple[SlotClickSignal, ...]:
        self._refresh_targets()
        self._beat()
        pending = self._drain()
        if pending:
            return pending
        self._wait()
        return self._drain()

    def _refresh_targets(self) -> None:
        now = tick_count()
        if (
            self._last_discovery_tick is not None
            and elapsed_ticks(now, self._last_discovery_tick)
            < self._discovery_interval_ms
        ):
            return
        self._last_discovery_tick = now
        try:
            process_ids = self._process_scanner()
        except Exception:  # noqa: BLE001 - 프로세스 목록 실패로 루프가 죽으면 안 된다
            return
        detached: list[int] = []
        joined: list[int] = []
        with self._lock:
            for process_id in sorted(set(self._channels) - set(process_ids)):
                self._channels.pop(process_id).close()
                _ = self._snapshots.pop(process_id, None)
                _ = self._cursors.pop(process_id, None)
                detached.append(process_id)
            for process_id in sorted(process_ids):
                if process_id in self._channels:
                    continue
                channel = self._channel_factory(process_id)
                if not channel.open():
                    # 브리지가 안 실린 한/글이다. 다음 발견 주기에 다시 본다.
                    channel.close()
                    continue
                self._channels[process_id] = channel
                joined.append(process_id)
                snapshot = channel.read()
                if snapshot is not None:
                    self._snapshots[process_id] = snapshot
                    # 우리가 붙기 전에 눌린 클릭은 실행하지 않는다. 워커가 늦게 뜨거나
                    # 다시 떴다고 옛 클릭을 소급 실행하면 사용자가 누른 적 없는 일이
                    # 벌어진다.
                    self._cursors[process_id] = snapshot.slot_click_count
            attached = set(self._channels)
        # 사라진 한/글의 클레임 항목을 버린다. 원장이 무한히 자라지 않게.
        with suppress(OSError):
            self._claims.prune(attached)
        # 콜백은 **잠금 밖에서** 부른다. 탭 구성은 COM 왕복이라 잠금을 들고 있으면
        # 상태 조회가 그 시간만큼 멈춘다. 어느 콜백이 터져도 발견 주기는 계속 돈다 —
        # 리본은 부가 기능이고 클릭 루프가 그것 때문에 죽으면 안 된다.
        for process_id in detached:
            self._notify(self._on_detach, process_id)
        for process_id in joined:
            self._notify(self._on_attach, process_id)

    @staticmethod
    def _notify(hook: ProcessHook | None, process_id: int) -> None:
        if hook is None:
            return
        try:
            hook(process_id)
        except Exception:  # noqa: BLE001 - 콜백 실패로 클릭 루프를 잃지 않는다
            return

    def _beat(self) -> None:
        with self._lock:
            channels = list(self._channels.values())
        for channel in channels:
            _ = channel.write_heartbeat(
                worker_pid=self._worker_pid, stale_after_ms=self._stale_after_ms
            )

    def _wait(self) -> None:
        with self._lock:
            handles = tuple(
                channel.click_event
                for channel in self._channels.values()
                if channel.click_event
            )
            stop = self._stop_event
        waitable = (*handles, stop) if stop else handles
        _ = wait_for_any(waitable, self._beat_interval_ms)

    def _drain(self) -> tuple[SlotClickSignal, ...]:
        with self._lock:
            channels = list(self._channels.items())
        signals: list[SlotClickSignal] = []
        for process_id, channel in channels:
            snapshot = channel.read()
            if snapshot is None:
                continue
            with self._lock:
                self._snapshots[process_id] = snapshot
                cursor = self._cursors.get(process_id, snapshot.slot_click_count)
            if not snapshot.supports_slots or snapshot.slot_click_count <= cursor:
                continue
            # 단일 실행자 선출. 같은 한/글에 워커가 둘 붙으면 각자 커서를 들고 같은
            # 링 항목을 재생해 클릭 하나가 두 번 실행됐다(실증: dispatch 2회).
            # 이 클레임을 통과한 구간만 이 워커 몫이다. 커서는 클레임과 무관하게
            # 전진시켜, 남의 몫이 된 클릭을 다음 주기에 다시 보지 않는다.
            try:
                claimed_floor = self._claims.claim(
                    process_id=process_id,
                    through=snapshot.slot_click_count,
                    floor=cursor,
                )
            except OSError:
                # 원장을 못 쓰면 실행하지 않는다. 중복 실행보다 미실행이 낫다 —
                # 버튼 하나가 표를 두 번 넣는 것이 최악이다. 다만 **조용히 넘기지는
                # 않는다**: 사용자는 버튼을 눌렀고 DLL 카운터도 올랐는데 저널에도
                # 결과에도 아무것도 없으면 클릭이 흔적 없이 사라진다.
                unclaimed = snapshot.slot_click_count - cursor
                with self._lock:
                    self._cursors[process_id] = snapshot.slot_click_count
                signals.append(
                    SlotClickSignal(
                        process_id=process_id,
                        slot_index=snapshot.last_slot_index,
                        sequence=snapshot.slot_click_count,
                        tick=tick_count(),
                        claim_failed=True,
                        unclaimed=unclaimed,
                    )
                )
                continue
            with self._lock:
                self._cursors[process_id] = snapshot.slot_click_count
            if claimed_floor >= snapshot.slot_click_count:
                # 다른 워커가 이미 가져갔다.
                continue
            recovered = snapshot.clicks_after(claimed_floor)
            # 링이 한 바퀴 돌아 덮어쓴 클릭은 되찾을 수 없다. 조용히 넘기지 않고
            # 몇 건을 잃었는지 세어 첫 클릭에 실어 보낸다.
            dropped = (snapshot.slot_click_count - claimed_floor) - len(recovered)
            for index, click in enumerate(recovered):
                signals.append(
                    SlotClickSignal(
                        process_id=process_id,
                        slot_index=click.slot_index,
                        sequence=click.sequence,
                        tick=click.tick,
                        dropped_before=dropped if index == 0 else 0,
                    )
                )
            if recovered:
                continue
            if dropped > 0:
                signals.append(
                    SlotClickSignal(
                        process_id=process_id,
                        slot_index=-1,
                        sequence=snapshot.slot_click_count,
                        tick=tick_count(),
                        dropped_before=dropped,
                    )
                )
        return tuple(sorted(signals, key=lambda signal: (signal.process_id, signal.sequence)))

    # --- 실행 --------------------------------------------------------------

    async def _execute(self, signal: SlotClickSignal) -> ClickOutcome:
        started = _now()
        if signal.claim_failed:
            return ClickOutcome(
                process_id=signal.process_id,
                slot_index=signal.slot_index,
                sequence=signal.sequence,
                status="claim_unavailable",
                started_at=started,
                finished_at=_now(),
                message=(
                    f"클릭 {signal.unclaimed}건의 실행권을 확정하지 못했습니다"
                    " (클레임 원장 쓰기 실패). 중복 실행을 피해 실행하지 않았습니다."
                ),
            )
        if signal.slot_index < 0:
            return ClickOutcome(
                process_id=signal.process_id,
                slot_index=signal.slot_index,
                sequence=signal.sequence,
                status="dropped",
                started_at=started,
                finished_at=_now(),
                message=f"클릭 {signal.dropped_before}건이 링을 넘겨 유실됐습니다.",
            )
        try:
            registry = await to_thread.run_sync(self._store.read)
            allowed = self._store.allowed_step_tools
        except CustomActionStoreError as error:
            return ClickOutcome(
                process_id=signal.process_id,
                slot_index=signal.slot_index,
                sequence=signal.sequence,
                status="registry_unreadable",
                started_at=started,
                finished_at=_now(),
                message=str(error),
            )
        # 라우팅의 유일한 진실은 탭 구성기가 적어 둔 배치다. 레지스트리 order로
        # 되짚으면 order가 비연속인 순간 누른 버튼과 다른 레시피가 돌아간다(실증).
        action_id = await to_thread.run_sync(
            self._bindings.action_for_slot, signal.process_id, signal.slot_index
        )
        action = (
            None
            if action_id is None
            else next(
                (item for item in registry.actions if item.id == action_id), None
            )
        )
        if action is None:
            return ClickOutcome(
                process_id=signal.process_id,
                slot_index=signal.slot_index,
                sequence=signal.sequence,
                status="unmapped",
                action_id=action_id,
                started_at=started,
                finished_at=_now(),
                message=(
                    f"슬롯 {signal.slot_index}에 매인 custom action이 없습니다"
                    + (
                        _NO_BINDING_HINT
                        if action_id is None
                        else f" (배치는 {action_id}를 가리키지만 레지스트리에 없습니다)."
                    )
                ),
            )
        self.append_journal(
            ClickOutcome(
                process_id=signal.process_id,
                slot_index=signal.slot_index,
                sequence=signal.sequence,
                status="started",
                action_id=action.id,
                label=action.label,
                step_count=len(action.steps),
                started_at=started,
                finished_at=started,
            )
        )
        return await self._run_steps(signal, action, allowed, started)

    async def _run_steps(
        self,
        signal: SlotClickSignal,
        action: CustomAction,
        allowed: frozenset[str],
        started: datetime,
    ) -> ClickOutcome:
        completed = 0
        for step in action.steps:
            # 저장 시점에 걸었던 허용목록을 실행 시점에 다시 건다. 저장 이후에 도구가
            # 사라졌을 수도, 파일이 손으로 변조됐을 수도 있다(스토어의 읽기는 관용
            # 이라 변조 스텝을 예외가 아니라 표시로 돌려준다).
            if step.tool not in allowed:
                return ClickOutcome(
                    process_id=signal.process_id,
                    slot_index=signal.slot_index,
                    sequence=signal.sequence,
                    status="unauthorized",
                    action_id=action.id,
                    label=action.label,
                    step_count=len(action.steps),
                    completed_steps=completed,
                    started_at=started,
                    finished_at=_now(),
                    message=(
                        f"{step.tool}은(는) 이 프로필의 전달 가능한 공개 도구가 "
                        + "아니므로 실행을 중단했습니다."
                    ),
                )
            try:
                _ = await self._dispatch(step.tool, dict(step.arguments))
            except Exception as error:  # noqa: BLE001 - 도구는 무엇이든 던진다
                return ClickOutcome(
                    process_id=signal.process_id,
                    slot_index=signal.slot_index,
                    sequence=signal.sequence,
                    status="failed",
                    action_id=action.id,
                    label=action.label,
                    step_count=len(action.steps),
                    completed_steps=completed,
                    started_at=started,
                    finished_at=_now(),
                    message=f"{step.tool}: {type(error).__name__}: {error}",
                )
            completed += 1
        return ClickOutcome(
            process_id=signal.process_id,
            slot_index=signal.slot_index,
            sequence=signal.sequence,
            status="executed",
            action_id=action.id,
            label=action.label,
            step_count=len(action.steps),
            completed_steps=completed,
            started_at=started,
            finished_at=_now(),
        )

    # --- 저널 --------------------------------------------------------------

    def _record(self, outcome: ClickOutcome) -> None:
        with self._lock:
            self._recent.append(outcome)
            if len(self._recent) > MAX_RECENT_OUTCOMES:
                del self._recent[:-MAX_RECENT_OUTCOMES]
        self.append_journal(outcome)

    def append_journal(self, outcome: ClickOutcome) -> None:
        """저널 한 줄. 실패해도 클릭 처리를 멈추지 않는다 — 기록은 부수 효과다.

        추가와 잘라내기를 같은 잠금 안에서 한다. 워커가 둘이면 한쪽이 잘라내는 동안
        다른 쪽이 덧붙여 줄이 섞이거나 사라진다 — 레지스트리 스토어와 같은 관례다.
        """
        line = outcome.model_dump_json() + "\n"
        with suppress(OSError):
            self._journal_path.parent.mkdir(parents=True, exist_ok=True)
            with _locked(self._journal_lock_path):
                with self._journal_path.open(
                    "a", encoding="utf-8", newline="\n"
                ) as stream:
                    _ = stream.write(line)
                self._trim_journal_unlocked()

    def _trim_journal_unlocked(self) -> None:
        with suppress(OSError, UnicodeError):
            if self._journal_path.stat().st_size <= MAX_JOURNAL_BYTES:
                return
            lines = self._journal_path.read_text(encoding="utf-8").splitlines()
            kept = lines[-JOURNAL_TRIM_LINES:]
            _ = self._journal_path.write_text(
                "\n".join(kept) + "\n", encoding="utf-8", newline="\n"
            )

    def read_journal(self, limit: int = MAX_RECENT_OUTCOMES) -> tuple[ClickOutcome, ...]:
        """저널 꼬리를 모델로. 워커가 다시 떠도 지난 클릭을 볼 수 있다."""
        try:
            raw = self._journal_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return ()
        outcomes: list[ClickOutcome] = []
        for line in raw.splitlines()[-limit:]:
            if not line.strip():
                continue
            with suppress(ValueError):
                outcomes.append(ClickOutcome.model_validate(json.loads(line)))
        return tuple(outcomes)


@asynccontextmanager
async def maintain_custom_action_clicks(
    runtime: CustomActionClickRuntime | None,
) -> AsyncGenerator[None]:
    """서버 lifespan이 여는 클릭 루프.

    `maintain_live_previews`와 같은 모양이다: 태스크 그룹 안에서 돌리고, 나갈 때는
    먼저 멈추라고 알린 뒤 취소하고, 마지막 정리는 취소로부터 보호한다.

    `runtime`이 None이면 아무것도 하지 않는다. 레지스트리 루트를 못 만들어 실행기를
    세우지 못했다는 뜻이고, 그 이유로 MCP 서버 전체가 못 뜨면 안 된다 — 리본 클릭은
    부가 기능이고 도구 68종은 그것 없이도 돌아야 한다.
    """
    if runtime is None:
        yield None
        return
    try:
        async with create_task_group() as tasks:
            _ = tasks.start_soon(runtime.serve)
            try:
                yield None
            finally:
                runtime.request_stop()
                tasks.cancel_scope.cancel()
    finally:
        with CancelScope(shield=True):
            await to_thread.run_sync(runtime.shutdown)
