import pytest

from valuationlab.triangulate import (
    Method,
    MethodRange,
    compare,
    diagnose,
    format_conclusion,
    format_football_field,
    recommend_anchor,
    triangulate,
)


def mr(method, low, mid, high, basis="b", caveat="c", provenance="p"):
    return MethodRange(method=method, low=low, mid=mid, high=high, basis=basis,
                        caveat=caveat, provenance=provenance)


def test_out_of_order_range_rejected():
    with pytest.raises(ValueError, match="must be ordered"):
        mr(Method.DCF, 100.0, 90.0, 120.0)


def test_width_pct():
    r = mr(Method.DCF, 80.0, 100.0, 120.0)
    assert r.width_pct == pytest.approx(0.40)


def test_compare_converge():
    a = mr(Method.DCF, 90.0, 100.0, 110.0)
    b = mr(Method.TRADING_COMPS, 95.0, 105.0, 115.0)
    d = compare(a, b)
    assert d.overlapping
    assert "Converge" in d.reading


def test_compare_partial_overlap_with_large_gap():
    a = mr(Method.DCF, 90.0, 100.0, 160.0)
    b = mr(Method.TRADING_COMPS, 140.0, 150.0, 200.0)
    d = compare(a, b)
    assert d.overlapping
    assert "Partial" in d.reading


def test_compare_non_overlapping_disagreement():
    a = mr(Method.DCF, 90.0, 100.0, 110.0)
    b = mr(Method.PRECEDENT, 300.0, 350.0, 400.0)
    d = compare(a, b)
    assert not d.overlapping
    assert "Disagree" in d.reading
    assert d.gap_pct < 0  # a is below b


def test_large_gap_is_a_contradiction_even_when_ranges_overlap():
    """Regression: found by running the real pipeline. Two wide ranges overlapped, and
    the classifier reported a 100% midpoint gap as 'not a contradiction' -- while the
    same report separately warned both ranges had low discriminating power. Overlap
    must never override a large midpoint gap."""
    a = mr(Method.PRECEDENT, 174.0, 310.0, 447.0)     # width 88%
    b = mr(Method.TRADING_COMPS, 105.0, 155.0, 308.0)  # width 131%
    d = compare(a, b)
    assert d.overlapping                # the ranges genuinely do overlap
    assert d.gap_pct == pytest.approx(1.0, rel=0.01)
    assert "Disagree" in d.reading      # ...and it is still a disagreement
    assert "carries no information" in d.reading


def test_tight_ranges_with_small_gap_still_converge():
    a = mr(Method.DCF, 95.0, 100.0, 110.0)
    b = mr(Method.TRADING_COMPS, 98.0, 105.0, 115.0)
    d = compare(a, b)
    assert "Converge" in d.reading


def test_small_gap_but_wide_range_is_inconclusive_not_convergent():
    """Agreement between two ranges that each span >60% of their midpoint is not
    evidence of anything -- it must not be reported as convergence."""
    a = mr(Method.DCF, 40.0, 100.0, 180.0)             # width 140%
    b = mr(Method.TRADING_COMPS, 50.0, 105.0, 190.0)   # width 133%
    d = compare(a, b)
    assert "Inconclusive" in d.reading
    assert "Converge" not in d.reading


def test_moderate_gap_with_wide_range_flags_weak_overlap_evidence():
    a = mr(Method.DCF, 50.0, 100.0, 160.0)             # width 110%
    b = mr(Method.TRADING_COMPS, 100.0, 130.0, 150.0)  # width 38%
    d = compare(a, b)
    assert "Partial" in d.reading
    assert "weak evidence" in d.reading


def test_overlap_threshold_matches_diagnose_width_warning():
    """The 60% overlap-credibility threshold in compare() and the 60% width warning in
    diagnose() must agree, or one function will call a range too wide to trust while
    the other treats its overlap as meaningful."""
    wide = mr(Method.DCF, 30.0, 100.0, 175.0)   # width 145%, well over 60%
    assert diagnose([wide])                      # diagnose warns
    other = mr(Method.TRADING_COMPS, 95.0, 102.0, 110.0)
    d = compare(wide, other)
    assert "Converge" not in d.reading            # compare refuses to call it agreement


def test_diagnose_flags_terminal_value_dominance():
    warnings = diagnose([mr(Method.DCF, 90.0, 100.0, 110.0)], terminal_value_share=0.82)
    assert any("terminal value is 82%" in w for w in warnings)


def test_diagnose_flags_equity_weight():
    warnings = diagnose([mr(Method.DCF, 90.0, 100.0, 110.0)], equity_weight_in_wacc=0.928)
    assert any("Beta selection" in w for w in warnings)


def test_diagnose_flags_precedent_spread():
    warnings = diagnose([mr(Method.PRECEDENT, 90.0, 100.0, 110.0)],
                         precedent_tier_spread=2.6)
    assert any("scenario set" in w for w in warnings)


def test_diagnose_flags_wide_own_range():
    warnings = diagnose([mr(Method.TRADING_COMPS, 50.0, 100.0, 180.0)])
    assert any("Low discriminating power" in w for w in warnings)


def test_diagnose_silent_when_nothing_is_wrong():
    assert diagnose([mr(Method.DCF, 95.0, 100.0, 105.0)],
                     terminal_value_share=0.55, equity_weight_in_wacc=0.60,
                     precedent_tier_spread=1.2) == []


def test_triangulate_requires_reasoning():
    with pytest.raises(ValueError, match="unsupported conclusion"):
        triangulate([mr(Method.DCF, 90.0, 100.0, 110.0)], anchor=Method.DCF,
                    reasoning="   ")


def test_triangulate_rejects_anchor_without_a_range():
    with pytest.raises(ValueError, match="no range supplied"):
        triangulate([mr(Method.DCF, 90.0, 100.0, 110.0)],
                    anchor=Method.TRADING_COMPS, reasoning="x")


def test_triangulate_rejects_duplicate_methods():
    with pytest.raises(ValueError, match="Duplicate methods"):
        triangulate([mr(Method.DCF, 90.0, 100.0, 110.0),
                     mr(Method.DCF, 80.0, 95.0, 120.0)],
                    anchor=Method.DCF, reasoning="x")


def test_triangulate_rejects_empty():
    with pytest.raises(ValueError, match="Nothing|nothing to triangulate"):
        triangulate([], anchor=Method.DCF, reasoning="x")


def test_triangulate_produces_pairwise_divergences():
    ranges = [mr(Method.DCF, 90.0, 100.0, 110.0),
              mr(Method.TRADING_COMPS, 95.0, 105.0, 115.0),
              mr(Method.PRECEDENT, 200.0, 250.0, 300.0)]
    c = triangulate(ranges, anchor=Method.TRADING_COMPS,
                     reasoning="Comps anchored: DCF is terminal-dominated.",
                     market_price=112.0, terminal_value_share=0.82,
                     equity_weight_in_wacc=0.93, precedent_tier_spread=2.6)
    assert len(c.divergences) == 3  # 3 methods -> 3 pairs
    assert c.anchor_method is Method.TRADING_COMPS
    assert c.anchor_range == (95.0, 115.0)
    assert len(c.warnings) == 3


def test_football_field_renders_all_methods_and_anchor():
    ranges = [mr(Method.DCF, 90.0, 100.0, 110.0),
              mr(Method.TRADING_COMPS, 95.0, 105.0, 115.0)]
    c = triangulate(ranges, anchor=Method.DCF, reasoning="r", market_price=112.0)
    field = format_football_field(c)
    assert "DCF" in field
    assert "Trading comparables" in field
    assert "<-- anchor" in field
    assert "Market price" in field
    assert "*" in field


def test_format_conclusion_includes_market_stance():
    ranges = [mr(Method.DCF, 90.0, 100.0, 110.0)]
    c = triangulate(ranges, anchor=Method.DCF, reasoning="r", market_price=140.0)
    text = format_conclusion(c)
    assert "above the anchor range" in text
    assert "VALUATION CONCLUSION" in text
    assert "provenance" in text


def test_market_price_inside_range_reported_as_such():
    c = triangulate([mr(Method.DCF, 90.0, 100.0, 110.0)], anchor=Method.DCF,
                     reasoning="r", market_price=100.0)
    assert "inside the anchor range" in format_conclusion(c)

# --- derived anchor recommendation -------------------------------------------


def test_recommend_anchor_disqualifies_dcf_on_terminal_value_dominance():
    ranges = [mr(Method.DCF, 95.0, 100.0, 110.0),
              mr(Method.TRADING_COMPS, 98.0, 105.0, 112.0)]
    rec = recommend_anchor(ranges, terminal_value_share=0.81)
    assert rec.method is Method.TRADING_COMPS
    assert Method.DCF not in rec.eligible
    dq = [d for d in rec.disqualified if d.method is Method.DCF][0]
    assert "81%" in dq.measured


def test_recommend_anchor_disqualifies_comps_on_peer_dispersion():
    ranges = [mr(Method.DCF, 95.0, 100.0, 110.0),
              mr(Method.TRADING_COMPS, 98.0, 105.0, 112.0)]
    rec = recommend_anchor(ranges, comps_multiple_spread=3.0)
    assert rec.method is Method.DCF
    dq = [d for d in rec.disqualified if d.method is Method.TRADING_COMPS][0]
    assert "3.0x" in dq.measured


def test_recommend_anchor_returns_none_when_everything_is_disqualified():
    """The behaviour that matters: with every method structurally unfit, the tool must
    decline to name an anchor rather than pick the least-bad one."""
    ranges = [mr(Method.DCF, 162.0, 239.0, 341.0),          # width 75%
              mr(Method.TRADING_COMPS, 96.0, 140.0, 296.0),  # width 143%
              mr(Method.PRECEDENT, 181.0, 326.0, 471.0)]     # width 89%
    rec = recommend_anchor(ranges, terminal_value_share=0.81,
                            precedent_tier_spread=2.6, comps_multiple_spread=3.0)
    assert rec.method is None
    assert rec.eligible == []
    assert len(rec.disqualified) == 3
    assert "not" in rec.rationale.lower() or "no method" in rec.rationale.lower()


def test_recommend_anchor_prefers_tightest_range_among_eligible():
    ranges = [mr(Method.DCF, 90.0, 100.0, 112.0),           # width 22%
              mr(Method.TRADING_COMPS, 95.0, 105.0, 110.0)]  # width 14%
    rec = recommend_anchor(ranges)
    assert rec.method is Method.TRADING_COMPS
    assert len(rec.eligible) == 2


def test_recommend_anchor_disqualifies_on_width_alone():
    ranges = [mr(Method.DCF, 40.0, 100.0, 180.0),            # width 140%
              mr(Method.TRADING_COMPS, 95.0, 105.0, 110.0)]
    rec = recommend_anchor(ranges)
    assert rec.method is Method.TRADING_COMPS
    assert any(d.method is Method.DCF and "midpoint" in d.measured
               for d in rec.disqualified)


# --- stated vs derived anchor ------------------------------------------------


def test_stated_anchor_wins_but_conflict_is_reported_when_disqualified():
    """Regression for the failure this redesign exists to prevent: a hardcoded anchor
    rationale survived three runs while the data moved enough to invalidate it. The
    stated anchor still stands -- the tool must not overrule the analyst -- but the
    contradiction has to be visible."""
    ranges = [mr(Method.DCF, 95.0, 100.0, 110.0),
              mr(Method.TRADING_COMPS, 96.0, 140.0, 296.0)]
    c = triangulate(ranges, anchor=Method.TRADING_COMPS,
                     reasoning="comps are observed rather than assumed",
                     comps_multiple_spread=3.0)
    assert c.anchor_method is Method.TRADING_COMPS       # stated anchor still in force
    assert "STATED ANCHOR IS DISQUALIFIED" in c.anchor_conflict
    assert "3.0x" in c.anchor_conflict
    assert c.recommendation.method is Method.DCF


def test_no_conflict_when_stated_matches_derived():
    ranges = [mr(Method.DCF, 95.0, 100.0, 110.0),
              mr(Method.TRADING_COMPS, 98.0, 105.0, 112.0)]
    c = triangulate(ranges, anchor=Method.DCF, reasoning="r",
                     comps_multiple_spread=3.0)
    assert c.anchor_conflict == ""


def test_defensible_difference_flagged_softly_when_both_eligible():
    ranges = [mr(Method.DCF, 90.0, 100.0, 112.0),
              mr(Method.TRADING_COMPS, 95.0, 105.0, 110.0)]
    c = triangulate(ranges, anchor=Method.DCF, reasoning="r")
    assert "difference of judgement" in c.anchor_conflict
    assert "DISQUALIFIED" not in c.anchor_conflict


def test_anchor_is_optional_and_derived_stands_alone():
    ranges = [mr(Method.DCF, 95.0, 100.0, 110.0),
              mr(Method.TRADING_COMPS, 98.0, 105.0, 112.0)]
    c = triangulate(ranges, terminal_value_share=0.81)
    assert c.stated_anchor is None
    assert c.anchor_method is Method.TRADING_COMPS
    assert c.anchor_conflict == ""


def test_stated_anchor_still_requires_reasoning():
    ranges = [mr(Method.DCF, 95.0, 100.0, 110.0)]
    with pytest.raises(ValueError, match="unsupported conclusion"):
        triangulate(ranges, anchor=Method.DCF, reasoning="   ")


def test_no_anchor_case_reports_full_span_against_market():
    ranges = [mr(Method.DCF, 162.0, 239.0, 341.0),
              mr(Method.TRADING_COMPS, 96.0, 140.0, 296.0),
              mr(Method.PRECEDENT, 181.0, 326.0, 471.0)]
    c = triangulate(ranges, market_price=265.58, terminal_value_share=0.81,
                     precedent_tier_spread=2.6, comps_multiple_spread=3.0)
    assert c.anchor_method is None
    assert c.anchor_range is None
    text = format_conclusion(c)
    assert "no method is fit to anchor" in text.lower()
    assert "96.00 - 471.00" in text


def test_diagnose_flags_comps_dispersion_and_small_peer_set():
    ws = diagnose([mr(Method.TRADING_COMPS, 95.0, 105.0, 112.0)],
                   comps_multiple_spread=3.0, comps_peer_count=4)
    assert any("3.0x from cheapest to richest" in w for w in ws)
    assert any("only 4 peers" in w for w in ws)
