# Hide AWS Bedrock Provider from Settings UI

**Date:** 2026-08-17
**Summary:** Gated the AWS Bedrock provider option in the Settings UI behind a single feature flag.

## Change Details
The AWS Bedrock provider has been hidden from the frontend UI. The backend services and routes remain fully operational and untouched.

- **Affected File:** `frontend/app.js`
- **Behavior when disabled:**
  - The "Provider" card in Settings renders only the OpenRouter option.
  - The Bedrock configuration pane is not rendered.
  - Saved provider resolution defaults to `openrouter`.
  - `collect()` submits `provider: 'openrouter'`.
  - Helper text in banners/onboarding is updated via `PROVIDERS_LABEL` to show only "OpenRouter".
  - Stored Bedrock credentials in `.cvmod/config.json` are preserved because the backend only updates `SECRET_FIELDS` when non-blank values are provided.

## Re-enable Switch
To restore AWS Bedrock support in the UI, modify `frontend/app.js`:
- Flip `const BEDROCK_ENABLED = false;` (approx. line 7) to `true`.
- The `PROVIDERS_LABEL` (approx. line 8) will automatically revert to "OpenRouter or AWS Bedrock".

## Verification Evidence
- **Smoke Tests:** Ran `$env:PYTHONPATH = "C:\D-Drive\Work\CVIQ"; & ".\.venv\Scripts\python.exe" tests\smoke_test.py` $\rightarrow$ `ALL SMOKE TESTS PASSED`.
- **Browser Verification:**
  - Verified Settings page contains no "AWS Bedrock" text, buttons, or fields.
  - Verified OpenRouter pane renders correctly.
  - Confirmed zero console errors.
  - Verified round-trip saving of OpenRouter API key: `GET /api/config` returned `provider: openrouter`, `configured: true`, and the key was redacted as `***`.
  - Confirmed "not configured" banner clears after successful save and reload.

## Remaining Risks
- The working tree contains large pre-existing uncommitted changes from other workstreams; however, this specific change is strictly isolated to the Bedrock UI visibility logic in `frontend/app.js`.
