"""ネット / API 不要のスモークテスト。

    cd usdjpy-forecast && python -m pytest -q
"""

from __future__ import annotations

import importlib.util
import types

import pytest

import config
import forecast
from conftest import fake_ohlc, make_instrument

needs_openai = pytest.mark.skipif(
    importlib.util.find_spec("openai") is None, reason="openai 未インストール"
)


def _fake_openai(content: str):
    """openai.OpenAI の差し替え用ファクトリ。create() が固定 content を返す。"""

    def _create(**_kw):
        message = types.SimpleNamespace(content=content)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])

    completions = types.SimpleNamespace(create=_create)
    chat = types.SimpleNamespace(completions=completions)

    class _Client:
        def __init__(self, *_a, **_k):
            self.chat = chat

    return _Client


# --------------------------------------------------------------------------- #
# テクニカル / JSON 抽出 / 整合化
# --------------------------------------------------------------------------- #
def test_compute_features_shape_and_bounds():
    feats = forecast.compute_features(fake_ohlc(), decimals=3)
    for key in ("current", "week_high", "week_low", "atr14", "sma20", "sma50", "trend"):
        assert key in feats
    assert feats["week_low"] <= feats["current"] <= feats["week_high"]
    assert feats["atr14"] > 0
    assert feats["trend"] in {"uptrend", "downtrend", "range"}


def test_compute_features_respects_decimals():
    feats = forecast.compute_features(fake_ohlc(), decimals=0)
    assert feats["current"] == round(feats["current"])


def test_extract_json_handles_wrapped_text():
    assert forecast._extract_json('{"week_high": 150, "week_low": 147}') == {
        "week_high": 150,
        "week_low": 147,
    }
    wrapped = "はい、以下です:\n```json\n{\"week_high\": 150.2, \"week_low\": 146.8}\n```"
    assert forecast._extract_json(wrapped)["week_high"] == 150.2


def test_reconcile_keeps_current_inside_range():
    hi, lo = forecast._reconcile(149.0, 148.0, 146.0, 3)
    assert lo <= 149.0 <= hi
    hi, lo = forecast._reconcile(145.0, 150.0, 146.0, 3)
    assert lo <= 145.0 <= hi


def test_reconcile_decimals_zero_for_stocks():
    hi, lo = forecast._reconcile(2714.0, 2800.4, 2650.6, 0)
    assert hi == 2800 and lo == 2651


def test_degraded_forecast_uses_instrument_multiplier():
    inst = make_instrument(atr_multiplier=2.0)
    fc = forecast._degraded_forecast(inst, {"current": 148.0, "atr14": 1.0}, "test")
    assert fc["source"] == "degraded"
    assert fc["week_high"] == pytest.approx(150.0)
    assert fc["week_low"] == pytest.approx(146.0)


# --------------------------------------------------------------------------- #
# プロンプト組み立て
# --------------------------------------------------------------------------- #
def test_system_prompt_uses_instrument_fields():
    inst = make_instrument(
        analyst_role="経験豊富な米国株アナリスト",
        asset_label="Apple Inc. (AAPL)",
        event_hint="四半期決算・FRB 金融政策",
    )
    sp = forecast._build_system_prompt(inst)
    assert "経験豊富な米国株アナリスト" in sp
    assert "Apple Inc. (AAPL)" in sp
    assert "四半期決算・FRB 金融政策" in sp


def test_build_messages_includes_article_titles(instrument):
    web = {
        "articles": [
            {
                "title": "日銀会合プレビュー",
                "url": "https://x/1",
                "published": "2026-08-29",
                "snippet": "抜粋テキスト本文",
            }
        ]
    }
    user = forecast._build_messages(instrument, {"current": 147.0}, web)[1]["content"]
    assert "日銀会合プレビュー" in user
    assert "参考ニュース" in user
    assert "抜粋テキスト本文" in user
    assert "[2026-08-29]" in user
    assert "USD/JPY" in user  # asset_label が見出しに入る


def test_build_messages_no_news_section_when_empty(instrument):
    user = forecast._build_messages(instrument, {"current": 147.0}, {"articles": []})[1]["content"]
    assert "参考ニュース" not in user


# --------------------------------------------------------------------------- #
# get_forecast_detail
# --------------------------------------------------------------------------- #
def test_detail_degraded(monkeypatch, instrument):
    monkeypatch.setattr(forecast, "_load_prices", lambda _i: (fake_ohlc(), "fake"))
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    d = forecast.get_forecast_detail(instrument)
    assert d["source"] == "degraded"
    assert d["week_low"] <= d["current"] <= d["week_high"]
    assert d["instrument_id"] == "usdjpy"
    assert d["display_name"] == "ドル円"


def test_get_forecast_list_with_mocked_deepseek(monkeypatch, instrument):
    monkeypatch.setattr(forecast, "_load_prices", lambda _i: (fake_ohlc(), "fake"))
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(
        forecast,
        "_deepseek_forecast",
        lambda inst, feats, web=None: {
            "week_high": feats["current"] + 2.0,
            "week_low": feats["current"] - 2.0,
            "rationale": "mock",
            "source": "deepseek",
        },
    )
    values = forecast.get_forecast(instrument)
    assert len(values) == 3
    week_high, week_low, current = values
    assert week_high > current > week_low


def test_deepseek_failure_falls_back_to_degraded(monkeypatch, instrument):
    monkeypatch.setattr(forecast, "_load_prices", lambda _i: (fake_ohlc(), "fake"))
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-test")

    def boom(_inst, _feats, _web=None):
        raise RuntimeError("api down")

    monkeypatch.setattr(forecast, "_deepseek_forecast", boom)
    d = forecast.get_forecast_detail(instrument)
    assert d["source"] == "degraded"


def test_detail_includes_web_articles(monkeypatch, instrument):
    monkeypatch.setattr(forecast, "_load_prices", lambda _i: (fake_ohlc(), "fake"))
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-test")
    fake_web = {
        "used": True,
        "articles": [
            {"title": "T1", "url": "https://x/1", "published": "2026-08-29", "snippet": "s1"},
            {"title": "T2", "url": "https://x/2", "published": "2026-08-28", "snippet": "s2"},
        ],
    }
    monkeypatch.setattr(forecast, "_collect_web_context", lambda _i: fake_web)
    monkeypatch.setattr(
        forecast,
        "_deepseek_forecast",
        lambda inst, feats, web=None: {
            "week_high": feats["current"] + 2,
            "week_low": feats["current"] - 2,
            "rationale": "m",
            "bias": "up",
            "key_events": ["FOMC"],
            "source": "deepseek",
        },
    )
    d = forecast.get_forecast_detail(instrument)
    assert [a["url"] for a in d["web_articles"]] == ["https://x/1", "https://x/2"]
    assert d["web_sources"] == ["https://x/1", "https://x/2"]
    assert d["web_used"] is True
    assert d["bias"] == "up"
    assert d["key_events"] == ["FOMC"]


def test_web_context_exception_is_safe(monkeypatch, instrument):
    monkeypatch.setattr(forecast, "_load_prices", lambda _i: (fake_ohlc(), "fake"))
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(config, "WEB_CONTEXT_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "TAVILY_API_KEY", "tvly", raising=False)

    import web_context

    monkeypatch.setattr(web_context, "get_web_context", lambda _i: (_ for _ in ()).throw(RuntimeError("web down")))
    monkeypatch.setattr(
        forecast,
        "_deepseek_forecast",
        lambda inst, feats, web=None: {
            "week_high": feats["current"] + 1,
            "week_low": feats["current"] - 1,
            "rationale": "m",
            "source": "deepseek",
        },
    )
    assert len(forecast.get_forecast(instrument)) == 3


# --------------------------------------------------------------------------- #
# DeepSeek 入力ダンプ（銘柄別ファイル）
# --------------------------------------------------------------------------- #
_OK_JSON = '{"week_high": 149.1, "week_low": 145.6, "rationale": "r", "bias": "up", "key_events": ["FOMC"]}'


@needs_openai
def test_deepseek_input_dump_written_per_instrument(monkeypatch, tmp_path, instrument):
    monkeypatch.setattr(config, "DEEPSEEK_PROMPT_DUMP", True, raising=False)
    monkeypatch.setattr(config, "PROMPT_DUMP_DIR", str(tmp_path / "logs"), raising=False)
    monkeypatch.setattr("openai.OpenAI", _fake_openai(_OK_JSON))
    web = {
        "used": True,
        "articles": [
            {"title": "日銀が政策修正を示唆", "url": "https://x/1", "published": "2026-08-29", "snippet": "本文抜粋"}
        ],
    }
    out = forecast._deepseek_forecast(instrument, {"current": 147.0, "atr14": 0.8}, web)
    assert out["week_high"] == 149.1
    dump = tmp_path / "logs" / "deepseek_usdjpy.txt"
    text = dump.read_text(encoding="utf-8")
    assert "日銀が政策修正を示唆" in text
    assert "----- raw response -----" in text
    assert "----- parsed -----" in text


@needs_openai
def test_deepseek_input_dump_disabled(monkeypatch, tmp_path, instrument):
    monkeypatch.setattr(config, "DEEPSEEK_PROMPT_DUMP", False, raising=False)
    monkeypatch.setattr(config, "PROMPT_DUMP_DIR", str(tmp_path / "logs"), raising=False)
    monkeypatch.setattr("openai.OpenAI", _fake_openai('{"week_high": 1, "week_low": 0}'))
    forecast._deepseek_forecast(instrument, {"current": 1.0, "atr14": 0.1}, None)
    assert not (tmp_path / "logs" / "deepseek_usdjpy.txt").exists()


def test_degraded_mode_writes_dump(monkeypatch, tmp_path, instrument):
    monkeypatch.setattr(config, "DEEPSEEK_PROMPT_DUMP", True, raising=False)
    monkeypatch.setattr(config, "PROMPT_DUMP_DIR", str(tmp_path / "logs"), raising=False)
    monkeypatch.setattr(forecast, "_load_prices", lambda _i: (fake_ohlc(), "fake"))
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(
        forecast,
        "_collect_web_context",
        lambda _i: {"used": True, "articles": [
            {"title": "記事X", "url": "https://x/9", "published": "2026-08-29", "snippet": "s"}
        ]},
    )
    d = forecast.get_forecast_detail(instrument)
    assert d["source"] == "degraded"
    text = (tmp_path / "logs" / "deepseek_usdjpy.txt").read_text(encoding="utf-8")
    assert "degraded" in text and "記事X" in text


def test_dump_failure_does_not_break_forecast(monkeypatch, tmp_path, instrument):
    monkeypatch.setattr(config, "DEEPSEEK_PROMPT_DUMP", True, raising=False)
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    monkeypatch.setattr(config, "PROMPT_DUMP_DIR", str(blocker / "sub"), raising=False)
    monkeypatch.setattr(forecast, "_load_prices", lambda _i: (fake_ohlc(), "fake"))
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    d = forecast.get_forecast_detail(instrument)
    assert d["week_low"] <= d["current"] <= d["week_high"]


# --------------------------------------------------------------------------- #
# 端寄り判定
# --------------------------------------------------------------------------- #
def test_edge_position_center_no_alert():
    e = forecast._edge_position(147.5, 150.0, 145.0)
    assert e["side"] == "中央" and e["ratio"] == 0.0 and e["alert"] is False


def test_edge_position_skewed_up_alerts(monkeypatch):
    monkeypatch.setattr(config, "EDGE_ALERT_RATIO", 0.30, raising=False)
    e = forecast._edge_position(149.5, 150.0, 145.0)
    assert e["side"] == "上寄り" and e["ratio"] == pytest.approx(0.4) and e["alert"] is True


def test_edge_position_zero_width_no_zero_division():
    e = forecast._edge_position(150.0, 150.0, 150.0)
    assert e["ratio"] == 0.0 and e["alert"] is False
