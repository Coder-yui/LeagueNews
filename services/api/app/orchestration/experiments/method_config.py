"""Resolve candidate method configuration for online-compatible backends."""

from typing import Any

from app.methods import MethodAssemblyConfig


def configured_backend(backend: Any, parameters: dict[str, Any]) -> Any:
    payload = parameters.get("method_config")
    if payload is None:
        return backend
    if not isinstance(payload, dict):
        raise ValueError("candidate parameters.method_config must be an object")
    config = MethodAssemblyConfig.model_validate(payload)
    builder = getattr(backend, "with_method_config", None)
    if builder is None:
        raise ValueError("backend does not support candidate method_config")
    return builder(config)
