import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.connectors.base import (
    BaseConnector,
    ConnectorRequest,
    RawItemCandidate,
)
from app.connectors.web_content import clean_text, html_to_blocks
from app.services.connector_http import ConnectorHTTPClient


class TencentConnectorError(RuntimeError):
    """Tencent's public web content endpoint returned an unusable response."""


@dataclass(frozen=True, slots=True)
class TencentArticleRecord:
    payload: dict[str, Any]
    discovery: dict[str, Any]


class TencentLolConnector(BaseConnector[TencentArticleRecord]):
    connector_type = "tencent_lol"
    allowed_run_options = frozenset({"target"})
    list_endpoint = "https://apps.game.qq.com/cmc/zmMcnTargetContentList"
    article_endpoint = "https://apps.game.qq.com/cmc/zmMcnContentInfo"
    article_url = "https://lol.qq.com/news/detail.shtml?docid={docid}"

    def __init__(
        self,
        *,
        http_client_factory: Callable[[], ConnectorHTTPClient] = ConnectorHTTPClient,
    ) -> None:
        self.http_client_factory = http_client_factory

    async def fetch(self, request: ConnectorRequest) -> list[TencentArticleRecord]:
        limit = min(max(request.limit, 1), 50)
        since = request.since
        configured_target = request.source.connector_config.get("target")
        target = clean_text(request.options.get("target") or configured_target) or "24"
        list_url = (
            f"{self.list_endpoint}?r0=json&page=1&num={limit}&target={target}&source=web_pc"
        )
        async with self.http_client_factory() as client:
            response = await client.get(list_url)
            discoveries = self.parse_list(response.json())
            if not discoveries:
                raise TencentConnectorError("Tencent news list returned no article records")
            records: list[TencentArticleRecord] = []
            for discovery in discoveries:
                published_at = _parse_tencent_datetime(discovery.get("sCreated"))
                if isinstance(since, datetime) and published_at and published_at < since:
                    continue
                docid = clean_text(discovery.get("iDocID"))
                response = await client.get(
                    f"{self.article_endpoint}?type=0&docid={docid}&source=web_pc"
                )
                records.append(TencentArticleRecord(response.json(), discovery))
            return records[:limit]

    def map_record(self, record: TencentArticleRecord) -> RawItemCandidate:
        return self.parse_article(record.payload, record.discovery)

    @staticmethod
    def parse_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
        if payload.get("status") not in {1, "1", None}:
            raise TencentConnectorError(
                f"Tencent news list failed: {clean_text(payload.get('msg')) or 'unknown error'}"
            )
        data = payload.get("data")
        result = data.get("result") if isinstance(data, dict) else None
        return [item for item in result or [] if isinstance(item, dict) and item.get("iDocID")]

    @classmethod
    def parse_article(
        cls, payload: dict[str, Any], discovery: dict[str, Any]
    ) -> RawItemCandidate:
        data = payload.get("data")
        result = data.get("result") if isinstance(data, dict) else None
        if payload.get("status") not in {1, "1"} or not isinstance(result, dict):
            raise TencentConnectorError(
                f"Tencent article failed: {clean_text(payload.get('msg')) or 'unknown error'}"
            )
        docid = clean_text(result.get("iDocID") or discovery.get("iDocID"))
        url = cls.article_url.format(docid=docid)
        content_html = _strip_control_characters(str(result.get("sContent") or ""))
        blocks = html_to_blocks(content_html, base_url=url)
        if not any(block.get("text") for block in blocks):
            raise TencentConnectorError(f"Tencent article body is empty: docid={docid}")

        source_response = {
            key: value
            for key, value in result.items()
            if key
            not in {
                "sContent",
                "iComment",
                "iShareNum",
                "iLikeNum",
                "likeTotal",
            }
        }
        source_response["content_length"] = len(content_html)
        return RawItemCandidate(
            external_id=docid,
            native_title=clean_text(result.get("sTitle") or discovery.get("sTitle")) or None,
            canonical_url=url,
            content_kind="article",
            author_name=clean_text(result.get("sAuthor") or discovery.get("sAuthor")) or None,
            language="zh-CN",
            published_at=_parse_tencent_datetime(
                result.get("sCreated") or discovery.get("sCreated")
            ),
            content_blocks=blocks,
            provenance={
                "source_response": source_response,
            },
        )


def _parse_tencent_datetime(value: object) -> datetime | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=ZoneInfo("Asia/Shanghai")
        )
    except ValueError:
        return None


def _strip_control_characters(value: str) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
