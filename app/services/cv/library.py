import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .models import CVData


def _path(data_dir: Path) -> Path:
    return Path(data_dir) / "library.json"


def _read(data_dir: Path) -> dict:
    path = _path(data_dir)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _write(data_dir: Path, data: dict) -> None:
    path = _path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save(data_dir: Path, name: str, cv: CVData, meta: dict | None = None) -> str:
    data = _read(data_dir)
    cid = str(uuid.uuid4())
    data[cid] = {
        "id": cid,
        "name": name,
        "updated": _now(),
        "meta": dict(meta or {}),
        "cv": cv.model_dump(),
    }
    _write(data_dir, data)
    return cid


def update(
    data_dir: Path,
    cid: str,
    cv: CVData,
    name: str | None = None,
    meta: dict | None = None,
) -> dict | None:
    """Overwrite an existing library entry, preserving its id.

    Refreshes ``updated`` and merges ``meta`` (when provided). Returns the
    stored summary record, or ``None`` when ``cid`` does not exist.
    """
    data = _read(data_dir)
    record = data.get(cid)
    if not record:
        return None
    if name is not None:
        record["name"] = name
    if meta is not None:
        record["meta"] = dict(meta)
    record["cv"] = cv.model_dump()
    record["updated"] = _now()
    _write(data_dir, data)
    return {
        "id": record["id"],
        "name": record.get("name", ""),
        "updated": record.get("updated", ""),
        "meta": record.get("meta", {}),
    }


def name_exists(data_dir: Path, name: str, exclude_id: str | None = None) -> bool:
    """True when another entry already uses this (trimmed, case-insensitive) name.

    Lets the frontend warn about duplicate names before saving.
    """
    needle = (name or "").strip().lower()
    if not needle:
        return False
    for record in _read(data_dir).values():
        if record.get("id") == exclude_id:
            continue
        if (record.get("name") or "").strip().lower() == needle:
            return True
    return False


def list_cvs(data_dir: Path) -> list[dict]:
    data = _read(data_dir)
    entries = []
    for record in data.values():
        entries.append(
            {
                "id": record["id"],
                "name": record.get("name", ""),
                "updated": record.get("updated", ""),
                "meta": record.get("meta", {}),
            }
        )
    entries.sort(key=lambda r: r.get("updated", ""), reverse=True)
    return entries


def get(data_dir: Path, cid: str) -> dict | None:
    record = _read(data_dir).get(cid)
    if not record:
        return None
    return {
        "id": record.get("id", cid),
        "name": record.get("name", ""),
        "updated": record.get("updated", ""),
        "meta": record.get("meta", {}),
        "cv": CVData(**record.get("cv", {})),
    }


def delete(data_dir: Path, cid: str) -> bool:
    data = _read(data_dir)
    if cid not in data:
        return False
    del data[cid]
    _write(data_dir, data)
    return True
