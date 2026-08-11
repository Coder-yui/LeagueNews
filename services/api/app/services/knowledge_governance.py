from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.workflow import GlossaryTerm, KnowledgeRule, MESSAGE_KNOWLEDGE_TYPES


def detect_active_knowledge_conflicts(db: Session) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    rule_groups: dict[tuple[str, str, str], list[KnowledgeRule]] = defaultdict(list)
    for rule in db.scalars(
        select(KnowledgeRule).where(
            KnowledgeRule.lifecycle_status == "active",
            KnowledgeRule.knowledge_type.in_(MESSAGE_KNOWLEDGE_TYPES),
        )
    ):
        constraint_key = rule.correction_data.get("constraint_key")
        if isinstance(constraint_key, str) and constraint_key.strip():
            rule_groups[
                (
                    rule.knowledge_type,
                    rule.scope,
                    constraint_key.strip().casefold(),
                )
            ].append(rule)
    for key, rules in rule_groups.items():
        normalized_texts = {" ".join(rule.rule_text.casefold().split()) for rule in rules}
        if len(normalized_texts) > 1:
            conflicts.append(
                {
                    "kind": "knowledge_rule",
                    "key": list(key),
                    "ids": sorted(rule.id for rule in rules),
                    "message": "active rules share a constraint_key but specify different text",
                }
            )

    glossary_groups: dict[tuple[str, str], list[GlossaryTerm]] = defaultdict(list)
    for term in db.scalars(select(GlossaryTerm).where(GlossaryTerm.is_active.is_(True))):
        glossary_groups[(term.scope, term.source_term.casefold())].append(term)
    for key, terms in glossary_groups.items():
        translations = {term.preferred_translation.casefold() for term in terms}
        if len(translations) > 1:
            conflicts.append(
                {
                    "kind": "glossary",
                    "key": list(key),
                    "ids": sorted(term.id for term in terms),
                    "message": "active glossary terms have different preferred translations",
                }
            )
    return conflicts
