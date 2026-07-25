from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "automate-hancom-documents"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from hwp_codex_skill_install import ensure_codex_skill_registered  # noqa: E402
from hwp_errors import HwpLiveError  # noqa: E402


def test_plugin_install_registers_the_project_skill_as_one_live_source(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "package"
    source = package_root / "skills" / "automate-hancom-documents"
    source.mkdir(parents=True)
    _ = (source / "SKILL.md").write_text("# live skill\n", encoding="utf-8")
    codex_home = tmp_path / ".codex"

    registered = ensure_codex_skill_registered(
        package_root=package_root,
        codex_home=codex_home,
    )

    expected = codex_home / "skills" / "automate-hancom-documents"
    assert registered.path == expected
    assert registered.created is True
    assert expected.resolve() == source.resolve()
    assert (expected / "SKILL.md").read_text(encoding="utf-8") == "# live skill\n"


def test_skill_registration_never_overwrites_an_unrelated_directory(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "package"
    source = package_root / "skills" / "automate-hancom-documents"
    source.mkdir(parents=True)
    _ = (source / "SKILL.md").write_text("# source\n", encoding="utf-8")
    destination = tmp_path / ".codex" / "skills" / "automate-hancom-documents"
    destination.mkdir(parents=True)
    _ = (destination / "SKILL.md").write_text("# user copy\n", encoding="utf-8")

    with pytest.raises(HwpLiveError, match="덮어쓰지"):
        _ = ensure_codex_skill_registered(
            package_root=package_root,
            codex_home=tmp_path / ".codex",
        )

    assert (destination / "SKILL.md").read_text(encoding="utf-8") == "# user copy\n"


def test_skill_registration_retargets_a_previous_gsg_hwp_junction(
    tmp_path: Path,
) -> None:
    old_package = tmp_path / "old-package"
    old_source = old_package / "skills" / "automate-hancom-documents"
    old_source.mkdir(parents=True)
    _ = (old_source / "SKILL.md").write_text("# old live skill\n", encoding="utf-8")
    _ = (old_package / "compatibility-manifest.json").write_text(
        json.dumps({"distribution": "1.1.0", "developer": "inodesign"}),
        encoding="utf-8",
    )
    new_package = tmp_path / "new-package"
    new_source = new_package / "skills" / "automate-hancom-documents"
    new_source.mkdir(parents=True)
    _ = (new_source / "SKILL.md").write_text("# new live skill\n", encoding="utf-8")
    _ = (new_package / "compatibility-manifest.json").write_text(
        json.dumps({"distribution": "1.1.1", "developer": "inodesign"}),
        encoding="utf-8",
    )
    codex_home = tmp_path / ".codex"
    _ = ensure_codex_skill_registered(
        package_root=old_package,
        codex_home=codex_home,
    )

    updated = ensure_codex_skill_registered(
        package_root=new_package,
        codex_home=codex_home,
    )

    assert updated.created is True
    assert updated.path.resolve() == new_source.resolve()
    assert (updated.path / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "# new live skill\n"
