"""instruments.py（プロファイル解決・YAML 読み込み）のテスト。ネット不要。"""

from __future__ import annotations

import textwrap

import pytest

import instruments


# --------------------------------------------------------------------------- #
# resolve
# --------------------------------------------------------------------------- #
def test_resolve_fx_defaults_and_pair_parse():
    p = instruments.resolve({"symbol": "JPY=X", "asset_class": "fx"})
    assert p["id"] == "jpyx"
    assert p["asset_label"] == "USD/JPY"
    assert p["market"] == "fx"
    assert p["price_decimals"] == 3
    assert p["stooq_symbol"] == "usdjpy"
    assert p["auto_adjust"] is False
    assert p["search_queries"]                      # 自動生成される
    assert "USD/JPY" in p["relevance_keywords"]


def test_resolve_cross_pair():
    p = instruments.resolve({"symbol": "EURJPY=X", "asset_class": "fx"})
    assert p["asset_label"] == "EUR/JPY"
    assert p["stooq_symbol"] == "eurjpy"


def test_resolve_jp_stock_defaults():
    p = instruments.resolve({"symbol": "7203.T", "asset_class": "jp_stock"})
    assert p["market"] == "jpx"
    assert p["price_decimals"] == 1
    assert p["stooq_symbol"] == "7203.jp"
    assert p["auto_adjust"] is True
    assert "7203" in p["relevance_keywords"]


def test_resolve_us_stock_and_class_guess():
    p = instruments.resolve({"symbol": "AAPL"})           # asset_class 省略 → 推定
    assert p["asset_class"] == "us_stock"
    assert p["market"] == "nyse"
    assert p["stooq_symbol"] == "aapl.us"
    assert p["price_decimals"] == 2


def test_resolve_explicit_overrides_defaults():
    p = instruments.resolve({
        "symbol": "AAPL", "asset_class": "us_stock",
        "atr_multiplier": 3.3, "search_queries": ["custom q"],
    })
    assert p["atr_multiplier"] == 3.3
    assert p["search_queries"] == ["custom q"]
    assert "atr_multiplier" in p["_explicit"]
    assert "search_queries" in p["_explicit"]


def test_resolve_bad_numeric_raises():
    with pytest.raises(ValueError):
        instruments.resolve({"symbol": "AAPL", "asset_class": "us_stock", "atr_multiplier": "abc"})


def test_resolve_empty_symbol_raises():
    with pytest.raises(ValueError):
        instruments.resolve({"symbol": "  ", "asset_class": "fx"})


def test_resolve_unknown_asset_class_raises():
    with pytest.raises(ValueError):
        instruments.resolve({"symbol": "X", "asset_class": "crypto"})


# --------------------------------------------------------------------------- #
# load_instruments
# --------------------------------------------------------------------------- #
def _write_yaml(tmp_path, body: str):
    p = tmp_path / "instruments.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_load_skips_broken_keeps_valid(tmp_path, capsys):
    path = _write_yaml(tmp_path, """
        - symbol: JPY=X
          asset_class: fx
        - asset_class: fx          # symbol 欠落 → スキップ
        - symbol: AAPL
          asset_class: us_stock
          atr_multiplier: nope     # 型不正 → スキップ
        - symbol: 7203.T
          asset_class: jp_stock
          enabled: false           # 無効 → 除外
        - symbol: MSFT
          asset_class: us_stock
    """)
    out = instruments.load_instruments(enrich_meta=False, path=path)
    assert [p["id"] for p in out] == ["jpyx", "msft"]
    err = capsys.readouterr().err
    assert "symbol が未指定" in err
    assert "解決に失敗" in err


def test_load_warns_unknown_key(tmp_path, capsys):
    path = _write_yaml(tmp_path, """
        - symbol: JPY=X
          asset_class: fx
          atr_multiplyer: 2.0
    """)
    instruments.load_instruments(enrich_meta=False, path=path)
    assert "未知のキー" in capsys.readouterr().err


def test_load_bad_yaml_returns_empty(tmp_path, capsys):
    path = tmp_path / "instruments.yaml"
    path.write_text("::: not : valid : yaml\n  - [", encoding="utf-8")
    assert instruments.load_instruments(enrich_meta=False, path=path) == []


def test_warn_deprecated_config(monkeypatch, capsys):
    import config
    monkeypatch.setattr(config, "ATR_MULTIPLIER", 1.5, raising=False)
    instruments.warn_deprecated_config()
    assert "config.ATR_MULTIPLIER は廃止" in capsys.readouterr().err
