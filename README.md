# ValuationLab

An integrated valuation engine that runs a DCF, trading comparables, and precedent
transactions on the same company from the same source data — and then reports **why the
three methods disagree** instead of averaging them into one number.

Built on [Trellis](https://github.com/narasimhamungi/trellis), which handles SEC EDGAR
XBRL ingestion, 3-statement construction, and the 5-year driver-based forecast.
ValuationLab consumes Trellis as an installed package and adds the valuation layer.

---

## The problem

Every valuation method disagrees with every other one. A DCF on a stable large-cap
frequently lands 30–40% away from where the same company's peers trade, and further
still from what an acquirer paid for a comparable business. Most valuation tooling
resolves this by averaging — producing a single number whose precision is fictional and
whose disagreement, the only genuinely informative part, has been deleted.

## Why it matters

In an interview or a committee memo, "the DCF says $X" is worth nothing on its own. The
answer that demonstrates competence is "the DCF says $X, but 82% of that is terminal
value, so I'd anchor on comps and treat the DCF as a sensitivity check." That judgment —
knowing which method to trust and being able to say why — is the actual skill. This tool
is built to surface the inputs that judgment depends on, not to hide them behind a
midpoint.

## What was built

Five modules, each independently tested:

| Module | Responsibility |
|---|---|
| `dcf.py` | CAPM WACC, unlevered FCF off Trellis's forecast, Gordon terminal value, sensitivity grid |
| `comps.py` | Peer EV/Revenue and EV/EBITDA multiples with exclusion transparency |
| `precedent.py` | Sourced M&A deals with tier discipline; every figure traceable to a filing |
| `marketdata.py` | Live-or-snapshot market data with provenance and staleness enforcement |
| `triangulate.py` | Football field, divergence classification, structural warnings, stated conclusion |

Plus a cross-module consistency diagnostic, an end-to-end runner with both live and
offline modes, and a cross-repo CI job that runs the real Trellis integration.

## Data & tools

- **Fundamentals** — SEC EDGAR company-facts XBRL, via Trellis. Subject company and all
  five peers go through the identical pipeline.
- **Market data** — share price and share count from a provider at run time, or from a
  committed dated snapshot. Both paths return the same type; `source` always reports
  which you got.
- **Deal data** — acquirer 8-K/10-K filings and target results releases, read and
  computed manually. Deal multiples are not in XBRL and not available free via API; the
  alternative to sourcing them this way is a paid database or fabrication.
- **Stack** — Python 3.10+, stdlib only for the core. `yfinance` is an optional extra;
  the offline path needs no market-data dependency at all.

## Methodology

**DCF.** Unlevered FCF = NOPAT + D&A − capex − ΔNWC, every input read from Trellis's
forecast rather than re-derived. WACC from CAPM, with cost of debt taken from Trellis's
own `interest_rate` driver — the company's embedded borrowing cost, either a cited
override or derived from reported interest expense, never an assumed credit spread.
Beta, risk-free rate, and ERP are supplied by the caller with a required citation string;
the module refuses to fetch them, because a provider's beta is a black box (unstated
lookback, index, and adjustment) and fetching it would launder a judgment call into
something that looks measured.

**Trading comps.** EV/EBITDA and EV/Revenue across five SEC-filing pharma peers.
Multiples are computed on last reported fiscal year, not LTM — Trellis produces annual
tables only, and building quarterly ingestion is a real scope addition rather than a free
upgrade. The consequence is disclosed: the denominator can be up to 12 months stale
against a current price, systematically overstating multiples for growing peers. Median
is the headline rather than mean, because a five-peer set is small enough that one
charge-depressed year moves the mean materially — both are reported so the gap is visible.
Every excluded peer is recorded with a reason; a comps set that silently drops peers is
indistinguishable from one that cherry-picked them.

**Precedent transactions.** Deals are tiered, not pooled. `MATURE_REVENUE` targets have a
large profitable revenue base; `PLATFORM_PIPELINE` targets are valued on pipeline
optionality, where multiples on current financials are close to meaningless. The tiers
are never averaged together.

**Triangulation.** Three ranges are kept separate, each carrying its own provenance and
its own structural caveat. Divergence is classified by midpoint gap first, with range
overlap as secondary evidence only when the ranges are tight enough for overlap to mean
anything. The anchor method and the reasoning for choosing it are **required arguments** —
there is no default and no auto-selected "best" method, because choosing which method to
trust is the analytical act this tool exists to support, and a tool that makes that choice
silently has taken the thinking away from the analyst and hidden it in a default.

## Key findings

**Two real, sourced, same-tier pharma deals price 2.6× apart.**

| Deal | EV | Target revenue | EV/Revenue |
|---|---|---|---|
| J&J / Actelion (Jan 2017) | $29.6B | CHF 2,412M (FY16) | **~12.3×** |
| BMS / Celgene (Jan 2019) | ~$74B | $15,281M (FY18) | **~4.8×** |

Both are mature-revenue pharma acquisitions. Both figures come from the acquirer's own
SEC filings. Applied to identical subject financials they imply per-share values 2.6×
apart. Actelion was a single-franchise, high-growth specialty biotech; Celgene was a
large diversified oncology/immunology business much closer to a mega-cap's growth and
scale profile. **There is no single defensible "pharma precedent multiple"** — the answer
is a function of which deal you pick. Any tool reporting one blended figure is hiding the
analysis rather than doing it. This is enforced by a test, so a future third deal cannot
quietly average the gap away.

**Terminal value dominance is measured, not assumed.** On a stable large-cap at a ~6.7%
WACC against 2.5% terminal growth, terminal value runs ~82% of enterprise value. The DCF
conclusion is therefore largely a restatement of two estimated assumptions rather than an
independent read on the forecast cash flows. The tool flags this automatically above 75%.

**Equity weight in WACC ~93%** on a low-leverage name, which means cost of debt barely
affects the discount rate and beta selection carries almost the entire decision. Also
flagged automatically.

## Insight demonstrated

Knowing that valuation methods disagree is not the same as knowing *why*. The mechanical
reasons here — terminal value share, denominator staleness, deal tier mismatch, embedded
control premium — are each traced to a specific number in the inputs, so a reader
discounts the right method instead of distrusting the whole exercise. The tool also
refuses to produce a conclusion without a stated anchor and stated reasoning, which is a
design position about what a valuation output should be.

## Employer takeaway

This is a candidate who builds valuation infrastructure with the provenance discipline of
an audit workpaper: every multiple traceable to a filing, every simplification documented
in the code rather than hidden, every method's weakness reported alongside its number.
The engineering is real — package-level integration with a separate repo, a cross-repo CI
job that breaks if the upstream schema changes, and a bug found and fixed by running the
pipeline rather than by reasoning about it. The finance judgment is real too: the
precedent-tier finding is the kind of observation that separates someone who has actually
thought about comparability from someone who has run a template.

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

- **CAPM inputs in `run_valuation.py` are placeholders**, explicitly labelled as such.
  They must be replaced with sourced figures before any conclusion is published.
- **Comps are annual, not LTM.** Standard practice is LTM; that needs quarterly XBRL
  ingestion Trellis does not currently produce.
- **Net debt uses long-term debt only.** Trellis's schema has no separate short-term debt
  line, so short-term borrowings sit inside `total_liabilities` and are not captured.
  This understates net debt and overstates equity value.
- **Share count is basic, not diluted.** Options, RSUs, and convertibles are not modeled;
  per-share values are correspondingly generous.
- **Precedent sample is two deals.** Enough to demonstrate the tier problem, not enough to
  form a range. A third mature-revenue deal is the next addition.
- **EBITDA is built from reported lines**, not company-adjusted figures, so litigation and
  IPR&D charges are included. Comparable across peers computed the same way; not
  comparable to an externally quoted adjusted EBITDA.
- **The equity bridge ignores minority interest and preferred stock.** Fine for the
  validated companies; not a general claim.

## License

MIT
