from typing import Any, Protocol


class HasManualOverrides(Protocol):
    manual_override_fields: list[str]


def set_automatic_projection_field(
    target: HasManualOverrides,
    field: str,
    value: Any,
) -> bool:
    """Apply an automatic projection update unless editorial ownership is locked."""
    if field in (target.manual_override_fields or []):
        return False
    setattr(target, field, value)
    return True
