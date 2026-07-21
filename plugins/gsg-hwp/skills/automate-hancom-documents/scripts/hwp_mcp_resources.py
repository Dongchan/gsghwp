from __future__ import annotations

from pathlib import Path
from typing import Final

from mcp.server.fastmcp import FastMCP


PLUGIN_ROOT: Final = Path(__file__).resolve().parents[3]
SKILL_RESOURCE_URI: Final = "gsg-hwp-beta://skill/automate-hancom-documents"
NATIVE_LAYOUT_RESOURCE_URI: Final = "gsg-hwp-beta://reference/native-layout"


def _read_utf8(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def register_guidance_resources(server: FastMCP[None]) -> None:
    skill_path = PLUGIN_ROOT / "skills" / "automate-hancom-documents" / "SKILL.md"
    layout_path = (
        PLUGIN_ROOT
        / "skills"
        / "automate-hancom-documents"
        / "references"
        / "native-layout.md"
    )

    def read_hwp_skill() -> str:
        return _read_utf8(skill_path)

    _ = server.resource(
        SKILL_RESOURCE_URI,
        name="automate-hancom-documents",
        title="GSG HWP automation instructions",
        description=(
            "Read this UTF-8 guidance before editing, analyzing, or verifying an HWP document."
        ),
        mime_type="text/markdown",
    )(read_hwp_skill)

    def read_native_layout_guidance() -> str:
        return _read_utf8(layout_path)

    _ = server.resource(
        NATIVE_LAYOUT_RESOURCE_URI,
        name="native-layout",
        title="Image-to-editable-HWP layout guidance",
        description=(
            "Read this before rebuilding a screenshot, slide, or reference image as an editable native HWP table."
        ),
        mime_type="text/markdown",
    )(read_native_layout_guidance)
