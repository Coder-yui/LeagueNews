from datetime import datetime

from app.connectors.base import BaseConnector, ConnectorRequest, RawItemCandidate


class ManualConnector(BaseConnector[dict[str, object]]):
    connector_type = "manual"
    allowed_run_options = frozenset(
        {
            "title",
            "url",
            "content",
            "content_blocks",
            "raw_payload",
            "author",
            "language",
            "external_id",
            "published_at",
        }
    )

    async def fetch(self, request: ConnectorRequest) -> list[dict[str, object]]:
        return [request.options]

    def map_record(self, record: dict[str, object]) -> RawItemCandidate:
        kwargs = record
        return RawItemCandidate(
                native_title=(
                    kwargs.get("title") if isinstance(kwargs.get("title"), str) else None
                ),
                canonical_url=(
                    kwargs.get("url") if isinstance(kwargs.get("url"), str) else None
                ),
                content_kind="manual",
                content_blocks=(
                    kwargs.get("content_blocks")
                    if isinstance(kwargs.get("content_blocks"), list)
                    else (
                        [{"type": "paragraph", "text": str(kwargs.get("content"))}]
                        if kwargs.get("content")
                        else []
                    )
                ),
                provenance=(
                    kwargs.get("raw_payload")
                    if isinstance(kwargs.get("raw_payload"), dict)
                    else {}
                ),
                author_name=(
                    kwargs.get("author") if isinstance(kwargs.get("author"), str) else None
                ),
                language=(
                    kwargs.get("language") if isinstance(kwargs.get("language"), str) else None
                ),
                external_id=(
                    kwargs.get("external_id")
                    if isinstance(kwargs.get("external_id"), str)
                    else None
                ),
                published_at=(
                    kwargs.get("published_at")
                    if isinstance(kwargs.get("published_at"), datetime)
                    else None
                ),
            )
