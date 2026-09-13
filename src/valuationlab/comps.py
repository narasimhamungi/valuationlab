"""
Trading comparables.

Peer multiples are computed from two sources that must not be confused: the numerator
(enterprise value) is market data, which goes stale; the denominator (revenue, EBITDA
proxy) is filing data from Trellis, which does not. Every PeerMultiple therefore carries
BOTH provenance dates, because a multiple built from today's price over an 11-month-old
revenue figure is a different object from one where both are current, and only the dates
reveal which you have.

Annual, not LTM -- a deliberate, disclosed choice. Standard practice is last-twelve-
months financials, which requires quarterly XBRL ingestion; Trellis produces annual
tables only. Using the last reported fiscal year means the denominator can be up to
~12 months stale, which systematically overstates multiples for a growing peer (old
revenue under a current price) and understates them for a shrinking one. The alternative
-- building quarterly ingestion -- is a real scope addition, not a free upgrade. Stated
here rather than hidden so nobody mistakes these for LTM multiples.

EBITDA proxy: operating_income + depreciation_amortization, both straight from Trellis's
canonical schema. This is EBITDA by construction from reported lines, NOT a company-
reported "adjusted EBITDA" -- it includes items a company's own adjusted figure would
strip out (restructuring, litigation, impairments). For pharma specifically, where large
legal settlements and IPR&D charges are common, this can differ materially from the
adjusted EBITDA a banker would quote. That's a feature for comparability across peers
computed the same way, and a caveat when comparing to any externally sourced multiple.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from valuationlab.marketdata import MarketSnapshot


@dataclass(frozen=True)
class PeerMultiple:
    ticker: str
    name: str
    fiscal_year: int          # provenance: which filing year the denominator is from
    market_as_of: str         # provenance: when the numerator was priced
    market_cap: float
    net_debt: float
    enterprise_value: float
    revenue: float
    ebitda_proxy: float
    ev_revenue: float
    ev_ebitda: float


@dataclass(frozen=True)
class CompsRange:
    basis: str                # "ev_revenue" or "ev_ebitda"
    included: list[str]       # tickers actually used
    excluded: list[tuple[str, str]]  # (ticker, reason) -- never silently dropped
    low: float
    median: float
    high: float
    mean: float


def ebitda_proxy(year_data: dict) -> float:
    """EBITDA built from Trellis's canonical lines. Raises rather than defaulting a
    missing component to zero -- a silently-zero D&A produces a wrong multiple that
    looks perfectly reasonable."""
    for field in ("operating_income", "depreciation_amortization"):
        if field not in year_data:
            raise KeyError(
                f"{field} missing -- refusing to compute an EBITDA proxy with an "
                f"absent component. Add a schema fallback in Trellis rather than "
                f"defaulting it here.")
    return year_data["operating_income"] + year_data["depreciation_amortization"]


def build_peer_multiple(ticker: str, name: str, fiscal_year: int,
                         year_data: dict, market: MarketSnapshot) -> PeerMultiple:
    """year_data: one year from Trellis's historical AnnualTable for this peer.
    market: that peer's MarketSnapshot. Units must already agree -- if the financials
    are in dollars, shares_outstanding must be an absolute count, not millions."""
    market_cap = market.share_price * market.shares_outstanding
    net_debt = (year_data.get("long_term_debt", 0.0)
                - year_data.get("cash_and_equivalents", 0.0))
    enterprise_value = market_cap + net_debt
    revenue = year_data["revenue"]
    ebitda = ebitda_proxy(year_data)

    if revenue <= 0:
        raise ValueError(f"{ticker}: non-positive revenue ({revenue}) -- not a "
                          f"meaningful multiple denominator.")

    return PeerMultiple(
        ticker=ticker, name=name, fiscal_year=fiscal_year, market_as_of=market.as_of,
        market_cap=market_cap, net_debt=net_debt, enterprise_value=enterprise_value,
        revenue=revenue, ebitda_proxy=ebitda,
        ev_revenue=enterprise_value / revenue,
        ev_ebitda=enterprise_value / ebitda if ebitda > 0 else float("nan"),
    )


def summarize(peers: list[PeerMultiple], basis: str = "ev_ebitda",
               exclude_negative_ebitda: bool = True) -> CompsRange:
    """Range across peers, reporting median as the headline. Median not mean: a comps
    set of five is small enough that one distorted peer moves the mean materially, and
    distorted peers are the norm in pharma (a single year with a multi-billion legal
    charge halves that year's operating income). Mean is still returned so the gap
    between the two is visible -- a large mean/median divergence is itself a signal
    that one peer is doing the work.

    Every exclusion is recorded with a reason. A comps set that silently drops peers
    is indistinguishable from one that cherry-picked them."""
    if basis not in ("ev_revenue", "ev_ebitda"):
        raise ValueError(f"basis must be ev_revenue or ev_ebitda, got {basis!r}")

    included, excluded, values = [], [], []
    for p in peers:
        value = getattr(p, basis)
        if value != value:  # NaN
            excluded.append((p.ticker, f"{basis} undefined (non-positive EBITDA proxy)"))
            continue
        if basis == "ev_ebitda" and exclude_negative_ebitda and p.ebitda_proxy <= 0:
            excluded.append((p.ticker, "non-positive EBITDA proxy"))
            continue
        included.append(p.ticker)
        values.append(value)

    if not values:
        raise ValueError("No peers remain after exclusions -- see excluded list; "
                          "refusing to report a range over an empty set.")

    return CompsRange(
        basis=basis, included=included, excluded=excluded,
        low=min(values), median=statistics.median(values), high=max(values),
        mean=statistics.fmean(values),
    )


def implied_value(subject_year_data: dict, comps: CompsRange,
                   shares_outstanding: float) -> dict[str, float]:
    """Apply a peer range to the subject company. Returns implied EQUITY value per
    share at low/median/high, with the net-debt bridge applied -- not implied EV, since
    a range quoted in EV terms is not directly comparable to the DCF's per-share
    output, and the whole point of the triangulation is comparing like with like."""
    denominator = (subject_year_data["revenue"] if comps.basis == "ev_revenue"
                   else ebitda_proxy(subject_year_data))
    net_debt = (subject_year_data.get("long_term_debt", 0.0)
                - subject_year_data.get("cash_and_equivalents", 0.0))

    out = {}
    for label, multiple in (("low", comps.low), ("median", comps.median),
                             ("high", comps.high)):
        ev = multiple * denominator
        out[f"implied_price_{label}"] = (ev - net_debt) / shares_outstanding
    return out
