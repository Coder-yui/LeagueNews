import pytest

from app.schemas.workflow import (
    GlossaryTermUpdate,
    KnowledgeRuleCreate,
    KnowledgeRuleUpdate,
)


def test_rule_update_allows_manual_type_scope_and_text_changes() -> None:
    update = KnowledgeRuleUpdate(
        knowledge_type="analysis",
        scope="lol-esports",
        rule_text="官方赛事赛果应分类为赛事公告。",
    )

    assert update.knowledge_type == "analysis"
    assert update.scope == "lol-esports"
    assert update.rule_text.startswith("官方赛事")


def test_rule_update_rejects_unknown_knowledge_type() -> None:
    with pytest.raises(ValueError):
        KnowledgeRuleUpdate(knowledge_type="unknown")


def test_relevance_rules_cannot_be_created_or_retyped() -> None:
    with pytest.raises(ValueError):
        KnowledgeRuleCreate(
            knowledge_type="relevance",
            rule_text="历史相关性规则不得进入新判断",
        )
    with pytest.raises(ValueError):
        KnowledgeRuleUpdate(knowledge_type="relevance")


def test_term_update_allows_all_manually_maintained_fields() -> None:
    update = GlossaryTermUpdate(
        source_term="Ability Haste",
        preferred_translation="技能急速",
        forbidden_translations=["能力急速"],
        scope="lol",
        notes="采用国服客户端译名。",
    )

    assert update.source_term == "Ability Haste"
    assert update.preferred_translation == "技能急速"
    assert update.forbidden_translations == ["能力急速"]
    assert update.notes == "采用国服客户端译名。"
