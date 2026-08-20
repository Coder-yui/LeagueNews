from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.event_types import EVENT_LIFECYCLES


class _PatchModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def require_change(self) -> "_PatchModel":
        if not self.model_fields_set:
            raise ValueError("at least one field must be revised")
        return self


class MessageEditorialPatch(_PatchModel):
    normalized_title: str | None = Field(default=None, min_length=1, max_length=500)
    normalized_text: str | None = None
    summary: str | None = None
    entities: list[dict[str, Any]] | None = None
    products: list[str] | None = Field(default=None, min_length=1, max_length=3)
    message_type: str | None = Field(default=None, min_length=1, max_length=80)
    topics: list[str] | None = Field(default=None, min_length=1)
    content_form: str | None = Field(default=None, min_length=1, max_length=30)
    importance_score: float | None = Field(default=None, ge=0, le=1)
    priority_score: float | None = Field(default=None, ge=0, le=1)
    translated_title: str | None = Field(default=None, max_length=500)
    translated_text: str | None = None
    translated_content_blocks: list[dict[str, Any]] | None = None


class EventEditorialPatch(_PatchModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    current_summary: str | None = None
    products: list[str] | None = Field(default=None, min_length=1)
    canonical_anchors: dict[str, Any] | None = None
    latest_development: str | None = None
    key_facts: list[dict[str, Any]] | None = None
    lifecycle_status: str | None = None

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "EventEditorialPatch":
        if (
            self.lifecycle_status is not None
            and self.lifecycle_status not in EVENT_LIFECYCLES
        ):
            raise ValueError("unsupported event lifecycle")
        return self


class ManualRevisionMetadata(BaseModel):
    editor_id: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(min_length=8, max_length=255)
    lock_edited_fields: bool = True


class ManualMessageRevisionCommand(BaseModel):
    normalized_item_id: int = Field(ge=1)
    expected_revision: int = Field(ge=1)
    patch: MessageEditorialPatch
    metadata: ManualRevisionMetadata


class ManualEventRevisionCommand(BaseModel):
    event_id: int = Field(ge=1)
    expected_revision: int = Field(ge=1)
    patch: EventEditorialPatch
    metadata: ManualRevisionMetadata


class EditorialRevisionResult(BaseModel):
    target_id: int = Field(ge=1)
    revision: int = Field(ge=1)
    changed_fields: list[str]
    idempotent_replay: bool = False
