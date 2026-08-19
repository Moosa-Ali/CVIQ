---
name: security-review
description: Review CVIQ secrets, uploads, templates, LLM prompts, persistence, dependencies, and API boundaries for security vulnerabilities. Use for security-sensitive changes and release review.
---

# Security Review

Check secret storage and redaction, environment overrides, logs, CORS, request validation, uploaded-file parsing, path traversal, template injection, unsafe serialization, SSRF, prompt injection, provider failures, and data exposure.

Never read actual credentials or `.cvmod` contents. Findings must include severity, exploitability, affected path, impact, and remediation. Treat secret exposure, arbitrary file access, and cross-user data exposure as release blockers.
