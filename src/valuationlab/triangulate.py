"""
Triangulation: the actual valuation conclusion.

This is the module the other three exist to feed. Its job is NOT to average three
numbers into one. Averaging destroys the only information worth having -- a DCF and a
comps set that disagree by 40% are telling you something specific about the market's
view versus the cash flows, and a blended midpoint throws that away and replaces it
with false precision.

What this module produces instead:
  1. A football-field: each method's range, kept separate and labeled with its own
     provenance and reliability caveat.
  2. An explicit divergence diagnosis: which methods disagree, by how much, and the
     mechanical reason -- not a narrative guess, but a reason traced to the inputs
     (terminal value dominance, stale denominators, deal tier mismatch).
  3. A stated conclusion that says which method to weight and WHY, with the reasoning
     visible. In an IB/ER interview the answer "the DCF says $X" is worth nothing; the
     answer "the DCF says $X but it's 82% terminal value so I'd anchor on comps" is
     the actual skill.

Reliability tiering is structural, not a judgment applied after seeing the numbers.
Each method carries a known weakness that is a property of HOW it was built here:
  DCF          -- terminal-value dominance; sensitive to WACC and g, both estimated
  COMPS        -- annual (not LTM) denominators; peer set small; market-sentiment laden
  PRECEDENT    -- tier mismatch risk; control premium embedded; small sourced sample

Some of those weaknesses ARE disqualifying, and the module now says so. `recommend_anchor`
derives which methods are structurally fit to anchor a conclusion, from the same measured
warnings `diagnose` produces -- and returns NO anchor when every method trips a
disqualifying condition. A valuation tool that always produces an anchor will eventually
produce a wrong one with full confidence; "no method here is fit to anchor, and here is
what each is worth" is a real answer, not a failure to answer.

The caller may still state an anchor, which is honoured -- choosing what to trust remains
the analyst's act, not the tool's. But the derived recommendation is computed independently
and reported alongside, and a contradiction between the two is surfaced loudly rather than
resolved silently. That disagreement is itself information: it means either the analyst
knows something the warnings don't capture, or the stated anchor has gone stale. This
module was built with a hardcoded anchor rationale that stayed in the output across three
consecutive runs while the underlying data changed enough to invalidate it -- the stated
anchor is exactly the kind of claim that needs an independent check standing next to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Method(Enum):
    DCF = "DCF"
    TRADING_COMPS = "Trading comparables"
    PRECEDENT = "Precedent transactions"


@dataclass(frozen=True)
class MethodRange:
    method: Method
    low: float
    mid: float
    high: float
    basis: str        # what drove the range (e.g. "WACC 6.0-8.0% x g 2.0-3.0%")
    caveat: str       # the known structural weakness of THIS method as built
    provenance: str   # where the inputs came from, with dates

    def __post_init__(self):
        if not (self.low <= self.mid <= self.high):
            raise ValueError(
                f"{self.method.value}: range must be ordered low<=mid<=high, got "
                f"{self.low:.2f}/{self.mid:.2f}/{self.high:.2f}. An out-of-order range "
                f"means the upstream module produced something inconsistent -- fix "
                f"there, don't sort here.")

    @property
    def width_pct(self) -> float:
        """Range width as a fraction of the midpoint. A method whose own range is
        wider than the gap between methods isn't really disagreeing with anything."""
        return (self.high - self.low) / self.mid if self.mid else float("inf")


@dataclass(frozen=True)
class Divergence:
    method_a: Method
    method_b: Method
    gap_pct: float        # signed: positive means a > b, relative to b
    overlapping: bool     # do the two ranges intersect at all?
    reading: str


@dataclass(frozen=True)
class Disqualification:
    """One structural reason a method is unfit to anchor a conclusion. Every one is
    traced to a measured number, never to a judgement about the result -- a method is
    disqualified by HOW it was built, not by whether its answer looks unwelcome."""
    method: Method
    reason: str
    measured: str  # the actual number that triggered it


@dataclass(frozen=True)
class AnchorRecommendation:
    """What the warnings support, computed independently of what the caller stated."""
    method: Method | None  # None = nothing here is fit to anchor
    eligible: list[Method]
    disqualified: list[Disqualification]
    rationale: str


@dataclass(frozen=True)
class Conclusion:
    ranges: list[MethodRange]
    divergences: list[Divergence]
    recommendation: AnchorRecommendation
    stated_anchor: Method | None = None
    stated_reasoning: str = ""
    anchor_conflict: str = ""  # non-empty when stated and derived anchors disagree
    market_price: float | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def anchor_method(self) -> Method | None:
        """The anchor actually in force: the analyst's if stated, else the derived one.
        A stated anchor always wins -- the tool reports the conflict, it does not
        overrule the person using it."""
        return self.stated_anchor or self.recommendation.method

    @property
    def anchor_range(self) -> tuple[float, float] | None:
        m = self.anchor_method
        if m is None:
            return None
        r = next((x for x in self.ranges if x.method is m), None)
        return (r.low, r.high) if r else None


def _overlaps(a: MethodRange, b: MethodRange) -> bool:
    return a.low <= b.high and b.low <= a.high


def compare(a: MethodRange, b: MethodRange) -> Divergence:
    """Classification is driven by the MIDPOINT gap first, with overlap as secondary
    evidence -- not the other way round.

    Overlap alone is close to worthless as a convergence signal, a defect found by
    running the real pipeline rather than by reasoning about it: two methods whose own
    ranges each span ~100% of their midpoint will overlap almost regardless of whether
    they agree about anything. An earlier version of this function reported a 100%
    midpoint gap as "Partial ... not a contradiction" purely because the wide ranges
    touched, while the same output separately warned that both ranges had low
    discriminating power -- it contradicted itself in one report. Overlap is now only
    allowed to soften the reading when the ranges are tight enough for the overlap to
    mean something."""
    gap = (a.mid - b.mid) / b.mid if b.mid else float("inf")
    overlapping = _overlaps(a, b)
    # An overlap is only informative if neither range is so wide that it would overlap
    # nearly anything. 60% matches the width threshold `diagnose` warns at, so the two
    # functions cannot disagree about whether a range is too wide to be discriminating.
    meaningful_overlap = overlapping and max(a.width_pct, b.width_pct) <= 0.60

    if abs(gap) >= 0.50:
        reading = (f"Disagree. Midpoints differ {abs(gap):.0%} -- a gap this large is a "
                   f"contradiction regardless of whether the ranges touch. "
                   + ("These ranges do overlap, but both are wide enough that the "
                      "overlap carries no information. " if overlapping else "")
                   + "This needs an explanation, not an average.")
    elif not overlapping:
        reading = (f"Disagree. Ranges do not overlap at all and midpoints differ "
                   f"{abs(gap):.0%} -- at least one method's inputs are doing something "
                   f"the other's are not. This gap needs an explanation, not an average.")
    elif abs(gap) < 0.15 and meaningful_overlap:
        reading = ("Converge. Ranges overlap, both are reasonably tight, and midpoints "
                   "are within 15% -- the two methods are telling the same story, which "
                   "raises confidence in both.")
    elif abs(gap) < 0.15:
        reading = (f"Inconclusive. Midpoints are within {abs(gap):.0%}, but at least one "
                   f"range spans more than 60% of its midpoint -- too wide for the "
                   f"agreement to be evidence of anything. Tighten the inputs before "
                   f"reading this as convergence.")
    else:
        reading = (f"Partial. Midpoints differ {abs(gap):.0%} and the ranges overlap "
                   f"-- a real difference in central estimate, not a contradiction."
                   + ("" if meaningful_overlap else
                      " Note the overlap is weak evidence here: at least one range is "
                      "wider than 60% of its midpoint."))

    return Divergence(method_a=a.method, method_b=b.method, gap_pct=gap,
                       overlapping=overlapping, reading=reading)


def diagnose(ranges: list[MethodRange], terminal_value_share: float | None = None,
              equity_weight_in_wacc: float | None = None,
              precedent_tier_spread: float | None = None,
              comps_multiple_spread: float | None = None,
              comps_peer_count: int | None = None) -> list[str]:
    """Mechanical warnings traced to actual inputs -- not narrative colour. Each one
    names a specific number that makes a specific method less trustworthy, so the
    reader discounts the right method rather than distrusting the whole exercise."""
    warnings = []

    if terminal_value_share is not None and terminal_value_share > 0.75:
        warnings.append(
            f"DCF: terminal value is {terminal_value_share:.0%} of enterprise value. "
            f"The conclusion is primarily a function of the terminal growth and WACC "
            f"assumptions, not the explicit forecast. Treat the DCF range as an "
            f"assumption-sensitivity output, not an independent cash-flow finding.")

    if equity_weight_in_wacc is not None and equity_weight_in_wacc > 0.85:
        warnings.append(
            f"DCF: equity is {equity_weight_in_wacc:.0%} of the capital structure in "
            f"WACC, so cost of debt barely affects the discount rate. Beta selection "
            f"carries almost the entire discount-rate decision -- a 0.1 change in beta "
            f"moves the answer more than any debt assumption.")

    if precedent_tier_spread is not None and precedent_tier_spread > 2.0:
        warnings.append(
            f"Precedent: sourced deals spread {precedent_tier_spread:.1f}x on identical "
            f"subject financials. There is no single defensible 'precedent multiple' "
            f"here -- the range is a function of which deal you pick, so it should be "
            f"read as a scenario set, not a valuation.")

    if comps_multiple_spread is not None and comps_multiple_spread > 2.0:
        warnings.append(
            f"Comps: peer multiples span {comps_multiple_spread:.1f}x from cheapest to "
            f"richest. A median across peers that disagree by more than 2x is not a "
            f"valuation, it is an average of companies in different situations -- the "
            f"spread itself is the finding, and the set is not comparable enough to "
            f"anchor a conclusion.")

    if comps_peer_count is not None and comps_peer_count < 5:
        warnings.append(
            f"Comps: only {comps_peer_count} peers in the set. One company's idiosyncratic "
            f"year moves the median materially at this sample size.")

    for r in ranges:
        if r.width_pct > 0.60:
            warnings.append(
                f"{r.method.value}: own range spans {r.width_pct:.0%} of its midpoint "
                f"-- wide enough that it may overlap every other method regardless of "
                f"whether they actually agree. Low discriminating power.")

    return warnings


def recommend_anchor(ranges: list[MethodRange], terminal_value_share: float | None = None,
                      precedent_tier_spread: float | None = None,
                      comps_multiple_spread: float | None = None,
                      comps_peer_count: int | None = None) -> AnchorRecommendation:
    """Derive which method is structurally fit to anchor, from measured properties only.

    Every threshold here matches the one `diagnose` warns at, so the two can never
    disagree about whether a method has a problem -- only about whether that problem is
    merely worth flagging or is outright disqualifying.

    The important behaviour is the empty case. When every method trips a disqualifying
    condition this returns method=None rather than the least-bad option, because a
    least-bad anchor reads to a reviewer exactly like a good one: the output format is
    identical, the hedging lives in a caveat nobody reads, and a number gets quoted. A
    tool that always names an anchor will eventually name a wrong one with full
    confidence. Reporting that nothing here is fit to anchor is a real answer -- it says
    the ranges are worth reading individually and the methods are not yet good enough to
    be combined into a verdict."""
    disqualified: list[Disqualification] = []
    present = {r.method for r in ranges}

    if Method.DCF in present:
        if terminal_value_share is not None and terminal_value_share > 0.75:
            disqualified.append(Disqualification(
                Method.DCF,
                "terminal value dominates: the conclusion restates the WACC and terminal "
                "growth assumptions rather than reading the forecast cash flows",
                f"terminal value {terminal_value_share:.0%} of EV (threshold 75%)"))

    if Method.PRECEDENT in present:
        if precedent_tier_spread is not None and precedent_tier_spread > 2.0:
            disqualified.append(Disqualification(
                Method.PRECEDENT,
                "sourced deals disagree too widely to form a range -- the answer is a "
                "function of which deal is picked, and a control premium is embedded "
                "that a trading valuation should not carry",
                f"{precedent_tier_spread:.1f}x spread between deals (threshold 2.0x)"))

    if Method.TRADING_COMPS in present:
        if comps_multiple_spread is not None and comps_multiple_spread > 2.0:
            disqualified.append(Disqualification(
                Method.TRADING_COMPS,
                "peer set is not comparable enough: a median across peers that disagree "
                "by more than 2x averages companies in different situations",
                f"{comps_multiple_spread:.1f}x spread across peers (threshold 2.0x)"))
        elif comps_peer_count is not None and comps_peer_count < 5:
            disqualified.append(Disqualification(
                Method.TRADING_COMPS,
                "peer set too small for the median to be stable",
                f"{comps_peer_count} peers (threshold 5)"))

    # Range width disqualifies any method: a range spanning more than 60% of its own
    # midpoint overlaps almost anything, so it cannot discriminate between hypotheses
    # even when its inputs are sound.
    for r in ranges:
        if r.width_pct > 0.60 and not any(d.method is r.method for d in disqualified):
            disqualified.append(Disqualification(
                r.method,
                "own range too wide to discriminate -- it would overlap competing "
                "methods regardless of whether they actually agree",
                f"range spans {r.width_pct:.0%} of its midpoint (threshold 60%)"))

    dq_methods = {d.method for d in disqualified}
    eligible = [r.method for r in ranges if r.method not in dq_methods]

    if not eligible:
        return AnchorRecommendation(
            method=None, eligible=[], disqualified=disqualified,
            rationale=(
                "No method here is structurally fit to anchor a conclusion -- every one "
                "trips a disqualifying condition listed above. Read the three ranges "
                "individually against the market price rather than treating any single "
                "one as a verdict. The honest output of this run is the divergence and "
                "its causes, not a target price."))

    # Among eligible methods, prefer the tightest range -- narrower means more
    # discriminating, and all eligible methods have already cleared the same structural
    # bars, so width is the remaining discriminator rather than a preference for one
    # methodology over another.
    by_width = sorted((r for r in ranges if r.method in eligible), key=lambda r: r.width_pct)
    winner = by_width[0]
    return AnchorRecommendation(
        method=winner.method, eligible=eligible, disqualified=disqualified,
        rationale=(
            f"{winner.method.value} is the only method clearing every structural bar"
            if len(eligible) == 1 else
            f"{winner.method.value} clears every structural bar and has the tightest "
            f"range ({winner.width_pct:.0%} of its midpoint) among "
            f"{len(eligible)} eligible methods"))


def triangulate(ranges: list[MethodRange], anchor: Method | None = None,
                 reasoning: str = "", market_price: float | None = None,
                 terminal_value_share: float | None = None,
                 equity_weight_in_wacc: float | None = None,
                 precedent_tier_spread: float | None = None,
                 comps_multiple_spread: float | None = None,
                 comps_peer_count: int | None = None) -> Conclusion:
    """`anchor` and `reasoning` are now OPTIONAL, and this is a deliberate reversal of
    the module's original position, worth stating rather than quietly changing.

    The original argument was that choosing which method to trust is the analytical act
    this tool exists to support, so the tool should never make that choice itself. That
    argument is still right about who decides. It was wrong about what an unchecked
    stated anchor does in practice: the rationale supplied here survived three
    consecutive runs, unchanged, while the underlying data moved enough to invalidate
    it twice -- once claiming comps were trustworthy because their inputs were
    "observed", while the same report warned the comps range spanned 383% of its
    midpoint.

    So: the caller's anchor still WINS when supplied -- the tool does not overrule the
    analyst. But `recommend_anchor` computes what the measured warnings support,
    independently, and a contradiction is reported rather than resolved. The analyst
    keeps the decision and loses the ability to make it silently.

    Supplying no anchor is a legitimate choice, not a degraded mode: the derived
    recommendation stands on its own, including when it concludes no method is fit."""
    if not ranges:
        raise ValueError("No method ranges supplied -- nothing to triangulate.")

    by_method = {r.method for r in ranges}
    if len(by_method) != len(ranges):
        raise ValueError("Duplicate methods supplied -- one range per method.")
    if anchor is not None and anchor not in by_method:
        raise ValueError(
            f"Anchor {anchor.value} has no range supplied. Available: "
            f"{[r.method.value for r in ranges]}")
    if anchor is not None and not reasoning.strip():
        raise ValueError(
            "An anchor without stated reasoning is an unsupported conclusion. Supply "
            "why this method is the one to weight, or pass no anchor and let the "
            "derived recommendation stand on its own.")

    divergences = []
    ordered = sorted(ranges, key=lambda r: r.method.value)
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            divergences.append(compare(a, b))

    warnings = diagnose(ranges, terminal_value_share, equity_weight_in_wacc,
                         precedent_tier_spread, comps_multiple_spread, comps_peer_count)
    recommendation = recommend_anchor(ranges, terminal_value_share, precedent_tier_spread,
                                       comps_multiple_spread, comps_peer_count)

    conflict = ""
    if anchor is not None:
        dq = next((d for d in recommendation.disqualified if d.method is anchor), None)
        if dq is not None:
            conflict = (
                f"STATED ANCHOR IS DISQUALIFIED. You anchored on {anchor.value}, but it "
                f"fails a structural check: {dq.reason} ({dq.measured}). The stated "
                f"anchor stands -- this tool does not overrule you -- but the rationale "
                f"above should address this directly, or the anchor should change. "
                f"Derived recommendation: "
                f"{recommendation.method.value if recommendation.method else 'no method is fit to anchor'}.")
        elif recommendation.method is not None and recommendation.method is not anchor:
            conflict = (
                f"Stated anchor ({anchor.value}) differs from the derived recommendation "
                f"({recommendation.method.value}). Both clear every structural bar, so "
                f"this is a defensible difference of judgement rather than an error -- "
                f"but the rationale should say why this method over the other.")

    return Conclusion(
        ranges=ranges, divergences=divergences, recommendation=recommendation,
        stated_anchor=anchor, stated_reasoning=reasoning, anchor_conflict=conflict,
        market_price=market_price, warnings=warnings,
    )


def format_football_field(conclusion: Conclusion, width: int = 48) -> str:
    """Text football-field. Deliberately text, not a chart image: it renders in a
    README, in a terminal, and in a diff, and it needs no plotting dependency."""
    lines = []
    all_lo = min(r.low for r in conclusion.ranges)
    all_hi = max(r.high for r in conclusion.ranges)
    if conclusion.market_price is not None:
        all_lo = min(all_lo, conclusion.market_price)
        all_hi = max(all_hi, conclusion.market_price)
    span = (all_hi - all_lo) or 1.0

    def pos(value):
        return max(0, min(width - 1, int((value - all_lo) / span * (width - 1))))

    lines.append(f"{'':<24}{all_lo:>8,.0f}{'':<{width - 16}}{all_hi:>8,.0f}")
    for r in conclusion.ranges:
        bar = [" "] * width
        for i in range(pos(r.low), pos(r.high) + 1):
            bar[i] = "="
        bar[pos(r.mid)] = "|"
        if r.method is conclusion.anchor_method:
            marker = (" <-- anchor (stated)" if conclusion.stated_anchor is not None
                      else " <-- anchor (derived)")
        elif any(d.method is r.method for d in conclusion.recommendation.disqualified):
            marker = " (disqualified)"
        else:
            marker = ""
        lines.append(f"{r.method.value:<24}{''.join(bar)}  "
                      f"{r.low:,.0f}-{r.high:,.0f}{marker}")

    if conclusion.market_price is not None:
        bar = [" "] * width
        bar[pos(conclusion.market_price)] = "*"
        lines.append(f"{'Market price':<24}{''.join(bar)}  "
                      f"{conclusion.market_price:,.0f}")

    return "\n".join(lines)


def format_conclusion(conclusion: Conclusion) -> str:
    out = ["=" * 78, "VALUATION CONCLUSION", "=" * 78, ""]
    out.append(format_football_field(conclusion))
    out.append("")

    out.append("-- Method detail " + "-" * 60)
    for r in conclusion.ranges:
        out.append(f"\n{r.method.value}: {r.low:,.2f} - {r.high:,.2f} (mid {r.mid:,.2f})")
        out.append(f"  basis:      {r.basis}")
        out.append(f"  provenance: {r.provenance}")
        out.append(f"  caveat:     {r.caveat}")

    out.append("\n-- Divergence " + "-" * 63)
    for d in conclusion.divergences:
        out.append(f"\n{d.method_a.value} vs {d.method_b.value}: {d.gap_pct:+.0%}")
        out.append(f"  {d.reading}")

    if conclusion.warnings:
        out.append("\n-- Structural warnings " + "-" * 54)
        for w in conclusion.warnings:
            out.append(f"\n  ! {w}")

    rec = conclusion.recommendation
    out.append("\n-- Anchor assessment " + "-" * 56)
    if rec.disqualified:
        out.append("\nDisqualified from anchoring:")
        for d in rec.disqualified:
            out.append(f"  x {d.method.value}: {d.reason}")
            out.append(f"      measured: {d.measured}")
    if rec.eligible:
        out.append(f"\nEligible to anchor: {', '.join(m.value for m in rec.eligible)}")
    out.append(f"\nDerived recommendation: "
                f"{rec.method.value if rec.method else 'NONE -- no method is fit to anchor'}")
    out.append(f"  {rec.rationale}")

    out.append("\n-- Conclusion " + "-" * 63)
    if conclusion.stated_anchor is not None:
        rng = conclusion.anchor_range
        out.append(f"\nStated anchor: {conclusion.stated_anchor.value} "
                    f"({rng[0]:,.2f} - {rng[1]:,.2f})")
        out.append(f"\n{conclusion.stated_reasoning}")
    elif rec.method is not None:
        rng = conclusion.anchor_range
        out.append(f"\nAnchor (derived): {rec.method.value} ({rng[0]:,.2f} - {rng[1]:,.2f})")
    else:
        out.append("\nNo anchor. " + rec.rationale)

    if conclusion.anchor_conflict:
        out.append(f"\n  !! {conclusion.anchor_conflict}")

    if conclusion.market_price is not None:
        mp = conclusion.market_price
        rng = conclusion.anchor_range
        if rng is None:
            lo = min(r.low for r in conclusion.ranges)
            hi = max(r.high for r in conclusion.ranges)
            out.append(f"\nMarket price {mp:,.2f}. Across all methods the ranges span "
                        f"{lo:,.2f} - {hi:,.2f}; with no method fit to anchor, that span "
                        f"is the honest statement of what this analysis supports.")
        else:
            lo, hi = rng
            if mp < lo:
                stance = f"below the anchor range ({(lo / mp - 1):.0%} below the low end)"
            elif mp > hi:
                stance = f"above the anchor range ({(mp / hi - 1):.0%} above the high end)"
            else:
                stance = "inside the anchor range"
            out.append(f"\nMarket price {mp:,.2f} sits {stance}.")
    out.append("")
    return "\n".join(out)
