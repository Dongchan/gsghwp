"""문서가 구역을 몇 개 가졌는지 파일에서 직접 센다. 엔진을 부르지 않는다.

왜 이것이 필요한가
------------------

되돌리기 복원에는 두 갈래가 있고, **어느 쪽이 문서를 망치는지는 크기가 아니라
구역 수가 가른다.** 실측:

===========  ========  ======  ==========================================
문서            크기     구역    파일 삽입 복원
===========  ========  ======  ==========================================
lv_e.hwp        80MB       1    정상 (28쪽 유지, 내용 복원)
lv_h1.hwp      331MB       3    **훼손** (164 -> 165쪽, 본문이 한 쪽씩 밀림)
===========  ========  ======  ==========================================

`SelectAll` + `Delete` 뒤에 남는 것은 첫 구역 하나뿐이라, `InsertFile` 이 3개
구역을 그 하나에 부으면서 쪽 설정을 잃는다. 단일 구역 문서에는 평탄화할 것이
없어서 아무 일도 안 난다.

반면 인코딩 블록 왕복은 구역을 보존한다(실측: 3구역 문서를 블록으로 떴다가
디코드하니 `Section0..2` 그대로). 그래서 **다구역 문서는 블록으로 떠야 한다.**

이 모듈이 하는 일
-----------------

캡처 *전에* 사용자 문서 파일에서 구역 수를 센다. 본문은 읽지 않는다 — `.hwp` 는
OLE 복합 파일이라 디렉터리 항목의 이름만 보면 되고, `.hwpx` 는 zip 이라 항목
이름만 보면 된다.

**파일 전체를 읽지 않는다.** 이 함수는 편집 한 번마다 도는 자리이고 대상은
331MB 짜리도 있다. 헤더와 디렉터리 섹터만 찾아 읽는다.
"""

from __future__ import annotations

import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final


_OLE_MAGIC: Final = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_DIFAT_IN_HEADER: Final = 109
_ENDOFCHAIN: Final = 0xFFFFFFFE
_FREESECT: Final = 0xFFFFFFFF
_DIRECTORY_ENTRY_BYTES: Final = 128
# 이름이 이보다 길면 그 항목은 규격을 벗어난 것이다. 64바이트 = UTF-16 32자.
_MAXIMUM_NAME_BYTES: Final = 64
# 사슬을 무한히 따라가지 않는다. 손상된 FAT 가 순환을 만들 수 있다.
_MAXIMUM_DIRECTORY_SECTORS: Final = 4096


@dataclass(frozen=True, slots=True)
class DocumentSections:
    """구역 수와, 셀 수 없었다면 그 이유.

    `count` 가 None 이면 **모른다**는 뜻이다. 0 이 아니다. 모르는 것을 1로
    가정하면 다구역 문서가 파일 삽입 경로로 흘러가 훼손된다.
    """

    count: int | None
    reason: str = ""

    @property
    def known(self) -> bool:
        return self.count is not None

    @property
    def multi_section(self) -> bool:
        """다구역으로 다뤄야 하는가.

        모르면 다구역으로 친다. 틀렸을 때의 대가가 한쪽으로 크게 기울어 있다 —
        다구역을 단일로 잘못 보면 문서가 조용히 망가지고, 단일을 다구역으로
        잘못 보면 블록으로 뜨거나(문제 없음) 안내가 하나 더 나갈 뿐이다.
        """
        return self.count is None or self.count > 1


def _read_at(handle: BinaryIO, offset: int, size: int) -> bytes:
    _ = handle.seek(offset)
    return handle.read(size)


def _sector_offset(sector: int, sector_size: int) -> int:
    return (sector + 1) * sector_size


def _difat(handle: BinaryIO, header: bytes, sector_size: int) -> list[int]:
    entries = list(struct.unpack_from(f"<{_DIFAT_IN_HEADER}I", header, 76))
    per_sector = sector_size // 4
    next_sector = struct.unpack_from("<I", header, 68)[0]
    visited: set[int] = set()
    while next_sector not in (_ENDOFCHAIN, _FREESECT) and next_sector not in visited:
        visited.add(next_sector)
        block = _read_at(handle, _sector_offset(next_sector, sector_size), sector_size)
        if len(block) < sector_size:
            break
        values = struct.unpack(f"<{per_sector}I", block)
        entries.extend(values[:-1])
        next_sector = values[-1]
    return entries


def _fat(handle: BinaryIO, difat: list[int], sector_size: int) -> list[int]:
    table: list[int] = []
    per_sector = sector_size // 4
    for sector in difat:
        if sector in (_ENDOFCHAIN, _FREESECT):
            continue
        block = _read_at(handle, _sector_offset(sector, sector_size), sector_size)
        if len(block) < sector_size:
            break
        table.extend(struct.unpack(f"<{per_sector}I", block))
    return table


def _ole_section_names(handle: BinaryIO) -> set[str]:
    header = _read_at(handle, 0, 512)
    if len(header) < 512 or header[:8] != _OLE_MAGIC:
        raise ValueError("not an OLE compound file")
    sector_size = 1 << struct.unpack_from("<H", header, 30)[0]
    if sector_size < _DIRECTORY_ENTRY_BYTES or sector_size > 1 << 20:
        raise ValueError("unsupported OLE sector size")
    table = _fat(handle, _difat(handle, header, sector_size), sector_size)

    names: set[str] = set()
    sector = struct.unpack_from("<I", header, 48)[0]
    visited: set[int] = set()
    while (
        sector not in (_ENDOFCHAIN, _FREESECT)
        and sector not in visited
        and len(visited) < _MAXIMUM_DIRECTORY_SECTORS
    ):
        visited.add(sector)
        block = _read_at(handle, _sector_offset(sector, sector_size), sector_size)
        if len(block) < _DIRECTORY_ENTRY_BYTES:
            break
        for offset in range(0, len(block) - _DIRECTORY_ENTRY_BYTES + 1, _DIRECTORY_ENTRY_BYTES):
            entry = block[offset : offset + _DIRECTORY_ENTRY_BYTES]
            name_bytes = struct.unpack_from("<H", entry, 64)[0]
            # 항목 종류: 1 저장소, 2 스트림, 5 루트. 나머지는 빈 자리다.
            if entry[66] not in (1, 2, 5):
                continue
            if name_bytes < 4 or name_bytes > _MAXIMUM_NAME_BYTES:
                continue
            name = entry[: name_bytes - 2].decode("utf-16-le", errors="replace")
            if name.startswith("Section"):
                names.add(name)
        sector = table[sector] if sector < len(table) else _ENDOFCHAIN
    return names


def _hwpx_section_names(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        return {
            name.rsplit("/", 1)[-1]
            for name in archive.namelist()
            if name.startswith("Contents/section")
        }


def count_document_sections(path: Path | str) -> DocumentSections:
    """구역 수를 센다. 셀 수 없으면 `count is None` 으로 돌려준다.

    예외를 올리지 않는다. 편집 경로에서 호출되므로, 세지 못한 것이 편집을
    막아서는 안 된다.
    """
    target = Path(path)
    try:
        with target.open("rb") as handle:
            head = handle.read(8)
            if head == _OLE_MAGIC:
                return DocumentSections(len(_ole_section_names(handle)))
    except (OSError, ValueError, struct.error) as error:
        return DocumentSections(None, f"{type(error).__name__}: {error}")

    try:
        if zipfile.is_zipfile(target):
            return DocumentSections(len(_hwpx_section_names(target)))
    except (OSError, zipfile.BadZipFile, ValueError) as error:
        return DocumentSections(None, f"{type(error).__name__}: {error}")

    return DocumentSections(None, "unrecognised document container")
