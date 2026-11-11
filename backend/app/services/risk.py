"""Risk calculations on decimal simple returns; loss metrics use positive-loss signs."""
from __future__ import annotations
import numpy as np
import pandas as pd
from scipy.stats import norm, skew, kurtosis

TRADING_DAYS = 252
HORIZON_RETURN_COUNTS = {"medium_term": 252, "short_term": 60}

def annual_rate_to_daily_rate(annual_rate:float)->float:
    """Convert an effective annual decimal rate to an effective 252-session rate."""
    if not np.isfinite(annual_rate) or annual_rate<=-1:
        raise ValueError("annual risk-free rate must be finite and greater than -100%")
    return float(np.expm1(np.log1p(annual_rate)/TRADING_DAYS))

def returns(prices: pd.Series) -> pd.DataFrame:
    p=pd.to_numeric(prices,errors="coerce").dropna()
    if (p<=0).any(): raise ValueError("Prices must be positive")
    simple=p.pct_change(fill_method=None).dropna()
    logs=np.log(p).diff().dropna()
    pairs=pd.DataFrame({"simple":simple,"log":logs}).dropna()
    # Avoid treating a long data outage as one ordinary daily return. A 7-day
    # threshold tolerates weekends and common US market holidays.
    if isinstance(p.index,pd.DatetimeIndex):
        elapsed=p.index.to_series().diff().dt.total_seconds().div(86400).reindex(pairs.index)
        pairs=pairs.loc[elapsed<=7]
        pairs.attrs["excluded_gap_returns"]=int((elapsed>7).sum())
    else:pairs.attrs["excluded_gap_returns"]=0
    return pairs

def annual_vol(r:pd.Series,window:int|None=None)->float:
    x=r.dropna().tail(window) if window else r.dropna()
    if window and len(x)<window:return float("nan")
    return float(x.std(ddof=1)*np.sqrt(TRADING_DAYS)) if len(x)>=2 else float("nan")

def _historical_horizon_returns(r:pd.Series,days:int)->pd.Series:
    x=r.dropna()
    if days<1: raise ValueError("days must be positive")
    if days==1: return x
    if len(x)<days: return pd.Series(dtype=float)
    return (1+x).rolling(days).apply(np.prod,raw=True).dropna()-1

def _tail_loss(values:pd.Series,confidence:float)->tuple[float,float]:
    x=values.dropna()
    if not len(x) or not 0<confidence<1: return float("nan"),float("nan")
    q=float(np.quantile(x,1-confidence,method="linear"))
    # Expected shortfall is the integrated empirical quantile: average exactly
    # (1-confidence)*N worst outcomes, fractionally weighting the boundary.
    ordered=np.sort(x.to_numpy()); mass=(1-confidence)*len(ordered)
    whole=int(np.floor(mass)); fraction=mass-whole
    tail_sum=float(ordered[:whole].sum()) if whole else 0.0
    if fraction and whole<len(ordered): tail_sum+=fraction*float(ordered[whole])
    tail_mean=tail_sum/mass
    return max(0.0,-q),max(0.0,-tail_mean)

def var_cvar(r:pd.Series,confidence:float=.95,horizon:int=1)->tuple[float,float]:
    """Empirical compounded horizon VaR and CVaR, reported as positive loss fractions."""
    if not 0<confidence<1: raise ValueError("confidence must be in (0,1)")
    values=_historical_horizon_returns(r,horizon)
    return _tail_loss(values,confidence)

def parametric_var_cvar(r:pd.Series,confidence:float=.95,horizon:int=1)->tuple[float,float]:
    """IID normal horizon losses; CVaR is analytic conditional tail mean."""
    x=r.dropna()
    if len(x)<2 or not 0<confidence<1 or horizon<1: return float("nan"),float("nan")
    mu=float(x.mean())*horizon
    sigma=float(x.std(ddof=1))*np.sqrt(horizon)
    z=float(norm.ppf(confidence))
    var=max(0.0,-mu+sigma*z)
    cvar=max(0.0,-mu+sigma*float(norm.pdf(z))/(1-confidence))
    return var,cvar

def beta(stock:pd.Series,benchmark:pd.Series,window:int|None=None)->float:
    z=pd.concat([stock.rename("s"),benchmark.rename("b")],axis=1).dropna()
    if window: z=z.tail(window)
    return float(z.s.cov(z.b)/z.b.var()) if len(z)>=2 and z.b.var()>0 else float("nan")

def drawdown(prices:pd.Series)->dict:
    p=prices.dropna().astype(float)
    if p.empty:return {"max_drawdown":float("nan"),"current_drawdown":float("nan"),"duration_days":0,"series":pd.Series(dtype=float)}
    dd=p/p.cummax()-1; under=dd<0; groups=(~under).cumsum()
    duration=int(under.groupby(groups).sum().iloc[-1]) if under.any() else 0
    return {"max_drawdown":float(dd.min()),"current_drawdown":float(dd.iloc[-1]),"duration_days":duration,"series":dd}

def performance(r:pd.Series,benchmark:pd.Series|None=None,risk_free_annual:float|None=None,sortino_mar_annual:float=0.0)->dict:
    """Arithmetic annualized Sharpe; Sortino target is configurable annual MAR (default 0%)."""
    x=r.dropna(); result={"daily_arithmetic_mean":float(x.mean()) if len(x) else np.nan,
        "annualized_arithmetic_return":float(x.mean()*TRADING_DAYS) if len(x) else np.nan,
        "annualized_geometric_return":float(np.expm1(np.log1p(x).sum()*TRADING_DAYS/len(x))) if len(x) and (x>-1).all() else np.nan,
        "sortino_mar_annual":sortino_mar_annual}
    if risk_free_annual is not None:
        if risk_free_annual<=-1: raise ValueError("annual risk-free yield must be greater than -100%")
        rf_daily=annual_rate_to_daily_rate(risk_free_annual)
        excess=x-rf_daily; sd=float(excess.std(ddof=1)) if len(excess)>1 else np.nan
        result["risk_free_annual"] = risk_free_annual
        result["risk_free_daily_effective"] = rf_daily
        result["sharpe"] = float(excess.mean()/sd*np.sqrt(TRADING_DAYS)) if sd>0 and np.isfinite(sd) else np.nan
    else:
        result.update(risk_free_annual=None,risk_free_daily_effective=None,sharpe=np.nan)
    mar_daily=float(np.expm1(np.log1p(sortino_mar_annual)/TRADING_DAYS))
    downside=np.minimum(x-mar_daily,0)
    down_dev=float(np.sqrt(np.mean(downside**2))) if len(x) else np.nan
    result["sortino"] = float((x.mean()-mar_daily)/down_dev*np.sqrt(TRADING_DAYS)) if down_dev>0 and np.isfinite(down_dev) else np.nan
    if benchmark is not None:
        z=pd.concat([x.rename("s"),benchmark.rename("b")],axis=1).dropna(); b=z.b;s=z.s
        bvar=b.var(ddof=1); bt=float(s.cov(b)/bvar) if len(z)>1 and bvar>0 else np.nan
        active=s-b
        result.update(beta=bt,correlation=float(s.corr(b)) if len(z)>1 else np.nan,
          treynor=float((s.mean()-rf_daily)*TRADING_DAYS/bt) if bt and np.isfinite(bt) and risk_free_annual is not None else np.nan,
          information_ratio=float(active.mean()/active.std(ddof=1)*np.sqrt(TRADING_DAYS)) if len(active)>1 and active.std(ddof=1)>0 else np.nan,
          jensen_alpha=float((s.mean()-rf_daily-bt*(b.mean()-rf_daily))*TRADING_DAYS) if np.isfinite(bt) and risk_free_annual is not None else np.nan)
    return result

def monte_carlo(r:pd.Series,horizon:int=20,paths:int=10000,seed:int=42)->dict:
    """IID empirical-bootstrap cumulative simple returns; tail loss metrics are positive."""
    x=r.dropna().to_numpy()
    if len(x)<2:raise ValueError("At least two returns required")
    rng=np.random.default_rng(seed); sims=rng.choice(x,size=(paths,horizon),replace=True)
    terminal=np.prod(1+sims,axis=1)-1
    result={"terminal_returns":terminal,"horizon_days":horizon,"paths":paths,"seed":seed}
    for confidence in (.95,.99):
        var,cvar=_tail_loss(pd.Series(terminal),confidence)
        tag=int(confidence*100); result[f"var{tag}"]=var;result[f"cvar{tag}"]=cvar
    result["p5_return"]=float(np.quantile(terminal,.05));result["p1_return"]=float(np.quantile(terminal,.01))
    return result

def analyze(prices:pd.Series,benchmark_prices:pd.Series|None=None,amount:float=10000,risk_free:float|None=None,
            window:int|None=None,sortino_mar_annual:float=0.0,performance_window:int|None=None)->dict:
    prices=pd.to_numeric(prices,errors="coerce").dropna().sort_index()
    rr=returns(prices); r=rr.simple.tail(window) if window else rr.simple
    if window and not r.empty: prices=prices.loc[:r.index[-1]].tail(window+1)
    br=returns(benchmark_prices).simple if benchmark_prices is not None else None
    aligned_all=pd.concat([r.rename("s"),br.rename("b")],axis=1,sort=False).dropna() if br is not None else None
    performance_r=r.tail(performance_window) if performance_window else r
    perf_aligned=pd.concat([performance_r.rename("s"),br.rename("b")],axis=1,sort=False).dropna() if br is not None else None
    br_selected=perf_aligned.b if perf_aligned is not None else None
    performance_r=perf_aligned.s if perf_aligned is not None else performance_r
    mc={str(h):(monte_carlo(r,horizon=h) if len(r)>=2 else {"terminal_returns":np.array([]),"horizon_days":h,"paths":0,"seed":42,"var95":np.nan,"cvar95":np.nan,"var99":np.nan,"cvar99":np.nan,"p5_return":np.nan,"p1_return":np.nan}) for h in (1,5,10)}
    d=drawdown(prices)
    risk_horizons={}
    for h in (1,5,10):
        risk_horizons[str(h)]={"historical":{},"parametric":{},"monte_carlo":{}}
        for conf in (.95,.99):
            tag=int(conf*100); hv,hc=var_cvar(r,conf,h); pv,pc=parametric_var_cvar(r,conf,h)
            risk_horizons[str(h)]["historical"].update({f"var{tag}":hv,f"cvar{tag}":hc})
            risk_horizons[str(h)]["parametric"].update({f"var{tag}":pv,f"cvar{tag}":pc})
            risk_horizons[str(h)]["monte_carlo"].update({f"var{tag}":mc[str(h)][f"var{tag}"],f"cvar{tag}":mc[str(h)][f"cvar{tag}"]})
    n=len(r)
    annual_geo=float(np.expm1(np.log1p(r).sum()*TRADING_DAYS/n)) if n and (r>-1).all() else np.nan
    mc20=monte_carlo(r,horizon=20) if len(r)>=2 else {"terminal_returns":np.array([]),"horizon_days":20,"paths":0,"seed":42,"var95":np.nan,"cvar95":np.nan,"var99":np.nan,"cvar99":np.nan,"p5_return":np.nan,"p1_return":np.nan}
    out={"observations":n,"start_date":str(prices.index.min().date()) if n else None,"end_date":str(prices.index.max().date()) if n else None,
         "latest_price":float(prices.iloc[-1]) if not prices.empty else np.nan,"daily_mean":float(r.mean()) if n else np.nan,"annualized_arithmetic_return":float(r.mean()*TRADING_DAYS) if n else np.nan,
         "annualized_geometric_return":annual_geo,"annualized_return":annual_geo,
         "volatility_20":annual_vol(r,20),"volatility_60":annual_vol(r,60),"volatility_252":annual_vol(r,252),
         "risk_horizons":risk_horizons,"var95":risk_horizons["1"]["historical"]["var95"],"cvar95":risk_horizons["1"]["historical"]["cvar95"],
         "var99":risk_horizons["1"]["historical"]["var99"],"cvar99":risk_horizons["1"]["historical"]["cvar99"],
         "parametric_var95":risk_horizons["1"]["parametric"]["var95"],"var95_5d":risk_horizons["5"]["historical"]["var95"],"var95_10d":risk_horizons["10"]["historical"]["var95"],
         "var95_amount":risk_horizons["1"]["historical"]["var95"]*amount if amount is not None else np.nan,"drawdown":d["max_drawdown"],"current_drawdown":d["current_drawdown"],"drawdown_duration_days":d["duration_days"],
         "performance":performance(performance_r,br_selected,risk_free,sortino_mar_annual),"monte_carlo":mc20,"monte_carlo_by_horizon":mc,
         "excluded_gap_returns":rr.attrs.get("excluded_gap_returns",0),
         "returns":r,"log_returns":rr.log.reindex(r.index),"drawdown_series":d["series"],"prices":prices}
    if aligned_all is not None:
        out["beta_60"]=beta(aligned_all.s,aligned_all.b,60)
        out["beta_252"]=beta(aligned_all.s,aligned_all.b,252) if len(aligned_all)>=252 else np.nan
    out["skewness"]=float(skew(r)) if len(r)>2 else np.nan;out["excess_kurtosis"]=float(kurtosis(r)) if len(r)>3 else np.nan
    return out
