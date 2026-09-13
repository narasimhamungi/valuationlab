import pytest

from valuationlab.segments import (
    JNJ_FY2025_SEGMENTS,
    PeerGroup,
    Segment,
    build_sotp,
    format_sotp,
)

MEDTECH = PeerGroup(
    name="MedTech", tickers=("MDT", "BDX", "SYK", "BSX"),
    low_multiple=3.0, median_multiple=3.9, high_multiple=4.6,
    basis="EV/Revenue, FY2025 annual denominators", provenance="Trellis + market")

PHARMA = PeerGroup(
    name="Large-cap pharma", tickers=("PFE", "MRK", "ABBV", "BMY"),
    low_multiple=3.4, median_multiple=6.0, high_multiple=8.4,
    basis="EV/Revenue, FY2025 annual denominators", provenance="Trellis + market",
    disqualified_reason="peer multiples span 2.5x; adding LLY and AMGN widened it to "
                        "4.7x, so the dispersion is economic rather than small-sample")


def test_jnj_segments_reconcile_to_consolidated():
    """The integrity check the whole module rests on: segment revenue is hand-entered
    from the 10-K while consolidated comes from XBRL, so they fail independently.
    Raw dollars, matching Trellis's convention -- a units bug here (millions vs raw
    dollars) previously slipped past an absolute-tolerance version of this check."""
    assert sum(s.revenue for s in JNJ_FY2025_SEGMENTS) == pytest.approx(94_193_000_000.0)


def test_jnj_segments_are_raw_dollars_not_millions():
    """Regression: JNJ_FY2025_SEGMENTS was originally written in millions (matching the
    10-K's own convention) while every other figure in this pipeline -- Trellis's
    consolidated revenue, net_debt, comps denominators -- is raw dollars. That mismatch
    passed silently until build_sotp's reconciliation caught a 94-billion-dollar gap on
    a live run. Each segment should be on the order of tens of billions, not tens of
    thousands."""
    for s in JNJ_FY2025_SEGMENTS:
        assert s.revenue > 1e9, (
            f"{s.name} revenue is {s.revenue:,.0f} -- looks like millions-scale, not "
            f"raw dollars. This is the exact bug that broke a live SOTP run.")


def test_every_segment_carries_a_source():
    for s in JNJ_FY2025_SEGMENTS:
        assert s.source and "10-K" in s.source


def test_build_sotp_sums_legs_and_bridges_to_equity():
    r = build_sotp(JNJ_FY2025_SEGMENTS,
                    {"Innovative Medicine": PHARMA, "MedTech": MEDTECH},
                    consolidated_revenue=94_193_000_000.0, net_debt=8_000_000_000.0,
                    shares_outstanding=2_409_898_597.0)

    assert r.total_ev_mid == pytest.approx(
        6.0 * 60_401_000_000.0 + 3.9 * 33_792_000_000.0)
    assert r.implied_price_mid == pytest.approx(
        (r.total_ev_mid - 8_000_000_000.0) / 2_409_898_597.0)
    assert r.implied_price_low < r.implied_price_mid < r.implied_price_high
    # A real dollar-per-share figure, not a number a million times too small or large --
    # this is the check that would have caught the units bug directly.
    assert 50.0 < r.implied_price_mid < 2000.0


def test_reconciliation_failure_raises_rather_than_valuing_around_it():
    bad = (Segment("A", 50_000_000_000.0, "10-K"), Segment("B", 30_000_000_000.0, "10-K"))
    with pytest.raises(ValueError, match="does not reconcile"):
        build_sotp(bad, {"A": MEDTECH, "B": MEDTECH},
                   consolidated_revenue=94_193_000_000.0, net_debt=0.0,
                   shares_outstanding=100.0)


def test_reconciliation_catches_a_million_times_unit_mismatch():
    """Regression, reproducing the exact live failure: segments correctly summing to
    the right real-world figure but expressed in millions against a raw-dollar
    consolidated figure must fail loudly, not pass because the relative gap looks small
    in the wrong direction or the absolute gap happens to be 'small' on some scale."""
    millions_scale = (Segment("Innovative Medicine", 60_401.0, "10-K, millions"),
                       Segment("MedTech", 33_792.0, "10-K, millions"))
    with pytest.raises(ValueError, match="does not reconcile"):
        build_sotp(millions_scale, {"Innovative Medicine": PHARMA, "MedTech": MEDTECH},
                   consolidated_revenue=94_193_000_000.0, net_debt=0.0,
                   shares_outstanding=100.0)


def test_missing_peer_group_raises():
    with pytest.raises(ValueError, match="No peer group"):
        build_sotp(JNJ_FY2025_SEGMENTS, {"MedTech": MEDTECH},
                   consolidated_revenue=94_193_000_000.0, net_debt=0.0,
                   shares_outstanding=100.0)


def test_unfit_leg_is_tracked_and_revenue_share_reported():
    r = build_sotp(JNJ_FY2025_SEGMENTS,
                    {"Innovative Medicine": PHARMA, "MedTech": MEDTECH},
                    consolidated_revenue=94_193_000_000.0, net_debt=8_000_000_000.0,
                    shares_outstanding=2_409_898_597.0)
    assert r.unfit_legs == ["Innovative Medicine"]
    assert not r.is_fully_fit
    # MedTech is 33,792M of 94,193M -- the fit share must reflect revenue weight, not
    # a count of legs, or a tiny sound segment would look like a sound valuation
    assert r.fit_share_of_revenue == pytest.approx(33_792_000_000.0 / 94_193_000_000.0)


def test_fully_fit_when_every_group_clears():
    r = build_sotp(JNJ_FY2025_SEGMENTS,
                    {"Innovative Medicine": MEDTECH, "MedTech": MEDTECH},
                    consolidated_revenue=94_193_000_000.0, net_debt=8_000_000_000.0,
                    shares_outstanding=2_409_898_597.0)
    assert r.is_fully_fit
    assert r.fit_share_of_revenue == pytest.approx(1.0)


def test_peer_group_spread_and_fitness():
    assert MEDTECH.spread == pytest.approx(4.6 / 3.0)
    assert MEDTECH.is_fit
    assert not PHARMA.is_fit


def test_format_states_the_split_rather_than_one_price():
    r = build_sotp(JNJ_FY2025_SEGMENTS,
                    {"Innovative Medicine": PHARMA, "MedTech": MEDTECH},
                    consolidated_revenue=94_193_000_000.0, net_debt=8_000_000_000.0,
                    shares_outstanding=2_409_898_597.0)
    text = format_sotp(r)
    assert "LEG NOT FIT" in text
    assert "is NOT a valuation" in text
    assert "36% of revenue" in text  # 33,792M / 94,193M
    assert "reconciliation" in text
