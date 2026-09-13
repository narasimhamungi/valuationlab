"""
Sum-of-the-parts comps for diversified filers.

Why this module exists, stated as evidence rather than preference: a single peer set
cannot value Johnson & Johnson. Measured across the same methodology and the same
subject company --

    MedTech peers (MDT, BDX, SYK, BSX):     1.4x EV/EBITDA spread  -> usable
    Pharma peers (PFE, MRK, ABBV, BMY):     2.9x                   -> not usable
    Pharma peers + LLY, AMGN (6 peers):     4.3x                   -> worse

Adding peers made the pharma set WORSE, which is what rules out small-sample noise as
the explanation. The dispersion is economic: medtech businesses share demand drivers and
replacement cycles, so they trade in a band; pharma valuation is dominated by
company-specific patent cliffs and pipeline outcomes that do not average into a
meaningful median. Pfizer and BMS price for decline, AbbVie and Lilly for growth, and
the median across them is a number no company in the set trades at.

So this module does NOT produce one blended figure. It values each segment against its
own peer group, carries each group's dispersion verdict separately, and reports a total
that states plainly which legs are sound and which are not. A sum containing one
disqualified leg is disclosed as such rather than presented as a valuation.

REVENUE MULTIPLES ONLY, and this is a real limitation, not a simplification of
convenience. Segment sales are disclosed in the 10-K and fully traceable. Segment EBITDA
is not: J&J reports segment income before tax, but it excludes unallocated corporate
expense, and there is no segment D&A disclosure that maps to the EBITDA proxy comps.py
builds from consolidated lines. Producing a segment EBITDA would mean inventing a D&A
allocation -- exactly the kind of unsourced number this project exists to refuse.

The cost of that choice is worth naming: revenue multiples ignore margin differences, and
J&J's two segments have very different margins (Innovative Medicine ran a 36.9% segment
income-before-tax margin in FY2025 against MedTech's materially lower figure). Applying a
peer revenue multiple to each segment implicitly assumes each segment's margin resembles
its peer group's -- defensible, since that is what the peer group is for, but an
assumption rather than an observation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Segment:
    """One reportable segment's disclosed revenue. Sourced from the filing, never
    derived from a ratio or an allocation."""
    name: str
    revenue: float
    source: str


@dataclass(frozen=True)
class PeerGroup:
    """A peer set matched to one segment, carrying its own dispersion verdict.

    `disqualified_reason` is empty when the group is fit to value a segment. When set,
    the group's multiple is still computed and reported -- suppressing it would hide
    the finding -- but every downstream total says the leg is unsound."""
    name: str
    tickers: tuple[str, ...]
    low_multiple: float
    median_multiple: float
    high_multiple: float
    basis: str          # e.g. "EV/Revenue, FY2025 annual denominators"
    provenance: str
    disqualified_reason: str = ""

    @property
    def spread(self) -> float:
        return self.high_multiple / self.low_multiple if self.low_multiple else float("inf")

    @property
    def is_fit(self) -> bool:
        return not self.disqualified_reason


@dataclass(frozen=True)
class SegmentValuation:
    segment: Segment
    group: PeerGroup
    ev_low: float
    ev_mid: float
    ev_high: float

    @property
    def is_fit(self) -> bool:
        return self.group.is_fit


@dataclass(frozen=True)
class SOTPResult:
    legs: list[SegmentValuation]
    total_ev_low: float
    total_ev_mid: float
    total_ev_high: float
    net_debt: float
    shares_outstanding: float
    revenue_reconciliation: str   # segment sum vs consolidated, stated explicitly
    unfit_legs: list[str]

    @property
    def implied_price_low(self) -> float:
        return (self.total_ev_low - self.net_debt) / self.shares_outstanding

    @property
    def implied_price_mid(self) -> float:
        return (self.total_ev_mid - self.net_debt) / self.shares_outstanding

    @property
    def implied_price_high(self) -> float:
        return (self.total_ev_high - self.net_debt) / self.shares_outstanding

    @property
    def fit_share_of_revenue(self) -> float:
        """What fraction of total segment revenue is valued by a FIT peer group. This is
        the honest headline for a partially-sound SOTP: a total resting 36% on solid
        peers and 64% on dispersed ones is a different object from one resting 90% on
        solid peers, and the single implied price hides that difference completely."""
        total = sum(l.segment.revenue for l in self.legs)
        fit = sum(l.segment.revenue for l in self.legs if l.is_fit)
        return fit / total if total else 0.0

    @property
    def is_fully_fit(self) -> bool:
        return not self.unfit_legs


# --- J&J FY2025 segments -------------------------------------------------------------
# Hardcoded from the filing with a citation, the same discipline precedent.py applies to
# deal data: segment revenue is not in the consolidated XBRL facts Trellis ingests, so it
# cannot come from the pipeline. It CAN be checked, and is -- see build_sotp's
# reconciliation, which fails loudly if the parts stop summing to the whole.

# Revenue is in RAW DOLLARS, not millions, to match Trellis's XBRL ingestion
# convention -- every other figure in this pipeline (base["revenue"], net_debt,
# comps multiples' denominators) is raw dollars. The 10-K states these in millions
# ("$60,401M"); the constants below are that figure x 1,000,000. Getting this wrong
# was a real bug caught by build_sotp's own reconciliation check: a millions-scale
# segment revenue multiplied by a correctly-scaled EV/Revenue multiple produces an EV
# a million times too small, which then gets a normal-sized net_debt subtracted from
# it -- silently wrong, not loudly wrong, if the reconciliation hadn't existed.
JNJ_FY2025_SEGMENTS = (
    Segment(
        name="Innovative Medicine", revenue=60_401_000_000.0,
        source="J&J FY2025 10-K (jnj-20251228.htm), segment sales table: "
               "TOTAL INNOVATIVE MEDICINE worldwide $60,401M. Converted to raw "
               "dollars (x1,000,000) to match Trellis's ingestion convention."),
    Segment(
        name="MedTech", revenue=33_792_000_000.0,
        source="J&J FY2025 10-K: derived as consolidated $94,193M less Innovative "
               "Medicine $60,401M. Cross-checked against the 10-K's own narrative "
               "figure of '$33.8 billion, an increase of 6.1% from 2024'. Converted "
               "to raw dollars (x1,000,000) to match Trellis's ingestion convention."),
)


def build_sotp(segments: tuple[Segment, ...], groups: dict[str, PeerGroup],
                consolidated_revenue: float, net_debt: float,
                shares_outstanding: float, tolerance: float = 0.001) -> SOTPResult:
    """`groups` maps segment name -> the peer group that values it.

    The reconciliation is not decorative. Segment revenue is hand-entered from a filing
    while consolidated revenue comes from the XBRL pipeline, so they have independent
    failure modes: a transcription error, a restatement, or a segment reorganisation all
    break the sum without breaking anything else. Checking it here means a stale segment
    figure surfaces as an error rather than as a quietly wrong valuation."""
    seg_total = sum(s.revenue for s in segments)
    gap = seg_total - consolidated_revenue
    # `tolerance` is a FRACTION of consolidated revenue, not an absolute dollar amount --
    # deliberately, after a real bug: an absolute tolerance of $1 looked reasonable when
    # this was first written with millions-scale test fixtures, then silently allowed
    # nothing to fail when the real inputs turned out to be raw-dollar-scale (a
    # million-times unit mismatch produces a gap in the tens of billions, which an
    # absolute-dollar tolerance has no scale-appropriate way to bound). A relative
    # tolerance catches a unit mismatch at any scale, including ones not yet seen.
    rel_gap = abs(gap) / consolidated_revenue if consolidated_revenue else float("inf")
    if rel_gap > tolerance:
        raise ValueError(
            f"Segment revenue does not reconcile to consolidated: segments sum to "
            f"{seg_total:,.0f} against consolidated {consolidated_revenue:,.0f}, a gap "
            f"of {gap:,.0f} ({rel_gap:.2%} of consolidated, tolerance {tolerance:.2%}). "
            f"The segment figures are hand-entered from the 10-K -- either they are "
            f"stale, the company reorganised its segments, the consolidated figure "
            f"changed, or (as happened once already) the two are in different units. "
            f"Do not value around this.")

    missing = [s.name for s in segments if s.name not in groups]
    if missing:
        raise ValueError(f"No peer group supplied for segment(s): {missing}")

    legs = []
    for seg in segments:
        g = groups[seg.name]
        legs.append(SegmentValuation(
            segment=seg, group=g,
            ev_low=g.low_multiple * seg.revenue,
            ev_mid=g.median_multiple * seg.revenue,
            ev_high=g.high_multiple * seg.revenue,
        ))

    return SOTPResult(
        legs=legs,
        total_ev_low=sum(l.ev_low for l in legs),
        total_ev_mid=sum(l.ev_mid for l in legs),
        total_ev_high=sum(l.ev_high for l in legs),
        net_debt=net_debt, shares_outstanding=shares_outstanding,
        revenue_reconciliation=(
            f"segments sum to {seg_total:,.0f} against consolidated "
            f"{consolidated_revenue:,.0f} (gap {gap:,.0f})"),
        unfit_legs=[l.segment.name for l in legs if not l.is_fit],
    )


def format_sotp(result: SOTPResult) -> str:
    out = ["-- Sum-of-the-parts (revenue multiples) " + "-" * 36, ""]
    out.append(f"  Revenue reconciliation: {result.revenue_reconciliation}")
    out.append("")
    for leg in result.legs:
        g = leg.group
        flag = "" if leg.is_fit else "   [LEG NOT FIT]"
        out.append(f"  {leg.segment.name}: revenue {leg.segment.revenue:,.0f}{flag}")
        out.append(f"     peers:    {', '.join(g.tickers)} ({g.name})")
        out.append(f"     multiple: {g.low_multiple:.1f}x - {g.high_multiple:.1f}x "
                    f"(median {g.median_multiple:.1f}x, spread {g.spread:.1f}x)")
        out.append(f"     implied EV: {leg.ev_low:,.0f} - {leg.ev_high:,.0f} "
                    f"(mid {leg.ev_mid:,.0f})")
        if not leg.is_fit:
            out.append(f"     ! {g.disqualified_reason}")
        out.append("")

    out.append(f"  Total EV:      {result.total_ev_low:,.0f} - {result.total_ev_high:,.0f} "
                f"(mid {result.total_ev_mid:,.0f})")
    out.append(f"  Less net debt: {result.net_debt:,.0f}")
    out.append(f"  Implied price: {result.implied_price_low:,.2f} - "
                f"{result.implied_price_high:,.2f} "
                f"(mid {result.implied_price_mid:,.2f})")
    out.append("")

    if result.is_fully_fit:
        out.append("  Every leg rests on a peer group tight enough to value a segment.")
    else:
        out.append(
            f"  ! {len(result.unfit_legs)} of {len(result.legs)} legs rest on a peer "
            f"group too dispersed to value a segment: {', '.join(result.unfit_legs)}. "
            f"Only {result.fit_share_of_revenue:.0%} of revenue is valued against a fit "
            f"peer set, so the total above is NOT a valuation -- the fit leg is, and "
            f"the rest is a scenario span. Reporting one implied price for the whole "
            f"company would hide exactly that split.")
    return "\n".join(out)
