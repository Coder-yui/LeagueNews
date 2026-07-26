import pytest
from pydantic import ValidationError

from app.content_blocks import (
    display_title,
    normalize_content_blocks,
    text_from_content_blocks,
)
from app.connectors.web_content import html_to_blocks
from app.schemas.raw_item import RawItemImport


def test_content_blocks_preserve_mixed_media_order() -> None:
    payload = RawItemImport(
        title="图文文章",
        content_blocks=[
            {"type": "paragraph", "text": "第一段"},
            {"type": "image", "storage_path": "/media/example.png"},
            {"type": "paragraph", "text": "第二段"},
        ],
    )
    assert [block.type for block in payload.content_blocks or []] == [
        "paragraph",
        "image",
        "paragraph",
    ]


def test_image_block_requires_location() -> None:
    with pytest.raises(ValidationError):
        RawItemImport(content_blocks=[{"type": "image"}])


def test_v2_normalizes_list_video_and_stable_ids() -> None:
    blocks = normalize_content_blocks(
        [
            {"type": "paragraph", "text": "完整第一段"},
            {"type": "list", "text": "列表项"},
            {"type": "video", "source_url": "https://example.com/post/1"},
        ]
    )

    assert [block["id"] for block in blocks] == ["b0001", "b0002", "b0003"]
    assert blocks[1]["items"] == ["列表项"]
    assert blocks[2]["type"] == "embed"
    assert blocks[2]["embed_kind"] == "video"
    assert text_from_content_blocks(blocks) == "完整第一段\n\n列表项"


def test_social_display_title_is_derived_without_mutating_content() -> None:
    blocks = normalize_content_blocks(
        [{"type": "paragraph", "text": "第一行\n第二行也必须保留"}]
    )

    assert (
        display_title(
            native_title=None,
            author_name="英雄联盟赛事",
            source_name=None,
            blocks=blocks,
        )
        == "英雄联盟赛事：第一行"
    )
    assert text_from_content_blocks(blocks) == "第一行\n第二行也必须保留"


def test_embed_requires_browser_openable_url() -> None:
    with pytest.raises(ValueError):
        normalize_content_blocks(
            [
                {
                    "type": "embed",
                    "embed_kind": "video",
                    "source_url": "sinaweibo://detail/1",
                }
            ]
        )


def test_html_lists_are_emitted_as_canonical_items() -> None:
    blocks = html_to_blocks(
        "<html><body><ul><li>第一项</li><li>第二项</li></ul></body></html>",
        base_url="https://example.com/article",
    )

    assert blocks == [
        {
            "type": "list",
            "items": ["第一项", "第二项"],
            "ordered": False,
        }
    ]
