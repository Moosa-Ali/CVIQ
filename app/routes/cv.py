import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..config import settings
from ..services.cv import analyzer, assist, gaps, library, parser, session, tailor, template_detect, validate
from ..services.cv.classify import classify_to_schema, heuristic_flags, merge_flags
from ..services.cv.json_util import cap_vision_images
from ..services.cv.models import CVData, Suggestion
from ..services.export.render_html import render_html
from ..services.llm import LLMClient, LLMConfigError, LLMError
from ..services.llm.base import friendly_llm_error, usage_with_cost

logger = logging.getLogger("cviq")

router = APIRouter(prefix="/api/cv", tags=["cv"])
library_router = APIRouter(prefix="/api/library", tags=["library"])

# Upload / library payload guards.
_MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15 MB
_MAX_LIBRARY_CV_BYTES = 5 * 1024 * 1024  # 5 MB

_SESSION_EXPIRED_WARNING = (
    "Upload session expired — re-upload for best results on scanned PDFs."
)


def get_llm_client() -> LLMClient:
    from ..services.llm import config_store
    from ..services.llm import get_client as _get_client

    cfg = config_store.load_config(settings.data_dir)
    return _get_client(cfg)


def _optional_llm_client() -> LLMClient | None:
    """Configured LLM client or None — used where the LLM is OPTIONAL (parse
    classification, gap analysis). Never raises and never makes network calls
    when credentials are absent (env overrides respected)."""
    from ..services.llm import config_store

    cfg = config_store.effective(config_store.load_config(settings.data_dir))
    if not cfg.configured():
        return None
    try:
        return get_llm_client()
    except (LLMConfigError, LLMError):
        logger.exception("LLM client unavailable; continuing without it")
        return None


_llm_dependency = Depends(get_llm_client)


def _resolve_session(session_id: str) -> tuple[session.SessionEntry | None, str]:
    """Look up an upload session, degrading gracefully when missing/expired.

    Returns ``(entry, warning)``. A missing/expired session is non-fatal: the
    request body is fully self-contained for text flows, and only the vision
    path strictly needs the stored page images. When a ``session_id`` was
    supplied but not found, a structured ``warning`` is returned so the UI can
    surface it instead of a raw 404 or silence.
    """
    if not session_id:
        return None, ""
    try:
        entry = session.get_session(session_id)
    except Exception:
        logger.exception("Session lookup failed; degrading to self-contained request")
        entry = None
    if entry is None:
        return None, _SESSION_EXPIRED_WARNING
    return entry, ""


def _guard_cv_size(cv: CVData) -> None:
    """Reject library CV payloads larger than the per-entry guard (413)."""
    size = len(cv.model_dump_json().encode("utf-8"))
    if size > _MAX_LIBRARY_CV_BYTES:
        raise HTTPException(
            status_code=413,
            detail="CV payload too large — maximum saved CV size is 5 MB.",
        )


def _usage_for(client) -> dict | None:
    """Accumulated token usage + cost for the request's LLM client, or None
    when the client does not track usage (e.g. the test FakeLLM)."""
    if client is None:
        return None
    from ..services.llm import config_store

    cfg = config_store.load_config(settings.data_dir)
    u = usage_with_cost(client, cfg)
    return u.model_dump() if u is not None else None


class AnalyzeRequest(BaseModel):
    cv: CVData
    text: str = ""
    job_description: str = ""
    session_id: str = ""


class SuggestRequest(BaseModel):
    cv: CVData
    text: str = ""
    job_description: str = ""
    session_id: str = ""


class ApplySuggestionsRequest(BaseModel):
    cv: CVData
    suggestions: list[Suggestion]


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    cv: CVData
    messages: list[ChatMessage] = []
    target: dict = {}
    job_description: str = ""
    session_id: str = ""
    # Deprecated: kept accepted for backwards compatibility. When ``messages``
    # is empty and ``segment`` is present, one user message is synthesized from
    # it (``context`` is folded in when provided).
    segment: str = ""
    context: str = ""


class AssistRequest(BaseModel):
    kind: str
    cv: CVData
    job_description: Optional[str] = None
    content: str = ""  # candidate's own draft text for the optimize* kinds


class ValidateRequest(BaseModel):
    cv: CVData


class GapsRequest(BaseModel):
    cv: CVData
    text: str = ""
    job_description: str = ""
    session_id: str = ""


class LibrarySaveRequest(BaseModel):
    name: str
    cv: CVData
    meta: dict = {}


class LibraryUpdateRequest(BaseModel):
    name: Optional[str] = None
    cv: CVData
    meta: dict = {}


@router.post("/parse")
def parse_upload(file: UploadFile):
    # Read at most limit+1 bytes so an oversized upload is rejected without
    # buffering the whole file into memory.
    content = file.file.read(_MAX_UPLOAD_BYTES + 1) if file.file else b""
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="File too large — maximum upload size is 15 MB.",
        )
    if not content:
        raise HTTPException(status_code=400, detail="Empty file uploaded")
    try:
        parsed = parser.parse_file(content, file.filename or "")
        text = parsed["text"]
        cv = parsed["cv"]
        confidence = parsed["confidence"]
        structure = parsed["structure"]
        is_image_pdf = parsed["is_image_pdf"]
        page_images = parsed["page_images"]
        page_count = parsed["page_count"]
        template, accent = template_detect.detect_template(cv, text, content, file.filename or "")
        cv.template = template
        cv.accent = accent
    except ValueError as exc:
        logger.exception("CV parse failed (ValueError)")
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("CV parse failed")
        raise HTTPException(status_code=400, detail=f"Could not parse file: {exc}")
    name = (file.filename or "").lower()
    kind = "pdf" if name.endswith(".pdf") else "docx"

    # LLM classification (optional): upgrades the heuristic CV when configured.
    # Any config/LLM/parse error falls back to the heuristic CV + heuristic flags
    # so the upload NEVER fails because the LLM misbehaved.
    classification = "heuristic"
    flags = heuristic_flags(parsed["cv"], parsed["confidence"])
    client = _optional_llm_client()
    if client is not None:
        try:
            llm_cv, llm_flags = classify_to_schema(
                text,
                structure,
                client,
                images=cap_vision_images(page_images, "parse classification") if is_image_pdf else None,
            )
        except Exception:
            logger.exception("LLM classification failed; using heuristic parse")
            llm_cv, llm_flags = None, []
        if llm_cv is not None:
            # Use the LLM-classified CV, preserving the detected template/accent.
            llm_cv.template = template
            llm_cv.accent = accent
            cv = llm_cv
            flags = merge_flags(flags, llm_flags)
            classification = "llm"

    session_id = session.create_session(
        kind,
        file.filename or "",
        content,
        text,
        structure,
        cv.model_dump(),
        confidence,
        is_image_pdf,
        page_images,
        page_count,
        confidence_flags=[flag.model_dump() for flag in flags],
        classification=classification,
    )
    return {
        "session_id": session_id,
        "cv": cv,
        "text": text,
        "confidence": confidence,
        "page_count": page_count,
        "image_mode": is_image_pdf,
        "confidence_flags": [flag.model_dump() for flag in flags],
        "classification": classification,
        "usage": _usage_for(client),
    }


@router.post("/analyze")
def analyze_cv(req: AnalyzeRequest, client: LLMClient = _llm_dependency):
    entry, warning = _resolve_session(req.session_id)
    try:
        if entry is not None and entry.is_image_pdf:
            cv = CVData.model_validate(entry.cv)
            report = analyzer.analyze(
                cv,
                entry.text,
                req.job_description,
                client,
                images=entry.page_images,
                structure=entry.structure,
            )
        else:
            structure = entry.structure if entry is not None else None
            report = analyzer.analyze(
                req.cv, req.text, req.job_description, client, structure=structure
            )
    except ValueError as exc:
        logger.exception("CV analyze failed (ValueError)")
        raise HTTPException(status_code=400, detail=str(exc))
    except LLMConfigError as exc:
        logger.exception("CV analyze failed (LLMConfigError)")
        raise HTTPException(status_code=400, detail=str(exc))
    except LLMError as exc:
        logger.exception("CV analyze failed (LLMError)")
        raise HTTPException(status_code=502, detail=friendly_llm_error(exc))
    report.session_warning = warning
    result = report.model_dump()
    result["usage"] = _usage_for(client)
    return result


@router.post("/tailor/suggest")
def tailor_suggest(req: SuggestRequest, client: LLMClient = _llm_dependency):
    entry, warning = _resolve_session(req.session_id)
    try:
        if entry is not None and entry.is_image_pdf:
            cv = CVData.model_validate(entry.cv)
            suggestions = tailor.generate_suggestions(
                cv,
                entry.text,
                req.job_description,
                client,
                images=entry.page_images,
                structure=entry.structure,
            )
        else:
            structure = entry.structure if entry is not None else None
            suggestions = tailor.generate_suggestions(
                req.cv, req.text, req.job_description, client, structure=structure
            )
    except ValueError as exc:
        logger.exception("Tailor suggest failed (ValueError)")
        raise HTTPException(status_code=400, detail=str(exc))
    except LLMConfigError as exc:
        logger.exception("Tailor suggest failed (LLMConfigError)")
        raise HTTPException(status_code=400, detail=str(exc))
    except LLMError as exc:
        logger.exception("Tailor suggest failed (LLMError)")
        raise HTTPException(status_code=502, detail=friendly_llm_error(exc))
    return {"suggestions": suggestions, "session_warning": warning, "usage": _usage_for(client)}


@router.post("/tailor/apply")
def tailor_apply(req: ApplySuggestionsRequest):
    new_cv, applied = tailor.apply_suggestions(req.cv, req.suggestions)
    return {"cv": new_cv, "applied": applied}


@router.post("/tailor/chat")
def tailor_chat(req: ChatRequest, client: LLMClient = _llm_dependency):
    entry, warning = _resolve_session(req.session_id)

    messages = list(req.messages or [])
    if not messages and (req.segment or "").strip():
        # Deprecated segment/context path: synthesize one user message.
        content = req.segment.strip()
        if (req.context or "").strip():
            content += "\n\nContext:\n" + req.context.strip()
        messages = [ChatMessage(role="user", content=content)]
    if not messages:
        raise HTTPException(
            status_code=400,
            detail="messages must contain at least one user message",
        )

    images = entry.page_images if (entry is not None and entry.is_image_pdf) else None
    structure = entry.structure if entry is not None else None
    try:
        reply, edits = tailor.chat_assist(
            req.cv,
            req.job_description,
            messages,
            req.target or {},
            client,
            images=images,
            structure=structure,
        )
    except LLMConfigError as exc:
        logger.exception("Tailor chat failed (LLMConfigError)")
        raise HTTPException(status_code=400, detail=str(exc))
    except LLMError as exc:
        logger.exception("Tailor chat failed (LLMError)")
        raise HTTPException(status_code=502, detail=friendly_llm_error(exc))
    return {
        "reply": reply,
        "proposed_edits": [s.model_dump() for s in edits],
        "session_warning": warning,
        "usage": _usage_for(client),
    }


@router.post("/assist")
def cv_assist(req: AssistRequest, client: LLMClient = _llm_dependency):
    try:
        draft = assist.assist(req.kind, req.cv, req.job_description, client, text=req.content)
    except ValueError as exc:
        logger.exception("CV assist failed (ValueError)")
        raise HTTPException(status_code=400, detail=str(exc))
    except LLMConfigError as exc:
        logger.exception("CV assist failed (LLMConfigError)")
        raise HTTPException(status_code=400, detail=str(exc))
    except LLMError as exc:
        logger.exception("CV assist failed (LLMError)")
        raise HTTPException(status_code=502, detail=friendly_llm_error(exc))
    return {"text": draft, "usage": _usage_for(client)}


@router.post("/validate")
def validate_cv(req: ValidateRequest):
    return {"warnings": validate.validate(req.cv)}


@router.post("/gaps")
def cv_gaps(req: GapsRequest):
    """Gap analysis: deterministic checks always run; semantic (LLM) when configured."""
    entry, warning = _resolve_session(req.session_id)
    try:
        structure = entry.structure if entry is not None else None
        client = _optional_llm_client()
        result = gaps.gap_analysis(
            req.cv, req.text, structure, req.job_description, client
        )
    except ValueError as exc:
        logger.exception("CV gaps failed (ValueError)")
        raise HTTPException(status_code=400, detail=str(exc))
    except LLMConfigError as exc:
        logger.exception("CV gaps failed (LLMConfigError)")
        raise HTTPException(status_code=400, detail=str(exc))
    except LLMError as exc:
        logger.exception("CV gaps failed (LLMError)")
        raise HTTPException(status_code=502, detail=friendly_llm_error(exc))
    result["session_warning"] = warning
    result["usage"] = _usage_for(client)
    return result


@library_router.get("")
def list_library():
    return library.list_cvs(settings.data_dir)


@library_router.post("")
def save_library(req: LibrarySaveRequest):
    _guard_cv_size(req.cv)
    cid = library.save(settings.data_dir, req.name, req.cv, meta=req.meta)
    return {"id": cid}


@library_router.put("/{cid}")
def update_library(cid: str, req: LibraryUpdateRequest):
    _guard_cv_size(req.cv)
    record = library.update(settings.data_dir, cid, req.cv, name=req.name, meta=req.meta)
    if record is None:
        raise HTTPException(status_code=404, detail="Library entry not found")
    return record


@library_router.get("/{cid}")
def get_library(cid: str):
    record = library.get(settings.data_dir, cid)
    if not record:
        raise HTTPException(status_code=404, detail="Library entry not found")
    return record


@library_router.get("/{cid}/preview")
def library_preview(cid: str):
    """Server-rendered HTML preview of a saved CV (for the My CVs card).

    Returns the full standalone HTML document (same universal renderer used
    by export), suitable for a scaled-down iframe. ``Cache-Control: no-store``.
    """
    record = library.get(settings.data_dir, cid)
    if not record:
        raise HTTPException(status_code=404, detail="Library entry not found")
    cv = record["cv"]
    html = render_html(cv, cv.template or "modern")
    return JSONResponse(content={"html": html}, headers={"Cache-Control": "no-store"})


@library_router.delete("/{cid}")
def delete_library(cid: str):
    if not library.delete(settings.data_dir, cid):
        raise HTTPException(status_code=404, detail="Library entry not found")
    return {"ok": True}
