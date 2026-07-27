"""표·그림 개체 종류 판정의 단일 출처.

이 모듈은 의도적으로 저장소 안의 다른 모듈을 하나도 import 하지 않는다.
개체 종류 판정은 미리보기(`hwp_live_preview`)부터 편집 recipe
(`hwp_priority_object_recipes`)까지 서로 다른 계층이 전부 필요로 하므로,
잎(leaf) 모듈이어야 순환 import 없이 어디서든 가져다 쓸 수 있다.

## 왜 대소문자를 무시하는가

값의 출처는 한/글 COM 의 ``CtrlID`` 이고, 네이티브 브리지는 그 문자열을
**전혀 변형하지 않고 그대로 내보낸다**:

* ``addon/HancomLiveBridgeNative/ComState.cpp:85`` (``ReadControlIdentity``) 와
  ``addon/HancomLiveBridgeNative/LiveInspection.cpp:188`` (``ControlIdentity``)
  는 ``PropertyGet(control, L"CtrlID")`` 결과를 ``AsString`` 으로 받아 그대로
  보관한다.
* ``addon/HancomLiveBridgeNative/LiveInspection.cpp:689`` 과 ``:875`` 는 그
  문자열을 그대로 ``CTRL\t<base64>`` 로 직렬화한다.
* 네이티브 전체에서 ``towlower`` 를 쓰는 곳은
  ``ActionExecutor.cpp:453`` 과 ``BatchExecutor.cpp:776`` 두 군데뿐이고,
  둘 다 개체 종류가 아니라 **문서 경로(``FullName``)** 정규화다.

즉 대소문자를 정하는 주체는 브리지가 아니라 한/글 본체다.  한/글을 실행하지
않고서는(이 과제에서는 금지) 한/글이 항상 소문자를 준다고 **확정할 수 없다**.
확정할 수 없으므로 관대한 쪽 — 대소문자 무시 — 로 통일한다.

간접 증거는 소문자를 가리킨다.  네이티브 자신이 ``type == L"gso"``,
``type == L"tbl"`` 같은 소문자 정확 비교로 그림·표를 판정하고
(``BatchExecutor.cpp:280-281``, ``:338``; ``ComState.cpp:316``, ``:508``;
``LiveInspection.cpp:590``, ``:600``, ``:665``, ``:865-879``, ``:1233-1261``;
``TableInspection.cpp:238``), 실제 캡처 산출물
(``artifacts/live-validation/**``)에 나타난 값도 ``gso`` ``tbl`` ``cold``
``secd`` ``pgnp`` ``nwno`` 전부 소문자였다.  그러므로 대소문자 무시는 지금
관측되는 동작을 바꾸지 않고, 대문자가 나오는 경우에만 추가로 통과시킨다.

## 무엇이 통과하지 *않는가*

``$pic`` 은 여기 포함되지 않는다.  ``BatchExecutor.cpp:280`` 과 ``:338`` 은
``$pic`` 을 그림으로 취급하지만 파이썬 경로는 예전부터 그러지 않았고,
``$pic`` 은 대소문자와 무관하므로 이 변경으로 달라지는 것이 없다.  판정 범위를
넓히는 것은 별개의 결정이라 여기서 함께 하지 않는다.
"""

from __future__ import annotations

from typing import Final


# 한/글 ``CtrlID`` 값이다. 공개 스키마의 ``target.kind`` ("table"/"picture")
# 와는 다른 문자열이므로 섞어 쓰지 말 것.
TABLE_CONTROL_TYPES: Final[frozenset[str]] = frozenset(("tbl",))
PICTURE_CONTROL_TYPES: Final[frozenset[str]] = frozenset(("gso", "pic", "picture"))

# 캡션을 붙일 수 있는 개체. 표 ∪ 그림이며, 두 집합은 서로소다.
CAPTIONABLE_CONTROL_TYPES: Final[frozenset[str]] = (
    TABLE_CONTROL_TYPES | PICTURE_CONTROL_TYPES
)

# 공개 스키마 ``target.kind`` 중 구체적인 개체 종류를 지목하는 값만 담는다.
# "document"/"page"/"selection"/"control" 은 *어디를* 볼지를 말할 뿐 *무엇을*
# 편집할지를 말하지 않으므로 후보 종류를 좁히지 않는다.
KIND_CONTROL_TYPES: Final[dict[str, frozenset[str]]] = {
    "table": TABLE_CONTROL_TYPES,
    "picture": PICTURE_CONTROL_TYPES,
}

KIND_LABELS: Final[dict[str, str]] = {"table": "표", "picture": "그림"}


def normalize_control_type(control_type: str) -> str:
    """``CtrlID`` 를 비교용 표준형으로 접는다."""
    return control_type.casefold()


def is_control_type(control_type: str, allowed: frozenset[str]) -> bool:
    """``control_type`` 이 ``allowed`` 에 속하는지 대소문자 무시로 판정한다.

    ``allowed`` 는 이 모듈의 상수처럼 이미 접힌(casefolded) 집합이어야 한다.
    """
    return normalize_control_type(control_type) in allowed


def is_picture_control_type(control_type: str) -> bool:
    return is_control_type(control_type, PICTURE_CONTROL_TYPES)


def is_table_control_type(control_type: str) -> bool:
    return is_control_type(control_type, TABLE_CONTROL_TYPES)
