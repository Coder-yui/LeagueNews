from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.engine import make_url

from app.core.config import settings


def psycopg_connection_string(database_url: str) -> str:
    url = make_url(database_url)
    if not url.drivername.startswith("postgresql"):
        raise ValueError("LangGraph production checkpointing requires PostgreSQL")
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


@asynccontextmanager
async def open_postgres_checkpointer(
    database_url: str | None = None,
) -> AsyncIterator[AsyncPostgresSaver]:
    """Open the migration-managed LangGraph checkpoint store."""

    connection_string = psycopg_connection_string(
        database_url or settings.database_url
    )
    async with AsyncPostgresSaver.from_conn_string(connection_string) as saver:
        yield saver
