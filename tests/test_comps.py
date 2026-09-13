import pytest

from valuationlab.comps import (
    build_peer_multiple,
    ebitda_proxy,
    implied_value,
    summarize,
)
from valuationlab.marketdata import MarketSnapshot


def peer_year(revenue, operating_income, da, debt=10_000.0, cash=5_000.0):
    return {"revenue": revenue, "operating_income": operating_income,
            "depreciation_amortization": da, "long_term_debt": debt,
            "cash_and_equivalents": cash}


def snap(ticker, price, shares, as_of="2026-09-01"):
    return MarketSnapshot(ticker=ticker, share_price=price, shares_outstanding=shares,
                          as_of=as_of, source="snapshot:test.json")


def test_ebitda_proxy_sums_reported_lines():
    assert ebitda_proxy(peer_year(100.0, 20.0, 5.0)) == pytest.approx(25.0)


def test_ebitda_proxy_refuses_missing_component():
    with pytest.raises(KeyError, match="depreciation_amortization"):
        ebitda_proxy({"operating_income": 20.0})


def test_build_peer_multiple_math_and_provenance():
    data = peer_year(revenue=50_000.0, operating_income=12_000.0, da=3_000.0,
                      debt=20_000.0, cash=8_000.0)
    p = build_peer_multiple("PFE", "Pfizer Inc.", 2025, data, snap("PFE", 30.0, 5_000.0))

    assert p.market_cap == pytest.approx(150_000.0)
    assert p.net_debt == pytest.approx(12_000.0)
    assert p.enterprise_value == pytest.approx(162_000.0)
    assert p.ev_revenue == pytest.approx(162_000.0 / 50_000.0)
    assert p.ev_ebitda == pytest.approx(162_000.0 / 15_000.0)
    # both provenance dates must survive onto the multiple
    assert p.fiscal_year == 2025
    assert p.market_as_of == "2026-09-01"


def test_non_positive_revenue_rejected():
    with pytest.raises(ValueError, match="non-positive revenue"):
        build_peer_multiple("X", "X", 2025, peer_year(0.0, 10.0, 2.0),
                             snap("X", 10.0, 100.0))


def test_summarize_reports_range_and_median():
    peers = [
        build_peer_multiple("A", "A", 2025, peer_year(100.0, 20.0, 5.0, 0.0, 0.0),
                             snap("A", 10.0, 20.0)),   # EV 200 / EBITDA 25 = 8.0x
        build_peer_multiple("B", "B", 2025, peer_year(100.0, 20.0, 5.0, 0.0, 0.0),
                             snap("B", 10.0, 25.0)),   # EV 250 / 25 = 10.0x
        build_peer_multiple("C", "C", 2025, peer_year(100.0, 20.0, 5.0, 0.0, 0.0),
                             snap("C", 10.0, 30.0)),   # EV 300 / 25 = 12.0x
    ]
    r = summarize(peers, basis="ev_ebitda")
    assert r.low == pytest.approx(8.0)
    assert r.median == pytest.approx(10.0)
    assert r.high == pytest.approx(12.0)
    assert r.included == ["A", "B", "C"]
    assert r.excluded == []


def test_negative_ebitda_peer_excluded_with_a_stated_reason():
    peers = [
        build_peer_multiple("A", "A", 2025, peer_year(100.0, 20.0, 5.0, 0.0, 0.0),
                             snap("A", 10.0, 20.0)),
        build_peer_multiple("D", "D", 2025, peer_year(100.0, -30.0, 5.0, 0.0, 0.0),
                             snap("D", 10.0, 20.0)),  # EBITDA proxy = -25
    ]
    r = summarize(peers, basis="ev_ebitda")
    assert r.included == ["A"]
    assert len(r.excluded) == 1
    assert r.excluded[0][0] == "D"
    assert "non-positive" in r.excluded[0][1]


def test_mean_median_divergence_is_visible_with_one_distorted_peer():
    """A single peer with a charge-depressed operating income should move the mean far
    more than the median -- the gap is the signal, so both must be reported."""
    peers = [
        build_peer_multiple("A", "A", 2025, peer_year(100.0, 20.0, 5.0, 0.0, 0.0),
                             snap("A", 10.0, 20.0)),   # 8.0x
        build_peer_multiple("B", "B", 2025, peer_year(100.0, 20.0, 5.0, 0.0, 0.0),
                             snap("B", 10.0, 22.0)),   # 8.8x
        build_peer_multiple("C", "C", 2025, peer_year(100.0, 2.0, 3.0, 0.0, 0.0),
                             snap("C", 10.0, 20.0)),   # EBITDA 5 -> 40.0x, distorted
    ]
    r = summarize(peers, basis="ev_ebitda")
    assert r.median == pytest.approx(8.8)
    assert r.mean > r.median * 1.5  # mean dragged far above median by the outlier


def test_summarize_rejects_bad_basis():
    with pytest.raises(ValueError, match="ev_revenue or ev_ebitda"):
        summarize([], basis="pe_ratio")


def test_summarize_refuses_empty_result_set():
    peers = [build_peer_multiple("D", "D", 2025, peer_year(100.0, -30.0, 5.0, 0.0, 0.0),
                                  snap("D", 10.0, 20.0))]
    with pytest.raises(ValueError, match="No peers remain"):
        summarize(peers, basis="ev_ebitda")


def test_implied_value_applies_net_debt_bridge():
    peers = [
        build_peer_multiple("A", "A", 2025, peer_year(100.0, 20.0, 5.0, 0.0, 0.0),
                             snap("A", 10.0, 20.0)),   # 8.0x
        build_peer_multiple("C", "C", 2025, peer_year(100.0, 20.0, 5.0, 0.0, 0.0),
                             snap("C", 10.0, 30.0)),   # 12.0x
    ]
    r = summarize(peers, basis="ev_ebitda")
    subject = peer_year(revenue=200.0, operating_income=40.0, da=10.0,
                         debt=100.0, cash=40.0)  # EBITDA 50, net debt 60
    out = implied_value(subject, r, shares_outstanding=10.0)

    # low: 8.0 * 50 = 400 EV, less 60 net debt = 340 equity, /10 shares = 34.0
    assert out["implied_price_low"] == pytest.approx(34.0)
    # high: 12.0 * 50 = 600 EV, less 60 = 540, /10 = 54.0
    assert out["implied_price_high"] == pytest.approx(54.0)
    assert out["implied_price_low"] < out["implied_price_median"] < out["implied_price_high"]


def test_implied_value_on_revenue_basis_uses_revenue_denominator():
    peers = [
        build_peer_multiple("A", "A", 2025, peer_year(100.0, 20.0, 5.0, 0.0, 0.0),
                             snap("A", 10.0, 20.0)),   # EV 200 / rev 100 = 2.0x
        build_peer_multiple("C", "C", 2025, peer_year(100.0, 20.0, 5.0, 0.0, 0.0),
                             snap("C", 10.0, 30.0)),   # 3.0x
    ]
    r = summarize(peers, basis="ev_revenue")
    subject = peer_year(revenue=200.0, operating_income=40.0, da=10.0,
                         debt=100.0, cash=40.0)
    out = implied_value(subject, r, shares_outstanding=10.0)
    # low: 2.0 * 200 revenue = 400 EV, less 60 = 340, /10 = 34.0
    assert out["implied_price_low"] == pytest.approx(34.0)
