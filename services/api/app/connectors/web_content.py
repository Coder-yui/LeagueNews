import re
from html import unescape
from typing import Any
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node


TEXT_TAGS = {
    "h1": "heading",
    "h2": "heading",
    "h3": "heading",
    "h4": "heading",
    "h5": "heading",
    "h6": "heading",
    "p": "paragraph",
    "blockquote": "quote",
    "ul": "list",
    "ol": "list",
}
def clean_text(value: object) -> str:
    text = unescape(str(value or "")).replace("\x00", "")
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def absolute_url(value: object, base_url: str) -> str | None:
    url = clean_text(value)
    if not url or url.startswith("data:"):
        return None
    if url.startswith("//"):
        return f"https:{url}"
    return urljoin(base_url, url)


def first_attribute(node: Node, names: tuple[str, ...]) -> str | None:
    for name in names:
        value = node.attributes.get(name)
        if value:
            return value
    return None


def html_to_blocks(html: str, *, base_url: str, root_selector: str | None = None) -> list[dict[str, Any]]:
    tree = HTMLParser(html)
    root = tree.css_first(root_selector) if root_selector else tree.body
    if root is None:
        return []

    blocks: list[dict[str, Any]] = []
    for node in root.traverse(include_text=False):
        tag = node.tag.lower()
        if tag not in {*TEXT_TAGS, "img", "video", "iframe"}:
            continue
        if tag in TEXT_TAGS:
            if _has_selected_ancestor(node, root):
                continue
            separator = "\n" if tag in {"ul", "ol"} else " "
            text = clean_text(node.text(separator=separator, strip=True))
            if text:
                block_type = TEXT_TAGS[tag]
                if block_type == "list":
                    items = [
                        clean_text(item.text(separator=" ", strip=True))
                        for item in node.css("li")
                    ]
                    items = [item for item in items if item]
                    if items:
                        blocks.append(
                            {
                                "type": "list",
                                "items": items,
                                "ordered": tag == "ol",
                            }
                        )
                elif block_type == "heading":
                    blocks.append(
                        {"type": "heading", "text": text, "level": int(tag[1])}
                    )
                else:
                    blocks.append({"type": block_type, "text": text})
            continue

        if tag == "img":
            source_url = absolute_url(
                first_attribute(
                    node,
                    ("src", "data-src", "data-original", "data-lazy-src", "data-echo"),
                ),
                base_url,
            )
            if source_url:
                blocks.append(
                    {
                        "type": "image",
                        "source_url": source_url,
                        "mime_type": _image_mime(source_url),
                        "alt_text": clean_text(node.attributes.get("alt")) or None,
                        "caption": _nearby_caption(node),
                    }
                )
            continue

        source_url = absolute_url(
            first_attribute(node, ("src", "data-src")) or _nested_source(node),
            base_url,
        )
        if source_url:
            blocks.append(
                {
                    "type": "embed",
                    "embed_kind": "video" if tag == "video" else "iframe",
                    "source_url": source_url,
                    "caption": clean_text(node.attributes.get("title")) or None,
                }
            )
    return blocks


def _has_selected_ancestor(node: Node, root: Node) -> bool:
    parent = node.parent
    while parent is not None and parent is not root:
        if parent.tag.lower() in TEXT_TAGS:
            return True
        parent = parent.parent
    return False


def _nested_source(node: Node) -> str | None:
    source = node.css_first("source")
    return first_attribute(source, ("src", "data-src")) if source else None


def _nearby_caption(node: Node) -> str | None:
    parent = node.parent
    if parent is None:
        return None
    if parent.tag.lower() == "figure":
        caption = parent.css_first("figcaption")
        if caption:
            return clean_text(caption.text(separator=" ", strip=True)) or None
    return None


def _image_mime(url: str) -> str | None:
    path = url.lower().split("?", 1)[0]
    if path.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if path.endswith(".png"):
        return "image/png"
    if path.endswith(".webp"):
        return "image/webp"
    if path.endswith(".gif"):
        return "image/gif"
    return None
