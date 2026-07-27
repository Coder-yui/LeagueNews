import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
import app.workflows.reviewed_pipeline as reviewed_pipeline
from app.core.database import Base
from app.models.media_asset import MediaAsset
from app.models.media_extraction import MediaExtraction
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.workflow import GlossaryTerm, KnowledgeRule, ProcessingRun, ReviewTask
from app.schemas.workflow import OCRReviewCorrection, ReviewRejection
from app.services.llm import AnalysisResult, LLMClient
from app.services.media_ocr import OCRResult
from app.services.patch_table import PatchTableResult
from app.workflows.reviewed_pipeline import (
    approve_review,
    correct_ocr_review,
    reject_review,
    retry_processing_run,
)
from app.workflows.understand_media import (
    build_patch_preview,
    extract_patch_preview,
    is_patch_preview,
)


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


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
        content_blocks=content_blocks
        or [{"type": "paragraph", "text": "A test patch preview"}],
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
    monkeypatch.setattr("app.workflows.understand_media.run_ocr", lambda *_: ocr)
    monkeypatch.setattr(
        "app.workflows.understand_media.parse_patch_table",
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


def test_approving_ocr_stages_extractions_then_moves_to_translation_review(monkeypatch) -> None:
    structured_ids: list[int] = []

    def fake_structure(extraction, *, title):
        structured_ids.append(extraction.id)
        extraction.structured_data = {"title": title, "sections": []}
        return extraction

    async def fake_generate_translation_review(db, run):
        assert run.current_stage == "translation"
        assert run.context["approved_media_extraction_ids"] == structured_ids
        run.status = "awaiting_review"
        db.commit()

    monkeypatch.setattr(reviewed_pipeline, "structure_patch_extraction", fake_structure)
    monkeypatch.setattr(
        reviewed_pipeline,
        "_generate_translation_review",
        fake_generate_translation_review,
    )

    with _session() as db:
        raw = _raw_item(db)
        asset = MediaAsset(
            raw_item_id=raw.id,
            block_index=1,
            mime_type="image/png",
            storage_path="test.png",
        )
        db.add(asset)
        db.flush()
        extraction = MediaExtraction(
            media_asset_id=asset.id,
            task_type="patch_preview",
            provider="patch-table+rapidocr",
            ocr_engine="test",
            structuring_model="",
            schema_version="v2-ocr-review",
            status="processed",
            raw_ocr_text="Aatrox",
            ocr_lines=[],
            structured_data={},
            processing_config={"table_data": {}},
            confidence=0.5,
        )
        db.add(extraction)
        db.flush()
        run = ProcessingRun(
            raw_item_id=raw.id,
            workflow_type="item",
            status="awaiting_review",
            current_stage="image_ocr",
        )
        db.add(run)
        db.flush()
        review = ReviewTask(
            processing_run_id=run.id,
            stage="image_ocr",
            status="pending",
            proposal={"approved_media_extraction_ids": [extraction.id]},
        )
        db.add(review)
        db.commit()

        result = asyncio.run(approve_review(db, review, note=None))

        assert structured_ids == [extraction.id]
        assert review.status == "approved"
        assert result.current_stage == "translation"
        assert result.status == "awaiting_review"


def test_approving_translation_moves_to_analysis_review(monkeypatch) -> None:
    translation_proposal = {
        "normalized_text": "Patch preview from a designer.",
        "translated_title": "26.15版本预览",
        "translated_text": "设计师发布版本预览。",
        "translated_content_blocks": [
            {"type": "paragraph", "text": "设计师发布版本预览。"}
        ],
        "approved_media_extraction_ids": [],
        "translated_media_extractions": [],
    }

    async def fake_generate_item_review(db, run):
        assert run.current_stage == "item_analysis"
        assert run.context["approved_translation_proposal"] == translation_proposal
        run.status = "awaiting_review"
        db.commit()

    monkeypatch.setattr(
        reviewed_pipeline,
        "_generate_item_review",
        fake_generate_item_review,
    )

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
            proposal=translation_proposal,
        )
        db.add(review)
        db.commit()

        result = asyncio.run(approve_review(db, review, note="翻译通过"))

        assert review.status == "approved"
        assert result.current_stage == "item_analysis"
        assert result.status == "awaiting_review"
        assert raw.normalized_item is None


def test_analysis_uses_only_approved_chinese_translation(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_analyze(_self, **payload):
        captured.update(payload)
        return AnalysisResult.model_validate(
            {
                "title": "26.15版本预览",
                "summary": "厄斐琉斯将在新版本中获得增强。",
                "category": "版本更新",
                "entities": [
                    {"name": "26.15", "type": "patch"},
                    {"name": "版本预览", "type": "document_type"},
                ],
                "importance_score": 0.8,
                "importance_evidence": ["属于版本预览，处于0.80至0.92区间。"],
                "credibility": "official",
                "credibility_score": 1.0,
                "credibility_evidence": ["设计师官方账号"],
            }
        )

    monkeypatch.setattr(LLMClient, "analyze", fake_analyze)
    with _session() as db:
        raw = _raw_item(db)
        proposal = asyncio.run(
            reviewed_pipeline._build_item_proposal(
                raw_item=raw,
                translation_proposal={
                    "normalized_text": "Aphelios receives buffs.",
                    "translated_title": "26.15版本预览",
                    "translated_text": "厄斐琉斯获得增强。",
                    "translated_content_blocks": [
                        {"type": "paragraph", "text": "厄斐琉斯获得增强。"}
                    ],
                    "translated_media_extractions": [
                        {
                            "extraction_id": 3,
                            "translated_data": {
                                "sections": [
                                    {
                                        "entries": [
                                            {
                                                "target": "厄斐琉斯",
                                                "target_type": "champion",
                                                "changes": ["攻击力提高。"],
                                            }
                                        ]
                                    }
                                ]
                            },
                        }
                    ],
                },
                rules=["摘要只陈述已确认事实。"],
            )
        )

    assert captured["title"] == "26.15版本预览"
    assert "厄斐琉斯获得增强" in str(captured["content"])
    assert "攻击力提高" in str(captured["content"])
    assert "Aphelios receives buffs" not in str(captured["content"])
    assert captured["knowledge_rules"] == ["摘要只陈述已确认事实。"]
    assert "glossary" not in captured
    assert proposal["summary"] == "厄斐琉斯将在新版本中获得增强。"
    assert proposal["normalized_text"] == "Aphelios receives buffs."
    assert proposal["importance_evidence"] == ["属于版本预览，处于0.80至0.92区间。"]
    assert proposal["credibility"] == "unverified"
    assert proposal["credibility_score"] == 0.6
    assert proposal["credibility_evidence"] == ["信源“测试信源”的配置权威度为 60"]


def test_source_authority_uses_sixty_for_tieba_and_one_hundred_for_official() -> None:
    with _session() as db:
        raw = _raw_item(db)
        raw.source.connector_type = "baidu_tieba"
        assert reviewed_pipeline._source_authority(raw) == 60

        raw.source.connector_type = "x_twitter"
        raw.source.external_key = "LeagueOfLegends"
        assert reviewed_pipeline._source_authority(raw) == 100


def test_chinese_item_skips_translation_review(monkeypatch) -> None:
    generated_item_reviews: list[int] = []

    async def fake_generate_item_review(db, run):
        generated_item_reviews.append(run.id)
        run.status = "awaiting_review"
        db.commit()

    monkeypatch.setattr(
        reviewed_pipeline,
        "_generate_item_review",
        fake_generate_item_review,
    )

    with _session() as db:
        raw = _raw_item(
            db,
            language="zh-CN",
            native_title="国服活动公告",
            content_blocks=[{"type": "paragraph", "text": "参与活动可免费获得皮肤。"}],
        )
        run = ProcessingRun(
            raw_item_id=raw.id,
            workflow_type="item",
            status="running",
            current_stage="translation",
            context={},
        )
        db.add(run)
        db.commit()

        asyncio.run(reviewed_pipeline._generate_translation_review(db, run))

        db.refresh(run)
        assert run.current_stage == "item_analysis"
        assert run.status == "awaiting_review"
        assert generated_item_reviews == [run.id]
        assert run.context["approved_translation_proposal"]["translation_status"] == "not_required"
        translation_reviews = list(
            db.scalars(
                select(ReviewTask).where(
                    ReviewTask.processing_run_id == run.id,
                    ReviewTask.stage == "translation",
                )
            )
        )
        assert translation_reviews == []


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
            current_stage="image_ocr",
        )
        db.add(run)
        db.flush()
        review = ReviewTask(
            processing_run_id=run.id,
            stage="image_ocr",
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
                reason="图片里的 125 被识别成了 12S",
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
            current_stage="image_ocr",
        )
        db.add(run)
        db.flush()
        review = ReviewTask(
            processing_run_id=run.id,
            stage="image_ocr",
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

        extractions = list(
            db.scalars(select(MediaExtraction).order_by(MediaExtraction.id))
        )
        pending_review = db.scalar(
            select(ReviewTask).where(ReviewTask.status == "pending")
        )
        assert result.status == "awaiting_review"
        assert len(extractions) == 2
        assert original.processing_config["table_data"] == original_table
        assert extractions[1].schema_version == "v2-ocr-review-manual"
        assert extractions[1].structured_data == {}
        assert extractions[1].processing_config["table_data"] == corrected_table
        assert extractions[1].processing_config["manual_correction"][
            "corrected_from_extraction_id"
        ] == original.id
        assert review.status == "superseded"
        assert pending_review.id != review.id
        assert pending_review.proposal["approved_media_extraction_ids"] == [
            extractions[1].id
        ]
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
            current_stage="item_analysis",
        )
        db.add(run)
        db.flush()
        review = ReviewTask(
            processing_run_id=run.id,
            stage="item_analysis",
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


def test_relevance_rejection_ends_run_and_creates_knowledge() -> None:
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
            proposal={"is_lol_relevant": False},
        )
        db.add(review)
        db.commit()

        result = reject_review(
            db,
            review,
            payload=ReviewRejection(
                feedback_type="relevance_correction",
                reason="该内容讨论英雄联盟端游版本改动，应判定为相关",
            ),
        )

        assert result.status == "rejected"
        assert result.outcome == "review_rejected"
        rule = db.scalar(select(KnowledgeRule))
        assert rule.knowledge_type == "relevance"


@pytest.mark.parametrize(
    "stage", ["relevance", "image_ocr", "item_analysis", "translation"]
)
def test_restart_continues_from_rejected_stage(monkeypatch, stage: str) -> None:
    generated_stages: list[str] = []

    async def fake_review(db, run):
        generated_stages.append(run.current_stage)
        run.status = "awaiting_review"
        db.commit()

    monkeypatch.setattr(reviewed_pipeline, "_generate_relevance_review", fake_review)
    monkeypatch.setattr(reviewed_pipeline, "_generate_ocr_review", fake_review)
    monkeypatch.setattr(reviewed_pipeline, "_generate_item_review", fake_review)
    monkeypatch.setattr(reviewed_pipeline, "_generate_translation_review", fake_review)
    with _session() as db:
        raw = _raw_item(db)
        old_run = ProcessingRun(
            raw_item_id=raw.id,
            workflow_type="item",
            status="rejected",
            outcome="review_rejected",
            current_stage=stage,
            context={"approved_media_extraction_ids": [7]},
        )
        db.add(old_run)
        db.commit()

        new_run = asyncio.run(retry_processing_run(db, old_run))

        assert new_run.id != old_run.id
        assert new_run.supersedes_run_id == old_run.id
        assert new_run.current_stage == stage
        assert new_run.context == {"approved_media_extraction_ids": [7]}
        assert new_run.status == "awaiting_review"
        assert old_run.status == "rejected"
        assert generated_stages == [stage]


def test_approved_item_persists_translated_patch_data_as_relational_link() -> None:
    with _session() as db:
        raw = _raw_item(db)
        asset = MediaAsset(
            raw_item_id=raw.id,
            block_index=1,
            mime_type="image/png",
            storage_path="test.png",
        )
        db.add(asset)
        db.flush()
        extraction = MediaExtraction(
            media_asset_id=asset.id,
            task_type="patch_preview",
            provider="patch-table+rapidocr",
            ocr_engine="test",
            structuring_model="patch-preview-deterministic-v1",
            schema_version="v2",
            status="processed",
            raw_ocr_text="Aatrox Health 100 -> 120",
            ocr_lines=[],
            structured_data={"sections": [{"entries": [{"target": "Aatrox"}]}]},
            processing_config={},
            confidence=0.95,
        )
        db.add(extraction)
        db.flush()
        run = ProcessingRun(
            raw_item_id=raw.id,
            workflow_type="item",
            status="awaiting_review",
            current_stage="item_analysis",
        )
        db.add(run)
        db.flush()
        review = ReviewTask(
            processing_run_id=run.id,
            stage="item_analysis",
            status="pending",
            proposal={
                "normalized_title": "26.15版本预览",
                "normalized_text": "Patch preview",
                "summary": "设计师发布版本预览。",
                "category": "版本更新",
                "entities": [{"英雄": "暗裔剑魔"}],
                "importance_score": 0.8,
                "credibility": "official",
                "credibility_score": 0.98,
                "credibility_evidence": ["设计师官方账号"],
                "language": "en",
                "source_language": "en",
                "target_language": "zh-CN",
                "translated_title": "26.15版本预览",
                "translated_text": "版本预览",
                "translated_content_blocks": [],
                "translation_status": "translated",
                "translation_model": "test",
                "analysis_model": "test",
                "analysis_version": "v4-reviewed-item",
                "approved_media_extraction_ids": [extraction.id],
                "translated_media_extractions": [
                    {
                        "extraction_id": extraction.id,
                        "translated_data": {
                            "sections": [{"entries": [{"target": "暗裔剑魔"}]}]
                        },
                    }
                ],
            },
        )
        db.add(review)
        db.commit()

        result = asyncio.run(approve_review(db, review, note="确认"))

        item = db.scalar(select(NormalizedItem))
        assert result.status == "completed"
        assert result.outcome == "approved"
        assert item.entities == [{"name": "暗裔剑魔", "type": "champion"}]
        assert item.approved_media_extraction_ids == [extraction.id]
        assert item.translated_media_extractions[0]["translated_data"]["sections"][0][
            "entries"
        ][0]["target"] == "暗裔剑魔"
        db.expire(raw, ["normalized_item"])
        assert raw.processing_status == "analyzed"
