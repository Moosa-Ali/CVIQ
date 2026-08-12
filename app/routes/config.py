from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..services.llm import LLMConfig, LLMConfigError, LLMError, get_client
from ..services.llm.base import SECRET_FIELDS
from ..services.llm import config_store
from ..templates import DEFAULTS, TEMPLATES, _ACCENTS

router = APIRouter(prefix="/api", tags=["config"])


class TestResponse(BaseModel):
    ok: bool
    message: str
    model: str = ""


@router.get("/health")
def health():
    """Liveness probe — no LLM involvement."""
    return {"status": "ok"}


@router.get("/config")
def read_config():
    cfg = config_store.load_config(settings.data_dir)
    data = cfg.redacted().model_dump()
    data["configured"] = cfg.configured()
    data["configured_provider"] = cfg.provider
    return data


@router.post("/config")
def write_config(cfg: LLMConfig):
    existing = config_store.load_config(settings.data_dir).model_dump()
    incoming = cfg.model_dump()
    for field in SECRET_FIELDS:
        if incoming.get(field) in (None, "", "***"):
            incoming[field] = existing.get(field, "")
    config_store.save_config(LLMConfig(**incoming), settings.data_dir)
    return {"ok": True}


@router.post("/config/test", response_model=TestResponse)
def test_config(cfg: LLMConfig):
    existing = config_store.load_config(settings.data_dir).model_dump()
    incoming = cfg.model_dump()
    for field in SECRET_FIELDS:
        if incoming.get(field) in (None, "", "***"):
            incoming[field] = existing.get(field, "")
    cfg = LLMConfig(**incoming)
    try:
        client = get_client(cfg)
        client.test()
    except LLMConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return TestResponse(ok=True, message=f"Connection to {client.provider_name} successful", model=getattr(client, "model", ""))


@router.get("/meta")
def meta():
    return {"templates": TEMPLATES, "accents": _ACCENTS, "defaults": DEFAULTS}
