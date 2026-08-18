"""DTOs for validator registration metadata."""

from __future__ import annotations

from pydantic import BaseModel, Field

from harnyx_validator.domain.shared_config import VALIDATOR_STRICT_CONFIG


class ValidatorRegistrationMetadata(BaseModel):
    model_config = VALIDATOR_STRICT_CONFIG

    validator_version: str = Field(min_length=1)
    source_revision: str | None = Field(default=None, min_length=1)
    registry_digest: str | None = Field(default=None, min_length=1)
    local_image_id: str | None = Field(default=None, min_length=1)


__all__ = ["ValidatorRegistrationMetadata"]
