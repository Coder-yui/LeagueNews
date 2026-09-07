"""Disposable event repository for one experiment case.

The repository uses the production admission, recall, validation, membership and
projection code. Its engine is always a new in-memory SQLite database; no URL or
session from the application can be supplied.
"""

from datetime import UTC, datetime
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Event, EventMention, NormalizedItem, RawItem, Source
from app.orchestration.contracts import RunMode
from app.orchestration.event_aggregation.backend import EventAggregationBackendV3
from app.orchestration.event_aggregation.graph import EventAggregationRequest
from app.services.event_method_support import apply_event_membership_transaction


def _time(value):
    if value is None:
        raise ValueError("frozen event inputs require published_at or received_at")
    parsed = datetime.fromisoformat(str(value))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


class ExperimentEventStore:
    def __init__(self, initial_state=None, *, visible_at=None):
        if visible_at:
            cutoff = _time(visible_at)
            for row in (initial_state or {}).get("candidates", []):
                if row.get("last_seen_at") and _time(row["last_seen_at"]) > cutoff:
                    raise ValueError("initial event state contains evidence beyond visible_at")
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, expire_on_commit=False)
        with self.factory() as db:
            for row in (initial_state or {}).get("candidates", []):
                db.add(
                    Event(
                        id=int(row["event_id"]),
                        title=row["title"],
                        current_summary=row.get("current_summary", row.get("summary", "")),
                        event_family=row["event_family"],
                        products=row["products"],
                        canonical_anchors=row.get("canonical_anchors", {}),
                        latest_development=row.get("latest_development", ""),
                        key_facts=row.get("key_facts", []),
                        lifecycle_status=row.get("lifecycle_status", "developing"),
                        last_seen_at=_time(row["last_seen_at"])
                        if row.get("last_seen_at")
                        else None,
                        last_material_update_at=_time(row["last_material_update_at"])
                        if row.get("last_material_update_at")
                        else None,
                    )
                )
            db.commit()

    def close(self):
        self.engine.dispose()

    async def process_item(self, payload, *, assembly, client):
        from hashlib import sha256
        from pathlib import Path
        from langgraph.checkpoint.memory import InMemorySaver
        from app.models import MediaAsset, KnowledgeRule, GlossaryTerm
        from app.orchestration.item_processing.backend import (
            ItemProcessingBackendV3,
            create_item_processing_run,
        )
        from app.orchestration.item_processing.graph import build_item_processing_graph

        raw_data = payload["raw_item"]
        source_data = payload.get("source", {})
        with self.factory() as db:
            source = Source(
                name=source_data.get("name", "frozen source"),
                connector_type=source_data.get("connector_type", "manual"),
                external_key=source_data.get("external_key"),
                is_official=source_data.get("is_official", False),
            )
            db.add(source)
            db.flush()
            raw = RawItem(
                source_id=source.id,
                external_id=str(raw_data.get("external_id", "raw-experiment")),
                native_title=raw_data.get("title"),
                language=raw_data.get("language"),
                content_blocks=raw_data["content_blocks"],
                published_at=_time(raw_data["published_at"]),
                ingested_at=_time(raw_data.get("received_at", raw_data["published_at"])),
            )
            db.add(raw)
            db.flush()
            for artifact in payload.get("media_artifacts", []):
                path = Path(artifact["uri"])
                if not path.is_absolute() or not path.is_file():
                    raise ValueError("frozen media requires an absolute local artifact path")
                if sha256(path.read_bytes()).hexdigest() != artifact["sha256"]:
                    raise ValueError("frozen media content hash does not match")
                db.add(
                    MediaAsset(
                        raw_item_id=raw.id,
                        block_index=artifact["block_index"],
                        mime_type=artifact.get("mime_type"),
                        storage_path=str(path),
                    )
                )
            for rule in payload.get("knowledge_rules", []):
                db.add(
                    KnowledgeRule(
                        knowledge_type=rule["knowledge_type"],
                        rule_text=rule["rule_text"],
                        scope="global",
                        lifecycle_status="active",
                        version=rule.get("version", 1),
                    )
                )
            for term in payload.get("glossary", []):
                db.add(
                    GlossaryTerm(
                        source_term=term["source_term"],
                        preferred_translation=term["preferred_translation"],
                        scope="global",
                    )
                )
            db.commit()
            request = create_item_processing_run(
                db,
                raw_item_id=raw.id,
                method_config=assembly.config,
            )
            request = request.model_copy(update={"run_mode": RunMode.EXPERIMENT, "batch_id": 1})
        graph = build_item_processing_graph(
            ItemProcessingBackendV3(
                self.factory, method_assembly=assembly, llm_factory=lambda: client
            ),
            checkpointer=InMemorySaver(),
        )
        result = await graph.ainvoke(
            {"request": request.model_dump(mode="json")},
            config={"configurable": {"thread_id": request.thread_id}},
        )
        if result.get("__interrupt__"):
            # A case awaiting OCR/manual review is not a completed experiment.
            raise ValueError(
                "frozen item requires manual review; curate the evidence before evaluation"
            )
        return result

    async def process(self, message, *, assembly, client):
        timestamp = _time(message.get("published_at") or message.get("received_at"))
        with self.factory() as db:
            source_data = message.get("source") or {}
            source_id = int(source_data.get("source_id", 1))
            source = db.get(Source, source_id)
            if source is None:
                source = Source(
                    id=source_id,
                    name=source_data.get("source_name", "frozen source"),
                    is_official=bool(source_data.get("is_official", False)),
                )
                db.add(source)
                db.flush()
            text = str(message.get("content") or message.get("text") or "")
            raw = RawItem(
                source_id=source.id,
                external_id=str(
                    message.get("normalized_item_id")
                    or message.get("message_id")
                    or db.query(RawItem).count() + 1
                ),
                published_at=timestamp,
                ingested_at=_time(message.get("received_at") or timestamp.isoformat()),
                content_blocks=[{"type": "paragraph", "text": text}],
            )
            db.add(raw)
            db.flush()
            item = NormalizedItem(
                raw_item_id=raw.id,
                normalized_title=message.get("title", ""),
                normalized_text=text,
                translated_title=message.get("title", ""),
                translated_text=text,
                summary=message.get("summary", ""),
                products=message.get("products", ["unknown"]),
                topics=message.get("topics", ["unknown"]),
                entities=message.get("entities", []),
                content_form=message.get("content_form", "original"),
                message_type=message.get("message_type", "unknown"),
                importance_score=message.get("importance_score", 0),
                importance_calculation=message.get("importance_calculation", {}),
                analysis_model="experiment",
                publication_status="published",
                translation_status="not_required",
            )
            db.add(item)
            db.commit()
            item_id = item.id
        backend = EventAggregationBackendV3(
            self.factory, method_assembly=assembly, llm_factory=lambda: client
        )
        request = EventAggregationRequest(
            workflow_run_id=1,
            normalized_item_id=item_id,
            normalized_item_revision=1,
            run_mode=RunMode.EXPERIMENT,
            batch_id=1,
        )
        snapshot = await backend.load_message(request)
        admission = await backend.minimal_filter(request)
        if admission.decision == "skip":
            return {
                "event_decision": {"mentions": []},
                "event_ids": [],
                "admission": admission.model_dump(mode="json"),
            }
        candidates = await backend.retrieve_candidates(request, admission)
        proposal = await backend.decide_semantics(request, snapshot, admission, candidates)
        with self.factory() as db:
            _, affected = apply_event_membership_transaction(
                db,
                item=db.get(NormalizedItem, item_id),
                result=proposal.result,
                candidates=candidates.candidates,
                refresh_metrics=False,
            )
            from app.services.event_metrics import refresh_event_metrics

            refresh_event_metrics(
                db, affected, as_of=_time(message.get("received_at") or timestamp.isoformat())
            )
            db.commit()
        return {
            "event_decision": proposal.result.model_dump(mode="json"),
            "event_ids": sorted(affected),
            "recalled_candidates": candidates.candidates,
            "suppressed_mentions": proposal.suppressed_mentions,
        }

    def snapshot(self):
        with self.factory() as db:
            return {
                "candidates": [
                    {
                        "event_id": event.id,
                        "title": event.title,
                        "current_summary": event.current_summary,
                        "event_family": event.event_family,
                        "products": event.products,
                        "canonical_anchors": event.canonical_anchors,
                        "latest_development": event.latest_development,
                        "key_facts": event.key_facts,
                        "lifecycle_status": event.lifecycle_status,
                        "importance_score": event.importance_score,
                        "credibility_score": event.credibility_score,
                        "heat_score": event.heat_score,
                        "last_seen_at": event.last_seen_at.isoformat()
                        if event.last_seen_at
                        else None,
                        "last_material_update_at": event.last_material_update_at.isoformat()
                        if event.last_material_update_at
                        else None,
                    }
                    for event in db.scalars(select(Event).order_by(Event.id))
                ],
                "memberships": [
                    {
                        "event_id": m.event_id,
                        "message_id": m.normalized_item_id,
                        "mention_index": m.mention_index,
                        "materiality": m.materiality,
                    }
                    for m in db.scalars(select(EventMention).order_by(EventMention.id))
                ],
            }
