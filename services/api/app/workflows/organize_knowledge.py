from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.workflow import KnowledgeRule
from app.services.llm import LLMClient


async def organize_active_knowledge_rules(db: Session) -> list[KnowledgeRule]:
    source_rules = list(
        db.scalars(
            select(KnowledgeRule)
            .where(KnowledgeRule.is_active.is_(True))
            .order_by(
                KnowledgeRule.knowledge_type,
                KnowledgeRule.scope,
                KnowledgeRule.id,
            )
        )
    )
    if not source_rules:
        raise ValueError("没有可整理的生效知识")

    result = await LLMClient().organize_knowledge(
        rules=[
            {
                "id": rule.id,
                "knowledge_type": rule.knowledge_type,
                "scope": rule.scope,
                "rule_text": rule.rule_text,
            }
            for rule in source_rules
        ]
    )

    source_by_id = {rule.id: rule for rule in source_rules}
    organized_rules: list[KnowledgeRule] = []
    for organized in result.rules:
        source_ids = organized.source_rule_ids
        source_reviews = {
            source_by_id[source_id].source_review_id
            for source_id in source_ids
            if source_by_id[source_id].source_review_id is not None
        }
        rule = KnowledgeRule(
            knowledge_type=organized.knowledge_type,
            scope=organized.scope,
            rule_text=organized.rule_text,
            correction_data={
                "organized_from_rule_ids": source_ids,
                "source_review_ids": sorted(source_reviews),
            },
            is_active=True,
        )
        db.add(rule)
        organized_rules.append(rule)

    for source in source_rules:
        source.is_active = False
        source.version += 1

    db.commit()
    for rule in organized_rules:
        db.refresh(rule)
    return organized_rules
