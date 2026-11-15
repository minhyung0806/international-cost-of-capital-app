# International Cost of Capital — Streamlit

An educational app based on Sergio Rebelo's **International Valuation** lecture (2026), PDF pp. 22–29. Compare project equity costs, borrowing costs, and WACC in USD and a selected country's currency.

## Quick start

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

## Deploy

1. Create a **public GitHub repository** (for example `international-cost-of-capital`).
2. Upload `app.py`, `requirements.txt`, and `README.md` to its root, or push using Git.
3. Visit https://share.streamlit.io, choose **Create app**, select the repository, branch `main`, and entrypoint `app.py`.
4. Deploy and open the app. No API key is required. The server must have internet access to the public source websites.
5. Press **Refresh all sources** to clear cached data and attempt fresh downloads.

## Default settings

- Industry: Software (System & Application), if the Damodaran dataset loads.
- Target country: United States.
- Project tax jurisdiction: United States.
- Equity risk premium: 6.5%.
- Debt financing: 30%.

## Method

Let `d = D/(D+E)`, `tau = project tax rate`, `ERP = market equity risk premium`, and `beta_u = industry unlevered beta`.

```
D/E = d / (1-d)
beta_e = beta_u * [1 + (1-tau)*D/E]
Re_USD = Rf_USD + beta_e * ERP
Rd_USD = Rf_USD + spread_USD
F = (1+Rf_local)/(1+Rf_USD)
Re_local = (1+Re_USD)*F - 1
spread_local = spread_USD * F  # unless manually overridden
Rd_local = Rf_local + spread_local
WACC = (1-d)*Re + d*Rd*(1-tau)
```

Rates are expressed as decimals inside calculations and percentage points in the UI. A single selected project tax rate, ERP, and target debt share are used in both currencies. The local spread is the **gross borrowing-rate conversion** from lecture page 28, not a separate country credit risk estimate.

## Sources and limitations

- [Damodaran U.S. industry betas](https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls) — unlevered beta.
- [Damodaran rating spreads](https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html) — Baa2/BBB large non-financial benchmark.
- [OECD corporate tax statistics](https://www.oecd.org/en/topics/sub-issues/corporate-tax-statistics.html) — combined statutory rates, when an accessible structured table can be parsed.
- [Trading Economics government bonds](https://tradingeconomics.com/bonds) — country 10-year yields, when a public HTML table can be parsed.

**Important:** OECD and Trading Economics may change their page structure or serve JavaScript-rendered tables. The app does not manufacture missing values: it displays source errors and lets you enter manually verified observations. The supplied 2024 OECD article is historical and should not be silently represented as current. Source retrieval timestamps are not the same as observation dates. Before submitting the assignment, verify that the latest OECD tax data and the intended BBB row were fetched correctly. For reliable production use, replace fragile page scraping with stable machine-readable official data endpoints when available.

Country coverage is the union of countries returned by OECD and Trading Economics, plus the U.S.; a country lacking a source observation requires manual input. Government yields are not strictly risk-free in all countries. No additional country equity premium is included. No live data is bundled or hard-coded.

## Validation against lecture Colombia example (pp. 25–28)

Manually set beta_u = 0.80, d = 30%, tau = 35%, ERP = 6.5%, U.S. yield = 4%, Colombia yield = 12%, and U.S. BBB spread = 1.5 percentage points. Expected:

| Output | USD | COP |
|---|---:|---:|
| Levered beta | 1.022857 | 1.022857 |
| Equity cost | 10.6486% | 19.1600% |
| Pretax debt cost | 5.5000% | 13.6154% |
| After-tax debt cost | 3.5750% | 8.8500% |
| WACC | 8.5265% | 16.0670% |

The app calculates using full precision; displayed values are rounded. If the target country is United States, the two columns should agree when the same yields and spreads are used.

## Troubleshooting

- **No beta table:** Damodaran may change the workbook layout; manually override beta.
- **No tax table:** OECD may offer only PDF/interactive data on the accessed page; manually override tax.
- **No bond yields:** Trading Economics may use client-side rendering or block scraping; manually override yields.
- **No BBB spread:** Confirm the correct large non-financial Baa2/BBB row and enter it manually.
- **No calculation:** All required market inputs must be available or overridden.

## Downloads

Two CSV download buttons: selected-project inputs/results, and all-industry comparison (when the industry dataset is available).
