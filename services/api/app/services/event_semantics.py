from app.models.normalized_item import NormalizedItem


def semantic_projection(item: NormalizedItem) -> tuple[str, str]:
    """Return the title and body projection used by event semantic consumers."""

    return (
        item.translated_title or item.normalized_title,
        item.translated_text or item.normalized_text,
    )
