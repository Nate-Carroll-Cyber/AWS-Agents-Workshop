"""
module2/config/models.py
========================
Model provider configuration for Module 2 Repository Analysis Agent.

This module uses LangChain's ChatBedrock for model access, demonstrating
the Module 2 framework approach compared to Module 1's Strands BedrockModel.

FRAMEWORK COMPARISON
--------------------
Module 1 (Strands):
    from strands.models import BedrockModel
    model = BedrockModel(model_id="...", region_name="...")

Module 2 (LangChain):
    from langchain_aws import ChatBedrock
    model = ChatBedrock(model_id="...", region_name="...")

Both use the same underlying Amazon Bedrock API, but LangChain provides
additional features like streaming, callbacks, and integration with the
broader LangChain ecosystem.
"""

from __future__ import annotations

import os
import re
from typing import Any

from langchain_aws import ChatBedrock

# Cross-region inference profile (recommended)
CLAUDE_SONNET_4_CRI = "us.anthropic.claude-sonnet-4-20250514-v1:0"

# Single-region model ID (fallback)
CLAUDE_SONNET_4_DIRECT = "anthropic.claude-sonnet-4-20250514-v1:0"

_REGION_PATTERN = re.compile(r"^[a-z]{2}-[a-z]+-\d+$")
_MAX_TOKENS_LIMIT = 8192


def _parse_allowed_model_ids() -> set[str]:
    """Return allowed model IDs from env or secure defaults."""
    configured = os.getenv("AGENT_ALLOWED_MODEL_IDS", "").strip()
    if configured:
        return {model_id.strip() for model_id in configured.split(",") if model_id.strip()}

    return {
        CLAUDE_SONNET_4_CRI,
        CLAUDE_SONNET_4_DIRECT,
    }


def _validate_region(region: str) -> str:
    """Validate AWS region format."""
    if not _REGION_PATTERN.match(region):
        raise ValueError(f"Invalid AWS region format: {region}")
    return region


def _validate_temperature(temperature: float) -> float:
    """Validate model temperature range."""
    if not (0.0 <= temperature <= 1.0):
        raise ValueError("temperature must be between 0.0 and 1.0")
    return temperature


def _validate_max_tokens(max_tokens: int) -> int:
    """Validate token budget to avoid excessive outputs/cost."""
    if max_tokens < 1 or max_tokens > _MAX_TOKENS_LIMIT:
        raise ValueError(f"max_tokens must be between 1 and {_MAX_TOKENS_LIMIT}")
    return max_tokens


def get_chat_bedrock_model(
    model_id: str = CLAUDE_SONNET_4_CRI,
    region: str | None = None,
    temperature: float = 0.1,
    max_tokens: int = 4096,
    streaming: bool = False,
    **kwargs: Any,
) -> ChatBedrock:
    """
    Return a LangChain ChatBedrock model configured for Claude Sonnet 4.

    This is the Module 2 equivalent of Module 1's get_bedrock_model().
    The key difference is that ChatBedrock integrates with LangChain's
    LCEL (LangChain Expression Language) and supports streaming responses.

    Prerequisites
    -------------
    1. AWS credentials configured (aws configure or AWS_* env vars)
    2. Bedrock model access enabled for Anthropic in your region:
       AWS Console → Amazon Bedrock → Model Access → Request Access

    Parameters
    ----------
    model_id : str
        Bedrock model ID. Default uses cross-region inference profile.
    region : str, optional
        AWS region. Falls back to AWS_REGION / AWS_DEFAULT_REGION env vars.
    temperature : float
        Low temperature (0.1) = more deterministic — appropriate for analysis tasks.
    max_tokens : int
        Max response tokens. 4096 is sufficient for structured analysis.
    streaming : bool
        Enable streaming responses. Useful for long-running analysis.
    **kwargs : Any
        Additional ChatBedrock parameters (e.g., model_kwargs).

    Returns
    -------
    ChatBedrock
        Configured LangChain ChatBedrock model instance.

    Example
    -------
    >>> from module2.config.models import get_chat_bedrock_model
    >>> model = get_chat_bedrock_model()
    >>> response = model.invoke("Analyze this repository...")
    """
    aws_region = region or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    aws_region = _validate_region(aws_region)

    allowed_model_ids = _parse_allowed_model_ids()
    if model_id not in allowed_model_ids:
        raise ValueError(
            "Model ID is not allowed by policy. "
            f"Configured allowlist size: {len(allowed_model_ids)}"
        )

    validated_temperature = _validate_temperature(temperature)
    validated_max_tokens = _validate_max_tokens(max_tokens)

    user_model_kwargs = kwargs.pop("model_kwargs", {})
    if not isinstance(user_model_kwargs, dict):
        raise TypeError("model_kwargs must be a dictionary")

    # Enforce validated generation controls even if caller passes model_kwargs.
    merged_model_kwargs = dict(user_model_kwargs)
    merged_model_kwargs["temperature"] = validated_temperature
    merged_model_kwargs["max_tokens"] = validated_max_tokens

    # LangChain ChatBedrock configuration
    return ChatBedrock(
        model_id=model_id,
        region_name=aws_region,
        model_kwargs=merged_model_kwargs,
        streaming=streaming,
        **kwargs,
    )


class ModelConfig:
    """Configuration constants for Module 2 models."""

    CLAUDE_SONNET_4 = CLAUDE_SONNET_4_CRI
    CLAUDE_SONNET_4_DIRECT = CLAUDE_SONNET_4_DIRECT
    DEFAULT_TEMPERATURE = 0.1
    DEFAULT_MAX_TOKENS = 4096
    DEFAULT_STREAMING = False
