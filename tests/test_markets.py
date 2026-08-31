"""markets.is_trading_day のテスト。

fx はハンドロール。jpx / nyse は exchange_calendars を使うため、未インストール環境では
該当テストを skip する（本番は requirements.txt で必ず入る）。いずれもネット不要。
"""

from __future__ import annotations

import importlib.util
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import config
import markets

_HAS_XCALS = importlib.util.find_spec("exchange_calendars") is not None
needs_xcals = pytest.mark.skipif(not _HAS_XCALS, reason="exchange_calendars 未インストール")


def _jst(y, m, d, hh=9):
    return datetime(y, m, d, hh, tzinfo=ZoneInfo(config.TIMEZONE))


# --- fx -------------------------------------------------------------------- #
def test_fx_weekday_true():
    ok, reason = markets.is_trading_day("fx", _jst(2026, 8, 31))  # 月曜
    assert ok is True and reason == ""


@pytest.mark.parametrize("date", [(2026, 8, 29), (2026, 8, 30)])  # 土・日
def test_fx_weekend_false(date):
    ok, reason = markets.is_trading_day("fx", _jst(*date))
    assert ok is False and reason


def test_fx_new_year_false():
    ok, reason = markets.is_trading_day("fx", _jst(2026, 1, 1))
    assert ok is False and "元日" in reason


def test_fx_extra_holiday(monkeypatch):
    monkeypatch.setattr(config, "EXTRA_HOLIDAYS", {"fx": ["2026-08-31"]}, raising=False)
    ok, reason = markets.is_trading_day("fx", _jst(2026, 8, 31))
    assert ok is False and "2026-08-31" in reason


def test_fx_legacy_holidays_key(monkeypatch):
    monkeypatch.setattr(config, "EXTRA_HOLIDAYS", {}, raising=False)
    monkeypatch.setattr(config, "FX_MARKET_HOLIDAYS", ["2026-08-31"], raising=False)
    ok, _ = markets.is_trading_day("fx", _jst(2026, 8, 31))
    assert ok is False


# --- jpx / nyse (exchange_calendars) ------------------------------------- #
@needs_xcals
def test_jpx_regular_weekday_true():
    ok, reason = markets.is_trading_day("jpx", _jst(2026, 8, 31))  # 月曜・祝日でない
    assert ok is True and reason == ""


def test_jpx_weekend_false():
    ok, _ = markets.is_trading_day("jpx", _jst(2026, 8, 30))  # 日曜（暦だけで判定可）
    assert ok is False


@needs_xcals
def test_jpx_new_year_closed():
    ok, reason = markets.is_trading_day("jpx", _jst(2026, 1, 2))  # 東証は 1/1-1/3 休場
    assert ok is False and "休場" in reason


@needs_xcals
def test_nyse_christmas_closed():
    ok, reason = markets.is_trading_day("nyse", _jst(2025, 12, 25))
    assert ok is False and "休場" in reason


@needs_xcals
def test_nyse_regular_weekday_true():
    ok, _ = markets.is_trading_day("nyse", _jst(2026, 3, 3))  # 火曜・祝日でない
    assert ok is True


def test_unknown_market_weekday_fallback():
    ok, _ = markets.is_trading_day("xyz", _jst(2026, 8, 31))
    assert ok is True
    ok, _ = markets.is_trading_day("xyz", _jst(2026, 8, 30))
    assert ok is False
