"""한컴MCP 탭 custom action 도구 7종 (Stage 1).

전부 문서를 건드리지 않습니다. 레지스트리 JSON 파일 하나와 리본 탭만 다룹니다.
레지스트리 변경과 탭 반영은 분리돼 있습니다 — 한/글이 안 떠 있거나 브리지 모니커가
없으면 레지스트리 변경은 그대로 성공하고 탭만 `deferred`로 보류됩니다.
"""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Annotated, ClassVar, Literal, final

from anyio import to_thread
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from hwp_custom_action_store import (
    CUSTOM_ACTION_SCHEMA_VERSION,
    CUSTOM_ACTION_TAB_NAME,
    CustomAction,
    CustomActionRegistry,
    CustomActionStep,
    CustomActionStore,
    CustomActionStoreError,
    MAX_CUSTOM_ACTIONS,
    MAX_STEPS_PER_ACTION,
)
from hwp_custom_action_binding import SlotBindingStore, TabKeyLedger
from hwp_custom_action_runtime import (
    CustomActionClickRuntime,
    CustomActionRuntimeState,
    StepDispatch,
)
from hwp_custom_action_tab import (
    CustomActionTabComposer,
    CustomActionTabState,
    process_is_alive,
)
from hwp_mcp_registry import McpProfile


class _CustomActionModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )


class CustomActionStepView(_CustomActionModel):
    tool: str
    arguments: dict[str, JsonValue]
    resolved: bool


class CustomActionView(_CustomActionModel):
    id: str
    label: str
    description: str
    order: int
    steps: tuple[CustomActionStepView, ...]
    created_at: datetime
    updated_at: datetime
    unresolved_step_tools: tuple[str, ...]


class CustomActionResponse(_CustomActionModel):
    """일곱 도구가 공유하는 한 장짜리 응답 봉투.

    `status`는 **레지스트리 연산**의 성패만 말한다(ok/error). 리본 반영 여부는 별개다 —
    `ribbon_reflects_registry`와 `tab.status`로 정직하게 표면화한다. 한/글이 안 떠
    있으면 레지스트리만 바뀌고 tab.status=deferred가 된다. 리본 반영 자체는 재구성
    때마다 새 탭 키를 쓰므로 한/글 재기동 없이 즉시 이뤄진다.
    """

    status: Literal["ok", "error"]
    schema_version: int = CUSTOM_ACTION_SCHEMA_VERSION
    registry_path: str | None = None
    tab_name: str = CUSTOM_ACTION_TAB_NAME
    action_count: int = 0
    actions: tuple[CustomActionView, ...] = ()
    action: CustomActionView | None = None
    allowed_step_tools: tuple[str, ...] = ()
    registry_purged: bool = False
    tab: CustomActionTabState | None = None
    # 클릭 실행 루프의 상태. 하트비트가 어느 한/글에 닿고 있는지, 최근 클릭이 무엇을
    # 했는지. 루프가 안 붙은 구성(시험·단독 호출)에서는 None이다.
    runtime: CustomActionRuntimeState | None = None
    # 리본이 방금 레지스트리 변경을 실제로 반영했는가. 탭을 만지지 않았으면 None.
    ribbon_reflects_registry: bool | None = None
    advisory: str | None = None
    error_code: str | None = None
    error_field: str | None = None
    message: str | None = None


def _step_view(step: CustomActionStep, allowed: frozenset[str]) -> CustomActionStepView:
    return CustomActionStepView(
        tool=step.tool,
        arguments=dict(step.arguments),
        resolved=step.tool in allowed,
    )


def _action_view(action: CustomAction, allowed: frozenset[str]) -> CustomActionView:
    steps = tuple(_step_view(step, allowed) for step in action.steps)
    return CustomActionView(
        id=action.id,
        label=action.label,
        description=action.description,
        order=action.order,
        steps=steps,
        created_at=action.created_at,
        updated_at=action.updated_at,
        unresolved_step_tools=tuple(
            dict.fromkeys(step.tool for step in steps if not step.resolved)
        ),
    )


def _ribbon_advisory(
    tab: CustomActionTabState | None,
) -> tuple[bool | None, str | None]:
    """리본이 레지스트리를 반영했는지, 아니면 왜 안 됐는지 한 줄로.

    레지스트리 변경은 status=ok로 성공했어도 리본은 다른 이야기를 할 수 있다. 그
    간극을 top-level에서 숨기지 않는다.
    """
    if tab is None:
        return None, None
    if tab.status in {"applied", "removed", "absent"}:
        if not tab.tab_key_known:
            return (
                True,
                "요청한 변경은 리본에 올렸습니다. 다만 이 한/글에 대한 탭 키 기록이 "
                + "없어, 예전에 우리가 세워 둔 탭이 남아 있는지는 확인하지 못했습니다 "
                + "(툴바에 탭을 열거하는 API가 없습니다). 남아 있다면 한/글을 다시 "
                + "시작할 때 사라집니다.",
            )
        return True, None
    if tab.status == "unknown":
        return (
            None,
            "이 한/글에 대한 탭 키 기록이 없어 탭 유무를 확인하지 못했습니다. "
            + "없다고 단정하지 마세요 — 툴바에 탭을 열거하는 API가 없어, 키를 모르면 "
            + "남아 있는 탭을 볼 수 없습니다.",
        )
    if tab.status == "stale":
        return (
            False,
            "탭은 세웠지만 관측한 리본이 놓으려던 것과 다릅니다. 한/글이 그 탭 키로 "
            + "캐시해 둔 옛 트리를 돌려준 경우입니다. tab.ribbon_stale_labels가 "
            + "사용자가 실제로 보는 라벨이고, 클릭 배치도 그 관측에서 뽑았으므로 "
            + "누른 라벨과 실행은 일치합니다. 짐작으로 메우지 말고 그대로 전하세요.",
        )
    if tab.status == "deferred":
        if tab.reason_code == "rebuild_busy":
            return (
                None,
                "레지스트리는 저장됐지만 다른 워커가 같은 한/글의 리본을 재구성하는"
                " 중이라 이번 반영은 보류됐습니다. 다음 발견 주기나 아무 custom"
                " action 도구 호출이 반영합니다.",
            )
        return (
            None,
            "레지스트리는 저장됐지만 리본은 보류됐습니다(한/글 미실행 또는 COM 미가용). "
            + "한/글을 띄운 뒤 아무 custom action 도구를 다시 호출하면 반영됩니다.",
        )
    # failed
    reason = tab.reason_code or "unknown"
    return (
        False,
        f"레지스트리는 저장됐지만 리본 반영이 실패했습니다(reason={reason}). "
        + (
            "한/글 프로세스가 사라졌을 수 있습니다. 상태를 확인하세요."
            if tab.reason_code == "process_died"
            else "tab.message를 확인하세요."
        ),
    )


def _failure(
    error: CustomActionStoreError, registry_path: Path
) -> CustomActionResponse:
    return CustomActionResponse(
        status="error",
        registry_path=str(registry_path),
        error_code=error.code,
        error_field=error.field,
        message=str(error),
    )


@final
class HwpCustomActionTools:
    """레지스트리 CRUD + 리본 탭 (재)구성. 문서는 절대 열지 않는다."""

    __slots__ = (
        "_bindings",
        "_composer",
        "_keys_attached",
        "_profile",
        "_root",
        "_runtime",
        "_store",
    )

    def __init__(
        self,
        *,
        profile: McpProfile,
        root: Path | None = None,
        composer: CustomActionTabComposer | None = None,
        runtime: CustomActionClickRuntime | None = None,
    ) -> None:
        self._profile: McpProfile = profile
        self._root = root
        self._composer = CustomActionTabComposer() if composer is None else composer
        self._store: CustomActionStore | None = None
        self._bindings: SlotBindingStore | None = None
        self._keys_attached = False
        self._runtime = runtime

    def close(self) -> None:
        """전용 COM 스레드를 접는다. 서버 lifespan이 부른다."""
        self._composer.close()

    @property
    def runtime(self) -> CustomActionClickRuntime | None:
        return self._runtime

    @property
    def bindings(self) -> SlotBindingStore:
        store = self._bindings
        if store is None:
            store = SlotBindingStore(self.store.path.parent)
            self._bindings = store
        return store

    def _attach_key_ledger(self) -> None:
        """구성기에 파일 탭 키 원장을 붙인다. 레지스트리 루트는 지연 생성이라서다.

        원장이 파일이어야 워커가 다시 떠도 옛 탭 키를 알고 지울 수 있다. 못 붙이면
        구성기는 메모리 원장으로 돌고, 그때는 훑기 복구가 뒤를 받친다.
        """
        if self._keys_attached:
            return
        try:
            ledger = TabKeyLedger(self.store.path.parent)
        except OSError:
            return
        self._composer.adopt_key_ledger(
            ledger, history_probe=self._composed_here_before
        )
        self._keys_attached = True

    def _composed_here_before(self, process_id: int | None) -> bool:
        """이 한/글에 예전에 탭을 세운 적이 있는가 — 배치 기록이 증인이다."""
        if process_id is None:
            return False
        return self.bindings.has_record(process_id)

    def build_runtime(self, dispatch: StepDispatch) -> CustomActionClickRuntime:
        """클릭 실행 루프를 만들어 붙인다. 서버 lifespan이 부르고 소유한다.

        스텝 실행은 주입받은 `dispatch` 하나로만 나간다 — 실행기가 도구를 직접 아는
        일이 없어야 공개 도구 경로의 검증·저널 계층을 우회할 방법이 없다.

        붙기/떨어지기 콜백도 여기서 건다. 워커가 한/글에 붙는 순간이 탭을 세울
        자리다 — 변이 도구 호출을 기다리지 않으므로 액션이 0개여도 탭이 생기고,
        한/글을 다시 켜면 새 PID를 발견하는 즉시 탭이 되살아난다.
        """
        runtime = CustomActionClickRuntime(
            self.store,
            dispatch,
            on_attach=self.compose_for_process,
            on_detach=self.forget_process,
        )
        self._runtime = runtime
        return runtime

    def compose_for_process(self, process_id: int) -> None:
        """워커가 방금 붙은 한/글에 레지스트리 내용대로 탭을 세운다.

        실패해도 던지지 않는다 — 클릭 루프의 발견 주기가 이걸로 죽으면 안 된다.
        결과는 다음 도구 호출의 `tab`으로 드러난다.
        """
        try:
            registry = self.store.read()
        except (CustomActionStoreError, OSError):
            return
        _ = self._compose(registry, process_id)

    def forget_process(self, process_id: int) -> None:
        """발견 루프가 이 PID를 더 못 보겠다고 알려 왔다.

        **그 말만 믿고 기억을 지우지 않는다.** detach 는 "스캐너에 안 보인다"일
        뿐이고, 스캐너가 한 번 헛돌면 살아 있는 한/글이 전부 사라진 것처럼 보인다.
        그때 탭 키를 잊으면 다음 주기에 멱등 판정을 못 해 리본을 전면 재구성하고,
        배치를 잊으면 그 사이 클릭이 unmapped 로 떨어진다(적대 검증 실증).
        그래서 여기서는 **OS 에 직접 물어** 정말 죽은 프로세스만 청소한다.
        """
        _ = process_id
        _ = self._composer.prune_dead_processes()
        with suppress(OSError):
            _ = self.bindings.prune_dead(process_is_alive)

    @property
    def store(self) -> CustomActionStore:
        # 지연 생성한다. 서버를 세우는 것만으로 %LOCALAPPDATA%에 디렉터리를
        # 만들지 않기 위해서다.
        store = self._store
        if store is None:
            store = CustomActionStore(self._root, profile=self._profile)
            self._store = store
        return store

    # --- 동기 본체 -------------------------------------------------------
    # COM과 파일 잠금은 블로킹이다. 전부 to_thread로 내보낸다.

    def _envelope(
        self,
        registry: CustomActionRegistry,
        *,
        tab: CustomActionTabState | None = None,
        action: CustomAction | None = None,
        registry_purged: bool = False,
        include_runtime: bool = False,
    ) -> CustomActionResponse:
        store = self.store
        allowed = store.allowed_step_tools
        reflects, advisory = _ribbon_advisory(tab)
        runtime = self._runtime
        runtime_state = (
            runtime.state() if include_runtime and runtime is not None else None
        )
        return CustomActionResponse(
            status="ok",
            schema_version=registry.schema_version,
            registry_path=str(store.path),
            tab_name=registry.tab_name,
            action_count=len(registry.actions),
            actions=tuple(_action_view(item, allowed) for item in registry.actions),
            action=None if action is None else _action_view(action, allowed),
            allowed_step_tools=tuple(sorted(allowed)),
            registry_purged=registry_purged,
            tab=tab,
            runtime=runtime_state,
            ribbon_reflects_registry=reflects,
            advisory=advisory,
        )

    def _compose(
        self, registry: CustomActionRegistry, process_id: int | None
    ) -> CustomActionTabState:
        self._attach_key_ledger()
        state = self._composer.compose(
            registry.actions,
            process_id=process_id,
            tab_name=registry.tab_name,
        )
        # 클릭 라우팅은 이 기록만 읽는다 — 레지스트리 order로 되짚으면 order가
        # 비연속인 순간 엉뚱한 레시피가 돌아간다(실증). 그리고 리본이 의도와
        # 어긋났을 때(stale) 구성기가 넘겨 주는 배치는 **관측에서 뽑은 것**이라,
        # 사용자가 누른 버튼의 라벨과 실행되는 레시피가 갈리지 않는다.
        if state.process_id is not None:
            with suppress(OSError):
                if state.status in {"applied", "stale"}:
                    self.bindings.publish(
                        process_id=state.process_id,
                        moniker=state.moniker,
                        placements=state.placements,
                    )
                elif state.status in {"absent", "removed"}:
                    self.bindings.forget(state.process_id)
        return state

    def _list(
        self, *, include_tab_state: bool, process_id: int | None
    ) -> CustomActionResponse:
        try:
            registry = self.store.read()
        except CustomActionStoreError as error:
            return _failure(error, self.store.path)
        if include_tab_state:
            # 관측도 원장을 봐야 어느 키를 볼지 안다. 없으면 후보 키를 훑느라
            # 느려지고, 다른 워커가 세운 탭의 키를 모른 채 보게 된다.
            self._attach_key_ledger()
        tab = (
            self._composer.observe(process_id=process_id) if include_tab_state else None
        )
        # 리본 상태를 물었다면 클릭 루프 상태도 같이 준다. 둘은 한 이야기다 — 버튼이
        # 올라갔는지와 그 버튼이 눌렸을 때 실행될 수 있는지.
        return self._envelope(registry, tab=tab, include_runtime=include_tab_state)

    def _get(self, *, action_id: str) -> CustomActionResponse:
        try:
            registry = self.store.read()
            action = self.store.get(action_id)
        except CustomActionStoreError as error:
            return _failure(error, self.store.path)
        return self._envelope(registry, action=action)

    def _register(
        self,
        *,
        action_id: str,
        label: str,
        steps: tuple[CustomActionStep, ...],
        description: str,
        process_id: int | None,
    ) -> CustomActionResponse:
        try:
            registry = self.store.register(
                action_id=action_id,
                label=label,
                steps=steps,
                description=description,
            )
        except CustomActionStoreError as error:
            return _failure(error, self.store.path)
        created = next(
            (item for item in registry.actions if item.id == action_id.strip()), None
        )
        return self._envelope(
            registry, tab=self._compose(registry, process_id), action=created
        )

    def _update(
        self,
        *,
        action_id: str,
        label: str | None,
        description: str | None,
        steps: tuple[CustomActionStep, ...] | None,
        process_id: int | None,
    ) -> CustomActionResponse:
        try:
            registry = self.store.update(
                action_id=action_id,
                label=label,
                description=description,
                steps=steps,
            )
        except CustomActionStoreError as error:
            return _failure(error, self.store.path)
        changed = next(
            (item for item in registry.actions if item.id == action_id.strip()), None
        )
        return self._envelope(
            registry, tab=self._compose(registry, process_id), action=changed
        )

    def _delete(
        self, *, action_id: str, process_id: int | None
    ) -> CustomActionResponse:
        try:
            registry = self.store.delete(action_id)
        except CustomActionStoreError as error:
            return _failure(error, self.store.path)
        return self._envelope(registry, tab=self._compose(registry, process_id))

    def _reorder(
        self, *, action_ids: tuple[str, ...], process_id: int | None
    ) -> CustomActionResponse:
        try:
            registry = self.store.reorder(action_ids)
        except CustomActionStoreError as error:
            return _failure(error, self.store.path)
        return self._envelope(registry, tab=self._compose(registry, process_id))

    def _remove_tab(
        self, *, purge_registry: bool, process_id: int | None
    ) -> CustomActionResponse:
        self._attach_key_ledger()
        tab = self._composer.remove(process_id=process_id)
        try:
            registry = self.store.purge() if purge_registry else self.store.read()
        except CustomActionStoreError as error:
            return _failure(error, self.store.path)
        return self._envelope(registry, tab=tab, registry_purged=purge_registry)

    # --- MCP 핸들러 ------------------------------------------------------

    async def hwp_list_custom_actions(
        self,
        *,
        include_tab_state: bool = False,
        process_id: Annotated[int | None, Field(ge=1)] = None,
    ) -> CustomActionResponse:
        return await to_thread.run_sync(
            partial(
                self._list,
                include_tab_state=include_tab_state,
                process_id=process_id,
            )
        )

    async def hwp_get_custom_action(
        self,
        *,
        action_id: Annotated[str, Field(min_length=1, max_length=64)],
    ) -> CustomActionResponse:
        return await to_thread.run_sync(partial(self._get, action_id=action_id))

    async def hwp_register_custom_action(
        self,
        *,
        action_id: Annotated[str, Field(min_length=1, max_length=64)],
        label: Annotated[str, Field(min_length=1, max_length=40)],
        steps: Annotated[
            tuple[CustomActionStep, ...],
            Field(min_length=1, max_length=MAX_STEPS_PER_ACTION),
        ],
        description: Annotated[str, Field(max_length=500)] = "",
        process_id: Annotated[int | None, Field(ge=1)] = None,
    ) -> CustomActionResponse:
        return await to_thread.run_sync(
            partial(
                self._register,
                action_id=action_id,
                label=label,
                steps=steps,
                description=description,
                process_id=process_id,
            )
        )

    async def hwp_update_custom_action(
        self,
        *,
        action_id: Annotated[str, Field(min_length=1, max_length=64)],
        label: Annotated[str | None, Field(max_length=40)] = None,
        description: Annotated[str | None, Field(max_length=500)] = None,
        steps: Annotated[
            tuple[CustomActionStep, ...] | None,
            Field(max_length=MAX_STEPS_PER_ACTION),
        ] = None,
        process_id: Annotated[int | None, Field(ge=1)] = None,
    ) -> CustomActionResponse:
        return await to_thread.run_sync(
            partial(
                self._update,
                action_id=action_id,
                label=label,
                description=description,
                steps=steps,
                process_id=process_id,
            )
        )

    async def hwp_delete_custom_action(
        self,
        *,
        action_id: Annotated[str, Field(min_length=1, max_length=64)],
        process_id: Annotated[int | None, Field(ge=1)] = None,
    ) -> CustomActionResponse:
        return await to_thread.run_sync(
            partial(self._delete, action_id=action_id, process_id=process_id)
        )

    async def hwp_reorder_custom_actions(
        self,
        *,
        action_ids: Annotated[
            tuple[str, ...], Field(min_length=1, max_length=MAX_CUSTOM_ACTIONS)
        ],
        process_id: Annotated[int | None, Field(ge=1)] = None,
    ) -> CustomActionResponse:
        return await to_thread.run_sync(
            partial(self._reorder, action_ids=action_ids, process_id=process_id)
        )

    async def hwp_remove_custom_action_tab(
        self,
        *,
        purge_registry: bool = False,
        process_id: Annotated[int | None, Field(ge=1)] = None,
    ) -> CustomActionResponse:
        return await to_thread.run_sync(
            partial(
                self._remove_tab,
                purge_registry=purge_registry,
                process_id=process_id,
            )
        )
