# Risk Analytics and Decision (RAD)

RAD is a standalone US equity risk assessment app. It contains a FastAPI calculation/data API and a Streamlit browser interface. It does not import, call, or modify EquityComps.

## Features

- Enter a US ticker; select 1Y, 2Y, 3Y, or 5Y data and SPY or QQQ as benchmark.
- Retrieve adjusted daily prices, volume, and benchmark prices from Yahoo Finance. The API returns actual price dates and return observation count, and reports unavailable volume as null.
- Retrieve the latest valid FRED DGS3MO observation for the US risk-free rate. The response identifies its annual yield, effective daily conversion, date, and source. If FRED is unavailable, risk-free-dependent ratios are unavailable with a warning; there is no zero-rate fallback. A direct API caller may explicitly supply `risk_free_rate`, which is labeled as a user override.
- Calculate simple returns, arithmetic and geometric annualized returns, sample annualized volatility, 20/60/252-session rolling volatility, historical/normal-parametric/bootstrap VaR and CVaR at 95%/99% for 1/5/10 sessions, beta, correlation, drawdown, Sharpe, Sortino, Treynor, Jensen alpha, and information ratio where the inputs suffice.
- Show configurable policy screening checks, price/drawdown/volatility charts, CSV/Excel downloads, and a PDF risk report.

Yahoo Finance is a best-effort source without an SLA. Review vendor terms for your intended data use. There is no NGX live integration because this project has no authorized account, endpoint schema, or credentials; requests outside supported US market data fail closed.

## Install and configure (Windows PowerShell)

Use Python 3.12 or 3.13. From the `rad` directory:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

The local `.env` file is ignored by Git. Keep actual credentials there only. If creating it for the first time, copy `.env.example` once, then enter the key in `.env`:

```powershell
Copy-Item .env.example .env
notepad .env
```

Set `FRED_API_KEY` in `.env` for automatic DGS3MO retrieval. `DATABASE_URL` is optional and enables stored assessment history after applying the Alembic migration. The NGX variables are unused until an authorized API integration is implemented.

## Start RAD

From the `rad` directory, the single local command is:

```powershell
.\run_rad.ps1 dashboard
```

Open the URL printed by the launcher (normally **http://localhost:8501**). If the default port is occupied, it selects a free port in the configured range. The launcher starts FastAPI on loopback and stops the instance it started when the dashboard exits. To run the API alone for development, use `.\run_rad.ps1 api`; it prints the corresponding docs URL. To check FRED or the full live integration, use `.\run_rad.ps1 fred-check` or `.\run_rad.ps1 live-check` from the `rad` directory.

The launcher adds FRED and Yahoo domains to both `NO_PROXY` and `no_proxy` for the launcher process and its children, preserving other proxy destinations and settings. It does not change Windows user/system variables or disable TLS certificate verification. API stdout/stderr logs are written under ignored `reports/runtime` without logging request URLs or API keys.

## Run tests

Unit/API tests use deterministic synthetic fixtures to verify mathematics and response behavior. Those tests do not substitute for live data checks. From `rad\backend`:

```powershell
..\.venv\Scripts\python.exe -m pytest tests -q
```

The real-data integration suite runs against Yahoo Finance and FRED (and requires network access and a configured FRED key):

```powershell
.\run_rad.ps1 live-check
```

It checks NVDA, AAPL, MSFT, AVGO, and SPY; SPY/QQQ benchmarks; all four selected periods; invalid ticker handling; and rendered Streamlit output. It uses real market data and fails if retrieval is unsuccessful.

## Calculation definitions and policy

- Daily returns are simple close-to-close adjusted-price returns. Trading-day annualization uses 252 sessions. Annualized arithmetic return is `mean(daily return) × 252`; annualized geometric return is `(product(1 + daily return))^(252 / N) − 1`. Annualized volatility is sample daily standard deviation times `sqrt(252)`.
- Sharpe is the annualized arithmetic mean daily excess return divided by sample daily excess-return standard deviation. DGS3MO is an annual effective decimal yield converted to an effective 252-session daily rate. The most recent observation is held constant over the analysis window, rather than represented as a historical daily yield series.
- Sortino's minimum acceptable return is a configurable annual rate in `config/risk_policies.yaml`, converted to an effective daily target; profile default is 0% annually.
- Historical multi-session returns are compounded overlapping windows. Parametric VaR/CVaR assumes IID normal daily returns, with mean scaled by days and standard deviation by the square root of days. Monte Carlo resamples observed daily returns with replacement and compounds them over each reported horizon (10,000 paths, fixed seed 42). All VaR/CVaR values are positive loss fractions; CVaR averages the worst tail with fractional boundary weighting.
- Beta and performance measures align stock and benchmark returns by date. The 60/252-day betas use the same trailing windows for each label; performance ratios use the configured profile lookback. Gaps longer than seven calendar days are excluded from daily return calculations.
- Thresholds in `config/risk_policies.yaml` are configurable policy preferences for screening. PASS/REVIEW/FAIL are not predictions, guarantees, or investment recommendations. Insufficient data produces unavailable values or an INSUFFICIENT DATA classification.

These estimates are historical and depend on provider data quality and modeling assumptions. They are not a VaR backtest or a forecast. The risk-free rate is the latest available observation and is not time-aligned to each past return.

## Optional PostgreSQL assessment history

Start the existing local database service and apply the migration from `rad\backend`:

```powershell
# Generate a local-only password for this PowerShell session; do not commit it.
$env:POSTGRES_PASSWORD = [guid]::NewGuid().ToString('N')
docker compose up -d postgres
$env:DATABASE_URL = "postgresql+psycopg://rad:$($env:POSTGRES_PASSWORD)@localhost:5432/rad"
Set-Location backend
..\.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade head
Set-Location ..
```

Without a configured database, calculations still work; the history panel reports that persistence is unavailable.

## Deployment preparation

`Dockerfile`, `.dockerignore`, `scripts/container_entrypoint.py`, and `render.yaml` prepare a single-container web service: Streamlit listens on the platform `PORT`, while FastAPI remains bound to container loopback. Render checks Streamlit's public `/_stcore/health` endpoint. The API applies an in-memory sliding-window limit of 5 analyses per client/session per minute and a service-wide ceiling of 20 per minute; limits reset with the instance and are not shared across multiple instances. The container launcher preserves proxy settings and adds only required no-proxy destinations. The image excludes `.env`; configure `FRED_API_KEY` as a secret in the hosting service. `render.yaml` uses `sync: false` so the hosting dashboard prompts for the secret instead of storing it in the file. Render expects web services to bind to `0.0.0.0` and the configured `PORT`, and its Blueprint format supports Docker services and secret variables ([web service port guidance](https://render.com/docs/web-services), [Blueprint spec](https://render.com/docs/blueprint-spec)).

To create a permanent URL, connect the RAD repository/root directory to a Render Blueprint and review the service plan and secret prompt before creating/deploying it. Deployment has not been run. Add a managed PostgreSQL service and a database URL if assessment history must persist; the current default deployment is stateless. Also review Yahoo's usage rights and the selected host's network/data policies before serving real user traffic. Use a domain, authentication, and operational monitoring before making the service public to users.

## Project layout

```text
rad/
  backend/app/               API, data retrieval, risk and decision services
  backend/tests/             deterministic math, FRED parsing, and API tests
  frontend/app.py            Streamlit UI
  config/risk_policies.yaml  configurable screening policies
  scripts/                   local checks and container entrypoint
  run_rad.ps1                local one-command launcher
  Dockerfile, render.yaml    deployment preparation
```
