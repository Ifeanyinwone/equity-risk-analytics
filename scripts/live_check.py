"""Live, no-fixture API checks for the requested US ticker set."""
import math
import os
import sys
import requests
from pathlib import Path
import tempfile
TEST_TEMP=Path(__file__).resolve().parents[1]/"reports"/"runtime"/"apptest-temp"
TEST_TEMP.mkdir(parents=True,exist_ok=True)
os.environ["TEMP"]=str(TEST_TEMP)
os.environ["TMP"]=str(TEST_TEMP)
tempfile.tempdir=str(TEST_TEMP)
from streamlit.testing.v1 import AppTest

BASE=os.getenv("RAD_API_BASE_URL","http://127.0.0.1:8765").rstrip("/")
TICKERS=("NVDA","AAPL","MSFT","AVGO","SPY")
_request_counter=0

def analyze_request(payload:dict,timeout:int):
    global _request_counter
    _request_counter+=1
    return requests.post(
        f"{BASE}/analyze",json=payload,timeout=timeout,
        headers={"X-RAD-Client-ID":f"rad-live-check-{_request_counter}"},
    )

def check_ticker(ticker:str)->tuple[bool,str]:
    response=analyze_request({"ticker":ticker,"market":"US","horizon":"medium_term","period":"1Y","benchmark":"SPY"},240)
    if response.status_code!=200:
        try: detail=response.json().get("detail","request failed")
        except ValueError: detail="unreadable error response"
        return False,f"{ticker}: HTTP {response.status_code}: {detail}"
    data=response.json(); metrics=data["metrics"]; rows=data.get("series",[])
    required=("latest_price","annualized_geometric_return","volatility_20","volatility_60","volatility_252","var95","var99","cvar95","cvar99","beta_60","beta_252","drawdown")
    missing=[name for name in required if metrics.get(name) is None or not math.isfinite(float(metrics[name]))]
    benchmark_count=sum(row.get("benchmark_close") is not None for row in rows)
    volume_count=sum(row.get("volume") is not None for row in rows)
    if not data.get("source"," ").startswith("Yahoo Finance"):
        return False,f"{ticker}: unexpected market-data source"
    if metrics.get("observations",0)<252 or len(rows)<253:
        return False,f"{ticker}: insufficient observations ({metrics.get('observations')} returns, {len(rows)} closes)"
    if benchmark_count<252:
        return False,f"{ticker}: benchmark coverage incomplete ({benchmark_count} closes)"
    if missing:
        return False,f"{ticker}: required risk metrics unavailable: {', '.join(missing)}"
    if not data.get("risk_free_rate"):
        return False,f"{ticker}: live FRED rate unavailable"
    status=f"{ticker}: PASS; {metrics['observations']} returns; benchmark {benchmark_count}; volume {volume_count}; source Yahoo Finance; FRED {data['risk_free_rate']['observation_date']}"
    return True,status

def main()->int:
    failures=0; passed=0
    for ticker in TICKERS:
        try: ok,message=check_ticker(ticker)
        except requests.Timeout: ok,message=False,f"{ticker}: request timed out"
        except requests.RequestException: ok,message=False,f"{ticker}: API connection failed"
        except Exception as exc: ok,message=False,f"{ticker}: {type(exc).__name__}"
        print(message)
        if ok: passed+=1
        else: failures+=1
    try:
        invalid=analyze_request({"ticker":"RADNOTAREAL","market":"US","horizon":"medium_term","period":"1Y","benchmark":"SPY"},90)
        if invalid.status_code==422:
            print("Invalid ticker: PASS (returned a clear HTTP 422 data error)")
            passed+=1
        else:
            print(f"Invalid ticker: FAIL (unexpected HTTP {invalid.status_code})")
            failures+=1
    except requests.RequestException:
        print("Invalid ticker: FAIL (backend connection error)")
        failures+=1
    try:
        qqq=analyze_request({"ticker":"NVDA","market":"US","horizon":"medium_term","period":"1Y","benchmark":"QQQ"},240)
        if qqq.ok and sum(row.get("benchmark_close") is not None for row in qqq.json().get("series",[]))>=252:
            print("QQQ benchmark: PASS (live adjusted history aligned with NVDA)")
            passed+=1
        else:
            print(f"QQQ benchmark: FAIL (HTTP {qqq.status_code})")
            failures+=1
    except requests.RequestException:
        print("QQQ benchmark: FAIL (backend connection error)")
        failures+=1
    for period,expected in (("2Y",504),("3Y",756),("5Y",1260)):
        try:
            response=analyze_request({"ticker":"NVDA","market":"US","horizon":"medium_term","period":period,"benchmark":"SPY"},300)
            if response.ok:
                metrics=response.json()["metrics"]
                if metrics.get("observations",0)>=expected:
                    print(f"NVDA {period} period: PASS ({metrics['observations']} returns)")
                    passed+=1
                else:
                    print(f"NVDA {period} period: FAIL (only {metrics.get('observations')} returns)")
                    failures+=1
            else:
                print(f"NVDA {period} period: FAIL (HTTP {response.status_code})")
                failures+=1
        except requests.RequestException:
            print(f"NVDA {period} period: FAIL (backend connection error)")
            failures+=1
    try:
        app_path=Path(__file__).resolve().parents[1]/"frontend"/"app.py"
        app=AppTest.from_file(str(app_path)).run(timeout=30)
        app.text_input[0].set_value("NVDA")
        app.button[0].click().run(timeout=240)
        app_errors=list(app.exception)
        metric_count=len(app.get("metric"))
        if app_errors:
            print(f"Frontend live analysis: FAIL (Streamlit exception: {app_errors[0].message[:240]})")
            failures+=1
        elif metric_count<8:
            errors=[item.value for item in app.error]
            detail=errors[0][:240] if errors else "no visible error detail"
            print(f"Frontend live analysis: FAIL (only {metric_count} metric cards rendered; {detail})")
            failures+=1
        else:
            print(f"Frontend live analysis: PASS ({metric_count} live metric cards rendered for NVDA)")
            passed+=1
    except Exception as exc:
        print(f"Frontend live analysis: FAIL ({type(exc).__name__})")
        failures+=1
    print(f"Live checks: {passed} passed; {failures} failed")
    return 1 if failures else 0

if __name__=="__main__": raise SystemExit(main())
