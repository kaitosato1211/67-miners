from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class BenchmarkAnswerType(StrEnum):
    SINGLE_ANSWER = "Single Answer"
    SET_ANSWER = "Set Answer"


@dataclass(frozen=True, slots=True)
class BenchmarkDatasetManifest:
    suite_slug: str
    suite_name: str
    dataset_version: str
    scoring_version: str
    source_url: str
    source_page_url: str
    license: str
    sha256: str
    row_count: int
    file_name: str
    fetched_at: str


@dataclass(frozen=True, slots=True)
class BenchmarkDatasetItem:
    item_index: int
    problem: str
    problem_category: str
    answer: str
    answer_type: BenchmarkAnswerType
    source_item_id: str | None = None


@dataclass(frozen=True, slots=True)
class BenchmarkDatasetSnapshot:
    manifest: BenchmarkDatasetManifest
    items: tuple[BenchmarkDatasetItem, ...]


__all__ = [
    "BenchmarkAnswerType",
    "BenchmarkDatasetItem",
    "BenchmarkDatasetManifest",
    "BenchmarkDatasetSnapshot",
]
