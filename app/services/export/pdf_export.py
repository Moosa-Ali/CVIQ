import html
import io
import logging

from ..cv.models import CVData
from .pdf_render import render_pdf

logger = logging.getLogger("cviq")


def _esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def _hex_color(hex_str: str):
    from reportlab.lib.colors import HexColor

    try:
        return HexColor((hex_str or "#2563eb"))
    except Exception:
        return HexColor("#2563eb")


def export_pdf(cv: CVData, template: str = "", page_size: str = "letter") -> bytes:
    """Canonical CV -> final PDF bytes (deterministic, no AI).

    Primary path renders Jinja/fallback HTML and paginates it with
    PyMuPDF ``fitz.Story`` (``pdf_render.render_pdf``). If the Story pipeline
    fails for any reason, we fall back to the proven reportlab renderer
    (``export_pdf_legacy``) so PDF export never breaks.
    """
    template_id = (template or "").strip() or cv.template or "modern"
    try:
        return render_pdf(cv, template_id=template_id, page_size=page_size)
    except Exception as exc:
        logger.warning("fitz.Story PDF rendering failed (%s); using legacy reportlab renderer", exc)
        return export_pdf_legacy(cv)


def export_pdf_legacy(cv: CVData) -> bytes:
    """Deterministic reportlab JSON->PDF renderer (legacy fallback).
    """
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

    accent = _hex_color(cv.accent)

    name_style = ParagraphStyle(
        "name", fontName="Helvetica-Bold", fontSize=22, leading=26, textColor=accent, alignment=TA_CENTER, spaceAfter=2
    )
    title_style = ParagraphStyle("title", fontName="Helvetica", fontSize=13, leading=16, alignment=TA_CENTER, spaceAfter=2)
    contact_style = ParagraphStyle("contact", fontName="Helvetica", fontSize=10, leading=13, alignment=TA_CENTER, spaceAfter=6)
    heading_style = ParagraphStyle("heading", fontName="Helvetica-Bold", fontSize=13, leading=16, textColor=accent, spaceBefore=10, spaceAfter=4)
    body_style = ParagraphStyle("body", fontName="Helvetica", fontSize=10.5, leading=14, spaceAfter=3)
    bold_style = ParagraphStyle("bold", fontName="Helvetica-Bold", fontSize=11, leading=14, spaceBefore=4)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, leftMargin=0.5 * inch, rightMargin=0.5 * inch, topMargin=0.5 * inch, bottomMargin=0.5 * inch)
    story = []

    personal = cv.personal
    if personal.name:
        story.append(Paragraph(_esc(personal.name), name_style))
    if personal.title:
        story.append(Paragraph(_esc(personal.title), title_style))
    contact = "  |  ".join(
        part
        for part in [personal.email, personal.phone, personal.location, personal.website, personal.linkedin, personal.github]
        if part
    )
    if contact:
        story.append(Paragraph(_esc(contact), contact_style))

    def heading(text: str):
        story.append(Paragraph(_esc(text.upper()), heading_style))

    if cv.summary:
        heading("Summary")
        story.append(Paragraph(_esc(cv.summary), body_style))

    if cv.experience:
        heading("Experience")
        for item in cv.experience:
            header = (item.role + " - " + item.company) if (item.role and item.company) else (item.role or item.company)
            if header:
                story.append(Paragraph(_esc(header), bold_style))
            meta = " | ".join(
                part
                for part in [item.location, f"{item.dates.start} - {item.dates.end}" if (item.dates.start or item.dates.end) else ""]
                if part
            )
            if meta:
                story.append(Paragraph(_esc(meta), body_style))
            if item.bullets:
                story.append(ListFlowable([ListItem(Paragraph(_esc(b), body_style), leftIndent=8) for b in item.bullets], bulletType="bullet"))

    if cv.education:
        heading("Education")
        for item in cv.education:
            header = " ".join(x for x in [item.degree, item.field] if x) or item.institution
            if header:
                story.append(Paragraph(_esc(header), bold_style))
            meta = " | ".join(
                part
                for part in [
                    item.institution if header != item.institution else "",
                    item.gpa,
                    f"{item.dates.start} - {item.dates.end}" if (item.dates.start or item.dates.end) else "",
                ]
                if part
            )
            if meta:
                story.append(Paragraph(_esc(meta), body_style))

    if cv.skills:
        heading("Skills")
        for group in cv.skills:
            label = f"{group.category}: " if group.category else ""
            story.append(Paragraph(_esc(label + ", ".join(group.skills)), body_style))

    if cv.projects:
        heading("Projects")
        for project in cv.projects:
            header = project.name + (f" ({project.link})" if project.link else "")
            if header:
                story.append(Paragraph(_esc(header), bold_style))
            if project.description:
                story.append(Paragraph(_esc(project.description), body_style))
            if project.bullets:
                story.append(ListFlowable([ListItem(Paragraph(_esc(b), body_style), leftIndent=8) for b in project.bullets], bulletType="bullet"))

    if cv.certifications:
        heading("Certifications")
        for cert in cv.certifications:
            suffix = " | ".join(x for x in [cert.issuer, cert.year] if x)
            story.append(Paragraph(_esc(cert.name + (f" ({suffix})" if suffix else "")), body_style))

    if cv.languages:
        heading("Languages")
        for lang in cv.languages:
            story.append(Paragraph(_esc(lang.name + (f" ({lang.level})" if lang.level else "")), body_style))

    doc.build(story)
    return buffer.getvalue()
