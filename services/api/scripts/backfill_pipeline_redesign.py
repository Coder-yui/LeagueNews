from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.database import SessionLocal
from app.domain.credibility import (
    CREDIBILITY_POLICY_VERSION,
    calculate_item_credibility,
    posterior_reliability,
    reliability_prior,
)
from app.models.credibility import SourceReliabilityHistory
from app.models.event import Event, EventMessage, EventRevision
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.services.claims import link_item_claims_to_event
from app.services.event_aggregation import refresh_event_projection


@dataclass(slots=True)
class BackfillReport:
    items_scored: int = 0
    events_created: int = 0
    events_updated: int = 0
    events_withdrawn: int = 0
    memberships_added: int = 0
    memberships_reactivated: int = 0
    memberships_withdrawn: int = 0


def _item_text(item: NormalizedItem) -> str:
    return " ".join(
        (
            item.translated_title or item.normalized_title,
            item.summary,
            item.normalized_text,
        )
    )


def infer_legacy_profile(item: NormalizedItem) -> tuple[str, str, str]:
    text = _item_text(item)
    lowered = text.casefold()
    source = item.raw_item.source
    prior = reliability_prior(
        source_name=source.name,
        connector_type=source.connector_type,
        external_key=source.external_key,
        authority=source.connector_config.get("authority_level"),
    )
    roster_signal = (
        any(team in text for team in ("WBG", "WB战队", "BLG", "TES"))
        and any(
            term in text
            for term in (
                "打野",
                "人选",
                "人员变动",
                "互换",
                "回归",
                "首发",
                "休息",
                "转会",
            )
        )
    )
    esports_signal = any(
        league in lowered for league in ("lpl", "lck", "lcp")
    ) and any(
        term in text
        for term in ("赛", "对战", "战胜", "击败", "比分", "晋级")
    )
    if roster_signal:
        topic = "roster"
    elif "神话商城" in text or "兑换代码" in text or "兑换码" in text:
        topic = "activity"
    elif esports_signal:
        topic = "esports"
    elif any(
        term in text
        for term in ("不停机更新", "版本更新", "BUG", "PBE", "经典匹配")
    ):
        topic = "patch"
    else:
        topic = item.primary_topic or "other"

    if prior.label == "official":
        if topic == "esports" and any(
            term in text for term in ("战胜", "击败", "比分", "赛果")
        ):
            content_type = "match_result"
        elif any(
            term in text for term in ("公告", "预告", "赛程", "更新")
        ):
            content_type = "official_notice"
        else:
            content_type = "official_fact"
        certainty = "confirmed"
    elif prior.label == "top_insider" and topic == "roster":
        if "确认下细节" in text:
            content_type = "insider_confirmed"
            certainty = "likely"
        else:
            content_type = "insider_rumor"
            certainty = "speculative"
    elif prior.label == "data_mining":
        content_type = (
            "aggregation"
            if "转推" in text or "repost" in lowered
            else "data_mine"
        )
        certainty = "likely"
    else:
        content_type = (
            "aggregation"
            if source.connector_type in {"weibo", "baidu_tieba", "x_twitter"}
            else "community_noise"
        )
        certainty = (
            "speculative"
            if any(
                term in text
                for term in ("传闻", "爆料", "官宣为准", "未定", "理论上")
            )
            else "likely"
        )
    return content_type, topic, certainty


def _backfill_item_scoring(
    db: Session,
    items: list[NormalizedItem],
    report: BackfillReport,
) -> None:
    histories = {
        history.source_id: history
        for history in db.scalars(select(SourceReliabilityHistory))
    }
    for item in items:
        content_type, topic, certainty = infer_legacy_profile(item)
        source = item.raw_item.source
        history = histories.get(source.id)
        if history is None:
            prior = reliability_prior(
                source_name=source.name,
                connector_type=source.connector_type,
                external_key=source.external_key,
                authority=source.connector_config.get("authority_level"),
            )
            source_reliability = prior.mean
        else:
            source_reliability = posterior_reliability(
                confirmed_count=history.confirmed_count,
                refuted_count=history.refuted_count,
                alpha=history.alpha,
                beta=history.beta,
            )
        score, components = calculate_item_credibility(
            source_reliability=source_reliability,
            certainty=certainty,
            content_type=content_type,
        )
        item.content_type = content_type
        item.primary_topic = topic
        item.credibility_score = score
        item.credibility = (
            "official"
            if content_type in {
                "official_fact",
                "official_notice",
                "match_result",
            }
            else "unverified"
        )
        item.credibility_components = components
        item.credibility_policy_version = CREDIBILITY_POLICY_VERSION
        item.credibility_evidence = [
            "历史数据按四因子公式回填；未改写源事实或 content_blocks",
            (
                f"source={source_reliability:.3f}, certainty={certainty}, "
                f"content_type={content_type}, staleness=0"
            ),
        ]
        report.items_scored += 1


def _active_item_ids(event: Event) -> set[int]:
    return {
        message.normalized_item_id
        for message in event.messages
        if message.membership_status == "active"
    }


def _record_revision(
    db: Session,
    event: Event,
    *,
    change_note: str,
    evidence: dict[str, object],
    is_new: bool = False,
) -> None:
    if not is_new:
        event.current_revision += 1
    db.add(
        EventRevision(
            event_id=event.id,
            revision=event.current_revision,
            title=event.title,
            summary=event.summary,
            change_note=change_note,
            evidence_snapshot=evidence,
        )
    )


def _ensure_projection(
    db: Session,
    report: BackfillReport,
    *,
    aggregation_key: str,
    event_type: str,
    title: str,
    summary: str,
    category: str,
    lifecycle_status: str,
    items: list[NormalizedItem],
    preferred_event: Event | None = None,
    roles: dict[int, str] | None = None,
) -> Event:
    event = db.scalar(
        select(Event).where(Event.aggregation_key == aggregation_key)
    )
    created = event is None and preferred_event is None
    event = event or preferred_event
    if event is None:
        event = Event(
            aggregation_key=aggregation_key,
            title=title,
            summary=summary,
            category=category,
            status="active",
            event_type=event_type,
            lifecycle_status=lifecycle_status,
            current_revision=1,
            latest_development="历史数据按管线重设计回填",
        )
        db.add(event)
        db.flush()
        report.events_created += 1

    metadata_changed = any(
        (
            event.aggregation_key != aggregation_key,
            event.event_type != event_type,
            event.lifecycle_status != lifecycle_status,
            event.status != "active",
        )
    )
    event.aggregation_key = aggregation_key
    event.event_type = event_type
    event.lifecycle_status = lifecycle_status
    event.status = "active"
    event.title = title
    event.summary = summary
    event.category = category
    membership_changed = False
    for item in items:
        role = (roles or {}).get(item.id, "primary")
        membership = db.get(EventMessage, (event.id, item.id))
        if membership is None:
            membership = EventMessage(
                event_id=event.id,
                normalized_item_id=item.id,
                membership_role=role,
                evidence_stance="supports",
                independence_key=f"source:{item.raw_item.source_id}",
                is_official_confirmation=item.credibility == "official",
                is_significant_update=True,
                source_published_at=item.raw_item.published_at,
            )
            db.add(membership)
            report.memberships_added += 1
            membership_changed = True
        else:
            if membership.membership_status != "active":
                report.memberships_reactivated += 1
                membership_changed = True
            if membership.membership_role != role:
                membership_changed = True
            membership.membership_status = "active"
            membership.membership_role = role
            membership.evidence_stance = "supports"
            membership.withdrawn_at = None
            membership.withdrawal_reason = None
            membership.is_official_confirmation = item.credibility == "official"
        link_item_claims_to_event(
            db,
            normalized_item_id=item.id,
            event_id=event.id,
            relation="supports",
        )
    db.flush()
    refresh_event_projection(db, event)
    if created:
        _record_revision(
            db,
            event,
            change_note="创建管线重设计历史投影",
            evidence={
                "action": "pipeline_redesign_backfill",
                "aggregation_key": aggregation_key,
                "normalized_item_ids": sorted(item.id for item in items),
            },
            is_new=True,
        )
    elif metadata_changed or membership_changed:
        _record_revision(
            db,
            event,
            change_note="升级为管线重设计事件投影",
            evidence={
                "action": "pipeline_redesign_backfill",
                "aggregation_key": aggregation_key,
                "normalized_item_ids": sorted(item.id for item in items),
            },
        )
        report.events_updated += 1
    return event


def _withdraw_other_memberships(
    db: Session,
    report: BackfillReport,
    *,
    target: Event,
    item_ids: set[int],
    eligible_event_ids: set[int],
) -> None:
    touched: set[int] = set()
    for membership in db.scalars(
        select(EventMessage).where(
            EventMessage.normalized_item_id.in_(item_ids),
            EventMessage.event_id.in_(eligible_event_ids - {target.id}),
            EventMessage.membership_status == "active",
        )
    ):
        membership.membership_status = "withdrawn"
        membership.withdrawn_at = datetime.now(UTC)
        membership.withdrawal_reason = (
            f"历史投影已合并到事件 {target.id} ({target.aggregation_key})"
        )
        touched.add(membership.event_id)
        report.memberships_withdrawn += 1
    db.flush()
    for event_id in touched:
        event = db.get(Event, event_id)
        was_active = event.status == "active"
        refresh_event_projection(db, event)
        if was_active and event.status == "withdrawn":
            report.events_withdrawn += 1
            _record_revision(
                db,
                event,
                change_note=f"旧投影已由事件 {target.id} 取代",
                evidence={
                    "action": "superseded_projection",
                    "replacement_event_id": target.id,
                },
            )


def _preferred_event(
    events: list[Event],
    item_ids: set[int],
    *,
    title_term: str | None = None,
) -> Event | None:
    candidates = [
        event
        for event in events
        if title_term is None or title_term in event.title
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda event: (len(_active_item_ids(event) & item_ids), -event.id),
    )


def _league(text: str) -> str:
    lowered = text.casefold()
    return next(
        (league for league in ("lpl", "lck", "lcp") if league in lowered),
        "unknown-league",
    )


def _local_date(item: NormalizedItem) -> str:
    published = item.raw_item.published_at
    if published is None:
        return "unknown-date"
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    return published.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()


def _backfill_event_projections(
    db: Session,
    items: list[NormalizedItem],
    events: list[Event],
    report: BackfillReport,
) -> None:
    patch_event = next(
        (event for event in events if "不停机更新" in event.title),
        None,
    )
    if patch_event is not None:
        patch_items = [
            item for item in items if item.id in _active_item_ids(patch_event)
        ]
        patch = _ensure_projection(
            db,
            report,
            aggregation_key="patch:2026-07-31-hotfix",
            event_type="patch_cycle",
            title=patch_event.title,
            summary=patch_event.summary,
            category=patch_event.category,
            lifecycle_status="completed",
            items=patch_items,
            preferred_event=patch_event,
        )
        gameplay_items = [
            item for item in patch_items if "经典模式" in _item_text(item)
        ]
        roles = {item.id: "component" for item in gameplay_items}
        _ensure_projection(
            db,
            report,
            aggregation_key="gameplay:经典模式",
            event_type="major_gameplay_change",
            title="英雄联盟经典模式上线与后续修复",
            summary="经典模式上线后持续获得官方更新、修复与内容补充。",
            category="游戏模式",
            lifecycle_status="live",
            items=gameplay_items,
            roles=roles,
        )
        refresh_event_projection(db, patch)

    wbg_items = [
        item
        for item in items
        if any(term in _item_text(item) for term in ("WBG", "WB战队"))
        and any(
            term in _item_text(item)
            for term in ("打野", "人选", "人员变动", "互换", "休息")
        )
    ]
    if wbg_items:
        wbg_ids = {item.id for item in wbg_items}
        transfer_events = [
            event
            for event in events
            if event.event_type in {"transfer", "transfer_saga"}
            and _active_item_ids(event) & wbg_ids
        ]
        preferred = _preferred_event(
            transfer_events,
            wbg_ids,
            title_term="WBG新打野",
        ) or _preferred_event(transfer_events, wbg_ids)
        wbg = _ensure_projection(
            db,
            report,
            aggregation_key="WBG:jungle:2026off",
            event_type="transfer_saga",
            title="WBG 2026 休赛期打野变动时间线",
            summary="汇总 WBG 打野人选、互换与人员变动的未确认消息。",
            category="电竞转会",
            lifecycle_status="unconfirmed",
            items=wbg_items,
            preferred_event=preferred,
        )
        _withdraw_other_memberships(
            db,
            report,
            target=wbg,
            item_ids=wbg_ids,
            eligible_event_ids={event.id for event in transfer_events},
        )

    shop_items = [item for item in items if "神话商城" in _item_text(item)]
    shop_events = [
        event
        for event in events
        if event.event_type in {"activity", "shop_rotation"}
        and _active_item_ids(event)
        & {item.id for item in shop_items}
    ]
    shop_groups: dict[int, list[NormalizedItem]] = {}
    for item in shop_items:
        published = item.raw_item.published_at
        if published is None:
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        week = published.astimezone(ZoneInfo("Asia/Shanghai")).isocalendar().week
        shop_groups.setdefault(week, []).append(item)
    for week, grouped in shop_groups.items():
        grouped_ids = {item.id for item in grouped}
        preferred = _preferred_event(shop_events, grouped_ids)
        shop = _ensure_projection(
            db,
            report,
            aggregation_key=f"mythic_shop:week:{week}",
            event_type="shop_rotation",
            title=f"国服神话商城 2026 年第 {week} 周轮换",
            summary="同一自然周的神话商城每日内容归并为一个周轮换事件。",
            category="游戏商城",
            lifecycle_status="live",
            items=grouped,
            preferred_event=preferred,
        )
        _withdraw_other_memberships(
            db,
            report,
            target=shop,
            item_ids=grouped_ids,
            eligible_event_ids={event.id for event in shop_events},
        )

    regular_events = [
        event
        for event in events
        if "常规赛" in event.title or "LCK夏季赛" in event.title
    ]
    regular_item_ids = set().union(
        *(_active_item_ids(event) for event in regular_events)
    ) if regular_events else set()
    match_groups: dict[str, list[NormalizedItem]] = {}
    for item in items:
        if item.id not in regular_item_ids:
            continue
        league = _league(_item_text(item))
        if league == "unknown-league":
            league = next(
                (
                    _league(event.title)
                    for event in regular_events
                    if item.id in _active_item_ids(event)
                    and _league(event.title) != "unknown-league"
                ),
                league,
            )
        key = f"{league}:{_local_date(item)}"
        match_groups.setdefault(key, []).append(item)
    used_regular_event_ids: set[int] = set()
    for key, grouped in sorted(match_groups.items()):
        grouped_ids = {item.id for item in grouped}
        league, date_key = key.split(":", 1)
        _year, month, day = (int(part) for part in date_key.split("-"))
        date_term = f"{month}月{day}日"
        matching_events = [
            event
            for event in regular_events
            if event.id not in used_regular_event_ids
            and league in event.title.casefold()
        ]
        preferred = _preferred_event(
            matching_events,
            grouped_ids,
            title_term=date_term,
        ) or _preferred_event(matching_events, grouped_ids)
        if preferred is not None:
            used_regular_event_ids.add(preferred.id)
        daily = _ensure_projection(
            db,
            report,
            aggregation_key=key,
            event_type="daily_matches",
            title=f"{key} 常规赛比赛日",
            summary="同一赛区同一比赛日的赛程、赛果与相关信息。",
            category="电竞赛事",
            lifecycle_status="completed",
            items=grouped,
            preferred_event=preferred,
        )
        _withdraw_other_memberships(
            db,
            report,
            target=daily,
            item_ids=grouped_ids,
            eligible_event_ids={event.id for event in regular_events},
        )

    qualification = next(
        (event for event in events if "Team Secret" in event.title),
        None,
    )
    if qualification is not None:
        qualification_items = [
            item
            for item in items
            if item.id in _active_item_ids(qualification)
        ]
        _ensure_projection(
            db,
            report,
            aggregation_key="lcp:worlds2026:qualification",
            event_type="qualification_saga",
            title=qualification.title,
            summary=qualification.summary,
            category=qualification.category,
            lifecycle_status="confirmed",
            items=qualification_items,
            preferred_event=qualification,
        )


def run_backfill(db: Session, *, apply: bool = False) -> BackfillReport:
    items = list(
        db.scalars(
            select(NormalizedItem)
            .where(NormalizedItem.publication_status == "published")
            .options(
                selectinload(NormalizedItem.raw_item).selectinload(
                    RawItem.source
                )
            )
            .order_by(NormalizedItem.id)
        )
    )
    events = list(
        db.scalars(
            select(Event)
            .options(selectinload(Event.messages))
            .order_by(Event.id)
        )
    )
    report = BackfillReport()
    _backfill_item_scoring(db, items, report)
    db.flush()
    _backfill_event_projections(db, items, events, report)
    if apply:
        db.commit()
    else:
        db.rollback()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill historical data for pipeline redesign P0-P3."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit changes. The default is a rollback-only dry run.",
    )
    args = parser.parse_args()
    with SessionLocal() as db:
        report = run_backfill(db, apply=args.apply)
    mode = "applied" if args.apply else "dry-run"
    print(mode, asdict(report))


if __name__ == "__main__":
    main()
