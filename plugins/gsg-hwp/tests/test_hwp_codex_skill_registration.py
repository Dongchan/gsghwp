from __future__ import annotations

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

from hwp_codex_skill_install import (  # noqa: E402
    diagnose_codex_skill_registration,
    install_legacy_codex_skill_junction,
)
from hwp_errors import HwpLiveError  # noqa: E402


def _write_plugin_package(
    package_root: Path,
    skill_text: str = "# live skill\n",
) -> Path:
    manifest = package_root / ".codex-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    _ = manifest.write_text('{"skills":"./skills/"}\n', encoding="utf-8")
    source = package_root / "skills" / "automate-hancom-documents"
    source.mkdir(parents=True)
    _ = (source / "SKILL.md").write_text(skill_text, encoding="utf-8")
    return source


def test_plugin_manifest_exposes_skill_without_legacy_junction(
    tmp_path: Path,
) -> None:
    # Given
    package_root = tmp_path / "package"
    source = _write_plugin_package(package_root)
    codex_home = tmp_path / ".codex"

    # When
    registration = diagnose_codex_skill_registration(
        package_root=package_root,
        codex_home=codex_home,
    )

    # Then
    assert registration.owner == "plugin_manifest"
    assert registration.active_sources == (source.resolve(),)
    assert registration.legacy_state == "absent"
    assert not registration.legacy_path.exists()


def test_explicit_legacy_install_registers_the_project_skill_as_one_live_source(
    tmp_path: Path,
) -> None:
    # Given
    package_root = tmp_path / "package"
    source = _write_plugin_package(package_root)
    codex_home = tmp_path / ".codex"

    # When
    registered = install_legacy_codex_skill_junction(
        package_root=package_root,
        codex_home=codex_home,
    )

    # Then
    expected = codex_home / "skills" / "automate-hancom-documents"
    assert registered.path == expected
    assert registered.created is True
    assert expected.resolve() == source.resolve()
    assert (expected / "SKILL.md").read_text(encoding="utf-8") == "# live skill\n"


def test_matching_legacy_junction_is_diagnostic_only_not_a_second_source(
    tmp_path: Path,
) -> None:
    # Given
    package_root = tmp_path / "package"
    source = _write_plugin_package(package_root)
    codex_home = tmp_path / ".codex"
    _ = install_legacy_codex_skill_junction(
        package_root=package_root,
        codex_home=codex_home,
    )

    # When
    registration = diagnose_codex_skill_registration(
        package_root=package_root,
        codex_home=codex_home,
    )

    # Then
    assert registration.active_sources == (source.resolve(),)
    assert registration.legacy_state == "current"


def test_legacy_install_never_overwrites_an_unrelated_directory(
    tmp_path: Path,
) -> None:
    # Given
    package_root = tmp_path / "package"
    _ = _write_plugin_package(package_root, "# source\n")
    destination = tmp_path / ".codex" / "skills" / "automate-hancom-documents"
    destination.mkdir(parents=True)
    _ = (destination / "SKILL.md").write_text("# user copy\n", encoding="utf-8")

    # When / Then
    with pytest.raises(HwpLiveError, match="덮어쓰지"):
        _ = install_legacy_codex_skill_junction(
            package_root=package_root,
            codex_home=tmp_path / ".codex",
        )

    assert (destination / "SKILL.md").read_text(encoding="utf-8") == "# user copy\n"


def test_legacy_install_never_overwrites_another_installations_junction(
    tmp_path: Path,
) -> None:
    # Given
    first_root = tmp_path / "first-package"
    first_source = _write_plugin_package(first_root, "# first\n")
    second_root = tmp_path / "second-package"
    _ = _write_plugin_package(second_root, "# second\n")
    codex_home = tmp_path / ".codex"
    installed = install_legacy_codex_skill_junction(
        package_root=first_root,
        codex_home=codex_home,
    )

    # When
    registration = diagnose_codex_skill_registration(
        package_root=second_root,
        codex_home=codex_home,
    )

    # Then
    assert registration.legacy_state == "other"
    with pytest.raises(HwpLiveError, match="덮어쓰지"):
        _ = install_legacy_codex_skill_junction(
            package_root=second_root,
            codex_home=codex_home,
        )
    assert installed.path.resolve() == first_source.resolve()
