"""共通フィクスチャ / ヘルパー。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402


def make_instrument(**over) -> dict:
    """テスト用の解決済み銘柄プロファイル。"""
    p = {
        "id": "usdjpy",
        "symbol": "JPY=X",
        "asset_class": "fx",
        "display_name": "ドル円",
        "asset_label": "USD/JPY",
        "market": "fx",
        "price_decimals": 3,
        "atr_multiplier": 1.5,
        "lookback_days": 60,
        "unit": "",
        "analyst_role": "経験豊富な為替アナリスト",
        "event_hint": "FOMC・日銀会合・要人発言",
        "stooq_symbol": "usdjpy",
        "price_source_order": ["yfinance", "stooq"],
        "auto_adjust": False,
        "search_queries": ["ドル円 来週 見通し"],
        "relevance_keywords": "ドル円|USD/JPY|日銀|FRB",
        "_explicit": set(),
    }
    p.update(over)
    return p


def fake_ohlc(days: int = 80, start: float = 148.0) -> pd.DataFrame:
    idx = pd.bdate_range(end="2026-08-28", periods=days)
    rng = np.random.default_rng(42)
    close = start + np.cumsum(rng.normal(0, 0.3, days))
    high = close + rng.uniform(0.1, 0.6, days)
    low = close - rng.uniform(0.1, 0.6, days)
    open_ = close + rng.normal(0, 0.2, days)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close}, index=idx
    )


@pytest.fixture
def instrument() -> dict:
    return make_instrument()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """既定でダンプ無効・Web 無効・パスを tmp へ。"""
    monkeypatch.setattr(config, "DEEPSEEK_PROMPT_DUMP", False, raising=False)
    monkeypatch.setattr(config, "PROMPT_DUMP_DIR", str(tmp_path / "logs"), raising=False)
    monkeypatch.setattr(config, "WEB_CONTEXT_ENABLED", False, raising=False)
    monkeypatch.setattr(config, "TAVILY_API_KEY", "", raising=False)
