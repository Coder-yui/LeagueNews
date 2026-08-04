import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.api.routes.knowledge import create_knowledge_rule, update_knowledge_rule
from app.core.database import Base
from app.models.workflow import GlossaryTerm, KnowledgeRule
from app.schemas.workflow import KnowledgeRuleCreate, KnowledgeRuleUpdate
from app.services.knowledge_governance import detect_active_knowledge_conflicts


def test_conflicting_active_glossary_and_rules_are_reported() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all(
            [
                GlossaryTerm(
                    source_term="First Stand",
                    preferred_translation="先锋赛",
                    scope="lol",
                ),
                GlossaryTerm(
                    source_term="first stand",
                    preferred_translation="第一站赛",
                    scope="lol",
                ),
                KnowledgeRule(
                    knowledge_type="analysis",
                    scope="global",
                    rule_text="常规赛最高 0.60",
                    correction_data={"constraint_key": "lpl_regular_importance"},
                    lifecycle_status="active",
                    is_active=True,
                ),
                KnowledgeRule(
                    knowledge_type="analysis",
                    scope="global",
                    rule_text="常规赛最高 0.80",
                    correction_data={"constraint_key": "lpl_regular_importance"},
                    lifecycle_status="active",
                    is_active=True,
                ),
            ]
        )
        db.commit()
        conflicts = detect_active_knowledge_conflicts(db)

    assert {conflict["kind"] for conflict in conflicts} == {
        "glossary",
        "knowledge_rule",
    }


def test_knowledge_rule_requires_evaluation_before_activation() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        with pytest.raises(HTTPException, match="must start as draft"):
            create_knowledge_rule(
                KnowledgeRuleCreate(
                    knowledge_type="analysis",
                    rule_text="Never publish directly",
                    lifecycle_status="active",
                    is_active=True,
                ),
                db,
            )

        rule = create_knowledge_rule(
            KnowledgeRuleCreate(
                knowledge_type="analysis",
                rule_text="Evaluate before publishing",
            ),
            db,
        )
        with pytest.raises(HTTPException, match="require evaluation_summary"):
            update_knowledge_rule(
                rule.id,
                KnowledgeRuleUpdate(lifecycle_status="evaluated"),
                db,
            )
        with pytest.raises(HTTPException, match="only evaluated rules"):
            update_knowledge_rule(
                rule.id,
                KnowledgeRuleUpdate(lifecycle_status="active"),
                db,
            )

        evaluated = update_knowledge_rule(
            rule.id,
            KnowledgeRuleUpdate(
                lifecycle_status="evaluated",
                evaluation_summary={"dataset": "offline-v1", "passed": True},
            ),
            db,
        )
        assert evaluated.lifecycle_status == "evaluated"
        assert evaluated.is_active is False

        active = update_knowledge_rule(
            rule.id,
            KnowledgeRuleUpdate(lifecycle_status="active"),
            db,
        )
        assert active.lifecycle_status == "active"
        assert active.is_active is True
