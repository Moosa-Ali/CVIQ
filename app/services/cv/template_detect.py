"""Heuristic template + accent detection for parsed CVs.

Given only what the parser extracted (normalized CVData + raw text + optionally the
original file bytes), pick the closest HTML render template and an accent so a
re-render resembles the candidate's original layout. This is intentionally a
heuristic — it does not need to be perfect, just better than always-'modern'.

The returned template is ALWAYS one of the ids in ``RENDER_TEMPLATES``
(``app/templates_html``); anything unknown is clamped to ``"modern"``.
"""

import re

from ..templates import RENDER_TEMPLATES
from .models import CVData

_TEMPLATE_DEFAULTS = {
    "modern": "#2563eb",
    "classic": "#111827",
    "minimal": "#6b7280",
}

_HEX_RE = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")

_SECTION_HEADINGS = {
    "summary", "profile", "professional summary", "objective", "about",
    "experience", "work experience", "employment", "work history", "career history",
    "education", "academic background",
    "skills", "technical skills", "core competencies", "competencies", "technologies",
    "projects", "project experience", "select projects",
    "certifications", "certificates", "licenses",
    "languages", "language",
    "contact", "personal details", "contact information",
}


def _heading_lines(text: str):
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        norm = line.rstrip(":").lower().strip()
        if norm in _SECTION_HEADINGS:
            yield line


def _uppercase_heading_count(text: str) -> int:
    return sum(1 for line in _heading_lines(text) if line.isupper())


def _title_case_heading_count(text: str) -> int:
    count = 0
    for line in _heading_lines(text):
        if line.isupper():
            continue
        words = line.split()
        if words and all(w[:1].isupper() for w in words if w):
            count += 1
    return count


def _bullet_count(text: str) -> int:
    return sum(1 for line in (text or "").splitlines() if line.strip().startswith(("-", "•", "*")))


def _skill_density(cv: CVData) -> float:
    total = 0
    groups = 0
    for group in cv.skills:
        groups += 1
        total += len(group.skills)
    return total / groups if groups else 0.0


def _section_count(cv: CVData) -> int:
    count = 0
    if cv.summary:
        count += 1
    if cv.experience:
        count += 1
    if cv.education:
        count += 1
    if cv.skills:
        count += 1
    if cv.projects:
        count += 1
    if cv.certifications:
        count += 1
    if cv.languages:
        count += 1
    return count


def _detect_accent(text: str, template: str) -> str:
    for match in _HEX_RE.findall(text or ""):
        return "#" + match.lower()
    return _TEMPLATE_DEFAULTS[template]


def detect_template(
    cv: CVData,
    text: str,
    original_file_bytes=None,
    filename: str = "",
) -> tuple[str, str]:
    """Return (template_id, accent_hex) for the parsed CV."""
    text = text or ""
    score = {"modern": 0, "classic": 0, "minimal": 0}

    # Many UPPERCASE section headings + dense comma-separated skill lists -> modern.
    up_headings = _uppercase_heading_count(text)
    if up_headings >= 2:
        score["modern"] += 2
    elif up_headings == 1:
        score["modern"] += 1

    density = _skill_density(cv)
    if density >= 8:
        score["modern"] += 2
    elif density >= 4:
        score["modern"] += 1

    # '|' separators and longer prose, or title-case headings -> classic.
    pipe_count = text.count("|")
    if pipe_count >= 3:
        score["classic"] += 2
    elif pipe_count >= 1:
        score["classic"] += 1

    title_headings = _title_case_heading_count(text)
    if title_headings >= 2:
        score["classic"] += 2
    elif title_headings == 1:
        score["classic"] += 1

    # Sparse, minimal bullets and few sections -> minimal.
    bullets = _bullet_count(text)
    if bullets == 0:
        score["minimal"] += 2
    elif bullets <= 3:
        score["minimal"] += 1

    if _section_count(cv) <= 2:
        score["minimal"] += 1

    if all(v == 0 for v in score.values()):
        template = "modern"
    else:
        template = max(score, key=score.get)

    # Clamp: the CV template must always be a real HTML render template id.
    if template not in RENDER_TEMPLATES:
        template = "modern"

    accent = _detect_accent(text, template)
    return template, accent
