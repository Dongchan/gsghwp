from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import final

from hwp_native_graph_cache import (
    NativeGraphCache,
    NativeGraphSnapshot,
    NativeGraphTransferProtocol,
)
from hwp_native_graph_models import NativeGraphQuery
from _hwp_native_graph_errors import GraphProtocolError
from hwp_live_native_batch import (
    bind_graph_query_content_freshness,
    current_native_document_window,
    query_native_graph,
    query_native_graph_pinned,
)


NativeQueryCallable = Callable[[NativeGraphQuery], Iterable[bytes]]
GraphQueryBinder = Callable[[NativeGraphQuery], tuple[NativeGraphQuery, bool]]
GraphWindowResolver = Callable[[], int | None]


@dataclass(frozen=True, slots=True)
class NativeQueryTransport:
    """Strict injected Todo 15 seam with an optional production transfer."""

    query_frames: NativeQueryCallable
    open_transfer: Callable[[NativeGraphQuery], NativeGraphTransferProtocol] | None = (
        None
    )
    open_fresh_transfer: (
        Callable[[NativeGraphQuery], NativeGraphTransferProtocol] | None
    ) = None

    def query(self, request: NativeGraphQuery) -> Iterable[bytes]:
        return self.query_frames(request)


def production_native_graph_client(
    window_handle: int, cache: NativeGraphCache
) -> NativeGraphClient:
    """Bind the existing cache/client lifecycle to production COM transport."""
    return NativeGraphClient(
        NativeQueryTransport(
            lambda request: (),
            open_transfer=lambda request: query_native_graph_pinned(
                window_handle, request
            ),
            open_fresh_transfer=lambda request: query_native_graph(
                window_handle, request
            ),
        ),
        cache,
        bind_query=lambda request: bind_graph_query_content_freshness(
            window_handle, request
        ),
    )


@final
class DeferredNativeGraphClient:
    """A production graph client for a window that is not known at startup.

    ``production_native_graph_client`` binds one window handle for its whole
    life, which suits a harness that opened the document itself. The MCP worker
    does not: the server is built before any document is connected, and the
    graph tools take a store id and a capture session id but never a window. So
    the handle is resolved per query, from whichever document the worker last
    made current, and the query is then handed to the very same production
    client -- same transport, same freshness bind, same cache.

    Constructing that client per query is a dataclass and two lambdas; the cache
    is the shared one, so generations published by an earlier query are found by
    a later one.
    """

    __slots__ = ("_cache", "_resolve_window_handle")

    def __init__(
        self,
        resolve_window_handle: GraphWindowResolver,
        cache: NativeGraphCache,
    ) -> None:
        self._resolve_window_handle = resolve_window_handle
        self._cache = cache

    def query(self, request: NativeGraphQuery) -> NativeGraphSnapshot:
        window_handle = self._resolve_window_handle()
        if window_handle is None:
            raise GraphProtocolError(
                "GRAPH_UNAVAILABLE",
                "연결된 한컴 문서가 없어 문서 그래프를 읽을 수 없습니다",
            )
        return production_native_graph_client(window_handle, self._cache).query(request)


@final
class NativeGraphClient:
    __slots__ = ("_bind_query", "_cache", "_transport")

    def __init__(
        self,
        transport: NativeQueryTransport,
        cache: NativeGraphCache,
        bind_query: GraphQueryBinder | None = None,
    ) -> None:
        self._transport = transport
        self._cache = cache
        self._bind_query = bind_query

    def query(self, request: NativeGraphQuery) -> NativeGraphSnapshot:
        bound = request
        reusable = True
        if self._bind_query is not None:
            bound, reusable = self._bind_query(request)
        transfer_factory = self._transport.open_transfer
        if transfer_factory is not None:
            selected_factory = (
                transfer_factory
                if reusable or self._transport.open_fresh_transfer is None
                else self._transport.open_fresh_transfer
            )

            def produce_transfer() -> NativeGraphTransferProtocol:
                return selected_factory(bound)

            if reusable:
                return self._cache.load_or_publish_transfer(bound, produce_transfer)
            return self._cache.publish_transfer(bound, produce_transfer)
        if reusable:
            return self._cache.load_or_publish(
                bound, lambda: self._transport.query(bound)
            )
        return self._cache.publish(bound, self._transport.query(bound))


def connected_native_graph_client(cache: NativeGraphCache) -> DeferredNativeGraphClient:
    """The production graph client the MCP worker runs with."""
    return DeferredNativeGraphClient(current_native_document_window, cache)


__all__ = [
    "DeferredNativeGraphClient",
    "GraphWindowResolver",
    "NativeGraphClient",
    "NativeQueryCallable",
    "NativeQueryTransport",
    "connected_native_graph_client",
    "production_native_graph_client",
]
