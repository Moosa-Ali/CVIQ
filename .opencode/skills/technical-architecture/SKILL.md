---
name: technical-architecture
description: Design small, compatible CVIQ solutions with explicit interfaces, ownership, data contracts, and rollout risks. Use for cross-layer or architectural changes.
---

# Technical Architecture

Prefer the smallest change that preserves current boundaries. Define API contracts, service responsibilities, persisted shapes, failure behavior, test seams, and file ownership.

Respect these boundaries:
- FastAPI serves the static vanilla-JS SPA.
- LLM providers implement the shared chat abstraction.
- Upload context is in-memory with TTL.
- `.cvmod/` is local persistence and contains secrets.
- Exports are deterministic and AI-free.

Record alternatives rejected and why. Avoid compatibility layers without a concrete persisted-data or external-consumer need.
