import logging
import re

from .base import LLMConfig, LLMConfigError, LLMError, normalize_content

TIMEOUT = 60.0

logger = logging.getLogger("cviq")

_MAX_RESPONSE_LOG = 500


def _redact(text: str) -> str:
    """Mask bearer tokens / API keys that might appear in logged bodies."""
    text = re.sub(r"Bearer\s+[A-Za-z0-9._-]+", "Bearer ***", text)
    text = re.sub(r"sk-or-v1-[A-Za-z0-9._-]+", "sk-or-v1-***", text)
    return text


def _to_openai_content(content) -> object:
    """Map a canonical content value to the OpenAI chat-completions shape.

    Plain strings pass through unchanged. Canonical blocks are mapped:
      {"type":"text","text":T} -> {"type":"text","text":T}
      {"type":"image","format":"png","bytes":B64} ->
          {"type":"image_url","image_url":{"url":"data:image/png;base64,"+B64}}
    """
    content = normalize_content(content)
    if isinstance(content, str):
        return content
    mapped = []
    for block in content:
        if block["type"] == "text":
            mapped.append({"type": "text", "text": block["text"]})
        else:  # image
            mapped.append(
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64," + block["bytes"]},
                }
            )
    return mapped


class OpenRouterClient:
    provider_name = "openrouter"

    def __init__(self, cfg: LLMConfig):
        self.api_key = cfg.openrouter_api_key
        self.model = cfg.openrouter_model
        self.endpoint = "https://openrouter.ai/api/v1/chat/completions"
        self._usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def _record_usage(self, data: dict) -> None:
        u = data.get("usage") or {}
        try:
            self._usage["prompt_tokens"] += int(u.get("prompt_tokens", 0) or 0)
            self._usage["completion_tokens"] += int(u.get("completion_tokens", 0) or 0)
        except (TypeError, ValueError):
            pass

    def _request(self, payload: dict) -> dict:
        import httpx

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_error = None
        for attempt in range(2):
            try:
                logger.debug("OpenRouter request model=%s attempt=%d", self.model, attempt + 1)
                resp = httpx.post(self.endpoint, json=payload, headers=headers, timeout=TIMEOUT)
                if resp.status_code >= 400:
                    body = _redact(resp.text[:_MAX_RESPONSE_LOG])
                    logger.error(
                        "OpenRouter HTTP error status=%s body=%s",
                        resp.status_code,
                        body,
                    )
                    # Some models reject a "reasoning" param with a 400. This fallback is only
                    # relevant if some code path passes a reasoning param; it's harmless to keep
                    # and retries without the key so the request still succeeds.
                    if (
                        resp.status_code == 400
                        and "reasoning" in payload
                        and "reasoning" in resp.text.lower()
                    ):
                        logger.info(
                            "OpenRouter model %s rejected 'reasoning' param; retrying without it",
                            self.model,
                        )
                        fallback = payload.copy()
                        fallback.pop("reasoning", None)
                        return self._request(fallback)
                    raise LLMError(f"OpenRouter error {resp.status_code}: {resp.text}")
                return resp.json()
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning("OpenRouter network error attempt=%d error=%s", attempt + 1, exc)
                if attempt == 0:
                    continue
                raise LLMError(f"OpenRouter request failed: {last_error}") from last_error
        raise LLMError(f"OpenRouter request failed: {last_error}") from last_error

    @staticmethod
    def _extract(data: dict) -> tuple[str, str, str]:
        """Return (content, finish_reason, reasoning) from an OpenRouter response."""
        try:
            choice = data["choices"][0]
            message = choice["message"]
            content = message.get("content")
            finish_reason = choice.get("finish_reason")
            reasoning = message.get("reasoning")
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Unexpected OpenRouter response shape") from exc
        if isinstance(content, list):
            text_parts = "".join(
                part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") != "reasoning"
            )
            reasoning_parts = "".join(
                part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "reasoning"
            )
            content = text_parts or reasoning_parts
        return content, finish_reason, reasoning

    def chat(self, messages: list[dict], temperature: float = 0.4, max_tokens: int = 2048) -> str:
        if not self.api_key:
            raise LLMConfigError("OpenRouter API key is not configured")
        payload_messages = [
            {**m, "content": _to_openai_content(m.get("content"))} for m in messages
        ]
        payload = {
            "model": self.model,
            "messages": payload_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            # Reasoning is intentionally left at the model's default (enabled for reasoning
            # models). Disabling it measurably degrades output quality, so we keep it on and
            # instead give reasoning models a large enough token budget to reason AND emit
            # content. Models that reject a "reasoning" param are handled by the fallback in
            # _request.
        }
        data = self._request(payload)
        content, finish_reason, reasoning = self._extract(data)
        self._record_usage(data)

        # Truncation retry: if the model ran out of tokens with no content (e.g. a reasoning
        # model that spent its whole budget reasoning), retry once with a much larger budget.
        if not content and finish_reason == "length":
            retry_tokens = max(max_tokens * 2, 16000)
            logger.info(
                "OpenRouter response truncated (finish_reason=length); retrying once with max_tokens=%d",
                retry_tokens,
            )
            data = self._request({**payload, "max_tokens": retry_tokens})
            content, finish_reason, reasoning = self._extract(data)
            self._record_usage(data)

        if not content:
            if finish_reason == "length":
                raise LLMError(
                    f"OpenRouter response truncated (max_tokens too small for model '{self.model}'). "
                    "Increase max_tokens or use a non-reasoning model."
                )
            if reasoning:
                content = reasoning
        if not content:
            raise LLMError(
                f"OpenRouter returned empty content (finish_reason: {finish_reason!r}) for model '{self.model}'"
            )
        logger.info("OpenRouter chat finish_reason=%s content_len=%d", finish_reason, len(content))
        return content

    def test(self) -> None:
        content = self.chat(
            [{"role": "user", "content": "Reply with the single word OK."}],
            temperature=0.0,
            max_tokens=1024,
        )
        if not content:
            raise LLMError("OpenRouter test returned an empty response")
