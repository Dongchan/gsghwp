from __future__ import annotations

from hwp_errors import HwpLiveError
from hwp_live_native_action_models import NativeSnapshot
from hwp_live_native_format_inputs import TextFormatSpec
from hwp_live_native_text_format import rgb_value
from hwp_live_values import ALIGNMENT_VALUES as _ALIGNMENT_VALUES


def verify_text_format(
    requested: TextFormatSpec,
    before: NativeSnapshot,
    after: NativeSnapshot,
) -> None:
    if (
        not before.selection.selected
        or not after.selection.selected
        or after.selection.start != before.selection.start
        or after.selection.end != before.selection.end
    ):
        raise HwpLiveError("글자 서식 적용 후 원래 텍스트 선택 영역을 확인하지 못했습니다")
    if after.selected_text != before.selected_text:
        raise HwpLiveError("글자 서식 적용 중 선택한 본문 내용이 바뀌었습니다")
    verify_requested_text_format(requested, after)


def verify_requested_text_format(
    requested: TextFormatSpec,
    after: NativeSnapshot,
) -> None:
    character = after.character_format
    if requested.bold is not None and character.bold != requested.bold:
        raise HwpLiveError("요청한 진하게 서식과 한컴의 실제 글자 서식이 일치하지 않습니다")
    if requested.font_name is not None and character.face_name != requested.font_name:
        raise HwpLiveError("요청한 글꼴과 한컴의 실제 글꼴이 일치하지 않습니다")
    if (
        requested.font_size_pt is not None
        and character.height_hwpunit != round(requested.font_size_pt * 100)
    ):
        raise HwpLiveError("요청한 글자 크기와 한컴의 실제 글자 크기가 일치하지 않습니다")
    if (
        requested.text_color is not None
        and character.text_color != rgb_value(requested.text_color)
    ):
        raise HwpLiveError("요청한 글자색과 한컴의 실제 글자색이 일치하지 않습니다")
    paragraph = after.paragraph_format
    if (
        requested.alignment != "inherit"
        and paragraph.alignment != _ALIGNMENT_VALUES[requested.alignment]
    ):
        raise HwpLiveError("요청한 문단 정렬과 한컴의 실제 문단 정렬이 일치하지 않습니다")
    if (
        requested.line_spacing is not None
        and paragraph.line_spacing != requested.line_spacing
    ):
        raise HwpLiveError("요청한 줄 간격과 한컴의 실제 줄 간격이 일치하지 않습니다")
