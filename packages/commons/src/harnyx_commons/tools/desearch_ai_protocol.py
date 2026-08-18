"""Typed boundary for DeSearch AI search responses.

This module exists to keep raw external response parsing (dict/JSON) isolated behind
Pydantic `TypeAdapter` validation, so downstream code can work with typed objects.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import ConfigDict, TypeAdapter

from harnyx_commons.tools.search_models import SearchXResult


@dataclass(frozen=True, slots=True)
class DeSearchAiDocsLinkResult:
    link: str
    title: str | None = None
    snippet: str | None = None

    __pydantic_config__ = ConfigDict(extra="ignore")


@dataclass(frozen=True, slots=True)
class DeSearchAiDocsResponse:
    results: list[DeSearchAiDocsLinkResult] | None = None

    search: list[DeSearchAiDocsLinkResult] | None = None
    wikipedia_search: list[DeSearchAiDocsLinkResult] | None = None
    youtube_search: list[DeSearchAiDocsLinkResult] | None = None
    arxiv_search: list[DeSearchAiDocsLinkResult] | None = None
    reddit_search: list[DeSearchAiDocsLinkResult] | None = None
    hacker_news_search: list[DeSearchAiDocsLinkResult] | None = None

    tweets: list[SearchXResult] | None = None
    miner_link_scores: dict[str, str] | None = None
    completion: str | None = None
    text: str | None = None
    summary: str | None = None

    __pydantic_config__ = ConfigDict(extra="ignore")


DESEARCH_AI_DOCS_RESPONSE_ADAPTER = TypeAdapter(DeSearchAiDocsResponse)
_LEGACY_RESULTS_KEYS: tuple[str, ...] = (
    "search",
    "wikipedia_search",
    "youtube_search",
    "arxiv_search",
    "reddit_search",
    "hacker_news_search",
)


def parse_desearch_ai_response(raw: object) -> DeSearchAiDocsResponse:
    raw_mapping = _require_object_mapping(raw, label="desearch ai search response must be a JSON object")
    return DESEARCH_AI_DOCS_RESPONSE_ADAPTER.validate_python(_normalize_desearch_ai_response(raw_mapping))


def _normalize_desearch_ai_response(raw: Mapping[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    aggregated_results: list[dict[str, str]] = []
    saw_known_shape = False
    saw_result_family = False

    for key in _LEGACY_RESULTS_KEYS:
        if key not in raw:
            continue
        saw_known_shape = True
        saw_result_family = True
        items = _normalize_link_results(raw[key], label=f"desearch ai search field '{key}'")
        normalized[key] = items
        aggregated_results.extend(items)

    if "results" in raw:
        saw_known_shape = True
        saw_result_family = True
        aggregated_results.extend(_normalize_link_results(raw["results"], label="desearch ai search field 'results'"))

    for key, value in raw.items():
        if not key.endswith("_search_results"):
            continue
        saw_known_shape = True
        saw_result_family = True
        envelope = _require_object_mapping(value, label=f"desearch ai search field '{key}' must be an object")
        aggregated_results.extend(
            _normalize_link_results(
                envelope.get("organic_results"),
                label=f"desearch ai search field '{key}.organic_results'",
            )
        )

    if saw_result_family:
        normalized["results"] = aggregated_results

    for key in ("tweets", "miner_link_scores", "completion", "text", "summary"):
        if key in raw:
            saw_known_shape = True
            normalized[key] = raw[key]

    if not saw_known_shape:
        raise ValueError(
            "unexpected desearch ai search response shape "
            "(missing recognized keys like 'summary', 'results', 'tweets', '*_search', or '*_search_results')"
        )
    return normalized


def _normalize_link_results(value: object, *, label: str) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"{label} must be a JSON array")
    return [_normalize_link_result(item, label=f"{label}[{index}]") for index, item in enumerate(value)]


def _normalize_link_result(value: object, *, label: str) -> dict[str, str]:
    item = _require_object_mapping(value, label=f"{label} must be an object")
    link_value = item.get("link", item.get("url"))
    if not isinstance(link_value, str) or not link_value.strip():
        raise ValueError(f"{label} must include a non-empty 'link' or 'url'")

    normalized: dict[str, str] = {"link": link_value.strip()}
    title_value = item.get("title")
    if isinstance(title_value, str) and title_value.strip():
        normalized["title"] = title_value.strip()

    snippet_value = item.get("snippet", item.get("summary_description"))
    if isinstance(snippet_value, str) and snippet_value.strip():
        normalized["snippet"] = snippet_value.strip()

    return normalized


def _require_object_mapping(value: object, *, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(label)
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError(f"{label}: key must be a string")
        result[key] = item
    return result


__all__ = [
    "DESEARCH_AI_DOCS_RESPONSE_ADAPTER",
    "DeSearchAiDocsLinkResult",
    "DeSearchAiDocsResponse",
    "parse_desearch_ai_response",
]
