"""
Market data layer.

Everything in this module is what Trellis deliberately does NOT cover: current share
price, share count, and the CAPM inputs. Filing-derived figures are stable and
reproducible forever -- market data is neither. That difference drives the whole design
here, and it is not a stylistic choice:

  1. A live provider fetch is the real path (fresh prices, current share count).
  2. A committed JSON snapshot is the reproducible path. Without it, every historical
     run of this repo produces a different answer, no test can assert a real number,
     and nobody cloning the repo in six months can reproduce anything in the README.
     The snapshot is dated; a stale snapshot is a loud, checkable condition, not a
     silent one.

Both paths return the identical MarketSnapshot type, so nothing downstream knows or
cares which was used -- except that `source` and `as_of` are always carried through,
so a reviewer can tell.

Deliberate boundary: this module does NOT fetch beta or the equity risk premium from a
provider. Provider-reported beta is a black box (unstated lookback window, unstated
index, unstated adjustment) and ERP is an estimate, not an observation -- pulling
either from an API would launder a judgment call into something that looks measured.
Both are supplied explicitly by the caller with a cited basis, the same discipline
Trellis applies to its sourced driver overrides.

Share count: uses shares outstanding as reported by the provider, which is a
point-in-time basic count. Diluted share count (options, RSUs, convertibles) would give
a more conservative per-share value. Not modeled here -- a real, disclosed limitation
that understates dilution, not an oversight.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path


class StaleSnapshotError(RuntimeError):
    """Raised when a snapshot is older than the caller's tolerance. Loud by design --
    a valuation quoting a share price from an unknown date is worse than one that
    refuses to run."""


@dataclass(frozen=True)
class MarketSnapshot:
    ticker: str
    share_price: float
    shares_outstanding: float  # in the same unit as the financials being used
    as_of: str                 # ISO date
    source: str                # "live:<provider>" or "snapshot:<path>"
    currency: str = "USD"

    def age_days(self, today: date | None = None) -> int:
        today = today or date.today()
        return (today - datetime.fromisoformat(self.as_of).date()).days

    def require_fresh(self, max_age_days: int, today: date | None = None) -> "MarketSnapshot":
        age = self.age_days(today)
        if age > max_age_days:
            raise StaleSnapshotError(
                f"{self.ticker} market snapshot is {age} days old (as_of {self.as_of}), "
                f"limit {max_age_days}. Refresh it or raise the limit deliberately -- "
                f"don't let a stale price silently set the equity weight in WACC.")
        return self


@dataclass(frozen=True)
class CAPMInputs:
    """Supplied, never fetched. `basis` is the citation -- same standard as Trellis's
    sourced overrides: a number without a stated basis is an assumption wearing a
    measurement's clothes."""
    beta: float
    risk_free_rate: float
    equity_risk_premium: float
    basis: str


def load_snapshot(path: str | Path, ticker: str) -> MarketSnapshot:
    path = Path(path)
    data = json.loads(path.read_text())
    if ticker not in data:
        raise KeyError(f"{ticker} not in snapshot {path}. Present: {sorted(data)}")
    entry = data[ticker]
    return MarketSnapshot(ticker=ticker, source=f"snapshot:{path.name}", **entry)


def save_snapshot(path: str | Path, snapshots: list[MarketSnapshot]) -> None:
    """Writes the committed fixture. Drops `source` and `ticker` from the payload --
    they're re-derived on load, so the file can't disagree with where it actually came
    from."""
    path = Path(path)
    payload = {}
    for snap in snapshots:
        entry = asdict(snap)
        entry.pop("source")
        entry.pop("ticker")
        payload[snap.ticker] = entry
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def fetch_live(ticker: str) -> MarketSnapshot:
    """Live provider path. Imported lazily so the package installs and the offline test
    suite runs with no market-data dependency at all -- the snapshot path must never
    require a provider to be installed."""
    try:
        import yfinance  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover -- environment-dependent
        raise ImportError(
            "Live market data needs yfinance: pip install 'valuationlab[live]'. The "
            "snapshot path works without it.") from exc

    t = yfinance.Ticker(ticker)  # pragma: no cover -- network
    info = t.info
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    shares = info.get("sharesOutstanding")
    if price is None or shares is None:
        raise ValueError(
            f"Provider returned no price and/or share count for {ticker} "
            f"(price={price}, shares={shares}). Refusing to construct a snapshot with "
            f"a missing field rather than defaulting it to something plausible.")
    return MarketSnapshot(
        ticker=ticker, share_price=float(price), shares_outstanding=float(shares),
        as_of=date.today().isoformat(), source="live:yfinance",
        currency=info.get("currency", "USD"),
    )


def get_market_data(ticker: str, snapshot_path: str | Path, prefer_live: bool = False,
                     max_age_days: int | None = None) -> MarketSnapshot:
    """Single entry point. Defaults to the reproducible snapshot; `prefer_live=True`
    tries the provider and falls back to the snapshot on any failure rather than
    aborting -- but the returned `source` always says which one you actually got, so a
    silent fallback can't be mistaken for a live fetch."""
    if prefer_live:
        try:
            return fetch_live(ticker)
        except Exception:  # noqa: BLE001 -- any provider failure falls back, visibly
            pass
    snap = load_snapshot(snapshot_path, ticker)
    if max_age_days is not None:
        snap.require_fresh(max_age_days)
    return snap


def net_debt_from_trellis(base_year_actuals: dict) -> float:
    """Net debt straight off Trellis's balance sheet -- filing-derived, not market
    data, kept here only because it's the other half of the EV/equity bridge.

    long_term_debt only: Trellis's schema has no separate short-term-debt or
    current-portion-of-LTD line, so any short-term borrowings sit inside
    total_liabilities and are not captured. For a net-cash company this understates
    net debt (overstates equity value). Real limitation, stated rather than papered
    over with an assumed split."""
    return (base_year_actuals.get("long_term_debt", 0.0)
            - base_year_actuals.get("cash_and_equivalents", 0.0))
