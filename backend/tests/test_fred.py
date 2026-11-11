import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
import numpy as np
import pandas as pd
from requests import HTTPError
from fastapi.testclient import TestClient

from app.services import market_data
from app.services.risk import annual_rate_to_daily_rate, performance


def test_fred_key_is_loaded_from_environment_and_not_returned_or_logged(monkeypatch, caplog):
    dummy_key = "unit" + "-test-value"
    monkeypatch.setenv("FRED_API_KEY", dummy_key)
    seen = {}

    def fake_get(url, params, timeout):
        seen.update(params)
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"observations": [
                {"date": "2026-01-02", "value": "."},
                {"date": "2026-01-05", "value": "4.00"},
                {"date": "not-a-date", "value": "9.00"},
            ]},
        )

    monkeypatch.setattr(market_data.requests, "get", fake_get)
    caplog.set_level(logging.DEBUG)
    rate = market_data.fetch_us_tbill()

    assert seen["api_key"] == dummy_key
    assert rate["series"] == "DGS3MO"
    assert rate["annual_yield"] == pytest.approx(.04)
    assert rate["daily_risk_free_rate"] == pytest.approx(annual_rate_to_daily_rate(.04))
    assert rate["observation_date"] == "2026-01-05"
    assert rate["source"] == "Federal Reserve Bank of St. Louis FRED"
    assert dummy_key not in repr(rate)
    assert dummy_key not in caplog.text


def test_fred_uses_latest_valid_observation_not_first_nonmissing(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "unit-test")
    payload = {"observations": [
        {"date": "2026-02-03", "value": "3.75"},
        {"date": "2026-02-05", "value": "bad"},
        {"date": "2026-02-04", "value": "4.10"},
        {"date": "2026-02-06", "value": "."},
    ]}
    monkeypatch.setattr(market_data.requests, "get", lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload))
    result = market_data.fetch_us_tbill()
    assert result["observation_date"] == "2026-02-04"
    assert result["annual_yield"] == pytest.approx(.041)


def test_fred_empty_observations_fails_without_zero_substitution(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "unit-test")
    monkeypatch.setattr(market_data.requests, "get", lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"observations": [{"date": "2026-01-05", "value": "."}]}))
    with pytest.raises(RuntimeError, match="no usable observations"):
        market_data.fetch_us_tbill()


def test_fred_http_error_is_sanitized(monkeypatch, caplog):
    dummy_key = "unit" + "-secret"
    monkeypatch.setenv("FRED_API_KEY", dummy_key)

    def fail_status():
        raise HTTPError(f"request failed for ?api_key={dummy_key}")

    monkeypatch.setattr(market_data.requests, "get", lambda *a, **k: SimpleNamespace(raise_for_status=fail_status))
    with pytest.raises(RuntimeError, match="credentials were omitted") as exc:
        market_data.fetch_us_tbill()
    assert dummy_key not in str(exc.value)
    assert dummy_key not in caplog.text


def test_missing_fred_key_and_invalid_json_fail_closed(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="FRED_API_KEY"):
        market_data.fetch_us_tbill()
    monkeypatch.setenv("FRED_API_KEY", "unit-test")
    monkeypatch.setattr(market_data.requests, "get", lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None, json=lambda: []))
    with pytest.raises(RuntimeError, match="no valid observations list"):
        market_data.fetch_us_tbill()


def test_daily_rate_conversion_is_effective_252_session_rate_and_used_by_ratios():
    annual = .04
    daily = annual_rate_to_daily_rate(annual)
    assert (1 + daily) ** 252 - 1 == pytest.approx(annual)
    assert daily == pytest.approx(.0001556498628, rel=1e-8)
    returns = __import__("pandas").Series([.01, -.005, .02, -.01, .015])
    bench = __import__("pandas").Series([.005, -.002, .01, -.004, .008])
    ratios = performance(returns, bench, annual)
    excess = returns - daily
    beta = returns.cov(bench) / bench.var(ddof=1)
    assert ratios["risk_free_daily_effective"] == pytest.approx(daily)
    assert ratios["sharpe"] == pytest.approx(excess.mean() / excess.std(ddof=1) * (252 ** .5))
    assert ratios["treynor"] == pytest.approx((returns.mean() - daily) * 252 / beta)
    assert ratios["jensen_alpha"] == pytest.approx((returns.mean() - daily - beta * (bench.mean() - daily)) * 252)


def test_env_example_and_gitignore_keep_local_key_out_of_repo():
    root = Path(__file__).resolve().parents[2]
    example = (root / ".env.example").read_text(encoding="utf-8")
    ignore = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "FRED_API_KEY=" in example
    assert ".env" in ignore


def test_fred_failure_does_not_leak_key_in_analysis_response(monkeypatch):
    from app import main
    from app.services.market_data import MarketData
    dummy_key = "private" + "-unit-test"
    monkeypatch.setenv("FRED_API_KEY", dummy_key)
    index = pd.bdate_range("2025-01-01", periods=253)
    closes = pd.Series(100 * np.cumprod(1 + .002 * np.sin(np.arange(253) * .2)), index=index)
    volume = pd.Series(np.nan, index=index)
    data = MarketData(closes, volume, closes * 1.2, "mock-for-unit-test", "2026-01-01T00:00:00Z")
    monkeypatch.setattr(main, "fetch", lambda *a, **k: data)
    monkeypatch.setattr(main, "fetch_us_tbill", lambda: (_ for _ in ()).throw(RuntimeError(f"bad URL with key={dummy_key}")))
    monkeypatch.setattr(main, "save_assessment", lambda *a, **k: None)

    response = TestClient(main.app).post("/analyze",json={"ticker":"NVDA","market":"US","horizon":"medium_term"})
    assert response.status_code==200
    response=response.json()
    serialized = repr(response)
    assert dummy_key not in serialized
    assert response["risk_free_rate"] is None
    assert "[redacted]" in response["risk_free_warning"]
    assert response["metrics"]["performance"]["risk_free_annual"] is None
    assert response["metrics"]["performance"]["sharpe"] is None


def test_user_supplied_rate_is_marked_as_override(monkeypatch):
    from app import main
    from app.services.market_data import MarketData
    index = pd.bdate_range("2025-01-01", periods=253)
    closes = pd.Series(100 * np.cumprod(1 + .002 * np.sin(np.arange(253) * .2)), index=index)
    data = MarketData(closes, pd.Series(np.ones(253)), closes * 1.2, "mock-for-unit-test", "2026-01-01T00:00:00Z")
    monkeypatch.setattr(main, "fetch", lambda *a, **k: data)
    monkeypatch.setattr(main, "fetch_us_tbill", lambda: pytest.fail("override must bypass automatic FRED fetch"))
    monkeypatch.setattr(main, "save_assessment", lambda *a, **k: None)
    response = TestClient(main.app).post("/analyze",json={"ticker":"NVDA","market":"US","horizon":"medium_term","risk_free_rate":.035})
    assert response.status_code==200
    response=response.json()
    assert response["risk_free_rate"]["source"] == "User-supplied override"
    assert response["risk_free_rate"]["annual_yield"] == pytest.approx(.035)
    assert response["risk_free_rate"]["daily_risk_free_rate"] == pytest.approx(annual_rate_to_daily_rate(.035))
