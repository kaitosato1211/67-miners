from __future__ import annotations

import json
from functools import lru_cache
from hashlib import sha256
from importlib.abc import Traversable
from importlib.resources import files

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from harnyx_commons.miner_task_benchmark.rubric_scoring import (
    BENCHMARK_WEIGHTED_RUBRIC_SCORING_VERSION,
    parse_weighted_rubric,
)
from harnyx_commons.miner_task_benchmark.types import (
    BenchmarkAnswerType,
    BenchmarkDatasetItem,
    BenchmarkDatasetManifest,
    BenchmarkDatasetSnapshot,
)

DRACO_SUITE_SLUG = "draco"
DRACO_SUITE_NAME = "DRACO"
_CURRENT_VERSION_FILE = "current_version.json"
_DATA_PACKAGE = "harnyx_commons.miner_task_benchmark.draco.data"
_VERSIONS_DIR = "versions"


class _DracoRowPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    problem: str = Field(min_length=1)
    answer: str = Field(min_length=1)

    @field_validator("id", "domain", "problem", "answer")
    @classmethod
    def _validate_nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value


def load_draco_snapshot(
    *,
    dataset_version: str | None = None,
    scoring_version: str | None = None,
) -> BenchmarkDatasetSnapshot:
    expected_version = _expected_version(
        dataset_version=dataset_version,
        scoring_version=scoring_version,
    )
    if expected_version is None:
        expected_version = _current_draco_version()
    for snapshot in list_draco_snapshots():
        snapshot_version = (snapshot.manifest.dataset_version, snapshot.manifest.scoring_version)
        if snapshot_version == expected_version:
            return snapshot
    raise RuntimeError(
        "unknown DRACO snapshot version: "
        f"dataset_version={expected_version[0]!r} scoring_version={expected_version[1]!r}"
    )


@lru_cache(maxsize=1)
def list_draco_snapshots() -> tuple[BenchmarkDatasetSnapshot, ...]:
    data_dir = files(_DATA_PACKAGE)
    versions_dir = data_dir.joinpath(_VERSIONS_DIR)
    snapshots = tuple(
        _load_snapshot_from_dir(entry)
        for entry in sorted(versions_dir.iterdir(), key=lambda path: path.name)
        if entry.is_dir()
    )
    if not snapshots:
        raise RuntimeError("DRACO snapshot catalog is empty")
    _current_draco_version()
    return snapshots


def _load_snapshot_from_dir(snapshot_dir: Traversable) -> BenchmarkDatasetSnapshot:
    manifest_payload = json.loads(snapshot_dir.joinpath("manifest.json").read_text(encoding="utf-8"))
    manifest = BenchmarkDatasetManifest(**manifest_payload)
    if manifest.suite_slug != DRACO_SUITE_SLUG:
        raise RuntimeError(
            f"DRACO suite slug mismatch: expected {DRACO_SUITE_SLUG} got {manifest.suite_slug}"
        )
    if manifest.suite_name != DRACO_SUITE_NAME:
        raise RuntimeError(
            f"DRACO suite name mismatch: expected {DRACO_SUITE_NAME} got {manifest.suite_name}"
        )
    if manifest.scoring_version != BENCHMARK_WEIGHTED_RUBRIC_SCORING_VERSION:
        raise RuntimeError(
            "DRACO scoring version mismatch: "
            f"expected {BENCHMARK_WEIGHTED_RUBRIC_SCORING_VERSION} got {manifest.scoring_version}"
        )
    jsonl_path = snapshot_dir.joinpath(manifest.file_name)
    checksum = sha256(jsonl_path.read_bytes()).hexdigest()
    if checksum != manifest.sha256:
        raise RuntimeError(f"DRACO checksum mismatch: expected {manifest.sha256} got {checksum}")
    rows = tuple(
        _load_item(index=item_index, line=line)
        for item_index, line in enumerate(jsonl_path.read_text(encoding="utf-8").splitlines())
    )
    if len(rows) != manifest.row_count:
        raise RuntimeError(f"DRACO row count mismatch: expected {manifest.row_count} got {len(rows)}")
    return BenchmarkDatasetSnapshot(manifest=manifest, items=rows)


def _load_item(*, index: int, line: str) -> BenchmarkDatasetItem:
    try:
        row = json.loads(line)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"DRACO row {index} is not valid JSON") from exc

    try:
        payload = _DracoRowPayload.model_validate(row)
    except ValidationError as exc:
        raise RuntimeError(f"DRACO row {index} does not match the expected shape") from exc

    try:
        parse_weighted_rubric(payload.answer)
    except ValueError as exc:
        raise RuntimeError(f"DRACO row {index} answer is not a valid weighted rubric") from exc

    return BenchmarkDatasetItem(
        item_index=index,
        problem=payload.problem,
        problem_category=payload.domain,
        answer=payload.answer,
        answer_type=BenchmarkAnswerType.SINGLE_ANSWER,
        source_item_id=payload.id,
    )


@lru_cache(maxsize=1)
def _current_draco_version() -> tuple[str, str]:
    data_dir = files(_DATA_PACKAGE)
    payload = json.loads(data_dir.joinpath(_CURRENT_VERSION_FILE).read_text(encoding="utf-8"))
    version = _expected_version(
        dataset_version=payload["dataset_version"],
        scoring_version=payload["scoring_version"],
    )
    if version is None:
        raise RuntimeError("DRACO current version file must define dataset_version and scoring_version")
    return version


def _expected_version(
    *,
    dataset_version: str | None,
    scoring_version: str | None,
) -> tuple[str, str] | None:
    if dataset_version is None and scoring_version is None:
        return None
    if dataset_version is None or scoring_version is None:
        raise RuntimeError("DRACO snapshot lookup requires both dataset_version and scoring_version")
    return dataset_version, scoring_version


__all__ = [
    "DRACO_SUITE_NAME",
    "DRACO_SUITE_SLUG",
    "list_draco_snapshots",
    "load_draco_snapshot",
]
