# AGENTS.md

CVIQ is a single-user, single-process **FastAPI** app that creates/optimizes ATS-friendly
CVs against a Job Description via an LLM. The backend serves a vanilla-JS SPA directly —
**there is no Node/build step; the frontend is static files mounted by FastAPI.**

## Run / verify

- **No `python` on PATH is assumed.** Always invoke the venv interpreter explicitly:
  `& ".\.venv\Scripts\python.exe" ...`. Launchers `start.cmd` / `start.ps1` create `.venv`
  if missing (auto-discovers Python and installs dependencies) and run uvicorn on
  `127.0.0.1:8000` **without `--reload`** (those flags are hard-coded).
- Dev run with reload: `& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`
- **Tests**: the single suite is `tests/smoke_test.py`. Run from project root:
  `$env:PYTHONPATH = (Get-Location).Path; & ".\.venv\Scripts\python.exe" tests\smoke_test.py`
  Expect `ALL SMOKE TESTS PASSED`. It uses a `FakeLLM` (no real API key/network needed).
- README's testing section uses a generic `(Get-Location).Path` for the project root.

## Architecture / boundaries

```
app/main.py            FastAPI app; mounts frontend/ as StaticFiles(html=True), CORS, routers
app/config.py          Settings via pydantic-settings, env prefix CVMOD_
app/routes/            config, cv, export, templates APIRouters (/api, /api/cv, /api/library, /api/export, /api/templates)
app/services/
  llm/                 provider abstraction — single chat() interface + vision content-blocks: base, openrouter, bedrock, config_store
  cv/                  models, parser, session (in-memory TTL upload store), classify (LLM/heuristic), jd_parser, gaps, analyzer, tailor, writer, assist, validate, library
  export/              render_html.py (Jinja2) / pdf_render.py (PyMuPDF Story) / docx_export.py (python-docx); pdf_export.py keeps only the reportlab legacy fallback — deterministic, AI-free
app/templates_html/    8 Jinja2 CV templates (render layer): modern, classic, minimal, awesome-cv, deedy-resume, cvresume, universal-resume, newfuture-cv
app/services/templates.py  CV template gallery: scan Templates/, PNG previews, slot detection (name/title/contact/sections), render_template + converted mapping
app/templates.py       generic template (modern/classic/minimal) + accent definitions (from-scratch path)
Templates/             8 dummy-data PDFs rendered from the 8 HTML templates (gallery source; original 20 designs archived in Templates/Archive/)
frontend/              index.html, app.js, styles.css, assets/ (brand logos; vanilla SPA, served by backend, full API wiring)
logos/                 source brand assets (favicon.png, logo_no_text.png, logo_text.png)
docs/                  SRS.md, design.md (authoritative requirements/design)
```

- LLM providers plug in behind the `chat(messages, temperature, max_tokens)` interface
  (`app/services/llm/base.py`). Message content may be a plain string OR a list of content
  blocks (`text_part` / `image_part`); vision is used only for scanned (image-based) PDF
  uploads — text-based uploads send extracted structure, never images.
- Upload context (original bytes, text, structure, page images) lives in an **in-memory TTL
  session store** (`app/services/cv/session.py`, ~30 min) — nothing is written to disk.
- Persistence is a git-ignored local folder: **`.cvmod/`** holds `config.json` (LLM creds +
  settings) and the saved CV library. Data dir is overridable via `CVMOD_DATA_DIR`.

## Gotchas / conventions

- **Not a git repo yet** — `.git` is absent (only `.gitignore` exists listing `.venv/` and
  `.cvmod/`). Don't assume git workflows work; initialize or confirm before committing.
- `.gitignore` is minimal; keep `.venv/` and `.cvmod/` out of any future commit.
- Secrets are stored in `.cvmod/config.json`, **redacted (`***`)** when read back by the UI,
  and never logged. Env vars `OPENROUTER_API_KEY`, `AWS_ACCESS_KEY_ID`,
  `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` override saved config at request time.
- App settings use the **`CVMOD_`** prefix (see `app/config.py`). Key endpoints example:
  `GET/POST /api/config`, `POST /api/config/test`, `POST /api/cv/parse|analyze|validate`,
  `POST /api/cv/gaps`, `POST /api/cv/tailor/suggest|chat`, `POST /api/cv/assist` (kinds
  incl. `optimize*`), `POST /api/export/pdf|docx` (deterministic render pipeline),
  `GET /api/templates`, `GET/.../preview/{page}`, `POST /api/templates/{id}/export/pdf|docx`
  (render the gallery design's mapped HTML template from canonical CV data).
- Uploads are parsed to a session then served from memory (TTL); nothing uploaded is persisted
  to disk. Scanned PDFs (no extractable text) are analyzed via page images (vision); text-based
  uploads send extracted structure only.
- All exports are rendered from the canonical JSON via deterministic HTML templates
  (Jinja2 → PyMuPDF Story / python-docx); nothing is ever edited in place. Output is
  selectable, non-image text (ATS-extractable) — some templates (deedy-resume,
  newfuture-cv) use table/two-column layouts that remain extractable.