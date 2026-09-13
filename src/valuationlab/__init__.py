"""
ValuationLab: an integrated DCF / trading comparables / precedent transactions engine
built on Trellis's validated 3-statement forecasts.

The point of this package is not to produce a valuation number. It is to produce three
of them, keep them separate, and report why they disagree -- see triangulate.py, which
is the module the other four exist to feed.

Modules, in pipeline order:

    marketdata.py   price/share-count, live or snapshot, with provenance and staleness
    dcf.py          CAPM WACC, unlevered FCF off Trellis's forecast, terminal value
    comps.py        peer multiples with exclusion transparency
    precedent.py    sourced M&A deals, tiered, every figure traceable to a filing
    triangulate.py  football field, divergence diagnosis, stated conclusion
"""

__version__ = "0.1.0"
