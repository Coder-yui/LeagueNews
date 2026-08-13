from typing import Any

from sqlalchemy import Text, cast, type_coerce
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement


def json_array_contains(
    db: Session,
    column: ColumnElement[Any],
    value: str,
) -> ColumnElement[bool]:
    """Return an exact JSON-array membership condition for supported databases."""
    if db.get_bind().dialect.name == "postgresql":
        return type_coerce(column, JSONB).contains([value])

    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return cast(column, Text).like(f'%"{escaped}"%', escape="\\")
