import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from ..services import templates as template_service
from ..services.cv.models import CVData
from ..services.export.docx_export import export_docx
from ..services.export.pdf_export import export_pdf
from ..services.export.render_html import render_html

logger = logging.getLogger("cviq")

router = APIRouter(prefix="/api/export", tags=["export"])

_MIME_BY_KIND = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class ExportRequest(BaseModel):
    cv: CVData
    template: str = ""


class PreviewRequest(BaseModel):
    cv: CVData
    template: str = ""


def _resolve_template(req_template: str, cv_template: str) -> str:
    """Resolve a catalog id (builtin render template OR gallery id) to a
    render-template id, defaulting to the CV's own template."""
    return template_service.resolve_render_template(req_template, cv_template)


@router.post("/preview")
def export_preview(req: PreviewRequest):
    """Server-rendered HTML preview of the CV in the requested template.

    Resolves ``template`` as a render-template id or a gallery id (via
    ``render_template_for``); defaults to ``cv.template``. Returns the full
    standalone HTML document with ``Cache-Control: no-store``.
    """
    template = _resolve_template(req.template, req.cv.template)
    html = render_html(req.cv, template)
    return JSONResponse(
        content={"html": html},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/pdf")
def export_pdf_endpoint(req: ExportRequest):
    try:
        template = _resolve_template(req.template, req.cv.template)
        data = export_pdf(req.cv, template=template)
    except Exception as exc:
        logger.exception("PDF export failed")
        raise HTTPException(status_code=500, detail=f"PDF export failed: {exc}")
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="resume.pdf"'},
    )


@router.post("/docx")
def export_docx_endpoint(req: ExportRequest):
    try:
        template = _resolve_template(req.template, req.cv.template)
        data = export_docx(req.cv, template=template)
    except Exception as exc:
        logger.exception("DOCX export failed")
        raise HTTPException(status_code=500, detail=f"DOCX export failed: {exc}")
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="resume.docx"'},
    )
