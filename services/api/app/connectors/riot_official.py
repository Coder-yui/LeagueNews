import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin, urlsplit, urlunsplit

from selectolax.parser import HTMLParser
from trafilatura import bare_extraction, extract

from app.connectors.base import (
    BaseConnector,
    ConnectorRequest,
    FetchBatch,
    RawItemCandidate,
)
from app.connectors.web_content import clean_text, html_to_blocks
from app.services.connector_http import ConnectorHTTPClient


class RiotConnectorError(RuntimeError):
    """Riot's list or article page no longer provides usable news content."""


@dataclass(frozen=True, slots=True)
class RiotArticleRecord:
    html: str
    discovery: dict[str, object]


class RiotOfficialConnector(BaseConnector[RiotArticleRecord]):
    connector_type = "riot_official"
    list_url = "https://www.leagueoflegends.com/en-us/news/"

    def __init__(
        self,
        *,
        http_client_factory: Callable[[], ConnectorHTTPClient] | None = None,
    ) -> None:
        # The public Riot site is region-routed. Local environments may need
        # their configured proxy to avoid the route being rewritten to the
        # Tencent mirror; cloud deployments can still inject a direct client.
        self.http_client_factory = http_client_factory or (
            lambda: ConnectorHTTPClient(trust_env=True)
        )

    async def fetch(self, request: ConnectorRequest) -> FetchBatch[RiotArticleRecord]:
        limit = min(max(request.limit, 1), 50)
        since = request.since
        pending_ids = {
            str(value)
            for value in (request.cursor or {}).get("pending_ids", [])
        }
        async with self.http_client_factory() as client:
            list_response = await client.get(self.list_url)
            articles = self.parse_list(list_response.text)
            if not articles:
                raise RiotConnectorError(
                    "Riot news list structure changed: no articles found"
                )

            records: list[RiotArticleRecord] = []
            for article in articles:
                published_at = article.get("published_at")
                if isinstance(since, datetime) and isinstance(published_at, datetime):
                    if published_at < since:
                        continue
                article_id = hashlib.sha256(
                    str(article["url"]).encode()
                ).hexdigest()
                if article_id in pending_ids:
                    continue
                if len(records) >= limit:
                    return FetchBatch(records=records, truncated=True)
                response = await client.get(str(article["url"]))
                records.append(RiotArticleRecord(response.text, article))
            return FetchBatch(records=records, truncated=False)

    def map_record(self, record: RiotArticleRecord) -> RawItemCandidate:
        return self.parse_article(record.html, record.discovery)

    @classmethod
    def parse_list(cls, html: str) -> list[dict[str, object]]:
        tree = HTMLParser(html)
        next_data = tree.css_first("#__NEXT_DATA__")
        if next_data:
            try:
                smart_list = _parse_smart_list(json.loads(next_data.text()), cls.list_url)
            except (json.JSONDecodeError, KeyError, TypeError):
                smart_list = []
            if smart_list:
                return smart_list

        found: list[dict[str, object]] = []
        seen: set[str] = set()
        for anchor in tree.css('a[href*="/news/"]'):
            href = anchor.attributes.get("href")
            if not href:
                continue
            url = _canonical_url(urljoin(cls.list_url, href))
            parsed = urlsplit(url)
            if parsed.netloc != "www.leagueoflegends.com":
                continue
            if url in seen or url.rstrip("/") == cls.list_url.rstrip("/"):
                continue
            seen.add(url)
            card = anchor.parent
            for _ in range(4):
                if card is None:
                    break
                if card.css_first("time") or card.css_first('[data-testid="card-title"]'):
                    break
                card = card.parent
            scope = card or anchor
            title_node = scope.css_first('[data-testid="card-title"],h2,h3')
            time_node = scope.css_first("time")
            if title_node is None or time_node is None:
                continue
            title = clean_text(
                title_node.text(separator=" ", strip=True)
            )
            if not title:
                continue
            found.append(
                {
                    "url": url,
                    "title": title,
                    "published_at": _parse_datetime(
                        time_node.attributes.get("datetime") if time_node else None
                    ),
                    "category": _category_from_url(url),
                }
            )
        return found

    @staticmethod
    def parse_article(html: str, discovery: dict[str, object]) -> RawItemCandidate:
        tree = HTMLParser(html)
        title_node = tree.css_first('[data-testid="title"],h1')
        time_node = tree.css_first("time")
        author_nodes = tree.css('[data-testid="author-name"],.author-name')
        title = clean_text(
            title_node.text(separator=" ", strip=True) if title_node else discovery.get("title")
        )
        url = str(discovery["url"])
        blocks: list[dict[str, object]] = []
        rich_text_selector = (
            '[data-testid="ArticleRichTextBlade"] [data-testid="rich-text-html"],'
            '[data-testid="RichTextPatchNotesBlade"] [data-testid="rich-text-html"]'
        )
        for root in tree.css(rich_text_selector):
            blocks.extend(html_to_blocks(root.html, base_url=url))
        if not blocks:
            blocks = html_to_blocks(
                html,
                base_url=url,
                root_selector='[data-testid="ArticleRichTextBlade"]',
            )
        if not _has_text(blocks):
            fallback = extract(
                html,
                url=url,
                output_format="txt",
                include_images=False,
                include_comments=False,
            )
            fallback_text = clean_text(fallback)
            if fallback_text:
                blocks = [{"type": "paragraph", "text": fallback_text}]
        if not _has_text(blocks):
            raise RiotConnectorError(f"Riot article structure changed or body is empty: {url}")

        metadata_document = bare_extraction(html, url=url, with_metadata=True)
        metadata = metadata_document.as_dict() if metadata_document else {}
        author = ", ".join(
            clean_text(node.text(separator=" ", strip=True))
            for node in author_nodes
            if clean_text(node.text(separator=" ", strip=True))
        ) or clean_text(metadata.get("author")) or None
        published_at = _parse_datetime(
            time_node.attributes.get("datetime") if time_node else None
        ) or discovery.get("published_at")
        return RawItemCandidate(
            external_id=hashlib.sha256(url.encode()).hexdigest(),
            native_title=title or None,
            canonical_url=url,
            content_kind="article",
            author_name=author,
            language="en",
            published_at=published_at if isinstance(published_at, datetime) else None,
            content_blocks=blocks,
            provenance={
                "source_response": {
                    "discovery": _json_safe(discovery),
                    "metadata": _json_safe(metadata),
                },
            },
        )


def _canonical_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/") + "/", "", ""))


def _parse_datetime(value: object) -> datetime | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _category_from_url(url: str) -> str:
    path_parts = [part for part in urlsplit(url).path.split("/") if part]
    try:
        news_index = path_parts.index("news")
        return path_parts[news_index + 1]
    except (ValueError, IndexError):
        return "unknown"


def _parse_smart_list(
    payload: dict[str, object], base_url: str
) -> list[dict[str, object]]:
    page_props = payload.get("props")
    page_props = page_props.get("pageProps") if isinstance(page_props, dict) else None
    page = page_props.get("page") if isinstance(page_props, dict) else None
    blades = page.get("blades") if isinstance(page, dict) else None
    if not isinstance(blades, list):
        return []

    found: list[dict[str, object]] = []
    seen: set[str] = set()
    for blade in blades:
        items = blade.get("items") if isinstance(blade, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            action = item.get("action")
            action_payload = action.get("payload") if isinstance(action, dict) else None
            raw_url = action_payload.get("url") if isinstance(action_payload, dict) else None
            if not isinstance(raw_url, str):
                continue
            url = _canonical_url(urljoin(base_url, raw_url))
            parsed = urlsplit(url)
            if (
                parsed.netloc != "www.leagueoflegends.com"
                or not parsed.path.startswith("/en-us/news/")
                or parsed.path.rstrip("/") == "/en-us/news"
                or url in seen
            ):
                continue
            title = clean_text(item.get("title"))
            if not title:
                continue
            category = item.get("category")
            found.append(
                {
                    "url": url,
                    "title": title,
                    "published_at": _parse_datetime(item.get("publishedAt")),
                    "category": (
                        clean_text(category.get("machineName"))
                        if isinstance(category, dict)
                        else _category_from_url(url)
                    ),
                }
            )
            seen.add(url)
    return found


def _has_text(blocks: list[dict[str, object]]) -> bool:
    return any(block.get("text") for block in blocks)


def _json_safe(value: object) -> object:
    return json.loads(json.dumps(value, default=str)) if value else {}
