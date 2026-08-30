"""슬롯 배치 기록·클릭 클레임·탭 키 원장 (한컴MCP 탭 Stage 2).

레지스트리 옆에 작은 파일 셋을 둡니다. 앞의 둘은 적대적 검증이 실증한 사고를,
셋째는 리본 탭 누적을 막기 위한 것입니다.

1. **슬롯 배치(`slot-bindings.json`)** — 탭 구성기가 *실제로* 어느 슬롯에 어느 액션의
   버튼을 만들었는지. 클릭 라우팅의 **유일한 진실**입니다. 예전에는 `build_tab`이
   목록 위치로 버튼을 놓고 라우팅은 `action.order`로 되찾았습니다. 두 값이 같다는
   보장은 "모든 쓰기가 order를 0..n-1로 다시 매긴다"는 관례뿐이었고, 파일이 손으로
   고쳐져 order가 비연속(0,2,5)이 되자 **CHARLIE 버튼을 누르면 bravo가 실행**됐습니다.
   이제 배치한 쪽이 배치를 적고, 라우팅은 그것만 읽습니다.

2. **클릭 클레임(`click-claims.json`)** — 같은 한/글에 MCP 워커가 둘 붙으면 각자
   커서를 들고 같은 링 항목을 재생해 **클릭 한 번이 두 번 실행**됐습니다(실증:
   dispatch 2회). 실행 전에 순번 구간을 원자적으로 클레임해 단일 실행자를 뽑습니다.

3. **탭 키 원장(`tab-keys.json`)** — 한/글은 툴박스 탭 트리를 **탭 키 단위로
   프로세스 수명 동안 캐시**하므로(라이브 실측), 재구성은 그 프로세스에서 한 번도
   쓰지 않은 **새 키**로 세워야 리본에 닿습니다. 그러면 옛 키를 알아야 옛 탭을
   지울 수 있는데, 툴박스 툴바에는 **탭을 열거하는 API가 없습니다**(실측:
   `InsertToolBoxTab`·`GetToolBoxTab(UID)`·`DeleteToolBoxTab(UID)`·
   `ChangeToolBoxTab`·`GetCurrentToolBoxTab`·`Create*` 가 전부입니다). 그래서 지금
   쓰는 키를 프로세스별로 여기 적어 둡니다. 워커가 다시 떠도 이 파일이 옛 키를
   알려 주므로 탭이 쌓이지 않습니다.

세 파일 모두 레지스트리 스토어와 같은 관례를 씁니다 — 잠금 파일에 `msvcrt` 락,
쓰기는 temp+fsync+replace.

**한계(정직하게)**: 공유메모리의 `heartbeatWorkerPid`는 last-writer-wins입니다. 워커가
둘이면 DLL은 마지막에 쓴 쪽의 PID만 봅니다. 버튼 활성/비활성 판정은 tick의 신선도로만
하므로 동작에는 영향이 없지만, 그 필드로 "지금 실행권을 가진 워커"를 알 수는 없습니다.
실행권은 오직 이 클레임 원장이 정합니다.
"""

from __future__ import annotations

import msvcrt
import os
import re
import secrets
import threading
from collections.abc import Callable, Collection, Generator, Mapping, Sequence
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Final, final

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
)


_LEDGER_OBJECT: Final[TypeAdapter[dict[str, JsonValue]]] = TypeAdapter(
    dict[str, JsonValue]
)
_LEDGER_VALUE: Final[TypeAdapter[JsonValue]] = TypeAdapter(JsonValue)

SLOT_BINDING_FILE_NAME: Final = "slot-bindings.json"
CLICK_CLAIM_FILE_NAME: Final = "click-claims.json"
TAB_KEY_FILE_NAME: Final = "tab-keys.json"


class _BindingModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class SlotPlacement(_BindingModel):
    """버튼 하나가 실제로 놓인 자리. 구성기가 만들고, 실행기가 읽는다."""

    slot: int = Field(ge=0)
    action_id: str = Field(min_length=1, max_length=64)
    aid: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=64)


class SlotBindingRecord(_BindingModel):
    moniker: str | None = None
    placements: tuple[SlotPlacement, ...] = ()


class SlotBindingFile(_BindingModel):
    schema_version: int = 1
    processes: dict[str, SlotBindingRecord] = Field(default_factory=dict)


class ClickClaimFile(_BindingModel):
    schema_version: int = 1
    # process_id(문자열) -> 이 한/글에서 지금까지 클레임된 마지막 클릭 순번.
    claimed_through: dict[str, int] = Field(default_factory=dict)


class TabKeyRecord(_BindingModel):
    """한 한/글 프로세스의 탭 키 상태.

    `tab_key`가 None이면 "지금 우리 탭이 없다"(지웠거나 아직 안 세웠다)는 뜻이고,
    `generation`은 그래도 남는다. 세대는 **이 프로세스에서 이미 태워 버린 키의
    최고 수위**다 — 탭을 지웠다고 세대를 0으로 되돌리면 다음 구성이 이미 캐시된
    키를 다시 집어 리본에 닿지 못한다.
    """

    tab_key: str | None = None
    generation: int = Field(default=0, ge=0)
    moniker: str | None = None
    # 이 한/글에 우리가 세워 놓고 **키를 잃어버린** 탭이 남아 있을 수 있다.
    # 한 번 참이면 그 프로세스가 죽을 때까지 참이다 — 다시 구성했다고 고아가
    # 사라지지는 않으므로, 이 사실은 기록에 눌러 담아 계속 말한다.
    orphan_suspected: bool = False


class TabKeyFile(_BindingModel):
    schema_version: int = 1
    processes: dict[str, TabKeyRecord] = Field(default_factory=dict)


@contextmanager
def locked(lock_path: Path) -> Generator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with lock_path.open("xb") as stream:
            _ = stream.write(b"\0")
    except FileExistsError:
        pass
    with lock_path.open("r+b", buffering=0) as stream:
        _ = stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield None
        finally:
            _ = stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def _write_atomic(path: Path, payload: str) -> None:
    temporary = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            _ = stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@final
class SlotBindingStore:
    """탭이 실제로 만든 슬롯→액션 배치. 클릭 라우팅의 유일한 진실."""

    __slots__ = ("_lock_path", "_path")

    def __init__(self, root: Path) -> None:
        self._path = root / SLOT_BINDING_FILE_NAME
        self._lock_path = root / ".slot-bindings.lock"

    @property
    def path(self) -> Path:
        return self._path

    def _read_unlocked(self) -> SlotBindingFile:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return SlotBindingFile()
        try:
            return SlotBindingFile.model_validate_json(raw)
        except ValidationError:
            # 손상된 배치 파일은 "배치를 모른다"로 읽는다. 지어내면 클릭이 엉뚱한
            # 레시피로 간다 — 그것이 이 파일이 존재하는 이유다.
            return SlotBindingFile()

    def publish(
        self,
        *,
        process_id: int,
        moniker: str | None,
        placements: Sequence[SlotPlacement],
    ) -> None:
        with locked(self._lock_path):
            current = self._read_unlocked()
            processes = dict(current.processes)
            processes[str(process_id)] = SlotBindingRecord(
                moniker=moniker, placements=tuple(placements)
            )
            updated = current.model_copy(update={"processes": processes})
            _write_atomic(self._path, updated.model_dump_json(indent=2) + "\n")

    def forget(self, process_id: int) -> None:
        with locked(self._lock_path):
            current = self._read_unlocked()
            if str(process_id) not in current.processes:
                return
            processes = {
                key: value
                for key, value in current.processes.items()
                if key != str(process_id)
            }
            updated = current.model_copy(update={"processes": processes})
            _write_atomic(self._path, updated.model_dump_json(indent=2) + "\n")

    def placements(self, process_id: int) -> tuple[SlotPlacement, ...]:
        with locked(self._lock_path):
            record = self._read_unlocked().processes.get(str(process_id))
        return () if record is None else record.placements

    def has_record(self, process_id: int) -> bool:
        """이 PID 에 우리가 이미 탭을 세운 적이 있는가.

        탭 키 원장을 잃었을 때 "고아 탭이 남았을 수 있다"를 **정확히** 가리는 증거다.
        배치는 구성이 성공할 때마다 여기 적히므로, 배치 기록은 있는데 탭 키 기록이
        없다면 그 사이에 기억을 잃은 것이다. 둘 다 없으면 이 한/글에는 애초에 세운
        적이 없다 — 그때는 경고할 것이 없다.
        """
        with locked(self._lock_path):
            return str(process_id) in self._read_unlocked().processes

    def action_for_slot(self, process_id: int, slot: int) -> str | None:
        for placement in self.placements(process_id):
            if placement.slot == slot:
                return placement.action_id
        return None

    def prune_dead(self, is_alive: Callable[[int], bool | None]) -> tuple[int, ...]:
        """죽은 한/글의 배치를 버린다. `TabKeyLedger`와 같은 청소다.

        두 가지를 막는다. 하나는 원장이 무한히 자라는 것(실측: 죽은 PID 4개가 그대로
        남아 있었다). 다른 하나가 더 무섭다 — **PID 재사용**이다. 새 한/글이 죽은
        한/글의 PID 를 물려받으면, 남아 있던 낡은 배치가 그 프로세스의 클릭을 엉뚱한
        레시피로 보낸다. 판정 불가(None)는 살아 있는 쪽으로 본다.
        """
        with locked(self._lock_path):
            current = self._read_unlocked()
            dead = tuple(
                int(key)
                for key in current.processes
                if key.isdecimal() and is_alive(int(key)) is False
            )
            if not dead:
                return ()
            retired = {str(process_id) for process_id in dead}
            processes = {
                key: value
                for key, value in current.processes.items()
                if key not in retired
            }
            updated = current.model_copy(update={"processes": processes})
            _write_atomic(self._path, updated.model_dump_json(indent=2) + "\n")
            return dead


@final
class ClickClaimLedger:
    """클릭 순번의 단일 실행자 선출.

    워커는 실행 전에 `(floor, through]` 구간을 클레임한다. 잠금 안에서 원장을 읽고
    올려 쓰므로, 같은 순번을 두 워커가 가져갈 수 없다.
    """

    __slots__ = ("_lock_path", "_path")

    def __init__(self, root: Path) -> None:
        self._path = root / CLICK_CLAIM_FILE_NAME
        self._lock_path = root / ".click-claims.lock"

    @property
    def path(self) -> Path:
        return self._path

    def _read_unlocked(self) -> ClickClaimFile:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return ClickClaimFile()
        try:
            return ClickClaimFile.model_validate_json(raw)
        except ValidationError:
            # 손상된 원장은 "아무것도 클레임되지 않았다"로 읽는다. 중복 실행보다는
            # 안전하지만 완전하지 않다 — 그래서 파일은 원자 쓰기로만 갱신한다.
            return ClickClaimFile()

    def claim(self, *, process_id: int, through: int, floor: int) -> int:
        """`(반환값, through]` 구간이 이 워커 몫이다. 반환값 == through 이면 없음."""
        if through <= floor:
            return through
        with locked(self._lock_path):
            current = self._read_unlocked()
            recorded = current.claimed_through.get(str(process_id), 0)
            # 카운터가 뒤로 갔다 = 같은 PID를 새 한/글이 물려받았다. 원장을 버린다.
            if through < recorded:
                recorded = 0
            start = max(recorded, floor)
            if through <= start:
                return start
            claimed = dict(current.claimed_through)
            claimed[str(process_id)] = through
            updated = current.model_copy(update={"claimed_through": claimed})
            _write_atomic(self._path, updated.model_dump_json(indent=2) + "\n")
            return start

    def claimed_through(self, process_id: int) -> int:
        with locked(self._lock_path):
            return self._read_unlocked().claimed_through.get(str(process_id), 0)

    def prune(self, keep: Collection[int]) -> None:
        """사라진 한/글의 항목을 버린다. 원장이 무한히 자라지 않게."""
        retained = {str(process_id) for process_id in keep}
        with locked(self._lock_path):
            current = self._read_unlocked()
            if all(key in retained for key in current.claimed_through):
                return
            claimed = {
                key: value
                for key, value in current.claimed_through.items()
                if key in retained
            }
            updated = current.model_copy(update={"claimed_through": claimed})
            _write_atomic(self._path, updated.model_dump_json(indent=2) + "\n")


def _salvage_tab_key_records(raw: str) -> dict[str, TabKeyRecord]:
    """손상된 원장에서 **온전한 항목만** 건져 낸다.

    두 가지 손상을 다르게 다룬다.
      * JSON 은 멀쩡한데 스키마가 어긋난 경우 — 항목별로 검증해 통과한 것만 남긴다.
      * JSON 자체가 잘린 경우(쓰기 도중 전원이 나간 모양) — 파일을 훑어 **닫힌
        중괄호까지 온전한** `"pid": {...}` 조각만 뽑아 검증한다. 잘려 나간 꼬리는
        되살릴 수 없지만, 앞쪽 항목은 그대로 살아 있다.
    """
    salvaged: dict[str, TabKeyRecord] = {}

    def keep(key: str, value: JsonValue) -> None:
        if not key.isdecimal():
            return
        with suppress(ValidationError):
            salvaged[key] = TabKeyRecord.model_validate(value)

    try:
        payload = _LEDGER_OBJECT.validate_json(raw)
    except ValidationError:
        payload = None
    if payload is not None:
        processes = payload.get("processes")
        if isinstance(processes, dict):
            for key, value in processes.items():
                keep(key, value)
        return salvaged
    # 잘린 JSON: `"숫자": { ... }` 조각을 중괄호 균형으로 잘라 낸다. 꼬리는 못
    # 되살리지만 앞쪽 항목은 바이트가 그대로 남아 있다.
    for match in re.finditer(r'"(\d+)"\s*:\s*\{', raw):
        depth = 0
        for index in range(match.end() - 1, len(raw)):
            if raw[index] == "{":
                depth += 1
            elif raw[index] == "}":
                depth -= 1
                if depth == 0:
                    with suppress(ValidationError):
                        keep(
                            match.group(1),
                            _LEDGER_VALUE.validate_json(
                                raw[match.end() - 1 : index + 1]
                            ),
                        )
                    break
    return salvaged


@final
class TabKeyLedger:
    """프로세스별 "지금 쓰는 탭 키"와 세대 수위. 탭 누적을 막는 유일한 기억.

    툴바에 탭 열거 API가 없으므로(실측), 옛 키를 잊으면 그 탭은 한/글이 죽을 때까지
    리본에 남는다. 그래서 이 파일은 워커 수명이 아니라 **디스크**에 산다.
    """

    __slots__ = ("_corrupted", "_lock_path", "_path")

    def __init__(self, root: Path) -> None:
        self._path = root / TAB_KEY_FILE_NAME
        self._lock_path = root / ".tab-keys.lock"
        self._corrupted = False
        # 빈 원장이라도 **파일은 즉시 만든다.** 그래야 "파일이 없다"가 곧 "누군가
        # 원장을 지웠다"가 되고, 처음 붙는 한/글(정상)과 기억을 잃은 상태(고아 탭이
        # 남았을 수 있음)를 구별할 수 있다. 구별하지 못하면 경고가 늘 울려서 아무도
        # 안 읽는다.
        with suppress(OSError):
            root.mkdir(parents=True, exist_ok=True)
            with self._path.open("x", encoding="utf-8", newline="\n") as stream:
                _ = stream.write(TabKeyFile().model_dump_json(indent=2) + "\n")

    @property
    def path(self) -> Path:
        return self._path

    def _read_unlocked(self) -> TabKeyFile:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return TabKeyFile()
        try:
            return TabKeyFile.model_validate_json(raw)
        except ValidationError:
            self._corrupted = True
        # 손상됐다고 통째로 버리면, 다음 `remember` 가 **멀쩡히 살아 있는 다른
        # 한/글의 탭 키까지** 지워 버린다(적대 검증 실증: 잘린 JSON 뒤 재기록이
        # 다른 PID 의 수위를 날렸다). 살릴 수 있는 항목은 살린다.
        salvaged = _salvage_tab_key_records(raw)
        with suppress(OSError):
            self._quarantine(raw)
        return TabKeyFile(processes=salvaged)

    def _quarantine(self, raw: str) -> None:
        """손상된 원장 원본을 남긴다. 조용히 덮어쓰면 무엇을 잃었는지 못 본다.

        두 번째 손상이 첫 번째 증거를 지우지 않도록 이름에 시각을 붙인다.
        """
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        backup = self._path.with_name(f"{self._path.name}.corrupt-{stamp}")
        if backup.exists():
            return
        _write_atomic(backup, raw)

    def _store(self, process_id: int, record: TabKeyRecord | None) -> None:
        with locked(self._lock_path):
            current = self._read_unlocked()
            processes = dict(current.processes)
            if record is None:
                if str(process_id) not in processes:
                    return
                del processes[str(process_id)]
            else:
                processes[str(process_id)] = record
            updated = current.model_copy(update={"processes": processes})
            _write_atomic(self._path, updated.model_dump_json(indent=2) + "\n")

    def current(self, process_id: int) -> TabKeyRecord | None:
        with locked(self._lock_path):
            return self._read_unlocked().processes.get(str(process_id))

    def remember(
        self,
        *,
        process_id: int,
        tab_key: str,
        generation: int,
        moniker: str | None,
        orphan_suspected: bool = False,
    ) -> None:
        self._store(
            process_id,
            TabKeyRecord(
                tab_key=tab_key,
                generation=generation,
                moniker=moniker,
                # 시킨 대로만 적는다. 이어받기는 구성기가 판단한다 — 여기서 몰래
                # 이전 값을 OR 하면 PID 를 물려받은 무고한 새 프로세스가 옛 의심을
                # 그대로 승계한다.
                orphan_suspected=orphan_suspected,
            ),
        )

    def release(self, *, process_id: int, generation: int) -> None:
        """탭은 지웠지만 세대 수위와 고아 의심은 남긴다."""
        record = self.current(process_id)
        self._store(
            process_id,
            TabKeyRecord(
                tab_key=None,
                generation=max(generation, 0 if record is None else record.generation),
                moniker=None if record is None else record.moniker,
                orphan_suspected=record is not None and record.orphan_suspected,
            ),
        )

    def forget(self, process_id: int) -> None:
        """한/글이 죽었다. 그 프로세스의 기억은 이제 쓸모가 없다."""
        self._store(process_id, None)

    @contextmanager
    def rebuild_lock(self, process_id: int | None) -> Generator[None]:
        """한 한/글의 [계획 → 삭제 → 기록 → 빌드] 전체를 감싸는 잠금.

        원장 잠금은 연산 단위라 그 사이가 TOCTOU였다. 워커 둘이 같은 "다음 키"를
        집으면 한쪽이 세운 살아 있는 키에 다른 쪽이 다시 삽입해 **탭이 둘로 갈리고**,
        다음 재구성은 `delete_not_confirmed`로 넘어졌다(적대 검증 실증). 재구성
        구간을 통째로 잠가야 그 창이 닫힌다.
        """
        if process_id is None:
            yield None
            return
        with locked(self._path.with_name(f".tab-rebuild.{process_id}.lock")):
            yield None

    def prune_dead(self, is_alive: Callable[[int], bool | None]) -> tuple[int, ...]:
        """죽은 한/글의 항목을 버린다. 원장이 무한히 자라지 않게.

        일시적인 detach 로는 절대 지우지 않는다. 발견 스캐너가 한 번 헛돌면 살아 있는
        한/글이 전부 사라진 것처럼 보이는데, 그때 원장을 지우면 다음 주기에 멱등
        판정을 못 해 리본을 전면 재구성한다(오늘 사고의 모양). 그래서 판정은 스캐너가
        아니라 OS 에 직접 묻는다. 판정 불가(None)는 살아 있는 쪽으로 본다.
        """
        with locked(self._lock_path):
            current = self._read_unlocked()
            dead = tuple(
                int(key)
                for key in current.processes
                if key.isdecimal() and is_alive(int(key)) is False
            )
            if not dead:
                return ()
            dropped = {str(process_id) for process_id in dead}
            processes = {
                key: value
                for key, value in current.processes.items()
                if key not in dropped
            }
            updated = current.model_copy(update={"processes": processes})
            _write_atomic(self._path, updated.model_dump_json(indent=2) + "\n")
            return dead


@final
class InMemoryTabKeyLedger:
    """파일 없는 원장. 루트를 모르는 구성기(시험·단독 호출)가 쓴다.

    프로세스 안에서는 제대로 동작하지만 워커가 다시 뜨면 잊는다 — 그때는 구성기의
    훑기 복구가 남은 탭을 찾아 지운다.
    """

    __slots__ = ("_locks", "_records")

    def __init__(self) -> None:
        self._records: dict[int, TabKeyRecord] = {}
        self._locks: dict[int, threading.Lock] = {}

    def current(self, process_id: int) -> TabKeyRecord | None:
        return self._records.get(process_id)

    def remember(
        self,
        *,
        process_id: int,
        tab_key: str,
        generation: int,
        moniker: str | None,
        orphan_suspected: bool = False,
    ) -> None:
        self._records[process_id] = TabKeyRecord(
            tab_key=tab_key,
            generation=generation,
            moniker=moniker,
            orphan_suspected=orphan_suspected,
        )

    def release(self, *, process_id: int, generation: int) -> None:
        record = self._records.get(process_id)
        self._records[process_id] = TabKeyRecord(
            tab_key=None,
            generation=max(generation, 0 if record is None else record.generation),
            moniker=None if record is None else record.moniker,
            orphan_suspected=record is not None and record.orphan_suspected,
        )

    def forget(self, process_id: int) -> None:
        _ = self._records.pop(process_id, None)

    @contextmanager
    def rebuild_lock(self, process_id: int | None) -> Generator[None]:
        if process_id is None:
            yield None
            return
        with self._locks.setdefault(process_id, threading.Lock()):
            yield None

    def prune_dead(self, is_alive: Callable[[int], bool | None]) -> tuple[int, ...]:
        dead = tuple(
            process_id for process_id in self._records if is_alive(process_id) is False
        )
        for process_id in dead:
            del self._records[process_id]
        return dead


def placements_from_mapping(
    mapping: Mapping[int, str], *, aids: Sequence[str], labels: Mapping[str, str]
) -> tuple[SlotPlacement, ...]:
    """시험 편의용 조립기. 프로덕션은 `build_tab`이 직접 만든다."""
    return tuple(
        SlotPlacement(
            slot=slot,
            action_id=action_id,
            aid=aids[slot],
            label=labels.get(action_id, action_id),
        )
        for slot, action_id in sorted(mapping.items())
    )
