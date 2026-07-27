from __future__ import annotations

import asyncio  # noqa: F401 -- # noqa: ANYIO_OK (concurrent Future bridge)
from collections.abc import Callable, Collection
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
from functools import partial
from threading import Event, Lock
from time import monotonic
from typing import ParamSpec, TypeVar, final

import anyio

from hwp_errors import HwpLiveError


P = ParamSpec("P")
T = TypeVar("T")
_DEFAULT_OPERATION_WORKERS = 8
_DEFAULT_CLEANUP_WORKERS = 2
_DEFAULT_CLEANUP_QUIET_SECONDS = 0.1
_DEFAULT_CLEANUP_RETRY_INITIAL_SECONDS = 0.05
_DEFAULT_CLEANUP_RETRY_MAX_SECONDS = 1.0


CleanupWaiter = Callable[[Event, float], bool]


def _wait_for_event(event: Event, timeout_seconds: float) -> bool:
    return event.wait(timeout_seconds)


@dataclass(frozen=True, slots=True)
class CleanupProcessStatus:
    process_id: int
    attempts: int
    successes: int
    failures: int
    retries: int
    pending_jobs: int
    running_jobs: int
    next_retry_at: float | None
    last_error: str | None


@dataclass(frozen=True, slots=True)
class CleanupStatus:
    coalesced: int
    attempts: int
    successes: int
    failures: int
    retries: int
    pending: int
    running: int
    processes: tuple[CleanupProcessStatus, ...]


@dataclass(slots=True)
class _CleanupProcessState:
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    retries: int = 0
    pending_jobs: int = 0
    running_jobs: int = 0
    next_retry_at: float | None = None
    last_error: str | None = None


@dataclass(slots=True)
class _IdleCleanupWork:
    operation: Callable[[frozenset[int]], None]
    generation: int
    failures: int = 0
    next_attempt_at: float = 0.0
    running: bool = False


@final
class McpThreadDispatcher:
    __slots__ = (
        "_cleanup_clock",
        "_cleanup_closing",
        "_cleanup_coalesced",
        "_cleanup_completions",
        "_cleanup_executor",
        "_cleanup_processes",
        "_cleanup_quiet_seconds",
        "_cleanup_quiet_until",
        "_cleanup_retry_initial_seconds",
        "_cleanup_retry_max_seconds",
        "_cleanup_state_lock",
        "_cleanup_stop",
        "_cleanup_waiter",
        "_cleanup_wake",
        "_closed",
        "_idle_cleanup_running",
        "_idle_cleanup_work",
        "_lifecycle_lock",
        "_operation_completions",
        "_operation_executor",
        "_watch_executor",
    )

    def __init__(
        self,
        *,
        watch_workers: int = 4,
        operation_workers: int = _DEFAULT_OPERATION_WORKERS,
        cleanup_workers: int = _DEFAULT_CLEANUP_WORKERS,
        cleanup_quiet_seconds: float = _DEFAULT_CLEANUP_QUIET_SECONDS,
        cleanup_retry_initial_seconds: float = (_DEFAULT_CLEANUP_RETRY_INITIAL_SECONDS),
        cleanup_retry_max_seconds: float = _DEFAULT_CLEANUP_RETRY_MAX_SECONDS,
        cleanup_clock: Callable[[], float] = monotonic,
        cleanup_waiter: CleanupWaiter = _wait_for_event,
    ) -> None:
        if operation_workers < 1:
            raise ValueError("한컴 MCP 작업 실행기 수는 1 이상이어야 합니다")
        if cleanup_workers < 1:
            raise ValueError("한컴 MCP 정리 실행기 수는 1 이상이어야 합니다")
        if cleanup_quiet_seconds < 0:
            raise ValueError("한컴 MCP 정리 quiet 시간은 음수일 수 없습니다")
        if cleanup_retry_initial_seconds <= 0:
            raise ValueError("한컴 MCP 정리 재시도 간격은 양수여야 합니다")
        if cleanup_retry_max_seconds < cleanup_retry_initial_seconds:
            raise ValueError("한컴 MCP 정리 최대 재시도 간격이 너무 짧습니다")
        self._closed = False
        self._lifecycle_lock = Lock()
        self._cleanup_clock = cleanup_clock
        self._cleanup_closing = Event()
        self._cleanup_coalesced = 0
        self._cleanup_executor = ThreadPoolExecutor(
            max_workers=cleanup_workers,
            thread_name_prefix="HancomMcp-Cleanup",
        )
        self._cleanup_completions: set[Event] = set()
        self._cleanup_processes: dict[int, _CleanupProcessState] = {}
        self._cleanup_quiet_seconds = cleanup_quiet_seconds
        self._cleanup_quiet_until = 0.0
        self._cleanup_retry_initial_seconds = cleanup_retry_initial_seconds
        self._cleanup_retry_max_seconds = cleanup_retry_max_seconds
        self._cleanup_state_lock = Lock()
        self._cleanup_stop = Event()
        self._cleanup_waiter = cleanup_waiter
        self._cleanup_wake = Event()
        self._idle_cleanup_running = False
        self._idle_cleanup_work: dict[int, _IdleCleanupWork] = {}
        self._operation_executor = ThreadPoolExecutor(
            max_workers=operation_workers,
            thread_name_prefix="HancomMcp-Operation",
        )
        self._operation_completions: set[Event] = set()
        self._watch_executor = ThreadPoolExecutor(
            max_workers=watch_workers,
            thread_name_prefix="HancomMcp-Watch",
        )

    def _submit_operation(self, operation: Callable[[], T]) -> Future[T]:
        completion = Event()
        context = copy_context()
        with self._lifecycle_lock:
            if self._closed:
                raise HwpLiveError("한컴 MCP 실행기가 이미 종료되었습니다")
            future = self._operation_executor.submit(context.run, operation)
            self._operation_completions.add(completion)

        def completed(_: Future[T]) -> None:
            completion.set()
            with self._lifecycle_lock:
                self._operation_completions.discard(completion)

        future.add_done_callback(completed)
        return future

    def _submit_cleanup(self, operation: Callable[[], T]) -> Future[T]:
        completion = Event()
        context = copy_context()
        with self._lifecycle_lock:
            if self._closed:
                raise HwpLiveError("한컴 MCP 실행기가 이미 종료되었습니다")
            future = self._cleanup_executor.submit(context.run, operation)
            self._cleanup_completions.add(completion)

        def completed(_: Future[T]) -> None:
            completion.set()
            with self._lifecycle_lock:
                self._cleanup_completions.discard(completion)

        future.add_done_callback(completed)
        return future

    def _submit_watch(self, operation: Callable[[], T]) -> Future[T]:
        context = copy_context()
        with self._lifecycle_lock:
            if self._closed:
                raise HwpLiveError("한컴 MCP 실행기가 이미 종료되었습니다")
            return self._watch_executor.submit(context.run, operation)

    @staticmethod
    def _cleanup_error(error: BaseException) -> str:
        return f"{type(error).__name__}: {error}"

    def _cleanup_process_locked(self, process_id: int) -> _CleanupProcessState:
        state = self._cleanup_processes.get(process_id)
        if state is None:
            state = _CleanupProcessState()
            self._cleanup_processes[process_id] = state
        return state

    def _mark_cleanup_scheduled_locked(
        self,
        process_ids: Collection[int],
    ) -> None:
        for process_id in process_ids:
            self._cleanup_process_locked(process_id).pending_jobs += 1

    def _mark_cleanup_started_locked(
        self,
        process_ids: Collection[int],
        *,
        retry: bool,
    ) -> None:
        for process_id in process_ids:
            state = self._cleanup_process_locked(process_id)
            state.attempts += 1
            state.running_jobs += 1
            state.next_retry_at = None
            if retry:
                state.retries += 1

    def _mark_cleanup_failed_locked(
        self,
        process_ids: Collection[int],
        error: BaseException,
        next_retry_at: float,
    ) -> None:
        detail = self._cleanup_error(error)
        for process_id in process_ids:
            state = self._cleanup_process_locked(process_id)
            state.failures += 1
            state.running_jobs = max(0, state.running_jobs - 1)
            state.next_retry_at = next_retry_at
            state.last_error = detail

    def _mark_cleanup_succeeded_locked(
        self,
        process_ids: Collection[int],
        *,
        complete: bool,
    ) -> None:
        for process_id in process_ids:
            state = self._cleanup_process_locked(process_id)
            state.successes += 1
            state.running_jobs = max(0, state.running_jobs - 1)
            if complete:
                state.pending_jobs = max(0, state.pending_jobs - 1)
            state.next_retry_at = None
            state.last_error = None

    def _mark_cleanup_abandoned_locked(
        self,
        process_ids: Collection[int],
    ) -> None:
        for process_id in process_ids:
            state = self._cleanup_process_locked(process_id)
            state.pending_jobs = max(0, state.pending_jobs - 1)
            state.running_jobs = 0
            state.next_retry_at = None

    def _cleanup_backoff(self, failure_count: int) -> float:
        multiplier = 1 << min(max(failure_count - 1, 0), 20)
        return min(
            self._cleanup_retry_max_seconds,
            self._cleanup_retry_initial_seconds * multiplier,
        )

    def submit_retriable_cleanup(
        self,
        process_ids: Collection[int],
        operation: Callable[P, T],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> Future[None]:
        tracked_process_ids = tuple(sorted(set(process_ids)))
        bound = partial(operation, *args, **kwargs)
        with self._cleanup_state_lock:
            self._mark_cleanup_scheduled_locked(tracked_process_ids)

        def retrying_cleanup() -> None:
            failure_count = 0
            while not self._cleanup_stop.is_set():
                with self._cleanup_state_lock:
                    self._mark_cleanup_started_locked(
                        tracked_process_ids,
                        retry=failure_count > 0,
                    )
                try:
                    _ = bound()
                except Exception as error:
                    failure_count += 1
                    retry_seconds = self._cleanup_backoff(failure_count)
                    next_retry_at = self._cleanup_clock() + retry_seconds
                    with self._cleanup_state_lock:
                        self._mark_cleanup_failed_locked(
                            tracked_process_ids,
                            error,
                            next_retry_at,
                        )
                    if self._cleanup_waiter(self._cleanup_stop, retry_seconds):
                        break
                else:
                    with self._cleanup_state_lock:
                        self._mark_cleanup_succeeded_locked(
                            tracked_process_ids,
                            complete=True,
                        )
                    return
            with self._cleanup_state_lock:
                self._mark_cleanup_abandoned_locked(tracked_process_ids)

        try:
            return self._submit_cleanup(retrying_cleanup)
        except Exception as error:
            with self._cleanup_state_lock:
                detail = self._cleanup_error(error)
                for process_id in tracked_process_ids:
                    state = self._cleanup_process_locked(process_id)
                    state.failures += 1
                    state.pending_jobs = max(0, state.pending_jobs - 1)
                    state.last_error = detail
            raise

    def notify_public_activity(self) -> None:
        with self._cleanup_state_lock:
            if not self._idle_cleanup_work:
                return
            self._cleanup_quiet_until = max(
                self._cleanup_quiet_until,
                self._cleanup_clock() + self._cleanup_quiet_seconds,
            )
            self._cleanup_wake.set()

    def schedule_idle_cleanup(
        self,
        process_ids: Collection[int],
        operation: Callable[[frozenset[int]], None],
    ) -> None:
        selected_process_ids = tuple(sorted(set(process_ids)))
        if not selected_process_ids:
            return
        with self._cleanup_state_lock:
            now = self._cleanup_clock()
            self._cleanup_quiet_until = max(
                self._cleanup_quiet_until,
                now + self._cleanup_quiet_seconds,
            )
            for process_id in selected_process_ids:
                work = self._idle_cleanup_work.get(process_id)
                if work is None:
                    self._idle_cleanup_work[process_id] = _IdleCleanupWork(
                        operation=operation,
                        generation=1,
                        next_attempt_at=now,
                    )
                    self._mark_cleanup_scheduled_locked((process_id,))
                else:
                    work.operation = operation
                    work.generation += 1
                    self._cleanup_coalesced += 1
            self._cleanup_wake.set()
            if self._idle_cleanup_running:
                return
            self._idle_cleanup_running = True
            try:
                _ = self._submit_cleanup(self._run_idle_cleanup)
            except Exception as error:
                self._idle_cleanup_running = False
                detail = self._cleanup_error(error)
                for process_id in selected_process_ids:
                    state = self._cleanup_process_locked(process_id)
                    state.failures += 1
                    state.pending_jobs = max(0, state.pending_jobs - 1)
                    state.last_error = detail
                    _ = self._idle_cleanup_work.pop(process_id, None)
                raise

    def _run_idle_cleanup(self) -> None:
        while True:
            selected: tuple[int, _IdleCleanupWork, int] | None = None
            wait_seconds = 0.0
            with self._cleanup_state_lock:
                closing = self._cleanup_closing.is_set()
                if self._cleanup_stop.is_set() and not closing:
                    abandoned = tuple(self._idle_cleanup_work)
                    self._idle_cleanup_work.clear()
                    self._idle_cleanup_running = False
                    self._mark_cleanup_abandoned_locked(abandoned)
                    return
                if not self._idle_cleanup_work:
                    self._idle_cleanup_running = False
                    return
                now = self._cleanup_clock()
                quiet_remaining = (
                    0.0 if closing else max(0.0, self._cleanup_quiet_until - now)
                )
                ready = tuple(
                    process_id
                    for process_id, work in self._idle_cleanup_work.items()
                    if not work.running and (closing or work.next_attempt_at <= now)
                )
                if quiet_remaining <= 0 and ready:
                    process_id = min(ready)
                    work = self._idle_cleanup_work[process_id]
                    work.running = True
                    selected = process_id, work, work.generation
                    self._mark_cleanup_started_locked(
                        (process_id,),
                        retry=work.failures > 0,
                    )
                else:
                    retry_deadline = min(
                        (
                            work.next_attempt_at
                            for work in self._idle_cleanup_work.values()
                            if not work.running
                        ),
                        default=now + self._cleanup_retry_max_seconds,
                    )
                    wait_seconds = max(
                        0.0,
                        max(self._cleanup_quiet_until, retry_deadline) - now,
                    )
                    self._cleanup_wake.clear()
            if selected is None:
                _ = self._cleanup_waiter(self._cleanup_wake, wait_seconds)
                continue

            process_id, work, generation = selected
            try:
                work.operation(frozenset((process_id,)))
            except Exception as error:
                with self._cleanup_state_lock:
                    current = self._idle_cleanup_work.get(process_id)
                    if current is not work:
                        continue
                    work.running = False
                    work.failures += 1
                    retry_seconds = self._cleanup_backoff(work.failures)
                    work.next_attempt_at = self._cleanup_clock() + retry_seconds
                    self._mark_cleanup_failed_locked(
                        (process_id,),
                        error,
                        work.next_attempt_at,
                    )
                    if self._cleanup_closing.is_set():
                        del self._idle_cleanup_work[process_id]
                        self._mark_cleanup_abandoned_locked((process_id,))
                    self._cleanup_wake.set()
            else:
                with self._cleanup_state_lock:
                    current = self._idle_cleanup_work.get(process_id)
                    if current is not work:
                        continue
                    work.running = False
                    complete = work.generation == generation
                    self._mark_cleanup_succeeded_locked(
                        (process_id,),
                        complete=complete,
                    )
                    if complete:
                        del self._idle_cleanup_work[process_id]
                    else:
                        work.failures = 0
                        work.next_attempt_at = self._cleanup_clock()
                    self._cleanup_wake.set()

    def cleanup_status(self) -> CleanupStatus:
        with self._cleanup_state_lock:
            processes = tuple(
                CleanupProcessStatus(
                    process_id=process_id,
                    attempts=state.attempts,
                    successes=state.successes,
                    failures=state.failures,
                    retries=state.retries,
                    pending_jobs=state.pending_jobs,
                    running_jobs=state.running_jobs,
                    next_retry_at=state.next_retry_at,
                    last_error=state.last_error,
                )
                for process_id, state in sorted(self._cleanup_processes.items())
            )
            return CleanupStatus(
                coalesced=self._cleanup_coalesced,
                attempts=sum(item.attempts for item in processes),
                successes=sum(item.successes for item in processes),
                failures=sum(item.failures for item in processes),
                retries=sum(item.retries for item in processes),
                pending=sum(item.pending_jobs for item in processes),
                running=sum(item.running_jobs for item in processes),
                processes=processes,
            )

    async def run(
        self, operation: Callable[P, T], *args: P.args, **kwargs: P.kwargs
    ) -> T:
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

    def submit_cleanup(
        self,
        operation: Callable[P, T],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> Future[T]:
        return self._submit_cleanup(partial(operation, *args, **kwargs))

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
            self._cleanup_closing.set()
            self._cleanup_stop.set()
            self._cleanup_wake.set()
            pending = tuple((*self._operation_completions, *self._cleanup_completions))

            def ordered_cleanup() -> None:
                for completion in pending:
                    _ = completion.wait()
                operation()

            cleanup = self._operation_executor.submit(ordered_cleanup)
        try:
            await asyncio.shield(asyncio.wrap_future(cleanup))
        finally:
            self._watch_executor.shutdown(wait=True, cancel_futures=True)
            self._cleanup_executor.shutdown(wait=True, cancel_futures=False)
            self._operation_executor.shutdown(wait=True, cancel_futures=False)
