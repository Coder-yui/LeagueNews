"""Small real alternatives used to exercise method substitution."""

import re
from typing import Any
from app.domain.event_types import EVENT_FAMILIES
from app.domain.importance import ImportanceScale
from app.methods.contracts import MethodSelection


async def _message_analysis_heuristic(
    *, payload: dict[str, Any], selection: "MethodSelection", **_context: Any
) -> Any:
    """Example candidate: extract controlled fields from frozen text."""

    from app.services.llm import MessageContentAnalysisResult

    title = str(payload.get("title") or "").strip()
    content = str(payload.get("content") or "").strip()
    text = f"{title} {content}".strip()
    lowered = text.casefold()
    if any(term in lowered for term in ("云顶", "tft")):
        products = ["tft"]
    elif any(term in lowered for term in ("赛事", "赛程", "季后赛", "lpl", "lck", "msi", "worlds")):
        products = ["lol_esports"]
    elif any(term in lowered for term in ("宇宙", "符文之地", "动画", "电影")):
        products = ["lol_universe"]
    elif any(term in lowered for term in ("手游", "2xko", "符文之地传说")):
        products = ["other_lol_product"]
    elif text:
        products = ["lol_pc"]
    else:
        products = ["unknown"]
    has_repost = bool((payload.get("evidence_structure") or {}).get("has_repost_evidence"))
    content_form = "repost" if has_repost else "original"
    summary_limit = int(selection.strategy_parameters.get("summary_max_chars", 180))
    summary = _first_sentence(content or title)[: max(summary_limit, 1)]
    if not summary and content_form == "original":
        content_form = "media_only"
        products = ["unknown"]
    return MessageContentAnalysisResult(
        title=title[:500],
        summary="" if content_form == "media_only" else summary,
        entities=[],
        products=products,
        content_form=content_form,
    )


async def _importance_scoring_rule_v2(
    *, payload: dict[str, Any], selection: "MethodSelection", **_context: Any
) -> Any:
    """Example candidate: deterministic evidence-weighted classification."""

    del selection
    from app.services.llm import MessageClassificationImportanceResult

    content = str(payload.get("content") or "")
    facts = payload.get("extracted_facts") or {}
    text = f"{content} {facts}".casefold()
    products = [str(value) for value in payload.get("products") or []]
    if any(term in text for term in ("版本", "patch", "热修复", "平衡")):
        message_type = "game_patch_notes"
        topics = ["balance_gameplay"]
        scale: ImportanceScale = "major"
        prominence = "notable"
        is_bulk_update = True
    elif any(term in text for term in ("赛事", "赛程", "季后赛", "决赛", "lpl", "lck")):
        message_type = "esports_announcement"
        topics = ["esports_schedule"]
        scale = "major"
        prominence = "notable"
        is_bulk_update = False
    elif "tft" in products or "云顶" in text:
        message_type = "other_lol_product_announcement"
        topics = ["tft_gameplay"]
        scale = "standard"
        prominence = "normal"
        is_bulk_update = False
    else:
        message_type = "game_community_discussion"
        topics = ["community"]
        scale = "standard"
        prominence = "normal"
        is_bulk_update = False
    evidence = _matched_terms(
        text, ("版本", "patch", "热修复", "平衡", "赛事", "赛程", "季后赛", "tft", "云顶")
    )
    return MessageClassificationImportanceResult(
        message_type=message_type,
        topics=topics,
        scale=scale,
        audience_region="global",
        competition_region="international" if "esports" in message_type else "none",
        prominence=prominence,
        skin_tier="none",
        is_bulk_update=is_bulk_update,
        evidence=evidence or ["text evidence"],
    )


async def _event_aggregation_token_recall_v2(
    *, payload: dict[str, Any], selection: "MethodSelection", **_context: Any
) -> Any:
    """Example candidate: choose the highest token-overlap event candidate."""

    from app.schemas.event_aggregation import EventAggregationResult

    message = dict(payload.get("message") or {})
    text = " ".join(str(value) for value in message.values())
    message_tokens = _tokens(text)
    candidates = [value for value in payload.get("candidates") or [] if isinstance(value, dict)]
    scored: list[tuple[float, dict[str, Any]]] = []
    for candidate in candidates:
        candidate_text = " ".join(
            str(candidate.get(key) or "")
            for key in ("title", "summary", "latest_development", "event_family")
        )
        candidate_tokens = _tokens(candidate_text)
        overlap = len(message_tokens & candidate_tokens)
        scored.append((overlap / max(len(message_tokens), 1), candidate))
    scored.sort(
        key=lambda item: (item[0], int(item[1].get("event_id") or 0)),
        reverse=True,
    )
    minimum_overlap = float(selection.strategy_parameters.get("min_token_overlap", 0.05))
    if scored and scored[0][0] >= minimum_overlap and scored[0][1].get("event_id"):
        candidate = scored[0][1]
        family = str(candidate.get("event_family") or "other_named_development")
        if family not in EVENT_FAMILIES:
            family = "other_named_development"
        return EventAggregationResult(
            mentions=[
                {
                    "mention_index": 0,
                    "action": "attach",
                    "event_id": int(candidate["event_id"]),
                    "product": _event_product(message),
                    "event_family": family,
                    "relation": "reports",
                    "source_role": "unknown",
                    "materiality": "material_update",
                    "evidence_excerpt": text[:200] or "frozen message evidence",
                }
            ]
        )
    if text.strip():
        title = str(message.get("title") or text[:80]).strip() or "未命名事件"
        return EventAggregationResult(
            mentions=[
                {
                    "mention_index": 0,
                    "action": "create",
                    "product": _event_product(message),
                    "event_family": "other_named_development",
                    "relation": "reports",
                    "source_role": "unknown",
                    "materiality": "material_update",
                    "evidence_excerpt": text[:200],
                    "new_event": {
                        "title": title[:500],
                        "summary": text[:1000],
                        "canonical_anchors": {},
                        "latest_development": text[:1000],
                        "key_facts": [],
                    },
                }
            ]
        )
    return EventAggregationResult(mentions=[])


def _first_sentence(value: str) -> str:
    return re.split(r"(?<=[。！？])|(?<=[.!?])\s+", value.strip(), maxsplit=1)[0].strip()


def _matched_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if term.casefold() in text]


def _tokens(value: str) -> set[str]:
    return {
        token for token in re.findall(r"[\w\u4e00-\u9fff]+", value.casefold()) if len(token) > 1
    }


def _event_product(message: dict[str, Any]) -> str:
    products = message.get("products")
    if isinstance(products, list) and products:
        return str(products[0])
    text = " ".join(str(value) for value in message.values()).casefold()
    return "lol_esports" if any(term in text for term in ("赛事", "lpl", "lck")) else "lol_pc"
