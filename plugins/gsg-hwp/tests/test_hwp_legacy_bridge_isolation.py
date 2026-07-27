from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_protocol_one_bridge_is_quarantined_from_the_supported_addon_tree() -> None:
    # Given
    supported_addon = ROOT / "addon" / "HancomLiveBridge"
    quarantined = ROOT / "legacy" / "unsupported" / "HancomLiveBridgeProtocol1"

    # When
    supported_path_exists = supported_addon.exists()

    # Then
    assert supported_path_exists is False
    assert (quarantined / "UNSUPPORTED.md").is_file()
    assert (quarantined / "ObjectRegistry.cs").is_file()
