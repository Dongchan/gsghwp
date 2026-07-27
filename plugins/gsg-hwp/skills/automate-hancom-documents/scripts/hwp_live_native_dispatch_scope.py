from __future__ import annotations

from collections.abc import Callable, Generator
from concurrent.futures import Future
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from typing import TypeVar, final

from hwp_live_native_batch import (
    enter_native_dispatch_operation_scope,
    exit_native_dispatch_operation_scope,
)
from hwp_live_process_lane import HwpProcessLane


T = TypeVar("T")


@final
class PublicNativeDispatchScope:
    __slots__ = ("_active_lanes", "_closed", "_lock", "_on_activity", "_owner_id")

    def __init__(
        self,
        owner: object,
        on_activity: Callable[[], None] | None = None,
    ) -> None:
        self._active_lanes: set[HwpProcessLane] = set()
        self._closed = False
        self._lock = Lock()
        self._on_activity = on_activity
        self._owner_id = id(owner)
        self.notify_activity()

    def belongs_to(self, owner: object) -> bool:
        return self._owner_id == id(owner)

    def notify_activity(self) -> None:
        callback = self._on_activity
        if callback is None:
            return
        try:
            callback()
        except Exception:
            # Cleanup coordination must never replace a public tool result.
            return

    def run(self, lane: HwpProcessLane, operation: Callable[[], T]) -> T:
        with self._lock:
            if self._closed:
                enter_scope = False
            else:
                enter_scope = lane not in self._active_lanes
                if enter_scope:
                    enter_native_dispatch_operation_scope()
                    self._active_lanes.add(lane)
        return operation()

    def process_ids(self) -> frozenset[int]:
        with self._lock:
            return frozenset(lane.process_id for lane in self._active_lanes)

    def close(self) -> tuple[Future[None], ...]:
        with self._lock:
            if self._closed:
                return ()
            self._closed = True
            lanes = tuple(self._active_lanes)
            self._active_lanes.clear()

        futures: list[Future[None]] = []
        for lane in lanes:
            try:
                futures.append(lane.submit(exit_native_dispatch_operation_scope))
            except RuntimeError:
                # A closed lane releases its thread-local dispatch state when its
                # STA worker exits; it cannot safely accept cross-thread cleanup.
                continue
        return tuple(futures)


_PUBLIC_NATIVE_DISPATCH_SCOPE: ContextVar[PublicNativeDispatchScope | None] = (
    ContextVar(
        "hwp_public_native_dispatch_scope",
        default=None,
    )
)


@contextmanager
def public_native_dispatch_scope(
    owner: object,
    *,
    on_activity: Callable[[], None] | None = None,
) -> Generator[PublicNativeDispatchScope, None, None]:
    scope = PublicNativeDispatchScope(owner, on_activity)
    token = _PUBLIC_NATIVE_DISPATCH_SCOPE.set(scope)
    try:
        yield scope
    finally:
        _PUBLIC_NATIVE_DISPATCH_SCOPE.reset(token)


def current_public_native_dispatch_scope(
    owner: object,
) -> PublicNativeDispatchScope | None:
    scope = _PUBLIC_NATIVE_DISPATCH_SCOPE.get()
    if scope is None or not scope.belongs_to(owner):
        return None
    scope.notify_activity()
    return scope
