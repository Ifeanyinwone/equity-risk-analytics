from fastapi import FastAPI, HTTPException, Request as HttpRequest
import math
import os
import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, field_validator
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[2]/".env")
from .services.market_data import fetch,fetch_us_tbill
from .services.risk import analyze,annual_rate_to_daily_rate
from .services.decision import decide,load_policy
from .services.throttle import AnalysisRateLimiter
from .database import save_assessment,get_history

app=FastAPI(title="RAD — Risk Analytics and Decision",version="0.1.0",description="Equity risk decision-support API. See README for data and methodology limits.")
ROOT=Path(__file__).resolve().parents[2]
POLICY=ROOT/"config"/"risk_policies.yaml"
analysis_limiter=AnalysisRateLimiter(per_client_limit=5,global_limit=20,window_seconds=60)
def finite_json(value):
    if isinstance(value,dict): return {k:finite_json(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)): return [finite_json(v) for v in value]
    if isinstance(value,(float,np.floating)) and not math.isfinite(float(value)): return None
    if isinstance(value,np.generic): return value.item()
    return value

def _redact_fred_key(message:str)->str:
    key=os.getenv("FRED_API_KEY")
    return message.replace(key,"[redacted]") if key else message
class Request(BaseModel):
    ticker:str=Field(min_length=1,max_length=24)
    market:str="US"
    horizon:str="medium_term"
    benchmark:str="SPY"
    period:str="1Y"
    amount:float|None=Field(default=None,gt=0)
    risk_free_rate:float|None=Field(default=None,ge=-0.99,le=2)

    @field_validator("ticker")
    @classmethod
    def validate_ticker(cls,value):
        import re
        normalized=value.strip().upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.^=-]{0,19}",normalized):
            raise ValueError("Ticker contains unsupported characters.")
        return normalized

    @field_validator("benchmark")
    @classmethod
    def validate_benchmark(cls,value):
        normalized=value.strip().upper()
        if normalized not in {"SPY","QQQ"}: raise ValueError("US benchmark must be SPY or QQQ.")
        return normalized

    @field_validator("period")
    @classmethod
    def validate_period(cls,value):
        normalized=value.strip().upper()
        if normalized not in {"1Y","2Y","3Y","5Y"}: raise ValueError("period must be 1Y, 2Y, 3Y or 5Y.")
        return normalized

@app.get("/health")
def health(): return {"status":"ok","version":"0.1.0"}

@app.post("/analyze")
def run(req:Request,http_request:HttpRequest):
    client_id=http_request.headers.get("x-rad-client-id") or (http_request.client.host if http_request.client else "unknown")
    allowed,retry_after=analysis_limiter.allow(client_id)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Analysis limit reached. Please wait {retry_after} seconds before trying again.",
            headers={"Retry-After":str(retry_after)},
        )
    try:
        policy=load_policy(POLICY)
        if req.horizon not in policy: raise ValueError("horizon must be medium_term or short_term")
        data=fetch(req.market.upper(),req.ticker,req.benchmark,req.horizon,req.period)
        risk_free_meta=None; risk_free=req.risk_free_rate; rate_warning=None
        if risk_free is not None:
            risk_free_meta={"annual_yield":risk_free,"daily_risk_free_rate":annual_rate_to_daily_rate(risk_free),"observation_date":None,"source":"User-supplied override"}
        elif req.market.upper()=="US":
            try:
                risk_free_meta=fetch_us_tbill(); risk_free=risk_free_meta["annual_yield"]
            except Exception as rate_error:
                rate_warning=str(rate_error)
                rate_warning=_redact_fred_key(rate_warning)
        profile=policy[req.horizon]
        metrics=analyze(data.prices,data.benchmark,req.amount,risk_free,sortino_mar_annual=profile.get("sortino_mar_annual",0.0),performance_window=profile["lookback_returns"])
        metrics["sharpe"]=metrics["performance"].get("sharpe")
        metrics["sortino"]=metrics["performance"].get("sortino")
        volume=data.volume.tail(20).dropna()
        traded=(data.volume*data.prices).tail(20).dropna()
        metrics["avg_volume"]=float(volume.mean()) if len(volume) else None
        metrics["avg_traded_value"]=float(traded.mean()) if len(traded) else None
        metrics["volume_status"]="available" if len(volume) else "unavailable"
        result=decide(metrics,req.horizon,policy,profile["min_observations"])
        px=metrics["prices"]; rets=metrics["returns"]
        dd=metrics["drawdown_series"]
        rolling={n:rets.rolling(n).std(ddof=1)*np.sqrt(252) for n in (20,60,252)}
        series=[]
        for stamp,price in px.items():
            vol=data.volume.get(stamp,np.nan)
            series.append({"date":stamp.date().isoformat(),"adjusted_close":float(price),
                "volume":float(vol) if pd.notna(vol) else None,
                "benchmark_close":float(data.benchmark.get(stamp,np.nan)) if pd.notna(data.benchmark.get(stamp,np.nan)) else None,
                "drawdown":float(dd.get(stamp,np.nan)) if pd.notna(dd.get(stamp,np.nan)) else None,
                "rolling_volatility_20":float(rolling[20].get(stamp,np.nan)) if pd.notna(rolling[20].get(stamp,np.nan)) else None,
                "rolling_volatility_60":float(rolling[60].get(stamp,np.nan)) if pd.notna(rolling[60].get(stamp,np.nan)) else None,
                "rolling_volatility_252":float(rolling[252].get(stamp,np.nan)) if pd.notna(rolling[252].get(stamp,np.nan)) else None})
        payload={"ticker":req.ticker,"market":req.market.upper(),"horizon":req.horizon,"period":data.period or req.period,"benchmark":req.benchmark,"source":data.source,"retrieved_at":data.retrieved_at,"risk_free_rate":risk_free_meta,"risk_free_warning":rate_warning,"decision":result,"metrics":{k:v for k,v in metrics.items() if k not in ("returns","log_returns","drawdown_series","prices","monte_carlo","monte_carlo_by_horizon")},"monte_carlo":{k:v for k,v in metrics["monte_carlo"].items() if k!="terminal_returns"},"monte_carlo_by_horizon":{h:{k:v for k,v in result.items() if k!="terminal_returns"} for h,result in metrics["monte_carlo_by_horizon"].items()},"series":series}
        payload=finite_json(payload)
        save_assessment(req.ticker.upper(),req.market.upper(),req.horizon,payload)
        return payload
    except (RuntimeError,ValueError,KeyError) as e: raise HTTPException(status_code=422,detail=str(e)) from e

@app.get("/history")
def history(limit:int=100):
    if not 1<=limit<=1000: raise HTTPException(status_code=422,detail="limit must be in [1,1000]")
    rows=get_history(limit)
    if rows is None: raise HTTPException(status_code=503,detail="Set DATABASE_URL and apply Alembic migrations to enable history.")
    return rows
