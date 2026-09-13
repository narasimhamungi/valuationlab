"""
DCF engine consuming Trellis's forecast output directly -- no re-derivation of revenue,
margins, or working capital. Trellis's `forecast.project_year` return schema (verified
against its actual source, not assumed) already carries every field a standard
unlevered-FCF build needs: operating_income, income_tax_expense, depreciation_amortization,
capex, accounts_receivable, inventory, accounts_payable. This module's only job is to
turn that into unlevered free cash flow, discount it at a CAPM-derived WACC, and report
a sensitivity grid -- not to touch anything Trellis already validated.

Explicit modeling choices, stated rather than buried in the arithmetic:

- Effective tax rate applied to EBIT is derived from LEVERED pretax income
  (income_tax_expense / (operating_income - interest_expense)), then applied to
  unlevered EBIT. This is the standard practitioner shortcut, not a fully unlevered
  tax recomputation -- it slightly overstates NOPAT when interest expense is material,
  because the levered pretax base is smaller than EBIT, but it avoids fabricating a
  hypothetical all-equity tax return that Trellis's schema was never built to produce.
  Flagged here so it's a documented approximation, not a silent one.
- Cost of debt uses Trellis's `drivers.interest_rate` -- for the five validated
  companies this is either a cited sourced override (e.g. Nike's FY2020 10-K debt
  schedule) or a derived interest_expense / average LT debt ratio. Either way it is
  the company's own embedded borrowing cost, not an assumed credit spread.
- Debt weight in WACC uses BOOK value of long-term debt (from the base year's actual
  balance sheet), not market value. Standard simplification when the debt isn't
  separately quoted; understates leverage for a name trading debt at a discount to par
  and vice versa. Not corrected for here.
- Terminal value uses Gordon growth on the final explicit-year UFCF. No exit-multiple
  cross-check is built into this module -- that's the trading-comps module's job, and
  the whole point of ValuationLab is comparing the two independently, not blending them
  into one number before the comparison happens.
- Net debt for the equity bridge is long_term_debt - cash_and_equivalents from the base
  year. Minority interest and preferred stock are not modeled -- fine for the five
  validated companies (none carry a material NCI or preferred balance), not a general
  claim this bridge is complete for an arbitrary filer.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MarketData:
    """Everything Trellis's schema doesn't cover: current market pricing and the CAPM
    inputs. Sourced separately (market data provider), not derived from filings."""
    share_price: float
    shares_outstanding: float
    beta: float
    risk_free_rate: float
    equity_risk_premium: float
    as_of: str  # ISO date string -- provenance for the market snapshot, since these
    # inputs go stale in a way filing-derived figures don't


@dataclass(frozen=True)
class UnleveredFCF:
    year: int
    ebit: float
    effective_tax_rate: float
    nopat: float
    depreciation_amortization: float
    capex: float
    delta_nwc: float
    ufcf: float


@dataclass(frozen=True)
class WACCResult:
    cost_of_equity: float
    cost_of_debt_pretax: float
    cost_of_debt_aftertax: float
    weight_equity: float
    weight_debt: float
    wacc: float


@dataclass(frozen=True)
class DCFResult:
    ufcf_by_year: list[UnleveredFCF]
    wacc: WACCResult
    terminal_growth: float
    pv_explicit_fcf: float
    pv_terminal_value: float
    enterprise_value: float
    net_debt: float
    equity_value: float
    implied_share_price: float


def unlevered_fcf(base_year_actuals: dict, forecast_year: dict, prior_year: dict,
                   year: int) -> UnleveredFCF:
    """Build one year of unlevered FCF from a single year of Trellis's forecast output
    plus the immediately prior year (base-year actuals or the previous forecast year)
    for the working-capital delta. `forecast_year` and `prior_year` are entries from
    the dict Trellis's `run_forecast` returns -- same schema as `project_year`'s return
    value, verified against trellis/src/trellis/forecast.py directly."""
    ebit = forecast_year["operating_income"]
    pretax = ebit - forecast_year["interest_expense"]
    effective_tax_rate = (forecast_year["income_tax_expense"] / pretax
                           if pretax else 0.0)
    nopat = ebit * (1 - effective_tax_rate)

    nwc_now = (forecast_year["accounts_receivable"] + forecast_year["inventory"]
               - forecast_year["accounts_payable"])
    nwc_prior = (prior_year["accounts_receivable"] + prior_year["inventory"]
                 - prior_year["accounts_payable"])
    delta_nwc = nwc_now - nwc_prior

    ufcf = (nopat + forecast_year["depreciation_amortization"]
            - forecast_year["capex"] - delta_nwc)

    return UnleveredFCF(
        year=year, ebit=ebit, effective_tax_rate=effective_tax_rate, nopat=nopat,
        depreciation_amortization=forecast_year["depreciation_amortization"],
        capex=forecast_year["capex"], delta_nwc=delta_nwc, ufcf=ufcf,
    )


def build_ufcf_series(base_year_actuals: dict, forecast: dict[int, dict]) -> list[UnleveredFCF]:
    """forecast: the dict `trellis.forecast.run_forecast` returns -- {year: year_dict}.
    base_year_actuals: table[base_year] from Trellis's historical AnnualTable, needed
    as the prior-year anchor for forecast year 1's working-capital delta."""
    years = sorted(forecast)
    series = []
    prior = base_year_actuals
    for year in years:
        series.append(unlevered_fcf(base_year_actuals, forecast[year], prior, year))
        prior = forecast[year]
    return series


def compute_wacc(market: MarketData, base_year_actuals: dict, interest_rate: float,
                  tax_rate: float) -> WACCResult:
    cost_of_equity = market.risk_free_rate + market.beta * market.equity_risk_premium
    cost_of_debt_aftertax = interest_rate * (1 - tax_rate)

    market_cap = market.share_price * market.shares_outstanding
    book_debt = base_year_actuals.get("long_term_debt", 0.0)
    total_capital = market_cap + book_debt
    weight_equity = market_cap / total_capital if total_capital else 1.0
    weight_debt = book_debt / total_capital if total_capital else 0.0

    wacc = weight_equity * cost_of_equity + weight_debt * cost_of_debt_aftertax
    return WACCResult(
        cost_of_equity=cost_of_equity, cost_of_debt_pretax=interest_rate,
        cost_of_debt_aftertax=cost_of_debt_aftertax, weight_equity=weight_equity,
        weight_debt=weight_debt, wacc=wacc,
    )


def run_dcf(base_year_actuals: dict, forecast: dict[int, dict], market: MarketData,
            interest_rate: float, tax_rate: float, terminal_growth: float) -> DCFResult:
    if terminal_growth < 0:
        raise ValueError("terminal_growth below 0% is not a stationarity assumption "
                          "this module accepts without an explicit override -- pass a "
                          "value >= 0.")

    ufcf_series = build_ufcf_series(base_year_actuals, forecast)
    wacc_result = compute_wacc(market, base_year_actuals, interest_rate, tax_rate)
    w = wacc_result.wacc
    if w <= terminal_growth:
        raise ValueError(f"WACC ({w:.4f}) must exceed terminal_growth "
                          f"({terminal_growth:.4f}) -- Gordon growth is undefined "
                          f"otherwise, not just numerically unstable.")

    pv_explicit = sum(u.ufcf / (1 + w) ** i for i, u in enumerate(ufcf_series, start=1))

    final_ufcf = ufcf_series[-1].ufcf
    terminal_value = final_ufcf * (1 + terminal_growth) / (w - terminal_growth)
    pv_terminal = terminal_value / (1 + w) ** len(ufcf_series)

    enterprise_value = pv_explicit + pv_terminal
    net_debt = base_year_actuals.get("long_term_debt", 0.0) - base_year_actuals.get(
        "cash_and_equivalents", 0.0)
    equity_value = enterprise_value - net_debt
    implied_share_price = equity_value / market.shares_outstanding

    return DCFResult(
        ufcf_by_year=ufcf_series, wacc=wacc_result, terminal_growth=terminal_growth,
        pv_explicit_fcf=pv_explicit, pv_terminal_value=pv_terminal,
        enterprise_value=enterprise_value, net_debt=net_debt,
        equity_value=equity_value, implied_share_price=implied_share_price,
    )


def sensitivity_grid(base_year_actuals: dict, forecast: dict[int, dict],
                      market: MarketData, interest_rate: float, tax_rate: float,
                      wacc_range: list[float], growth_range: list[float]
                      ) -> dict[tuple[float, float], float]:
    """Grid of implied share price over independently varied WACC and terminal growth.
    wacc_range overrides the CAPM-derived WACC directly (not beta/ERP) -- this grid is
    testing sensitivity to the discount rate and terminal assumption, not re-deriving
    CAPM inputs at each cell."""
    ufcf_series = build_ufcf_series(base_year_actuals, forecast)
    net_debt = base_year_actuals.get("long_term_debt", 0.0) - base_year_actuals.get(
        "cash_and_equivalents", 0.0)
    grid = {}
    for w in wacc_range:
        for g in growth_range:
            if w <= g:
                grid[(w, g)] = float("nan")
                continue
            pv_explicit = sum(u.ufcf / (1 + w) ** i for i, u in enumerate(ufcf_series, start=1))
            final_ufcf = ufcf_series[-1].ufcf
            terminal_value = final_ufcf * (1 + g) / (w - g)
            pv_terminal = terminal_value / (1 + w) ** len(ufcf_series)
            equity_value = pv_explicit + pv_terminal - net_debt
            grid[(w, g)] = equity_value / market.shares_outstanding
    return grid
