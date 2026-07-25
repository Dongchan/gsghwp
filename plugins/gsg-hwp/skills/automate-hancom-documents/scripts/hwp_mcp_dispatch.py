from __future__ import annotations

import asyncio  # noqa: F401 -- # noqa: ANYIO_OK (concurrent Future bridge)
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial
from threading import Event, Lock
from typing import ParamSpec, TypeVar, final

import anyio

from hwp_errors import HwpLiveError


P = ParamSpec("P")
T = TypeVar("T")


@final
class McpThreadDispatcher:
    __slots__ = (
        "_closed",
        "_lifecycle_lock",
        "_operation_completions",
        "_operation_executor",
        "_watch_executor",
    )

    def __init__(self, *, watch_workers: int = 4) -> None:
        self._closed = False
        self._lifecycle_lock = Lock()
        self._operation_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="HancomMcp-Operation",
        )
        self._operation_completions: set[Event] = set()
        self._watch_executor = ThreadPoolExecutor(
            max_workers=watch_workers,
            thread_name_prefix="HancomMcp-Watch",
        )

    def _submit_operation(self, operation: Callable[[], T]) -> Future[T]:
        completion = Event()
        with self._lifecycle_lock:
            if self._closed:
                raise HwpLiveError("한컴 MCP 실행기가 이미 종료되었습니다")
            future = self._operation_executor.submit(operation)
            self._operation_completions.add(completion)

        def completed(_: Future[T]) -> None:
            completion.set()
            with self._lifecycle_lock:
                self._operation_completions.discard(completion)

        future.add_done_callback(completed)
        return future

    def _submit_watch(self, operation: Callable[[], T]) -> Future[T]:
        with self._lifecycle_lock:
            if self._closed:
                raise HwpLiveError("한컴 MCP 실행기가 이미 종료되었습니다")
            return self._watch_executor.submit(operation)

    async def run(self, operation: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        future = self._submit_operation(partial(operation, *args, **kwargs))
        return await asyncio.wrap_future(future)

    async def run_mutation(
        self,
        operation: Callable[P, T],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> T:
        future = self._submit_operation(partial(operation, *args, **kwargs))
        wrapped = asyncio.wrap_future(future)
        try:
            return await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            if future.cancel():
                raise
            with anyio.CancelScope(shield=True):
                _ = await asyncio.shield(wrapped)
            return wrapped.result()

    async def watch(
        self,
        operation: Callable[P, T],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> T:
        future = self._submit_watch(partial(operation, *args, **kwargs))
        return await asyncio.wrap_future(future)

    async def close(self, operation: Callable[[], None]) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            pending = tuple(self._operation_completions)

            def ordered_cleanup() -> None:
                for completion in pending:
                    _ = completion.wait()
                operation()

            cleanup = self._operation_executor.submit(ordered_cleanup)
        try:
            await asyncio.shield(asyncio.wrap_future(cleanup))
        finally:
            self._watch_executor.shutdown(wait=True, cancel_futures=True)
            self._operation_executor.shutdown(wait=True, cancel_futures=False)
