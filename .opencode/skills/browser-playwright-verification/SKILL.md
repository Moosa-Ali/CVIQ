---
name: browser-playwright-verification
description: Use Playwright MCP to verify CVIQ workflows, visible output, responsive behavior, console errors, and network failures. Use after frontend, API, template, export, or end-to-end changes.
---

# Browser Playwright Verification

Use a disposable `CVMOD_DATA_DIR` and local server. Never inspect the real `.cvmod/` or load real credentials.

Verify:
- Landing and navigation.
- CV upload and job-description entry.
- Parse, analyze, gaps, tailor, assist, and chat paths when affected.
- Template gallery, preview, PDF, DOCX, and library paths when affected.
- Loading, empty, validation, error, retry, and long-content states.
- Desktop and mobile viewports.
- DOM behavior, visible text, console errors, failed requests, and screenshots.

Report exact steps, expected and actual results, severity, evidence, and environment details. Do not fix source during verification.
