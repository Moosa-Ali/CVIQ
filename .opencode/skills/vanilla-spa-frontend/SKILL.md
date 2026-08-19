---
name: vanilla-spa-frontend
description: Maintain CVIQ's static vanilla-JavaScript SPA, HTML structure, API wiring, and browser states without adding a Node build step. Use for frontend behavior changes.
---

# Vanilla SPA Frontend

Read `frontend/index.html`, `frontend/app.js`, and `frontend/styles.css` before editing. Preserve the static-files-served-by-FastAPI architecture.

Every async flow needs visible loading, success, empty, validation, network-error, retry, and long-content behavior. Keep API calls aligned with backend routes and avoid duplicating business rules in the browser. Keep markup semantic and delegate visual CSS ownership to `ui-expert`.
