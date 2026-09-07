import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.services import item_processing_context, item_publication
from app.api.routes.workflows import _corrected_review_proposal
from app.core.database import Base
from app.models.media_asset import MediaAsset
from app.models.media_extraction import MediaExtraction
from app.models.ocr_lab import OCRProfile
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.workflow import GlossaryTerm, KnowledgeRule, ProcessingRun, ReviewTask
from app.schemas.workflow import (
    OCRReviewCorrection,
    ReviewCorrectionApproval,
    ReviewRejection,
)
from app.services.media_ocr import OCRResult
from app.services.patch_table import PatchTableResult
from app.services.review_actions import correct_ocr_review, reject_review
from app.services.media_methods import (
    build_patch_preview,
    extract_patch_preview,
    is_patch_preview,
)


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def test_manual_importance_override_keeps_calculation_auditable() -> None:
    review = ReviewTask(
        processing_run_id=1,
        stage="importance",
        proposal={
            "importance_score": 0.67,
            "importance_calculation": {
                "policy_version": "importance-v4-intrinsic-priority",
                "final_score": 0.67,
            },
        },
    )

    proposal = _corrected_review_proposal(
        review,
        ReviewCorrectionApproval(
            importance_score=0.91,
            note="完整版本预览应为高重要性",
        ),
    )

    assert proposal["importance_score"] == 0.91
    calculation = proposal["importance_calculation"]
    assert calculation["computed_score"] == 0.67
    assert calculation["final_score"] == 0.91
    assert calculation["manual_override"] == {
        "score": 0.91,
        "reason": "完整版本预览应为高重要性",
    }


@pytest.mark.parametrize(
    ("proposal", "content_form", "expected_error"),
    [
        (
            {
                "title": "",
                "summary": "",
                "entities": [],
                "products": ["unknown"],
                "content_form": "media_only",
            },
            "original",
            "必须生成标题",
        ),
        (
            {
                "title": "已有标题",
                "summary": "",
                "entities": [],
                "products": ["unknown"],
                "content_form": "media_only",
            },
            "repost",
            "必须生成摘要",
        ),
    ],
)
def test_manual_content_form_correction_rejects_missing_content_fields(
    proposal: dict[str, object],
    content_form: str,
    expected_error: str,
) -> None:
    review = ReviewTask(
        processing_run_id=1,
        stage="message_analysis",
        proposal=proposal,
    )

    with pytest.raises(ValueError, match=f"{expected_error}.*重新运行消息分析"):
        _corrected_review_proposal(
            review,
            ReviewCorrectionApproval(content_form=content_form),
        )


def test_manual_content_correction_keeps_complete_message_valid() -> None:
    review = ReviewTask(
        processing_run_id=1,
        stage="message_analysis",
        proposal={
            "title": "完整标题",
            "summary": "完整摘要",
            "entities": [],
            "products": ["lol_pc"],
            "content_form": "original",
        },
    )

    corrected = _corrected_review_proposal(
        review,
        ReviewCorrectionApproval(content_form="quote"),
    )

    assert corrected["content_form"] == "quote"


def test_importance_scoring_uses_approved_title_and_body() -> None:
    content = item_processing_context.importance_scoring_content(
        {"translated_content_blocks": [{"id": "b0001", "type": "paragraph", "text": "第1楼"}]},
        {"title": "战斗之夜皮肤现已开放领取"},
    )

    assert content == "战斗之夜皮肤现已开放领取\n第1楼"


def test_importance_correction_respects_upstream_products_and_source() -> None:
    with _session() as db:
        raw = _raw_item(db)
        run = ProcessingRun(
            raw_item_id=raw.id,
            workflow_type="item",
            status="awaiting_review",
            current_stage="importance",
            context={
                    "approved_message_analysis_proposal": {
                        "products": ["lol_pc"],
                        "content_form": "original",
                        "classification_source": {"source_kind": "unofficial"},
                    }
            },
        )
        db.add(run)
        db.flush()
        review = ReviewTask(
            processing_run_id=run.id,
            stage="importance",
            status="pending",
            proposal={
                "message_type": "game_leak",
                "topics": ["balance_gameplay"],
                "importance_score": 0.5,
            },
        )
        db.add(review)
        db.commit()

        with pytest.raises(ValueError, match="不适用于当前信源性质"):
            _corrected_review_proposal(
                review,
                ReviewCorrectionApproval(message_type="game_patch_notes"),
            )

        with pytest.raises(ValueError, match="不适用于所选 products"):
            _corrected_review_proposal(
                review,
                ReviewCorrectionApproval(topics=["esports_matches"]),
            )

        corrected = _corrected_review_proposal(
            review,
            ReviewCorrectionApproval(
                message_type="game_community_discussion",
                topics=["champions"],
            ),
        )
        assert corrected["message_type"] == "game_community_discussion"
        assert corrected["topics"] == ["champions"]


def _raw_item(
    db: Session,
    *,
    language: str | None = None,
    native_title: str = "Patch preview",
    content_blocks: list[dict[str, object]] | None = None,
) -> RawItem:
    source = Source(
        name="测试信源",
        connector_type="x_twitter",
        external_key="testaccount",
        connector_config={},
    )
    db.add(source)
    db.flush()
    raw = RawItem(
        source_id=source.id,
        native_title=native_title,
        language=language,
        content_blocks=content_blocks or [{"type": "paragraph", "text": "A test patch preview"}],
        published_at=datetime(2026, 7, 25, tzinfo=UTC),
    )
    db.add(raw)
    db.commit()
    return raw


def test_patch_ocr_only_triggers_for_designer_preview_with_image() -> None:
    with _session() as db:
        raw = _raw_item(db)
        raw.media_assets.append(
            MediaAsset(block_index=1, mime_type="image/png", storage_path="test.png")
        )
        assert is_patch_preview(raw) is False

        raw.source.external_key = "RiotPhroxzon"
        assert is_patch_preview(raw) is True

        raw.native_title = "General gameplay thoughts"
        raw.content_blocks = [
            {
                "type": "paragraph",
                "text": "Champion design discussion without an update table.",
            }
        ]
        assert is_patch_preview(raw) is False


def test_low_confidence_patch_table_is_saved_for_manual_ocr_review(monkeypatch) -> None:
    ocr = OCRResult(
        raw_text="Aatrox",
        lines=[],
        confidence=0.9,
        sha256="test",
        width=1000,
        height=800,
        processed_width=1000,
        processed_height=800,
        engine="test",
    )
    table = PatchTableResult(
        preview_kind="preview",
        divider_x=None,
        structure_confidence=0.1375,
        sections=[
            {
                "section_type": "champion_buff",
                "label": "CHAMPION BUFFS",
                "records": [
                    {
                        "target": "Aatrox",
                        "raw_changes": [],
                        "bbox": [0, 0, 100, 30],
                        "ocr_confidence": 0.9,
                    }
                ],
            }
        ],
        warnings=["结构置信度较低"],
        boundaries=[],
    )
    monkeypatch.setattr("app.services.media_methods.run_ocr", lambda *_: ocr)
    monkeypatch.setattr(
        "app.services.media_methods.parse_patch_table",
        lambda *_args, **_kwargs: table,
    )

    with _session() as db:
        raw = _raw_item(db)
        raw.source.external_key = "RiotPhroxzon"
        asset = MediaAsset(
            raw_item_id=raw.id,
            block_index=1,
            mime_type="image/png",
            storage_path="test.png",
        )
        db.add(asset)
        db.commit()

        extraction = asyncio.run(
            extract_patch_preview(
                db,
                raw_item=raw,
                media_asset=asset,
                structure=False,
                enforce_confidence=False,
            )
        )

        assert extraction.schema_version == "v2-ocr-review"
        assert extraction.structured_data == {}
        assert extraction.processing_config["structure_confidence"] == 0.1375


def test_patch_ocr_reprocesses_when_active_profile_parameters_change(
    monkeypatch,
) -> None:
    observed_parameters: list[dict[str, object]] = []
    ocr = OCRResult(
        raw_text="Poppy\n50/80/110/140/170 -> 75/105/135/165/195",
        lines=[],
        confidence=0.99,
        sha256="updated",
        width=1000,
        height=800,
        processed_width=2000,
        processed_height=1600,
        engine="test",
    )
    table = PatchTableResult(
        preview_kind="full_preview",
        divider_x=None,
        structure_confidence=1.0,
        sections=[
            {
                "section_type": "champion_buff",
                "label": "CHAMPION BUFFS",
                "records": [
                    {
                        "target": "Poppy",
                        "raw_changes": ["50/80/110/140/170 -> 75/105/135/165/195"],
                        "bbox": [0, 0, 100, 30],
                        "ocr_confidence": 0.99,
                    }
                ],
            }
        ],
        warnings=[],
        boundaries=[],
    )

    def fake_ocr(_path: str, parameters: dict[str, object]) -> OCRResult:
        observed_parameters.append(parameters)
        return ocr

    monkeypatch.setattr("app.services.media_methods.run_ocr", fake_ocr)
    monkeypatch.setattr(
        "app.services.media_methods.parse_patch_table",
        lambda *_args, **_kwargs: table,
    )

    with _session() as db:
        raw = _raw_item(db)
        asset = MediaAsset(
            raw_item_id=raw.id,
            block_index=1,
            mime_type="image/png",
            storage_path="test.png",
        )
        profile = OCRProfile(
            name="production-2026-07-25",
            parameters={"scale": 2},
            is_active=True,
        )
        db.add_all([asset, profile])
        db.flush()
        stale = MediaExtraction(
            media_asset_id=asset.id,
            task_type="patch_preview",
            provider="patch-table+rapidocr",
            ocr_engine="test",
            structuring_model="",
            schema_version="v2-ocr-review",
            status="processed",
            raw_ocr_text="stale",
            ocr_lines=[],
            structured_data={},
            processing_config={"parameters": {}},
            confidence=0.8,
        )
        db.add(stale)
        db.commit()

        extraction = asyncio.run(
            extract_patch_preview(
                db,
                raw_item=raw,
                media_asset=asset,
                structure=False,
                enforce_confidence=False,
            )
        )

        assert extraction.id != stale.id
        assert extraction.processing_config["parameters"] == {"scale": 2}
        assert extraction.processing_config["ocr_profile_name"] == profile.name
        assert observed_parameters == [{"scale": 2}]
        assert stale.status == "superseded"


def test_patch_preview_structure_is_deterministic_for_six_supported_sections() -> None:
    section_types = [
        "champion_buff",
        "champion_nerf",
        "champion_adjustment",
        "system_buff",
        "system_nerf",
        "system_adjustment",
    ]
    result = build_patch_preview(
        title="@RiotPhroxzon: Patch 26.13 Full Preview!\n\nSenna\n\nBody text",
        table_data={
            "preview_kind": "full_preview",
            "sections": [
                {
                    "section_type": section_type,
                    "label": section_type,
                    "records": [
                        {
                            "target": f"Target {index}",
                            "raw_changes": ["Health: 100 -> 120"],
                            "ocr_confidence": 0.96,
                        }
                    ],
                }
                for index, section_type in enumerate(section_types)
            ],
            "warnings": [],
        },
    )

    assert result.patch == "26.13"
    assert result.title == "@RiotPhroxzon: Patch 26.13 Full Preview!"
    assert [section.section_type for section in result.sections] == section_types
    assert [section.label for section in result.sections] == [
        "英雄增强",
        "英雄削弱",
        "英雄调整",
        "系统增强",
        "系统削弱",
        "系统调整",
    ]
    assert [section.entries[0].target_type for section in result.sections] == [
        "champion",
        "champion",
        "champion",
        "system",
        "system",
        "system",
    ]
    change = result.sections[0].entries[0].changes[0]
    assert change == "Health: 100 -> 120"


def test_patch_preview_structure_rejects_sections_outside_six_supported_types() -> None:
    with pytest.raises(ValueError, match="只允许"):
        build_patch_preview(
            title="Patch 26.13 Preview!",
            table_data={
                "preview_kind": "preview",
                "sections": [
                    {
                        "section_type": "item_buff",
                        "label": "ITEM BUFFS",
                        "records": [
                            {
                                "target": "Infinity Edge",
                                "raw_changes": [],
                                "ocr_confidence": 0.9,
                            }
                        ],
                    }
                ],
                "warnings": [],
            },
        )


def test_deterministic_full_preview_keeps_reviewed_empty_changes() -> None:
    result = build_patch_preview(
        title="Patch 26.15 Full Preview!",
        table_data={
            "preview_kind": "full_preview",
            "sections": [
                {
                    "section_type": "champion_buff",
                    "label": "CHAMPION BUFFS",
                    "records": [
                        {
                            "target": "Azir",
                            "raw_changes": [],
                            "ocr_confidence": 0.98,
                        }
                    ],
                }
            ],
            "warnings": [],
        },
    )

    assert result.sections[0].entries[0].target == "Azir"
    assert result.sections[0].entries[0].changes == []






def test_message_analysis_content_keeps_title_visible_for_media_posts() -> None:
    content = item_processing_context.message_analysis_content(
        {
            "translated_title": "经典模式 直播带货内容",
            "translated_content_blocks": [
                {"type": "heading", "text": "第1楼"},
                {"type": "image", "storage_path": "/media/example.jpg"},
            ],
        }
    )

    assert content.startswith("[消息标题]\n经典模式 直播带货内容")
    assert "[消息正文]\n第1楼" in content








def test_analysis_assembles_only_approved_stage_outputs() -> None:
    with _session() as db:
        raw = _raw_item(db)
        proposal = item_publication.build_item_proposal(
            raw_item=raw,
            translation_proposal={
                "normalized_text": "Aphelios receives buffs.",
                "translated_title": "26.15版本预览",
                "translated_text": "厄斐琉斯获得增强。",
                "translated_content_blocks": [{"type": "paragraph", "text": "厄斐琉斯获得增强。"}],
                "translated_media_extractions": [],
            },
            analysis_proposal={
                "title": "26.15版本预览",
                "summary": "厄斐琉斯将在新版本中获得增强。",
                "entities": [
                    {"name": "26.15", "type": "patch"},
                    {"name": "厄斐琉斯", "type": "champion"},
                ],
                "products": ["lol_pc"],
                "content_form": "original",
                "classification_version": "message-taxonomy-v2",
            },
            importance_proposal={
                "message_type": "game_official_preview",
                "topics": ["balance_gameplay", "champions"],
                "classification_source": {
                    "current_source_kind": "official",
                    "source_kind": "official",
                    "basis": "current",
                    "upstream_source_url": None,
                },
                "importance_score": 0.8,
                "importance_dimensions": {},
                "importance_policy_version": "importance-v11-repost-weekly-rotation",
                "importance_calculation": {"final_score": 0.8},
                "priority_score": 0.8,
                "priority_calculation": {"final_score": 0.8},
            },
            relevance_proposal={"decision": "relevant"},
        )

    assert proposal["summary"] == "厄斐琉斯将在新版本中获得增强。"
    assert proposal["normalized_text"] == "Aphelios receives buffs."
    assert proposal["importance_score"] == 0.8
    assert proposal["products"] == ["lol_pc"]
    assert proposal["message_type"] == "game_official_preview"
    assert proposal["topics"] == ["balance_gameplay", "champions"]
    assert proposal["facets"]["classification_source"]["source_kind"] == "official"
    assert proposal["facets"]["relevance"]["decision"] == "relevant"


@pytest.mark.parametrize(
    ("content_form", "expected_title"),
    [("media_only", "仅媒体消息"), ("link_only", "仅链接消息")],
)
def test_published_nonsemantic_message_uses_deterministic_title(
    content_form: str,
    expected_title: str,
) -> None:
    with _session() as db:
        raw = _raw_item(
            db,
            native_title="",
            content_blocks=[
                {"type": "image", "source_url": "https://example.com/image.jpg"}
                if content_form == "media_only"
                else {
                    "type": "embed",
                    "embed_kind": "external_link",
                    "source_url": "https://example.com/",
                }
            ],
        )
        proposal = item_publication.build_item_proposal(
            raw_item=raw,
            translation_proposal={
                "normalized_text": "",
                "translated_title": "",
                "translated_text": "",
                "translated_content_blocks": raw.content_blocks,
                "translation_status": "not_required",
            },
            analysis_proposal={
                "title": "",
                "summary": "",
                "entities": [],
                "products": ["unknown"],
                "content_form": content_form,
                "classification_version": "message-taxonomy-v3",
                "classification_source": {
                    "current_source_kind": "unofficial",
                    "source_kind": "unofficial",
                    "basis": "current",
                    "upstream_source_url": None,
                },
            },
            importance_proposal=None,
        )
        item = item_publication.apply_normalized_item(db, raw, proposal)
        db.commit()

        assert item.normalized_title == expected_title
        assert item.facets["classification_source"]["basis"] == "current"


def test_media_message_preserves_explicit_analysis_title() -> None:
    with _session() as db:
        raw = _raw_item(
            db,
            native_title="原始明确标题",
            content_blocks=[{"type": "image", "source_url": "https://example.com/a.jpg"}],
        )
        title = item_publication.normalized_title(
            raw_item=raw,
            translation_proposal={"translated_title": "已有译文标题"},
            analysis_proposal={"title": "分析明确标题", "content_form": "media_only"},
        )
        assert title == "分析明确标题"


def test_source_authority_uses_sixty_for_tieba_and_one_hundred_for_official() -> None:
    with _session() as db:
        raw = _raw_item(db)
        raw.source.connector_type = "baidu_tieba"
        assert item_processing_context.source_authority(raw) == 60

        raw.source.connector_type = "x_twitter"
        raw.source.external_key = "LeagueOfLegends"
        raw.source.is_official = True
        assert item_processing_context.source_authority(raw) == 100




def test_translation_rejection_accepts_glossary_without_reason() -> None:
    with _session() as db:
        raw = _raw_item(db)
        run = ProcessingRun(
            raw_item_id=raw.id,
            workflow_type="item",
            status="awaiting_review",
            current_stage="translation",
        )
        db.add(run)
        db.flush()
        review = ReviewTask(
            processing_run_id=run.id,
            stage="translation",
            status="pending",
            proposal={"translated_title": "错误译文"},
        )
        db.add(review)
        db.commit()

        result = reject_review(
            db,
            review,
            payload=ReviewRejection(
                feedback_type="translation_correction",
                glossary_updates=[
                    {
                        "source_term": "Ability Haste",
                        "preferred_translation": "技能急速",
                        "forbidden_translations": ["能力急速"],
                    },
                    {
                        "source_term": "Movement Speed",
                        "preferred_translation": "移动速度",
                    },
                ],
            ),
        )

        assert result.status == "rejected"
        assert result.outcome == "review_rejected"
        assert db.scalar(select(KnowledgeRule)) is None
        terms = list(db.scalars(select(GlossaryTerm).order_by(GlossaryTerm.id)))
        assert [term.preferred_translation for term in terms] == [
            "技能急速",
            "移动速度",
        ]
        assert all(term.is_active for term in terms)


def test_translation_rejection_accepts_reason_without_glossary() -> None:
    with _session() as db:
        raw = _raw_item(db)
        run = ProcessingRun(
            raw_item_id=raw.id,
            workflow_type="item",
            status="awaiting_review",
            current_stage="translation",
        )
        db.add(run)
        db.flush()
        review = ReviewTask(
            processing_run_id=run.id,
            stage="translation",
            status="pending",
            proposal={"translated_title": "待修正译文"},
        )
        db.add(review)
        db.commit()

        result = reject_review(
            db,
            review,
            payload=ReviewRejection(
                feedback_type="translation_correction",
                reason="描述尚未实装的改动时统一使用将来时，不要写成已经生效。",
            ),
        )

        rule = db.scalar(select(KnowledgeRule))
        assert result.status == "rejected"
        assert rule.knowledge_type == "translation"
        assert rule.rule_text == "描述尚未实装的改动时统一使用将来时，不要写成已经生效。"
        assert db.scalar(select(GlossaryTerm)) is None


def test_translation_rejection_requires_reason_or_glossary() -> None:
    with pytest.raises(ValueError, match="退回理由或至少一条术语修正"):
        ReviewRejection(
            feedback_type="translation_correction",
            reason="   ",
        )


def test_ocr_rejection_does_not_grow_knowledge_or_glossary() -> None:
    with _session() as db:
        raw = _raw_item(db)
        run = ProcessingRun(
            raw_item_id=raw.id,
            workflow_type="item",
            status="awaiting_review",
            current_stage="media",
        )
        db.add(run)
        db.flush()
        review = ReviewTask(
            processing_run_id=run.id,
            stage="media",
            status="pending",
            proposal={"translated_title": "OCR 后的译文"},
        )
        db.add(review)
        db.commit()

        result = reject_review(
            db,
            review,
            payload=ReviewRejection(
                feedback_type="ocr_error",
                knowledge_rule="不应被写入",
                glossary_updates=[
                    {
                        "source_term": "12S",
                        "preferred_translation": "125",
                    }
                ],
            ),
        )

        assert result.status == "rejected"
        assert review.feedback["feedback_type"] == "ocr_error"
        assert review.feedback["reason"] is None
        assert db.scalar(select(KnowledgeRule)) is None
        assert db.scalar(select(GlossaryTerm)) is None


def test_manual_ocr_correction_without_note_creates_revision_and_regenerates_ocr_review() -> None:
    with _session() as db:
        raw = _raw_item(db, language="zh-CN")
        asset = MediaAsset(
            raw_item_id=raw.id,
            block_index=1,
            mime_type="image/png",
            storage_path="test.png",
        )
        db.add(asset)
        db.flush()
        original_table = {
            "preview_kind": "preview",
            "divider_x": 300,
            "structure_confidence": 0.9,
            "sections": [
                {
                    "section_type": "champion_buff",
                    "label": "CHAMPION BUFFS",
                    "records": [
                        {
                            "target": "Aatrox",
                            "raw_changes": ["Health: 12S -> 12S"],
                            "bbox": [10, 20, 600, 100],
                            "ocr_confidence": 0.8,
                        }
                    ],
                }
            ],
            "warnings": [],
            "boundaries": [],
        }
        original = MediaExtraction(
            media_asset_id=asset.id,
            task_type="patch_preview",
            provider="paddleocr+openai-compatible",
            ocr_engine="paddleocr",
            structuring_model="test",
            schema_version="v2",
            status="processed",
            raw_ocr_text="Aatrox Health 12S -> 12S",
            ocr_lines=[],
            structured_data={"title": "old"},
            processing_config={"table_data": original_table},
            confidence=0.8,
        )
        db.add(original)
        db.flush()
        run = ProcessingRun(
            raw_item_id=raw.id,
            workflow_type="item",
            status="awaiting_review",
            current_stage="media",
        )
        db.add(run)
        db.flush()
        review = ReviewTask(
            processing_run_id=run.id,
            stage="media",
            status="pending",
            proposal={"approved_media_extraction_ids": [original.id]},
        )
        db.add(review)
        db.commit()

        corrected_table = {
            **original_table,
            "sections": [
                {
                    **original_table["sections"][0],
                    "records": [
                        {
                            **original_table["sections"][0]["records"][0],
                            "raw_changes": ["Health: 12S -> 125"],
                        }
                    ],
                }
            ],
        }
        result = asyncio.run(
            correct_ocr_review(
                db,
                review,
                payload=OCRReviewCorrection(
                    extraction_id=original.id,
                    table_data=corrected_table,
                ),
            )
        )

        extractions = list(db.scalars(select(MediaExtraction).order_by(MediaExtraction.id)))
        pending_review = db.scalar(select(ReviewTask).where(ReviewTask.status == "pending"))
        assert result.status == "awaiting_review"
        assert len(extractions) == 2
        assert original.processing_config["table_data"] == original_table
        assert extractions[1].schema_version == "v2-ocr-review-manual"
        assert extractions[1].structured_data == {}
        assert extractions[1].processing_config["table_data"] == corrected_table
        assert (
            extractions[1].processing_config["manual_correction"]["corrected_from_extraction_id"]
            == original.id
        )
        assert review.status == "superseded"
        assert pending_review.id != review.id
        assert pending_review.proposal["approved_media_extraction_ids"] == [extractions[1].id]
        assert pending_review.proposal["ocr_corrections"][0]["note"] is None
        assert db.scalar(select(KnowledgeRule)) is None
        assert db.scalar(select(GlossaryTerm)) is None


def test_analysis_rejection_creates_editable_knowledge_rule() -> None:
    with _session() as db:
        raw = _raw_item(db)
        run = ProcessingRun(
            raw_item_id=raw.id,
            workflow_type="item",
            status="awaiting_review",
            current_stage="message_analysis",
        )
        db.add(run)
        db.flush()
        review = ReviewTask(
            processing_run_id=run.id,
            stage="message_analysis",
            status="pending",
            proposal={"summary": "错误摘要"},
        )
        db.add(review)
        db.commit()

        reject_review(
            db,
            review,
            payload=ReviewRejection(
                feedback_type="analysis_correction",
                reason="版本预览不是正式实装公告",
            ),
        )

        rule = db.scalar(select(KnowledgeRule))
        assert rule.rule_text == "版本预览不是正式实装公告"
        assert rule.knowledge_type == "analysis"


def test_relevance_rejection_stops_run_without_creating_analysis_rule() -> None:
    with _session() as db:
        raw = _raw_item(db)
        run = ProcessingRun(
            raw_item_id=raw.id,
            workflow_type="item",
            status="awaiting_review",
            current_stage="relevance",
        )
        db.add(run)
        db.flush()
        review = ReviewTask(
            processing_run_id=run.id,
            stage="relevance",
            status="pending",
            proposal={"decision": "uncertain"},
        )
        db.add(review)
        db.commit()

        reject_review(
            db,
            review,
            payload=ReviewRejection(
                feedback_type="analysis_correction",
                reason="现有证据无法判断产品范围",
            ),
        )

        assert run.status == "rejected"
        assert db.scalar(select(KnowledgeRule)) is None









