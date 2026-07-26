from app.connectors.registry import connector_registry


def test_all_builtin_connectors_are_registered() -> None:
    assert connector_registry.registered_types() == [
        "baidu_tieba",
        "manual",
        "riot_official",
        "tencent_lol",
        "weibo",
        "x_twitter",
    ]
