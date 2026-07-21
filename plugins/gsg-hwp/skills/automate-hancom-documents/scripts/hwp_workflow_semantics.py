from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final, Literal

from hwp_operation_contract import HwpWorkflowId
from hwp_workflow_hybrid import domain_concepts


type SemanticConcept = Literal[
    "add",
    "blank",
    "copy",
    "create",
    "directional",
    "fill",
    "format",
    "image",
    "new",
    "page",
    "quantity",
    "replace",
    "selection",
    "table",
    "template",
]


_PRESERVED_IMAGE: Final = re.compile(
    r"(?:그림|이미지|사진|도면)(?:은|는|을|를)?\s*"
    + r"(?:건드리지|바꾸지|교체하지|삭제하지|수정하지|그대로\s*두|유지)"
)
_NO_EXPANSION: Final = re.compile(
    r"(?:(?:페이지\s*수|쪽\s*수|행|줄)(?:은|는|을|를)?\s*)"
    + r"(?:늘리|증가시키|추가하|확장하)(?:지\s*)?"
    + r"(?:않고|말고|마|않게|않도록|않은\s*채)"
)
_CONCEPT_PATTERNS: Final[tuple[tuple[SemanticConcept, re.Pattern[str]], ...]] = (
    ("table", re.compile(r"표|테이블|셀|행|열|빈\s*(?:칸|셀)")),
    ("image", re.compile(r"그림|이미지|사진|도면|picture|image|photo")),
    ("page", re.compile(r"페이지|쪽|page")),
    ("selection", re.compile(r"선택(?:한|된|영역)?|selection")),
    ("template", re.compile(r"양식|템플릿|서식\s*(?:그대로|복제|복사)|template")),
    (
        "quantity",
        re.compile(
            r"(?:\d+|여러|열아홉)\s*개|개수|수만큼"
            + r"|(?:두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무)\s*(?:개|장|세트|쪽)"
        ),
    ),
    ("create", re.compile(r"만들|생성|구성|create")),
    ("replace", re.compile(r"교체|바꾸|대체|replace|change")),
    ("fill", re.compile(r"채우|채워|기입|입력|갱신|반영|fill|populate")),
    ("copy", re.compile(r"복사|복제|반복|늘리|확장|copy|duplicate|repeat|expand")),
    ("format", re.compile(r"서식|스타일|format|style")),
    ("blank", re.compile(r"빈\s*(?:칸|셀)|없는\s*것")),
    ("new", re.compile(r"새\s*(?:페이지|쪽|사진|그림|이미지)|신규|new")),
    ("add", re.compile(r"추가|삽입|넣어|배치|add|insert|place")),
    (
        "directional",
        re.compile(
            r"(?:첫|첫\s*번째|1\s*번).*(?:둘째|두\s*번째|2\s*번)"
            + r"|(?:원본|기준).*(?:대상|적용)"
        ),
    ),
)
_UNSAFE_SINGLE_TERMS: Final = frozenset({"가", "개", "써"})


@dataclass(frozen=True, slots=True)
class WorkflowSemanticFrame:
    normalized: str
    positive: str
    compact: str
    positive_compact: str
    concepts: frozenset[SemanticConcept]
    preserves_image: bool
    forbids_expansion: bool

    def contains(self, term: str) -> bool:
        normalized_term = normalize_workflow_text(term)
        compact_term = compact_workflow_text(normalized_term)
        if normalized_term in _UNSAFE_SINGLE_TERMS:
            return bool(
                re.search(
                    rf"(?<![0-9a-z가-힣]){re.escape(normalized_term)}(?![0-9a-z가-힣])",
                    self.positive,
                )
            )
        return bool(compact_term) and compact_term in self.positive_compact


def normalize_workflow_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def compact_workflow_text(value: str) -> str:
    return "".join(character for character in normalize_workflow_text(value) if character.isalnum())


def parse_workflow_semantics(value: str) -> WorkflowSemanticFrame:
    normalized = normalize_workflow_text(value)
    preserves_image = _PRESERVED_IMAGE.search(normalized) is not None
    forbids_expansion = _NO_EXPANSION.search(normalized) is not None
    positive = _NO_EXPANSION.sub(" ", _PRESERVED_IMAGE.sub(" ", normalized))
    concepts: set[SemanticConcept] = {
        concept
        for concept, pattern in _CONCEPT_PATTERNS
        if pattern.search(positive) is not None
    }
    return WorkflowSemanticFrame(
        normalized=normalized,
        positive=positive,
        compact=compact_workflow_text(normalized),
        positive_compact=compact_workflow_text(positive),
        concepts=frozenset(concepts),
        preserves_image=preserves_image,
        forbids_expansion=forbids_expansion,
    )


def preferred_workflow(frame: WorkflowSemanticFrame) -> HwpWorkflowId | None:
    concepts = frame.concepts | domain_concepts(frame.positive)
    if any(
        frame.contains(term)
        for term in ("재구성", "다시만들", "재작성", "rebuild")
    ):
        return "document.rebuild"
    if (
        "inspect" in concepts
        and frame.contains("구조")
        and not any(
            frame.contains(term)
            for term in ("재구성", "다시만들", "재작성", "rebuild")
        )
    ):
        return "document.inspect_structure"
    if {"document", "end"} <= concepts and bool(
        {"add", "create", "new"} & concepts
    ) and (
        "table" in concepts or "template" in concepts or "page" in concepts
    ):
        return "document.append_layout"
    if {"table", "image"} <= concepts and (
        ("copy" in concepts and bool({"fill", "create", "add"} & concepts))
        or ("quantity" in concepts and bool({"fill", "create"} & concepts))
        or ({"template", "create"} <= concepts and bool({"fill", "add"} & concepts))
    ):
        return "table.build_series"
    if {"table", "image", "fill"} <= concepts:
        return "table.insert_images"
    if {"table", "image", "add"} <= concepts and not (
        {"copy", "quantity"} & concepts
    ):
        return "table.insert_images"
    if {"table", "fill"} <= concepts and frame.forbids_expansion:
        return "table.fill_existing"
    if {"table", "split"} <= concepts:
        return "table.split_cells"
    if {"table", "merge"} <= concepts:
        return "table.merge_cells"
    if {"caption", "add"} <= concepts or {"caption", "replace"} <= concepts:
        return "caption.add"
    if {"table", "copy", "fill"} <= concepts:
        return "table.expand_and_fill"
    if {"table", "template", "quantity"} <= concepts and (
        "copy" in concepts or "create" in concepts
    ):
        return "table.repeat_template"
    if {"image", "replace"} <= concepts:
        return "image.replace"
    if {"image", "add"} <= concepts and "table" not in concepts:
        return "image.insert"
    if {"page", "new"} <= concepts and {"add", "create"} & concepts and "image" not in concepts:
        return "document.insert_page"
    if {"table", "blank", "fill"} <= concepts:
        return "table.fill_existing"
    if {"format", "copy", "directional"} <= concepts:
        return "style.copy"
    if {"table", "fill"} <= concepts and not (
        {"copy", "create", "image"} & concepts
    ):
        return "table.fill_existing"
    return None
