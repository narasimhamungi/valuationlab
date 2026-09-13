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

```bash
PYTHONPATH=src pytest tests/ -v
```

**Expected:** `57 passed, 1 skipped`.

The skip is the Trellis integration contract test — correct behaviour without EDGAR
access. Anything else failing means something is wrong; read the assertion message, they
are written to explain the failure rather than just report it.

To see the suite by module:

```bash
PYTHONPATH=src pytest tests/ -v --tb=short
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

**5c. Replace the CAPM placeholders.** Open `scripts/run_valuation.py` and find the
`CAPM = CAPMInputs(...)` block. It ships labelled `PLACEHOLDER -- not yet sourced`. You
need three figures with a stated basis:

| Input | What to source |
|---|---|
| `beta` | State the lookback window and the index. "5-year monthly vs S&P 500" is a basis; a bare number is not. |
| `risk_free_rate` | 10-year US Treasury yield on your valuation date. |
| `equity_risk_premium` | A cited estimate with its own date — e.g. Damodaran's implied ERP. |

Write the actual citation into the `basis=` string. It is printed in the output and is
the difference between a defensible number and an invented one. The module will run with
the placeholders, but the conclusion is not publishable until they are replaced.

## Step 6 — Generate golden snapshots

```bash
python scripts/refresh_snapshots.py
```

This pulls J&J and all five peers through Trellis from SEC EDGAR, runs the forecast, and
writes dated fixtures to `data/snapshots/`, plus a market snapshot.

**Expected:** six `wrote <TICKER>_financials.json` lines and one `wrote market.json`.

**If a company fails,** the script reports which fields were missing rather than aborting
the whole run. That is a real Trellis tag-resolution issue, not a ValuationLab bug — run
Trellis's `scripts/diagnose_tags.py` against the blocking field. Do not commit a partial
snapshot set: the offline run will silently drop those peers from the comps range.

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

1. **Football field** — three ranges plus the market price marker, anchor labelled.
2. **Method detail** — each range with its basis, provenance (both dates: filing year and
   market date), and structural caveat.
3. **Divergence** — pairwise, classified by midpoint gap. `Disagree` means a gap ≥50%,
   which is a contradiction regardless of whether the ranges touch.
4. **Structural warnings** — mechanical flags traced to specific numbers: terminal value
   share above 75%, equity weight above 85%, precedent spread above 2×, any range wider
   than 60% of its midpoint.
5. **Conclusion** — anchor, range, stated reasoning, and where the market price sits.

### What to sanity-check first

- Is the base year the most recent fiscal year? If Trellis fell back further, a field is
  missing — check the snapshot's `drivers_note`.
- Did any peer get excluded? The reason is printed. One exclusion on a five-peer set is
  material.
- Is terminal value share above 75%? Expected for a stable large-cap, but it determines
  how much weight the DCF deserves.
- Does the market price sit inside or outside the anchor range? Outside is interesting;
  make sure you can explain it before publishing.

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

---

## Repository layout

```
valuationlab/
├── README.md                      portfolio-facing writeup
├── RUNNING.md                     this file
├── pyproject.toml                 package manifest; trellis as a git dependency
├── .github/workflows/ci.yml       unit (offline) + integration (real Trellis)
├── src/valuationlab/
│   ├── dcf.py                     CAPM WACC, unlevered FCF, terminal value, sensitivity
│   ├── comps.py                   peer multiples, exclusion transparency, implied value
│   ├── precedent.py               sourced deals, tier discipline
│   ├── marketdata.py              live/snapshot provider, provenance, staleness
│   └── triangulate.py             football field, divergence, warnings, conclusion
├── scripts/
│   ├── run_valuation.py           end-to-end runner (--offline / --live)
│   ├── refresh_snapshots.py       regenerate golden fixtures
│   └── diagnose_consistency.py    cross-module consistency checks
├── tests/                         57 offline tests
│   └── integration/               Trellis contract (skips without EDGAR)
└── data/snapshots/                empty until Step 6; no placeholder data committed
```
