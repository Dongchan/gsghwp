"""한컴MCP 리본 탭 구성기 (Stage 2).

레지스트리 내용만으로 "한컴MCP" 탭을 멱등하게 (재)구성합니다. COM 체인은 Stage 0
실측 경로 그대로입니다.

    ROT `!HancomLiveBridge.<PID>`
      → XHwpWindows.Active_XHwpWindow.XHwpToolbarLayout.GetToolBoxToolBar()
      → InsertToolBoxTab / InsertToolBox / GetLayout(0) / InsertGroup
      → CreateToolBoxItemButtonEx / InsertItem

피로 배운 규칙 다섯을 코드가 강제합니다.

1. **한 그룹에 든 아이템 객체를 또 넣지 않는다.** 2026-08-19 실기에서 그 경로로 한/글
   프로세스를 잃었다. 그래서 `build_tab`은 **AID 슬롯 풀을 순회하며 AID마다 정확히
   버튼 하나**만 만들고 한 번만 넣는다. 풀이 마르면 남은 액션은 만들지 않는다. 사인이
   "같은 객체 두 번 삽입"인지 "한 탭에 같은 AID 둘"인지까지는 좁히지 못했으므로
   `aid_reused` 가드는 그대로 둔다.
2. **탭 트리는 탭 키 단위로 프로세스 수명 내내 고착된다.** Stage 2 라이브 실측이
   확정했다: `DeleteToolBoxTab` 뒤 같은 탭 키로 다시 세우면 한/글은 그 키로 **처음
   만든 트리**를 돌려준다 — 그룹 개수도 라벨도. 이번 삽입은 전부 받아들여지지만
   버려진다. 상자 키와 그룹 키를 새것으로 줘도 못 벗어난다. **탭 키를 바꾸면 완전히
   벗어난다**(새 라벨·새 구조가 그대로 올라간다).
   → 그래서 이 구성기는 **재구성마다 새 탭 키**를 쓴다(`{GSG-HWP-MCP-TAB-XXXX}`,
   XXXX는 세대). 순서는 언제나 [옛 키 삭제 → 새 키 생성]이고, 옛 키는 프로세스별
   원장(`hwp_custom_action_binding.TabKeyLedger`)이 기억한다. 툴박스 툴바에는
   **탭 열거 API가 없으므로**(실측: Insert/Get/Delete/Change/GetCurrent/Create* 뿐)
   기억하지 않으면 옛 탭을 영영 못 지운다.
   → Stage 1이 이 현상을 "AID 싱글턴 탓 라벨 고착"이라고 적은 것은 원인 지목이
   틀렸다. 실측: 같은 AID로 `CreateToolBoxItemButtonEx`를 두 번 부르면 **서로 다른
   객체**가 나오고 각자 자기 이름을 든다.
3. **Stage 2의 AID 풀 크기는 32다.** 브리지 DLL(0.5.173)의 `EnumAction`이 슬롯 AID
   32개를 광고하고, 레지스트리 정원도 32다. 슬롯마다 AID가 다른 덕분에 **클릭이 어느
   레시피인지 구분된다** — 그것이 슬롯 풀의 본래 목적이다.
4. **조회·삭제 키는 InsertToolBoxTab에 준 원래 문자열이다.** 아이템의 `.UID`는 AID와
   같은 문자열이라 식별자로 쓸 수 없다.
5. **DeleteToolBoxTab의 반환값은 판정 근거가 아니다.** False를 돌려주고도 삭제된다.
   판정은 언제나 `GetToolBoxTab(key) is None`으로 한다.

InsertGroup은 행렬 인자를 무시하고 Type이 기하를 결정한다(0 = 1×1)는 것도 Stage 0
실측이다. 그래서 한 그룹에 버튼을 여러 개 밀어 넣지 않고 버튼마다 Type 0 그룹을
하나씩 쓴다.

**기본 탭**: 등록된 액션이 0개여도 탭은 선다. 워커가 한/글에 붙는 순간 레지스트리
내용대로 구성하므로, 한/글을 다시 켜도 탭이 저절로 돌아온다. 액션이 없을 때 무엇을
올릴지는 실측으로 정했다 — 툴박스가 0개인 **빈 탭도 탭 띠에는 그려지지만**(일회용
인스턴스 창 비트맵으로 확인) 그 탭을 고르면 리본 본문이 완전히 비어 고장처럼
보인다. 그래서 부트스트랩 AID 버튼 하나를 씨앗으로 놓는다. 이 AID는 슬롯 풀 밖이라
클릭 라우팅과 겹치지 않고, DLL의 `DoAction`이 그 AID를 받으면 ROT 모니커를 다시
publish한다 — 장식이 아니라 연결 복구 버튼이다.

COM 호출은 전부 이 구성기가 소유한 **전용 단일 스레드**에서 돈다. anyio 워커 풀의
아무 스레드에서나 `CoInitialize` 하고 UI 객체를 만들지 않기 위해서다.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Generator, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import (
    AbstractContextManager,
    contextmanager,
    redirect_stderr,
    redirect_stdout,
    suppress,
)
from importlib import import_module
from io import StringIO
from typing import (
    ClassVar,
    Final,
    Literal,
    NamedTuple,
    Protocol,
    cast,
    final,
    runtime_checkable,
)

from pydantic import BaseModel, ConfigDict

from hwp_custom_action_binding import (
    InMemoryTabKeyLedger,
    SlotPlacement,
    TabKeyRecord,
)
from hwp_custom_action_store import (
    CUSTOM_ACTION_BOOTSTRAP_AID,
    CUSTOM_ACTION_BOX_KEY,
    CUSTOM_ACTION_GROUP_KEY,
    CUSTOM_ACTION_SLOT_AIDS,
    CUSTOM_ACTION_TAB_KEY,
    CUSTOM_ACTION_TAB_KEY_SWEEP_LIMIT,
    CUSTOM_ACTION_TAB_NAME,
    CustomAction,
    new_tab_key,
    tab_key_for_generation,
    tab_key_generation,
)
from hwp_live_addon import ADDON_MONIKER_PREFIX


# Stage 0 성공값. 스타일 상수의 의미는 아직 모른다 — 문서화된 이름이 없으므로
# 실측으로 통한 값만 고정해 쓴다.
BUTTON_TYPE_STANDARD: Final = 0
BUTTON_STYLE_STAGE0: Final = 3
GROUP_TYPE_UNIT: Final = 0

# 이 튜플의 길이가 곧 탭에 올릴 수 있는 버튼의 최대 개수다. Stage 2에서 DLL이 슬롯
# AID 32개를 광고하기 시작했으므로 풀이 곧 슬롯 목록이다. 인덱스 i의 AID는 슬롯 i이고,
# 슬롯 i는 레지스트리의 order=i인 레시피다.
CUSTOM_ACTION_AID_POOL: Final[tuple[str, ...]] = CUSTOM_ACTION_SLOT_AIDS

# 액션이 0개일 때 기본 탭에 놓는 씨앗 버튼. 부트스트랩 AID는 슬롯 풀 밖이라 클릭
# 라우팅과 겹치지 않고, DLL은 이 AID의 DoAction 을 ROT publish 로 처리한다.
DEFAULT_TAB_BUTTON_LABEL: Final = "한컴MCP 연결"

# 탭 키를 모를 때 내는 문장들. 툴바에 탭 열거 API 가 없어서 "없다"를 단정할 수 없다.
_UNKNOWN_TAB_KEY_ON_REMOVE: Final = (
    "이 프로세스의 탭 키 기록이 없어, 우리가 예전에 세운 탭이 리본에 남아 있는지"
    + " 확인하지 못했습니다. 옛 순차 키만 훑을 수 있고 툴바에는 탭을 열거하는 API가"
    + " 없습니다. 남아 있다면 한/글을 다시 시작할 때 사라집니다."
)
# OpenProcess 가 이 오류로 실패하면 "죽었다"가 아니라 "모른다"다. 보호된 프로세스와
# 권한 밖 프로세스가 여기 걸린다 — 실측: 살아 있는 668개 중 168개.
_ERROR_ACCESS_DENIED: Final = 5
_ERROR_PRIVILEGE_NOT_HELD: Final = 1314
_OPEN_PROCESS_UNDECIDABLE: Final[frozenset[int]] = frozenset(
    (_ERROR_ACCESS_DENIED, _ERROR_PRIVILEGE_NOT_HELD)
)

_UNKNOWN_TAB_KEY_ON_OBSERVE: Final = (
    "이 프로세스의 탭 키 기록이 없어 탭 유무를 확인하지 못했습니다. 툴바에는 탭을"
    + " 열거하는 API가 없어, 키를 모르면 남아 있는 탭을 볼 수 없습니다."
)

_BARE_PID_MONIKER: Final = re.compile(
    re.escape(ADDON_MONIKER_PREFIX) + r"(?P<pid>[1-9][0-9]*)$"
)

type TabFailureCode = Literal[
    "com_unavailable",
    "no_bridge_moniker",
    "toolbar_unavailable",
    "delete_not_confirmed",
    "aid_reused",
    "build_failed",
    "process_died",
    "rebuild_busy",
]

# resolver 단계에서 나는 실패는 "한/글이 안 떠 있다 / COM이 없다"류라 benign하다.
# 레지스트리 변경은 그대로 두고 나중에 재시도하면 되므로 deferred로 보고한다.
# 반대로 살아 있는 UI를 만지다가 난 실패(build_failed/process_died 등)는 benign이
# 아니다. deferred로 위장하면 안 된다.
# rebuild_busy 도 benign 이다. 다른 워커가 같은 한/글을 재구성하는 중이라 잠금을
# 못 잡았을 뿐, **리본을 만진 적이 없다.** 만지다 실패한 것처럼 build_failed 로
# 말하면 거짓이다(적대 검증 실측: 잠금 대기 초과가 build_failed 로 위장했다).
_BENIGN_DEFERRAL_CODES: Final[frozenset[str]] = frozenset(
    (
        "com_unavailable",
        "no_bridge_moniker",
        "toolbar_unavailable",
        "rebuild_busy",
    )
)


class _TabKeyPlan(NamedTuple):
    """재구성 한 번이 쓸 키 계획."""

    previous_key: str | None
    next_key: str
    generation: int
    swept: tuple[str, ...]
    known: bool


class CustomActionTabError(RuntimeError):
    code: TabFailureCode
    detail: str

    def __init__(self, code: TabFailureCode, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


# --- COM surface (HncObject.tlb / HwpObject.tlb) ---------------------------
# 프로토콜로 좁혀 두면 시험이 COM 없이 삽입 호출 시퀀스를 그대로 검사할 수 있다.


class ToolBoxItemLike(Protocol):
    @property
    def Name(self) -> str: ...

    @property
    def AID(self) -> str: ...

    @property
    def UID(self) -> str: ...


class ToolBoxGroupLike(Protocol):
    @property
    def UID(self) -> str: ...

    @property
    def Type(self) -> int: ...

    @property
    def ItemCount(self) -> int: ...

    def InsertItem(self, index: int, item: ToolBoxItemLike) -> object: ...

    def GetItem(self, index: int) -> ToolBoxItemLike: ...


class ToolBoxLayoutLike(Protocol):
    @property
    def GroupCount(self) -> int: ...

    def InsertGroup(
        self,
        index: int,
        uid: str,
        group_type: int,
        rows: int,
        columns: int,
    ) -> ToolBoxGroupLike: ...

    def GetGroup(self, index: int) -> ToolBoxGroupLike: ...


class ToolBoxLike(Protocol):
    @property
    def UID(self) -> str: ...

    @property
    def LayoutCount(self) -> int: ...

    def GetLayout(self, index: int) -> ToolBoxLayoutLike: ...


class ToolBoxTabLike(Protocol):
    @property
    def UID(self) -> str: ...

    @property
    def Name(self) -> str: ...

    @property
    def NormalTabIndex(self) -> int: ...

    @property
    def ToolBoxCount(self) -> int: ...

    def InsertToolBox(self, index: int, uid: str, name: str) -> ToolBoxLike: ...

    def GetToolBox(self, index: int) -> ToolBoxLike: ...


class ToolBoxToolbarLike(Protocol):
    def InsertToolBoxTab(self, index: int, uid: str, name: str) -> ToolBoxTabLike: ...

    def GetToolBoxTab(self, uid: str) -> ToolBoxTabLike | None: ...

    def DeleteToolBoxTab(self, uid: str) -> bool: ...

    def CreateToolBoxItemButtonEx(
        self,
        name: str,
        aid: str,
        button_type: int,
        style: int,
    ) -> ToolBoxItemLike: ...


class ToolbarResolver(Protocol):
    def __call__(self, *, process_id: int | None) -> tuple[ToolBoxToolbarLike, str]: ...


class TabKeyLedgerLike(Protocol):
    """프로세스별 "지금 쓰는 탭 키" 기억. 실물은 파일, 시험은 메모리."""

    def current(self, process_id: int) -> TabKeyRecord | None: ...

    def remember(
        self,
        *,
        process_id: int,
        tab_key: str,
        generation: int,
        moniker: str | None,
        orphan_suspected: bool = False,
    ) -> None: ...

    def release(self, *, process_id: int, generation: int) -> None: ...

    def forget(self, process_id: int) -> None: ...

    def prune_dead(
        self, is_alive: Callable[[int], bool | None]
    ) -> tuple[int, ...]: ...

    def rebuild_lock(
        self, process_id: int | None
    ) -> AbstractContextManager[None]: ...


# --- pywin32 진입점 (hwp_live_rot.py 의 로더 관례를 그대로 따른다) --------------


class BindContext(Protocol):
    pass


class InterfaceIdentifier(Protocol):
    pass


class RotMoniker(Protocol):
    def GetDisplayName(self, context: BindContext, moniker: RotMoniker) -> str: ...


class DispatchSource(Protocol):
    def QueryInterface(self, interface_id: InterfaceIdentifier) -> DispatchSource: ...


class RunningObjectTable(Protocol):
    def EnumRunning(self) -> Iterable[RotMoniker]: ...

    def GetObject(self, moniker: RotMoniker) -> DispatchSource: ...


class HwpToolbarApplication(Protocol):
    @property
    def XHwpWindows(self) -> HwpWindowsLike: ...


class HwpWindowsLike(Protocol):
    @property
    def Active_XHwpWindow(self) -> HwpWindowLike: ...


class HwpWindowLike(Protocol):
    @property
    def XHwpToolbarLayout(self) -> HwpToolbarLayoutLike: ...


class HwpToolbarLayoutLike(Protocol):
    def GetToolBoxToolBar(self) -> ToolBoxToolbarLike: ...


@runtime_checkable
class PythonComModule(Protocol):
    @property
    def IID_IDispatch(self) -> InterfaceIdentifier: ...

    def CoInitialize(self) -> None: ...

    def CreateBindCtx(self, reserved: int) -> BindContext: ...

    def GetRunningObjectTable(self) -> RunningObjectTable: ...


@runtime_checkable
class Win32DynamicModule(Protocol):
    def Dispatch(self, source: DispatchSource) -> HwpToolbarApplication: ...


@runtime_checkable
class PyWinTypesModule(Protocol):
    @property
    def com_error(self) -> type[Exception]: ...


def _load_pythoncom() -> PythonComModule:
    module = import_module("pythoncom")
    if not isinstance(module, PythonComModule):
        raise CustomActionTabError(
            "com_unavailable", "Windows COM 런타임을 찾을 수 없습니다"
        )
    return module


def _load_win32_dynamic() -> Win32DynamicModule:
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        module = import_module("win32com.client.dynamic")
    if not isinstance(module, Win32DynamicModule):
        raise CustomActionTabError(
            "com_unavailable", "Windows COM 디스패치 런타임을 찾을 수 없습니다"
        )
    return module


def _load_com_error() -> type[Exception]:
    module = import_module("pywintypes")
    if not isinstance(module, PyWinTypesModule):
        raise CustomActionTabError(
            "com_unavailable", "Windows COM 오류 형식을 찾을 수 없습니다"
        )
    return module.com_error


# --- observation models ----------------------------------------------------


class _TabModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class CustomActionTabItem(_TabModel):
    label: str
    aid: str
    group_key: str


class CustomActionTabObservation(_TabModel):
    present: bool
    tab_name: str | None = None
    tab_uid: str | None = None
    normal_tab_index: int | None = None
    tool_box_count: int | None = None
    group_count: int | None = None
    items: tuple[CustomActionTabItem, ...] = ()


class CustomActionTabState(_TabModel):
    """탭 반영 결과. 레지스트리 변경 성공과 무관하게 이것만 실패할 수 있다.

    status 의미:
      applied  — 탭을 세웠고 리본의 버튼 라벨이 레지스트리와 일치한다. 액션이
                 0개면 기본 탭(씨앗 버튼 하나)이 서고 default_tab=True다.
      stale    — 탭은 세웠지만 **관측한** 리본이 놓으려던 것과 다르다. 원인은 거의
                 언제나 탭 키 캐시다: 그 프로세스에서 이미 쓴 적 있는 키로 다시
                 세우면 한/글이 첫 트리를 돌려준다(적대 검증이 이 상태를 실제로
                 재현했다 — 순차 키가 원장 상실 뒤 태운 키를 다시 집었을 때).
                 새 키는 무작위라 그 재선택이 원리적으로 없어졌지만, 상태 자체는
                 남긴다 — 삽입 성공을 믿지 않고 언제나 관측으로 판정하기 때문이다.
                 이때 `placements`는 의도가 아니라 **관측**에서 뽑는다. 그래야
                 사용자가 누른 버튼의 라벨과 실행되는 레시피가 갈리지 않는다.
      removed  — 있던 탭을 지웠다.
      absent   — 탭이 없다(지울 것도 없었다). **키를 알고 확인한** 부재다.
      unknown  — 이 프로세스의 탭 키 기록이 없어 우리 탭이 남아 있는지 확인할 수
                 없다. 툴바에 탭 열거 API 가 없으므로 키를 모르면 물어볼 방법이
                 없다. `absent`(없다)로 위장하지 않는다.
      deferred — 한/글이 안 떠 있거나 COM을 못 잡아 탭만 보류했다. benign, 재시도 가능.
      failed   — 살아 있는 UI를 만지다가 실패했다. process_died면 한/글이 죽었다.
                 benign이 아니다. deferred로 위장하지 않는다.
    """

    status: Literal[
        "applied", "stale", "removed", "absent", "unknown", "deferred", "failed"
    ]
    # 이 프로세스의 탭 키를 알고 말하는가. False 면 우리가 세운 옛 탭이 리본에 남아
    # 있어도 찾아낼 수 없다(열거 API 부재). 부재·제거를 단정하지 않는 근거다.
    tab_key_known: bool = True
    # 이번에 실제로 쓴 탭 키. 재구성할 때마다 바뀐다.
    tab_key: str | None = None
    # 이번 호출이 탭을 정말 다시 지었는가. 리본이 이미 레지스트리와 같으면 아무것도
    # 헐지 않고 False로 돌아온다 — 워커가 붙을 때마다 멀쩡한 탭을 다시 짓지 않는다.
    rebuilt: bool = True
    # 이번 재구성이 지운 옛 키. 탭 누적이 없었다는 증거다.
    previous_tab_key: str | None = None
    tab_key_generation: int | None = None
    # 원장을 잃었을 때 훑기로 찾아 지운 남은 탭들(옛 고정 키 포함).
    swept_tab_keys: tuple[str, ...] = ()
    # 새 키를 원장에 적었는가. 실패해도 탭은 서지만, 워커가 다시 뜨면 이 탭을 못
    # 지워 하나가 남을 수 있다. 숨기지 않는다.
    tab_key_persisted: bool = True
    tab_name: str = CUSTOM_ACTION_TAB_NAME
    bootstrap_aid: str = CUSTOM_ACTION_BOOTSTRAP_AID
    aid_slot_capacity: int = len(CUSTOM_ACTION_AID_POOL)
    # 등록된 액션이 0개라 씨앗 버튼 하나만 올린 기본 탭인가.
    default_tab: bool = False
    process_id: int | None = None
    moniker: str | None = None
    button_count: int = 0
    group_count: int = 0
    placed_actions: tuple[str, ...] = ()
    # `build_tab`이 실제로 만든 배치. 클릭 라우팅이 읽는 바로 그 값이지, 파생된
    # 짐작이 아니다. placed_slots[i]는 placed_actions[i]가 받은 슬롯 번호다.
    placed_slots: tuple[int, ...] = ()
    placements: tuple[SlotPlacement, ...] = ()
    pending_actions: tuple[str, ...] = ()
    deleted_existing: bool = False
    # 리본이 실제로 레지스트리를 보여 주는가. 관측으로만 판정한다.
    ribbon_reflects_registry: bool = True
    ribbon_stale_labels: tuple[str, ...] = ()
    process_alive: bool | None = None
    reason_code: TabFailureCode | None = None
    message: str | None = None
    observation: CustomActionTabObservation | None = None
    click_dispatch_limitation: str = (
        "Stage 2에서 브리지 DLL(0.5.173)이 슬롯 AID 32개를 광고하므로 버튼은 슬롯마다"
        " 별개 AID를 받고, 클릭은 슬롯 번호로 어느 custom action인지 구분됩니다."
        " 한/글은 툴박스 탭 트리를 탭 키 단위로 프로세스 수명 동안 캐시하지만(라이브"
        " 실측), 구성기가 재구성마다 **한 번도 쓴 적 없는 무작위 키**로 세우고 옛 키를"
        " 지우므로 등록·수정·삭제·재정렬은 한/글 재기동 없이 리본에 그대로 반영됩니다."
        " 남은 한계는 셋입니다. (1) 슬롯은 32개이므로 그보다 많은 액션은"
        " pending_actions에 남습니다. (2) 클릭이 실제로 실행되려면 MCP 워커가 살아서"
        " 하트비트를 쓰고 있어야 합니다. 워커가 없으면 UpdateUI가 버튼을 비활성"
        " 상태로 돌려줍니다. (3) 탭 키 원장을 잃으면(파일 삭제·손상) 그 프로세스에"
        " 우리가 세워 둔 탭을 더는 찾을 수 없습니다 — 툴바에 탭 열거 API가 없기"
        " 때문입니다. 새 탭은 정상 동작하고 옛 탭은 고아로 남으며, 그 사실은"
        " tab_key_known=false로 드러납니다(상태는 재구성이 성공하면 applied 그대로고,"
        " status=unknown은 우리 키의 탭 자체를 못 찾았을 때만 납니다)."
    )


def group_key(index: int) -> str:
    return f"{CUSTOM_ACTION_GROUP_KEY[:-1]}-{index:02d}}}"


# --- ROT resolution --------------------------------------------------------


def _initialize_com_apartment() -> None:
    """전용 COM 스레드가 살아나는 순간 아파트먼트를 연다."""
    try:
        _load_pythoncom().CoInitialize()
    except CustomActionTabError:  # pragma: no cover - Windows 전용 경로
        return


def resolve_bridge_toolbar(
    *, process_id: int | None = None
) -> tuple[ToolBoxToolbarLike, str]:
    """실행 중인 한/글의 툴박스 툴바와 그 모니커 이름을 돌려준다."""
    pythoncom = _load_pythoncom()
    dynamic = _load_win32_dynamic()
    com_error = _load_com_error()

    pythoncom.CoInitialize()
    context = pythoncom.CreateBindCtx(0)
    table = pythoncom.GetRunningObjectTable()
    wanted = None if process_id is None else f"{ADDON_MONIKER_PREFIX}{process_id}"
    seen: list[str] = []
    chosen: tuple[RotMoniker, str] | None = None
    for moniker in table.EnumRunning():
        try:
            name = moniker.GetDisplayName(context, moniker)
        except com_error:
            continue
        if not name.startswith(ADDON_MONIKER_PREFIX):
            continue
        seen.append(name)
        if wanted is not None:
            if name == wanted:
                chosen = (moniker, name)
                break
            continue
        if _BARE_PID_MONIKER.fullmatch(name) is not None and chosen is None:
            chosen = (moniker, name)
    if chosen is None:
        raise CustomActionTabError(
            "no_bridge_moniker",
            "실행 중인 한/글의 HancomLiveBridge 모니커를 찾지 못했습니다. "
            + f"관측된 모니커: {seen or '없음'}",
        )
    moniker, name = chosen
    try:
        # 체인의 중간 COM 객체를 하나도 임시로 흘리지 않는다. 한 줄 점 연쇄로 쓰면
        # 중간 IDispatch가 다음 호출 직후 해제되고, 부모 잃은 자식을 만지는 순간
        # 한/글이 즉사한다(B1). 각 단계를 지역변수로 붙들어 수명을 살린다.
        raw_object = table.GetObject(moniker)
        source = raw_object.QueryInterface(pythoncom.IID_IDispatch)
        application = dynamic.Dispatch(source)
        windows = application.XHwpWindows
        window = windows.Active_XHwpWindow
        toolbar_layout = window.XHwpToolbarLayout
        toolbar = toolbar_layout.GetToolBoxToolBar()
    except (com_error, AttributeError) as error:
        raise CustomActionTabError(
            "toolbar_unavailable",
            f"{name}에서 GetToolBoxToolBar()까지 도달하지 못했습니다: {error}",
        ) from error
    return toolbar, name


def process_is_alive(process_id: int | None) -> bool | None:
    """PID가 아직 살아 있는가. 판정 불가면 None.

    한/글을 만지다 COM 예외가 났을 때, 그게 '프로세스가 죽어서'인지 아닌지를
    가른다. B1 사고 때 compose가 예외를 삼켜 deferred라 보고했지만 한/글은 이미
    죽어 있었다. 그 거짓 보고를 막는다.

    **OpenProcess 실패를 전부 죽음으로 접으면 안 된다.** 이 머신에서 살아 있는
    프로세스 668개 중 168개가 그 접기 때문에 "죽었다"로 판정됐다(적대 검증 실측) —
    권한이 없어 못 열었을 뿐이다. 이 함수는 원장·증인 청소의 유일한 근거라,
    오판 하나가 살아 있는 한/글의 탭 키와 증인을 함께 지워 **경고 없는 고아**를
    만든다. 그래서 접근 거부는 "모른다"로 돌려준다 — 호출자는 None 을 산 것으로
    다룬다.
    """
    if process_id is None or process_id <= 0:
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:  # noqa: BLE001 - 비 Windows 경로
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    process_query_limited_information = 0x1000
    handle: int | None = cast(
        "int | None",
        kernel32.OpenProcess(process_query_limited_information, False, process_id),
    )
    if not handle:
        # 왜 못 열었는지로 갈린다. 없는 PID 면 죽은 것이고, 권한이 없으면 모르는 것이다.
        error = ctypes.get_last_error()
        return None if error in _OPEN_PROCESS_UNDECIDABLE else False
    try:
        exit_code = wintypes.DWORD()
        kernel32.GetExitCodeProcess.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        )
        ok = cast("int", kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)))
        if not ok:
            return None
        still_active = 259
        return int(exit_code.value) == still_active
    finally:
        _ = cast("int", kernel32.CloseHandle(handle))


def moniker_process_id(moniker: str) -> int | None:
    suffix = moniker[len(ADDON_MONIKER_PREFIX) :].split(".", 1)[0]
    return int(suffix) if suffix.isdecimal() else None


# --- primitive operations --------------------------------------------------


def observe_tab(
    toolbar: ToolBoxToolbarLike, *, tab_key: str | None
) -> CustomActionTabObservation:
    """읽기 전용 상태 덤프. 무엇도 만들지 않고 지우지 않는다."""
    if tab_key is None:
        return CustomActionTabObservation(present=False)
    tab = toolbar.GetToolBoxTab(tab_key)
    if tab is None:
        return CustomActionTabObservation(present=False)
    items: list[CustomActionTabItem] = []
    group_count = 0
    box_count = int(tab.ToolBoxCount)
    if box_count > 0:
        # B1: 중간 COM 객체를 지역변수로 붙든다. `tab.GetToolBox(0).GetLayout(0)`처럼
        # 연쇄하면 GetToolBox(0)가 반환한 임시 box가 GetLayout 직후 해제되고, 부모
        # 잃은 layout을 만지는 순간 한/글이 죽는다. box를 살려 둬야 layout이 산다.
        box = tab.GetToolBox(0)
        layout = box.GetLayout(0)
        group_count = int(layout.GroupCount)
        for index in range(group_count):
            group = layout.GetGroup(index)
            for item_index in range(int(group.ItemCount)):
                item = group.GetItem(item_index)
                items.append(
                    CustomActionTabItem(
                        label=str(item.Name),
                        aid=str(item.AID),
                        group_key=str(group.UID),
                    )
                )
    return CustomActionTabObservation(
        present=True,
        tab_name=str(tab.Name),
        tab_uid=str(tab.UID),
        normal_tab_index=int(tab.NormalTabIndex),
        tool_box_count=box_count,
        group_count=group_count,
        items=tuple(items),
    )


def delete_tab(
    toolbar: ToolBoxToolbarLike, *, tab_key: str = CUSTOM_ACTION_TAB_KEY
) -> bool:
    """탭이 있었으면 지우고 True. 판정은 오직 GetToolBoxTab None이다."""
    if toolbar.GetToolBoxTab(tab_key) is None:
        return False
    # 반환값은 무시한다. Stage 0에서 False를 돌려주고도 삭제된 것을 확인했다.
    _ = toolbar.DeleteToolBoxTab(tab_key)
    if toolbar.GetToolBoxTab(tab_key) is not None:
        raise CustomActionTabError(
            "delete_not_confirmed",
            f"{tab_key}: DeleteToolBoxTab 뒤에도 GetToolBoxTab이 탭을 계속 반환합니다.",
        )
    return True


def candidate_tab_keys(
    *, limit: int = CUSTOM_ACTION_TAB_KEY_SWEEP_LIMIT
) -> tuple[str, ...]:
    """우리가 만들었을 수 있는 탭 키 전부 — 옛 고정 키부터 세대 키까지.

    툴바에 탭 열거 API가 없으므로(실측) "남은 우리 탭 찾기"는 이 목록을 하나씩
    `GetToolBoxTab`으로 두드리는 수밖에 없다.
    """
    return (
        CUSTOM_ACTION_TAB_KEY,
        *(tab_key_for_generation(generation) for generation in range(limit)),
    )


def find_tab_key(
    toolbar: ToolBoxToolbarLike, *, limit: int = CUSTOM_ACTION_TAB_KEY_SWEEP_LIMIT
) -> str | None:
    """살아 있는 우리 탭의 키를 찾는다. 읽기 전용 — 아무것도 지우지 않는다."""
    found: str | None = None
    for key in candidate_tab_keys(limit=limit):
        if toolbar.GetToolBoxTab(key) is not None:
            found = key
    return found


def sweep_tabs(
    toolbar: ToolBoxToolbarLike, *, limit: int = CUSTOM_ACTION_TAB_KEY_SWEEP_LIMIT
) -> tuple[tuple[str, ...], int | None]:
    """원장을 잃었을 때의 복구. 남은 우리 탭을 전부 지운다.

    돌려주는 것은 (지운 키들, 그중 최고 세대)다. 최고 세대는 다음 세대를 어디서부터
    시작할지 정한다 — 이미 태운 키를 다시 집으면 캐시된 트리가 돌아오기 때문이다.
    """
    removed: list[str] = []
    highest: int | None = None
    for key in candidate_tab_keys(limit=limit):
        if toolbar.GetToolBoxTab(key) is None:
            continue
        if delete_tab(toolbar, tab_key=key):
            removed.append(key)
        generation = tab_key_generation(key)
        if generation is not None and (highest is None or generation > highest):
            highest = generation
    return tuple(removed), highest


def placements_for(
    actions: Sequence[CustomAction],
    observation: CustomActionTabObservation,
    *,
    aid_pool: Sequence[str] = CUSTOM_ACTION_AID_POOL,
) -> tuple[SlotPlacement, ...] | None:
    """리본이 이미 이 액션들을 그대로 들고 있으면 그 배치를, 아니면 None.

    "이미 맞다"의 판정은 **관측**이다. 라벨이 순서까지 같고, 각 버튼의 AID가 슬롯
    풀에 있어야 한다. 슬롯 번호도 짐작하지 않고 관측한 AID의 풀 위치에서 읽는다 —
    자리를 되짚는 순간 누른 버튼과 다른 레시피가 도는 그 사고가 다시 열린다.

    이게 있어야 재구성이 멱등해진다. 워커가 다시 뜰 때마다 붙는 순간 탭을 세우는데,
    이미 맞는 탭을 매번 헐고 다시 짓는 것은 사용자 한/글 UI 스레드에 대한 순수한
    낭비다(실측: 워커 hot-reload 가 반복되며 한 프로세스에서 세대가 500을 넘겼다).
    """
    buildable = tuple(actions[: len(aid_pool)])
    if len(observation.items) != len(buildable):
        return None
    placements: list[SlotPlacement] = []
    for item, action in zip(observation.items, buildable, strict=True):
        if item.label != action.label:
            return None
        if item.aid not in aid_pool:
            return None
        placements.append(
            SlotPlacement(
                slot=aid_pool.index(item.aid),
                action_id=action.id,
                aid=item.aid,
                label=item.label,
            )
        )
    return tuple(placements)


def placements_from_observation(
    actions: Sequence[CustomAction],
    observation: CustomActionTabObservation,
    *,
    aid_pool: Sequence[str] = CUSTOM_ACTION_AID_POOL,
) -> tuple[SlotPlacement, ...]:
    """리본이 **지금 보여 주는 것**에서 클릭 배치를 뽑는다.

    삽입 의도가 리본과 어긋났을 때(stale) 의도대로 배치를 발행하면, 사용자가 누른
    버튼의 라벨과 실행되는 레시피가 갈린다 — 적대 검증이 실증했다: 리본에는 ALPHA 가
    보이는데 그 자리를 누르면 bravo 가 돌았다. 그래서 어긋난 순간에는 관측이 진실이다.

    슬롯은 관측한 AID 의 풀 위치에서 읽고, 레시피는 **버튼에 실제로 적힌 라벨**로
    찾는다. 라벨이 레지스트리에 없거나 둘 이상이면 그 자리는 배치하지 않는다 —
    지어내는 대신 `unmapped` 로 정직하게 잃는다.
    """
    by_label: dict[str, list[CustomAction]] = {}
    for action in actions:
        by_label.setdefault(action.label, []).append(action)
    placements: list[SlotPlacement] = []
    for item in observation.items:
        if item.aid not in aid_pool:
            continue
        candidates = by_label.get(item.label, ())
        if len(candidates) != 1:
            continue
        placements.append(
            SlotPlacement(
                slot=aid_pool.index(item.aid),
                action_id=candidates[0].id,
                aid=item.aid,
                label=item.label,
            )
        )
    return tuple(placements)


def is_default_tab(observation: CustomActionTabObservation) -> bool:
    """관측한 탭이 씨앗 버튼 하나짜리 기본 탭인가."""
    return tuple(item.aid for item in observation.items) == (
        CUSTOM_ACTION_BOOTSTRAP_AID,
    )


def build_default_tab(
    toolbar: ToolBoxToolbarLike,
    *,
    tab_key: str,
    tab_name: str = CUSTOM_ACTION_TAB_NAME,
    label: str = DEFAULT_TAB_BUTTON_LABEL,
) -> str:
    """등록된 액션이 0개일 때 서는 탭. 씨앗 버튼 하나만 올린다.

    빈 탭(툴박스 0개)도 탭 띠에는 그려지는 것을 일회용 인스턴스 창 비트맵으로
    확인했지만, 그 탭을 고르면 리본 본문이 완전히 비어 고장으로 읽힌다. 그래서
    부트스트랩 AID 버튼 하나를 놓는다 — 슬롯 풀 밖의 AID라 클릭 라우팅과 겹치지
    않고, DLL은 이 AID의 DoAction을 ROT publish로 처리한다.

    돌려주는 것은 실제로 올린 버튼 라벨이다. 호출자는 그것으로 관측을 대조한다.
    """
    # B1: 중간 COM 객체를 전부 지역변수로 붙든다.
    tab = toolbar.InsertToolBoxTab(-1, tab_key, tab_name)
    box = tab.InsertToolBox(-1, CUSTOM_ACTION_BOX_KEY, tab_name)
    layout = box.GetLayout(0)
    group = layout.InsertGroup(-1, group_key(0), GROUP_TYPE_UNIT, 1, 1)
    button = toolbar.CreateToolBoxItemButtonEx(
        label,
        CUSTOM_ACTION_BOOTSTRAP_AID,
        BUTTON_TYPE_STANDARD,
        BUTTON_STYLE_STAGE0,
    )
    _ = group.InsertItem(-1, button)
    return label


def build_tab(
    toolbar: ToolBoxToolbarLike,
    actions: Sequence[CustomAction],
    *,
    tab_key: str,
    tab_name: str = CUSTOM_ACTION_TAB_NAME,
    aid_pool: Sequence[str] = CUSTOM_ACTION_AID_POOL,
) -> tuple[tuple[SlotPlacement, ...], tuple[str, ...]]:
    """AID 풀이 허락하는 만큼만 버튼을 만든다.

    돌려주는 것은 (**실제로 놓인 배치**, 슬롯이 없어 보류된 action id)다.

    배치를 돌려주는 것이 계약의 핵심이다. 예전에는 action id 목록만 돌려주고 클릭
    라우팅이 `action.order`로 슬롯을 되짚었다. 두 값이 같다는 보장은 "모든 쓰기가
    order를 0..n-1로 다시 매긴다"는 관례뿐이었고, order가 비연속인 파일에서
    **누른 버튼과 실행되는 레시피가 달라졌다**(적대적 검증 실증). 이제 버튼을 놓는
    쪽이 자리를 기록하고, 라우팅은 그 기록만 읽는다.

    AID 하나당 정확히 한 번만 만들고 한 번만 넣는다. 2026-08-19 실기에서 버튼 하나를
    두 그룹에 넣다가 한/글 프로세스를 잃었다. 사인이 "같은 객체 두 번 삽입"인지 "한
    탭에 같은 AID 둘"인지까지는 좁히지 못했으므로, 둘 다 못 하게 아래에서 예외로
    막는다.

    `tab_key`는 필수 인자다. 한/글이 탭 트리를 탭 키 단위로 프로세스 수명 동안
    캐시하기 때문에(라이브 실측), 재구성이 리본에 닿으려면 그 프로세스에서 한 번도
    쓰지 않은 키여야 한다. 어느 키를 쓸지는 구성기가 세대 원장으로 정한다 — 여기서
    기본값을 주면 실수로 이미 태운 키에 세우게 된다.
    """
    placed: list[SlotPlacement] = []
    pending = tuple(action.id for action in actions[len(aid_pool) :])
    buildable = tuple(actions[: len(aid_pool)])
    # 아무것도 만들기 전에 판정한다. 탭을 세운 뒤 중간에 멈추면 반쪽 탭이 남는다.
    consumed = aid_pool[: len(buildable)]
    duplicates = sorted({aid for aid in consumed if consumed.count(aid) > 1})
    if duplicates:
        raise CustomActionTabError(
            "aid_reused",
            f"AID {duplicates}에 버튼을 두 번 만들려 했습니다. 버튼은 AID당 "
            + "싱글턴이라 두 번째 삽입은 한/글을 죽입니다.",
        )
    # B1: 체인의 모든 중간 COM 객체를 지역변수로 붙든다. tab·box·layout·group을
    # 한 줄에 연쇄하지 않는다 — 임시 부모가 해제되면 자식을 만지는 순간 한/글이 죽는다.
    tab = toolbar.InsertToolBoxTab(-1, tab_key, tab_name)
    box = tab.InsertToolBox(-1, CUSTOM_ACTION_BOX_KEY, tab_name)
    layout = box.GetLayout(0)
    for index, action in enumerate(buildable):
        aid = aid_pool[index]
        group = layout.InsertGroup(-1, group_key(index), GROUP_TYPE_UNIT, 1, 1)
        button = toolbar.CreateToolBoxItemButtonEx(
            action.label,
            aid,
            BUTTON_TYPE_STANDARD,
            BUTTON_STYLE_STAGE0,
        )
        _ = group.InsertItem(-1, button)
        # 슬롯 번호는 AID 풀의 인덱스다 — 클릭이 실어 오는 바로 그 값.
        placed.append(
            SlotPlacement(
                slot=index, action_id=action.id, aid=aid, label=action.label
            )
        )
    return tuple(placed), pending


@final
class CustomActionTabComposer:
    """레지스트리 → 리본 탭. 실패는 예외가 아니라 구조화된 상태로 돌려준다."""

    __slots__ = (
        "_executor",
        "_history",
        "_keys",
        "_liveness",
        "_resolver",
        "_sweep_limit",
    )

    def __init__(
        self,
        resolver: ToolbarResolver | None = None,
        *,
        liveness_probe: Callable[[int | None], bool | None] = process_is_alive,
        key_ledger: TabKeyLedgerLike | None = None,
        sweep_limit: int = CUSTOM_ACTION_TAB_KEY_SWEEP_LIMIT,
        history_probe: Callable[[int | None], bool] | None = None,
    ) -> None:
        self._resolver = resolve_bridge_toolbar if resolver is None else resolver
        # 프로세스 생사 판정은 주입 가능하게 둔다. 실기에서는 실제 OpenProcess를,
        # 모의에서는 결정론적 스텁을 쓴다(모의 PID는 실존 프로세스가 아니므로).
        self._liveness = liveness_probe
        # 탭 키 기억. 파일 원장을 주면 워커가 다시 떠도 옛 탭을 지울 수 있다.
        # 안 주면 메모리 원장이고, 그때는 훑기 복구가 뒤를 받친다.
        self._keys: TabKeyLedgerLike = (
            InMemoryTabKeyLedger() if key_ledger is None else key_ledger
        )
        self._sweep_limit = sweep_limit
        # "이 PID 에 예전에 세운 적이 있는가"를 탭 키 원장 **밖에서** 확인하는 증인.
        # 원장을 잃었을 때 고아 탭을 정확히 가리키는 유일한 근거다.
        self._history = history_probe
        self._executor: ThreadPoolExecutor | None = None

    @property
    def key_ledger(self) -> TabKeyLedgerLike:
        return self._keys

    def adopt_key_ledger(
        self,
        ledger: TabKeyLedgerLike,
        *,
        history_probe: Callable[[int | None], bool] | None = None,
    ) -> None:
        """파일 원장을 뒤늦게 붙인다. 레지스트리 루트는 지연 생성이라서다."""
        self._keys = ledger
        if history_probe is not None:
            self._history = history_probe

    def _com_thread(self) -> ThreadPoolExecutor:
        # COM은 아파트먼트에 묶인다. anyio 워커 풀의 아무 스레드에서나 CoInitialize
        # 하고 한/글의 UI 객체를 만들면 어느 스레드가 걸릴지 모른다. 전용 스레드
        # 하나를 잡아 두고 거기서만 부른다.
        executor = self._executor
        if executor is None:
            executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="hwp-custom-action-com",
                initializer=_initialize_com_apartment,
            )
            self._executor = executor
        return executor

    def close(self) -> None:
        executor = self._executor
        if executor is not None:
            self._executor = None
            executor.shutdown(wait=True)

    def _run(self, work: Callable[[], CustomActionTabState]) -> CustomActionTabState:
        return self._com_thread().submit(work).result()

    def _deferred(self, error: CustomActionTabError) -> CustomActionTabState:
        return CustomActionTabState(
            status="deferred",
            reason_code=error.code,
            message=error.detail,
        )

    def _failed(
        self, error: BaseException, *, process_id: int | None
    ) -> CustomActionTabState:
        # resolver 단계의 benign한 실패만 deferred로 돌려준다. 그 밖에는 살아 있는
        # UI를 만지다 난 실패다 — deferred로 위장하지 않고 failed로 표면화한다.
        if (
            isinstance(error, CustomActionTabError)
            and error.code in _BENIGN_DEFERRAL_CODES
        ):
            return self._deferred(error)
        if isinstance(error, CustomActionTabError):
            # 우리가 의도적으로 던진 코드(delete_not_confirmed·aid_reused 등)는
            # 프로세스가 응답한 결과다. 생사 판정으로 덮어쓰지 않는다.
            return CustomActionTabState(
                status="failed",
                process_id=process_id,
                process_alive=self._liveness(process_id),
                reason_code=error.code,
                message=error.detail,
            )
        # 원시 COM/OS 예외만 프로세스 사망 후보다. 생사를 확인해 표면화한다.
        alive = self._liveness(process_id)
        detail = f"{type(error).__name__}: {error}"
        if alive is False:
            return CustomActionTabState(
                status="failed",
                process_id=process_id,
                process_alive=False,
                reason_code="process_died",
                message=f"한/글 프로세스가 사라졌습니다. 원인 예외: {detail}",
            )
        return CustomActionTabState(
            status="failed",
            process_id=process_id,
            process_alive=alive,
            reason_code="build_failed",
            message=detail,
        )

    # --- 탭 키 원장 접근 (기록 실패로 리본 작업을 무르지 않는다) ---------------

    @contextmanager
    def _hold_rebuild_lock(self, process_id: int | None) -> Generator[None]:
        """재구성 구간 잠금. 못 잡으면 benign 한 보류로 바꿔 던진다.

        `msvcrt` 잠금은 대기가 길어지면 OSError 로 떨어진다. 그 예외를 광역
        except 가 주워 `build_failed` 로 만들면, 리본을 만진 적도 없는데 만지다
        실패했다고 말하는 셈이다. 여기서 구별해 둔다 — 차단도 재시도 루프도
        더하지 않고, 상태만 정직하게 바꾼다.
        """
        try:
            guard = self._keys.rebuild_lock(process_id)
            _ = guard.__enter__()
        except OSError as error:
            raise CustomActionTabError(
                "rebuild_busy",
                f"다른 워커가 이 한/글({process_id})의 리본을 재구성하는 중이라 "
                + f"차례를 기다리다 물러났습니다: {error}",
            ) from error
        try:
            yield None
        finally:
            _ = guard.__exit__(None, None, None)

    def _prune_probe(self, process_id: int) -> bool | None:
        """원장 청소용 생사 판정. 실기는 OpenProcess, 시험은 주입된 스텁."""
        return self._liveness(process_id)

    def _history_known(
        self, record: TabKeyRecord | None, process_id: int | None
    ) -> bool:
        """이 프로세스의 탭 이력을 설명할 수 있는가.

        기록이 있으면 안다. 기록이 없을 때가 갈린다 — 이 한/글에 **애초에 세운 적이
        없는** 것과, 세워 놓고 **기억을 잃은** 것은 전혀 다르다. 앞은 경고할 일이
        없고, 뒤는 무작위 키라 다시 찾을 수 없는 고아 탭이 남았다는 뜻이다.

        그 둘을 가르는 증거가 `history_probe`다. 프로덕션에서는 슬롯 배치 기록을
        본다: 배치는 구성이 성공할 때마다 남으므로, 배치는 있는데 탭 키가 없으면
        그 사이에 잃은 것이다.
        """
        if record is not None:
            # 한 번 고아를 의심한 프로세스는 계속 의심한다 — 다시 구성했다고 잃어버린
            # 탭이 사라지지는 않는다.
            return not record.orphan_suspected
        if self._history is None:
            return True
        try:
            return not self._history(process_id)
        except OSError:
            return False

    def _current_record(self, process_id: int | None) -> TabKeyRecord | None:
        if process_id is None:
            return None
        try:
            return self._keys.current(process_id)
        except OSError:
            # 원장을 못 읽으면 "모른다"다. 훑기 복구가 남은 탭을 찾아 지운다.
            return None

    def _remember_key(
        self,
        process_id: int | None,
        *,
        tab_key: str,
        generation: int,
        moniker: str | None,
        orphan_suspected: bool = False,
    ) -> bool:
        if process_id is None:
            return False
        try:
            self._keys.remember(
                process_id=process_id,
                tab_key=tab_key,
                generation=generation,
                moniker=moniker,
                orphan_suspected=orphan_suspected,
            )
        except OSError:
            return False
        return True

    def _release_key(self, process_id: int | None, generation: int) -> None:
        if process_id is None:
            return
        try:
            self._keys.release(process_id=process_id, generation=generation)
        except OSError:
            return

    def _plan_keys(
        self, toolbar: ToolBoxToolbarLike, process_id: int | None
    ) -> _TabKeyPlan:
        """[지울 옛 키 · 세울 새 키 · 재구성 횟수 · 훑어 지운 키 · 키를 아는가].

        새 키는 언제나 `new_tab_key()`가 뽑는 **한 번도 쓴 적 없는** 것이다. 순차
        번호를 쓰던 때는 원장을 잃으면 0부터 다시 세며 이미 태운 키를 다시 집었고,
        한/글이 그 키로 캐시해 둔 옛 트리가 되살아났다(적대 검증 실증).

        원장이 옛 키를 알면 그것만 지운다. 모르면 옛 순차 스킴이 남긴 탭을 훑어
        지운다 — 그건 최선의 회수일 뿐이고, 못 찾은 우리 탭이 남을 수 있다는 사실은
        `tab_key_known=False`로 표면화한다.
        """
        # 죽은 한/글의 항목을 여기서 흘려 보낸다. 워커가 곱게 안 내려가면 원장이
        # 계속 자라고, PID 재사용 때 낡은 기록이 새 프로세스에 붙는다.
        with suppress(OSError):
            _ = self._keys.prune_dead(self._prune_probe)
        record = self._current_record(process_id)
        known = self._history_known(record, process_id)
        if record is None:
            swept, _highest = sweep_tabs(toolbar, limit=self._sweep_limit)
            return _TabKeyPlan(None, new_tab_key(), 0, swept, known)
        return _TabKeyPlan(
            record.tab_key, new_tab_key(), record.generation + 1, (), known
        )

    def _already_showing(
        self,
        toolbar: ToolBoxToolbarLike,
        process_id: int | None,
        actions: Sequence[CustomAction],
        moniker: str,
        tab_name: str,
    ) -> CustomActionTabState | None:
        """리본이 이미 이 레지스트리를 그대로 보여 주면 그 상태를, 아니면 None.

        재구성을 멱등하게 만든다. 워커가 붙을 때마다(= 워커가 다시 뜰 때마다) 탭을
        세우는데, 이미 맞는 탭을 헐고 다시 짓는 것은 사용자 한/글 UI 스레드에 대한
        낭비이고 세대만 태운다. 판정은 언제나 관측으로 한다.
        """
        record = self._current_record(process_id)
        if record is None or record.tab_key is None:
            return None
        observation = observe_tab(toolbar, tab_key=record.tab_key)
        if not observation.present or observation.tab_name != tab_name:
            return None
        if actions:
            placements = placements_for(actions, observation)
            if placements is None:
                return None
            pending = tuple(action.id for action in actions[len(CUSTOM_ACTION_AID_POOL) :])
            default_tab = False
        elif is_default_tab(observation):
            placements, pending, default_tab = (), (), True
        else:
            return None
        return CustomActionTabState(
            status="applied",
            rebuilt=False,
            tab_key=record.tab_key,
            # 아무것도 헐지 않았다고 해서 잃어버린 탭이 사라지지는 않는다. 멱등
            # 경로가 이걸 빠뜨려 고아 경고가 조용히 증발했다(적대 검증 실증).
            tab_key_known=self._history_known(record, process_id),
            tab_key_generation=record.generation,
            tab_name=tab_name,
            default_tab=default_tab,
            process_id=process_id,
            moniker=moniker,
            button_count=len(observation.items),
            group_count=observation.group_count or 0,
            placed_actions=tuple(placement.action_id for placement in placements),
            placed_slots=tuple(placement.slot for placement in placements),
            placements=placements,
            pending_actions=pending,
            process_alive=True,
            observation=observation,
        )

    # --- 공개 연산 -----------------------------------------------------------

    def compose(
        self,
        actions: Iterable[CustomAction],
        *,
        process_id: int | None = None,
        tab_name: str = CUSTOM_ACTION_TAB_NAME,
    ) -> CustomActionTabState:
        ordered = tuple(actions)

        def work() -> CustomActionTabState:
            try:
                toolbar, moniker = self._resolver(process_id=process_id)
            except CustomActionTabError as error:
                return self._deferred(error)
            pid = moniker_process_id(moniker) if process_id is None else process_id
            try:
                # 재구성 구간 전체를 PID 별로 잠근다. 워커 둘이 같은 창에서 [계획 →
                # 삭제 → 기록 → 빌드]를 겹치면 살아 있는 키에 다시 삽입해 탭이 둘로
                # 갈렸다(적대 검증 실증).
                with self._hold_rebuild_lock(pid):
                    settled = self._already_showing(
                        toolbar, pid, ordered, moniker, tab_name
                    )
                    if settled is not None:
                        return settled
                    plan = self._plan_keys(toolbar, pid)
                    # 순서가 계약이다: 옛 키를 먼저 지우고, 그 다음에 새 키를 세운다.
                    deleted = (
                        False
                        if plan.previous_key is None
                        else delete_tab(toolbar, tab_key=plan.previous_key)
                    )
                    # 세울 키를 **세우기 전에** 적는다. 빌드가 도중에 터져도 다음
                    # 재구성이 이 키를 지우고 넘어갈 수 있게.
                    persisted = self._remember_key(
                        pid,
                        tab_key=plan.next_key,
                        generation=plan.generation,
                        moniker=moniker,
                        orphan_suspected=not plan.known,
                    )
                    if ordered:
                        placed, pending = build_tab(
                            toolbar, ordered, tab_key=plan.next_key, tab_name=tab_name
                        )
                        intended_labels = tuple(
                            placement.label for placement in placed
                        )
                        default_tab = False
                    else:
                        # 액션이 0개여도 탭은 선다. 씨앗 버튼 하나짜리 기본 탭이다.
                        placed, pending = (), ()
                        intended_labels = (
                            build_default_tab(
                                toolbar, tab_key=plan.next_key, tab_name=tab_name
                            ),
                        )
                        default_tab = True
                    observation = observe_tab(toolbar, tab_key=plan.next_key)
            except Exception as error:  # noqa: BLE001 - COM은 무엇이든 던진다
                return self._failed(error, process_id=pid)
            # 삽입이 성공했다고 믿지 않는다. 리본이 무엇을 들고 있는지는 관측이 말한다.
            observed_labels = tuple(item.label for item in observation.items)
            reflects = observed_labels == intended_labels
            # 클릭 배치의 진실. 리본이 의도대로면 놓은 기록이 곧 진실이고, 어긋났으면
            # **관측**에서 뽑는다 — 그러지 않으면 누른 라벨과 실행이 갈린다(실증).
            routing = (
                placed
                if reflects
                else placements_from_observation(ordered, observation)
            )
            return CustomActionTabState(
                status="applied" if reflects else "stale",
                tab_key=plan.next_key,
                tab_key_known=plan.known,
                previous_tab_key=plan.previous_key,
                tab_key_generation=plan.generation,
                swept_tab_keys=plan.swept,
                tab_key_persisted=persisted,
                tab_name=tab_name,
                default_tab=default_tab,
                process_id=pid,
                moniker=moniker,
                # 우리가 올린 버튼 수 — 기본 탭이면 씨앗 버튼 하나가 그것이다.
                # 버튼 하나에 그룹 하나이므로 둘은 같은 수다.
                button_count=len(intended_labels),
                group_count=len(intended_labels),
                placed_actions=tuple(
                    placement.action_id for placement in routing
                ),
                placed_slots=tuple(placement.slot for placement in routing),
                placements=routing,
                pending_actions=pending,
                deleted_existing=deleted or bool(plan.swept),
                ribbon_reflects_registry=reflects,
                ribbon_stale_labels=() if reflects else observed_labels,
                process_alive=True,
                observation=observation,
            )

        return self._run(work)

    def remove(self, *, process_id: int | None = None) -> CustomActionTabState:
        def work() -> CustomActionTabState:
            try:
                toolbar, moniker = self._resolver(process_id=process_id)
            except CustomActionTabError as error:
                return self._deferred(error)
            pid = moniker_process_id(moniker) if process_id is None else process_id
            try:
                with self._hold_rebuild_lock(pid):
                    record = self._current_record(pid)
                    known_key = None if record is None else record.tab_key
                    deleted = (
                        False
                        if known_key is None
                        else delete_tab(toolbar, tab_key=known_key)
                    )
                    # 원장이 모르는 탭도 훑어 지운다 — 열거 API 가 없으니 후보 키를
                    # 두드리는 수밖에 없고, 그 후보는 옛 순차 스킴뿐이다.
                    swept, _highest = sweep_tabs(toolbar, limit=self._sweep_limit)
                    observation = observe_tab(toolbar, tab_key=known_key)
                    generation = 0 if record is None else record.generation
                    known = self._history_known(record, pid)
                    self._release_key(pid, generation)
            except Exception as error:  # noqa: BLE001 - COM은 무엇이든 던진다
                return self._failed(error, process_id=pid)
            removed = deleted or bool(swept)
            # 아무것도 못 지웠고 키도 몰랐다면 "지웠다"도 "없다"도 말할 수 없다.
            # 우리 탭이 리본에 남아 있어도 키가 없으면 물어볼 방법이 없기 때문이다.
            status: Literal["removed", "absent", "unknown"] = (
                "removed" if removed else ("absent" if known else "unknown")
            )
            return CustomActionTabState(
                status=status,
                tab_key=None,
                tab_key_known=known,
                previous_tab_key=known_key,
                tab_key_generation=generation,
                swept_tab_keys=swept,
                process_id=pid,
                moniker=moniker,
                deleted_existing=removed,
                process_alive=True,
                observation=observation,
                message=None if known else _UNKNOWN_TAB_KEY_ON_REMOVE,
            )

        return self._run(work)

    def prune_dead_processes(self) -> tuple[int, ...]:
        """정말 죽은 한/글의 탭 키 기억만 버린다.

        예전에는 발견 루프의 detach 훅이 곧장 `forget`을 불렀다. 그런데 detach 는
        "스캐너가 더 이상 안 보인다"일 뿐이고, 스캐너가 한 번 헛돌면 살아 있는 한/글이
        전부 사라진 것처럼 보인다 — 그때 기억을 지우면 다음 주기에 멱등 판정을 못 해
        리본을 전면 재구성한다. 그래서 스캐너 말고 OS 에 직접 묻는다.
        """
        try:
            return self._keys.prune_dead(self._prune_probe)
        except OSError:
            return ()

    def observe(self, *, process_id: int | None = None) -> CustomActionTabState:
        def work() -> CustomActionTabState:
            try:
                toolbar, moniker = self._resolver(process_id=process_id)
            except CustomActionTabError as error:
                return self._deferred(error)
            pid = moniker_process_id(moniker) if process_id is None else process_id
            try:
                # 재구성 구간 **안에서** 본다. 밖에서 보면 [옛 탭 삭제 완료 · 새 탭
                # 생성 전]의 찰나를 잡아 "탭이 없다"고 자신 있게 말한다(적대 검증
                # 실증: known=True 인 absent). 그 순간은 관측할 상태가 아니라
                # 지나가는 중간 상태다.
                with self._hold_rebuild_lock(pid):
                    record = self._current_record(pid)
                    # 원장이 키를 알면 그것만 본다. 모르면 옛 순차 키를 훑어 찾는다 —
                    # 읽기 전용이라 아무것도 지우지 않는다.
                    key = (
                        record.tab_key
                        if record is not None
                        else find_tab_key(toolbar, limit=self._sweep_limit)
                    )
                    observation = observe_tab(toolbar, tab_key=key)
            except Exception as error:  # noqa: BLE001 - COM은 무엇이든 던진다
                return self._failed(error, process_id=pid)
            return self._observed(
                observation,
                moniker,
                pid,
                tab_key=key,
                # 탭을 못 봤는데 원장 기록도 없다면, 그건 "없다"가 아니라 "모른다"다.
                # 지금 탭이 보인다고 해서 안다고 하지 않는다 — 잃어버린 옛 탭은
                # 그것대로 남아 있고, 보이는 탭은 그 사실을 지우지 못한다.
                known=self._history_known(record, pid),
            )

        return self._run(work)

    @staticmethod
    def _observed(
        observation: CustomActionTabObservation,
        moniker: str,
        process_id: int | None,
        *,
        tab_key: str | None,
        known: bool = True,
    ) -> CustomActionTabState:
        aids = tuple(item.aid for item in observation.items)
        if observation.present:
            status: Literal["applied", "absent", "unknown"] = "applied"
        else:
            status = "absent" if known else "unknown"
        return CustomActionTabState(
            status=status,
            tab_key=tab_key if observation.present else None,
            tab_key_known=known,
            tab_key_generation=None if tab_key is None else tab_key_generation(tab_key),
            default_tab=aids == (CUSTOM_ACTION_BOOTSTRAP_AID,),
            process_id=process_id,
            moniker=moniker,
            button_count=len(observation.items),
            group_count=observation.group_count or 0,
            process_alive=True,
            observation=observation,
            message=None if known else _UNKNOWN_TAB_KEY_ON_OBSERVE,
        )
