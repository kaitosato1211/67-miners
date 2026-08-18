from __future__ import annotations

from itertools import count
from uuid import UUID

import pytest

from harnyx_commons.domain.tool_call import ToolResultPolicy
from harnyx_commons.tools.executor import _build_tool_results


@pytest.fixture(autouse=True)
def deterministic_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    sequence = count(1)

    def _fake_uuid() -> UUID:
        return UUID(int=next(sequence))

    monkeypatch.setattr("harnyx_commons.tools.executor.uuid4", _fake_uuid)


def test_build_tool_results_search_web_referenceable() -> None:
    payload = {
        "data": [
            {"link": "https://example.com/a", "snippet": "alpha", "title": "Alpha"},
            {"link": "", "snippet": "ignored", "title": "Missing URL"},
            {"link": "https://example.com/b", "snippet": "", "title": "Beta"},
            42,
        ],
    }

    results = _build_tool_results(
        "search_web",
        payload,
        ToolResultPolicy.REFERENCEABLE,
    )

    assert len(results) == 2
    first, second = results

    assert first.index == 0
    assert first.url == "https://example.com/a"
    assert first.note == "alpha"
    assert first.title == "Alpha"
    assert first.result_id == UUID(int=1).hex
    assert first.raw is None

    assert second.index == 1
    assert second.url == "https://example.com/b"
    assert second.note is None
    assert second.title == "Beta"
    assert second.result_id == UUID(int=2).hex
    assert second.raw is None


def test_build_tool_results_fetch_page_referenceable() -> None:
    payload = {
        "data": [
            {"url": "https://example.com/page-1", "content": "first page", "title": "Page"},
            {"url": "https://example.com/page-2", "content": "second page"},
            {"url": None, "text": "missing url"},
        ],
    }

    results = _build_tool_results(
        "fetch_page",
        payload,
        ToolResultPolicy.REFERENCEABLE,
    )

    assert len(results) == 2
    assert [(result.index, result.url, result.note, result.title) for result in results] == [
        (0, "https://example.com/page-1", "first page", "Page"),
        (1, "https://example.com/page-2", "second page", None),
    ]
    assert [result.raw for result in results] == [None, None]


def test_build_tool_results_search_ai_keeps_note_field() -> None:
    payload = {
        "data": [
            {
                "url": "https://example.com/harnyx",
                "note": "alpha note",
                "title": "Harnyx",
            }
        ]
    }

    results = _build_tool_results(
        "search_ai",
        payload,
        ToolResultPolicy.REFERENCEABLE,
    )

    assert len(results) == 1
    assert results[0].url == "https://example.com/harnyx"
    assert results[0].note == "alpha note"
    assert results[0].title == "Harnyx"
    assert results[0].raw is None


def test_build_tool_results_referenceable_with_non_mapping_payload() -> None:
    assert (
        _build_tool_results(
            "search_web",
            ["unexpected", "payload"],
            ToolResultPolicy.REFERENCEABLE,
        )
        == ()
    )


def test_build_tool_results_log_only_normalizes_payload() -> None:
    payload = {
        "response": (
            {"tool": "llm_chat", "usage": {"prompt_tokens": 12}},
            {"tool": "llm_chat", "messages": [{"role": "assistant", "content": "ok"}]},
        ),
        "status": "ok",
    }

    results = _build_tool_results(
        "llm_chat",
        payload,
        ToolResultPolicy.LOG_ONLY,
    )

    assert len(results) == 1
    result = results[0]

    assert result.index == 0
    assert result.result_id == UUID(int=1).hex
    assert result.raw == {
        "response": [
            {"tool": "llm_chat", "usage": {"prompt_tokens": 12}},
            {"tool": "llm_chat", "messages": [{"role": "assistant", "content": "ok"}]},
        ],
        "status": "ok",
    }
