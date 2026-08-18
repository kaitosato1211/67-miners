"""Benchmark rubric judge LLM settings shared by platform and public miner tools."""

from __future__ import annotations

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from harnyx_commons.llm.provider_types import LlmProviderName


class BenchmarkRubricJudgeLlmSettings(BaseSettings):
    """LLM settings for DRACO-style benchmark rubric judging."""

    model_config = SettingsConfigDict(
        env_prefix="",
        extra="ignore",
        case_sensitive=False,
        frozen=True,
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        populate_by_name=True,
    )

    provider: LlmProviderName | None = Field(default=None, alias="BENCHMARK_RUBRIC_JUDGE_LLM_PROVIDER")
    model: str = Field(default="", alias="BENCHMARK_RUBRIC_JUDGE_LLM_MODEL")
    reasoning_effort: str | None = Field(default=None, alias="BENCHMARK_RUBRIC_JUDGE_LLM_REASONING_EFFORT")
    temperature: float | None = Field(default=None, alias="BENCHMARK_RUBRIC_JUDGE_LLM_TEMPERATURE")
    timeout_seconds: float | None = Field(default=None, alias="BENCHMARK_RUBRIC_JUDGE_LLM_TIMEOUT_SECONDS")

    @field_validator("model")
    @classmethod
    def _strip_model(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _provider_and_model_are_configured_together(self) -> BenchmarkRubricJudgeLlmSettings:
        has_provider = self.provider is not None
        has_model = bool(self.model)
        if has_provider != has_model:
            raise ValueError(
                "BENCHMARK_RUBRIC_JUDGE_LLM_PROVIDER and BENCHMARK_RUBRIC_JUDGE_LLM_MODEL must be set together"
            )
        return self


__all__ = ["BenchmarkRubricJudgeLlmSettings"]
