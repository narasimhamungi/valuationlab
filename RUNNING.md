# Running and verifying ValuationLab

Follow these in order. Steps 1–4 need no network and no Trellis install. Steps 5–7 need
SEC EDGAR access and a market-data provider.

---

## Step 1 — Environment

Python 3.10 or newer.

```bash
cd valuationlab
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
```

## Step 2 — Install (offline mode)

The offline path deliberately requires **no Trellis and no market-data provider**. Install
only the test tooling:

```bash
pip install pytest
```

Everything below uses `PYTHONPATH=src` so no package install is needed for the offline
path. To install the package properly instead (this will pull Trellis from GitHub):

```bash
pip install -e ".[dev]"
```

## Step 3 — Run the test suite

A committed `conftest.py` adds `src/` to the path automatically, so plain `pytest`
works with no environment variable on any OS:

```bash
pytest tests/ -v
```

(`PYTHONPATH=src pytest tests/ -v` also still works, and is the only form that works on
PowerShell if `conftest.py` is ever missing from a checkout for some reason.)

**Expected:** `82 passed, 1 skipped`.

The skip is the Trellis integration contract test — correct behaviour without EDGAR
access. Anything else failing means something is wrong; read the assertion message, they
are written to explain the failure rather than just report it.

To see the suite by module:

```bash
pytest tests/ -v --tb=short
```

## Step 4 — Run the cross-module diagnostic

```bash
PYTHONPATH=src python scripts/diagnose_consistency.py
```

**Expected:** 9 `[PASS]` lines, then `All cross-module consistency checks passed.`
Exit code 0.

This checks what unit tests structurally cannot: that the three methods agree on shared
plumbing they each independently touch. Net debt is computed in three separate places —
drift between them would produce three per-share numbers that look comparable and aren't.

The most informative single check in the output:

```
[PASS] comps: median multiple applied to the median peer returns its own price
       -- got 180.0000, expected 180.0000
```

Apply the median peer multiple back to the median peer's own financials and you must get
that peer's own share price back. If this fails, the EV→equity bridge is broken.

---

## Step 5 — Credentials and CAPM inputs

**5a. SEC EDGAR user agent.** SEC requires an identifying user agent:

```bash
export TRELLIS_USER_AGENT="Your Name your@email.com"
```

**5b. Market data provider:**

```bash
pip install -e ".[live]"
```

**5c. CAPM inputs are already sourced for J&J**, in `scripts/run_valuation.py`'s
`CAPM = CAPMInputs(...)` block — not placeholders. Read the `basis=` string before
trusting the output; it states exactly what each figure is, where it came from, and one
disclosed limitation:

| Input | What's there now |
|---|---|
| `beta` | Blume-adjusted (0.33 + 0.67 × raw), averaged from two independent raw-beta sources, with the adjustment rationale stated |
| `risk_free_rate` | 10-year US Treasury close, dated |
| `equity_risk_premium` | Damodaran's implied ERP as of January 2026 — the last edition with a fixed, citable source. Paired with a current risk-free rate this is a real, disclosed mismatch, not a rounding issue |

**If you're valuing a different company**, replace all three with your own sourced
figures — the same discipline applies: state the lookback window and index for beta, not
a bare number; cite the ERP's date, don't just quote a figure; and write the actual
citation into `basis=`, since it's printed in the output and is the difference between a
defensible number and an invented one.

## Step 6 — Generate golden snapshots

```bash
python scripts/refresh_snapshots.py
```

This pulls the subject and every peer — pharma and medtech — through Trellis from SEC
EDGAR, runs the forecast, and writes dated fixtures to `data/snapshots/`, plus a market
snapshot for all of them.

**Expected:** nine `wrote <TICKER>_financials.json` lines, one `FAILED ABT` line
(expected — see below), and one `wrote market.json (10 tickers)`.

**Abbott Laboratories (ABT) is a known, permanent failure**, not something to chase here.
Its ingestion fails on nine fields across every year 2020-2025 — a broader
data-availability problem Trellis's tag-level fixes don't address. `run_valuation.py`
excludes it explicitly and states why.

**If any OTHER company fails,** the script reports which fields were missing rather than
aborting the whole run. That's a real Trellis tag-resolution issue, not a ValuationLab
bug — run Trellis's `scripts/diagnose_tags.py` against the blocking field, and if it
returns "no candidate list defined," query SEC's companyfacts API directly for that
CIK and field name to find what the filer actually uses (see Trellis's own commit
history for worked examples: R&D, D&A, long-term debt, payables, and receivables tags
were all found and fixed this way). Do not commit a partial snapshot set: the offline
run will silently drop missing peers from whichever comps group they belong to.

Review the diff before committing. Snapshots are never auto-refreshed in CI by design —
changing the numbers should be an explicit, reviewable commit, otherwise the fixture is a
moving target rather than a reproducibility contract.

## Step 7 — Run the valuation

Offline, from the snapshots you just generated:

```bash
PYTHONPATH=src python scripts/run_valuation.py --offline
```

Live, straight from EDGAR and the market provider:

```bash
PYTHONPATH=src python scripts/run_valuation.py --live
```

Refuse to run on stale market data:

```bash
PYTHONPATH=src python scripts/run_valuation.py --offline --max-market-age-days 30
```

### Reading the output

1. **Football field** — DCF, trading comps, and precedent transactions, plus the market
   price marker. Each range is labelled `(disqualified)` when it trips a structural bar,
   or `<-- anchor (stated)` / `<-- anchor (derived)` when it's the one in force. It's
   normal, not broken, for every method to show `(disqualified)` — see Anchor assessment.
2. **Method detail** — each range with its basis, provenance (both dates: filing year and
   market date), and structural caveat.
3. **Divergence** — pairwise, classified by midpoint gap. `Disagree` means a gap ≥50%,
   a contradiction regardless of whether the ranges touch; `Inconclusive` means the gap
   is small but at least one range is too wide (>60% of its midpoint) for that agreement
   to mean anything.
4. **Structural warnings** — mechanical flags traced to specific numbers.
5. **Anchor assessment** — which methods are disqualified and why, which are eligible,
   and the derived recommendation. `NONE` is a real, correctly-produced answer when every
   method fails its own bar — it is not an error and does not mean the run is broken.
6. **Conclusion** — the anchor actually in force (stated overrides derived if you passed
   one; a mismatch between the two is flagged explicitly, not silently resolved either
   way) and where the market price sits relative to it. When there's no anchor, this
   section reports the full span across all methods instead.
7. **Sum-of-the-parts** — a separate section below the main conclusion, only when the
   subject's base year matches the year `segments.py`'s hardcoded segment data is dated
   to (currently FY2025 for J&J). Each segment's peer group, multiple range, and implied
   EV are shown independently, with `[LEG NOT FIT]` on any segment whose peer group is
   too dispersed — read the closing line for what fraction of total revenue actually
   rests on a sound peer group before trusting the total.

### What to sanity-check first

- Is the base year the most recent fiscal year? If Trellis fell back further, a field is
  missing — check the snapshot's `drivers_note`.
- Did any peer get excluded? The reason is printed. On a four-peer group, one exclusion
  is material.
- Is terminal value share above 75%? Expected for a stable large-cap, but it determines
  how much weight the DCF deserves — and above that threshold the DCF disqualifies from
  anchoring entirely.
- Does the Anchor assessment section say `NONE`? That's a legitimate outcome, not a bug —
  check which disqualifications fired and whether they still make sense for the inputs
  you're running.
- **In the SOTP section:** does the revenue reconciliation show a gap of 0? If not, the
  hardcoded segment figures in `segments.py` are stale relative to whatever Trellis just
  ingested — don't value around a nonzero gap, fix the segment data. This check has
  already caught one real unit-mismatch bug (millions vs. raw dollars) before it could
  produce a silently wrong number.
- Does the market price sit inside or outside the anchor range (when one exists)?
  Outside is interesting; make sure you can explain it before publishing.

---

## Step 8 — CI

Two jobs in `.github/workflows/ci.yml`:

**`unit`** — installs *without* Trellis on purpose, then runs the suite and the
diagnostic. If this job starts failing on an import error, something below the data layer
has grown a hard Trellis dependency and the offline guarantee is broken.

**`integration`** — checks out Trellis at HEAD, installs it as a package, and runs
`tests/integration` against live EDGAR. This is the proof the coupling is real rather than
cosmetic. If Trellis renames a forecast field, this job breaks. A golden snapshot cannot
catch that — it is frozen, so it would keep passing forever against a schema that no
longer exists.

Set the repository secret `TRELLIS_USER_AGENT` for the integration job.

To run the integration tests locally:

```bash
pip install -e ".[dev]" git+https://github.com/narasimhamungi/trellis.git
export TRELLIS_USER_AGENT="Your Name your@email.com"
PYTHONPATH=src pytest tests/integration -v
```

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `ModuleNotFoundError: valuationlab` | Missing `PYTHONPATH=src`, or install with `pip install -e .` |
| `FileNotFoundError: No golden snapshot for JNJ` | Run Step 6 first |
| `StaleSnapshotError` | Snapshot older than `--max-market-age-days`. Re-run Step 6 or raise the limit deliberately |
| `403` from SEC | `TRELLIS_USER_AGENT` unset or malformed. SEC requires a real contact string |
| `No peers remain after exclusions` | Every peer hit a non-positive EBITDA proxy. Check the snapshots are real data, not empty |
| `WACC must exceed terminal_growth` | CAPM inputs produce a discount rate at or below `TERMINAL_GROWTH`. Check beta and ERP |
| `range must be ordered low<=mid<=high` | An upstream module produced something inconsistent. Fix there — the error deliberately does not sort it away |
| Integration tests skip | No Trellis install or no `TRELLIS_USER_AGENT`. Expected offline |
| `Segment revenue does not reconcile to consolidated` | The hardcoded segment data in `segments.py` is stale, the company reorganised its segments, or (this has happened once) the two figures are in different units. Fix the segment constants — never widen the tolerance to make this pass |
| `SOTP skipped: subject base year is FYxxxx` | `segments.py`'s hardcoded segment revenue is dated to a specific fiscal year (FY2025 for J&J). If Trellis's base year has since rolled forward, update the segment constants with the new year's 10-K figures before the SOTP section will run again |
| `git push` → `Recv failure: Connection was reset` | Not a git or GitHub problem — an HTTP/2 negotiation failure on some Windows/network combinations. Run `git config --global http.version HTTP/1.1` once, then retry |
| PowerShell mangles a pasted multi-line Python one-liner | Don't paste `python -c "..."` with embedded quotes into PowerShell — write the script to a `.py` file with a here-string (`@'` ... `'@ \| Set-Content`) and run that instead |

---

## Repository layout

```
valuationlab/
├── README.md                      portfolio-facing writeup
├── RUNNING.md                     this file
├── conftest.py                    puts src/ on the path so plain `pytest` works everywhere
├── pyproject.toml                 package manifest; trellis as a git dependency
├── .github/workflows/ci.yml       unit (offline) + integration (real Trellis)
├── src/valuationlab/
│   ├── dcf.py                     CAPM WACC, unlevered FCF, terminal value, sensitivity
│   ├── comps.py                   peer multiples, exclusion transparency, implied value
│   ├── precedent.py               sourced deals, tier discipline
│   ├── marketdata.py              live/snapshot provider, provenance, staleness
│   ├── triangulate.py             football field, divergence, derived anchor, conclusion
│   └── segments.py                segment-weighted SOTP, per-leg fitness tracking
├── scripts/
│   ├── run_valuation.py           end-to-end runner (--offline / --live)
│   ├── refresh_snapshots.py       regenerate golden fixtures (subject + both peer groups)
│   └── diagnose_consistency.py    cross-module consistency checks
├── tests/                         82 offline tests, one file per module above
│   └── integration/               Trellis contract (skips without EDGAR)
└── data/snapshots/                real, committed fixtures for 9 of 10 companies
                                    (ABT excluded — see its own README)
```
