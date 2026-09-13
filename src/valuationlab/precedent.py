"""
Precedent transaction analysis.

The hard data problem this module exists to solve: deal multiples aren't in XBRL and
aren't free via a live API (that's Capital IQ/PitchBook territory). Every deal here is
sourced from the acquirer's or target's own SEC filings or contemporaneous press
release with numbers quoted directly, not pulled from a paid database and not
invented. Each PrecedentDeal carries its source so a reviewer can verify every number
independently -- that traceability is the entire point of building this instead of
hardcoding a multiples table.

Tiering, discovered while sourcing real deals rather than assumed up front: pharma M&A
multiples do NOT form one comparable population. Two structurally different deal types
get mixed together in most casual "precedent transaction" writeups:

  MATURE_REVENUE: target has a large, diversified, profitable revenue base. Multiple
  reflects normal growth and margin expectations. J&J/Actelion and BMS/Celgene both
  qualify -- but even within this tier the multiples aren't close (see below), because
  "mature revenue" doesn't mean "comparable growth profile."

  PLATFORM_PIPELINE: target is pre-revenue or early-revenue, valued overwhelmingly on
  pipeline optionality (clinical-stage assets, projected peak sales years out). EV/
  Revenue and EV/EBITDA on CURRENT financials are close to meaningless here -- a high
  multiple reflects near-zero current-year earnings, not overpayment. Not yet populated
  in this module (Seagen/AbbVie-ImmunoGen/Prometheus-class deals) -- deliberately kept
  separate so a future addition can't get silently averaged into the mature-revenue
  multiple range.

Applying ANY of these multiples to J&J requires picking the tier whose target profile
actually resembles J&J's (diversified, moderate-growth, profitable) -- not averaging
across tiers, and not pretending within-tier variance doesn't exist. J&J/Actelion prices
at ~12x revenue / ~30x core operating income because Actelion was a single-franchise,
high-growth specialty biotech -- that multiple says almost nothing about what a buyer
would pay for J&J's own diversified, low-single-digit-growth pharma segment. BMS/Celgene,
despite also being "mature revenue," prices far lower (~4.8x revenue) because Celgene
was a large diversified oncology/immunology franchise closer to J&J's own growth and
scale profile. The gap between those two numbers -- both real, both mature-revenue,
both pharma -- IS the finding: there is no single "pharma precedent multiple," and any
tool that reports one without this caveat is hiding the analysis, not doing it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DealTier(Enum):
    MATURE_REVENUE = "mature_revenue"
    PLATFORM_PIPELINE = "platform_pipeline"  # not yet populated -- see module docstring


@dataclass(frozen=True)
class PrecedentDeal:
    acquirer: str
    target: str
    announced: str  # ISO date
    tier: DealTier
    enterprise_value_usd_mm: float
    target_revenue_usd_mm: float
    target_ebitda_proxy_usd_mm: float
    ebitda_proxy_label: str  # what the EBITDA-proxy figure actually is -- these
    # targets don't all report a clean "EBITDA" line, so state what's really being used
    source: str
    notes: str = ""

    @property
    def ev_revenue(self) -> float:
        return self.enterprise_value_usd_mm / self.target_revenue_usd_mm

    @property
    def ev_ebitda_proxy(self) -> float:
        return self.enterprise_value_usd_mm / self.target_ebitda_proxy_usd_mm


# --- Sourced deals -----------------------------------------------------------------
# Every figure below is quoted directly from the cited filing/release, not computed
# from a secondary aggregator. USD conversions for non-USD-reporting targets are noted
# where applied.

JNJ_ACTELION_2017 = PrecedentDeal(
    acquirer="Johnson & Johnson", target="Actelion Ltd.", announced="2017-01-26",
    tier=DealTier.MATURE_REVENUE,
    enterprise_value_usd_mm=29_600.0,  # J&J 10-K FY2017: "$29.6 billion, net of cash
    # acquired" -- this IS the EV figure (net of cash = enterprise, not equity, value)
    target_revenue_usd_mm=2_412.0,  # Actelion FY2016 full-year sales, CHF 2,412mm;
    # CHF/USD was near parity through this period, used at face value, not converted
    target_ebitda_proxy_usd_mm=992.0,  # Actelion FY2016 "core operating income"
    # (company's own non-GAAP adjusted figure), CHF 992mm -- NOT a GAAP EBITDA; flagged
    # via ebitda_proxy_label rather than presented as equivalent to a reported EBITDA
    ebitda_proxy_label="core operating income (non-GAAP, company-reported)",
    source="J&J FY2017 10-K (purchase price allocation, sec.gov/Archives/edgar/data/"
           "200406); Actelion FY2016 results press release, Feb 13 2017",
    notes="Single-franchise (pulmonary arterial hypertension) specialty biotech, "
          "double-digit revenue growth. Rich multiple reflects that growth/franchise "
          "concentration, not a generalizable 'pharma M&A multiple' -- see module "
          "docstring before applying this to a diversified, moderate-growth target.",
)

BMY_CELGENE_2019 = PrecedentDeal(
    acquirer="Bristol-Myers Squibb", target="Celgene Corporation",
    announced="2019-01-03", tier=DealTier.MATURE_REVENUE,
    enterprise_value_usd_mm=74_000.0,  # BMS 8-K: "equity value of approximately $74
    # billion" -- used directly as an EV proxy. NOT net-debt-adjusted (Celgene's
    # announcement-date net debt figure wasn't sourced in this pass) -- a labeled
    # approximation, not a precise EV, flagged rather than silently treated as exact
    target_revenue_usd_mm=15_281.0,  # Celgene FY2018 10-K, total revenue, last full
    # reported year before the Jan 2019 announcement
    target_ebitda_proxy_usd_mm=6_511.0,  # Celgene FY2018 adjusted net income
    # (company-reported non-GAAP) -- used as the closest available profitability proxy
    # sourced this pass; not GAAP EBITDA and not GAAP operating income, flagged
    ebitda_proxy_label="adjusted net income (non-GAAP, company-reported) -- weaker "
                       "EBITDA proxy than Actelion's core operating income; a real "
                       "operating-income figure should replace this before this "
                       "deal is used for anything beyond an EV/Revenue check",
    source="BMS 8-K/A, Jan 2 2019 (sec.gov/Archives/edgar/data/14272); Celgene Q4/FY2018 "
           "results press release, Jan 31 2019",
    notes="Large, diversified oncology/immunology franchise (REVLIMID, OTEZLA, "
          "POMALYST) -- far closer to J&J's own scale and diversification than "
          "Actelion. Multiple is markedly lower despite both deals sitting in the same "
          "'mature revenue' tier -- the gap is the reason a single blended pharma "
          "multiple is not a defensible input.",
)

MATURE_REVENUE_DEALS: list[PrecedentDeal] = [JNJ_ACTELION_2017, BMY_CELGENE_2019]
# PLATFORM_PIPELINE_DEALS intentionally not yet populated -- see module docstring.
# Next deals planned: AbbVie/Allergan (2020) or a second diversified-franchise deal,
# to widen the mature-revenue sample past two before drawing any range conclusion.


def apply_multiple(deal: PrecedentDeal, subject_revenue_usd_mm: float,
                    subject_ebitda_proxy_usd_mm: float) -> dict[str, float]:
    """Implied EV for a subject company under one precedent deal's multiples. Returns
    both bases (revenue and EBITDA-proxy) rather than picking one -- the divergence
    between the two implied values, and against the OTHER deal's implied values, is
    itself the analysis. Never average across deals in different tiers or call the
    result a single 'precedent transaction value' without the tier caveat attached."""
    return {
        "implied_ev_from_revenue_multiple": deal.ev_revenue * subject_revenue_usd_mm,
        "implied_ev_from_ebitda_multiple": deal.ev_ebitda_proxy * subject_ebitda_proxy_usd_mm,
    }
