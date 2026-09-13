import math

import pytest

from valuationlab.dcf import (
    MarketData,
    build_ufcf_series,
    compute_wacc,
    run_dcf,
    sensitivity_grid,
    unlevered_fcf,
)

# Synthetic base year, shaped exactly like a row of Trellis's real AnnualTable /
# forecast dict (verified field names against trellis/src/trellis/forecast.py's
# project_year return statement, not guessed).
BASE_YEAR = {
    "revenue": 10_000.0, "operating_income": 2_000.0, "interest_expense": 100.0,
    "income_tax_expense": 380.0, "net_income": 1_520.0,
    "accounts_receivable": 900.0, "inventory": 1_200.0, "accounts_payable": 800.0,
    "capex": 500.0, "depreciation_amortization": 400.0,
    "long_term_debt": 2_000.0, "cash_and_equivalents": 1_500.0,
}


def make_forecast_year(revenue, growth_from_base=0.0):
    """Minimal forecast-year dict with the same field set project_year emits, scaled
    off BASE_YEAR by a simple uniform growth factor -- enough to exercise the DCF
    module without re-implementing Trellis's own driver logic in the test."""
    scale = 1 + growth_from_base
    return {
        "revenue": revenue,
        "operating_income": BASE_YEAR["operating_income"] * scale,
        "interest_expense": BASE_YEAR["interest_expense"],
        "income_tax_expense": BASE_YEAR["income_tax_expense"] * scale,
        "accounts_receivable": BASE_YEAR["accounts_receivable"] * scale,
        "inventory": BASE_YEAR["inventory"] * scale,
        "accounts_payable": BASE_YEAR["accounts_payable"] * scale,
        "capex": BASE_YEAR["capex"] * scale,
        "depreciation_amortization": BASE_YEAR["depreciation_amortization"] * scale,
        "long_term_debt": BASE_YEAR["long_term_debt"],
        "cash_and_equivalents": BASE_YEAR["cash_and_equivalents"] * scale,
    }


MARKET = MarketData(
    share_price=100.0, shares_outstanding=50.0, beta=1.1, risk_free_rate=0.04,
    equity_risk_premium=0.05, as_of="2026-09-01",
)


def test_unlevered_fcf_matches_hand_calculation():
    fy1 = make_forecast_year(revenue=10_500.0, growth_from_base=0.05)
    result = unlevered_fcf(BASE_YEAR, fy1, BASE_YEAR, year=1)

    pretax = fy1["operating_income"] - fy1["interest_expense"]
    expected_tax_rate = fy1["income_tax_expense"] / pretax
    expected_nopat = fy1["operating_income"] * (1 - expected_tax_rate)
    nwc_now = fy1["accounts_receivable"] + fy1["inventory"] - fy1["accounts_payable"]
    nwc_prior = (BASE_YEAR["accounts_receivable"] + BASE_YEAR["inventory"]
                 - BASE_YEAR["accounts_payable"])
    expected_ufcf = (expected_nopat + fy1["depreciation_amortization"]
                     - fy1["capex"] - (nwc_now - nwc_prior))

    assert result.effective_tax_rate == pytest.approx(expected_tax_rate)
    assert result.nopat == pytest.approx(expected_nopat)
    assert result.ufcf == pytest.approx(expected_ufcf)


def test_ufcf_series_uses_prior_forecast_year_not_base_year_after_year_one():
    forecast = {
        1: make_forecast_year(10_500.0, 0.05),
        2: make_forecast_year(11_025.0, 0.10),
    }
    series = build_ufcf_series(BASE_YEAR, forecast)
    assert len(series) == 2
    # year 2's delta_nwc should be computed against year 1's forecast values, not
    # BASE_YEAR -- confirm it's NOT equal to what you'd get diffing straight from base
    nwc_y2 = (forecast[2]["accounts_receivable"] + forecast[2]["inventory"]
              - forecast[2]["accounts_payable"])
    nwc_base = (BASE_YEAR["accounts_receivable"] + BASE_YEAR["inventory"]
                - BASE_YEAR["accounts_payable"])
    wrong_delta_vs_base = nwc_y2 - nwc_base
    assert series[1].delta_nwc != pytest.approx(wrong_delta_vs_base)


def test_wacc_computation():
    result = compute_wacc(MARKET, BASE_YEAR, interest_rate=0.05, tax_rate=0.19)
    expected_coe = 0.04 + 1.1 * 0.05
    expected_cod_at = 0.05 * (1 - 0.19)
    market_cap = 100.0 * 50.0
    expected_we = market_cap / (market_cap + 2_000.0)
    expected_wd = 2_000.0 / (market_cap + 2_000.0)
    expected_wacc = expected_we * expected_coe + expected_wd * expected_cod_at

    assert result.cost_of_equity == pytest.approx(expected_coe)
    assert result.cost_of_debt_aftertax == pytest.approx(expected_cod_at)
    assert result.weight_equity == pytest.approx(expected_we)
    assert result.wacc == pytest.approx(expected_wacc)


def test_run_dcf_end_to_end_reconciles():
    forecast = {y: make_forecast_year(10_000 * 1.05 ** y, growth_from_base=0.05 * y)
                for y in range(1, 6)}
    result = run_dcf(BASE_YEAR, forecast, MARKET, interest_rate=0.05, tax_rate=0.19,
                      terminal_growth=0.025)

    # PV of explicit FCF + PV of terminal value should equal enterprise value exactly
    assert result.enterprise_value == pytest.approx(
        result.pv_explicit_fcf + result.pv_terminal_value)
    # equity bridge should be internally consistent
    assert result.equity_value == pytest.approx(
        result.enterprise_value - result.net_debt)
    assert result.implied_share_price == pytest.approx(
        result.equity_value / MARKET.shares_outstanding)
    assert result.implied_share_price > 0


def test_wacc_not_exceeding_growth_raises():
    forecast = {1: make_forecast_year(10_500.0, 0.05)}
    with pytest.raises(ValueError, match="must exceed"):
        run_dcf(BASE_YEAR, forecast, MARKET, interest_rate=0.05, tax_rate=0.19,
                terminal_growth=0.20)  # CAPM WACC here is well under 20%


def test_negative_terminal_growth_rejected():
    forecast = {1: make_forecast_year(10_500.0, 0.05)}
    with pytest.raises(ValueError, match="stationarity"):
        run_dcf(BASE_YEAR, forecast, MARKET, interest_rate=0.05, tax_rate=0.19,
                terminal_growth=-0.01)


def test_sensitivity_grid_shape_and_invalid_cells():
    forecast = {y: make_forecast_year(10_000 * 1.05 ** y, growth_from_base=0.05 * y)
                for y in range(1, 6)}
    grid = sensitivity_grid(BASE_YEAR, forecast, MARKET, interest_rate=0.05,
                             tax_rate=0.19, wacc_range=[0.06, 0.08, 0.10],
                             growth_range=[0.02, 0.03, 0.12])
    assert len(grid) == 9
    # g=0.12 exceeds every wacc tested -> every one of those cells must be nan, not
    # a silently wrong number
    for w in [0.06, 0.08, 0.10]:
        assert math.isnan(grid[(w, 0.12)])
    # a valid, ordinary cell should be a real finite number
    assert math.isfinite(grid[(0.10, 0.02)])


def test_sensitivity_grid_monotonic_in_wacc():
    """Holding growth fixed, a higher discount rate must produce a lower implied
    share price -- this is a real economic invariant, not just a smoke test."""
    forecast = {y: make_forecast_year(10_000 * 1.05 ** y, growth_from_base=0.05 * y)
                for y in range(1, 6)}
    grid = sensitivity_grid(BASE_YEAR, forecast, MARKET, interest_rate=0.05,
                             tax_rate=0.19, wacc_range=[0.07, 0.09, 0.11],
                             growth_range=[0.02])
    prices = [grid[(w, 0.02)] for w in [0.07, 0.09, 0.11]]
    assert prices[0] > prices[1] > prices[2]
