"""CV template gallery API (B3): list, previews, render-final exports.

Exports are rendered deterministically from the canonical CV JSON through the
HTML/CSS template layer — gallery PDFs are used only for previews (design
inspiration) and are never edited, overlaid or filled.

``GET /api/templates`` returns the UNIFIED catalog: the builtin render templates
(modern/classic/minimal, ``source: "builtin"``) first, then every gallery design
(``source: "gallery"``) with its existing metadata keys unchanged.
"""

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from ..services import templates as template_service
from ..services.cv.models import CVData
from ..services.export.docx_export import export_docx
from ..services.export.pdf_render import render_pdf
from ..templates import TEMPLATES

logger = logging.getLogger("cviq")

router = APIRouter(prefix="/api/templates", tags=["templates"])


class FillExportRequest(BaseModel):
    cv: CVData


@router.get("")
def list_templates():
    """Unified template catalog: builtin render templates first, then gallery."""
    builtin = [
        {
            "id": tpl["id"],
            "name": tpl["name"],
            "source": "builtin",
            "render_template": tpl["id"],
            "converted": True,
            "pages": 0,
            "preview_url": "",
        }
        for tpl in TEMPLATES
    ]
    gallery = [dict(tpl, source="gallery") for tpl in template_service.scan_templates()]
    return {"templates": builtin + gallery}


@router.get("/{tpl_id}/preview/{page}")
def template_preview_page(tpl_id: str, page: int):
    """Render one template page as a PNG (cached; 1h browser cache)."""
    png = template_service.template_preview(tpl_id, page)
    if png is None:
        raise HTTPException(status_code=404, detail="Template or page not found")
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


def _resolve_render_template(tpl_id: str) -> str:
    meta = template_service.get_template(tpl_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return meta.get("render_template") or "modern"


@router.post("/{tpl_id}/export/docx")
def export_template_docx(tpl_id: str, req: FillExportRequest):
    render_template = _resolve_render_template(tpl_id)
    try:
        data = export_docx(req.cv, template=render_template)
    except Exception as exc:
        logger.exception("Template DOCX export failed")
        raise HTTPException(status_code=500, detail=f"Template export failed: {exc}")
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{tpl_id}_cv.docx"'},
    )


@router.post("/{tpl_id}/export/pdf")
def export_template_pdf(tpl_id: str, req: FillExportRequest):
    render_template = _resolve_render_template(tpl_id)
    try:
        data = render_pdf(req.cv, template_id=render_template)
    except Exception as exc:
        logger.exception("Template PDF export failed")
        raise HTTPException(status_code=500, detail=f"Template export failed: {exc}")
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{tpl_id}_cv.pdf"'},
    )
