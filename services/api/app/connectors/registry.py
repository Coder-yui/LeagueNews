from collections.abc import Callable

from app.connectors.base import BaseConnector

ConnectorFactory = Callable[[], BaseConnector]


class ConnectorRegistry:
    """Maps a source type to its fetch-only connector implementation."""

    def __init__(self) -> None:
        self._factories: dict[str, ConnectorFactory] = {}

    def register(self, factory: ConnectorFactory) -> None:
        connector_type = factory().connector_type
        if connector_type in self._factories:
            raise ValueError(f"connector already registered: {connector_type}")
        self._factories[connector_type] = factory

    def create(self, connector_type: str) -> BaseConnector:
        try:
            return self._factories[connector_type]()
        except KeyError as exc:
            raise LookupError(f"connector is not registered: {connector_type}") from exc

    def registered_types(self) -> list[str]:
        return sorted(self._factories)


connector_registry = ConnectorRegistry()


def register_builtin_connectors() -> None:
    from app.connectors.baidu_tieba import BaiduTiebaConnector
    from app.connectors.manual import ManualConnector
    from app.connectors.riot_official import RiotOfficialConnector
    from app.connectors.tencent_lol import TencentLolConnector
    from app.connectors.weibo import WeiboConnector
    from app.connectors.x_twitter import XTwitterConnector

    for factory in (
        BaiduTiebaConnector,
        ManualConnector,
        RiotOfficialConnector,
        TencentLolConnector,
        WeiboConnector,
        XTwitterConnector,
    ):
        if factory.connector_type not in connector_registry.registered_types():
            connector_registry.register(factory)


register_builtin_connectors()
