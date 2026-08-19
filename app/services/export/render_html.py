"""Deterministic, no-AI HTML rendering of the canonical CV object.

This module is the single entry point of the content/presentation pipeline:
``CVData`` (canonical) -> HTML (presentation). Rendering is pure, stateless and
deterministic — no LLM, no file-system edits, always valid escaped HTML.

Template files live in ``app/templates_html/<template_id>.html.j2`` (a data
directory, not a package). If a template file is missing, a builtin
single-column modern fallback renderer is used instead, so ``render_html``
never raises and never produces broken markup.
"""

import html as _html
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, TemplateNotFound, select_autoescape

from ..cv.models import CVData, DEFAULT_SECTION_ORDER
from ..cv.template_config import resolve_template_config, PRESET_IDS

_TEMPLATES_HTML_DIR = Path(__file__).resolve().parent.parent.parent / "templates_html"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_HTML_DIR)),
    autoescape=select_autoescape(("html", "htm", "html.j2")),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _escape(text) -> str:
    return _html.escape(str(text or ""), quote=False)


def _fmt_dates(start: str, end: str) -> str:
    if start or end:
        return f"{start} - {end}".strip(" -")
    return ""


def _section_order_of(cv: CVData) -> list[str]:
    """The CV's section order, materialized to the default order when empty."""
    order = [str(s).strip() for s in (cv.section_order or []) if str(s or "").strip()]
    return order or list(DEFAULT_SECTION_ORDER)


def _stitle(titles: dict, section_id: str, default_title: str) -> str:
    """Heading override for ``section_id`` from ``section_titles``, else default."""
    override = (titles or {}).get(section_id)
    if override is not None and str(override).strip():
        return str(override).strip()
    return default_title


def render_html(cv: CVData, template_id: str = "modern") -> str:
    """Render ``cv`` to a full HTML document using the universal template.

    The legacy ``template_id`` (modern/classic/minimal/awesome-cv/deedy-resume/
    cvresume/universal-resume/newfuture-cv plus gallery ids) resolves to a named
    preset of visual toggles (``TemplateConfig``); an explicit
    ``cv.template_config`` set by the UI toggles overrides the preset. User
    content is always escaped (Jinja autoescape for the universal template,
    ``html.escape`` for the fallback). Section ordering honors
    ``cv.section_order`` (default order when empty) and headings honor
    ``cv.section_titles`` overrides via the ``stitle`` template global.
    """
    template_id = (template_id or "").strip() or "modern"
    tc = resolve_template_config(cv, template_id)
    context = {
        "cv": cv.model_dump(),
        "accent": str(cv.accent or "#2563eb"),
        "template_id": template_id,
        "section_order": _section_order_of(cv),
        "section_titles": dict(cv.section_titles or {}),
        "stitle": lambda sid, default: _stitle(cv.section_titles, sid, default),
        "tc": tc.model_dump(),
    }
    # Known presets (and user-customized configs) render through the universal
    # template; unknown ids fall through to the builtin fallback renderer.
    use_universal = template_id in PRESET_IDS or cv.template_config is not None
    if use_universal:
        try:
            template = _env.get_template("universal.html.j2")
            return template.render(**context)
        except TemplateNotFound:
            pass
    return _render_fallback(context)


# ---------------------------------------------------------------------------
# Builtin fallback (single-column modern) renderer
# ---------------------------------------------------------------------------

_FALLBACK_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
       font-size: 10.5pt; line-height: 1.45; color: #1f2937; }
.resume { max-width: 720px; margin: 0 auto; padding: 36px 48px; }
header { text-align: center; margin-bottom: 18px; }
.name { font-size: 24pt; font-weight: 700; color: {{ accent }}; letter-spacing: 0.5px; }
.title { font-size: 12.5pt; color: #374151; margin-top: 2px; }
.contact { font-size: 9.5pt; color: #4b5563; margin-top: 6px; }
section { margin-top: 16px; }
h2 { font-size: 11.5pt; text-transform: uppercase; letter-spacing: 1.5px;
     color: {{ accent }}; border-bottom: 1px solid #e5e7eb; padding-bottom: 3px;
     margin-bottom: 8px; }
.item { margin: 8px 0 0; }
.item-head { font-weight: 600; }
.item-meta { font-size: 9.5pt; color: #6b7280; }
ul { margin: 4px 0 0 16px; }
li { margin: 2px 0; }
.skills-line { margin: 3px 0; }
"""


def _fallback_section(cv: dict, title: str, body: str) -> str:
    if not body:
        return ""
    return f"<section><h2>{_escape(title)}</h2>{body}</section>"


def _fallback_bullets(bullets: list) -> str:
    items = "".join(f"<li>{_escape(b)}</li>" for b in bullets if str(b or "").strip())
    return f"<ul>{items}</ul>" if items else ""


def _render_fallback(context: dict) -> str:
    """Builtin single-column modern HTML used when the template file is absent.

    Honors ``section_order`` / ``section_titles`` from the render context;
    with defaults the output is identical to the historical fixed order.
    """
    cv = context["cv"]
    # Fallback is not Jinja, so the accent must be escaped exactly once here.
    accent = _escape(str(context.get("accent") or "#2563eb"))
    titles = context.get("section_titles") or {}
    order = context.get("section_order") or list(DEFAULT_SECTION_ORDER)
    p = cv.get("personal") or {}

    parts: list[str] = ["<!DOCTYPE html>", '<html lang="en">', "<head>",
                        '<meta charset="utf-8">', f"<title>{_escape(p.get('name') or 'Resume')}</title>",
                        "<style>", _FALLBACK_CSS.replace("{{ accent }}", accent or "#2563eb"), "</style>",
                        "</head>", "<body>", '<div class="resume">']

    header_bits: list[str] = []
    if p.get("name"):
        header_bits.append(f'<div class="name">{_escape(p["name"])}</div>')
    if p.get("title"):
        header_bits.append(f'<div class="title">{_escape(p["title"])}</div>')
    contact = "  |  ".join(
        str(x)
        for x in [
            p.get("email"), p.get("phone"), p.get("location"),
            p.get("website"), p.get("linkedin"), p.get("github"),
        ]
        if str(x or "").strip()
    )
    if contact:
        header_bits.append(f'<div class="contact">{_escape(contact)}</div>')
    if header_bits:
        parts.append("<header>" + "".join(header_bits) + "</header>")

    # Section bodies keyed by section id, in the historical default order.
    bodies: dict[str, str] = {}

    if cv.get("summary"):
        bodies["summary"] = _fallback_section(
            cv, _stitle(titles, "summary", "Summary"), f"<p>{_escape(cv['summary'])}</p>"
        )

    exp_body: list[str] = []
    for item in cv.get("experience") or []:
        head = " - ".join(x for x in (item.get("role"), item.get("company")) if str(x or "").strip())
        d = item.get("dates") or {}
        meta = " | ".join(
            x for x in (item.get("location"), _fmt_dates(d.get("start", ""), d.get("end", "")))
            if str(x or "").strip()
        )
        if head:
            exp_body.append(f'<div class="item-head">{_escape(head)}</div>')
        if meta:
            exp_body.append(f'<div class="item-meta">{_escape(meta)}</div>')
        exp_body.append(_fallback_bullets(item.get("bullets") or []))
    bodies["experience"] = _fallback_section(cv, _stitle(titles, "experience", "Experience"), "".join(exp_body))

    edu_body: list[str] = []
    for item in cv.get("education") or []:
        d = item.get("dates") or {}
        head = " ".join(
            x for x in (item.get("degree"), item.get("field")) if str(x or "").strip()
        ) or str(item.get("institution") or "")
        meta = " | ".join(
            x for x in (
                "" if head == item.get("institution") else item.get("institution"),
                item.get("gpa"),
                _fmt_dates(d.get("start", ""), d.get("end", "")),
            )
            if str(x or "").strip()
        )
        if head:
            edu_body.append(f'<div class="item-head">{_escape(head)}</div>')
        if meta:
            edu_body.append(f'<div class="item-meta">{_escape(meta)}</div>')
    bodies["education"] = _fallback_section(cv, _stitle(titles, "education", "Education"), "".join(edu_body))

    skills_body: list[str] = []
    for group in cv.get("skills") or []:
        label = str(group.get("category") or "")
        joined = ", ".join(str(s) for s in group.get("skills") or [] if str(s or "").strip())
        line = f"{label}: {joined}" if label else joined
        if str(line or "").strip():
            skills_body.append(f'<p class="skills-line">{_escape(line)}</p>')
    bodies["skills"] = _fallback_section(cv, _stitle(titles, "skills", "Skills"), "".join(skills_body))

    proj_body: list[str] = []
    for project in cv.get("projects") or []:
        head = project.get("name") or ""
        if project.get("link"):
            head = f"{head} ({project['link']})" if head else str(project["link"])
        if head:
            proj_body.append(f'<div class="item-head">{_escape(head)}</div>')
        if project.get("description"):
            proj_body.append(f'<p>{_escape(project["description"])}</p>')
        proj_body.append(_fallback_bullets(project.get("bullets") or []))
    bodies["projects"] = _fallback_section(cv, _stitle(titles, "projects", "Projects"), "".join(proj_body))

    cert_body: list[str] = []
    for cert in cv.get("certifications") or []:
        suffix = " | ".join(x for x in (cert.get("issuer"), cert.get("year")) if str(x or "").strip())
        line = cert.get("name") or ""
        if line and suffix:
            line = f"{line} ({suffix})"
        if line:
            cert_body.append(f'<p>{_escape(line)}</p>')
    bodies["certifications"] = _fallback_section(cv, _stitle(titles, "certifications", "Certifications"), "".join(cert_body))

    lang_body: list[str] = []
    for lang in cv.get("languages") or []:
        line = lang.get("name") or ""
        if lang.get("level"):
            line = f"{line} ({lang['level']})" if line else str(lang["level"])
        if line:
            lang_body.append(f'<p>{_escape(line)}</p>')
    bodies["languages"] = _fallback_section(cv, _stitle(titles, "languages", "Languages"), "".join(lang_body))

    custom_html: list[str] = []
    for section in cv.get("custom_sections") or []:
        title = str(section.get("title") or "").strip()
        bullets_html = _fallback_bullets(section.get("bullets") or [])
        if title and bullets_html:
            body = f'<div class="item-head">{_escape(title)}</div>' + bullets_html
            heading = title
        elif title:
            # Heading-only section: the section heading carries the title.
            body, heading = "", title
        elif bullets_html:
            body, heading = bullets_html, "Additional"
        else:
            continue
        custom_html.append(_fallback_section(cv, heading, body))
    if custom_html:
        bodies["custom_sections"] = "\n".join(custom_html)

    emitted: set[str] = set()
    for sid in order:
        if sid in bodies and sid not in emitted:
            parts.append(bodies[sid])
            emitted.add(sid)
    # Anything not covered by the (possibly partial) custom order keeps its
    # default relative position at the end — content is never hidden.
    for sid, body in bodies.items():
        if sid not in emitted:
            parts.append(body)

    parts += ["</div>", "</body>", "</html>"]
    return "\n".join(parts)