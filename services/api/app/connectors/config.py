from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _ConfigBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyConnectorConfig(_ConfigBase):
    pass


class TencentConnectorConfig(_ConfigBase):
    target: str = "24"

    @field_validator("target")
    @classmethod
    def require_numeric_target(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.isdecimal():
            raise ValueError("target must be a numeric Tencent channel ID")
        return normalized


class WeiboConnectorConfig(_ConfigBase):
    include_reposts: bool = True


class TiebaConnectorConfig(_ConfigBase):
    forum_name: str = Field(min_length=1)
    max_thread_pages: int = Field(default=5, ge=1, le=20)
    max_post_pages: int = Field(default=100, ge=1, le=200)

    @field_validator("forum_name")
    @classmethod
    def normalize_forum_name(cls, value: str) -> str:
        return value.strip()


CONFIG_MODELS: dict[str, type[_ConfigBase]] = {
    "baidu_tieba": TiebaConnectorConfig,
    "manual": EmptyConnectorConfig,
    "riot_official": EmptyConnectorConfig,
    "tencent_lol": TencentConnectorConfig,
    "weibo": WeiboConnectorConfig,
    "x_twitter": EmptyConnectorConfig,
}


def validate_connector_config(
    connector_type: str, config: dict[str, Any]
) -> dict[str, Any]:
    try:
        model = CONFIG_MODELS[connector_type]
    except KeyError as exc:
        raise ValueError(f"connector is not registered: {connector_type}") from exc
    return model.model_validate(config).model_dump()


def validate_external_key(connector_type: str, external_key: str | None) -> str | None:
    if connector_type == "x_twitter":
        if not external_key:
            raise ValueError("x_twitter source requires an external_key username")
    elif connector_type in {"weibo", "baidu_tieba"}:
        if not external_key or not external_key.isdecimal():
            raise ValueError(
                f"{connector_type} source requires a numeric external_key"
            )
    return external_key
