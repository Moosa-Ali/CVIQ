import logging
import os
from typing import Protocol

from pydantic import BaseModel

SECRET_FIELDS = ("openrouter_api_key", "bedrock_access_key", "bedrock_secret_key")

logger = logging.getLogger("cviq")


class LLMError(Exception):
    pass


def friendly_llm_error(exc: Exception) -> str:
    """Map common provider errors to friendly, user-facing messages.

    Returns the original message when nothing matches so the UI never shows raw
    provider JSON for the common cases (401/unauthorized, 429 rate-limit,
    timeout). Never logs or echoes secrets.
    """
    msg = str(exc or "")
    low = msg.lower()
    if "401" in msg or "unauthorized" in low or "invalid api key" in low or "authentication" in low:
        return "Invalid API key — check Settings."
    if "429" in msg or "rate limit" in low:
        return "Rate limited by the provider — wait a moment and retry."
    if "timeout" in low or "timed out" in low:
        return "The AI provider timed out — try again."
    return msg or "The AI provider failed — try again."


def text_part(text: str) -> dict:
    """Canonical text content block for a user message."""
    return {"type": "text", "text": text}


def image_part(base64_bytes_str: str) -> dict:
    """Canonical image content block for a user message (PNG, base64-encoded bytes)."""
    return {"type": "image", "format": "png", "bytes": base64_bytes_str}


def normalize_content(content) -> object:
    """Normalize a message's ``content`` to the canonical block convention.

    A plain string is returned unchanged (backwards compatible with existing
    callers). A list is validated as a sequence of content blocks: each item must
    be a dict with a known ``type`` (``"text"`` or ``"image"``), otherwise an
    ``LLMError`` is raised.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                raise LLMError(f"Invalid content block (expected dict, got {type(block).__name__})")
            block_type = block.get("type")
            if block_type not in ("text", "image"):
                raise LLMError(f"Unknown content block type: {block_type!r}")
        return content
    raise LLMError(f"Invalid content type: {type(content).__name__}")


class LLMConfigError(LLMError):
    pass


class LLMConfig(BaseModel):
    provider: str = "openrouter"
    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-sonnet-4-6"
    bedrock_access_key: str = ""
    bedrock_secret_key: str = ""
    bedrock_region: str = ""
    bedrock_model: str = "anthropic.claude-sonnet-4-5-v2-0"

    def redacted(self) -> "LLMConfig":
        data = self.model_dump()
        for field in SECRET_FIELDS:
            if data.get(field):
                data[field] = "***"
        return LLMConfig(**data)

    def configured(self) -> bool:
        if self.provider == "openrouter":
            return bool(self.openrouter_api_key)
        if self.provider == "bedrock":
            return bool(self.bedrock_access_key and self.bedrock_secret_key and self.bedrock_region)
        return False


_ENV_OVERRIDES = {
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "bedrock_access_key": "AWS_ACCESS_KEY_ID",
    "bedrock_secret_key": "AWS_SECRET_ACCESS_KEY",
    "bedrock_region": "AWS_REGION",
}


def apply_env_overrides(cfg: LLMConfig) -> LLMConfig:
    data = cfg.model_dump()
    changed = False
    for field, env_name in _ENV_OVERRIDES.items():
        value = os.environ.get(env_name)
        if value:
            data[field] = value
            changed = True
    return LLMConfig(**data) if changed else cfg


class LLMClient(Protocol):
    provider_name: str

    def chat(self, messages: list[dict], temperature: float = 0.4, max_tokens: int = 2048) -> str: ...

    def test(self) -> None: ...


# Content-block convention for user messages:
#
# A message's ``content`` may be a plain string (backwards compatible) OR a list of
# canonical content blocks:
#
#   Text part (canonical):
#     {"type": "text", "text": "..."}
#   Image part (canonical, PNG bytes base64-encoded):
#     {"type": "image", "format": "png", "bytes": "<base64-string>"}
#
# Providers map these canonical blocks to their own wire format (see
# ``openrouter.py`` and ``bedrock.py``). Use ``text_part()`` / ``image_part()`` to
# build blocks and ``normalize_content()`` to validate them.


def get_client(cfg: LLMConfig) -> LLMClient:
    cfg = apply_env_overrides(cfg)
    model = cfg.openrouter_model if cfg.provider == "openrouter" else cfg.bedrock_model
    logger.info("Initializing LLM client provider=%s model=%s", cfg.provider, model)
    if cfg.provider == "openrouter":
        from .openrouter import OpenRouterClient

        return OpenRouterClient(cfg)
    if cfg.provider == "bedrock":
        from .bedrock import BedrockClient

        return BedrockClient(cfg)
    raise LLMConfigError(f"Unknown LLM provider: {cfg.provider!r}")
