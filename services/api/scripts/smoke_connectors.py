"""Opt-in live fetch smoke test; never runs as part of pytest."""

import asyncio
import os

from app.connectors.base import ConnectorRequest, ConnectorSource
from app.connectors.riot_official import RiotOfficialConnector
from app.connectors.tencent_lol import TencentLolConnector
from app.connectors.x_twitter import XTwitterConnector


def request(connector_type: str, *, external_key: str | None = None) -> ConnectorRequest:
    return ConnectorRequest(
        source=ConnectorSource(
            id=0,
            name=f"Live smoke: {connector_type}",
            connector_type=connector_type,
            external_key=external_key,
            base_url=None,
            connector_config={},
        ),
        limit=1,
        since=None,
        options={},
    )


async def main() -> None:
    if os.getenv("CONNECTOR_LIVE_SMOKE") != "1":
        raise SystemExit("Set CONNECTOR_LIVE_SMOKE=1 to access live provider websites")

    for connector in (RiotOfficialConnector(), TencentLolConnector()):
        try:
            items = await connector.collect(request(connector.connector_type))
            item = items[0]
            print(
                connector.connector_type,
                item.external_id,
                item.native_title,
                len(item.content_blocks),
            )
        except Exception as exc:
            print(connector.connector_type, type(exc).__name__, str(exc))

    try:
        username = os.getenv("X_SMOKE_USERNAME", "riotphroxzon")
        items = await XTwitterConnector().collect(
            request("x_twitter", external_key=username)
        )
        print("x_twitter", items[0].external_id, items[0].native_title)
    except Exception as exc:
        print("x_twitter", type(exc).__name__, str(exc))


if __name__ == "__main__":
    asyncio.run(main())
