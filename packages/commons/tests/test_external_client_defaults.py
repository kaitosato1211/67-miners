from __future__ import annotations

import pytest

from harnyx_commons.clients import DESEARCH, PARALLEL


def test_non_llm_search_client_defaults_are_60_seconds() -> None:
    assert DESEARCH.timeout_seconds == pytest.approx(60.0)
    assert PARALLEL.timeout_seconds == pytest.approx(60.0)
