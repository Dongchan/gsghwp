from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import uuid4

from hwp_errors import HwpLiveError


SKILL_NAME = "automate-hancom-documents"


@dataclass(frozen=True, slots=True)
class CodexSkillRegistration:
    path: Path
    source: Path
    created: bool


def _create_directory_junction(link: Path, target: Path) -> None:
    completed = subprocess.run(
        (
            "cmd.exe",
            "/d",
            "/c",
            "mklink",
            "/J",
            str(link),
            str(target),
        ),
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise HwpLiveError(
            f"Codex 한컴 스킬 junction을 만들지 못했습니다: {detail or completed.returncode}"
        )


def _managed_skill_junction_target(link: Path) -> Path | None:
    if not os.path.isjunction(link):
        return None
    try:
        target = link.resolve(strict=True)
        manifest_path = target.parents[1] / "compatibility-manifest.json"
        manifest = cast(
            dict[str, str],
            json.loads(manifest_path.read_text(encoding="utf-8")),
        )
    except (IndexError, OSError, ValueError, json.JSONDecodeError):
        return None
    if (
        target.name != SKILL_NAME
        or not (target / "SKILL.md").is_file()
        or manifest.get("developer") != "inodesign"
    ):
        return None
    return target


def _retarget_managed_skill_junction(destination: Path, source: Path) -> None:
    backup = destination.with_name(f".{destination.name}.gsg-hwp-backup-{uuid4().hex}")
    _ = destination.rename(backup)
    try:
        _create_directory_junction(destination, source)
    except (HwpLiveError, OSError):
        if os.path.isjunction(destination):
            os.rmdir(destination)
        _ = backup.rename(destination)
        raise
    os.rmdir(backup)


def ensure_codex_skill_registered(
    *,
    package_root: Path | None = None,
    codex_home: Path | None = None,
) -> CodexSkillRegistration:
    root = Path(__file__).parents[3] if package_root is None else package_root
    source = (root / "skills" / SKILL_NAME).resolve()
    source_skill = source / "SKILL.md"
    if not source_skill.is_file():
        raise HwpLiveError(f"패키지 한컴 스킬 파일이 없습니다: {source_skill}")
    if codex_home is None:
        configured = os.environ.get("CODEX_HOME")
        codex_home = (
            Path(configured).expanduser() if configured else Path.home() / ".codex"
        )
    destination = codex_home.expanduser().resolve() / "skills" / SKILL_NAME
    if os.path.lexists(destination):
        if destination.is_dir() and destination.resolve() == source:
            if not (destination / "SKILL.md").is_file():
                raise HwpLiveError(
                    f"등록된 Codex 한컴 스킬의 SKILL.md를 읽을 수 없습니다: {destination}"
                )
            return CodexSkillRegistration(
                path=destination,
                source=source,
                created=False,
            )
        if _managed_skill_junction_target(destination) is not None:
            _retarget_managed_skill_junction(destination, source)
            return CodexSkillRegistration(
                path=destination,
                source=source,
                created=True,
            )
        raise HwpLiveError(
            f"기존 Codex 스킬 경로가 설치 소스와 다르므로 덮어쓰지 않았습니다: {destination}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    _create_directory_junction(destination, source)
    if (
        not destination.is_dir()
        or destination.resolve() != source
        or not (destination / "SKILL.md").is_file()
    ):
        raise HwpLiveError(f"Codex 한컴 스킬 등록 검증에 실패했습니다: {destination}")
    return CodexSkillRegistration(
        path=destination,
        source=source,
        created=True,
    )
