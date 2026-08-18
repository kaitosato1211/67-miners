from __future__ import annotations

import pytest
from pydantic import ValidationError

from harnyx_commons.domain.miner_task import (
    EvaluationError,
    MinerTaskErrorCode,
    is_delivery_disqualifying_validator_pair_error,
    is_miner_attributed_pair_error,
)


def test_evaluation_error_normalizes_string_code_to_enum() -> None:
    error = EvaluationError(code="timeout_inconclusive", message="terminal timeout")

    assert error.code is MinerTaskErrorCode.TIMEOUT_INCONCLUSIVE
    assert error.model_dump(mode="json") == {
        "code": "timeout_inconclusive",
        "message": "terminal timeout",
    }


def test_evaluation_error_accepts_persisted_script_validation_failed_code() -> None:
    error = EvaluationError(code="script_validation_failed", message="invalid script")

    assert error.code is MinerTaskErrorCode.SCRIPT_VALIDATION_FAILED
    assert error.model_dump(mode="json") == {
        "code": "script_validation_failed",
        "message": "invalid script",
    }


def test_evaluation_error_accepts_enum_member_directly() -> None:
    error = EvaluationError(
        code=MinerTaskErrorCode.SCORING_LLM_RETRY_EXHAUSTED,
        message="scoring exhausted",
    )

    assert error.code is MinerTaskErrorCode.SCORING_LLM_RETRY_EXHAUSTED


def test_evaluation_error_rejects_unknown_code() -> None:
    with pytest.raises(ValidationError, match="not_a_real_code"):
        EvaluationError(code="not_a_real_code", message="boom")


def test_miner_attributed_pair_error_codes_are_not_delivery_disqualifying() -> None:
    for code in (
        MinerTaskErrorCode.MINER_RESPONSE_INVALID,
        MinerTaskErrorCode.MINER_UNHANDLED_EXCEPTION,
        MinerTaskErrorCode.SCRIPT_VALIDATION_FAILED,
        MinerTaskErrorCode.SESSION_BUDGET_EXHAUSTED,
        MinerTaskErrorCode.TIMEOUT_MINER_OWNED,
        MinerTaskErrorCode.ARTIFACT_SIZE_INVALID,
    ):
        assert is_miner_attributed_pair_error(code)
        assert not is_delivery_disqualifying_validator_pair_error(code)


def test_delivery_disqualifying_codes_are_not_miner_attributed() -> None:
    for code in (
        MinerTaskErrorCode.SCORING_LLM_RETRY_EXHAUSTED,
        MinerTaskErrorCode.ARTIFACT_FETCH_FAILED,
        MinerTaskErrorCode.ARTIFACT_HASH_MISMATCH,
        MinerTaskErrorCode.ARTIFACT_STAGING_FAILED,
        MinerTaskErrorCode.ARTIFACT_SETUP_FAILED,
        MinerTaskErrorCode.SANDBOX_START_FAILED,
        MinerTaskErrorCode.SANDBOX_INVOCATION_FAILED,
    ):
        assert is_delivery_disqualifying_validator_pair_error(code)
        assert not is_miner_attributed_pair_error(code)


def test_timeout_inconclusive_stays_readable_but_inactive_for_new_delivery_exclusion() -> None:
    error = EvaluationError(code="timeout_inconclusive", message="terminal timeout")

    assert error.code is MinerTaskErrorCode.TIMEOUT_INCONCLUSIVE
    assert not is_delivery_disqualifying_validator_pair_error(MinerTaskErrorCode.TIMEOUT_INCONCLUSIVE)
    assert not is_miner_attributed_pair_error(MinerTaskErrorCode.TIMEOUT_INCONCLUSIVE)
