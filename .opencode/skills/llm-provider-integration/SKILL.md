---
name: llm-provider-integration
description: Implement CVIQ provider abstraction, prompts, structured output, streaming, and vision content blocks safely. Use for LLM and AI-assisted CV changes.
---

# LLM Provider Integration

Preserve the shared `chat(messages, temperature, max_tokens)` contract and support plain strings plus text and image content blocks. Use vision only for scanned PDFs; text-based uploads send extracted structure, not images.

Validate structured responses, bound generation, handle provider timeouts and malformed output, and never log API keys, full prompts, uploaded CV content, or model responses containing personal data. Keep deterministic exports outside the AI path.
