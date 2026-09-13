"""
Cross-module consistency diagnostic.

Unit tests verify each module against itself. This checks the thing unit tests
structurally cannot: that the three valuation methods agree on the shared plumbing they
each independently touch. Net debt is computed in THREE separate places
(dcf.run_dcf, marketdata.net_debt_from_trellis, comps.implied_value) and share count
flows through two -- any drift between them produces three per-share numbers that look
comparable and aren't, which is the exact failure this whole project exists to expose.

Run: python scripts/diagnose_consistency.py
"""

import sys

sys.path.insert(0, "src")

from valuationlab.comps import build_peer_multiple, implied_value, summarize
from valuationlab.dcf import MarketData, run_dcf
from valuationlab.marketdata import MarketSnapshot, net_debt_from_trellis
from valuationlab.precedent import MATURE_REVENUE_DEALS, apply_multiple

# JNJ-shaped synthetic base year, $ millions. Rough real-world scale so the diagnostic
# surfaces magnitude errors, not just sign errors. NOT real JNJ data -- this script
# tests plumbing consistency, not valuation accuracy.
BASE = {
    "revenue": 88_000.0, "operating_income": 21_000.0, "interest_expense": 1_000.0,
    "income_tax_expense": 3_500.0, "net_income": 16_500.0,
    "accounts_receivable": 16_000.0, "inventory": 12_000.0, "accounts_payable": 10_000.0,
    "capex": 4_500.0, "depreciation_amortization": 8_000.0,
    "long_term_debt": 30_000.0, "cash_and_equivalents": 22_000.0,
}
SHARES = 2_400.0  # millions, to match $mm financials
PRICE = 160.0

failures = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" -- {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def forecast_year(scale):
    return {
        "revenue": BASE["revenue"] * scale,
        "operating_income": BASE["operating_income"] * scale,
        "interest_expense": BASE["interest_expense"],
        "income_tax_expense": BASE["income_tax_expense"] * scale,
        "accounts_receivable": BASE["accounts_receivable"] * scale,
        "inventory": BASE["inventory"] * scale,
        "accounts_payable": BASE["accounts_payable"] * scale,
        "capex": BASE["capex"] * scale,
        "depreciation_amortization": BASE["depreciation_amortization"] * scale,
        "long_term_debt": BASE["long_term_debt"],
        "cash_and_equivalents": BASE["cash_and_equivalents"] * scale,
    }


FORECAST = {y: forecast_year(1.03 ** y) for y in range(1, 6)}

print("=== ValuationLab cross-module consistency diagnostic ===\n")

# --- 1. Net debt: three independent implementations must agree exactly --------------
nd_marketdata = net_debt_from_trellis(BASE)
dcf_result = run_dcf(BASE, FORECAST,
                      MarketData(share_price=PRICE, shares_outstanding=SHARES, beta=0.55,
                                 risk_free_rate=0.042, equity_risk_premium=0.05,
                                 as_of="2026-09-01"),
                      interest_rate=0.038, tax_rate=0.17, terminal_growth=0.025)
nd_dcf = dcf_result.net_debt
nd_comps_implied = BASE["long_term_debt"] - BASE["cash_and_equivalents"]

check("net debt: dcf vs marketdata", abs(nd_dcf - nd_marketdata) < 1e-9,
      f"dcf={nd_dcf:,.0f} marketdata={nd_marketdata:,.0f}")
check("net debt: dcf vs comps convention", abs(nd_dcf - nd_comps_implied) < 1e-9,
      f"dcf={nd_dcf:,.0f} comps={nd_comps_implied:,.0f}")

# --- 2. DCF internal bridge ---------------------------------------------------------
check("DCF: EV == PV(explicit) + PV(terminal)",
      abs(dcf_result.enterprise_value
          - (dcf_result.pv_explicit_fcf + dcf_result.pv_terminal_value)) < 1e-6)
check("DCF: equity == EV - net debt",
      abs(dcf_result.equity_value - (dcf_result.enterprise_value - dcf_result.net_debt)) < 1e-6)

tv_share = dcf_result.pv_terminal_value / dcf_result.enterprise_value
check("DCF: terminal value share < 90% of EV", tv_share < 0.90,
      f"terminal = {tv_share:.1%} of EV")

# --- 3. Comps bridge must reproduce a known multiple by construction -----------------
peers = [
    build_peer_multiple("P1", "Peer 1", 2025, BASE,
                         MarketSnapshot("P1", 160.0, 2_400.0, "2026-09-01", "diag")),
    build_peer_multiple("P2", "Peer 2", 2025, BASE,
                         MarketSnapshot("P2", 180.0, 2_400.0, "2026-09-01", "diag")),
    build_peer_multiple("P3", "Peer 3", 2025, BASE,
                         MarketSnapshot("P3", 200.0, 2_400.0, "2026-09-01", "diag")),
]
comps_range = summarize(peers, basis="ev_ebitda")
comps_out = implied_value(BASE, comps_range, shares_outstanding=SHARES)

# P2 is the median peer and uses BASE's own financials, so applying the median multiple
# back to BASE must return P2's own share price. A bridge error shows up here exactly.
check("comps: median multiple applied to the median peer returns its own price",
      abs(comps_out["implied_price_median"] - 180.0) < 1e-6,
      f"got {comps_out['implied_price_median']:.4f}, expected 180.0000")

# --- 4. All three methods land on the same order of magnitude -----------------------
subject_ebitda = BASE["operating_income"] + BASE["depreciation_amortization"]
prec_prices = []
for deal in MATURE_REVENUE_DEALS:
    ev = apply_multiple(deal, BASE["revenue"], subject_ebitda)["implied_ev_from_revenue_multiple"]
    prec_prices.append((ev - nd_dcf) / SHARES)

print(f"\n  DCF implied price:        ${dcf_result.implied_share_price:,.2f}")
print(f"  Comps implied (median):   ${comps_out['implied_price_median']:,.2f}")
for deal, p in zip(MATURE_REVENUE_DEALS, prec_prices):
    print(f"  Precedent ({deal.target[:18]:<18}): ${p:,.2f}")
print(f"  WACC: {dcf_result.wacc.wacc:.2%}  (Ke {dcf_result.wacc.cost_of_equity:.2%}, "
      f"We {dcf_result.wacc.weight_equity:.1%})\n")

all_prices = [dcf_result.implied_share_price, comps_out["implied_price_median"]] + prec_prices
check("all methods produce positive per-share values", all(p > 0 for p in all_prices))
check("precedent spread confirms tier caveat is material",
      max(prec_prices) / min(prec_prices) > 2.0,
      f"{max(prec_prices)/min(prec_prices):.1f}x spread between two sourced deals")

print()
if failures:
    print(f"DIAGNOSTIC FAILED: {len(failures)} check(s) -- {failures}")
    sys.exit(1)
print("All cross-module consistency checks passed.")
