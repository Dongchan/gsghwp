from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_protocol_one_bridge_is_excluded_from_the_public_release() -> None:
    # Given
    supported_addon = ROOT / "addon" / "HancomLiveBridge"
    legacy_root = ROOT / "legacy"

    # When
    supported_path_exists = supported_addon.exists()

    # Then
    assert supported_path_exists is False
    assert legacy_root.exists() is False
