import pytest

from app.domain.credibility import (
    calculate_item_credibility,
    calibrate_reliability,
    posterior_reliability,
    reliability_prior,
)


def test_source_priors_match_documented_cold_start_values() -> None:
    official = reliability_prior(
        source_name="Riot Games Official",
        connector_type="riot_official",
        external_key="leagueoflegends.com",
    )
    insider = reliability_prior(
        source_name="召唤师Park",
        connector_type="weibo",
        external_key="2522098777",
    )
    data_mining = reliability_prior(
        source_name="SkinSpotlights (@SkinSpotlights)",
        connector_type="x_twitter",
        external_key="skinspotlights",
    )
    community = reliability_prior(
        source_name="Community",
        connector_type="manual",
        external_key=None,
    )

    assert official.mean == 1.0
    assert insider.mean == 0.75
    assert data_mining.mean == 0.8
    assert community.mean == 0.35


def test_four_factor_product_orders_speculation_confirmation_and_official() -> None:
    speculative, speculative_components = calculate_item_credibility(
        source_reliability=0.75,
        certainty="speculative",
        content_type="insider_rumor",
    )
    confirmed, _ = calculate_item_credibility(
        source_reliability=0.75,
        certainty="likely",
        content_type="insider_confirmed",
    )
    official, _ = calculate_item_credibility(
        source_reliability=1.0,
        certainty="confirmed",
        content_type="official_fact",
    )

    assert speculative == pytest.approx(0.268125)
    assert confirmed == pytest.approx(0.48)
    assert official == 1.0
    assert speculative < confirmed < official
    assert {
        "source_reliability",
        "statement_certainty",
        "content_type_prior",
        "staleness",
    } <= speculative_components.keys()


def test_beta_calibration_updates_posterior_without_overwriting_prior() -> None:
    confirmed_count, refuted_count = calibrate_reliability(
        confirmed_count=0,
        refuted_count=0,
        was_confirmed=True,
    )
    assert posterior_reliability(
        confirmed_count=confirmed_count,
        refuted_count=refuted_count,
        alpha=7.5,
        beta=2.5,
    ) == pytest.approx(8.5 / 11)

    confirmed_count, refuted_count = calibrate_reliability(
        confirmed_count=confirmed_count,
        refuted_count=refuted_count,
        was_confirmed=False,
    )
    assert (confirmed_count, refuted_count) == (1, 1)
    assert posterior_reliability(
        confirmed_count=confirmed_count,
        refuted_count=refuted_count,
        alpha=7.5,
        beta=2.5,
    ) == pytest.approx(8.5 / 12)
