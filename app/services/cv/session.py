"""In-memory session store for original CV upload context.

Holds the original uploaded file (bytes) plus the parsed text / structure / CVData
so later requests (analyze/tailor/export) can reuse the same upload without the
client re-sending it. NOT persisted to disk.

Single-user app, but FastAPI runs sync endpoints in a threadpool (and AWS SDK may
spawn threads), so all access is guarded by a ``threading.Lock``.
"""

import threading
import time
import uuid
from dataclasses import dataclass, field

MAX_SESSIONS = 25
MAX_TOTAL_BYTES = 300 * 1024 * 1024  # 300 MiB
DEFAULT_TTL = 1800  # seconds


@dataclass
class SessionEntry:
    session_id: str
    kind: str  # "pdf" | "docx"
    filename: str
    original: bytes
    text: str
    structure: dict
    cv: dict
    confidence: float
    is_image_pdf: bool
    page_images: list[bytes] = field(default_factory=list)
    page_count: int = 0
    confidence_flags: list = field(default_factory=list)  # serialized ConfidenceFlag dicts
    classification: str = "heuristic"  # "llm" | "heuristic"
    created: float = field(default_factory=time.time)


_lock = threading.Lock()
_sessions: dict[str, SessionEntry] = {}


def _is_expired(entry: SessionEntry, now: float) -> bool:
    return (now - entry.created) > DEFAULT_TTL


def _evict(now: float) -> None:
    """Evict expired entries, then oldest-expiry / oldest-created until limits fit."""
    # Drop expired entries first.
    expired = [sid for sid, e in _sessions.items() if _is_expired(e, now)]
    for sid in expired:
        del _sessions[sid]

    total_bytes = sum(len(e.original) + sum(len(p) for p in e.page_images) for e in _sessions.values())
    while len(_sessions) > MAX_SESSIONS or total_bytes > MAX_TOTAL_BYTES:
        if not _sessions:
            break
        # Oldest-expiry first (earliest created == earliest expiry since TTL is fixed).
        sid = min(_sessions, key=lambda s: _sessions[s].created)
        entry = _sessions.pop(sid)
        total_bytes -= len(entry.original) + sum(len(p) for p in entry.page_images)


def create_session(
    kind: str,
    filename: str,
    original: bytes,
    text: str,
    structure: dict,
    cv: dict,
    confidence: float,
    is_image_pdf: bool,
    page_images: list[bytes],
    page_count: int,
    confidence_flags: list | None = None,
    classification: str = "heuristic",
) -> str:
    """Create a session entry and return its id. Evicts to stay within bounds."""
    session_id = uuid.uuid4().hex
    entry = SessionEntry(
        session_id=session_id,
        kind=kind,
        filename=filename,
        original=original,
        text=text,
        structure=structure,
        cv=cv,
        confidence=confidence,
        is_image_pdf=is_image_pdf,
        page_images=page_images,
        page_count=page_count,
        confidence_flags=confidence_flags or [],
        classification=classification,
    )
    with _lock:
        _sessions[session_id] = entry
        _evict(time.time())
    return session_id


def get_session(session_id: str) -> SessionEntry | None:
    """Return the session entry, expiring + evicting it if past its TTL."""
    with _lock:
        entry = _sessions.get(session_id)
        if entry is None:
            return None
        if _is_expired(entry, time.time()):
            del _sessions[session_id]
            return None
        return entry


def delete_session(session_id: str) -> bool:
    with _lock:
        return _sessions.pop(session_id, None) is not None


def session_stats() -> dict:
    """Debug helper: count and total bytes currently held."""
    with _lock:
        total_bytes = sum(
            len(e.original) + sum(len(p) for p in e.page_images) for e in _sessions.values()
        )
        return {"count": len(_sessions), "total_bytes": total_bytes}