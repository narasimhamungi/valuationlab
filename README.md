# ValuationLab

An integrated valuation engine that runs a DCF, trading comparables, precedent
transactions, and a segment-weighted sum-of-the-parts on the same company from the same
source data — and reports **which methods can be trusted and why**, instead of averaging
them into one number.

Built on [Trellis](https://github.com/narasimhamungi/trellis), which handles SEC EDGAR
XBRL ingestion, 3-statement construction, and the 5-year driver-based forecast.
ValuationLab consumes Trellis as an installed package and adds the valuation layer.

---

## The problem

Every valuation method disagrees with every other one, and most valuation tooling
resolves this by averaging — producing a single number whose precision is fictional and
whose disagreement, the only genuinely informative part, has been deleted. Worse: most
tools don't check whether a method's own inputs were ever fit to produce an answer in the
first place. A DCF that's 80% terminal value, a comps set spanning 3x on the same peer
group, a precedent sample of two deals with a control premium baked in — none of these
disqualify the method in a typical model. They just quietly make the number wrong.

## Why it matters

In an interview or a committee memo, "the DCF says $X" is worth nothing on its own. The
answer that demonstrates competence is "the DCF says $X, but it's 81% terminal value, so
I'd treat it as a sensitivity check, not a finding — and the comps set is too dispersed to
trust either." That judgment is the actual skill. This tool is built to make the inputs
that judgment depends on impossible to skip past.

## What was built

Six modules:

| Module | Responsibility |
|---|---|
| `dcf.py` | CAPM WACC, unlevered FCF off Trellis's forecast, Gordon terminal value, sensitivity grid |
| `comps.py` | Peer EV/Revenue and EV/EBITDA multiples with exclusion transparency |
| `precedent.py` | Sourced M&A deals with tier discipline; every figure traceable to a filing |
| `marketdata.py` | Live-or-snapshot market data with provenance and staleness enforcement |
| `triangulate.py` | Football field, divergence classification, and a **derived anchor recommendation** that disqualifies methods on measured thresholds rather than defaulting to whichever the analyst names |
| `segments.py` | Segment-weighted sum-of-the-parts for diversified filers, with per-leg fitness tracking |

Plus a cross-module consistency diagnostic, an end-to-end runner with live and offline
modes, and a CI job that checks out Trellis at HEAD and runs the real cross-repo
integration — not a mock.

## Data & tools

- **Fundamentals** — SEC EDGAR company-facts XBRL, via Trellis. Subject company and all
  peers go through the identical pipeline.
- **Market data** — share price and share count from a provider at run time, or from a
  committed dated snapshot. Both paths return the same type; `source` always reports
  which you got.
- **Deal data** — acquirer 8-K/10-K filings and target results releases, read and
  computed manually. Deal multiples aren't in XBRL and aren't available free via API; the
  alternative is a paid database or fabrication.
- **Segment data** — hardcoded from the 10-K with a citation, the same discipline
  `precedent.py` applies to deal data, since segment revenue isn't in the consolidated
  XBRL facts Trellis ingests. Checked against the consolidated figure on every run — see
  Methodology.
- **Stack** — Python 3.10+, stdlib only for the core. `yfinance` is an optional extra;
  the offline path needs no market-data dependency at all.

## Methodology

**DCF.** Unlevered FCF = NOPAT + D&A − capex − ΔNWC, every input read from Trellis's
forecast. WACC from CAPM, with cost of debt taken from Trellis's own `interest_rate`
driver — the company's embedded borrowing cost, never an assumed credit spread. Beta,
risk-free rate, and ERP are supplied by the caller with a required citation string; the
module refuses to fetch them, because a provider's beta is a black box and fetching it
would launder a judgment call into something that looks measured.

**Trading comps.** EV/EBITDA and EV/Revenue across SEC-filing peers. Multiples are
computed on last reported fiscal year, not LTM — a disclosed limitation, not a hidden one
(see Known limitations). Median is the headline over mean, and every excluded peer is
recorded with a reason.

**Precedent transactions.** Deals are tiered, not pooled — `MATURE_REVENUE` targets have
a large profitable revenue base, `PLATFORM_PIPELINE` targets are valued on pipeline
optionality where multiples on current financials are close to meaningless. The tiers are
never averaged together.

**Sum-of-the-parts.** For a filer diversified enough that a single peer set can't value
it, each reportable segment is valued against its own dedicated peer group instead of
being forced into one blended multiple. Segment revenue is hardcoded from the 10-K and
checked against Trellis's consolidated figure on every run — a relative tolerance, not an
absolute one, after a real bug where a millions-vs-raw-dollars mismatch produced a gap in
the tens of billions that an absolute-dollar check wouldn't have caught at every possible
scale. **Revenue multiples only** — segment EBITDA isn't cleanly disclosed (see Known
limitations). Every leg tracks its own fitness independently: a leg whose peer group is
too dispersed to trust is flagged, and the total states plainly what fraction of revenue
rests on a sound peer group rather than blending a good leg and a bad one into one number.

**Triangulation.** Three ranges are kept separate, each with its own provenance and
caveat. Divergence is classified by midpoint gap first, with range overlap as secondary
evidence — overlap between two ranges that each span more than 60% of their own midpoint
carries no information and is never read as agreement. The anchor is **derived**, not
asserted: each method is checked against measured, numeric disqualifying conditions
(terminal value share, peer dispersion, deal spread, own-range width), and when every
method trips one, the tool reports that no method is fit to anchor rather than picking the
least-bad option. A caller-supplied anchor still wins if given — the analyst's judgment is
never overruled — but a contradiction between what's stated and what the numbers support
is surfaced loudly, not silently absorbed.

## Key findings

**A single peer set cannot value Johnson & Johnson, and the evidence rules out
small-sample noise as the explanation.** Measured on identical methodology against the
same subject company:

| Peer set | EV/Revenue spread | EV/EBITDA spread |
|---|---|---|
| Pharma (PFE, MRK, ABBV, BMY) | 2.5x | 2.9x |
| Pharma + LLY, AMGN (6 peers) | **4.7x** | **4.3x** |
| MedTech (MDT, BDX, SYK, BSX) | **1.5x** | 1.4x |

Adding more pharma peers made the dispersion *worse*, not better — the opposite of what
sampling noise would produce. The dispersion is economic: pharma valuation is dominated by
company-specific patent-cliff timing (Pfizer and BMS pricing for decline, AbbVie and Lilly
for growth), which doesn't average into a meaningful median. Medtech businesses share
demand drivers and replacement cycles and genuinely cluster. **This is why the
sum-of-the-parts module exists**, and it's a finding no generic single-multiple comps repo
can produce, because producing it required noticing the dispersion, testing whether it
survived a larger sample, and building an architecture that reports per-segment fitness
instead of a single blended number.

**Applying that finding: the MedTech segment clears every structural bar on its own.**
J&J's MedTech segment ($33.8B FY2025 revenue) values at **~$101-157B enterprise value**
against its own peer group — a real, defensible number. The Innovative Medicine segment
does not clear the bar, and the tool says so explicitly rather than blending the two into
one company-wide figure that would hide which half is trustworthy.

**Two real, sourced, same-tier pharma precedent deals price 2.6x apart** (J&J/Actelion at
~12.3x EV/Revenue vs. BMS/Celgene at ~4.8x), proving there is no single defensible
"pharma precedent multiple" — enforced by a test, so a future third deal can't quietly
average the gap away.

**Building and validating this surfaced six real bugs in Trellis**, all latent on the
originally-validated consumer/tech companies and all invisible until this project applied
Trellis to pharma and medtech filers:

1. `operating_income`'s gap-fill derivation omitted R&D entirely, overstating J&J's
   margin by 15.5 points (42.7% to 27.2%) and comparably for Merck, Pfizer, and BMS.
2. The same omission existed **independently** in the forecast engine — fixing (1) alone
   left the DCF's output unchanged to the cent, because the DCF consumes only the
   forecast, not the historical table.
3. `depreciation_amortization` dropped acquired-intangible amortization for AbbVie
   entirely ($762M reported against a true ~$8.1B), inflating its EV/EBITDA to 32x.
4. `long_term_debt` had no fallback for filers that tag neither of its two standard
   forms — Medtronic and Boston Scientific failed ingestion completely on this one field.
5-6. Single-tag ingestion gaps for Stryker's accounts payable and Boston Scientific's
   accounts receivable, the latter confirmed as a true alias (not a broader scope
   requiring special handling) by reconciling it against the filer's own gross-receivable
   and allowance figures before adding it.

All six are fixed upstream in Trellis with regression tests, not worked around here.

## Insight demonstrated

Knowing that valuation methods disagree is not the same as knowing why, and knowing a
peer set is dispersed is not the same as knowing whether adding more peers would fix it or
confirm it's structural. Both were tested, not assumed, in this project. The tool also
refuses to produce a triangulated conclusion without a derived, numerically-grounded
anchor recommendation — a design position about what a valuation output should be, not
just a formatting choice. And building it surfaced upstream data-quality bugs that a
purely consumer/tech-validated pipeline had never been forced to confront — the kind of
finding that only comes from actually running a tool against real, messy data rather than
a clean synthetic example.

## Employer takeaway

This is a candidate who builds valuation infrastructure with the provenance discipline of
an audit workpaper — every multiple traceable to a filing, every simplification
documented in the code, every method's weakness reported alongside its number — and who
tests conclusions against a wider sample before trusting them, rather than stopping at the
first plausible-looking result. The engineering is real: package-level integration with a
separate repo, a cross-repo CI job that breaks if the upstream schema changes, a bug found
and fixed by running the pipeline rather than by reasoning about it (the divergence
classifier), and a units bug caught by a reconciliation check before it could ship a wrong
number. The finance judgment is real too: recognizing that J&J's segment structure — not
its tagging idiosyncrasies — was the actual reason no single comps model could value it,
and building the architecture to prove that rather than assert it.

---

## Quick start

```bash
pip install -e ".[dev]"
PYTHONPATH=src pytest tests/ -v
PYTHONPATH=src python scripts/diagnose_consistency.py
```

Full setup, live-run instructions, and verification steps: **[RUNNING.md](RUNNING.md)**.

## Honest limitations

Stated here rather than discovered by a reviewer:

- **CAPM's equity risk premium is dated.** Beta and the risk-free rate are current;
  the ERP is Damodaran's implied estimate as of January 2026, the last one with a fixed,
  citable source (his updates after that live only as a rolling, non-datestamped figure
  on his site). Paired with a September risk-free rate, this is a real ~8-month mismatch,
  disclosed in the CAPM basis string rather than silently absorbed.
- **Comps are annual, not LTM.** Standard practice is LTM; that needs quarterly XBRL
  ingestion Trellis does not currently produce.
- **SOTP uses revenue multiples only, not EBITDA.** J&J's segment income before tax
  excludes unallocated corporate expense and there's no segment D&A disclosure that maps
  to the EBITDA proxy `comps.py` builds from consolidated lines — producing a segment
  EBITDA would mean inventing a D&A allocation, exactly what this project exists to
  refuse. Revenue multiples ignore margin differences between segments, which is a real
  cost of this choice, not a free simplification.
- **Net debt uses long-term debt only.** Trellis's schema has no separate short-term debt
  line, so short-term borrowings sit inside `total_liabilities` and aren't captured.
- **Share count is basic, not diluted.**
- **Precedent sample is two deals.** Enough to demonstrate the tiering problem, not
  enough to form a stable range.
- **Abbott Laboratories is excluded from both peer sets.** Its ingestion fails on nine
  fields across every year from 2020-2025 — a broader ingestion problem, not a single
  missing tag, and outside the scope of what this project's tag-level fixes address.
- **EBITDA is built from reported lines**, not company-adjusted figures, so litigation
  and IPR&D charges are included — comparable across peers computed the same way, not
  comparable to an externally quoted adjusted EBITDA.
- **The equity bridge ignores minority interest and preferred stock.**

## License

MIT
