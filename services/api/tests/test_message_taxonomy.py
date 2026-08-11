from app.domain.importance import (
    IMPORTANCE_POLICY_VERSION,
    PROFILE_ROUTES,
    SCORE_BANDS,
    calculate_importance,
    calculate_message_priority,
    derive_importance_profile,
)
from app.domain.message_taxonomy import (
    CONTENT_FORM_RULES,
    MESSAGE_TYPE_ORDER,
    MESSAGE_TYPE_RULES,
    MESSAGE_TYPES,
    PRODUCT_RULES,
    TOPIC_RULES,
    classification_catalog,
    classification_error,
    content_analysis_catalog,
    content_analysis_error,
)


def _message_definition(catalog: dict[str, object], code: str) -> str:
    return next(
        value["definition"]
        for value in catalog["message_types"]
        if value["code"] == code
    )


def test_catalog_contract_and_source_specific_candidates() -> None:
    content_catalog = content_analysis_catalog()
    message_codes = [rule.code for rule in MESSAGE_TYPE_RULES]
    topic_codes = [rule.code for rule in TOPIC_RULES]

    assert [value["code"] for value in content_catalog["products"]] == [
        rule.code for rule in PRODUCT_RULES
    ]
    assert [value["code"] for value in content_catalog["content_forms"]] == [
        rule.code for rule in CONTENT_FORM_RULES
    ]
    assert len(message_codes) == 26
    assert set(message_codes) == MESSAGE_TYPES
    assert len(MESSAGE_TYPE_ORDER) == len(set(MESSAGE_TYPE_ORDER)) == 26
    assert len(topic_codes) == len(set(topic_codes)) == 26
    assert "生成非空摘要" in next(
        value["definition"]
        for value in content_catalog["content_forms"]
        if value["code"] == "media_only"
    )
    other_product = next(
        value["definition"]
        for value in content_catalog["products"]
        if value["code"] == "other_lol_product"
    )
    assert "Riftbound（裂界征伐）" in other_product
    assert "具体产品名" in other_product

    official = classification_catalog(products=["lol_pc"], is_official_source=True)
    unofficial = classification_catalog(products=["lol_pc"], is_official_source=False)
    assert [value["code"] for value in official["message_types"]] == [
        "game_patch_notes",
        "game_official_preview",
        "game_announcement",
        "game_notice",
        "game_promotion_interaction",
        "unknown",
    ]
    assert [value["code"] for value in unofficial["message_types"]] == [
        "game_community_notice",
        "game_leak",
        "game_community_discussion",
        "game_community_promotion_interaction",
        "unknown",
    ]
    assert "实质信息披露" in _message_definition(official, "game_official_preview")
    assert "可独立核验" in _message_definition(official, "game_announcement")
    assert "测试服免责声明" in _message_definition(
        official, "game_promotion_interaction"
    )
    assert "没有形成独立完整的正式告知" in _message_definition(
        official, "game_promotion_interaction"
    )
    assert "带货" in _message_definition(
        unofficial, "game_community_promotion_interaction"
    )
    assert "esports_matches" not in {value["code"] for value in official["topics"]}
    assert "champions" in {value["code"] for value in official["topics"]}


def test_content_form_validation_boundaries() -> None:
    assert content_analysis_error(products=["lol_pc"], content_form="original") is None
    assert content_analysis_error(products=["lol_pc"], content_form="media_only") == (
        "纯媒体或纯链接消息的 products 必须为 unknown"
    )
    assert (
        classification_error(
            products=["unknown"],
            content_form="media_only",
            message_type="unknown",
            topics=["unknown"],
            is_official_source=False,
        )
        is None
    )
    assert classification_error(
        products=["lol_pc"],
        content_form="media_only",
        message_type="unknown",
        topics=["unknown"],
        is_official_source=False,
    ) == "纯媒体或纯链接消息的 products 必须为 unknown"


def test_classification_validation_boundaries() -> None:
    cases = [
        (
            {
                "products": ["lol_pc"],
                "content_form": "original",
                "message_type": "game_patch_notes",
                "topics": ["balance_gameplay"],
                "is_official_source": True,
            },
            None,
        ),
        (
            {
                "products": ["lol_pc"],
                "content_form": "original",
                "message_type": "game_patch_notes",
                "topics": ["balance_gameplay"],
                "is_official_source": False,
            },
            "message_type 不适用于当前信源性质",
        ),
        (
            {
                "products": ["lol_esports"],
                "content_form": "original",
                "message_type": "game_patch_notes",
                "topics": ["esports_matches"],
                "is_official_source": True,
            },
            "message_type 不适用于所选 products",
        ),
        (
            {
                "products": ["tft"],
                "content_form": "original",
                "message_type": "game_announcement",
                "topics": ["champions"],
                "is_official_source": True,
            },
            "topic=champions 不适用于所选 products",
        ),
    ]
    base = {
        "products": ["lol_pc"],
        "content_form": "original",
        "message_type": "game_announcement",
        "topics": ["champions"],
        "is_official_source": True,
    }
    cases.extend(
        [
            (base | {"products": ["valorant"]}, "products 包含不受支持的值"),
            (base | {"content_form": "thread"}, "content_form 包含不受支持的值"),
            (base | {"message_type": "breaking_news"}, "message_type 包含不受支持的值"),
            (base | {"topics": ["release_date"]}, "topics 包含不受支持的值"),
        ]
    )

    for payload, expected in cases:
        assert classification_error(**payload) == expected
    assert (
        classification_error(
            products=["lol_pc"],
            content_form="original",
            message_type="game_promotion_interaction",
            topics=["activities_rewards"],
            is_official_source=True,
        )
        is None
    )
    assert classification_error(
        products=["lol_pc"],
        content_form="original",
        message_type="game_promotion_interaction",
        topics=["activities_rewards"],
        is_official_source=False,
    ) == "message_type 不适用于当前信源性质"
    assert (
        classification_error(
            products=["lol_pc"],
            content_form="original",
            message_type="game_community_promotion_interaction",
            topics=["activities_rewards"],
            is_official_source=False,
        )
        is None
    )
    assert classification_error(
        products=["lol_pc"],
        content_form="original",
        message_type="game_community_promotion_interaction",
        topics=["activities_rewards"],
        is_official_source=True,
    ) == "message_type 不适用于当前信源性质"


def test_importance_policy_is_classification_native() -> None:
    assert IMPORTANCE_POLICY_VERSION == "importance-v11-repost-weekly-rotation"
    assert set(PROFILE_ROUTES) == MESSAGE_TYPES
    routed_profiles = {
        route.profile for routes in PROFILE_ROUTES.values() for route in routes
    }
    specialized_profiles = {
        "patch_hotfix",
        "patch_full_preview",
        "weekly_free_champion_rotation",
        "shop_cosmetic_rotation",
        "shop_rare_cosmetic",
        "shop_bulk_refresh",
        "activity_free_skin",
        "esports_playoffs",
        "esports_final",
        "worlds_regular",
        "worlds_key",
    }
    assert set(SCORE_BANDS) == routed_profiles | specialized_profiles
    assert derive_importance_profile(
        message_type="game_official_preview",
        topics=["balance_gameplay", "champions"],
        content="26.17 Full Preview",
    ) == "patch_full_preview"
    assert derive_importance_profile(
        message_type="game_promotion_interaction",
        topics=["game_modes"],
        content="经典模式现已上线",
    ) == "promotion_gameplay"
    assert derive_importance_profile(
        message_type="game_promotion_interaction",
        topics=["game_modes", "activities_rewards"],
        content="活动宣传",
    ) == derive_importance_profile(
        message_type="game_promotion_interaction",
        topics=["activities_rewards", "game_modes"],
        content="活动宣传",
    ) == "promotion_activity"
    assert derive_importance_profile(
        message_type="game_community_promotion_interaction",
        topics=["activities_rewards", "game_modes"],
        content="社区活动宣传",
    ) == "promotion_activity"
    assert derive_importance_profile(
        message_type="esports_promotion_interaction",
        topics=["esports_matches", "esports_broadcast"],
        content="每日精彩集锦",
    ) == "esports_promotion"
    assert derive_importance_profile(
        message_type="game_community_notice",
        topics=["balance_gameplay"],
        content="社区提醒",
    ) == "community_game_notice"
    assert derive_importance_profile(
        message_type="game_announcement",
        topics=["champions"],
        content="7月17日周免英雄更新公告",
    ) == "weekly_free_champion_rotation"

    features = {
        "scale": "standard",
        "audience_region": "cn",
        "competition_region": "none",
        "prominence": "normal",
        "skin_tier": "none",
        "is_bulk_update": False,
        "evidence": ["经典模式"],
    }
    promotion_score, promotion_calculation = calculate_importance(
        features,
        message_type="game_promotion_interaction",
        topics=["game_modes"],
        content="经典模式宣传",
    )
    community_promotion_score, community_promotion_calculation = calculate_importance(
        features,
        message_type="game_community_promotion_interaction",
        topics=["game_modes"],
        content="经典模式社区宣传",
    )
    announcement_score, announcement_calculation = calculate_importance(
        features,
        message_type="game_announcement",
        topics=["game_modes"],
        content="经典模式正式公告",
    )
    assert promotion_score == 0.52
    assert community_promotion_score == promotion_score
    assert community_promotion_calculation["importance_profile"] == "promotion_gameplay"
    assert announcement_score == 0.86
    assert promotion_score < announcement_score
    assert promotion_calculation["importance_profile"] == "promotion_gameplay"
    assert announcement_calculation["importance_profile"] == "gameplay_announcement"

    priority, calculation = calculate_message_priority(
        promotion_score,
        content_form="original",
        audience_region="cn",
    )
    assert priority == promotion_score
    assert calculation["modifiers"] == []

    repost_score, repost_calculation = calculate_importance(
        features,
        message_type="game_promotion_interaction",
        topics=["game_modes"],
        content_form="repost",
        content="经典模式宣传",
    )
    assert repost_score == 0.44
    assert repost_calculation["profile_score"] == promotion_score
    assert repost_calculation["modifiers"][-1]["key"] == "content_form"

    repost_priority, repost_priority_calculation = calculate_message_priority(
        repost_score,
        content_form="repost",
        audience_region="cn",
    )
    assert repost_priority == repost_score
    assert repost_priority_calculation["modifiers"] == []

    weekly_features = {**features, "is_bulk_update": True}
    weekly_score, weekly_calculation = calculate_importance(
        weekly_features,
        message_type="game_announcement",
        topics=["champions"],
        content="7月17日周免英雄更新公告",
    )
    assert weekly_score == 0.5
    assert weekly_calculation["importance_profile"] == "weekly_free_champion_rotation"
    assert weekly_calculation["modifiers"] == []
