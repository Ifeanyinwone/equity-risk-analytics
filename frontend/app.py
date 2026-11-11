from __future__ import annotations
import io
import os
import uuid
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from dotenv import load_dotenv
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from ui_errors import analysis_error_message

ROOT=Path(__file__).resolve().parents[1]
load_dotenv(ROOT/".env")
API_BASE=os.getenv("RAD_API_BASE_URL","http://127.0.0.1:8765").rstrip("/")

st.set_page_config(page_title="RAD | Equity Risk",layout="wide")
st.title("Risk Analytics and Decision")
st.caption("Quantitative equity risk assessment · Screening support, not an investment recommendation")

with st.sidebar:
    st.subheader("Analysis inputs")
    ticker=st.text_input("US ticker",value="NVDA",max_chars=20).strip().upper()
    period=st.selectbox("Historical period",["1Y","2Y","3Y","5Y"])
    benchmark=st.selectbox("Benchmark",["SPY","QQQ"])
    horizon=st.selectbox("Risk profile",["medium_term","short_term"],format_func=lambda x:"Medium-term investing" if x=="medium_term" else "Short-term / swing trading")
    use_amount=st.checkbox("Calculate monetary loss estimates",value=False)
    amount=st.number_input("Reference investment ($)",min_value=1.0,value=10000.0,step=1000.0,disabled=not use_amount) if use_amount else None
    run=st.button("Analyse",type="primary",use_container_width=True)

if run:
    if not ticker:
        st.error("Enter a US ticker symbol.")
    else:
        request_body={"ticker":ticker,"market":"US","horizon":horizon,"period":period,"benchmark":benchmark,"amount":amount}
        try:
            with st.spinner(f"Retrieving {period} of {ticker} and {benchmark} data and calculating risk metrics…"):
                if "rad_throttle_session" not in st.session_state:
                    st.session_state["rad_throttle_session"]=uuid.uuid4().hex
                client_ip=getattr(st.context,"ip_address",None)
                if not isinstance(client_ip,str) or not client_ip.strip(): client_ip=None
                client_id=client_ip or st.session_state["rad_throttle_session"]
                response=requests.post(f"{API_BASE}/analyze",json=request_body,headers={"X-RAD-Client-ID":client_id},timeout=240)
            if response.status_code>=400:
                st.error(analysis_error_message(response))
                st.session_state.pop("rad_assessment",None)
            else:
                st.session_state["rad_assessment"]=response.json()
        except requests.Timeout:
            st.error("The analysis timed out while retrieving market data. Try again later or use a shorter historical period.")
        except requests.ConnectionError:
            st.error(f"RAD backend is not responding at {API_BASE}. Start the app with run_rad.ps1 dashboard.")
        except (requests.RequestException,ValueError) as exc:
            st.error(f"Could not complete the analysis: {exc}")

if "rad_assessment" not in st.session_state:
    st.info("Choose a ticker, historical period, and benchmark, then select Analyse. US market history and the DGS3MO rate are retrieved automatically.")
else:
    report=st.session_state["rad_assessment"]
    metrics=report["metrics"]
    perf=metrics.get("performance",{})
    observations=pd.DataFrame(report.get("series",[]))
    if not observations.empty:
        observations["date"]=pd.to_datetime(observations["date"])
        observations=observations.set_index("date")

    st.subheader(f"{report['ticker']} · {report['period']} · {report['benchmark']}")
    st.caption(f"{metrics.get('start_date')} to {metrics.get('end_date')} · {metrics.get('observations')} return observations · {report['source']} · retrieved {report['retrieved_at']}")
    if report.get("risk_free_rate"):
        rate=report["risk_free_rate"]
        st.caption(f"US risk-free rate: {rate['annual_yield']:.4%} annual / {rate['daily_risk_free_rate']:.6%} daily · {rate['observation_date']} · {rate['source']}")
    elif report.get("risk_free_warning"):
        st.warning(f"Risk-free rate unavailable; Sharpe, Treynor, and Jensen's alpha are not reported. {report['risk_free_warning']}")

    decision=report["decision"]["decision"]
    color={"PASS":"green","REVIEW":"orange","FAIL":"red","INSUFFICIENT DATA":"gray"}.get(decision,"gray")
    st.markdown(f"### Risk screening result: :{color}[{decision}]")
    st.caption(report["decision"].get("interpretation","Configurable screening classification only."))

    beta_name="beta_252" if horizon=="medium_term" else "beta_60"
    volatility_name="volatility_252" if horizon=="medium_term" else "volatility_60"
    kpis=[
        ("Latest price",metrics.get("latest_price"),"price"),
        ("Annualized return",metrics.get("annualized_geometric_return"),"percent"),
        ("Annualized volatility",metrics.get(volatility_name),"percent"),
        ("Historical VaR 95% (1d)",metrics.get("var95"),"percent"),
        ("Historical CVaR 95% (1d)",metrics.get("cvar95"),"percent"),
        (f"Beta ({'252' if horizon=='medium_term' else '60'}d)",metrics.get(beta_name),"number"),
        ("Benchmark correlation",perf.get("correlation"),"number"),
        ("Maximum drawdown",metrics.get("drawdown"),"percent"),
        ("Sharpe ratio",perf.get("sharpe"),"number"),
        ("Sortino ratio",perf.get("sortino"),"number"),
    ]
    for start in range(0,len(kpis),5):
        cols=st.columns(5)
        for col,(label,value,kind) in zip(cols,kpis[start:start+5]):
            if value is None or pd.isna(value): text="Unavailable"
            elif kind=="price": text=f"{value:,.2f}"
            elif kind=="percent": text=f"{value:.2%}"
            else: text=f"{value:.3f}"
            col.metric(label,text)

    overview,charts,downside,performance,decision_tab,history_tab=st.tabs(["Overview","Charts","Downside risk","Performance","Decision checks","Historical assessments"])
    with overview:
        st.write({"Daily arithmetic mean":metrics.get("daily_mean"),"Annualized arithmetic return":metrics.get("annualized_arithmetic_return"),"Annualized geometric return":metrics.get("annualized_geometric_return"),"60-day beta":metrics.get("beta_60"),"252-day beta":metrics.get("beta_252"),"Current drawdown":metrics.get("current_drawdown"),"Average volume (20d)":metrics.get("avg_volume"),"Average traded value (20d)":metrics.get("avg_traded_value"),"Volume status":metrics.get("volume_status")})
    with charts:
        if observations.empty:
            st.info("No chartable price observations were returned.")
        else:
            price=go.Figure()
            price.add_trace(go.Scatter(x=observations.index,y=observations["adjusted_close"],name=report["ticker"]))
            if observations["benchmark_close"].notna().any(): price.add_trace(go.Scatter(x=observations.index,y=observations["benchmark_close"],name=report["benchmark"],yaxis="y2"))
            price.update_layout(title="Adjusted close prices",yaxis_title="Stock price",yaxis2={"title":"Benchmark price","overlaying":"y","side":"right"},legend={"orientation":"h"})
            st.plotly_chart(price,width="stretch")
            vol=go.Figure()
            for days in (20,60,252):
                col=f"rolling_volatility_{days}"
                if col in observations: vol.add_trace(go.Scatter(x=observations.index,y=observations[col],name=f"{days}-day"))
            vol.update_layout(title="Rolling annualized volatility",yaxis_tickformat=".0%")
            st.plotly_chart(vol,width="stretch")
            dd=go.Figure(go.Scatter(x=observations.index,y=observations["drawdown"],name="Drawdown",fill="tozeroy"))
            dd.update_layout(title="Drawdown from running peak",yaxis_tickformat=".0%")
            st.plotly_chart(dd,width="stretch")
    with downside:
        st.json(metrics.get("risk_horizons",{}))
        st.write({"VaR 95% amount":metrics.get("var95_amount"),"Maximum drawdown":metrics.get("drawdown"),"Current drawdown":metrics.get("current_drawdown"),"Drawdown duration (observations)":metrics.get("drawdown_duration_days"),"Excluded long-gap returns":metrics.get("excluded_gap_returns")})
        st.caption("VaR/CVaR are positive loss fractions. Historical, parametric, and bootstrap measures are shown separately by horizon and confidence level.")
    with performance:
        st.json(report["metrics"].get("performance",{}))
        st.caption("Arithmetic and geometric annualized returns are distinct. Ratios are historical estimates, not forecasts.")
    with decision_tab:
        st.dataframe(pd.DataFrame(report["decision"].get("checks",[])),width="stretch",hide_index=True)
        for reason in report["decision"].get("reasons",[]): st.write("• "+reason)
    with history_tab:
        try:
            prior=requests.get(f"{API_BASE}/history",timeout=8)
            if prior.ok: st.dataframe(pd.DataFrame(prior.json()),width="stretch",hide_index=True)
            else: st.info("Historical assessment storage is unavailable. Configure DATABASE_URL and apply the migration to enable it.")
        except requests.RequestException:
            st.info("Could not retrieve stored assessment history from the RAD backend.")

    csv_data=observations.to_csv(index=True).encode("utf-8") if not observations.empty else b""
    st.download_button("Download CSV data",csv_data,file_name=f"{report['ticker']}_{report['period']}_risk_data.csv",mime="text/csv",disabled=not bool(csv_data))
    excel=io.BytesIO()
    with pd.ExcelWriter(excel,engine="openpyxl") as writer:
        pd.DataFrame([metrics]).to_excel(writer,index=False,sheet_name="Metrics")
        pd.DataFrame(report["decision"].get("checks",[])).to_excel(writer,index=False,sheet_name="Decision")
        if not observations.empty: observations.to_excel(writer,sheet_name="History")
    st.download_button("Download Excel report",excel.getvalue(),file_name=f"{report['ticker']}_rad_report.xlsx",mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    pdf=io.BytesIO(); pdf_canvas=canvas.Canvas(pdf,pagesize=letter); page_width,page_height=letter; y=page_height-48
    pdf_canvas.setFont("Helvetica-Bold",16); pdf_canvas.drawString(48,y,"RAD Equity Risk Assessment"); y-=28
    pdf_canvas.setFont("Helvetica",10)
    lines=[f"Ticker: {report['ticker']} | Period: {report['period']} | Benchmark: {report['benchmark']}",f"Data range: {metrics.get('start_date')} to {metrics.get('end_date')} ({metrics.get('observations')} returns)",f"Data source: {report['source']} | Retrieved: {report['retrieved_at']}",f"Decision: {decision}"]
    lines.extend([f"{label}: {value:.6g}" for label,value in [("Annualized geometric return",metrics.get("annualized_geometric_return")),("Annualized volatility",metrics.get(volatility_name)),("Historical VaR95 1d",metrics.get("var95")),("Historical CVaR95 1d",metrics.get("cvar95")),("Beta",metrics.get(beta_name)),("Correlation",perf.get("correlation")),("Maximum drawdown",metrics.get("drawdown")),("Sharpe",perf.get("sharpe")),("Sortino",perf.get("sortino"))] if value is not None and pd.notna(value)])
    for line in lines:
        if y<48: pdf_canvas.showPage(); y=page_height-48; pdf_canvas.setFont("Helvetica",10)
        pdf_canvas.drawString(48,y,line[:110]); y-=16
    pdf_canvas.save()
    st.download_button("Download PDF risk report",pdf.getvalue(),file_name=f"{report['ticker']}_rad_risk_report.pdf",mime="application/pdf")
