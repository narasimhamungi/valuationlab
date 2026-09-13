"""
Cross-repo integration contract with Trellis.

These tests require a real Trellis install and live SEC EDGAR access. They are skipped
automatically without them, so the offline suite stays fast and network-free -- but in
CI's integration job they run for real.

What they exist to catch: ValuationLab reads specific field names out of Trellis's
forecast output. If Trellis renames `operating_income`, stops emitting
`depreciation_amortization`, or changes what `run_forecast` returns, every number
ValuationLab produces silently becomes wrong or the pipeline crashes far downstream.
A golden snapshot cannot catch that -- it's frozen, so it would keep passing forever
against a schema that no longer exists. Only running against real Trellis catches it.
"""

import os

import pytest

trellis = pytest.importorskip("trellis", reason="Trellis not installed")

pytestmark = pytest.mark.skipif(
    not os.environ.get("TRELLIS_USER_AGENT"),
    reason="TRELLIS_USER_AGENT not set -- SEC EDGAR access unavailable",
)

# Fields ValuationLab reads out of Trellis's forecast dict. This list IS the contract.
REQUIRED_FORECAST_FIELDS = (
    "revenue", "operating_income", "interest_expense", "income_tax_expense",
    "accounts_receivable", "inventory", "accounts_payable",
    "capex", "depreciation_amortization",
    "long_term_debt", "cash_and_equivalents",
)

# Base-year selection must test the HISTORICAL table, which is a different set:
# interest_expense is frequently untagged (Nike is the worked example -- it's why
# Trellis flags an interest_rate assumption), but project_year emits it unconditionally.
# Requiring it here made every year fail and max() raise on an empty sequence.
REQUIRED_BASE_YEAR_FIELDS = tuple(
    f for f in REQUIRED_FORECAST_FIELDS if f != "interest_expense")
NIKE_CIK = 320187  # smallest/fastest of the validated companies for a CI run


@pytest.fixture(scope="module")
def trellis_output():
    from trellis.companies import get_profile
    from trellis.forecast import derive_drivers_from_history, run_forecast
    from trellis.ingest import fetch_all
    from trellis.statements import build_annual_table, fill_derived_gaps

    profile = get_profile(NIKE_CIK)
    raw = fetch_all(NIKE_CIK)
    result = build_annual_table(raw)
    table = result.table
    fill_derived_gaps(table)

    stub_years = {s.year for s in result.stub_periods}
    candidates = [y for y in table if y not in stub_years
                  and all(f in table[y] for f in REQUIRED_BASE_YEAR_FIELDS)]
    if not candidates:
        newest = max(table)
        missing = [f for f in REQUIRED_BASE_YEAR_FIELDS if f not in table[newest]]
        pytest.fail(f"No year has the base-year fields. FY{newest} missing: {missing}")
    base_year = max(candidates)
    drivers = derive_drivers_from_history(table, base_year, lookback_years=5,
                                           overrides=profile.overrides,
                                           exclude_years=stub_years)
    forecast = run_forecast(table, base_year, drivers, years=5)
    return {"table": table, "base_year": base_year, "drivers": drivers,
            "forecast": forecast}


def test_forecast_emits_every_field_valuationlab_reads(trellis_output):
    for year, data in trellis_output["forecast"].items():
        missing = [f for f in REQUIRED_FORECAST_FIELDS if f not in data]
        assert not missing, (
            f"Trellis forecast FY{year} is missing {missing}. ValuationLab's DCF reads "
            f"these directly -- the integration contract is broken, not just degraded.")


def test_drivers_expose_the_wacc_inputs_valuationlab_needs(trellis_output):
    drivers = trellis_output["drivers"]
    assert hasattr(drivers, "interest_rate"), "cost of debt input gone from Drivers"
    assert hasattr(drivers, "tax_rate"), "tax rate input gone from Drivers"
    assert 0.0 <= drivers.tax_rate < 1.0
    assert 0.0 <= drivers.interest_rate < 0.30


def test_dcf_runs_end_to_end_on_real_trellis_output(trellis_output):
    from valuationlab.dcf import MarketData, run_dcf

    base = trellis_output["table"][trellis_output["base_year"]]
    market = MarketData(share_price=75.0, shares_outstanding=1_480_000_000.0,
                         beta=1.05, risk_free_rate=0.042, equity_risk_premium=0.05,
                         as_of="2026-09-01")
    result = run_dcf(base, trellis_output["forecast"], market,
                      trellis_output["drivers"].interest_rate,
                      trellis_output["drivers"].tax_rate, terminal_growth=0.025)

    assert result.enterprise_value > 0
    assert result.implied_share_price > 0
    assert result.enterprise_value == pytest.approx(
        result.pv_explicit_fcf + result.pv_terminal_value)


def test_comps_runs_end_to_end_on_real_trellis_output(trellis_output):
    from valuationlab.comps import build_peer_multiple
    from valuationlab.marketdata import MarketSnapshot

    base = trellis_output["table"][trellis_output["base_year"]]
    peer = build_peer_multiple(
        "NKE", "Nike, Inc.", trellis_output["base_year"], base,
        MarketSnapshot("NKE", 75.0, 1_480_000_000.0, "2026-09-01", "integration-test"))
    assert peer.ev_revenue > 0
    assert peer.enterprise_value > 0
