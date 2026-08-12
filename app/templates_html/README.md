# HTML CV templates (`app/templates_html/`)

This directory holds the Jinja2 HTML CV templates used by the deterministic
rendering pipeline (`app/services/export/render_html.py`).

- Template files are named `<template_id>.html.j2`, e.g. `modern.html.j2`.
- Each template renders the canonical CV JSON: the Jinja context is
  `cv` (a `CVData.model_dump()` dict) plus `accent` (hex string) and
  `template_id`.
- Autoescaping is enabled: user content is escaped by the renderer.
- All templates must produce selectable, non-image text output (see the product
  brief); table/two-column designs (deedy-resume, newfuture-cv) remain
  ATS-extractable.
- This is a **data directory**, deliberately NOT a Python package (no
  `__init__.py`, not importable). `render_html.py` resolves it relative to the
  package root via `Path(__file__)`.

The eight template files:

  modern, classic, minimal, awesome-cv, deedy-resume, cvresume,
  universal-resume, newfuture-cv

All eight templates are verified to render to PDF through the PyMuPDF Story
pipeline (`app/services/export/pdf_render.py`); DOCX output
(`app/services/export/docx_export.py`) is generated with python-docx and ignores
HTML. Gallery metadata in `app/services/templates.py` maps each gallery design to
one of these ids: dedicated designs use their own template (`render_template`,
`converted: true`), unconverted designs map via `GALLERY_RENDER_MAP` to
`modern`/`minimal`/`classic` (all of which have files here). `modern.html.j2` is
the default template; the builtin fallback renderer is only used when a
`.html.j2` file is missing.

Notes for template authors:

- MuPDF's Story engine (PyMuPDF 1.28) does **not** support CSS custom
  properties (`var(...)`) or `text-transform`. Accent colors are therefore
  injected as literal values at render time (`{{ accent }}` in the `<style>`
  block) while the root element still carries `style="--accent: ..."` for
  in-browser previews. Uppercase heading text is written literally or via the
  Jinja `upper`/`title` filters, never via CSS.
- Two-column designs (deedy-resume, newfuture-cv skills) use HTML `<table>`
  layouts — block/table CSS only, no flexbox/grid/absolute positioning.

If a requested `<template_id>.html.j2` file is missing, `render_html` falls
back to its builtin single-column modern renderer — it never raises and always
returns valid, escaped HTML.