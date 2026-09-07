"""Shared deterministic interpretation of the importance model output."""

from app.domain.importance import (
    IMPORTANCE_POLICY_VERSION,
    calculate_importance,
    calculate_message_priority,
    derive_importance_profile,
    normalize_importance_features,
)


def calculate_message_importance(result, *, content_form: str, scoring_content: str):
    result_payload = result.model_dump(mode="json")
    message_type = str(result_payload.pop("message_type"))
    topics = list(result_payload.pop("topics"))
    profile = derive_importance_profile(
        message_type=message_type, topics=topics, content=scoring_content
    )
    features = normalize_importance_features(
        result_payload, profile=profile, content=scoring_content
    )
    score, calculation = calculate_importance(
        features,
        message_type=message_type,
        topics=topics,
        content_form=content_form,
        content=scoring_content,
    )
    priority_score, priority_calculation = calculate_message_priority(
        score,
        content_form=content_form,
        audience_region=str(features["audience_region"]),
    )
    evidence_values = list(features["evidence"])
    dimensions = {
        name: {"value": features[name], "evidence": evidence_values[0]}
        for name in (
            "scale",
            "audience_region",
            "competition_region",
            "prominence",
            "skin_tier",
        )
    }
    dimensions["importance_profile"] = {
        "value": calculation["importance_profile"],
        "evidence": evidence_values[0],
    }
    return {
        "message_type": message_type,
        "topics": topics,
        "importance_score": score,
        "importance_evidence": evidence_values,
        "importance_dimensions": dimensions,
        "importance_policy_version": IMPORTANCE_POLICY_VERSION,
        "calculation": calculation,
        "priority_score": priority_score,
        "priority_calculation": priority_calculation,
    }
