from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.workflow import GlossaryTerm, KnowledgeRule
from app.models.workflow import ReviewTask
from app.schemas.workflow import (
    GlossaryTermCreate,
    GlossaryTermRead,
    GlossaryTermUpdate,
    KnowledgeRuleCreate,
    KnowledgeRuleRead,
    KnowledgeRuleUpdate,
)
from app.services.llm import LLMAnalysisError, LLMConfigurationError
from app.services.knowledge_governance import detect_active_knowledge_conflicts
from app.workflows.organize_knowledge import organize_active_knowledge_rules

router = APIRouter()


@router.get("/conflicts")
def list_knowledge_conflicts(
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return detect_active_knowledge_conflicts(db)


@router.get("/evaluation-export")
def export_review_evaluation_cases(
    review_ids: list[int] = Query(default=[]),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    if not review_ids:
        return []
    reviews = list(
        db.scalars(
            select(ReviewTask)
            .where(ReviewTask.id.in_(set(review_ids)))
            .order_by(ReviewTask.id)
        )
    )
    return [
        {
            "dataset_version": "admin-export-v1",
            "case_id": f"review-{review.id}",
            "task": review.stage,
            "input": {
                "raw_item_id": review.processing_run.raw_item_id,
                "title": review.processing_run.raw_item.display_title,
                "content_blocks": review.processing_run.raw_item.content_blocks,
            },
            "model_output": review.proposal,
            "correction": review.feedback,
            "source_review_id": review.id,
            "label_status": "needs_admin_label",
        }
        for review in reviews
    ]


@router.get("/rules", response_model=list[KnowledgeRuleRead])
def list_knowledge_rules(
    knowledge_type: str | None = None,
    active: bool | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[KnowledgeRule]:
    statement = select(KnowledgeRule).order_by(KnowledgeRule.updated_at.desc()).limit(500)
    if knowledge_type:
        statement = statement.where(KnowledgeRule.knowledge_type == knowledge_type)
    if active is not None:
        statement = statement.where(KnowledgeRule.is_active.is_(active))
    return list(db.scalars(statement))


@router.post(
    "/rules",
    response_model=KnowledgeRuleRead,
    status_code=status.HTTP_201_CREATED,
)
def create_knowledge_rule(
    payload: KnowledgeRuleCreate,
    db: Session = Depends(get_db),
) -> KnowledgeRule:
    values = payload.model_dump()
    if values["lifecycle_status"] != "draft" or values["is_active"]:
        raise HTTPException(
            status_code=409,
            detail="new knowledge rules must start as draft",
        )
    values["is_active"] = False
    rule = KnowledgeRule(**values)
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.post("/rules/organize", response_model=list[KnowledgeRuleRead])
async def organize_knowledge_rules(
    db: Session = Depends(get_db),
) -> object:
    try:
        return await organize_active_knowledge_rules(db)
    except LLMConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except LLMAnalysisError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.patch("/rules/{rule_id}", response_model=KnowledgeRuleRead)
def update_knowledge_rule(
    rule_id: int,
    payload: KnowledgeRuleUpdate,
    db: Session = Depends(get_db),
) -> KnowledgeRule:
    rule = db.get(KnowledgeRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="knowledge rule not found")
    if rule.knowledge_type == "relevance":
        raise HTTPException(
            status_code=409,
            detail="historical relevance rules are read-only",
        )
    updates = payload.model_dump(exclude_unset=True)
    target_lifecycle = updates.get("lifecycle_status")
    evaluation_summary = updates.get(
        "evaluation_summary", rule.evaluation_summary
    )
    if target_lifecycle == "evaluated" and not evaluation_summary:
        raise HTTPException(
            status_code=409,
            detail="evaluated rules require evaluation_summary",
        )
    if target_lifecycle == "active" and rule.lifecycle_status != "evaluated":
        raise HTTPException(
            status_code=409,
            detail="only evaluated rules can be promoted to active",
        )
    if updates.get("is_active") is True and rule.lifecycle_status != "evaluated":
        raise HTTPException(
            status_code=409,
            detail="only evaluated rules can be activated",
        )
    for field, value in updates.items():
        setattr(rule, field, value)
    if "lifecycle_status" in updates:
        rule.is_active = rule.lifecycle_status == "active"
        now = datetime.now(UTC)
        if rule.lifecycle_status == "evaluated":
            rule.evaluated_at = now
        elif rule.lifecycle_status == "active":
            rule.promoted_at = now
        elif rule.lifecycle_status == "retired":
            rule.retired_at = now
    elif "is_active" in updates:
        rule.lifecycle_status = "active" if rule.is_active else "retired"
    rule.version += 1
    db.commit()
    db.refresh(rule)
    return rule


@router.get("/glossary", response_model=list[GlossaryTermRead])
def list_glossary_terms(
    active: bool | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[GlossaryTerm]:
    statement = select(GlossaryTerm).order_by(GlossaryTerm.updated_at.desc()).limit(1000)
    if active is not None:
        statement = statement.where(GlossaryTerm.is_active.is_(active))
    return list(db.scalars(statement))


@router.post(
    "/glossary",
    response_model=GlossaryTermRead,
    status_code=status.HTTP_201_CREATED,
)
def create_glossary_term(
    payload: GlossaryTermCreate,
    db: Session = Depends(get_db),
) -> GlossaryTerm:
    term = GlossaryTerm(**payload.model_dump())
    db.add(term)
    db.commit()
    db.refresh(term)
    return term


@router.patch("/glossary/{term_id}", response_model=GlossaryTermRead)
def update_glossary_term(
    term_id: int,
    payload: GlossaryTermUpdate,
    db: Session = Depends(get_db),
) -> GlossaryTerm:
    term = db.get(GlossaryTerm, term_id)
    if not term:
        raise HTTPException(status_code=404, detail="glossary term not found")
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(term, field, value)
    term.version += 1
    db.commit()
    db.refresh(term)
    return term
