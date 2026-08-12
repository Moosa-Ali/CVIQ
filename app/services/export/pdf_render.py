"""Deterministic HTML -> PDF rendering via PyMuPDF's ``fitz.Story``.

Confirmed against PyMuPDF 1.28.2 (installed):

- ``fitz.Story(html=..., user_css=...)`` accepts raw HTML (full documents or
  fragments).
- ``DocumentWriter(buffer)`` writes pages into the supplied ``io.BytesIO``;
  ``writer.close()`` returns ``None`` in 1.28 — read ``buffer.getvalue()``.
- ``story.place(rect)`` returns ``(more, used_rect)``; call ``story.draw(dev)``
  then start a new page while ``more`` is truthy (multi-page overflow).

No AI, no document editing: the input HTML was rendered from the canonical CV
object and this module only paginates it.
"""

import io

import fitz

from ..cv.models import CVData
from .render_html import render_html


def _paper_rect(page_size: str):
    """fitz.Rect within which to lay out the story, or None if unknown."""
    name = (page_size or "letter").strip().lower()
    try:
        return fitz.paper_rect(name)
    except Exception:
        pass
    # Fall back to a letter-size rect rather than failing outright.
    return fitz.Rect(0, 0, 612, 792)


def html_to_pdf(html: str, page_size: str = "letter", margins_pt=(36, 36, 36, 36)) -> bytes:
    """Paginate ``html`` into PDF bytes (multi-page when content overflows).

    Margins are ``(left, top, right, bottom)`` in points and shrink the content
    area inside the paper rect. Deterministic: same input HTML always yields
    the same PDF.
    """
    left, top, right, bottom = margins_pt or (36, 36, 36, 36)
    mediabox = _paper_rect(page_size)
    if not mediabox or mediabox.is_empty:
        mediabox = fitz.Rect(0, 0, 612, 792)
    content_rect = fitz.Rect(
        mediabox.x0 + left,
        mediabox.y0 + top,
        mediabox.x1 - right,
        mediabox.y1 - bottom,
    )

    buffer = io.BytesIO()
    writer = fitz.DocumentWriter(buffer)
    story = fitz.Story(
        html=html or "",
        user_css="@page{margin:0} html,body{margin:0;padding:0}",
    )
    try:
        while True:
            dev = writer.begin_page(mediabox)
            result = story.place(content_rect)
            # 1.28 returns (more, used_rect); be defensive about other shapes.
            if isinstance(result, (tuple, list)):
                more = bool(result[0]) if result else False
            else:
                more = bool(result)
            story.draw(dev)
            writer.end_page()
            if not more:
                break
    finally:
        writer.close()
    out = buffer.getvalue()
    # Defensive: some builds return bytes from close(); prefer buffer contents.
    return out or b""


def render_pdf(cv: CVData, template_id: str = "modern", page_size: str = "letter") -> bytes:
    """Canonical CV -> PDF bytes (render_html + html_to_pdf)."""
    document = render_html(cv, template_id)
    return html_to_pdf(document, page_size=page_size)