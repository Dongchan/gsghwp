"""한컴MCP 탭 — 사용자 정의 동작(custom action) 레지스트리와 슬롯 AID 풀.

이 모듈은 문서를 건드리지 않습니다. `%LOCALAPPDATA%\\HancomDocumentAutomation\\
custom-actions-v1\\custom-actions.json` 한 파일만 읽고 씁니다.

보안 불변식은 하나입니다. 스텝의 `tool`은 **현재 프로필에 실제로 등록된 공개 MCP
도구 이름**이어야 하며 `hwp_execute`와 custom-action 도구 자신은 제외됩니다
(`hwp_mcp_registration.register_mcp_tools`의 `forwardable_tools` 정의와 동일한
집합에서 자기 참조만 더 뺀 것). 원시 HAction·COM·스크립트 매크로 문자열을 담을
필드는 스키마에 존재하지 않습니다.

이 불변식을 **저장 시점과 로드 시점 양쪽에서** 지킵니다.

- 쓰기(엄격): `_write_unlocked`는 무엇을 쓰기 전에 그 레지스트리 **전체**의 모든
  스텝을 허용목록으로 다시 검증합니다. 파일이 손으로 변조돼 `hwp_execute`나
  `RunScriptMacro` 같은 스텝이 섞였다면, 그 항목을 건드리지 않는 update(label)·
  reorder조차 쓰기를 거부합니다 — 그러지 않으면 스토어 자신의 원자 쓰기가 변조
  스텝을 정식으로 재봉인하는 세탁이 됩니다. 변조 항목을 지우는 delete·purge만이
  결과 레지스트리를 깨끗하게 만들어 통과합니다.
- 읽기(관용+표시): `read()`는 변조 항목을 조용히 버리거나 통과시키지 않고, 스텝별
  `resolved` 플래그로 위반을 드러냅니다(도구 응답의 `unresolved_step_tools`).
"""

from __future__ import annotations

import msvcrt
import os
import re
import secrets
from collections.abc import Generator, Iterable, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from os import environ
from pathlib import Path
from typing import ClassVar, Final, Literal, final

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from hwp_mcp_registry import McpProfile, tool_names


CUSTOM_ACTION_SCHEMA_VERSION: Final = 1
CUSTOM_ACTION_TAB_NAME: Final = "한컴MCP"
# InsertToolBoxTab / InsertToolBox / InsertGroup 에 넘긴 **원래 문자열**이 곧 조회·
# 삭제 키다. 반환된 객체의 .UID 는 한/글이 따로 발급한 GUID 이므로 키가 아니다.
#
# 0.5.173 까지 쓰던 **고정** 탭 키. 이제는 새 탭을 이 키로 세우지 않는다 — 한/글이
# 탭 트리를 탭 키 단위로 프로세스 수명 동안 캐시하므로 같은 키의 재구성은 리본에
# 닿지 못한다(라이브 실측). 남겨 두는 이유는 하나, **옛 빌드가 남긴 탭을 지우기
# 위해서다**: 구성기는 첫 구성 때 이 키도 훑어 지운다.
CUSTOM_ACTION_TAB_KEY: Final = "{GSG-HWP-MCP-TAB}"
# 탭 키 접두사. 재구성마다 **한 번도 쓴 적 없는** 키를 새로 뽑는다.
CUSTOM_ACTION_TAB_KEY_PREFIX: Final = "{GSG-HWP-MCP-TAB-"
# 새 키의 무작위 성분 길이(16진 자릿수). 순차 세대 키는 쓰지 않는다 — 원장을 잃으면
# 세대가 0부터 다시 시작해 **이미 태운 키를 다시 집었고**, 한/글이 그 키로 캐시해 둔
# 옛 트리가 돌아왔다(적대 검증 실증: 연속 stale + 탭 2개 누적 + observe/remove 거짓
# 보고). 무작위 성분은 그 재선택을 원리적으로 없앤다.
CUSTOM_ACTION_TAB_KEY_RANDOM_DIGITS: Final = 16
# 옛 순차 스킴이 남긴 탭을 회수하기 위한 훑기 범위. 원장이 살아 있으면 훑지 않는다.
# 정직한 한계: 옛 스킴 세대가 이 범위를 넘긴 채 원장까지 잃으면 그 탭은 못 찾는다.
# 그때는 새 탭이 정상 동작하고 옛 탭은 고아로 남으며, 상태가 그 사실을 말한다.
CUSTOM_ACTION_TAB_KEY_SWEEP_LIMIT: Final = 256
CUSTOM_ACTION_BOX_KEY: Final = "{GSG-HWP-MCP-BOX}"
CUSTOM_ACTION_GROUP_KEY: Final = "{GSG-HWP-MCP-GRP}"
# Bridge.cpp 가 처음부터 광고해 온 사설 액션. Stage 2 에서도 그대로 남아 있지만
# 버튼에는 더 이상 쓰지 않는다 — 버튼은 아래 슬롯 풀을 쓴다.
CUSTOM_ACTION_BOOTSTRAP_AID: Final = "{CFB0F99F-3589-4A85-9D8B-2D6BCE5B35D1}"

# Stage 2 슬롯 AID 풀. **권위는 C++** 이다 —
# `addon/HancomLiveBridgeNative/Bridge.cpp` 의 `kSlotActions` 배열이 원본이고,
# 여기는 그 사본이다. `EnumAction` 이 광고하지 않는 AID 로 버튼을 만들면 클릭이
# 아무 데도 닿지 않으므로 두 목록은 바이트까지 같아야 한다.
# tests/test_hwp_custom_action_slot_runtime.py 가 Bridge.cpp 를 파싱해 대조한다.
#
# 슬롯 배정은 순서 기반이다: 레시피의 order 가 곧 슬롯 번호이고, 슬롯 i 의 버튼은
# 이 튜플의 i 번째 AID 로 만들어진다. 그래서 클릭이 실어 오는 슬롯 번호 하나로 어느
# 레시피인지가 결정된다 — 그것이 슬롯 풀의 목적이다.
CUSTOM_ACTION_SLOT_AID_PREFIX: Final = "{2C445309-901C-49B2-BED1-D2D9CFFB"
CUSTOM_ACTION_SLOT_COUNT: Final = 32
CUSTOM_ACTION_SLOT_AIDS: Final[tuple[str, ...]] = tuple(
    f"{CUSTOM_ACTION_SLOT_AID_PREFIX}{index:04X}}}"
    for index in range(CUSTOM_ACTION_SLOT_COUNT)
)

# 레지스트리 정원과 슬롯 수는 같아야 한다. 32개를 등록할 수 있는데 슬롯이 31개면
# 마지막 하나는 영원히 버튼이 되지 못한다.
MAX_CUSTOM_ACTIONS: Final = CUSTOM_ACTION_SLOT_COUNT
MAX_STEPS_PER_ACTION: Final = 20

CUSTOM_ACTION_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    (
        "hwp_list_custom_actions",
        "hwp_get_custom_action",
        "hwp_register_custom_action",
        "hwp_update_custom_action",
        "hwp_delete_custom_action",
        "hwp_reorder_custom_actions",
        "hwp_remove_custom_action_tab",
    )
)

_ACTION_ID_PATTERN: Final = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
_CONTROL_CHARACTERS: Final = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class CustomActionStoreError(RuntimeError):
    """레지스트리 계약 위반. 호출자에게 구조화해 돌려줄 실패다."""

    code: str
    field: str

    def __init__(self, code: str, field: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


def forwardable_step_tools(profile: McpProfile) -> frozenset[str]:
    """스텝이 참조할 수 있는 유일한 이름 집합.

    `hwp_mcp_registration.register_mcp_tools`의 `forwardable_tools`
    (`tool_names(profile) - {"hwp_execute"}`)에서 custom-action 도구 자신을 더
    뺀다. 자기 참조를 막아 두면 Stage 2에서 실행기가 붙어도 무한 재귀가 문법적으로
    불가능하다.
    """
    return tool_names(profile) - {"hwp_execute"} - CUSTOM_ACTION_TOOL_NAMES


def _clean_label(value: str, *, field: str, allow_newline: bool) -> str:
    stripped = value.strip()
    if not stripped:
        raise CustomActionStoreError("blank", field, f"{field}은(는) 비울 수 없습니다.")
    if _CONTROL_CHARACTERS.search(stripped):
        raise CustomActionStoreError(
            "control_character", field, f"{field}에 제어 문자를 넣을 수 없습니다."
        )
    if not allow_newline and "\n" in stripped:
        raise CustomActionStoreError(
            "newline", field, f"{field}에는 줄바꿈을 넣을 수 없습니다."
        )
    if allow_newline and stripped.count("\n") > 1:
        raise CustomActionStoreError(
            "newline", field, f"{field}의 줄바꿈은 한 번까지만 허용합니다."
        )
    return stripped


class CustomActionStep(BaseModel):
    """인증된 공개 도구 호출 하나. 자유 문자열 액션 필드는 없다."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    tool: str = Field(min_length=1, max_length=128)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class CustomAction(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=40)
    description: str = Field(default="", max_length=500)
    order: int = Field(ge=0)
    steps: tuple[CustomActionStep, ...] = Field(min_length=1)
    created_at: datetime
    updated_at: datetime


class CustomActionRegistry(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = CUSTOM_ACTION_SCHEMA_VERSION
    tab_name: str = CUSTOM_ACTION_TAB_NAME
    actions: tuple[CustomAction, ...] = ()


def new_tab_key() -> str:
    """이 프로세스에서 한 번도 쓴 적 없는 탭 키. 매 호출이 새것이다.

    순차 번호를 쓰지 않는 이유는 하나다. 한/글은 탭 트리를 **탭 키 단위로 프로세스
    수명 동안** 캐시하므로 한 번 쓴 키는 영구히 오염된다. 그런데 "무엇을 이미 썼는가"는
    원장(파일)에만 있고, 툴바에는 탭을 열거하는 API가 없어 한/글에 되물을 수 없다.
    원장을 잃은 순간 순차 스킴은 0부터 다시 세며 태운 키를 다시 집는다 — 그러면 옛
    트리가 되살아나 리본이 얼어붙는다(적대 검증 실증). 무작위 성분은 원장이 없어도
    재선택을 불가능하게 만든다.
    """
    body = secrets.token_hex(CUSTOM_ACTION_TAB_KEY_RANDOM_DIGITS // 2).upper()
    # 'R' 접두는 옛 순차 키와 키 공간을 갈라 놓는다 — 회수 훑기가 새 키를 세대로
    # 오독하지 않게.
    return f"{CUSTOM_ACTION_TAB_KEY_PREFIX}R{body}}}"


def tab_key_for_generation(generation: int) -> str:
    """**옛** 순차 스킴의 키. 새로 세울 때는 쓰지 않고, 회수 훑기에만 쓴다."""
    if generation < 0:
        raise ValueError("탭 키 세대는 0 이상이어야 합니다.")
    return f"{CUSTOM_ACTION_TAB_KEY_PREFIX}{generation:04X}}}"


def tab_key_generation(tab_key: str) -> int | None:
    """옛 순차 탭 키에서 세대를 되읽는다. 순차 키가 아니면 None."""
    if not tab_key.startswith(CUSTOM_ACTION_TAB_KEY_PREFIX) or not tab_key.endswith(
        "}"
    ):
        return None
    body = tab_key[len(CUSTOM_ACTION_TAB_KEY_PREFIX) : -1]
    if body.startswith("R"):
        return None
    try:
        return int(body, 16)
    except ValueError:
        return None


def default_custom_action_root() -> Path:
    local_app_data = environ.get("LOCALAPPDATA")
    root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return root / "HancomDocumentAutomation" / "custom-actions-v1"


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _validated_id(value: str) -> str:
    candidate = value.strip()
    if not _ACTION_ID_PATTERN.fullmatch(candidate):
        raise CustomActionStoreError(
            "invalid_id",
            "action_id",
            "action_id는 소문자·숫자로 시작하고 끝나며 . _ - 만 섞을 수 있습니다 (1~64자).",
        )
    return candidate


def _validated_steps(
    steps: Sequence[Mapping[str, object] | CustomActionStep],
    *,
    allowed_tools: frozenset[str],
) -> tuple[CustomActionStep, ...]:
    if not steps:
        raise CustomActionStoreError(
            "empty_steps", "steps", "steps는 최소 한 개가 필요합니다."
        )
    if len(steps) > MAX_STEPS_PER_ACTION:
        raise CustomActionStoreError(
            "too_many_steps",
            "steps",
            f"steps는 최대 {MAX_STEPS_PER_ACTION}개까지입니다.",
        )
    validated: list[CustomActionStep] = []
    for index, raw in enumerate(steps):
        try:
            step = (
                raw
                if isinstance(raw, CustomActionStep)
                else CustomActionStep.model_validate(raw)
            )
        except ValidationError as error:
            raise CustomActionStoreError(
                "invalid_step", f"steps[{index}]", str(error)
            ) from error
        if step.tool not in allowed_tools:
            raise CustomActionStoreError(
                "unauthorized_tool",
                f"steps[{index}].tool",
                f"{step.tool}은(는) 이 프로필의 전달 가능한 공개 도구가 아니므로 "
                + "custom action 스텝으로 저장할 수 없습니다.",
            )
        validated.append(step)
    return tuple(validated)


def _renumbered(actions: Iterable[CustomAction]) -> tuple[CustomAction, ...]:
    return tuple(
        action.model_copy(update={"order": index})
        for index, action in enumerate(actions)
    )


def buildable_actions(
    registry: CustomActionRegistry,
) -> tuple[CustomAction, ...]:
    """탭에 버튼으로 올라갈 액션들, 레지스트리 순서 그대로.

    **여기에 "슬롯 번호"는 없다.** 슬롯은 탭 구성기가 버튼을 실제로 놓으면서 정하고,
    그 자리는 `hwp_custom_action_binding.SlotBindingStore`에 기록된다. 클릭 라우팅은
    그 기록만 읽는다.

    예전에는 이 모듈에 `slot_assignments`/`action_for_slot`이 있어서 `action.order`로
    슬롯을 되짚었다. 두 값이 같다는 보장은 "모든 쓰기가 order를 0..n-1로 다시 매긴다"는
    관례뿐이었고, order가 비연속(0,2,5)인 파일에서 **CHARLIE 버튼이 bravo를 실행**했다
    (적대적 검증 실증). 두 번째 진실을 남겨 두면 언젠가 다시 갈라지므로 지웠다.
    """
    return tuple(registry.actions[:CUSTOM_ACTION_SLOT_COUNT])


@final
class CustomActionStore:
    """레지스트리 파일 하나에 대한 잠금·원자 쓰기·스키마 검증 게이트."""

    __slots__ = ("_lock_path", "_path", "_profile", "_root")

    def __init__(self, root: Path | None = None, *, profile: McpProfile) -> None:
        self._root = default_custom_action_root() if root is None else root
        self._root.mkdir(parents=True, exist_ok=True)
        self._path = self._root / "custom-actions.json"
        self._lock_path = self._root / ".custom-actions.lock"
        self._profile: McpProfile = profile
        try:
            with self._lock_path.open("xb") as stream:
                _ = stream.write(b"\0")
        except FileExistsError:
            pass

    @property
    def path(self) -> Path:
        return self._path

    @property
    def profile(self) -> McpProfile:
        return self._profile

    @property
    def allowed_step_tools(self) -> frozenset[str]:
        return forwardable_step_tools(self._profile)

    @contextmanager
    def _transaction(self) -> Generator[None]:
        with self._lock_path.open("r+b", buffering=0) as stream:
            _ = stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield None
            finally:
                _ = stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)

    def _read_unlocked(self) -> CustomActionRegistry:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return CustomActionRegistry()
        try:
            return CustomActionRegistry.model_validate_json(raw)
        except ValidationError as error:
            raise CustomActionStoreError(
                "corrupt_registry",
                "registry",
                f"custom action 레지스트리를 읽을 수 없습니다: {self._path}",
            ) from error

    def _assert_registry_authorized(self, registry: CustomActionRegistry) -> None:
        """레지스트리 전체 스텝을 허용목록으로 검증한다. 위반 시 쓰기 거부.

        이게 세탁 방지의 핵심이다. update(label)/reorder가 변조 항목을 그대로 둔 채
        스토어 자신의 원자 쓰기로 재봉인하는 것을 막는다.
        """
        allowed = self.allowed_step_tools
        for action in registry.actions:
            for position, step in enumerate(action.steps):
                if step.tool not in allowed:
                    raise CustomActionStoreError(
                        "tampered_registry",
                        f"actions[{action.id}].steps[{position}].tool",
                        f"{action.id}의 스텝에 허용되지 않은 도구 {step.tool!r}가 "
                        + "있어 쓰기를 거부합니다. 이 항목을 삭제한 뒤 다시 시도하세요.",
                    )

    def _write_unlocked(self, registry: CustomActionRegistry) -> None:
        # 무엇을 쓰기 전에 전체를 검증한다. 임시 파일도 만들지 않은 채 거부하므로
        # 원본은 손대지 않는다.
        self._assert_registry_authorized(registry)
        temporary = self._path.with_name(
            f"{self._path.name}.{secrets.token_hex(8)}.tmp"
        )
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                _ = stream.write(
                    registry.model_dump_json(indent=2, exclude_none=False) + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            temporary.unlink(missing_ok=True)

    def read(self) -> CustomActionRegistry:
        with self._transaction():
            return self._read_unlocked()

    def get(self, action_id: str) -> CustomAction:
        target = _validated_id(action_id)
        for action in self.read().actions:
            if action.id == target:
                return action
        raise CustomActionStoreError(
            "not_found", "action_id", f"{target} custom action이 없습니다."
        )

    def register(
        self,
        *,
        action_id: str,
        label: str,
        steps: Sequence[Mapping[str, object] | CustomActionStep],
        description: str = "",
    ) -> CustomActionRegistry:
        identifier = _validated_id(action_id)
        caption = _clean_label(label, field="label", allow_newline=True)
        summary = (
            ""
            if not description.strip()
            else _clean_label(description, field="description", allow_newline=False)
        )
        validated = _validated_steps(steps, allowed_tools=self.allowed_step_tools)
        with self._transaction():
            registry = self._read_unlocked()
            if any(action.id == identifier for action in registry.actions):
                raise CustomActionStoreError(
                    "duplicate_id",
                    "action_id",
                    f"{identifier}은(는) 이미 있습니다. 수정하려면 "
                    + "hwp_update_custom_action을 쓰세요.",
                )
            if len(registry.actions) >= MAX_CUSTOM_ACTIONS:
                raise CustomActionStoreError(
                    "capacity",
                    "actions",
                    f"custom action은 최대 {MAX_CUSTOM_ACTIONS}개까지입니다.",
                )
            moment = _now()
            created = CustomAction(
                id=identifier,
                label=caption,
                description=summary,
                order=len(registry.actions),
                steps=validated,
                created_at=moment,
                updated_at=moment,
            )
            updated = registry.model_copy(
                update={"actions": _renumbered((*registry.actions, created))}
            )
            self._write_unlocked(updated)
            return self._read_unlocked()

    def update(
        self,
        *,
        action_id: str,
        label: str | None = None,
        description: str | None = None,
        steps: Sequence[Mapping[str, object] | CustomActionStep] | None = None,
    ) -> CustomActionRegistry:
        identifier = _validated_id(action_id)
        if label is None and description is None and steps is None:
            raise CustomActionStoreError(
                "no_change",
                "arguments",
                "label·description·steps 중 최소 하나는 지정해야 합니다.",
            )
        caption = (
            None
            if label is None
            else _clean_label(label, field="label", allow_newline=True)
        )
        summary = (
            None
            if description is None
            else (
                ""
                if not description.strip()
                else _clean_label(description, field="description", allow_newline=False)
            )
        )
        validated = (
            None
            if steps is None
            else _validated_steps(steps, allowed_tools=self.allowed_step_tools)
        )
        with self._transaction():
            registry = self._read_unlocked()
            index = next(
                (
                    position
                    for position, action in enumerate(registry.actions)
                    if action.id == identifier
                ),
                None,
            )
            if index is None:
                raise CustomActionStoreError(
                    "not_found", "action_id", f"{identifier} custom action이 없습니다."
                )
            existing = registry.actions[index]
            changes: dict[str, object] = {"updated_at": _now()}
            if caption is not None:
                changes["label"] = caption
            if summary is not None:
                changes["description"] = summary
            if validated is not None:
                changes["steps"] = validated
            actions = list(registry.actions)
            actions[index] = existing.model_copy(update=changes)
            updated = registry.model_copy(update={"actions": _renumbered(actions)})
            self._write_unlocked(updated)
            return self._read_unlocked()

    def delete(self, action_id: str) -> CustomActionRegistry:
        identifier = _validated_id(action_id)
        with self._transaction():
            registry = self._read_unlocked()
            remaining = tuple(
                action for action in registry.actions if action.id != identifier
            )
            if len(remaining) == len(registry.actions):
                raise CustomActionStoreError(
                    "not_found", "action_id", f"{identifier} custom action이 없습니다."
                )
            updated = registry.model_copy(update={"actions": _renumbered(remaining)})
            self._write_unlocked(updated)
            return self._read_unlocked()

    def reorder(self, action_ids: Sequence[str]) -> CustomActionRegistry:
        ordered = [_validated_id(value) for value in action_ids]
        if len(set(ordered)) != len(ordered):
            raise CustomActionStoreError(
                "duplicate_id", "action_ids", "action_ids에 같은 id가 두 번 있습니다."
            )
        with self._transaction():
            registry = self._read_unlocked()
            existing = {action.id: action for action in registry.actions}
            if set(ordered) != set(existing):
                raise CustomActionStoreError(
                    "incomplete_order",
                    "action_ids",
                    "action_ids는 현재 등록된 custom action 전체를 정확히 한 번씩 "
                    + "담아야 합니다.",
                )
            moment = _now()
            resequenced = _renumbered(existing[value] for value in ordered)
            touched = tuple(
                action
                if action == registry.actions[action.order]
                else action.model_copy(update={"updated_at": moment})
                for action in resequenced
            )
            updated = registry.model_copy(update={"actions": touched})
            self._write_unlocked(updated)
            return self._read_unlocked()

    def purge(self) -> CustomActionRegistry:
        """레지스트리를 비운다. 파일 자체는 남겨 스키마 버전을 보존한다."""
        with self._transaction():
            registry = self._read_unlocked()
            updated = registry.model_copy(update={"actions": ()})
            self._write_unlocked(updated)
            return self._read_unlocked()
