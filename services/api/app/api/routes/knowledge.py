from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.workflow import GlossaryTerm, KnowledgeRule
from app.schemas.workflow import (
    GlossaryTermCreate,
    GlossaryTermRead,
    GlossaryTermUpdate,
    KnowledgeRuleCreate,
    KnowledgeRuleRead,
    KnowledgeRuleUpdate,
)

router = APIRouter()


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
    rule = KnowledgeRule(**payload.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.patch("/rules/{rule_id}", response_model=KnowledgeRuleRead)
def update_knowledge_rule(
    rule_id: int,
    payload: KnowledgeRuleUpdate,
    db: Session = Depends(get_db),
) -> KnowledgeRule:
    rule = db.get(KnowledgeRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="knowledge rule not found")
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(rule, field, value)
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
