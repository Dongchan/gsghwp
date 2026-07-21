from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter
from itertools import combinations
from typing import Final, Literal


type DomainConcept = Literal[
    "add", "begin", "blank", "caption", "copy", "create", "delete",
    "directional", "document", "embedded", "end", "fill", "format",
    "hyperlink", "image", "import_data", "inspect", "linked", "merge",
    "move", "new", "page", "preserve", "quantity", "replace", "resize",
    "selection", "split", "table", "template", "text",
]


_WORD: Final = re.compile(r"[0-9A-Za-z]+|[가-힣]+|[\u3400-\u9fff]+")
_CAMEL_BOUNDARY: Final = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_EMBEDDING_DIMENSIONS: Final = 64
_SUFFIXES: Final = (
    "으로부터", "에게서", "으로", "에서", "에게", "부터", "까지", "처럼",
    "보다", "하고", "해서", "하면", "의", "을", "를", "이", "가", "은",
    "는", "에", "로",
)
_CONCEPT_TERMS: Final[tuple[tuple[DomainConcept, tuple[str, ...]], ...]] = (
    ("document", ("문서", "보고서", "파일", "한글문서", "document", "report", "file")),
    ("table", ("표", "표를", "표의", "표가", "표는", "사진칸", "테이블", "셀", "행", "열", "칸", "표틀", "table", "cell", "row", "column")),
    ("image", ("그림", "이미지", "사진", "도면", "picture", "image", "photo")),
    ("page", ("페이지", "쪽", "새장", "page")),
    ("selection", ("선택", "골라둔", "지정", "selection", "selected")),
    ("template", ("양식", "템플릿", "기준표", "기준", "같은표", "서식그대로", "형식", "template")),
    ("quantity", ("열아홉", "19", "여러", "수만큼", "개수", "세트", "마다", "각각")),
    ("create", ("만들", "생성", "구성", "꾸미", "create", "generate")),
    ("replace", ("교체", "바꾸", "바꿔", "갈아끼", "대체", "고쳐", "replace", "change")),
    ("fill", ("채우", "채워", "기입", "입력", "반영", "써넣", "써줘", "집어넣", "populate", "fill")),
    ("copy", ("복제", "복사", "반복", "늘리", "늘려", "늘린", "줄늘", "똑같", "같은표", "이어붙", "찍어내", "뽑아", "입혀", "옮겨", "전파", "copy", "repeat", "duplicate")),
    ("format", ("서식", "스타일", "모양", "글꼴", "정렬", "테두리", "배경", "여백", "format", "style", "font", "align")),
    ("blank", ("빈칸", "빈셀", "공란", "비어", "없는곳")),
    ("new", ("새페이지", "새쪽", "새장", "빈페이지", "빈쪽", "새사진", "새그림", "신규", "new")),
    ("add", ("추가", "삽입", "끼워넣", "붙여", "이어줘", "달아", "append", "insert", "add")),
    ("directional", ("첫", "둘째", "두번째", "앞문단", "다음문단", "원본", "대상", "기준")),
    ("caption", ("캡션", "설명문구", "제목", "표제", "caption")),
    ("end", ("끝", "맨끝", "맨뒤", "마지막", "뒤쪽", "끝부분", "후미", "documentend")),
    ("begin", ("처음", "맨앞", "앞쪽", "시작", "첫머리", "documentbegin")),
    ("move", ("이동", "옮겨", "보내", "가줘", "돌려보내", "move", "go")),
    ("embedded", ("포함", "내장", "내부데이터", "파일안", "문서자체", "박아넣", "embedded", "embed")),
    ("linked", ("연결", "외부링크", "linked")),
    ("preserve", ("유지", "그대로", "손대지", "건드리지", "보존", "preserve")),
    ("text", ("글", "문장", "문구", "텍스트", "본문", "text", "paragraph")),
    ("delete", ("삭제", "지워", "제거", "delete", "remove")),
    ("inspect", ("조회", "확인", "파악", "읽어", "찾아", "검사", "inspect", "find")),
    ("resize", ("크기", "너비", "폭", "높이", "resize", "width", "height")),
    ("merge", ("병합", "합쳐", "merge")),
    ("split", ("나누", "나눠", "분할", "쪼개", "split")),
    ("hyperlink", ("하이퍼링크", "링크주소", "hyperlink")),
    ("import_data", ("가져오", "불러오", "엑셀", "csv", "pptx", "xlsx", "import")),
)


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _compact(value: str) -> str:
    return "".join(character for character in _normalize(value) if character.isalnum())


def _stem(token: str) -> str:
    if not token or not ("가" <= token[0] <= "힣"):
        return token.casefold()
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) > len(suffix) + 1:
            return token[: -len(suffix)]
    return token


def _words(value: str) -> tuple[str, ...]:
    separated = _CAMEL_BOUNDARY.sub(" ", _normalize(value))
    return tuple(_stem(match.group(0)) for match in _WORD.finditer(separated))


def domain_concepts(value: str) -> frozenset[str]:
    compact = _compact(value)
    words = frozenset(_words(value))
    concepts: set[str] = set()
    for concept, expressions in _CONCEPT_TERMS:
        for expression in expressions:
            normalized = _compact(expression)
            if (len(normalized) == 1 and normalized in words) or (
                len(normalized) > 1 and normalized in compact
            ):
                concepts.add(concept)
                break
    if any(token.isdigit() for token in words) and re.search(
        r"\d+\s*(?:개|세트|번|장|줄|행|회)", _normalize(value)
    ):
        concepts.add("quantity")
    return frozenset(concepts)


def workflow_ngrams(value: str) -> frozenset[str]:
    compact = _compact(value)
    return frozenset(
        compact[index : index + width]
        for width in (2, 3)
        for index in range(max(0, len(compact) - width + 1))
    )


def workflow_terms(value: str) -> Counter[str]:
    terms = Counter(_words(value))
    terms.update(f"@{concept}" for concept in domain_concepts(value))
    return terms


def workflow_embedding(value: str) -> tuple[float, ...]:
    concepts = tuple(sorted(domain_concepts(value)))
    features: list[tuple[str, float]] = [
        (f"concept:{concept}", 2.0) for concept in concepts
    ]
    features.extend(
        (f"relation:{left}:{right}", 1.5)
        for left, right in combinations(concepts, 2)
    )
    features.extend(
        (f"term:{word}", 0.35)
        for word in frozenset(_words(value))
        if len(word) > 1
    )
    vector = [0.0] * _EMBEDDING_DIMENSIONS
    for feature, weight in features:
        digest = hashlib.blake2s(feature.encode("utf-8"), digest_size=4).digest()
        bucket = int.from_bytes(digest[:2], "little") % _EMBEDDING_DIMENSIONS
        sign = 1.0 if digest[2] & 1 else -1.0
        vector[bucket] += sign * weight
    magnitude = math.sqrt(sum(component * component for component in vector))
    if magnitude == 0:
        return tuple(vector)
    return tuple(component / magnitude for component in vector)


def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return max(0.0, sum(a * b for a, b in zip(left, right, strict=True)))
