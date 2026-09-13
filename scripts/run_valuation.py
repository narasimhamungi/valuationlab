"""
End-to-end ValuationLab run: subject company + peer set -> triangulated conclusion.

Two modes, both real:

    python scripts/run_valuation.py --offline
        Runs entirely from committed golden snapshots. No network, no Trellis install
        required. This is what a reviewer cloning the repo gets, and what CI runs.

    python scripts/run_valuation.py --live
        Pulls the subject and every peer through Trellis from SEC EDGAR, and market
        data from the configured provider. Requires TRELLIS_USER_AGENT (see Trellis's
        docs/NETWORK.md) and network access to data.sec.gov.

The two modes share every line of analysis below the data layer -- the only difference
is where `table`, `forecast`, and `MarketSnapshot` come from. That's the point of the
snapshot design: the offline path is not a simplified demo, it's the same computation
on frozen inputs.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

from valuationlab.comps import build_peer_multiple, implied_value, summarize
from valuationlab.dcf import MarketData, run_dcf, sensitivity_grid
from valuationlab.marketdata import CAPMInputs, get_market_data
from valuationlab.precedent import MATURE_REVENUE_DEALS, apply_multiple
from valuationlab.segments import (
    JNJ_FY2025_SEGMENTS,
    PeerGroup,
    build_sotp,
    format_sotp,
)
from valuationlab.triangulate import (
    Method,
    MethodRange,
    format_conclusion,
    triangulate,
)

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = ROOT / "data" / "snapshots"
MARKET_SNAPSHOT = SNAPSHOT_DIR / "market.json"

SUBJECT = {"cik": 200406, "ticker": "JNJ", "name": "Johnson & Johnson"}
# Two peer sets, matched to J&J's two reportable segments. The split is not a
# presentation choice -- measured on identical methodology, medtech multiples span 1.4x
# (EV/EBITDA) while pharma spans 2.9x, and adding LLY and AMGN widened pharma to 4.3x.
# Adding peers making a set WORSE is what rules out small-sample noise: pharma dispersion
# is driven by company-specific patent-cliff timing, which does not average.
PHARMA_PEERS = [
    {"cik": 78003, "ticker": "PFE", "name": "Pfizer Inc."},
    {"cik": 310158, "ticker": "MRK", "name": "Merck & Co., Inc."},
    {"cik": 1551152, "ticker": "ABBV", "name": "AbbVie Inc."},
    {"cik": 14272, "ticker": "BMY", "name": "Bristol-Myers Squibb Company"},
]
MEDTECH_PEERS = [
    {"cik": 1613103, "ticker": "MDT", "name": "Medtronic plc"},
    {"cik": 10795, "ticker": "BDX", "name": "Becton, Dickinson and Company"},
    {"cik": 310764, "ticker": "SYK", "name": "Stryker Corporation"},
    {"cik": 885725, "ticker": "BSX", "name": "Boston Scientific Corporation"},
]
# Abbott (CIK 1800) is deliberately absent: it fails Trellis ingestion on nine fields
# across every year 2020-2025, not a single missing tag. 3M is absent too -- its only
# cash tag bundles restricted cash with no way to net it out, and restricted cash cannot
# offset debt. Both are excluded rather than patched around.
PEERS = PHARMA_PEERS  # whole-company Trading Comparables stays pharma-only,
# deliberately, and is expected to disqualify on dispersion -- that IS the
# correct naive-comps answer for a diversified filer. MEDTECH_PEERS is fetched
# separately below, only for the segment-weighted SOTP.

# CAPM inputs are supplied, never fetched -- see marketdata.CAPMInputs. These carry a
# stated basis so a reviewer can challenge the number rather than guess where it came
# from. REPLACE THESE with your own sourced figures before publishing any conclusion:
# they are placeholders with an honest label, not researched values.
CAPM = CAPMInputs(
    beta=0.53,
    risk_free_rate=0.0496,
    equity_risk_premium=0.0423,
    basis=(
        "beta: Blume-adjusted (0.33 + 0.67 x raw) from 5Y raw betas of 0.26 "
        "(Investing.com, NYSE:JNJ) and 0.36 (GuruFocus, 2026-05-29); adjusted "
        "because raw betas mean-revert and the 5Y window spans the 2023 Kenvue "
        "separation, so it prices a capital structure J&J no longer has. "
        "rf: 10Y UST close 4.96% on 2026-09-11 (Treasury DGS10). "
        "ERP: Damodaran implied ERP 4.23%, as of January 2026 (Equity Risk "
        "Premiums: Determinants, Estimates and Implications, 2026 Edition, "
        "SSRN, March 2026). Damodaran republishes this monthly on damodaran.com's "
        "front page, but that figure isn't independently datestamped or citable "
        "after the fact -- the January print is the last one with a fixed, "
        "verifiable source. Known limitation: paired with a September risk-free "
        "rate, an 8-month-old ERP after a ~75bp rate move is a real mismatch, "
        "not a rounding issue -- flagged here rather than silently absorbed."
    ),
)
TERMINAL_GROWTH = 0.025
WACC_RANGE = [0.060, 0.065, 0.070, 0.075, 0.080]
GROWTH_RANGE = [0.015, 0.020, 0.025, 0.030]


def load_financial_snapshot(ticker: str) -> dict:
    path = SNAPSHOT_DIR / f"{ticker}_financials.json"
    if not path.exists():
        raise FileNotFoundError(
            f"No golden snapshot for {ticker} at {path}. Generate it with "
            f"`python scripts/refresh_snapshots.py --live` on a machine with EDGAR "
            f"access, then commit the result.")
    data = json.loads(path.read_text())
    return {
        "base_year": data["base_year"],
        "table": {int(k): v for k, v in data["table"].items()},
        "forecast": {int(k): v for k, v in data["forecast"].items()},
        "generated": data["generated"],
        "trellis_version": data.get("trellis_version", "unknown"),
    }


def load_financial_live(cik: int) -> dict:
    """Live path through Trellis. Imported lazily so the offline path needs no Trellis
    install at all."""
    from trellis.companies import get_profile
    from trellis.forecast import derive_drivers_from_history, run_forecast
    from trellis.ingest import fetch_all
    from trellis.statements import build_annual_table, fill_derived_gaps

    profile = get_profile(cik)
    raw = fetch_all(cik)
    build_result = build_annual_table(raw)
    table = build_result.table
    fill_derived_gaps(table)

    stub_years = {s.year for s in build_result.stub_periods}
    required = ("revenue", "operating_income", "income_tax_expense", "capex",
                "depreciation_amortization", "accounts_receivable", "inventory",
                "accounts_payable", "long_term_debt", "cash_and_equivalents")
    base_year = None
    newest = max(table)
    for year in sorted(table, reverse=True):
        if newest - year > 5:
            break
        if year in stub_years:
            continue
        if all(f in table[year] for f in required):
            base_year = year
            break
    if base_year is None:
        raise RuntimeError(
            f"CIK {cik} ({profile.name}): no recent year has every field the valuation "
            f"needs. Run Trellis's scripts/diagnose_tags.py to find the blocking tag "
            f"rather than widening the search window.")

    drivers = derive_drivers_from_history(table, base_year, lookback_years=5,
                                           overrides=profile.overrides,
                                           exclude_years=stub_years)
    forecast = run_forecast(table, base_year, drivers, years=5)
    return {"base_year": base_year, "table": table, "forecast": forecast,
            "drivers": drivers, "generated": "live", "trellis_version": "live"}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--offline", action="store_true",
                       help="run from committed golden snapshots (no network)")
    mode.add_argument("--live", action="store_true",
                       help="pull fresh from SEC EDGAR via Trellis + market provider")
    parser.add_argument("--max-market-age-days", type=int, default=None,
                        help="refuse to run if the market snapshot is older than this")
    args = parser.parse_args()

    # Must include MEDTECH_PEERS too -- PEERS is deliberately pharma-only (see its
    # definition above), but the offline loader is also called for medtech CIKs when
    # building the SOTP section below. Missing this produced a StopIteration the first
    # time the SOTP block ran, since the medtech tickers weren't in the search list.
    loader = load_financial_live if args.live else lambda c: load_financial_snapshot(
        next(x["ticker"] for x in [SUBJECT, *PEERS, *MEDTECH_PEERS] if x["cik"] == c))

    print(f"ValuationLab -- {SUBJECT['name']} ({SUBJECT['ticker']})")
    print(f"Mode: {'LIVE' if args.live else 'OFFLINE (golden snapshots)'}\n")

    subject = loader(SUBJECT["cik"])
    base = subject["table"][subject["base_year"]]
    print(f"Subject base year: FY{subject['base_year']} "
          f"(data generated: {subject['generated']})")

    market = get_market_data(SUBJECT["ticker"], MARKET_SNAPSHOT,
                              prefer_live=args.live,
                              max_age_days=args.max_market_age_days)
    print(f"Market: {market.share_price:,.2f}/share, "
          f"{market.shares_outstanding:,.0f} shares, as of {market.as_of} "
          f"[{market.source}]\n")

    # --- DCF ------------------------------------------------------------------------
    interest_rate = subject.get("drivers").interest_rate if args.live else base.get(
        "_interest_rate", 0.038)
    tax_rate = subject.get("drivers").tax_rate if args.live else base.get(
        "_tax_rate", 0.17)

    dcf_market = MarketData(
        share_price=market.share_price, shares_outstanding=market.shares_outstanding,
        beta=CAPM.beta, risk_free_rate=CAPM.risk_free_rate,
        equity_risk_premium=CAPM.equity_risk_premium, as_of=market.as_of)
    dcf = run_dcf(base, subject["forecast"], dcf_market, interest_rate, tax_rate,
                   TERMINAL_GROWTH)

    grid = sensitivity_grid(base, subject["forecast"], dcf_market, interest_rate,
                             tax_rate, WACC_RANGE, GROWTH_RANGE)
    finite = [v for v in grid.values() if v == v and v > 0]
    dcf_range = MethodRange(
        method=Method.DCF, low=min(finite), mid=dcf.implied_share_price,
        high=max(finite),
        basis=f"WACC {min(WACC_RANGE):.1%}-{max(WACC_RANGE):.1%} x g "
              f"{min(GROWTH_RANGE):.1%}-{max(GROWTH_RANGE):.1%}; "
              f"computed WACC {dcf.wacc.wacc:.2%}",
        caveat=f"Terminal value is {dcf.pv_terminal_value / dcf.enterprise_value:.0%} "
               f"of EV. Equity weight in WACC {dcf.wacc.weight_equity:.0%}. "
               f"CAPM basis: {CAPM.basis[:60]}...",
        provenance=f"Trellis forecast off FY{subject['base_year']}; market {market.as_of}")

    # --- Trading comps ---------------------------------------------------------------
    peer_multiples = []
    for peer in PEERS:
        try:
            pdata = loader(peer["cik"])
            pmarket = get_market_data(peer["ticker"], MARKET_SNAPSHOT,
                                       prefer_live=args.live)
            peer_multiples.append(build_peer_multiple(
                peer["ticker"], peer["name"], pdata["base_year"],
                pdata["table"][pdata["base_year"]], pmarket))
        except (FileNotFoundError, KeyError, ValueError) as exc:
            print(f"  ! {peer['ticker']} excluded: {exc}")

    if not peer_multiples:
        raise SystemExit("No peers available -- cannot build a comps range.")

    comps = summarize(peer_multiples, basis="ev_ebitda")
    print(f"Comps ({comps.basis}): {len(comps.included)} peers included "
          f"{comps.included}, {len(comps.excluded)} excluded")
    for ticker, reason in comps.excluded:
        print(f"  ! {ticker}: {reason}")

    comps_implied = implied_value(base, comps, market.shares_outstanding)
    comps_range = MethodRange(
        method=Method.TRADING_COMPS,
        low=comps_implied["implied_price_low"],
        mid=comps_implied["implied_price_median"],
        high=comps_implied["implied_price_high"],
        basis=f"EV/EBITDA {comps.low:.1f}x-{comps.high:.1f}x "
              f"(median {comps.median:.1f}x) across {len(comps.included)} peers",
        caveat="Annual, not LTM denominators -- up to 12 months stale against a "
               "current price. EBITDA is built from reported lines, not company "
               "adjusted figures, so litigation and IPR&D charges are included.",
        provenance=f"Trellis annual tables; market {market.as_of}")

    # --- Precedent transactions ------------------------------------------------------
    subject_ebitda = base["operating_income"] + base["depreciation_amortization"]
    net_debt = base.get("long_term_debt", 0.0) - base.get("cash_and_equivalents", 0.0)
    prec_prices = []
    for deal in MATURE_REVENUE_DEALS:
        ev = apply_multiple(deal, base["revenue"], subject_ebitda)[
            "implied_ev_from_revenue_multiple"]
        prec_prices.append((ev - net_debt) / market.shares_outstanding)

    tier_spread = max(prec_prices) / min(prec_prices) if min(prec_prices) > 0 else None
    prec_range = MethodRange(
        method=Method.PRECEDENT, low=min(prec_prices),
        mid=sum(prec_prices) / len(prec_prices), high=max(prec_prices),
        basis=f"EV/Revenue from {len(MATURE_REVENUE_DEALS)} sourced mature-revenue "
              f"pharma deals: " + ", ".join(
                  f"{d.target.split()[0]} {d.ev_revenue:.1f}x" for d in MATURE_REVENUE_DEALS),
        caveat=f"Only {len(MATURE_REVENUE_DEALS)} deals sourced, spreading "
               f"{tier_spread:.1f}x on identical subject financials. Control premium "
               f"embedded. Read as a scenario set, not a valuation.",
        provenance="Acquirer SEC filings and target results releases; see precedent.py")

    # --- Segment-weighted view (SOTP) ------------------------------------------------
    # Supplementary to the three-method triangulation above, not a fourth competing
    # method inside it. J&J is diversified enough that a single peer set cannot value
    # it -- see segments.py's module docstring for the measured evidence (medtech
    # spreads 1.4x, pharma spreads 2.9x and gets WORSE with more peers added). This
    # section values each segment against its own peer group instead of pretending one
    # peer set fits a company that is not one business.
    if subject["base_year"] != 2025:
        print(f"  ! SOTP skipped: JNJ_FY2025_SEGMENTS is hardcoded to FY2025 segment "
              f"sales, but the subject base year is FY{subject['base_year']}. Segment "
              f"figures would not match the financials used elsewhere in this run.")
        sotp = None
    else:
        pharma_rev_comps = summarize(peer_multiples, basis="ev_revenue")

        medtech_multiples = []
        for peer in MEDTECH_PEERS:
            try:
                pdata = loader(peer["cik"])
                pmarket = get_market_data(peer["ticker"], MARKET_SNAPSHOT,
                                           prefer_live=args.live)
                medtech_multiples.append(build_peer_multiple(
                    peer["ticker"], peer["name"], pdata["base_year"],
                    pdata["table"][pdata["base_year"]], pmarket))
            except (FileNotFoundError, KeyError, ValueError) as exc:
                print(f"  ! {peer['ticker']} (medtech) excluded: {exc}")

        if not medtech_multiples:
            print("  ! SOTP skipped: no medtech peers available.")
            sotp = None
        else:
            medtech_rev_comps = summarize(medtech_multiples, basis="ev_revenue")

            # Disqualification threshold matches triangulate.recommend_anchor's own
            # comps_multiple_spread bar (2.0x) -- the two must never disagree about
            # what counts as too dispersed to value a segment or a company.
            def _dq_reason(name: str, comps_range) -> str:
                if comps_range.high / comps_range.low <= 2.0:
                    return ""
                return (f"{name} peer multiples span {comps_range.high/comps_range.low:.1f}x "
                        f"on revenue ({comps_range.low:.1f}x-{comps_range.high:.1f}x) -- "
                        f"too dispersed for the median to value a segment.")

            pharma_group = PeerGroup(
                name="Large-cap pharma", tickers=tuple(comps.included),
                low_multiple=pharma_rev_comps.low, median_multiple=pharma_rev_comps.median,
                high_multiple=pharma_rev_comps.high,
                basis=f"EV/Revenue, FY{subject['base_year']} annual denominators",
                provenance=f"Trellis annual tables; market {market.as_of}",
                disqualified_reason=_dq_reason("Innovative Medicine", pharma_rev_comps))
            medtech_group = PeerGroup(
                name="MedTech", tickers=tuple(medtech_rev_comps.included),
                low_multiple=medtech_rev_comps.low, median_multiple=medtech_rev_comps.median,
                high_multiple=medtech_rev_comps.high,
                basis=f"EV/Revenue, FY{subject['base_year']} annual denominators",
                provenance=f"Trellis annual tables; market {market.as_of}",
                disqualified_reason=_dq_reason("MedTech", medtech_rev_comps))

            try:
                sotp = build_sotp(
                    JNJ_FY2025_SEGMENTS,
                    {"Innovative Medicine": pharma_group, "MedTech": medtech_group},
                    consolidated_revenue=base["revenue"], net_debt=net_debt,
                    shares_outstanding=market.shares_outstanding)
            except ValueError as exc:
                print(f"  ! SOTP skipped: {exc}")
                sotp = None

    # --- Triangulate -----------------------------------------------------------------
    comps_spread = comps.high / comps.low if comps.low > 0 else None

    # The anchor is NOT hardcoded here. An earlier version of this script supplied a
    # fixed rationale arguing comps should anchor; it survived three consecutive runs
    # unchanged while the underlying data moved enough to invalidate it twice. The
    # derived recommendation is computed from measured warnings instead, and states
    # plainly when no method is fit to anchor. Supply anchor= and reasoning= here only
    # when you have a specific argument the structural checks don't capture -- the tool
    # will honour it and report the conflict if it contradicts the warnings.
    conclusion = triangulate(
        [dcf_range, comps_range, prec_range],
        market_price=market.share_price,
        terminal_value_share=dcf.pv_terminal_value / dcf.enterprise_value,
        equity_weight_in_wacc=dcf.wacc.weight_equity,
        precedent_tier_spread=tier_spread,
        comps_multiple_spread=comps_spread,
        comps_peer_count=len(comps.included),
    )

    print()
    print(format_conclusion(conclusion))

    if sotp is not None:
        print()
        print(format_sotp(sotp))


if __name__ == "__main__":
    main()
