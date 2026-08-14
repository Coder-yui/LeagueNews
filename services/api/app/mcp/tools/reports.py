from datetime import date
from typing import Any

from mcp.server import MCPServer

from app.mcp.tools._common import mcp_db_session
from app.mcp.tools.news import news_list_projection
from app.services.daily_report_read import (
    get_latest_published_daily_report,
    get_published_daily_report,
)


def _report_projection(report: dict[str, Any]) -> dict[str, Any]:
    return {
        **report,
        "sections": {
            section: [
                news_list_projection(message, include_related_event_ids=False)
                for message in messages
            ]
            for section, messages in report["sections"].items()
        },
    }


def register(mcp: MCPServer) -> None:
    @mcp.tool(
        name="get_daily_report",
        description=(
            "Read the already-published LeagueNews daily report for an ISO calendar date. "
            "The response preserves the lolpc, esports, tft, and other sections and their "
            "compact published message summaries. Call get_news_item for full content or "
            "evidence. Reading never generates or withdraws a report."
        ),
        structured_output=True,
    )
    def get_daily_report(report_date: date) -> dict[str, Any]:
        with mcp_db_session() as db:
            report = get_published_daily_report(db, report_date)
        if report is None:
            raise ValueError(f"no published daily report for {report_date.isoformat()}")
        return _report_projection(report)

    @mcp.tool(
        name="get_latest_daily_report",
        description=(
            "Read the newest already-published LeagueNews daily report. If none exists, "
            "return a clear not-found tool error. The report contains compact published "
            "message summaries; call get_news_item for full content or evidence. Reading "
            "never generates a report."
        ),
        structured_output=True,
    )
    def get_latest_daily_report() -> dict[str, Any]:
        with mcp_db_session() as db:
            report = get_latest_published_daily_report(db)
        if report is None:
            raise ValueError("no published daily report available")
        return _report_projection(report)
