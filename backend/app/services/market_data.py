"""Provider boundary. US Yahoo chart data is best-effort and not licensed for redistribution.
NGX is intentionally fail-closed pending authorized NGX API credentials/contract.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import os
import math
from pathlib import Path
import pandas as pd
import requests
from .risk import HORIZON_RETURN_COUNTS
from .risk import annual_rate_to_daily_rate

@dataclass
class MarketData:
    prices: pd.Series
    volume: pd.Series
    benchmark: pd.Series
    source: str
    retrieved_at: str
    period: str = ""

PERIOD_RETURN_COUNTS={"1Y":252,"2Y":504,"3Y":756,"5Y":1260}

def fetch_us(ticker: str, benchmark: str="SPY", horizon: str="medium_term", period: str|None=None) -> MarketData:
    try:
        import yfinance as yf
    except ImportError as e: raise RuntimeError("US data provider missing. Install requirements.txt (yfinance).") from e
    selected_period=period.upper() if period else None
    if selected_period and selected_period not in PERIOD_RETURN_COUNTS: raise ValueError("period must be 1Y, 2Y, 3Y or 5Y")
    if not selected_period and horizon not in HORIZON_RETURN_COUNTS: raise ValueError("horizon must be medium_term or short_term")
    cache_dir=Path(os.getenv("RAD_YFINANCE_CACHE_DIR",Path(__file__).resolve().parents[3]/".cache"/"yfinance"))
    cache_dir.mkdir(parents=True,exist_ok=True)
    if hasattr(yf,"cache") and hasattr(yf.cache,"set_cache_location"):
        yf.cache.set_cache_location(str(cache_dir))
    symbol=ticker.strip().upper(); bmark=benchmark.strip().upper()
    if selected_period:
        count=PERIOD_RETURN_COUNTS[selected_period]
        years=int(selected_period[0]); request_start=datetime.now(timezone.utc).date()-timedelta(days=years*366+45)
        request_end=datetime.now(timezone.utc).date()+timedelta(days=1)
        history_args={"start":request_start.isoformat(),"end":request_end.isoformat()}
        response_period=selected_period
    else:
        count=HORIZON_RETURN_COUNTS[horizon]
        history_args={"period":"2y" if horizon=="medium_term" else "6mo"}
        response_period="2Y" if horizon=="medium_term" else "6M"
    def history(s):
        try:
            frame=yf.Ticker(s).history(**history_args, auto_adjust=True, actions=True)
        except Exception as exc:
            detail=str(exc).lower()
            if "429" in detail or "rate limit" in detail: reason="provider rate limit"
            elif "timeout" in detail or "connect" in detail or "proxy" in detail: reason="network/proxy failure"
            else: reason="provider request failure"
            raise RuntimeError(f"Yahoo Finance {reason} while retrieving {s} history.") from None
        def column(name):
            if isinstance(frame.columns,pd.MultiIndex):
                if name in frame.columns.get_level_values(0): value=frame[name]
                elif name in frame.columns.get_level_values(1): value=frame.xs(name,axis=1,level=1)
                else: return None
                if isinstance(value,pd.DataFrame):
                    if s in value.columns: value=value[s]
                    elif value.shape[1]==1: value=value.iloc[:,0]
                    else: return None
                return value
            return frame[name] if name in frame.columns else None
        if frame is None or not isinstance(frame,pd.DataFrame) or frame.empty: raise RuntimeError(f"Yahoo Finance returned no historical data for {s}; verify the ticker is valid and US-listed.")
        raw_close=column("Close")
        if raw_close is None: raise RuntimeError(f"No closing-price column returned for {s}.")
        close=raw_close.dropna()
        close.index=pd.DatetimeIndex(pd.to_datetime(close.index))
        if close.index.tz is not None: close.index=close.index.tz_localize(None)
        close.index=close.index.normalize()
        if close.index.has_duplicates: raise RuntimeError(f"Duplicate daily rows returned for {s}; data is ambiguous.")
        if not close.index.is_monotonic_increasing: raise RuntimeError(f"Unordered daily rows returned for {s}.")
        raw_volume=column("Volume")
        if raw_volume is not None:
            raw_volume=raw_volume.copy()
            raw_volume.index=pd.DatetimeIndex(pd.to_datetime(raw_volume.index))
            if raw_volume.index.tz is not None: raw_volume.index=raw_volume.index.tz_localize(None)
            raw_volume.index=raw_volume.index.normalize()
            volume=raw_volume.reindex(close.index)
        else:
            volume=pd.Series(index=close.index,dtype=float)
        return close, volume
    p,v=history(symbol)
    if symbol==bmark: b=p.copy()
    else: b,_=history(bmark)
    # N daily returns require N+1 closes. Keep the requested window exactly.
    p=p.tail(count+1); v=v.reindex(p.index)
    if p.empty or (datetime.now(timezone.utc).date()-p.index[-1].date()).days>7:
        raise RuntimeError(f"Latest {symbol} quote is missing or stale (older than 7 calendar days).")
    if not p.empty: b=b.loc[(b.index>=p.index[0])&(b.index<=p.index[-1])]
    return MarketData(p,v,b,"Yahoo Finance via yfinance (best-effort; non-institutional; check terms)",datetime.now(timezone.utc).isoformat(),response_period)

def fetch_ngx(ticker: str, benchmark: str="NGXASI", period: str="5y") -> MarketData:
    endpoint=os.getenv("NGX_API_BASE_URL"); token=os.getenv("NGX_API_TOKEN")
    if not endpoint or not token:
        raise RuntimeError("NGX data unavailable: configure an authorized NGX Market Data API subscription with NGX_API_BASE_URL and NGX_API_TOKEN. NGX officially offers historical EOD quotes, but no credentials or endpoint contract are configured. No substitute data was used.")
    raise RuntimeError("NGX credentials are configured, but the licensed API endpoint schema must be mapped in market_data.py before querying; no undocumented endpoint is called.")

def fetch_us_tbill() -> dict:
    """Latest available 3-month Treasury constant-maturity yield from FRED DGS3MO."""
    key=os.getenv("FRED_API_KEY")
    if not key: raise RuntimeError("Automatic US risk-free rate unavailable: set FRED_API_KEY for the official FRED observations API, or supply an explicit risk-free override.")
    try:
        response=requests.get("https://api.stlouisfed.org/fred/series/observations",params={"series_id":"DGS3MO","api_key":key,"file_type":"json","sort_order":"desc","limit":100},timeout=15)
        response.raise_for_status()
    except requests.RequestException:
        # Never propagate the request URL: FRED requires the key in query params,
        # and HTTP exception text may include that URL.
        raise RuntimeError("FRED DGS3MO request failed (network or HTTP status). API credentials were omitted.") from None
    try:
        payload=response.json()
    except (ValueError,TypeError):
        raise RuntimeError("FRED DGS3MO returned invalid JSON.") from None
    observations=payload.get("observations") if isinstance(payload,dict) else None
    if not isinstance(observations,list):
        raise RuntimeError("FRED DGS3MO response has no valid observations list.")
    valid=[]
    for row in observations:
        if not isinstance(row,dict) or row.get("value") in (None,"."): continue
        try:
            observation_date=datetime.strptime(row["date"],"%Y-%m-%d").date()
            percent=float(row["value"])
            annual_yield=percent/100
            if observation_date>datetime.now(timezone.utc).date() or not math.isfinite(annual_yield) or annual_yield<=-1: continue
        except (KeyError,TypeError,ValueError,OverflowError):
            continue
        valid.append((observation_date,annual_yield))
    if not valid: raise RuntimeError("FRED DGS3MO returned no usable observations.")
    observation_date,annual_yield=max(valid,key=lambda item:item[0])
    return {"annual_yield":annual_yield,"daily_risk_free_rate":annual_rate_to_daily_rate(annual_yield),
            "observation_date":observation_date.isoformat(),"series":"DGS3MO","source":"Federal Reserve Bank of St. Louis FRED"}

def fetch(market: str, ticker: str, benchmark: str, horizon: str="medium_term", period: str|None=None) -> MarketData:
    if market=="US": return fetch_us(ticker,benchmark,horizon,period)
    if market=="NGX": return fetch_ngx(ticker,benchmark)
    raise ValueError("market must be US or NGX")
