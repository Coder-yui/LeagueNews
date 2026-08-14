from contextlib import contextmanager
from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.core import database


@contextmanager
def mcp_db_session() -> Iterator[Session]:
    """Open one short-lived read session for an MCP tool call."""
    with database.SessionLocal() as db:
        yield db
