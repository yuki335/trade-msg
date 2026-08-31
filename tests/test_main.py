"""main.py（複数銘柄ループ・メッセージ組み立て・フラグ）のテスト。"""

from __future__ import annotations

import json
import sys

import pytest

import config
import main
from conftest import make_instrument


def _detail_stub(**over) -> dict:
    d = {
        "instrument_id": "usdjpy",
        "display_name": "ドル円",
        "asset_class": "fx",
        "market": "fx",
        "price_decimals": 2,
        "unit": "",
        "week_high": 150.0,
        "week_low": 145.0,
        "current": 147.5,
        "rationale": "r",
        "source": "deepseek",
        "web_articles": [],
        "web_sources": [],
        "web_used": False,
        "bias": "",
        "key_events": [],
        "edge": {"ratio": 0.0, "side": "中央", "alert": False, "threshold": 0.3},
    }
    d.update(over)
    return d


# --------------------------------------------------------------------------- #
# build_message
# --------------------------------------------------------------------------- #
def test_build_message_basic():
    msg = main.build_message(_detail_stub())
    assert "=== ドル円 1週間見通し" in msg
    assert "現在値  : 147.50" in msg
    assert "予想上値: 150.00" in msg
    assert "予想下値: 145.00" in msg


def test_build_message_stock_decimals_and_unit():
    d = _detail_stub(display_name="トヨタ自動車", price_decimals=0, unit="円",
                     week_high=2800.0, week_low=2650.0, current=2714.0)
    msg = main.build_message(d)
    assert "現在値  : 2714 円" in msg
    assert "予想上値: 2800 円" in msg


def test_build_message_web_summary_and_bias():
    d = _detail_stub(
        web_articles=[
            {"title": "日銀が政策修正を示唆する報道", "published": "2026-08-29", "url": "https://x/1"},
            {"title": "Fed高官タカ派", "published": "2026-08-28", "url": "https://x/2"},
        ],
        bias="up",
        key_events=["9/5 米雇用統計"],
    )
    msg = main.build_message(d)
    assert "--- 参照ニュース (2件) ---" in msg
    assert "[08-29]" in msg
    assert "bias : up" in msg
    assert "注目 : 9/5 米雇用統計" in msg


def test_build_message_line_limit(monkeypatch):
    monkeypatch.setattr(config, "WEB_SUMMARY_MAX_LINES", 2, raising=False)
    arts = [{"title": f"T{i}", "published": "2026-08-29", "url": f"https://x/{i}"} for i in range(5)]
    msg = main.build_message(_detail_stub(web_articles=arts))
    assert msg.count("・[") == 2 and "(5件)" in msg


def test_build_message_degraded_hides_web_and_bias():
    d = _detail_stub(
        source="degraded",
        web_articles=[{"title": "T", "published": "2026-08-29", "url": "https://x/1"}],
        bias="up",
        key_events=["x"],
    )
    msg = main.build_message(d)
    assert "参照ニュース" not in msg and "bias" not in msg


def test_build_message_edge_line_on_alert():
    d = _detail_stub(edge={"ratio": 0.41, "side": "下寄り", "alert": True, "threshold": 0.30})
    msg = main.build_message(d)
    assert "位置    :" in msg and "下寄り" in msg and "41%" in msg


# --------------------------------------------------------------------------- #
# 銘柄セレクタ
# --------------------------------------------------------------------------- #
def test_arg_value_forms():
    assert main._arg_value(["--market", "jpx"], "--market") == "jpx"
    assert main._arg_value(["--market=nyse"], "--market") == "nyse"
    assert main._arg_value(["--force"], "--market") is None


def test_select_by_market_and_only():
    insts = [
        make_instrument(id="usdjpy", symbol="JPY=X", market="fx"),
        make_instrument(id="toyota", symbol="7203.T", market="jpx"),
        make_instrument(id="aapl", symbol="AAPL", market="nyse"),
    ]
    assert [i["id"] for i in main._select(insts, ["--market", "jpx"])] == ["toyota"]
    assert [i["id"] for i in main._select(insts, ["--only", "aapl,usdjpy"])] == ["usdjpy", "aapl"]
    assert [i["id"] for i in main._select(insts, ["--only", "7203.T"])] == ["toyota"]


# --------------------------------------------------------------------------- #
# main() ループ
# --------------------------------------------------------------------------- #
@pytest.fixture
def _two_markets(monkeypatch):
    insts = [
        make_instrument(id="usdjpy", symbol="JPY=X", market="fx"),
        make_instrument(id="toyota", symbol="7203.T", market="jpx", display_name="トヨタ"),
    ]
    monkeypatch.setattr(main.instruments_mod, "load_instruments", lambda enrich_meta=True: insts)
    return insts


def test_main_skips_closed_market_runs_open(monkeypatch, _two_markets):
    monkeypatch.setattr(config, "TRADING_DAY_CHECK", True, raising=False)
    monkeypatch.setattr(main, "is_trading_day",
                        lambda m, now=None: (True, "") if m == "fx" else (False, "jpx 休場"))
    monkeypatch.setattr(main, "get_forecast_detail", lambda i: _detail_stub(instrument_id=i["id"]))
    sent = []
    monkeypatch.setattr(main, "send_message", lambda msg: sent.append(msg))
    monkeypatch.setattr(sys, "argv", ["main.py"])
    main.main()
    assert len(sent) == 1


def test_main_force_bypasses_check(monkeypatch, _two_markets):
    monkeypatch.setattr(config, "TRADING_DAY_CHECK", True, raising=False)
    monkeypatch.setattr(main, "is_trading_day",
                        lambda m, now=None: (_ for _ in ()).throw(AssertionError("must not be called")))
    monkeypatch.setattr(main, "get_forecast_detail", lambda i: _detail_stub(instrument_id=i["id"]))
    sent = []
    monkeypatch.setattr(main, "send_message", lambda msg: sent.append(msg))
    monkeypatch.setattr(sys, "argv", ["main.py", "--force"])
    main.main()
    assert len(sent) == 2


def test_main_one_failure_does_not_stop_others(monkeypatch, _two_markets):
    monkeypatch.setattr(config, "TRADING_DAY_CHECK", False, raising=False)

    def detail(i):
        if i["id"] == "usdjpy":
            raise RuntimeError("price fetch failed")
        return _detail_stub(instrument_id=i["id"])

    monkeypatch.setattr(main, "get_forecast_detail", detail)
    sent = []
    monkeypatch.setattr(main, "send_message", lambda msg: sent.append(msg))
    monkeypatch.setattr(sys, "argv", ["main.py"])
    with pytest.raises(SystemExit):
        main.main()
    assert len(sent) == 1  # toyota は送信された


def test_main_json_outputs_dict(monkeypatch, capsys, _two_markets):
    monkeypatch.setattr(main, "get_forecast_detail",
                        lambda i: _detail_stub(instrument_id=i["id"], week_high=1.0, week_low=0.5, current=0.7))
    monkeypatch.setattr(main, "send_message", lambda msg: (_ for _ in ()).throw(AssertionError("no send")))
    monkeypatch.setattr(sys, "argv", ["main.py", "--json"])
    main.main()
    out = json.loads(capsys.readouterr().out)
    assert out == {"usdjpy": [1.0, 0.5, 0.7], "toyota": [1.0, 0.5, 0.7]}


def test_main_check_no_network(monkeypatch, capsys, _two_markets):
    monkeypatch.setattr(main, "is_trading_day", lambda m, now=None: (True, ""))
    monkeypatch.setattr(main, "get_forecast_detail",
                        lambda i: (_ for _ in ()).throw(AssertionError("no forecast on --check")))
    monkeypatch.setattr(sys, "argv", ["main.py", "--check"])
    main.main()
    out = capsys.readouterr().out
    assert "usdjpy" in out and "toyota" in out and "市場サマリ" in out
