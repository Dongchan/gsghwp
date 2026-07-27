from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from json import JSONDecodeError, dumps, loads
from pathlib import Path
from typing import Final, Literal, assert_never

from hwp_errors import HwpLiveError


SKILL_NAME: Final = "automate-hancom-documents"
type ObservedLegacyJunctionState = Literal[
    "absent",
    "current",
    "other",
    "unreadable",
]
type LegacyJunctionState = ObservedLegacyJunctionState | Literal["not_checked"]


@dataclass(frozen=True, slots=True)
class CodexSkillRegistration:
    path: Path
    source: Path
    created: bool


@dataclass(frozen=True, slots=True)
class CodexSkillRegistrationDiagnostic:
    manifest_path: Path
    manifest_state: Literal["ready", "invalid"]
    source: Path | None
    legacy_path: Path
    legacy_state: LegacyJunctionState
    detail: str | None = None
    owner: Literal["plugin_manifest"] = "plugin_manifest"

    @property
    def active_sources(self) -> tuple[Path, ...]:
        return () if self.source is None else (self.source,)

    def startup_record(self) -> str:
        return dumps(
            {
                "event": "hwp_codex_skill_registration",
                "owner": self.owner,
                "manifest_path": str(self.manifest_path),
                "manifest_state": self.manifest_state,
                "source": None if self.source is None else str(self.source),
                "active_source_count": len(self.active_sources),
                "legacy_path": str(self.legacy_path),
                "legacy_state": self.legacy_state,
                "detail": self.detail,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


def _package_root(package_root: Path | None) -> Path:
    root = Path(__file__).parents[3] if package_root is None else package_root
    return root.resolve()


def _plugin_skill_source(root: Path) -> tuple[Path, Path]:
    manifest_path = root / ".codex-plugin" / "plugin.json"
    try:
        manifest = loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, JSONDecodeError) as error:
        raise HwpLiveError(
            f"Codex plugin manifest를 읽을 수 없습니다: {manifest_path}: {error}"
        ) from error
    match manifest:
        case {"skills": str(skill_directory)}:
            skill_root = (root / skill_directory).resolve()
        case _:
            raise HwpLiveError(
                f"Codex plugin manifest의 skills 경로가 없습니다: {manifest_path}"
            )
    expected_root = (root / "skills").resolve()
    if skill_root != expected_root:
        raise HwpLiveError(
            f"Codex plugin manifest의 skills 경로가 패키지 skills와 다릅니다: "
            f"{skill_root}"
        )
    source = (skill_root / SKILL_NAME).resolve()
    source_skill = source / "SKILL.md"
    if not source_skill.is_file():
        raise HwpLiveError(f"패키지 한컴 스킬 파일이 없습니다: {source_skill}")
    return manifest_path, source


def _codex_home(codex_home: Path | None) -> Path:
    if codex_home is not None:
        return codex_home.expanduser().absolute()
    configured = os.environ.get("CODEX_HOME")
    if configured:
        return Path(configured).expanduser().absolute()
    return (Path.home() / ".codex").absolute()


def _legacy_junction_path(codex_home: Path | None) -> Path:
    return _codex_home(codex_home) / "skills" / SKILL_NAME


def _legacy_junction_state(
    destination: Path,
    source: Path,
) -> ObservedLegacyJunctionState:
    try:
        if not os.path.lexists(destination):
            return "absent"
        if (
            destination.is_dir()
            and destination.resolve() == source
            and (destination / "SKILL.md").is_file()
        ):
            return "current"
        return "other"
    except OSError:
        return "unreadable"


def diagnose_codex_skill_registration(
    *,
    package_root: Path | None = None,
    codex_home: Path | None = None,
) -> CodexSkillRegistrationDiagnostic:
    root = _package_root(package_root)
    manifest_path = root / ".codex-plugin" / "plugin.json"
    legacy_path = _legacy_junction_path(codex_home)
    try:
        manifest_path, source = _plugin_skill_source(root)
    except HwpLiveError as error:
        return CodexSkillRegistrationDiagnostic(
            manifest_path=manifest_path,
            manifest_state="invalid",
            source=None,
            legacy_path=legacy_path,
            legacy_state="not_checked",
            detail=str(error),
        )
    return CodexSkillRegistrationDiagnostic(
        manifest_path=manifest_path,
        manifest_state="ready",
        source=source,
        legacy_path=legacy_path,
        legacy_state=_legacy_junction_state(legacy_path, source),
    )


def startup_codex_skill_registration_record(
    *,
    package_root: Path | None = None,
    codex_home: Path | None = None,
) -> str:
    return diagnose_codex_skill_registration(
        package_root=package_root,
        codex_home=codex_home,
    ).startup_record()


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
            f"Codex 한컴 스킬 junction을 만들지 못했습니다: "
            f"{detail or completed.returncode}"
        )


def install_legacy_codex_skill_junction(
    *,
    package_root: Path | None = None,
    codex_home: Path | None = None,
) -> CodexSkillRegistration:
    root = _package_root(package_root)
    _, source = _plugin_skill_source(root)
    destination = _legacy_junction_path(codex_home)
    state = _legacy_junction_state(destination, source)
    match state:
        case "current":
            return CodexSkillRegistration(
                path=destination,
                source=source,
                created=False,
            )
        case "other" | "unreadable":
            raise HwpLiveError(
                "기존 Codex 스킬 경로가 설치 소스와 다르거나 읽을 수 없으므로 "
                f"덮어쓰지 않았습니다: {destination}"
            )
        case "absent":
            destination.parent.mkdir(parents=True, exist_ok=True)
        case unreachable:
            assert_never(unreachable)
    _create_directory_junction(destination, source)
    if _legacy_junction_state(destination, source) != "current":
        raise HwpLiveError(f"Codex 한컴 스킬 등록 검증에 실패했습니다: {destination}")
    return CodexSkillRegistration(
        path=destination,
        source=source,
        created=True,
    )


def main() -> None:
    registration = install_legacy_codex_skill_junction()
    state = "created" if registration.created else "already-current"
    print(f"legacy Codex skill junction {state}: {registration.path}")


if __name__ == "__main__":
    main()
