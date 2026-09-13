import pytest

from valuationlab.precedent import (
    BMY_CELGENE_2019,
    JNJ_ACTELION_2017,
    MATURE_REVENUE_DEALS,
    DealTier,
    apply_multiple,
)


def test_actelion_multiples_match_sourced_figures():
    assert JNJ_ACTELION_2017.ev_revenue == pytest.approx(29_600.0 / 2_412.0)
    assert JNJ_ACTELION_2017.ev_ebitda_proxy == pytest.approx(29_600.0 / 992.0)
    assert JNJ_ACTELION_2017.tier is DealTier.MATURE_REVENUE


def test_celgene_multiples_match_sourced_figures():
    assert BMY_CELGENE_2019.ev_revenue == pytest.approx(74_000.0 / 15_281.0)
    assert BMY_CELGENE_2019.ev_ebitda_proxy == pytest.approx(74_000.0 / 6_511.0)


def test_actelion_and_celgene_multiples_diverge_materially():
    """The whole point of sourcing two 'mature revenue' deals instead of one: prove
    they are NOT interchangeable, so nothing downstream can quietly average them into
    a single 'pharma multiple' without confronting this gap."""
    revenue_multiple_ratio = JNJ_ACTELION_2017.ev_revenue / BMY_CELGENE_2019.ev_revenue
    assert revenue_multiple_ratio > 2.0  # Actelion prices at more than double
    # Celgene's revenue multiple despite both being real, sourced, mature-revenue deals


def test_apply_multiple_returns_both_bases_independently():
    result = apply_multiple(JNJ_ACTELION_2017, subject_revenue_usd_mm=88_000.0,
                             subject_ebitda_proxy_usd_mm=25_000.0)
    assert result["implied_ev_from_revenue_multiple"] == pytest.approx(
        JNJ_ACTELION_2017.ev_revenue * 88_000.0)
    assert result["implied_ev_from_ebitda_multiple"] == pytest.approx(
        JNJ_ACTELION_2017.ev_ebitda_proxy * 25_000.0)
    # the two bases must not collapse to the same number by construction
    assert result["implied_ev_from_revenue_multiple"] != pytest.approx(
        result["implied_ev_from_ebitda_multiple"])


def test_mature_revenue_deals_registry_contains_both_sourced_deals():
    assert JNJ_ACTELION_2017 in MATURE_REVENUE_DEALS
    assert BMY_CELGENE_2019 in MATURE_REVENUE_DEALS
    assert len(MATURE_REVENUE_DEALS) == 2
    assert all(d.tier is DealTier.MATURE_REVENUE for d in MATURE_REVENUE_DEALS)
