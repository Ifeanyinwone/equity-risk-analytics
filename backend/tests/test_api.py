import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from app import main
from app.services.market_data import MarketData
from app.services.throttle import AnalysisRateLimiter


def test_analyze_api_serializes_timeseries_and_horizon_metrics(monkeypatch):
    index=pd.bdate_range("2024-01-01",periods=253)
    changes=.002*np.sin(np.arange(252)*.31)
    prices=pd.Series(100*np.r_[1,np.cumprod(1+changes)],index=index)
    benchmark=pd.Series(200*np.r_[1,np.cumprod(1+changes*.7)],index=index)
    volume=pd.Series(np.full(253,12345),index=index)
    data=MarketData(prices,volume,benchmark,"unit test fixture","2026-01-01T00:00:00Z","1Y")
    monkeypatch.setattr(main,"fetch",lambda *args,**kwargs:data)
    monkeypatch.setattr(main,"save_assessment",lambda *args,**kwargs:None)
    response=TestClient(main.app).post("/analyze",json={"ticker":"NVDA","market":"US","horizon":"medium_term","period":"1Y","benchmark":"SPY","risk_free_rate":.04})
    assert response.status_code==200
    payload=response.json()
    assert payload["metrics"]["observations"]==252
    assert len(payload["series"])==253
    assert payload["series"][-1]["volume"]==12345
    assert "terminal_returns" not in payload["monte_carlo"]
    assert all("terminal_returns" not in method for horizon in payload["monte_carlo_by_horizon"].values() for method in (horizon,))
    assert payload["metrics"]["risk_horizons"]["10"]["historical"]["var95"]>=0


def test_analyze_api_returns_clear_429_after_client_limit(monkeypatch):
    index=pd.bdate_range("2024-01-01",periods=253)
    changes=.002*np.sin(np.arange(252)*.31)
    prices=pd.Series(100*np.r_[1,np.cumprod(1+changes)],index=index)
    data=MarketData(prices,pd.Series(np.full(253,100),index=index),prices,"test source","2026-01-01T00:00:00Z","1Y")
    monkeypatch.setattr(main,"fetch",lambda *args,**kwargs:data)
    monkeypatch.setattr(main,"save_assessment",lambda *args,**kwargs:None)
    monkeypatch.setattr(main,"analysis_limiter",AnalysisRateLimiter(per_client_limit=2,global_limit=10,window_seconds=60))
    client=TestClient(main.app)
    payload={"ticker":"NVDA","market":"US","horizon":"medium_term","period":"1Y","benchmark":"SPY","risk_free_rate":.04}
    headers={"X-RAD-Client-ID":"test-client"}

    assert client.post("/analyze",json=payload,headers=headers).status_code==200
    assert client.post("/analyze",json=payload,headers=headers).status_code==200
    limited=client.post("/analyze",json=payload,headers=headers)
    assert limited.status_code==429
    assert "limit reached" in limited.json()["detail"].lower()
    assert int(limited.headers["Retry-After"])>=1


def test_analysis_limiter_enforces_service_wide_ceiling():
    limiter=AnalysisRateLimiter(per_client_limit=5,global_limit=2,window_seconds=60)
    assert limiter.allow("client-a",now=100)[0]
    assert limiter.allow("client-b",now=101)[0]
    allowed,retry=limiter.allow("client-c",now=102)
    assert not allowed
    assert retry==58
