from dataclasses import dataclass
from typing import Final

CONTENT_TYPE_PRIORS: Final = {
    "official_fact": 1.0,
    "official_notice": 0.95,
    "match_result": 0.95,
    "data_mine": 0.85,
    "insider_confirmed": 0.8,
    "insider_rumor": 0.65,
    "community_noise": 0.3,
}
STATEMENT_CERTAINTY: Final = {
    "confirmed": 1.0,
    "likely": 0.8,
    "speculative": 0.55,
}
CREDIBILITY_POLICY_VERSION: Final = "credibility-v2-four-factor-beta"


@dataclass(frozen=True, slots=True)
class ReliabilityPrior:
    alpha: float
    beta: float
    label: str

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)


def reliability_prior(
    *,
    source_name: str,
    connector_type: str,
    external_key: str | None,
    authority: int | None = None,
) -> ReliabilityPrior:
    normalized_name = source_name.casefold()
    normalized_key = (external_key or "").casefold()
    if "skinspotlights" in normalized_name or "spideraxe" in normalized_name:
        return ReliabilityPrior(8.0, 2.0, "data_mining")
    if any(
        name in normalized_name
        for name in ("召唤师park", "尧阿尧", "riotphroxzon")
    ):
        return ReliabilityPrior(7.5, 2.5, "top_insider")
    if (
        authority is not None
        and authority >= 100
        or connector_type in {"riot_official", "tencent_lol"}
        or normalized_key
        in {
            "leagueoflegends",
            "lolesports",
            "5756404150",
            "5720474518",
        }
    ):
        return ReliabilityPrior(10.0, 0.0, "official")
    if connector_type in {"weibo", "x_twitter", "baidu_tieba"}:
        return ReliabilityPrior(5.5, 4.5, "secondary_or_aggregation")
    return ReliabilityPrior(3.5, 6.5, "community")


def posterior_reliability(
    *,
    confirmed_count: int,
    refuted_count: int,
    alpha: float,
    beta: float,
) -> float:
    numerator = confirmed_count + alpha
    denominator = confirmed_count + refuted_count + alpha + beta
    return round(numerator / denominator, 6) if denominator else 0.0


def calibrate_reliability(
    *,
    confirmed_count: int,
    refuted_count: int,
    was_confirmed: bool,
) -> tuple[int, int]:
    if was_confirmed:
        return confirmed_count + 1, refuted_count
    return confirmed_count, refuted_count + 1


def calculate_item_credibility(
    *,
    source_reliability: float,
    certainty: str,
    content_type: str | None,
    staleness_penalty: float = 0.0,
    aggregation_upstream_prior: float | None = None,
) -> tuple[float, dict[str, object]]:
    source_factor = max(0.0, min(1.0, source_reliability))
    certainty_factor = STATEMENT_CERTAINTY.get(certainty, STATEMENT_CERTAINTY["speculative"])
    if content_type == "aggregation":
        content_factor = max(
            0.0,
            min(
                1.0,
                0.55
                if aggregation_upstream_prior is None
                else aggregation_upstream_prior,
            ),
        )
        content_factor_source = (
            "aggregation_fallback"
            if aggregation_upstream_prior is None
            else "upstream_inherited"
        )
    else:
        content_factor = CONTENT_TYPE_PRIORS.get(content_type or "", 0.5)
        content_factor_source = "content_type_prior"
    penalty = max(0.0, min(1.0, staleness_penalty))
    freshness = 1.0 - penalty
    score = round(
        source_factor * certainty_factor * content_factor * freshness,
        6,
    )
    return score, {
        "source_reliability": {
            "value": source_factor,
            "source": "beta_posterior",
        },
        "statement_certainty": {
            "value": certainty_factor,
            "label": certainty,
        },
        "content_type_prior": {
            "value": content_factor,
            "label": content_type,
            "source": content_factor_source,
        },
        "staleness": {
            "penalty": penalty,
            "freshness_factor": freshness,
        },
        "formula": (
            "source_reliability × statement_certainty × "
            "content_type_prior × (1 - staleness_penalty)"
        ),
        "score": score,
        "policy_version": CREDIBILITY_POLICY_VERSION,
    }
