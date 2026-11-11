import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm
from app.services.risk import returns,annual_vol,var_cvar,parametric_var_cvar,beta,drawdown,performance,monte_carlo,analyze
from app.services.decision import decide

def test_returns_manual():
    got=returns(pd.Series([100.,110.,99.]))
    assert got.simple.tolist()==pytest.approx([.1,-.1])
    assert got.log.tolist()==pytest.approx([np.log(1.1),np.log(.9)])

def test_returns_do_not_bridge_long_missing_data_gaps():
    p=pd.Series([100.,110.,55.],index=pd.to_datetime(["2026-01-02","2026-01-05","2026-01-20"]))
    got=returns(p)
    assert got.simple.tolist()==pytest.approx([.10])
    assert got.attrs["excluded_gap_returns"]==1

def test_annualized_volatility():
    r=pd.Series([-.01,.01,-.01,.01])
    assert annual_vol(r)==pytest.approx(r.std(ddof=1)*np.sqrt(252))
    assert np.isnan(annual_vol(r,252))

def test_historical_var_es():
    r=pd.Series(np.arange(-.10,.10,.01))
    var,es=var_cvar(r,.95)
    assert var==pytest.approx(.0905)
    assert es>=var

def test_parametric_var_manual_normal():
    r=pd.Series([-.01,0,.01])
    assert parametric_var_cvar(r,.95,1)[0]==pytest.approx(max(0,-(r.mean()+-1.6448536269514722*r.std(ddof=1))))

def test_beta_and_drawdown():
    b=pd.Series([-.02,0,.02]); s=2*b
    assert beta(s,b)==pytest.approx(2)
    d=drawdown(pd.Series([100.,120.,90.,95.]))
    assert d["max_drawdown"]==pytest.approx(-.25)
    assert d["current_drawdown"]==pytest.approx(-25/120)

def test_performance_constant_returns_no_sharpe():
    assert np.isnan(performance(pd.Series([.01,.01,.01]))["sharpe"])

def test_monte_carlo_reproducible():
    a=monte_carlo(pd.Series([-.1,.1]),horizon=4,paths=200,seed=7)
    b=monte_carlo(pd.Series([-.1,.1]),horizon=4,paths=200,seed=7)
    assert np.array_equal(a["terminal_returns"],b["terminal_returns"])
    assert a["paths"]==200

def test_decision_hard_breach_overrides_preferences():
    policy={"medium_term":{"checks":[{"metric":"volatility_252","limit":.35,"direction":"max","hard":False,"description":"soft"},{"metric":"cvar95","limit":.08,"direction":"max","hard":True,"description":"hard"}]}}
    got=decide({"observations":300,"volatility_252":.50,"cvar95":.10},"medium_term",policy)
    assert got["decision"]=="FAIL"

def test_insufficient_history():
    assert decide({"observations":20},"medium_term",{"medium_term":{"checks":[]}})["decision"]=="INSUFFICIENT DATA"

def test_constant_prices():
    with pytest.raises(ValueError): returns(pd.Series([10.,0.,10.]))

def test_horizon_trims_to_requested_return_count_and_reports_dates():
    idx=pd.bdate_range("2020-01-01",periods=1001)
    prices=pd.Series(100*np.cumprod(1+np.sin(np.arange(1000)*.19)*.01),index=idx[1:])
    # Include a base close so the 60 returns use 61 prices.
    prices=pd.concat([pd.Series([100.],index=idx[:1]),prices])
    bench=pd.Series(200*np.cumprod(1+np.cos(np.arange(1000)*.13)*.005),index=idx[1:])
    bench=pd.concat([pd.Series([200.],index=idx[:1]),bench])
    got=analyze(prices,bench,window=60,risk_free=.04)
    assert got["observations"]==60
    assert len(got["prices"])==61
    assert got["start_date"]==str(idx[-61].date())
    assert got["end_date"]==str(idx[-1].date())

def test_performance_beta_is_same_aligned_window_as_beta_metric():
    idx=pd.bdate_range("2022-01-01",periods=301)
    b=np.sin(np.arange(300)*.31)*.01
    s=np.r_[2*b[:48],1.25*b[48:]]
    bp=pd.Series(100*np.r_[1,np.cumprod(1+b)],index=idx)
    sp=pd.Series(100*np.r_[1,np.cumprod(1+s)],index=idx)
    got=analyze(sp,bp,window=252,risk_free=.04)
    assert got["beta_252"]==pytest.approx(1.25)
    assert got["performance"]["beta"]==pytest.approx(got["beta_252"])

def test_beta_252_unavailable_in_short_window_not_mislabeled():
    idx=pd.bdate_range("2023-01-01",periods=61)
    b=np.linspace(-.01,.01,60); bp=pd.Series(100*np.r_[1,np.cumprod(1+b)],index=idx)
    sp=pd.Series(100*np.r_[1,np.cumprod(1+2*b)],index=idx)
    got=analyze(sp,bp,window=60)
    assert got["beta_60"]==pytest.approx(2)
    assert np.isnan(got["beta_252"])
    assert got["performance"]["beta"]==pytest.approx(got["beta_60"])

def test_historical_var_uses_compounded_matching_horizon_and_positive_losses():
    r=pd.Series([-.10,.02,.03,.04,.05,.06,.07,.08,.09,.10])
    h5=(1+r).rolling(5).apply(np.prod,raw=True).dropna()-1
    expected_var=max(0.,-np.quantile(h5,.05,method="linear"))
    ordered=np.sort(h5.to_numpy()); mass=.05*len(ordered); whole=int(np.floor(mass)); frac=mass-whole
    tail_sum=ordered[:whole].sum()+ (frac*ordered[whole] if frac else 0)
    expected_cvar=max(0.,-tail_sum/mass)
    assert var_cvar(r,.95,5)==pytest.approx((expected_var,expected_cvar))
    assert var_cvar(r,.99,1)[0]>=0

def test_parametric_horizon_var_and_expected_shortfall_formula():
    r=pd.Series([-.02,-.01,0,.01,.02])
    var,cvar=parametric_var_cvar(r,.95,10)
    sigma=r.std(ddof=1)*np.sqrt(10); z=norm.ppf(.95); mu=r.mean()*10
    assert var==pytest.approx(max(0,-mu+sigma*z))
    assert cvar==pytest.approx(max(0,-mu+sigma*norm.pdf(z)/.05))
    assert cvar>=var

def test_analyze_returns_all_methods_confidence_levels_and_requested_horizons():
    idx=pd.bdate_range("2024-01-01",periods=253)
    daily=.002*np.sin(np.arange(252)*.37)-.0002
    prices=pd.Series(100*np.r_[1,np.cumprod(1+daily)],index=idx)
    got=analyze(prices,window=252)
    assert set(got["risk_horizons"])=={"1","5","10"}
    for horizon in ("1","5","10"):
        for method in ("historical","parametric","monte_carlo"):
            for confidence in (95,99):
                assert np.isfinite(got["risk_horizons"][horizon][method][f"var{confidence}"])
                assert np.isfinite(got["risk_horizons"][horizon][method][f"cvar{confidence}"])
                assert got["risk_horizons"][horizon][method][f"cvar{confidence}"] >= got["risk_horizons"][horizon][method][f"var{confidence}"]

def test_monte_carlo_tail_is_positive_loss_and_horizon_explicit():
    r=pd.Series([-.02,.01,.03])
    one=monte_carlo(r,horizon=1,paths=50000,seed=17)
    ten=monte_carlo(r,horizon=10,paths=50000,seed=17)
    assert one["horizon_days"]==1 and ten["horizon_days"]==10
    assert one["var95"]>=0 and one["cvar95"]+1e-12>=one["var95"]
    assert ten["cvar95"]>one["cvar95"]
    assert ten["p5_return"]<0

def test_performance_annualization_sharpe_and_sortino_mar():
    r=pd.Series([.01,.02,-.01,.03])
    got=performance(r,risk_free_annual=.05,sortino_mar_annual=.02)
    rf=np.expm1(np.log1p(.05)/252)
    mar=np.expm1(np.log1p(.02)/252)
    assert got["daily_arithmetic_mean"]==pytest.approx(r.mean())
    assert got["annualized_arithmetic_return"]==pytest.approx(r.mean()*252)
    assert got["annualized_geometric_return"]==pytest.approx(np.expm1(np.log1p(r).sum()*252/len(r)))
    assert got["sharpe"]==pytest.approx((r-rf).mean()/(r-rf).std(ddof=1)*np.sqrt(252))
    down=np.minimum(r-mar,0)
    assert got["sortino"]==pytest.approx((r.mean()-mar)/np.sqrt(np.mean(down**2))*np.sqrt(252))

def test_missing_risk_free_does_not_silently_become_zero():
    got=performance(pd.Series([.01,-.01,.02]),risk_free_annual=None)
    assert got["risk_free_annual"] is None
    assert np.isnan(got["sharpe"])

def test_all_missing_volume_is_unavailable_not_zero(monkeypatch):
    import sys
    from types import SimpleNamespace
    from app.services import market_data
    idx=pd.bdate_range(end=pd.Timestamp.today().normalize(),periods=400)
    def fake_history(**kwargs):
        return pd.DataFrame({"Close":np.linspace(10,20,len(idx)),"Volume":np.nan},index=idx)
    monkeypatch.setitem(sys.modules,"yfinance",SimpleNamespace(Ticker=lambda _:SimpleNamespace(history=fake_history)))
    data=market_data.fetch_us("NVDA","SPY","short_term")
    assert len(data.prices)==61
    assert data.volume.isna().all()
    assert data.volume.mean()!=0

def test_provider_uses_horizon_specific_yfinance_period_and_exact_window(monkeypatch):
    import sys
    from types import SimpleNamespace
    from app.services import market_data
    idx=pd.bdate_range(end=pd.Timestamp.today().normalize(),periods=800)
    seen=[]
    def fake_history(**kwargs):
        seen.append(kwargs["period"])
        return pd.DataFrame({"Close":np.linspace(10,20,len(idx)),"Volume":np.ones(len(idx))},index=idx)
    monkeypatch.setitem(sys.modules,"yfinance",SimpleNamespace(Ticker=lambda _:SimpleNamespace(history=fake_history)))
    short=market_data.fetch_us("NVDA","SPY","short_term")
    assert seen[-2:]==["6mo","6mo"]
    assert len(short.prices)==61
    assert len(returns(short.prices))==60
    medium=market_data.fetch_us("NVDA","SPY","medium_term")
    assert seen[-2:]==["2y","2y"]
    assert len(medium.prices)==253
    assert len(returns(medium.prices))==252

def test_explicit_market_period_fetches_exact_trading_window(monkeypatch):
    import sys
    from types import SimpleNamespace
    from app.services import market_data
    idx=pd.bdate_range(end=pd.Timestamp.today().normalize(),periods=900)
    observed=[]
    frame=pd.DataFrame({"Close":np.linspace(10,20,len(idx)),"Volume":np.ones(len(idx))},index=idx)
    def fake_history(**kwargs):
        observed.append(kwargs)
        return frame
    monkeypatch.setitem(sys.modules,"yfinance",SimpleNamespace(Ticker=lambda _:SimpleNamespace(history=fake_history)))
    data=market_data.fetch_us("NVDA","SPY",period="1Y")
    assert data.period=="1Y"
    assert len(data.prices)==253
    assert len(returns(data.prices))==252
    assert "start" in observed[0] and "end" in observed[0]

def test_yahoo_rate_limit_is_reported_as_provider_rate_limit(monkeypatch):
    import sys
    from types import SimpleNamespace
    from app.services import market_data
    def limited(**kwargs): raise RuntimeError("HTTP 429 Too Many Requests")
    monkeypatch.setitem(sys.modules,"yfinance",SimpleNamespace(Ticker=lambda _:SimpleNamespace(history=limited)))
    with pytest.raises(RuntimeError,match="Yahoo Finance provider rate limit"):
        market_data.fetch_us("NVDA","SPY",period="1Y")

def test_yahoo_empty_history_is_a_clear_invalid_or_unavailable_error(monkeypatch):
    import sys
    from types import SimpleNamespace
    from app.services import market_data
    monkeypatch.setitem(sys.modules,"yfinance",SimpleNamespace(Ticker=lambda _:SimpleNamespace(history=lambda **kwargs:pd.DataFrame())))
    with pytest.raises(RuntimeError,match="verify the ticker is valid and US-listed"):
        market_data.fetch_us("RADNOTAREAL","SPY",period="1Y")

def test_yfinance_multiindex_columns_extract_close_and_volume(monkeypatch):
    import sys
    from types import SimpleNamespace
    from app.services import market_data
    idx=pd.bdate_range(end=pd.Timestamp.today().normalize(),periods=80)
    columns=pd.MultiIndex.from_tuples([("Close","NVDA"),("Volume","NVDA")])
    frame=pd.DataFrame(np.column_stack([np.linspace(10,20,len(idx)),np.full(len(idx),1234)]),index=idx,columns=columns)
    monkeypatch.setitem(sys.modules,"yfinance",SimpleNamespace(Ticker=lambda _:SimpleNamespace(history=lambda **kwargs:frame)))
    got=market_data.fetch_us("NVDA","SPY","short_term")
    assert got.prices.iloc[-1]==pytest.approx(20)
    assert got.volume.iloc[-1]==1234

def test_yfinance_volume_timezone_is_normalized_before_alignment(monkeypatch):
    import sys
    from types import SimpleNamespace
    from app.services import market_data
    idx=pd.bdate_range(end=pd.Timestamp.today().normalize(),periods=80,tz="America/New_York")
    frame=pd.DataFrame({"Close":np.linspace(10,20,len(idx)),"Volume":np.full(len(idx),4321)},index=idx)
    monkeypatch.setitem(sys.modules,"yfinance",SimpleNamespace(Ticker=lambda _:SimpleNamespace(history=lambda **kwargs:frame)))
    got=market_data.fetch_us("NVDA","SPY","short_term")
    assert got.volume.notna().all()
    assert got.volume.iloc[-1]==4321

def test_fred_rate_yield_and_observation_date(monkeypatch):
    import os
    from types import SimpleNamespace
    from app.services import market_data
    monkeypatch.setenv("FRED_API_KEY","test-key")
    monkeypatch.setattr(market_data.requests,"get",lambda *a,**k:SimpleNamespace(raise_for_status=lambda:None,json=lambda:{"observations":[{"date":"2026-11-09","value":"4.00"}]}))
    got=market_data.fetch_us_tbill()
    assert got["annual_yield"]==pytest.approx(.04)
    assert got["observation_date"]=="2026-11-09"

def test_fred_missing_key_fails_explicitly(monkeypatch):
    from app.services.market_data import fetch_us_tbill
    monkeypatch.delenv("FRED_API_KEY",raising=False)
    with pytest.raises(RuntimeError,match="FRED_API_KEY"):
        fetch_us_tbill()
