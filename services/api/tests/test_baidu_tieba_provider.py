import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from app.connectors.baidu_tieba import BaiduTiebaConnector
from app.connectors.base import ConnectorRequest, ConnectorSource


FIXTURES = Path(__file__).parent / "fixtures" / "connectors"


class Result(list[object]):
    def __init__(
        self,
        values: list[object],
        *,
        has_more: bool = False,
        err: Exception | None = None,
    ) -> None:
        super().__init__(values)
        self.has_more = has_more
        self.err = err


class FakeTiebaClient:
    def __init__(
        self,
        *,
        threads: list[object],
        post_pages: list[Result],
    ) -> None:
        self.threads = threads
        self.post_pages = post_pages
        self.post_calls: list[tuple[int, int, dict[str, object]]] = []

    async def __aenter__(self) -> "FakeTiebaClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get_user_threads(self, user_id: int, page: int) -> Result:
        assert user_id == 86124184
        assert page == 1
        return Result(self.threads)

    async def get_posts(
        self, tid: int, page: int, **kwargs: object
    ) -> Result:
        self.post_calls.append((tid, page, kwargs))
        return self.post_pages[page - 1]


def tieba_source() -> ConnectorSource:
    return ConnectorSource(
        id=20,
        name="lol半价吧 · 小老鼠小伟",
        connector_type="baidu_tieba",
        external_key="86124184",
        base_url="https://tieba.baidu.com/home/main",
        connector_config={
            "forum_name": "lol半价",
            "max_thread_pages": 5,
            "max_post_pages": 100,
        },
    )


def request() -> ConnectorRequest:
    return ConnectorRequest(
        source=tieba_source(),
        limit=1,
        since=None,
        options={},
    )


def load_fixture_objects() -> tuple[list[object], list[Result]]:
    threads_payload = json.loads(
        (FIXTURES / "tieba_user_threads.json").read_text(encoding="utf-8")
    )
    posts_payload = json.loads(
        (FIXTURES / "tieba_author_posts.json").read_text(encoding="utf-8")
    )
    threads = []
    for data in threads_payload["threads"]:
        data["user"] = SimpleNamespace(**data["user"])
        threads.append(SimpleNamespace(**data))
    pages = []
    for page in posts_payload["pages"]:
        posts = []
        for data in page["posts"]:
            fragments = []
            for fragment_data in data.pop("fragments"):
                kind = fragment_data.pop("kind")
                fragment_type = type(f"Frag{kind.title()}", (), {})
                fragment = fragment_type()
                for key, value in fragment_data.items():
                    setattr(fragment, key, value)
                fragments.append(fragment)
            text = "".join(
                str(getattr(fragment, "text", ""))
                for fragment in fragments
                if type(fragment).__name__ == "FragText"
            )
            posts.append(
                SimpleNamespace(
                    **data,
                    text=text,
                    contents=SimpleNamespace(objs=fragments),
                )
            )
        pages.append(Result(posts, has_more=page["has_more"]))
    return threads, pages


def test_tieba_filters_forum_and_concatenates_all_author_floors() -> None:
    threads, pages = load_fixture_objects()
    client = FakeTiebaClient(threads=threads, post_pages=pages)
    connector = BaiduTiebaConnector(client_factory=lambda: client)

    items = asyncio.run(connector.collect(request()))

    item = items[0]
    assert item.external_id == "10893395340"
    assert item.native_title == "两款上古版本皮肤的创始版获取方式"
    text = "\n".join(
        str(block.get("text") or "") for block in item.content_blocks
    )
    assert "首楼正文" in text
    assert "楼主补充内容" in text
    assert "其他用户内容" not in text
    assert [block["type"] for block in item.content_blocks] == [
        "heading",
        "paragraph",
        "image",
        "embed",
        "heading",
        "paragraph",
        "embed",
    ]
    assert [post["floor"] for post in item.provenance["author_posts"]] == [1, 3]
    assert [call[1] for call in client.post_calls] == [1, 2]
    assert all(call[2]["only_thread_author"] is True for call in client.post_calls)
