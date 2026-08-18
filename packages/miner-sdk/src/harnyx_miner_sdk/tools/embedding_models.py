"""Provider-agnostic request/response models for miner-facing embedding tools."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from harnyx_miner_sdk.tools.llm_provider_extra import OpenRouterExtra, validate_provider_extra
from harnyx_miner_sdk.tools.types import ToolInvocationTimeout

EmbeddingProviderName = Literal["chutes", "openrouter"]
EmbeddingInputType = Literal["query", "document"]

QWEN3_CHUTES_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-8B-TEE"
QWEN3_OPENROUTER_EMBEDDING_MODEL = "qwen/qwen3-embedding-8b"
QWEN3_DEFAULT_QUERY_INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query"
MINER_SELECTED_EMBEDDING_PROVIDERS: tuple[EmbeddingProviderName, ...] = (
    "chutes",
    "openrouter",
)
MINER_SELECTED_EMBEDDING_PROVIDER_MODELS: Mapping[EmbeddingProviderName, tuple[str, ...]] = {
    "chutes": (QWEN3_CHUTES_EMBEDDING_MODEL,),
    "openrouter": (QWEN3_OPENROUTER_EMBEDDING_MODEL,),
}


@dataclass(frozen=True, slots=True)
class MinerSelectedEmbeddingProviderModel:
    provider: EmbeddingProviderName
    model: str


class EmbedTextRequest(BaseModel):
    """Request payload for the `embed_text` tool."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: EmbeddingProviderName
    model: str = Field(min_length=1)
    texts: tuple[str, ...] = Field(min_length=1)
    input_type: EmbeddingInputType
    instruction: str | None = Field(default=None, min_length=1)
    dimensions: int | None = Field(default=None, ge=32, le=4096)
    provider_extra: OpenRouterExtra | None = None
    timeout: ToolInvocationTimeout | None = None

    @field_validator("texts", mode="before")
    @classmethod
    def _normalize_texts(cls, value: object) -> object:
        if isinstance(value, str):
            return (value,)
        return value

    @field_validator("texts")
    @classmethod
    def _validate_texts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if not normalized or any(not item for item in normalized):
            raise ValueError("texts must contain non-empty strings")
        return normalized

    @model_validator(mode="before")
    @classmethod
    def _validate_provider_extra(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = cast(Mapping[str, object], value)
        if "provider_extra" not in data:
            return value
        provider = data.get("provider")
        if not isinstance(provider, str):
            return value

        parsed = validate_provider_extra(provider=provider, provider_extra=data.get("provider_extra"))
        normalized = dict(data)
        normalized["provider_extra"] = parsed
        return normalized

    @model_validator(mode="after")
    def _validate_provider_model_and_instruction_scope(self) -> EmbedTextRequest:
        parse_miner_selected_embedding_provider_model(provider=self.provider, model=self.model)
        if self.input_type == "document" and self.instruction is not None:
            raise ValueError("instruction is only supported for query embeddings")
        return self


class TextEmbeddingResult(BaseModel):
    """Single embedding result item."""

    index: int = Field(ge=0)
    embedding: list[float] = Field(min_length=1)


class EmbeddingUsage(BaseModel):
    """Token usage returned by an embedding provider."""

    prompt_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class EmbedTextResponse(BaseModel):
    """Response payload for the `embed_text` tool."""

    provider: EmbeddingProviderName
    model: str
    input_type: EmbeddingInputType
    data: list[TextEmbeddingResult] = Field(min_length=1)
    dimensions: int = Field(gt=0)
    usage: EmbeddingUsage | None = None


def parse_miner_selected_embedding_provider(raw: str | None) -> EmbeddingProviderName:
    if raw is None:
        raise ValueError("embedding provider must be specified")
    value = raw.strip().lower()
    if value not in MINER_SELECTED_EMBEDDING_PROVIDERS:
        raise ValueError(f"embedding provider {value!r} is not supported")
    return cast(EmbeddingProviderName, value)


def parse_miner_selected_embedding_provider_model(
    *,
    provider: str | None,
    model: str | None,
) -> MinerSelectedEmbeddingProviderModel:
    selected_provider = parse_miner_selected_embedding_provider(provider)
    if model is None:
        raise ValueError("model must be provided for validator tools")
    selected_model = model.strip()
    if not selected_model:
        raise ValueError("model must be provided for validator tools")
    if selected_model not in MINER_SELECTED_EMBEDDING_PROVIDER_MODELS[selected_provider]:
        raise ValueError(
            f"model {selected_model!r} is not supported for embedding provider {selected_provider!r}"
        )
    return MinerSelectedEmbeddingProviderModel(provider=selected_provider, model=selected_model)


__all__ = [
    "EmbeddingInputType",
    "EmbeddingProviderName",
    "EmbeddingUsage",
    "EmbedTextRequest",
    "EmbedTextResponse",
    "MINER_SELECTED_EMBEDDING_PROVIDER_MODELS",
    "MINER_SELECTED_EMBEDDING_PROVIDERS",
    "MinerSelectedEmbeddingProviderModel",
    "QWEN3_DEFAULT_QUERY_INSTRUCTION",
    "QWEN3_CHUTES_EMBEDDING_MODEL",
    "QWEN3_OPENROUTER_EMBEDDING_MODEL",
    "TextEmbeddingResult",
    "parse_miner_selected_embedding_provider",
    "parse_miner_selected_embedding_provider_model",
]
