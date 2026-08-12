import base64

from .base import LLMConfig, LLMConfigError, LLMError, normalize_content


def _to_converse(messages: list[dict]):
    system = [{"text": m["content"]} for m in messages if m.get("role") == "system"]
    converted = []
    for message in messages:
        role = message.get("role")
        raw = message.get("content")
        if role == "system":
            continue
        if isinstance(raw, str):
            parts = [{"text": raw}]
        elif isinstance(raw, list):
            raw = normalize_content(raw)
            parts = []
            for block in raw:
                if block["type"] == "text":
                    parts.append({"text": block["text"]})
                else:  # image
                    parts.append(
                        {
                            "image": {
                                "format": block.get("format", "png"),
                                "source": {"bytes": base64.b64decode(block["bytes"])},
                            }
                        }
                    )
        else:
            parts = [{"text": str(raw)}]
        converted.append({"role": role, "content": parts})
    return system, converted


class BedrockClient:
    provider_name = "bedrock"

    def __init__(self, cfg: LLMConfig):
        self.access_key = cfg.bedrock_access_key
        self.secret_key = cfg.bedrock_secret_key
        self.region = cfg.bedrock_region
        self.model = cfg.bedrock_model

    def _runtime(self):
        import boto3

        return boto3.client(
            "bedrock-runtime",
            region_name=self.region or None,
            aws_access_key_id=self.access_key or None,
            aws_secret_access_key=self.secret_key or None,
        )

    def chat(self, messages: list[dict], temperature: float = 0.4, max_tokens: int = 2048) -> str:
        if not (self.access_key and self.secret_key and self.region):
            raise LLMConfigError("AWS Bedrock credentials are not configured")
        system, converse = _to_converse(messages)
        request = {
            "modelId": self.model,
            "messages": converse,
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
        }
        if system:
            request["system"] = system
        client = self._runtime()
        try:
            resp = client.converse(**request)
        except Exception as exc:
            raise LLMError(f"Bedrock request failed: {exc}") from exc
        try:
            content = resp["output"]["message"]["content"]
            parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("text")]
            text = "".join(parts)
        except (KeyError, TypeError) as exc:
            raise LLMError("Unexpected Bedrock response shape") from exc
        return text

    def test(self) -> None:
        content = self.chat(
            [{"role": "user", "content": "Reply with the single word OK."}],
            temperature=0.0,
            max_tokens=5,
        )
        if not content:
            raise LLMError("Bedrock test returned an empty response")
