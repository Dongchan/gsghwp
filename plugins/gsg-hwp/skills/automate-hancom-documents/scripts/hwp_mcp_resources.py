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
        # 무엇이 여기 있는지와 어디서 얻는지만 적는다. "먼저 읽어라"는 강제는
        # 걷어냈다(.codex-plugin/plugin.json, AGENTS.md 는 8762f2f 에서 이미).
        # 얻는 방법이 적혀 있지 않으면 모델은 파일 경로를 셸로 열러 간다 --
        # 실제로 따옴표 처리와 하위 경로를 각각 한 번씩 틀려 왕복을 더 썼다.
        description=(
            "The automate-hancom-documents skill text (SKILL.md) for editing,"
            " analyzing, and verifying HWP documents. Served here as UTF-8"
            " markdown; reading this resource needs no file path."
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
            "The native-layout reference (references/native-layout.md) for"
            " rebuilding a screenshot, slide, or reference image as an editable"
            " native HWP table. Served here as UTF-8 markdown; reading this"
            " resource needs no file path."
        ),
        mime_type="text/markdown",
    )(read_native_layout_guidance)
