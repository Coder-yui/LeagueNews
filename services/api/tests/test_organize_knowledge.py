import asyncio

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base
from app.models.workflow import KnowledgeRule
from app.services.llm import KnowledgeOrganizationResult, LLMClient
from app.workflows.organize_knowledge import organize_active_knowledge_rules


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def test_ai_organization_replaces_active_rules_with_traceable_compact_rules(
    monkeypatch,
) -> None:
    captured_rules: list[dict[str, object]] = []

    async def fake_organize(_self, *, rules):
        captured_rules.extend(rules)
        return KnowledgeOrganizationResult.model_validate(
            {
                "rules": [
                    {
                        "knowledge_type": "analysis",
                        "scope": "global",
                        "rule_text": "版本预览资讯的核心实体应为版本号和预览类型。",
                        "source_rule_ids": [1, 2],
                    },
                    {
                        "knowledge_type": "relevance",
                        "scope": "global",
                        "rule_text": "仅依据本条内容判断是否属于英雄联盟保留范围。",
                        "source_rule_ids": [3],
                    },
                ]
            }
        )

    monkeypatch.setattr(LLMClient, "organize_knowledge", fake_organize)

    with _session() as db:
        db.add_all(
            [
                KnowledgeRule(
                    knowledge_type="analysis",
                    scope="global",
                    rule_text="这条推文有很多英雄，但我觉得应该提取版本号。",
                ),
                KnowledgeRule(
                    knowledge_type="analysis",
                    scope="global",
                    rule_text="完整预览也应该作为实体，不要提取一堆英雄。",
                ),
                KnowledgeRule(
                    knowledge_type="relevance",
                    scope="global",
                    rule_text="不要看到设计师账号就直接判断相关，要看内容。",
                ),
            ]
        )
        db.commit()

        organized = asyncio.run(organize_active_knowledge_rules(db))

        assert len(captured_rules) == 3
        assert [rule.rule_text for rule in organized] == [
            "版本预览资讯的核心实体应为版本号和预览类型。",
            "仅依据本条内容判断是否属于英雄联盟保留范围。",
        ]
        assert organized[0].correction_data["organized_from_rule_ids"] == [1, 2]
        all_rules = list(db.scalars(select(KnowledgeRule).order_by(KnowledgeRule.id)))
        assert all(not rule.is_active for rule in all_rules[:3])
        assert all(rule.version == 2 for rule in all_rules[:3])
        assert all(rule.is_active for rule in all_rules[3:])


def test_knowledge_organization_requires_active_rules() -> None:
    with _session() as db:
        db.add(
            KnowledgeRule(
                knowledge_type="analysis",
                scope="global",
                rule_text="停用规则",
                is_active=False,
            )
        )
        db.commit()

        try:
            asyncio.run(organize_active_knowledge_rules(db))
        except ValueError as exc:
            assert str(exc) == "没有可整理的生效知识"
        else:
            raise AssertionError("expected organization to reject an empty active set")
