"""Provider-agnostic request/response models for embedding tools.

This module re-exports the miner SDK models so commons/validator/platform share
the exact same schema and typing.
"""

from __future__ import annotations

from harnyx_miner_sdk.tools.embedding_models import (
    MINER_SELECTED_EMBEDDING_PROVIDER_MODELS,
    MINER_SELECTED_EMBEDDING_PROVIDERS,
    QWEN3_CHUTES_EMBEDDING_MODEL,
    QWEN3_DEFAULT_QUERY_INSTRUCTION,
    QWEN3_OPENROUTER_EMBEDDING_MODEL,
    EmbeddingInputType,
    EmbeddingProviderName,
    EmbeddingUsage,
    EmbedTextRequest,
    EmbedTextResponse,
    MinerSelectedEmbeddingProviderModel,
    TextEmbeddingResult,
    parse_miner_selected_embedding_provider,
    parse_miner_selected_embedding_provider_model,
)

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
