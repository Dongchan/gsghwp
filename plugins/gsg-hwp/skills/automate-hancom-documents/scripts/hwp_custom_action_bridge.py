"""브리지 공유메모리 채널 (한컴MCP 탭 Stage 2).

한/글 프로세스마다 브리지 DLL이 `Local\\HancomLiveBridgeStatus.<PID>` 이름의 공유
메모리 한 장을 만들어 둡니다. Stage 2에서 이 한 장이 **양방향**이 됩니다.

- DLL → 워커: 슬롯 버튼 클릭. `DoAction`이 슬롯 AID를 받으면 링 버퍼에 {순번, 슬롯,
  tick}을 적고 `Local\\HancomLiveBridgeSlotClick.<PID>` 이벤트를 셋합니다.
- 워커 → DLL: 하트비트. 워커가 살아 있다는 tick을 적으면 `UpdateUI`가 그 신선도로
  버튼 활성/비활성을 정합니다. 워커가 죽으면 tick이 늙고 버튼이 회색이 됩니다.

레이아웃은 `addon/HancomLiveBridgeNative/BridgeStatus.h`의 `Snapshot`을 그대로
비칩니다. **버전 1 필드의 오프셋은 하나도 움직이지 않습니다** — Stage 0/1의 판독기가
앞 124바이트를 고정 오프셋으로 읽고 있고, C++ 쪽 `static_assert`가 그 약속을 잡고
있습니다. 새 필드는 전부 124바이트 뒤에 붙습니다.

tick은 32비트 `GetTickCount`입니다. 64비트 값이었다면 32비트 DLL이 64비트 워커가
쓴 값을 두 번에 나눠 읽다가 찢어집니다. 32비트 정렬 저장은 양쪽 다 원자적이고,
부호 없는 뺄셈이 49.7일 랩어라운드를 알아서 흡수합니다.
"""

from __future__ import annotations

import ctypes
import struct
from ctypes import wintypes
from types import TracebackType
from typing import ClassVar, Final, cast, final

from pydantic import BaseModel, ConfigDict


# --- BridgeStatus.h 미러 -----------------------------------------------------

BRIDGE_STATUS_MAGIC: Final = 0x31534248
BRIDGE_STATUS_VERSION: Final = 2
BRIDGE_STATUS_V1_SIZE: Final = 124
BRIDGE_STATUS_SIZE: Final = 384
BRIDGE_STATUS_NAME_PREFIX: Final = "Local\\HancomLiveBridgeStatus."
SLOT_CLICK_EVENT_PREFIX: Final = "Local\\HancomLiveBridgeSlotClick."

BRIDGE_SLOT_COUNT: Final = 32
BRIDGE_CLICK_RING_CAPACITY: Final = 16
BRIDGE_CLICK_RECORD_SIZE: Final = 12

OFFSET_SEQUENCE: Final = 16
OFFSET_DO_ACTION_COUNT: Final = 32
OFFSET_LAST_ACTION: Final = 60
OFFSET_SLOT_COUNT: Final = 124
OFFSET_SLOT_CLICK_COUNT: Final = 128
OFFSET_LAST_SLOT_INDEX: Final = 132
OFFSET_LAST_SLOT_SEQUENCE: Final = 136
OFFSET_HEARTBEAT_WORKER_PID: Final = 140
OFFSET_HEARTBEAT_TICK: Final = 144
OFFSET_HEARTBEAT_STALE_AFTER_MS: Final = 148
OFFSET_SLOT_UI_ENABLED: Final = 152
OFFSET_CLICK_RING_CAPACITY: Final = 156
OFFSET_CLICK_RING: Final = 160
OFFSET_RESERVED: Final = 352

# BridgeStatus.h 의 클램프와 같은 값이어야 한다. 워커가 선언한 창을 DLL이 이 범위로
# 자른다.
MIN_HEARTBEAT_STALE_MS: Final = 1000
MAX_HEARTBEAT_STALE_MS: Final = 60000
DEFAULT_HEARTBEAT_STALE_MS: Final = 6000

_FILE_MAP_READ: Final = 0x0004
_FILE_MAP_WRITE: Final = 0x0002
_SYNCHRONIZE: Final = 0x00100000
_EVENT_MODIFY_STATE: Final = 0x0002
_WAIT_OBJECT_0: Final = 0
_WAIT_TIMEOUT: Final = 0x00000102

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.OpenFileMappingW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
_kernel32.OpenFileMappingW.restype = wintypes.HANDLE
_kernel32.MapViewOfFile.argtypes = (
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_size_t,
)
_kernel32.MapViewOfFile.restype = ctypes.c_void_p
_kernel32.UnmapViewOfFile.argtypes = (ctypes.c_void_p,)
_kernel32.UnmapViewOfFile.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.OpenEventW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
_kernel32.OpenEventW.restype = wintypes.HANDLE
_kernel32.CreateEventW.argtypes = (
    ctypes.c_void_p,
    wintypes.BOOL,
    wintypes.BOOL,
    wintypes.LPCWSTR,
)
_kernel32.CreateEventW.restype = wintypes.HANDLE
_kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
_kernel32.SetEvent.restype = wintypes.BOOL
_kernel32.WaitForMultipleObjects.argtypes = (
    wintypes.DWORD,
    ctypes.POINTER(wintypes.HANDLE),
    wintypes.BOOL,
    wintypes.DWORD,
)
_kernel32.WaitForMultipleObjects.restype = wintypes.DWORD
_kernel32.GetTickCount.argtypes = ()
_kernel32.GetTickCount.restype = wintypes.DWORD


# --- 얇은 형 지정 래퍼 --------------------------------------------------------
# ctypes 호출은 전부 Any 를 돌려준다. 경계를 여기 한 곳으로 모아 두면 아래 코드는
# 평범한 int/bool 로만 이야기한다.


def _open_file_mapping(access: int, name: str) -> int:
    return cast("int | None", _kernel32.OpenFileMappingW(access, False, name)) or 0


def _map_view(mapping: int, access: int, size: int) -> int:
    return (
        cast(
            "int | None",
            _kernel32.MapViewOfFile(wintypes.HANDLE(mapping), access, 0, 0, size),
        )
        or 0
    )


def _unmap_view(view: int) -> bool:
    return bool(cast("int", _kernel32.UnmapViewOfFile(ctypes.c_void_p(view))))


def _close(handle: int) -> bool:
    return bool(cast("int", _kernel32.CloseHandle(wintypes.HANDLE(handle))))


def _open_event(access: int, name: str) -> int:
    return cast("int | None", _kernel32.OpenEventW(access, False, name)) or 0


def _create_event(manual_reset: bool) -> int:
    return cast("int | None", _kernel32.CreateEventW(None, manual_reset, False, None)) or 0


def _wait_multiple(handles: tuple[int, ...], timeout_ms: int) -> int:
    array = (wintypes.HANDLE * len(handles))(
        *(wintypes.HANDLE(handle) for handle in handles)
    )
    return cast(
        "int",
        _kernel32.WaitForMultipleObjects(len(handles), array, False, timeout_ms),
    )


def _read_int32(address: int) -> int:
    return int(ctypes.c_int32.from_address(address).value)


def _write_int32(address: int, value: int) -> None:
    ctypes.c_int32.from_address(address).value = value


def _read_bytes(address: int, size: int) -> bytes:
    return ctypes.string_at(address, size)


def _unpack_signed(count: int, raw: bytes, offset: int) -> tuple[int, ...]:
    return cast("tuple[int, ...]", struct.unpack_from(f"<{count}i", raw, offset))


def _unpack_unsigned(count: int, raw: bytes, offset: int) -> tuple[int, ...]:
    return cast("tuple[int, ...]", struct.unpack_from(f"<{count}I", raw, offset))


def status_mapping_name(process_id: int) -> str:
    return f"{BRIDGE_STATUS_NAME_PREFIX}{process_id}"


def slot_click_event_name(process_id: int) -> str:
    return f"{SLOT_CLICK_EVENT_PREFIX}{process_id}"


def tick_count() -> int:
    """`GetTickCount()`. DLL과 같은 시계여야 하므로 time.monotonic을 쓰지 않는다."""
    return cast("int", _kernel32.GetTickCount()) & 0xFFFFFFFF


def elapsed_ticks(now: int, then: int) -> int:
    """부호 없는 32비트 뺄셈. 49.7일 랩어라운드를 흡수한다(C++ 쪽과 같은 계산)."""
    return (now - then) & 0xFFFFFFFF


def clamp_stale_window(declared_ms: int) -> int:
    """DLL이 하는 클램프를 그대로 재현한다."""
    window = DEFAULT_HEARTBEAT_STALE_MS if declared_ms == 0 else declared_ms
    return max(MIN_HEARTBEAT_STALE_MS, min(MAX_HEARTBEAT_STALE_MS, window))


def _as_signed32(value: int) -> int:
    return value - 0x100000000 if value >= 0x80000000 else value


class _BridgeModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class SlotClickRecord(_BridgeModel):
    sequence: int
    slot_index: int
    tick: int


class BridgeSnapshot(_BridgeModel):
    """공유메모리 한 장의 일관된 사본.

    `supports_slots`가 False면 그 한/글에 실린 브리지가 Stage 2 이전(version 1)이라
    슬롯 필드가 없다는 뜻이다. 그때는 앞 124바이트만 믿는다.
    """

    process_id: int
    magic_ok: bool
    version: int
    size: int
    do_action_count: int
    last_action: str
    slot_count: int = 0
    slot_click_count: int = 0
    last_slot_index: int = -1
    last_slot_sequence: int = 0
    heartbeat_worker_pid: int = 0
    heartbeat_tick: int = 0
    heartbeat_stale_after_ms: int = 0
    slot_ui_enabled: int = 0
    click_ring_capacity: int = 0
    clicks: tuple[SlotClickRecord, ...] = ()

    @property
    def supports_slots(self) -> bool:
        return self.magic_ok and self.version >= BRIDGE_STATUS_VERSION

    def clicks_after(self, sequence: int) -> tuple[SlotClickRecord, ...]:
        """`sequence` 이후로 링에 남아 있는 클릭을 순번 순으로.

        링이 한 바퀴 돌아 덮어쓴 클릭은 돌려줄 수 없다 — 사라진 것을 지어내지 않고,
        남아 있는 것만 준다. 호출자는 `slot_click_count`와 비교해 유실 건수를 안다.
        """
        return tuple(
            sorted(
                (click for click in self.clicks if click.sequence > sequence),
                key=lambda click: click.sequence,
            )
        )


def decode_snapshot(raw: bytes, *, process_id: int) -> BridgeSnapshot:
    """바이트 → 스냅샷. 시험이 COM도 한/글도 없이 레이아웃을 검사할 수 있게 연다."""
    if len(raw) < BRIDGE_STATUS_V1_SIZE:
        raise ValueError(f"BridgeStatus 스냅샷이 너무 짧습니다: {len(raw)}바이트")
    magic, version, size = _unpack_unsigned(3, raw, 0)
    magic_ok = magic == BRIDGE_STATUS_MAGIC
    do_action_count = _unpack_signed(1, raw, OFFSET_DO_ACTION_COUNT)[0]
    last_action = (
        raw[OFFSET_LAST_ACTION : OFFSET_LAST_ACTION + 64]
        .split(b"\x00", 1)[0]
        .decode("ascii", "replace")
    )
    if (
        not magic_ok
        or version < BRIDGE_STATUS_VERSION
        or size < BRIDGE_STATUS_SIZE
        or len(raw) < BRIDGE_STATUS_SIZE
    ):
        return BridgeSnapshot(
            process_id=process_id,
            magic_ok=magic_ok,
            version=version,
            size=size,
            do_action_count=do_action_count,
            last_action=last_action,
        )
    (
        slot_count,
        slot_click_count,
        last_slot_index,
        last_slot_sequence,
        heartbeat_worker_pid,
        heartbeat_tick,
        heartbeat_stale_after_ms,
        slot_ui_enabled,
        click_ring_capacity,
    ) = _unpack_signed(9, raw, OFFSET_SLOT_COUNT)
    capacity = max(0, min(click_ring_capacity, BRIDGE_CLICK_RING_CAPACITY))
    clicks: list[SlotClickRecord] = []
    for index in range(capacity):
        sequence, slot_index, tick = _unpack_signed(
            3, raw, OFFSET_CLICK_RING + index * BRIDGE_CLICK_RECORD_SIZE
        )
        if sequence <= 0:
            continue
        clicks.append(
            SlotClickRecord(sequence=sequence, slot_index=slot_index, tick=tick)
        )
    return BridgeSnapshot(
        process_id=process_id,
        magic_ok=True,
        version=version,
        size=size,
        do_action_count=do_action_count,
        last_action=last_action,
        slot_count=slot_count,
        slot_click_count=slot_click_count,
        last_slot_index=last_slot_index,
        last_slot_sequence=last_slot_sequence,
        heartbeat_worker_pid=heartbeat_worker_pid,
        heartbeat_tick=heartbeat_tick & 0xFFFFFFFF,
        heartbeat_stale_after_ms=heartbeat_stale_after_ms,
        slot_ui_enabled=slot_ui_enabled,
        click_ring_capacity=click_ring_capacity,
        clicks=tuple(clicks),
    )


@final
class BridgeStatusChannel:
    """한 한/글 프로세스의 공유메모리에 대한 열린 창.

    읽기는 seqlock으로 찢김을 걸러 낸다(DLL이 쓰기 전후로 `sequence`를 올린다).
    쓰기는 하트비트 세 필드뿐이다 — 카운터는 DLL 것이라 손대지 않는다.
    """

    __slots__ = ("_click_event", "_mapping", "_process_id", "_view")

    def __init__(self, process_id: int) -> None:
        self._process_id = process_id
        self._mapping: int = 0
        self._view: int = 0
        self._click_event: int = 0

    @property
    def process_id(self) -> int:
        return self._process_id

    @property
    def click_event(self) -> int:
        return self._click_event

    @property
    def opened(self) -> bool:
        return self._view != 0

    def open(self) -> bool:
        """열렸으면 True. 그 한/글에 브리지가 안 실렸으면 조용히 False."""
        if self._view != 0:
            return True
        access = _FILE_MAP_READ | _FILE_MAP_WRITE
        mapping = _open_file_mapping(access, status_mapping_name(self._process_id))
        if mapping == 0:
            return False
        view = _map_view(mapping, access, BRIDGE_STATUS_SIZE)
        if view == 0:
            _ = _close(mapping)
            return False
        self._mapping = mapping
        self._view = view
        # 이벤트가 없어도 채널은 유효하다. 그때는 폴링으로 떨어질 뿐이다.
        self._click_event = _open_event(
            _SYNCHRONIZE | _EVENT_MODIFY_STATE,
            slot_click_event_name(self._process_id),
        )
        return True

    def close(self) -> None:
        if self._click_event:
            _ = _close(self._click_event)
            self._click_event = 0
        if self._view:
            _ = _unmap_view(self._view)
            self._view = 0
        if self._mapping:
            _ = _close(self._mapping)
            self._mapping = 0

    def __enter__(self) -> BridgeStatusChannel:
        _ = self.open()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def read(self) -> BridgeSnapshot | None:
        """일관된 스냅샷 하나. 찢김이 계속되면 None(다음 주기에 다시 본다)."""
        if self._view == 0:
            return None
        for _attempt in range(8):
            before = _read_int32(self._view + OFFSET_SEQUENCE)
            if before % 2 != 0:
                continue
            raw = _read_bytes(self._view, BRIDGE_STATUS_SIZE)
            if _read_int32(self._view + OFFSET_SEQUENCE) != before:
                continue
            return decode_snapshot(raw, process_id=self._process_id)
        return None

    def write_heartbeat(self, *, worker_pid: int, stale_after_ms: int) -> bool:
        """하트비트 세 필드만 쓴다. 32비트 정렬 저장이라 원자적이다."""
        if self._view == 0:
            return False
        _write_int32(
            self._view + OFFSET_HEARTBEAT_STALE_AFTER_MS,
            clamp_stale_window(stale_after_ms),
        )
        _write_int32(self._view + OFFSET_HEARTBEAT_WORKER_PID, worker_pid)
        # tick을 마지막에 쓴다. DLL이 tick으로 판정하므로, tick이 신선해진 순간에는
        # 나머지 두 필드가 이미 제자리에 있다.
        _write_int32(self._view + OFFSET_HEARTBEAT_TICK, _as_signed32(tick_count()))
        return True

    def clear_heartbeat(self) -> bool:
        """워커가 정상 종료할 때 자기 흔적을 지운다 — 버튼이 즉시 회색이 된다."""
        if self._view == 0:
            return False
        _write_int32(self._view + OFFSET_HEARTBEAT_WORKER_PID, 0)
        _write_int32(self._view + OFFSET_HEARTBEAT_TICK, 0)
        return True


def create_stop_event() -> int:
    """이름 없는 수동 리셋 이벤트. 대기 중인 워커를 즉시 깨우는 데 쓴다."""
    return _create_event(True)


def set_event(handle: int) -> bool:
    if handle == 0:
        return False
    return bool(cast("int", _kernel32.SetEvent(wintypes.HANDLE(handle))))


def close_handle(handle: int) -> bool:
    return _close(handle) if handle else False


def wait_for_any(handles: tuple[int, ...], timeout_ms: int) -> int:
    """신호된 핸들의 인덱스. 시간 초과면 -1, 대기 실패면 -2.

    핸들이 하나도 없으면 timeout만큼 잔다 — 대기할 대상이 없다고 바쁜 루프를 돌면
    한/글이 없는 동안 CPU를 태운다.
    """
    waitable = handles
    idle = 0
    if not waitable:
        idle = create_stop_event()
        if idle == 0:
            return -2
        waitable = (idle,)
    try:
        result = _wait_multiple(waitable, timeout_ms)
    finally:
        if idle:
            _ = close_handle(idle)
    if result == _WAIT_TIMEOUT:
        return -1
    if not handles:
        return -2
    if _WAIT_OBJECT_0 <= result < _WAIT_OBJECT_0 + len(handles):
        return result - _WAIT_OBJECT_0
    return -2
