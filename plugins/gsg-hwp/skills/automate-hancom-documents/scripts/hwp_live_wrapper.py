from __future__ import annotations

import sys
from importlib import import_module
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from threading import Lock
from types import ModuleType
from typing import Protocol, final, override, runtime_checkable

from hwp_errors import HwpLiveError
from hwp_live_api import LiveHwpApplication


@runtime_checkable
class PyhwpxModule(Protocol):
    @property
    def Hwp(self) -> type[LiveHwpApplication]: ...


class ModuleAttributeReader(Protocol):
    def __call__(self, module: object, name: str, /) -> object: ...


_read_module_attribute: ModuleAttributeReader = getattr


@final
class DeferredModule(ModuleType):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self._loaded: ModuleType | None = None
        self._load_lock = Lock()

    @override
    def __getattr__(self, name: str) -> object:
        with self._load_lock:
            if self._loaded is None:
                self._loaded = import_module(self.__name__)
            return _read_module_attribute(self._loaded, name)


_wrapper_import_lock = Lock()
_wrapper_module: PyhwpxModule | None = None


def _load_live_wrapper_module() -> PyhwpxModule:
    global _wrapper_module
    with _wrapper_import_lock:
        if _wrapper_module is not None:
            return _wrapper_module
        placeholders: dict[str, ModuleType] = {}
        for name in ("numpy", "pandas"):
            if name not in sys.modules:
                placeholder = DeferredModule(name)
                placeholders[name] = placeholder
                sys.modules[name] = placeholder
        try:
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                module = import_module("pyhwpx")
        finally:
            for name, placeholder in placeholders.items():
                if sys.modules.get(name) is placeholder:
                    del sys.modules[name]
        if not isinstance(module, PyhwpxModule):
            raise HwpLiveError("pyhwpx HWP 어댑터를 찾을 수 없습니다")
        _wrapper_module = module
        return module


def preload_live_wrapper() -> None:
    _ = _load_live_wrapper_module()


def detached_live_wrapper() -> LiveHwpApplication:
    module = _load_live_wrapper_module()
    wrapper_type: type[LiveHwpApplication] = type(
        "RotAttachedHwp",
        (module.Hwp,),
        {"__del__": _skip_wrapper_destructor},
    )
    return wrapper_type.__new__(wrapper_type)


def _skip_wrapper_destructor(wrapper: object) -> None:
    _ = wrapper
