import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from app.connectors.base import (
    BaseConnector,
    ConnectorRequest,
    FetchBatch,
    RawItemCandidate,
)
from app.connectors.web_content import clean_text, html_to_blocks
from app.content_blocks import text_from_content_blocks
from app.services.connector_http import ConnectorHTTPClient


class TencentConnectorError(RuntimeError):
    """Tencent's public web content endpoint returned an unusable response."""


@dataclass(frozen=True, slots=True)
class TencentArticleRecord:
    payload: dict[str, Any]
    discovery: dict[str, Any]
    redirect: "TencentRedirectContent | None" = None


@dataclass(frozen=True, slots=True)
class TencentRedirectContent:
    url: str
    html: str
    content_length: int
    week_free_board: str | None = None
    hero_list: dict[str, Any] | None = None


class TencentLolConnector(BaseConnector[TencentArticleRecord]):
    connector_type = "tencent_lol"
    allowed_run_options = frozenset({"target"})
    list_endpoint = "https://apps.game.qq.com/cmc/zmMcnTargetContentList"
    article_endpoint = "https://apps.game.qq.com/cmc/zmMcnContentInfo"
    article_url = "https://lol.qq.com/news/detail.shtml?docid={docid}"
    week_free_board_url = (
        "https://lol.qq.com/act/AutoCMS/publish/LOLAct/"
        "ZMSubject_Board_Site/ZMSubject_Board_Site.js"
    )
    hero_list_url = "https://game.gtimg.cn/images/lol/act/img/js/heroList/hero_list.js"

    def __init__(
        self,
        *,
        http_client_factory: Callable[[], ConnectorHTTPClient] = ConnectorHTTPClient,
    ) -> None:
        self.http_client_factory = http_client_factory

    async def fetch(
        self, request: ConnectorRequest
    ) -> FetchBatch[TencentArticleRecord]:
        limit = min(max(request.limit, 1), 50)
        since = request.since
        configured_target = request.source.connector_config.get("target")
        target = clean_text(request.options.get("target") or configured_target) or "24"
        pending_ids = {
            str(value)
            for value in (request.cursor or {}).get("pending_ids", [])
        }
        async with self.http_client_factory() as client:
            records: list[TencentArticleRecord] = []
            skipped_ids: list[str] = []
            found_page = False
            for page in range(1, 51):
                list_url = (
                    f"{self.list_endpoint}?r0=json&page={page}&num=50"
                    f"&target={target}&source=web_pc"
                )
                response = await client.get(list_url)
                discoveries = self.parse_list(response.json())
                if not discoveries:
                    if not found_page:
                        raise TencentConnectorError(
                            "Tencent news list returned no article records"
                        )
                    break
                found_page = True
                reached_boundary = False
                for discovery in discoveries:
                    published_at = _parse_tencent_datetime(
                        discovery.get("sCreated")
                    )
                    if (
                        isinstance(since, datetime)
                        and published_at
                        and published_at < since
                    ):
                        reached_boundary = True
                        break
                    docid = clean_text(discovery.get("iDocID"))
                    if docid in pending_ids:
                        continue
                    if len(records) >= limit:
                        return FetchBatch(
                            records=records,
                            truncated=True,
                            skipped_ids=tuple(skipped_ids),
                        )
                    response = await client.get(
                        f"{self.article_endpoint}?type=0&docid={docid}&source=web_pc"
                    )
                    article_payload = response.json()
                    if _is_missing_article(article_payload):
                        skipped_ids.append(docid)
                        continue
                    records.append(
                        TencentArticleRecord(
                            article_payload,
                            discovery,
                            await self._fetch_redirect_content(
                                client, article_payload, discovery
                            ),
                        )
                    )
                if reached_boundary or len(discoveries) < 50:
                    return FetchBatch(
                        records=records,
                        truncated=False,
                        skipped_ids=tuple(skipped_ids),
                    )
            return FetchBatch(
                records=records,
                truncated=True,
                skipped_ids=tuple(skipped_ids),
            )

    def map_record(self, record: TencentArticleRecord) -> RawItemCandidate:
        return self.parse_article(
            record.payload, record.discovery, redirect=record.redirect
        )

    async def _fetch_redirect_content(
        self,
        client: ConnectorHTTPClient,
        payload: dict[str, Any],
        discovery: dict[str, Any],
    ) -> TencentRedirectContent | None:
        redirect_url = _redirect_url(payload, discovery)
        if not _is_tencent_lol_url(redirect_url):
            return None
        if not _is_supported_tencent_redirect_url(redirect_url):
            raise TencentConnectorError(
                f"Tencent redirect page is unsupported: {redirect_url}"
            )

        response = await client.get(redirect_url, follow_redirects=False)
        if response.is_redirect:
            raise TencentConnectorError(
                f"Tencent redirect page returned another redirect: {redirect_url}"
            )
        resolved_url = str(response.url)
        if not _is_tencent_lol_url(resolved_url):
            raise TencentConnectorError(
                f"Tencent redirect left the trusted LOL site: {resolved_url}"
            )
        week_free_board = None
        hero_list = None
        if _is_week_free_url(resolved_url):
            board_response = await client.get(self.week_free_board_url)
            hero_response = await client.get(self.hero_list_url)
            week_free_board = board_response.content.decode("utf-8")
            hero_list = hero_response.json()
        return TencentRedirectContent(
            url=resolved_url,
            html=_decode_tencent_html(response.content),
            content_length=len(response.content),
            week_free_board=week_free_board,
            hero_list=hero_list,
        )

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
        cls,
        payload: dict[str, Any],
        discovery: dict[str, Any],
        *,
        redirect: TencentRedirectContent | None = None,
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
        redirect_url = clean_text(result.get("sRedirectURL") or discovery.get("sRedirectURL"))
        is_redirect = str(result.get("iIsRedirect") or discovery.get("iIsRedirect") or "0") == "1"
        redirect_diagnostics: dict[str, object] | None = None
        if redirect is not None:
            if _is_week_free_url(redirect.url):
                blocks, redirect_diagnostics = _parse_week_free_content(redirect)
            else:
                blocks = _parse_redirect_html(redirect)
                redirect_diagnostics = {"extraction_kind": "html_article"}
            url = redirect.url
            redirect_diagnostics.update(
                {
                    "url": redirect.url,
                    "content_length": redirect.content_length,
                }
            )
        elif is_redirect and _is_tencent_lol_url(redirect_url):
            raise TencentConnectorError(
                f"Tencent redirect content is missing: docid={docid}"
            )
        elif is_redirect and _is_http_url(redirect_url):
            blocks = [
                {
                    "type": "embed",
                    "embed_kind": "external_link",
                    "source_url": redirect_url,
                    "text": "查看完整公告",
                }
            ]
            url = redirect_url
        else:
            blocks = html_to_blocks(content_html, base_url=url)
        if not any(block.get("text") or block.get("items") for block in blocks):
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
        provenance: dict[str, object] = {"source_response": source_response}
        if redirect_diagnostics is not None:
            provenance["redirect_response"] = redirect_diagnostics
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
            provenance=provenance,
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


def _is_http_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_missing_article(payload: dict[str, Any]) -> bool:
    return payload.get("status") in {0, "0"} and clean_text(payload.get("msg")).casefold() == "news not found"


def _redirect_url(payload: dict[str, Any], discovery: dict[str, Any]) -> str:
    data = payload.get("data")
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict):
        return ""
    is_redirect = str(
        result.get("iIsRedirect") or discovery.get("iIsRedirect") or "0"
    ) == "1"
    if not is_redirect:
        return ""
    return clean_text(result.get("sRedirectURL") or discovery.get("sRedirectURL"))


def _is_tencent_lol_url(value: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").casefold() == "lol.qq.com"
    )


def _is_week_free_url(value: str) -> bool:
    return urlsplit(value).path.casefold() == "/act/a20200421weekfree/index.html"


def _is_supported_tencent_redirect_url(value: str) -> bool:
    path = urlsplit(value).path.casefold()
    return _is_week_free_url(value) or (
        path.startswith("/gicp/news/") and path.endswith(".html")
    )


def _decode_tencent_html(content: bytes) -> str:
    match = re.search(br"charset\s*=\s*[\"']?\s*([A-Za-z0-9._-]+)", content[:4096], re.I)
    encoding = match.group(1).decode("ascii").casefold() if match else "utf-8"
    if encoding in {"gbk", "gb2312"}:
        encoding = "gb18030"
    try:
        return content.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return content.decode("utf-8", errors="replace")


def _parse_redirect_html(redirect: TencentRedirectContent) -> list[dict[str, Any]]:
    for selector in (".article", "article", "main"):
        blocks = html_to_blocks(
            redirect.html,
            base_url=redirect.url,
            root_selector=selector,
        )
        if text_from_content_blocks(blocks).strip():
            return blocks
    raise TencentConnectorError(f"Tencent redirect article body is empty: {redirect.url}")


def _parse_week_free_content(
    redirect: TencentRedirectContent,
) -> tuple[list[dict[str, Any]], dict[str, object]]:
    site_id = (parse_qs(urlsplit(redirect.url).query).get("siteId") or [""])[0]
    if not site_id or not redirect.week_free_board or redirect.hero_list is None:
        raise TencentConnectorError(
            f"Tencent week-free data is incomplete: siteId={site_id or 'missing'}"
        )
    match = re.search(r"return\s+(\{.*\});\}\);", redirect.week_free_board, re.S)
    if not match:
        raise TencentConnectorError("Tencent week-free board format changed")
    try:
        board = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise TencentConnectorError("Tencent week-free board is invalid JSON") from exc
    if not isinstance(board, dict):
        raise TencentConnectorError("Tencent week-free board root is not an object")
    entry = next(
        (
            value
            for value in board.values()
            if isinstance(value, dict)
            and isinstance(value.get("newBulle"), dict)
            and clean_text(value["newBulle"].get("freeNum")) == site_id
        ),
        None,
    )
    if not isinstance(entry, dict):
        raise TencentConnectorError(f"Tencent week-free issue was not found: siteId={site_id}")

    hero_rows = redirect.hero_list.get("hero")
    if not isinstance(hero_rows, list):
        raise TencentConnectorError("Tencent hero list format changed")
    hero_names = {
        clean_text(hero.get("heroId")): clean_text(hero.get("name"))
        for hero in hero_rows
        if isinstance(hero, dict) and hero.get("heroId") and hero.get("name")
    }
    hero_ids = [
        value.strip()
        for value in clean_text(entry.get("freeHero")).split(",")
        if value.strip()
    ]
    missing_ids = [hero_id for hero_id in hero_ids if hero_id not in hero_names]
    if not hero_ids or missing_ids:
        raise TencentConnectorError(
            "Tencent week-free hero mapping is incomplete: "
            + ", ".join(missing_ids or ["no hero IDs"])
        )
    bulletin = entry["newBulle"]
    issue_date = clean_text(bulletin.get("iDate"))
    version = clean_text(bulletin.get("version"))
    if not issue_date or not version:
        raise TencentConnectorError(
            f"Tencent week-free bulletin is incomplete: siteId={site_id}"
        )
    blocks: list[dict[str, Any]] = [
        {"type": "heading", "level": 2, "text": f"第{site_id}期周免英雄"},
        {"type": "paragraph", "text": f"周免日期：{issue_date}"},
        {"type": "paragraph", "text": f"游戏版本：{version}"},
        {
            "type": "list",
            "ordered": False,
            "items": [hero_names[hero_id] for hero_id in hero_ids],
        },
    ]
    return blocks, {
        "extraction_kind": "week_free_cms",
        "site_id": site_id,
        "issue_date": issue_date,
        "version": version,
        "hero_count": len(hero_ids),
    }
