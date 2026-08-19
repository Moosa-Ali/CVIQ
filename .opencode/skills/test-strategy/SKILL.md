---
name: test-strategy
description: Build deterministic CVIQ test coverage across services, API routes, exports, persistence, LLM failures, and browser workflows. Use for test planning and regression verification.
---

# Test Strategy

Start with the acceptance criteria and select the smallest layer that proves each behavior. Use unit tests for pure logic, API tests for route contracts, integration tests for service boundaries, and Playwright for visible user workflows.

Tests must avoid real provider calls, credentials, network dependence, and writes to the real `.cvmod/`. Include failure, empty, validation, timeout, malformed-model-output, and long-content cases where relevant. Report environment failures separately from product failures.
