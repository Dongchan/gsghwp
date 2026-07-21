from __future__ import annotations

from pathlib import Path
from typing import Protocol
from winreg import HKEY_CURRENT_USER, HKEYType, OpenKey, QueryValueEx


class QueryRegistryValue(Protocol):
    def __call__(
        self,
        key: HKEYType,
        value_name: str,
        /,
    ) -> tuple[object, int]: ...


_query_registry_value: QueryRegistryValue = QueryValueEx


def is_file_path_checker_installed() -> bool:
    for registry_path in (
        r"Software\HNC\HwpAutomation\Modules",
        r"Software\Hnc\HwpUserAction\Modules",
    ):
        try:
            with OpenKey(HKEY_CURRENT_USER, registry_path) as key:
                value, _ = _query_registry_value(key, "FilePathCheckerModule")
        except OSError:
            continue
        if isinstance(value, str) and Path(value).is_file():
            return True
    return False
