import json
from datetime import date

import pytest

from valuationlab.marketdata import (
    CAPMInputs,
    MarketSnapshot,
    StaleSnapshotError,
    get_market_data,
    load_snapshot,
    net_debt_from_trellis,
    save_snapshot,
)

SNAP = MarketSnapshot(
    ticker="JNJ", share_price=160.0, shares_outstanding=2_400_000_000.0,
    as_of="2026-09-01", source="snapshot:test.json",
)


def test_snapshot_round_trip(tmp_path):
    path = tmp_path / "market.json"
    save_snapshot(path, [SNAP])
    loaded = load_snapshot(path, "JNJ")
    assert loaded.share_price == SNAP.share_price
    assert loaded.shares_outstanding == SNAP.shares_outstanding
    assert loaded.as_of == SNAP.as_of
    # source is re-derived from the actual file, never trusted from the payload
    assert loaded.source == "snapshot:market.json"


def test_saved_payload_omits_source_and_ticker(tmp_path):
    path = tmp_path / "market.json"
    save_snapshot(path, [SNAP])
    raw = json.loads(path.read_text())
    assert "source" not in raw["JNJ"]
    assert "ticker" not in raw["JNJ"]


def test_age_and_freshness_pass():
    snap = SNAP
    assert snap.age_days(today=date(2026, 9, 11)) == 10
    assert snap.require_fresh(max_age_days=30, today=date(2026, 9, 11)) is snap


def test_stale_snapshot_raises_loudly():
    with pytest.raises(StaleSnapshotError, match="days old"):
        SNAP.require_fresh(max_age_days=5, today=date(2026, 12, 1))


def test_missing_ticker_raises_with_available_keys(tmp_path):
    path = tmp_path / "market.json"
    save_snapshot(path, [SNAP])
    with pytest.raises(KeyError, match="PFE"):
        load_snapshot(path, "PFE")


def test_get_market_data_defaults_to_snapshot(tmp_path):
    path = tmp_path / "market.json"
    save_snapshot(path, [SNAP])
    result = get_market_data("JNJ", path)
    assert result.source.startswith("snapshot:")


def test_live_failure_falls_back_but_source_reveals_it(tmp_path, monkeypatch):
    """prefer_live=True with a broken provider must fall back silently in behaviour but
    NOT in provenance -- source has to say snapshot, or a reviewer can't tell whether
    the number was live."""
    path = tmp_path / "market.json"
    save_snapshot(path, [SNAP])

    def boom(ticker):
        raise RuntimeError("provider down")

    monkeypatch.setattr("valuationlab.marketdata.fetch_live", boom)
    result = get_market_data("JNJ", path, prefer_live=True)
    assert result.source == "snapshot:market.json"


def test_get_market_data_enforces_max_age(tmp_path):
    path = tmp_path / "market.json"
    save_snapshot(path, [MarketSnapshot(
        ticker="JNJ", share_price=160.0, shares_outstanding=2_400_000_000.0,
        as_of="2020-01-01", source="x")])
    with pytest.raises(StaleSnapshotError):
        get_market_data("JNJ", path, max_age_days=30)


def test_net_debt_from_trellis():
    actuals = {"long_term_debt": 30_000.0, "cash_and_equivalents": 22_000.0}
    assert net_debt_from_trellis(actuals) == pytest.approx(8_000.0)
    # net-cash company produces a negative net debt, which must pass through as
    # negative rather than being floored at zero
    assert net_debt_from_trellis(
        {"long_term_debt": 1_000.0, "cash_and_equivalents": 5_000.0}) == pytest.approx(-4_000.0)


def test_capm_inputs_require_a_basis_string():
    capm = CAPMInputs(beta=0.55, risk_free_rate=0.042, equity_risk_premium=0.05,
                       basis="beta: 5y monthly vs S&P 500; rf: 10y UST; ERP: Damodaran")
    assert capm.basis  # non-empty citation is the whole point of the dataclass
