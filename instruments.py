"""instruments.yaml を読み、銘柄プロファイル（1 銘柄 = 1 dict）に解決する。

必須は ``symbol`` のみ（``asset_class`` は symbol から推定可）。省略項目は
``CLASS_DEFAULTS``（asset_class ごとの既定値）→ 実行時に yfinance のメタ情報で補完。
``instruments.yaml`` の明示値が常に最優先。

公開 API:
    load_instruments(enrich_meta=True) -> list[dict]   すべての有効な銘柄プロファイル
    resolve(entry) -> dict                             1 エントリを解決（ネット不要・純関数）
    warn_deprecated_config()                           config.py の旧・銘柄別キーを警告

優先順位:  instruments.yaml 明示値  >  CLASS_DEFAULTS  >  ハードコード既定
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import config

_YAML_PATH = Path(__file__).resolve().parent / "instruments.yaml"
_CACHE_DIR = Path(__file__).resolve().parent / "cache"

ASSET_CLASSES = ("fx", "jp_stock", "us_stock")

# asset_class ごとの既定値。instruments.yaml に書かれた値が優先される。
CLASS_DEFAULTS: dict[str, dict] = {
    "fx": {
        "market": "fx",
        "price_decimals": 3,
        "atr_multiplier": 1.5,
        "lookback_days": 60,
        "unit": "",
        "analyst_role": "経験豊富な為替アナリスト",
        "event_hint": "FOMC・日銀会合・要人発言・重要指標・地政学リスク",
        "auto_adjust": False,
    },
    "jp_stock": {
        "market": "jpx",
        "price_decimals": 1,
        "atr_multiplier": 1.8,
        "lookback_days": 90,
        "unit": "円",
        "analyst_role": "経験豊富な日本株アナリスト",
        "event_hint": (
            "四半期決算・通期ガイダンス・想定為替レート・自社株買い/増配・"
            "格付け変更・日銀会合の株式への影響・セクター需給"
        ),
        "auto_adjust": True,
    },
    "us_stock": {
        "market": "nyse",
        "price_decimals": 2,
        "atr_multiplier": 2.0,
        "lookback_days": 90,
        "unit": "USD",
        "analyst_role": "経験豊富な米国株アナリスト",
        "event_hint": (
            "四半期決算・EPS 予想・通期ガイダンス・FRB 金融政策・主要経済指標・"
            "アナリスト格付け変更"
        ),
        "auto_adjust": True,
    },
}

# instruments.yaml で銘柄別に指定できるキー（これ以外は警告して無視）
_ALLOWED_KEYS = {
    "id", "symbol", "asset_class", "display_name", "asset_label",
    "market", "price_decimals", "atr_multiplier", "lookback_days", "unit",
    "analyst_role", "event_hint",
    "stooq_symbol", "price_source_order", "auto_adjust",
    "search_queries", "relevance_keywords", "enabled",
}

# config.py に残っていると警告する旧・銘柄別キー（instruments.yaml へ移動済み）
_DEPRECATED_CONFIG_KEYS = {
    "SEARCH_QUERIES": "instruments.yaml の search_queries",
    "ATR_MULTIPLIER": "instruments.yaml の atr_multiplier",
    "PRICE_LOOKBACK_DAYS": "instruments.yaml の lookback_days",
    "PROMPT_DUMP_PATH": "config.PROMPT_DUMP_DIR",
}

_CLASS_KEYWORDS = {
    "fx": (
        "為替|外国為替|通貨|ドル|円|ユーロ|ポンド|日銀|BOJ|FRB|Fed|FOMC|ECB|"
        "金利|利上げ|利下げ|介入|インフレ|CPI|雇用統計"
    ),
    "jp_stock": (
        "株価|決算|営業利益|純利益|通期|上方修正|下方修正|ガイダンス|目標株価|"
        "格付け|日経平均|TOPIX|自社株買い|増配|受注"
    ),
    "us_stock": (
        "stock|shares|earnings|guidance|revenue|EPS|price target|upgrade|"
        "downgrade|Fed|FOMC|Nasdaq|S&P 500|analyst"
    ),
}


def _warn(msg: str) -> None:
    print(f"[warn] instruments: {msg}", file=sys.stderr)


def _now() -> datetime:
    return datetime.now(ZoneInfo(config.TIMEZONE))


# --------------------------------------------------------------------------- #
# ネット不要の導出
# --------------------------------------------------------------------------- #
def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "", str(text).lower())
    return s or "inst"


def guess_asset_class(symbol: str) -> str:
    s = symbol.upper()
    if s.endswith("=X"):
        return "fx"
    if s.endswith(".T") or s.endswith(".JP"):
        return "jp_stock"
    return "us_stock"


def _fx_pair(symbol: str) -> str:
    core = symbol.upper().replace("=X", "")
    if len(core) == 3:            # "JPY=X" -> USD/JPY
        return f"USD/{core}"
    if len(core) == 6:           # "EURJPY=X" -> EUR/JPY
        return f"{core[:3]}/{core[3:]}"
    return symbol


def _derive_stooq(symbol: str, asset_class: str) -> str:
    s = symbol.upper()
    if asset_class == "fx":
        core = s.replace("=X", "")
        if len(core) == 3:
            core = "USD" + core
        return core.lower()
    if asset_class == "jp_stock":
        return s.split(".")[0].lower() + ".jp"
    if s.startswith("^"):
        return s.lower()
    return s.split(".")[0].lower() + ".us"


def _default_queries(p: dict) -> list[str]:
    ac = p["asset_class"]
    name = p.get("display_name") or p["asset_label"]
    if ac == "fx":
        return [
            f"{p['asset_label']} 為替 見通し 来週",
            f"{p['asset_label']} outlook this week",
            "日銀 FRB 金融政策 為替 今週",
        ]
    if ac == "jp_stock":
        code = p["symbol"].split(".")[0]
        return [
            f"{name} 株価 見通し",
            f"{code} 決算 目標株価",
            f"{name} アナリスト 予想 来週",
        ]
    return [
        f"{name} stock forecast next week",
        f"{p['symbol']} earnings price target",
        f"{name} analyst rating outlook",
    ]


def _default_relevance(p: dict) -> str:
    ac = p["asset_class"]
    toks: set[str] = {p["symbol"], p["asset_label"]}
    name = p.get("display_name") or ""
    if name:
        toks.add(name)
    if ac == "jp_stock":
        toks.add(p["symbol"].split(".")[0])          # 4 桁コード単体
    if ac == "fx":
        core = p["symbol"].upper().replace("=X", "")
        if len(core) == 3:
            toks.update({"USD", core})
        elif len(core) == 6:
            toks.update({core[:3], core[3:]})
    escaped = sorted({re.escape(t) for t in toks if t})
    return "|".join(escaped) + "|" + _CLASS_KEYWORDS[ac]


# --------------------------------------------------------------------------- #
# resolve: 1 エントリ -> 完全なプロファイル（ネット不要）
# --------------------------------------------------------------------------- #
def resolve(entry: dict) -> dict:
    """1 エントリを解決する。純関数。足りない項目はすべて既定値で埋める。"""
    ac = entry.get("asset_class") or guess_asset_class(str(entry.get("symbol", "")))
    if ac not in CLASS_DEFAULTS:
        raise ValueError(f"unknown asset_class: {ac!r}")
    symbol = str(entry.get("symbol", "")).strip()
    if not symbol:
        raise ValueError("symbol is empty")

    explicit = {
        k for k, v in entry.items()
        if k in _ALLOWED_KEYS and k not in ("symbol", "asset_class", "enabled") and v is not None
    }

    p = dict(CLASS_DEFAULTS[ac])
    for k, v in entry.items():
        if k in _ALLOWED_KEYS and k not in ("enabled", "id") and v is not None:
            p[k] = v
    p["asset_class"] = ac
    p["symbol"] = symbol
    p["id"] = _slug(entry.get("id") or symbol)
    p.setdefault("stooq_symbol", _derive_stooq(symbol, ac))
    p.setdefault("asset_label", _fx_pair(symbol) if ac == "fx" else symbol)
    p.setdefault("price_source_order", ["yfinance", "stooq"])
    p.setdefault("display_name", p["asset_label"])

    # 数値項目の型チェック（不正なら例外 → 呼び出し側でスキップ）
    for k in ("price_decimals", "lookback_days"):
        try:
            p[k] = int(p[k])
        except (TypeError, ValueError):
            raise ValueError(f"{k} は整数で指定してください: {p[k]!r}")
    try:
        p["atr_multiplier"] = float(p["atr_multiplier"])
    except (TypeError, ValueError):
        raise ValueError(f"atr_multiplier は数値で指定してください: {p['atr_multiplier']!r}")
    p["auto_adjust"] = bool(p["auto_adjust"])

    if "search_queries" not in p or not p["search_queries"]:
        p["search_queries"] = _default_queries(p)
    if "relevance_keywords" not in p or not p["relevance_keywords"]:
        p["relevance_keywords"] = _default_relevance(p)

    p["_explicit"] = explicit
    return p


# --------------------------------------------------------------------------- #
# enrich: yfinance メタ情報で display_name / 検索クエリを改善（任意）
# --------------------------------------------------------------------------- #
def _meta_cache_path(inst_id: str) -> Path:
    return _CACHE_DIR / f"meta_{inst_id}.json"


def _fetch_yf_meta(symbol: str) -> dict:
    import yfinance as yf

    info = yf.Ticker(symbol).info or {}
    return {
        "longName": (info.get("longName") or info.get("shortName") or "").strip(),
        "sector": (info.get("sector") or "").strip(),
        "currency": (info.get("currency") or "").strip(),
        "quoteType": (info.get("quoteType") or "").strip(),
    }


def _get_meta(inst_id: str, symbol: str, now: datetime | None = None) -> dict:
    now = now or _now()
    today = now.strftime("%Y-%m-%d")
    path = _meta_cache_path(inst_id)
    try:
        if path.exists():
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("_date") == today:
                return cached
    except Exception:  # noqa: BLE001
        pass
    meta = _fetch_yf_meta(symbol)          # 失敗時は呼び出し側で握る
    meta["_date"] = today
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return meta


def enrich(p: dict, now: datetime | None = None) -> dict:
    """yfinance の会社名などでプロファイルを補強する。ユーザー明示値は変更しない。"""
    explicit = p.get("_explicit", set())
    meta = _get_meta(p["id"], p["symbol"], now)
    name = meta.get("longName")
    if name and "display_name" not in explicit:
        p["display_name"] = name
        if "search_queries" not in explicit:
            p["search_queries"] = _default_queries(p)
        if "relevance_keywords" not in explicit:
            p["relevance_keywords"] = _default_relevance(p)
    if meta.get("sector"):
        p.setdefault("sector", meta["sector"])
    return p


# --------------------------------------------------------------------------- #
# config.py の旧キー検出
# --------------------------------------------------------------------------- #
def warn_deprecated_config() -> None:
    for key, moved_to in _DEPRECATED_CONFIG_KEYS.items():
        if hasattr(config, key):
            _warn(f"config.{key} は廃止されました（{moved_to} を使用）。値は無視されます。")
    # FX_MARKET_HOLIDAYS は EXTRA_HOLIDAYS['fx'] へ自動移送（markets.py 側で吸収）
    if getattr(config, "FX_MARKET_HOLIDAYS", None):
        _warn("config.FX_MARKET_HOLIDAYS は EXTRA_HOLIDAYS['fx'] へ移行してください（今回は自動移送）。")


# --------------------------------------------------------------------------- #
# YAML 読み込み
# --------------------------------------------------------------------------- #
def _read_yaml(path: Path) -> list:
    try:
        import yaml
    except ModuleNotFoundError:
        _warn("PyYAML が未インストールです。pip install PyYAML")
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        _warn(f"{path} を読めません: {exc}")
        return []
    try:
        data = yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001
        _warn(f"{path} のパースに失敗: {exc}")
        return []
    if data is None:
        return []
    if not isinstance(data, list):
        _warn(f"{path} はリスト形式である必要があります")
        return []
    return data


def load_instruments(enrich_meta: bool = True, path: Path | None = None) -> list[dict]:
    """instruments.yaml を読み、有効な銘柄プロファイルのリストを返す。

    壊れたエントリは警告してスキップし、他は継続する。
    """
    warn_deprecated_config()
    raw = _read_yaml(path or _YAML_PATH)
    out: list[dict] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            _warn(f"[{i}] エントリが dict ではありません: スキップ")
            continue
        if not entry.get("symbol"):
            _warn(f"[{i}] symbol が未指定: スキップ")
            continue
        if entry.get("enabled") is False:
            continue
        for k in entry:
            if k not in _ALLOWED_KEYS:
                _warn(f"{entry['symbol']}: 未知のキー {k!r} は無視されます")
        try:
            p = resolve(entry)
        except Exception as exc:  # noqa: BLE001
            _warn(f"{entry['symbol']}: 解決に失敗、スキップ ({exc})")
            continue
        if p["id"] in seen:
            _warn(f"{p['id']}: id が重複しています（後勝ち）")
        seen.add(p["id"])
        if enrich_meta:
            try:
                enrich(p)
            except Exception as exc:  # noqa: BLE001
                _warn(f"{p['id']}: メタ情報の取得に失敗、既定値を使用 ({exc})")
        out.append(p)
    return out


if __name__ == "__main__":
    for prof in load_instruments(enrich_meta=False):
        prof.pop("_explicit", None)
        print(json.dumps(prof, ensure_ascii=False, indent=2))
