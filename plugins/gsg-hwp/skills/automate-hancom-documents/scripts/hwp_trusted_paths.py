"""사용자가 이름을 대서 지목한 문서·그림 경로를 여는 자리.

여기 남은 검사는 셋뿐이고, 셋 다 막는 실패가 무엇인지 말할 수 있다.

* 확장자·실재 검사 -- 한/글이 열 수 없는 파일을 열라고 보내면 그 실패는
  엔진 안에서 나므로 호출자가 무엇이 잘못됐는지 알기 어렵다. stat 한 번으로
  같은 답을 먼저 준다.
* Zone.Identifier -- 인터넷에서 받은 문서를 차단 해제 없이 여는 것을 막는다.
  이건 경로 취향이 아니라 실제 보안 표식이고, 사용자가 할 조치도 분명하다.
* 출력 덮어쓰기 금지 -- 사용자의 기존 파일을 지우는 실패를 막는다.

없앤 것과 그 이유(2026-08-23 가드 감사):

* UNC(``\\\\server\\share``)·NT 네임스페이스 경로 거부, 그리고
  ``GetDriveType`` 이 ``DRIVE_REMOTE`` 를 답하면 거부하던 검사.
  - 무엇을 막았나: 대응하는 실패 모드가 코드·주석·시험·사고 기록 어디에도
    없었다. 이 모듈에는 시험이 한 건도 없었고 근거 주석도 한 줄이 없었다.
  - 무엇을 막고 있었나: 사내 파일서버(``\\\\nas\\팀공유\\보고서.hwp``)와
    매핑된 네트워크 드라이브(``Z:``)의 문서 전부. 우회 인자가 없었다.
  - 더 나쁜 것: 실패가 fail-closed 였다. ``win32file`` 을 못 불러오거나
    ``GetDriveType`` 이 0/1 을 답하면 **로컬 C: 문서까지** 거부했다. 즉
    라이브러리 부재가 파일 거부로 번역됐다.
  - 경로를 댄 것은 사용자다. 그 경로가 회사 공유 폴더인지 아닌지는 이 코드가
    사용자 대신 판단할 일이 아니다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from hwp_errors import DocumentAutomationError


_IMAGE_EXTENSIONS: Final = frozenset(
    {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
)


def input_document(path: Path) -> Path:
    resolved = path.expanduser().absolute().resolve()
    if resolved.suffix.lower() not in {".hwp", ".hwpx"} or not resolved.is_file():
        raise DocumentAutomationError(f"HWP/HWPX 입력 파일이 없습니다: {resolved}")
    zone_path = Path(f"{resolved}:Zone.Identifier")
    try:
        zone = zone_path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return resolved
    except OSError as error:
        raise DocumentAutomationError(
            f"파일 신뢰 정보를 읽을 수 없습니다: {resolved}"
        ) from error
    if "ZoneId=3" in zone or "ZoneId=4" in zone:
        raise DocumentAutomationError("인터넷에서 받은 문서는 차단 해제 후 사용하세요")
    return resolved


def output_document(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.suffix.lower() not in {".hwp", ".hwpx"}:
        raise DocumentAutomationError("출력 확장자는 .hwp 또는 .hwpx여야 합니다")
    if resolved.exists():
        raise DocumentAutomationError(f"출력 파일이 이미 있습니다: {resolved}")
    return resolved


def input_local_image(path: Path) -> Path:
    expanded = path.expanduser()
    absolute = expanded if expanded.is_absolute() else expanded.absolute()
    if absolute.suffix.lower() not in _IMAGE_EXTENSIONS:
        raise DocumentAutomationError(f"지원하지 않는 그림 확장자입니다: {absolute}")
    return absolute
