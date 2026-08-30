"""ネット / API 不要のスモークテスト。

    cd usdjpy-forecast && python -m pytest -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import forecast  # noqa: E402
import main  # noqa: E402


def _fake_ohlc(days: int = 80, start: float = 148.0) -> pd.DataFrame:
    idx = pd.bdate_range(end="2026-08-28", periods=days)
    rng = np.random.default_rng(42)
    close = start + np.cumsum(rng.normal(0, 0.3, days))
    high = close + rng.uniform(0.1, 0.6, days)
    low = close - rng.uniform(0.1, 0.6, days)
    open_ = close + rng.normal(0, 0.2, days)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close}, index=idx
    )


def test_compute_features_shape_and_bounds():
    feats = forecast.compute_features(_fake_ohlc())
    for key in ("current", "week_high", "week_low", "atr14", "sma20", "sma50", "trend"):
        assert key in feats
    assert feats["week_low"] <= feats["current"] <= feats["week_high"]
    assert feats["atr14"] > 0
    assert feats["trend"] in {"uptrend", "downtrend", "range"}


def test_extract_json_handles_wrapped_text():
    assert forecast._extract_json('{"week_high": 150, "week_low": 147}') == {
        "week_high": 150,
        "week_low": 147,
    }
    wrapped = "はい、以下です:\n```json\n{\"week_high\": 150.2, \"week_low\": 146.8}\n```"
    assert forecast._extract_json(wrapped)["week_high"] == 150.2


def test_reconcile_keeps_current_inside_range():
    hi, lo = forecast._reconcile(149.0, 148.0, 146.0)  # current 上抜け
    assert lo <= 149.0 <= hi
    hi, lo = forecast._reconcile(145.0, 150.0, 146.0)  # current 下抜け
    assert lo <= 145.0 <= hi


def test_degraded_forecast_uses_atr_multiplier():
    feats = {"current": 148.0, "atr14": 1.0}
    monkey_mult = config.ATR_MULTIPLIER
    fc = forecast._degraded_forecast(feats, "test")
    assert fc["source"] == "degraded"
    assert fc["week_high"] == pytest.approx(148.0 + monkey_mult)
    assert fc["week_low"] == pytest.approx(148.0 - monkey_mult)


def test_get_forecast_detail_degraded(monkeypatch):
    monkeypatch.setattr(forecast, "_load_prices", lambda _d: (_fake_ohlc(), "fake"))
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    d = forecast.get_usdjpy_forecast_detail()
    assert d["source"] == "degraded"
    assert d["week_low"] <= d["current"] <= d["week_high"]


def test_get_forecast_list_with_mocked_deepseek(monkeypatch):
    monkeypatch.setattr(forecast, "_load_prices", lambda _d: (_fake_ohlc(), "fake"))
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(
        forecast,
        "_deepseek_forecast",
        lambda feats: {
            "week_high": feats["current"] + 2.0,
            "week_low": feats["current"] - 2.0,
            "rationale": "mock",
            "source": "deepseek",
        },
    )
    values = forecast.get_usdjpy_forecast()
    assert len(values) == 3
    assert all(isinstance(v, float) for v in values)
    week_high, week_low, current = values
    assert week_high > current > week_low


def test_deepseek_failure_falls_back_to_degraded(monkeypatch):
    monkeypatch.setattr(forecast, "_load_prices", lambda _d: (_fake_ohlc(), "fake"))
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-test")

    def boom(_feats):
        raise RuntimeError("api down")

    monkeypatch.setattr(forecast, "_deepseek_forecast", boom)
    d = forecast.get_usdjpy_forecast_detail()
    assert d["source"] == "degraded"


def test_build_message_format():
    msg = main.build_message([150.12, 146.98, 148.55], "テスト根拠")
    assert "現在値  : 148.55" in msg
    assert "予想上値: 150.12" in msg
    assert "予想下値: 146.98" in msg
    assert "テスト根拠" in msg
