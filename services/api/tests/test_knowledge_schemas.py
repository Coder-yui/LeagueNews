import pytest

from app.schemas.workflow import GlossaryTermUpdate, KnowledgeRuleUpdate


def test_rule_update_allows_manual_type_scope_and_text_changes() -> None:
    update = KnowledgeRuleUpdate(
        knowledge_type="event_aggregation",
        scope="lol-esports",
        rule_text="同一赛程的赛前提醒应聚合到同一事件。",
    )

    assert update.knowledge_type == "event_aggregation"
    assert update.scope == "lol-esports"
    assert update.rule_text.startswith("同一赛程")


def test_rule_update_rejects_unknown_knowledge_type() -> None:
    with pytest.raises(ValueError):
        KnowledgeRuleUpdate(knowledge_type="unknown")


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
