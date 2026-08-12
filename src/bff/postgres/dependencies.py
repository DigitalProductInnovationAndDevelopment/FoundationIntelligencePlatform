"""Fail-closed read/write PostgreSQL dependency selection."""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from bff.database import WriterDatabaseUnavailable


def reader_sessions(request: Request):
    """Return the SELECT-only pool used by reads and pure calculations."""
    return request.app.state.database.sessions()


def writer_sessions(request: Request):
    """Return the writer pool only after route-level RBAC dependencies pass."""
    try:
        return request.app.state.database.write_sessions()
    except WriterDatabaseUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The mutation database path is temporarily unavailable.",
        ) from exc


__all__ = ["reader_sessions", "writer_sessions"]
