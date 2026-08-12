"""Markdown rendering of CV documents for LLM prompts.

The AI pipeline consumes a Markdown representation of the CV's structure rather
than raw text. Two renderers exist:

- :func:`structure_to_markdown` — layout-aware rendering from the PDF structure
  dict (blocks with fonts/sizes/positions) or the DOCX structure dict
  (paragraphs with Word styles / run formatting). Section headings are detected
  with the same heuristics as the parser, and an ``OBSERVED LAYOUT`` subsection
  tells the model the real reading order and real heading names.
- :func:`cv_to_markdown` — fallback rendering from a parsed :class:`CVData`
  object when no structure is available.
"""

from collections import Counter

from .models import CVData
from .parser import _SECTION_ORDER, _classify_section, _looks_like_pdf_heading

_BULLET_CHARS = ("-", "•", "*")


def _is_section_heading(line: str, block: dict, body_size: float) -> bool:
    """A line is a section heading if it matches a known alias or the font/size
    heuristic (same classifier the PDF parser uses)."""
    if _classify_section(line):
        return True
    return _looks_like_pdf_heading(line, block, body_size)


def _looks_like_name(line: str) -> bool:
    """Heuristic: a short, mostly-capitalized line likely to be the candidate name."""
    words = line.split()
    if not 1 <= len(words) <= 4:
        return False
    if len(line) > 40 or line.endswith((".", "!", "?")):
        return False
    capitalized = sum(1 for w in words if w and w[0].isupper())
    return capitalized >= len(words) * 0.6


def _looks_like_bullet(line: str) -> bool:
    """Bullet-like lines: explicit bullet chars or short fragments without
    sentence-ending punctuation."""
    if line.startswith(_BULLET_CHARS):
        return True
    if len(line) <= 60 and not line.endswith((".", "!", "?")):
        words = line.split()
        if 1 <= len(words) <= 8:
            return True
    return False


def _docx_headings(paragraph: dict) -> bool:
    """Section-heading detection for a DOCX paragraph dict.

    A paragraph is a section heading when:
    (a) ``_classify_section`` matches its text (canonical alias), OR
    (b) its Word style name starts with "Heading" (case-insensitive), OR
    (c) it is a short line (<= 40 chars) with no trailing period and at least one
        bold run (the bold/visual-weight heuristic).
    """
    text = (paragraph.get("text") or "").strip()
    if not text:
        return False
    if _classify_section(text):
        return True
    style = (paragraph.get("style") or "").strip()
    if style.lower().startswith("heading"):
        return True
    runs = paragraph.get("runs") or []
    has_bold = any(bool(r.get("bold")) for r in runs)
    return len(text) <= 40 and not text.endswith(".") and has_bold


def _structure_to_markdown_docx(structure: dict, blocks_max_chars: int = 14000) -> str:
    """Build a Markdown CV document from a DOCX structure dict.

    ``structure`` is ``{"kind": "docx", "paragraphs": [...], "tables": [...],
    "headers_text": [...], "footers_text": [...], "sections": [...]}`` where each
    paragraph is ``{"text", "style", "alignment", "runs"}`` (runs carry
    ``bold``/``italic``/``underline``/``size_pt``).

    Rendering:
    - ``# Name`` for the first non-heading line if it looks like a name,
    - ``## <Heading>`` for detected section headings (via :func:`_docx_headings`),
    - ``- `` for paragraphs whose style contains "List" or that start with a
      bullet glyph (``-`` ``•`` ``*`` ``–``),
    - tables as ``| cell | cell |`` rows (one per row),
    - plain paragraphs otherwise.

    After the CV body an ``## OBSERVED LAYOUT`` subsection lists the detected
    headings in actual reading order (``1. <heading> (paragraph N)``) plus a
    one-line note of standard sections not detected and the paragraph count. The
    body is truncated to ``blocks_max_chars`` (keeping the layout section intact)
    with a ``… [truncated]`` suffix.
    """
    paragraphs = structure.get("paragraphs", []) or []
    tables = structure.get("tables", []) or []
    if not paragraphs:
        return ""

    headings: list[tuple[str, int]] = []  # (heading text, 1-based paragraph index)
    out: list[str] = []
    name_done = False
    for idx, paragraph in enumerate(paragraphs, start=1):
        line = (paragraph.get("text", "") or "").strip()
        if not line:
            continue
        if _docx_headings(paragraph):
            headings.append((line, idx))
            if out and out[-1] != "":
                out.append("")
            out.append(f"## {line}")
            continue
        if not name_done:
            name_done = True
            out.append(f"# {line}" if _looks_like_name(line) else line)
            continue
        style = (paragraph.get("style", "") or "").strip()
        if "list" in style.lower() or line.startswith(("-", "•", "*", "–")):
            out.append(f"- {line.lstrip('-•*– ').strip()}")
        else:
            out.append(line)

    for table in tables:
        for row in table.get("rows", []) or []:
            cells = " | ".join((cell or "").strip() for cell in row if (cell or "").strip())
            if cells:
                out.append(f"| {cells} |")

    body_text = "\n".join(out).strip()
    layout_lines = [
        f"{i + 1}. {text} (paragraph {idx})"
        for i, (text, idx) in enumerate(headings)
    ]
    detected = {c for c in (_classify_section(h) for h, _ in headings) if c}
    missing = [s for s in _SECTION_ORDER if s not in detected]
    note = f"Detected {len(headings)} section heading(s) across {len(paragraphs)} paragraph(s)."
    if missing:
        note += f" Standard sections not detected: {', '.join(missing)}."
    layout_section = "\n\n## OBSERVED LAYOUT\n" + "\n".join(layout_lines) + "\n\n" + note
    if len(body_text) > blocks_max_chars:
        body_text = body_text[:blocks_max_chars].rstrip() + "\n\n… [truncated]"
    return body_text + layout_section


def structure_to_markdown(structure: dict, blocks_max_chars: int = 14000) -> str:
    """Build a Markdown CV document from a layout structure dict.

    Dispatches on ``structure["kind"]``:
    - ``"pdf"`` → layout-block rendering (fonts/sizes/positions; ``OBSERVED
      LAYOUT`` with page/y references),
    - ``"docx"`` → paragraph/table rendering (Word styles + run formatting;
      ``OBSERVED LAYOUT`` with paragraph references),
    - anything else → ``""``.

    Both renderers append an ``## OBSERVED LAYOUT`` subsection and keep it intact
    when the body is truncated to ``blocks_max_chars``.
    """
    kind = structure.get("kind")
    if kind == "pdf":
        return _structure_to_markdown_pdf(structure, blocks_max_chars)
    if kind == "docx":
        return _structure_to_markdown_docx(structure, blocks_max_chars)
    return ""


def _structure_to_markdown_pdf(structure: dict, blocks_max_chars: int = 14000) -> str:
    """Build a Markdown CV document from a PDF structure dict.

    ``structure`` is ``{"kind": "pdf", "blocks": [...], "page_count": n}`` where
    each block is a line dict with ``page``/``text``/``x0``/``y0``/``size``/
    ``font``/``bold``. Blocks are sorted in reading order (``page``, then ``y0``).

    Rendering:
    - ``# Name`` for the first non-heading line if it looks like a name,
    - ``## <Heading>`` for detected section headings,
    - ``- `` for bullet-like lines,
    - plain paragraphs otherwise.

    After the CV body an ``## OBSERVED LAYOUT`` subsection lists the detected
    headings in actual reading order (``1. <heading> (page N, y≈Y)``) plus a
    one-line note of standard sections not detected and the page count. The body
    is truncated to ``blocks_max_chars`` (keeping the layout section intact) with
    a ``… [truncated]`` suffix.
    """
    blocks = structure.get("blocks", []) or []
    page_count = structure.get("page_count", 0)
    if not blocks:
        return ""
    ordered = sorted(blocks, key=lambda b: (b.get("page", 0), b.get("y0", 0.0)))
    sizes = [b.get("size", 0.0) for b in ordered if b.get("size", 0.0) > 0]
    body_size = Counter(sizes).most_common(1)[0][0] if sizes else 0.0

    headings: list[tuple[str, int, float]] = []  # (heading text, page, y0)
    out: list[str] = []
    name_done = False
    for block in ordered:
        line = (block.get("text", "") or "").strip()
        if not line:
            continue
        page = block.get("page", 0)
        y0 = block.get("y0", 0.0)
        if _is_section_heading(line, block, body_size):
            headings.append((line, page, y0))
            if out and out[-1] != "":
                out.append("")
            out.append(f"## {line}")
            continue
        if not name_done:
            name_done = True
            out.append(f"# {line}" if _looks_like_name(line) else line)
            continue
        if _looks_like_bullet(line):
            out.append(f"- {line.lstrip(''.join(_BULLET_CHARS) + ' ').strip()}")
        else:
            out.append(line)

    body_text = "\n".join(out).strip()
    layout_lines = [
        f"{i + 1}. {text} (page {page}, y≈{y0:.0f})"
        for i, (text, page, y0) in enumerate(headings)
    ]
    detected = {c for c in (_classify_section(h) for h, _, _ in headings) if c}
    missing = [s for s in _SECTION_ORDER if s not in detected]
    note = f"Detected {len(headings)} section heading(s) across {page_count} page(s)."
    if missing:
        note += f" Standard sections not detected: {', '.join(missing)}."
    layout_section = "\n\n## OBSERVED LAYOUT\n" + "\n".join(layout_lines) + "\n\n" + note
    if len(body_text) > blocks_max_chars:
        body_text = body_text[:blocks_max_chars].rstrip() + "\n\n… [truncated]"
    return body_text + layout_section


def _contact_line(cv: CVData) -> str:
    personal = cv.personal
    parts = [
        personal.email,
        personal.phone,
        personal.location,
        personal.website,
        personal.linkedin,
        personal.github,
    ]
    return " | ".join(part for part in parts if part)


def cv_to_markdown(cv: CVData) -> str:
    """Fallback Markdown rendering of a parsed CVData (no layout structure).

    Mirrors ``writer.cv_to_text`` but emits ``## HEADING`` and ``- bullet``
    Markdown so the LLM sees the same shape whether or not structure is present.
    """
    lines: list[str] = []
    personal = cv.personal
    if personal.name:
        lines.append(f"# {personal.name}")
    if personal.title:
        lines.append(personal.title)
    contact = _contact_line(cv)
    if contact:
        lines.append(contact)
    lines.append("")

    if cv.summary:
        lines.append("## SUMMARY")
        lines.append(cv.summary)
        lines.append("")

    if cv.experience:
        lines.append("## EXPERIENCE")
        for item in cv.experience:
            header = item.role if item.role else item.company
            if item.role and item.company:
                header = f"{item.role} at {item.company}"
            lines.append(header)
            meta = []
            if item.company and not item.role:
                meta.append(item.company)
            if item.location:
                meta.append(item.location)
            if item.dates.start or item.dates.end:
                meta.append(f"{item.dates.start} - {item.dates.end}")
            if meta:
                lines.append(" | ".join(meta))
            for bullet in item.bullets:
                lines.append(f"- {bullet}")
        lines.append("")

    if cv.education:
        lines.append("## EDUCATION")
        for item in cv.education:
            name = " ".join(x for x in [item.degree, item.field] if x) or item.institution
            lines.append(name)
            meta = []
            if item.institution and name != item.institution:
                meta.append(item.institution)
            if item.gpa:
                meta.append(f"GPA: {item.gpa}")
            if item.dates.start or item.dates.end:
                meta.append(f"{item.dates.start} - {item.dates.end}")
            if meta:
                lines.append(" | ".join(meta))
        lines.append("")

    if cv.skills:
        lines.append("## SKILLS")
        for group in cv.skills:
            label = f"{group.category}: " if group.category else ""
            lines.append(label + ", ".join(group.skills) if group.skills else label.strip())
        lines.append("")

    if cv.projects:
        lines.append("## PROJECTS")
        for project in cv.projects:
            lines.append(project.name + (f" ({project.link})" if project.link else ""))
            if project.description:
                lines.append(project.description)
            for bullet in project.bullets:
                lines.append(f"- {bullet}")
        lines.append("")

    if cv.certifications:
        lines.append("## CERTIFICATIONS")
        for cert in cv.certifications:
            line = cert.name
            suffix = " | ".join(x for x in [cert.issuer, cert.year] if x)
            if suffix:
                line = f"{line} ({suffix})"
            lines.append(line)
        lines.append("")

    if cv.languages:
        lines.append("## LANGUAGES")
        for language in cv.languages:
            line = language.name
            if language.level:
                line = f"{line} ({language.level})"
            lines.append(line)
        lines.append("")

    return "\n".join(lines).strip()


def document_markdown(cv, text: str, structure: dict | None) -> str:
    """Dispatch to the best available Markdown rendering of the CV.

    Uses the layout-aware :func:`structure_to_markdown` when a PDF structure with
    blocks OR a DOCX structure with paragraphs is available; otherwise falls back
    to :func:`cv_to_markdown`. ``text`` is accepted for interface symmetry (the
    raw text is not needed by either renderer).
    """
    if structure:
        kind = structure.get("kind")
        has_blocks = kind == "pdf" and structure.get("blocks")
        has_paragraphs = kind == "docx" and structure.get("paragraphs")
        if has_blocks or has_paragraphs:
            md = structure_to_markdown(structure)
            if md.strip():
                return md
    return cv_to_markdown(cv)