from __future__ import annotations

from dataclasses import dataclass

from hwp_operation_certification import certified_recipe
from hwp_workflow_korean_corpus import KOREAN_WORKFLOW_EXAMPLES
from hwp_workflow_router import resolve_workflow


@dataclass(frozen=True, slots=True)
class KoreanWorkflowMetric:
    hits: int
    total: int

    @property
    def rate(self) -> float:
        if self.total == 0:
            return 1.0
        return self.hits / self.total


@dataclass(frozen=True, slots=True)
class KoreanWorkflowEvaluation:
    example_count: int
    positive_example_count: int
    ambiguous_example_count: int
    workflow_group_count: int
    category_counts: tuple[tuple[str, int], ...]
    candidate_retrieval: KoreanWorkflowMetric
    resolution: KoreanWorkflowMetric
    certified_executability: KoreanWorkflowMetric
    explicit_authority: KoreanWorkflowMetric
    certified_workflow_ids: tuple[str, ...]
    uncertified_workflow_ids: tuple[str, ...]
    candidate_limit_violations: int


def evaluate_korean_workflow_corpus() -> KoreanWorkflowEvaluation:
    examples = KOREAN_WORKFLOW_EXAMPLES
    positive = tuple(example for example in examples if example.expected is not None)
    natural = tuple(
        example
        for example in positive
        if example.explicit is None
    )
    groups = {
        example.expected
        for example in positive
        if example.expected is not None
    }
    categories = tuple(sorted({example.category for example in examples}))
    category_counts = tuple(
        (category, sum(example.category == category for example in examples))
        for category in categories
    )
    retrieval_hits = 0
    resolution_hits = 0
    candidate_limit_violations = 0
    for example in natural:
        resolution = resolve_workflow(example.query)
        expected = example.expected
        if expected is None:
            continue
        candidate_ids = {candidate.workflow_id for candidate in resolution.candidates}
        retrieval_hits += expected in candidate_ids
        resolution_hits += (
            resolution.status == "resolved" and resolution.workflow_id == expected
        )
        candidate_limit_violations += len(resolution.candidates) > 24
    for example in examples:
        if example.explicit is None and example.expected is None:
            resolution = resolve_workflow(example.query)
            candidate_limit_violations += len(resolution.candidates) > 24

    authority_cases = tuple(
        example for example in positive if example.explicit is not None
    )
    authority_hits = 0
    for example in authority_cases:
        resolution = resolve_workflow(example.query, explicit=example.explicit)
        authority_hits += (
            resolution.status == "resolved"
            and resolution.workflow_id == example.expected
            and resolution.match_kind == "explicit"
        )

    certified_ids = tuple(
        sorted(
            {
                example.expected
                for example in positive
                if example.expected is not None
                and certified_recipe(example.expected) is not None
            }
        )
    )
    uncertified_ids = tuple(sorted(set(groups) - set(certified_ids)))
    certified_hits = sum(
        certified_recipe(example.expected) is not None
        for example in positive
        if example.expected is not None
    )
    return KoreanWorkflowEvaluation(
        example_count=len(examples),
        positive_example_count=len(positive),
        ambiguous_example_count=len(examples) - len(positive),
        workflow_group_count=len(groups),
        category_counts=category_counts,
        candidate_retrieval=KoreanWorkflowMetric(retrieval_hits, len(natural)),
        resolution=KoreanWorkflowMetric(resolution_hits, len(natural)),
        certified_executability=KoreanWorkflowMetric(certified_hits, len(positive)),
        explicit_authority=KoreanWorkflowMetric(authority_hits, len(authority_cases)),
        certified_workflow_ids=certified_ids,
        uncertified_workflow_ids=uncertified_ids,
        candidate_limit_violations=candidate_limit_violations,
    )


def format_korean_workflow_evaluation(
    evaluation: KoreanWorkflowEvaluation,
) -> str:
    category_text = ",".join(
        f"{category}={count}" for category, count in evaluation.category_counts
    )
    return "\n".join(
        (
            f"examples={evaluation.example_count}",
            f"positive={evaluation.positive_example_count}",
            f"ambiguous={evaluation.ambiguous_example_count}",
            f"workflow_groups={evaluation.workflow_group_count}",
            f"categories={category_text}",
            f"candidate_retrieval={evaluation.candidate_retrieval.hits}/{evaluation.candidate_retrieval.total}",
            f"resolution={evaluation.resolution.hits}/{evaluation.resolution.total}",
            f"certified_executability={evaluation.certified_executability.hits}/{evaluation.certified_executability.total}",
            f"explicit_authority={evaluation.explicit_authority.hits}/{evaluation.explicit_authority.total}",
            f"candidate_limit_violations={evaluation.candidate_limit_violations}",
        )
    )
