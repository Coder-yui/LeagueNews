from __future__ import annotations

import argparse
import re
from typing import Any

from sqlalchemy import create_engine, inspect


def _column_signature(column: dict[str, Any]) -> tuple[object, ...]:
    return (
        column["name"],
        str(column["type"]),
        bool(column["nullable"]),
        str(column.get("default")),
    )


def _freeze(value: object) -> object:
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _named_signatures(values: list[dict[str, Any]], *keys: str) -> list[tuple[object, ...]]:
    return sorted(
        tuple(_freeze(value.get(key)) for key in keys)
        for value in values
    )


def _check_signatures(values: list[dict[str, Any]]) -> list[tuple[object, ...]]:
    signatures = []
    for value in values:
        sqltext = str(value.get("sqltext") or "")
        sqltext = re.sub(r"::text\[\]|::character varying|::text", "", sqltext)
        signatures.append((value.get("name"), " ".join(sqltext.split())))
    return sorted(signatures)


def schema_signature(database_url: str) -> dict[str, object]:
    inspector = inspect(create_engine(database_url, pool_pre_ping=True))
    signature: dict[str, object] = {}
    for table in sorted(set(inspector.get_table_names()) - {"schema_migrations"}):
        signature[table] = {
            "columns": sorted(
                _column_signature(column) for column in inspector.get_columns(table)
            ),
            "primary_key": tuple(
                inspector.get_pk_constraint(table).get("constrained_columns") or ()
            ),
            "unique_constraints": _named_signatures(
                inspector.get_unique_constraints(table),
                "name",
                "column_names",
            ),
            "indexes": _named_signatures(
                inspector.get_indexes(table),
                "name",
                "column_names",
                "unique",
            ),
            "foreign_keys": _named_signatures(
                inspector.get_foreign_keys(table),
                "name",
                "constrained_columns",
                "referred_table",
                "referred_columns",
                "options",
            ),
            "checks": _check_signatures(inspector.get_check_constraints(table)),
        }
    return signature


def compare_schemas(left_url: str, right_url: str) -> None:
    left = schema_signature(left_url)
    right = schema_signature(right_url)
    if left != right:
        tables = sorted(set(left) | set(right))
        differences = [table for table in tables if left.get(table) != right.get(table)]
        raise RuntimeError(
            "database schemas do not converge; differing tables: "
            + ", ".join(differences)
        )
    print(f"Database schemas converge across {len(left)} application tables.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare current application schema from two migration paths."
    )
    parser.add_argument("left_database_url")
    parser.add_argument("right_database_url")
    args = parser.parse_args()
    compare_schemas(args.left_database_url, args.right_database_url)


if __name__ == "__main__":
    main()
