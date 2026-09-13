"""
Regenerate the committed golden snapshots.

Run this on a machine with EDGAR access and a market-data provider, then commit the
result. Everything downstream (the offline valuation run, CI, the README numbers) reads
these files, so they are the reproducibility contract: the repo produces the same answer
for anyone who clones it, at any time, without network access.

    export TRELLIS_USER_AGENT="Your Name your@email.com"
    python scripts/refresh_snapshots.py

Deliberately NOT run automatically in CI. A snapshot that silently refreshes is not a
fixture, it's a moving target -- the whole point is that changing the numbers is an
explicit, reviewable commit.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, "src")

from valuationlab.marketdata import fetch_live, save_snapshot

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = ROOT / "data" / "snapshots"

COMPANIES = [
    {"cik": 200406, "ticker": "JNJ", "name": "Johnson & Johnson"},
    {"cik": 78003, "ticker": "PFE", "name": "Pfizer Inc."},
    {"cik": 310158, "ticker": "MRK", "name": "Merck & Co., Inc."},
    {"cik": 1551152, "ticker": "ABBV", "name": "AbbVie Inc."},
    {"cik": 1800, "ticker": "ABT", "name": "Abbott Laboratories"},
    {"cik": 14272, "ticker": "BMY", "name": "Bristol-Myers Squibb Company"},
    # Medtech peers, added for the segment-weighted SOTP in run_valuation.py -- these
    # were absent from this list even after run_valuation.py started requiring them,
    # which is why the SOTP section skipped with "no medtech peers available" the first
    # time it ran end to end. This list and run_valuation.py's PHARMA_PEERS/MEDTECH_PEERS
    # are two independent sources of truth for the same tickers; if they drift apart
    # again, it will be silent here and loud only in run_valuation.py's exclusion lines.
    {"cik": 1613103, "ticker": "MDT", "name": "Medtronic plc"},
    {"cik": 10795, "ticker": "BDX", "name": "Becton, Dickinson and Company"},
    {"cik": 310764, "ticker": "SYK", "name": "Stryker Corporation"},
    {"cik": 885725, "ticker": "BSX", "name": "Boston Scientific Corporation"},
]

REQUIRED = ("revenue", "operating_income", "income_tax_expense", "capex",
            "depreciation_amortization", "accounts_receivable", "inventory",
            "accounts_payable", "long_term_debt", "cash_and_equivalents")


def snapshot_company(cik: int, ticker: str) -> dict:
    from trellis.companies import get_profile
    from trellis.forecast import derive_drivers_from_history, run_forecast
    from trellis.ingest import fetch_all
    from trellis.statements import build_annual_table, fill_derived_gaps

    profile = get_profile(cik)
    print(f"  fetching {ticker} (CIK {cik}, {profile.name})...")
    raw = fetch_all(cik)
    build_result = build_annual_table(raw)
    table = build_result.table
    fill_derived_gaps(table)

    stub_years = {s.year for s in build_result.stub_periods}
    newest = max(table)
    base_year = None
    for year in sorted(table, reverse=True):
        if newest - year > 5:
            break
        if year in stub_years:
            continue
        if all(f in table[year] for f in REQUIRED):
            base_year = year
            break
    if base_year is None:
        missing = {f: f in table[newest] for f in REQUIRED}
        raise RuntimeError(
            f"{ticker}: no recent complete year. Field presence in FY{newest}: "
            f"{[f for f, present in missing.items() if not present]} missing. "
            f"Use Trellis's scripts/diagnose_tags.py on the blocking field.")

    drivers = derive_drivers_from_history(table, base_year, lookback_years=5,
                                           overrides=profile.overrides,
                                           exclude_years=stub_years)
    forecast = run_forecast(table, base_year, drivers, years=5)

    # Persist the two derived driver values the offline DCF needs, so the offline run
    # doesn't have to re-derive them (and can't silently diverge from the live one).
    trimmed_base = dict(table[base_year])
    trimmed_base["_interest_rate"] = drivers.interest_rate
    trimmed_base["_tax_rate"] = drivers.tax_rate
    out_table = {str(base_year): trimmed_base}

    return {
        "ticker": ticker, "cik": cik, "name": profile.name,
        "base_year": base_year,
        "table": out_table,
        "forecast": {str(y): {k: v for k, v in fy.items()
                               if not isinstance(v, bool)}
                      for y, fy in forecast.items()},
        "generated": date.today().isoformat(),
        "drivers_note": (
            f"interest_rate={drivers.interest_rate:.4f}, "
            f"tax_rate={drivers.tax_rate:.4f}; overrides applied: "
            f"{drivers.overrides_applied or 'none'}; assumptions: "
            f"{drivers.assumptions or 'none'}"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-market", action="store_true",
                        help="regenerate financial snapshots only")
    args = parser.parse_args()

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Writing snapshots to {SNAPSHOT_DIR}\n")

    print("Financial snapshots (Trellis -> SEC EDGAR):")
    failures = []
    for company in COMPANIES:
        try:
            data = snapshot_company(company["cik"], company["ticker"])
            path = SNAPSHOT_DIR / f"{company['ticker']}_financials.json"
            path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
            print(f"    wrote {path.name} (base year FY{data['base_year']})")
        except Exception as exc:  # noqa: BLE001 -- report every failure, don't abort
            print(f"    FAILED {company['ticker']}: {exc}")
            failures.append(company["ticker"])

    if not args.skip_market:
        print("\nMarket snapshots:")
        snaps = []
        for company in COMPANIES:
            try:
                snap = fetch_live(company["ticker"])
                snaps.append(snap)
                print(f"    {snap.ticker}: {snap.share_price:,.2f} x "
                      f"{snap.shares_outstanding:,.0f} shares")
            except Exception as exc:  # noqa: BLE001
                print(f"    FAILED {company['ticker']}: {exc}")
                failures.append(f"{company['ticker']} (market)")
        if snaps:
            save_snapshot(SNAPSHOT_DIR / "market.json", snaps)
            print(f"    wrote market.json ({len(snaps)} tickers)")

    if failures:
        print(f"\n{len(failures)} failure(s): {failures}")
        print("Snapshots for the rest were written. Fix the failures and re-run "
              "before committing -- a partial snapshot set will make the offline run "
              "silently drop peers.")
        sys.exit(1)
    print("\nAll snapshots regenerated. Review the diff before committing.")


if __name__ == "__main__":
    main()
