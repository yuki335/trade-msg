"""指定した銘柄（為替 / 日本株 / 米国株）の 1 週間見通しを算出する。

公開 API:
    get_forecast(instrument)        -> [予想上値, 予想下値, 現在値]
    get_forecast_detail(instrument) -> dict  (根拠テキストなどを含む詳細)

``instrument`` は instruments.resolve() が返す dict（symbol / stooq_symbol /
price_decimals / atr_multiplier / lookback_days / analyst_role / event_hint /
asset_label / search_queries / relevance_keywords / auto_adjust ...）。

処理の流れ:
    1. 価格取得   yfinance(symbol) -> 失敗時 stooq(stooq_symbol) にフォールバック
    2. テクニカル 週間高安 / ATR14 / SMA20 / SMA50 / 直近の方向性
    2.5 Web参照  Tavily でニュース抜粋を収集 (web_context, 任意・失敗しても続行)
    3. DeepSeek   テクニカル + ニュース抜粋を1コールで渡し想定レンジ・bias・key_events を JSON で受領
    4. 整合化     current は必ず実測値。week_low < current < week_high を保証。
                  現在値のレンジ内位置 (edge) も算出
劣化モード (DeepSeek のキー未設定 / API 失敗 / 応答不正):
    week_high = current + ATR14 * instrument["atr_multiplier"]
    week_low  = current - ATR14 * instrument["atr_multiplier"]
"""

from __future__ import annotations

import io
import json
import re
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import config

_HTTP_TIMEOUT = 15
_STOOQ_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"


# --------------------------------------------------------------------------- #
# 1. 価格取得
# --------------------------------------------------------------------------- #
def _fetch_prices_yfinance(instrument: dict, lookback_days: int) -> pd.DataFrame:
    import yfinance as yf

    period = f"{max(lookback_days, 30) + 10}d"
    raw = yf.download(
        instrument["symbol"],
        period=period,
        interval="1d",
        auto_adjust=bool(instrument.get("auto_adjust", False)),
        progress=False,
    )
    if raw is None or raw.empty:
        raise RuntimeError("yfinance returned no data")
    # MultiIndex 列 (('Close', 'AAPL') など) を単層へ
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    return _normalize_ohlc(raw)


def _fetch_prices_stooq(instrument: dict, lookback_days: int) -> pd.DataFrame:
    url = _STOOQ_URL.format(symbol=instrument["stooq_symbol"])
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:  # noqa: S310
        text = resp.read().decode("utf-8", errors="replace")
    if "Date" not in text.splitlines()[0]:
        raise RuntimeError(f"unexpected stooq response: {text[:120]!r}")
    df = pd.read_csv(io.StringIO(text), parse_dates=["Date"]).set_index("Date")
    if df.empty:
        raise RuntimeError("stooq returned no rows")
    return _normalize_ohlc(df).tail(max(lookback_days, 30) + 10)


def _normalize_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns={c: str(c).capitalize() for c in df.columns})
    needed = ["Open", "High", "Low", "Close"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise RuntimeError(f"missing OHLC columns: {missing}")
    out = df[needed].apply(pd.to_numeric, errors="coerce").dropna()
    if out.empty:
        raise RuntimeError("no valid OHLC rows after cleaning")
    return out


_FETCHERS = {"yfinance": _fetch_prices_yfinance, "stooq": _fetch_prices_stooq}


def _load_prices(instrument: dict) -> tuple[pd.DataFrame, str]:
    lookback = int(instrument.get("lookback_days", 60))
    order = instrument.get("price_source_order") or ["yfinance", "stooq"]
    errors = []
    for name in order:
        fn = _FETCHERS.get(name)
        if fn is None:
            errors.append(f"{name}: unknown price source")
            continue
        try:
            return fn(instrument, lookback), name
        except Exception as exc:  # noqa: BLE001 - フォールバックのため広めに捕捉
            errors.append(f"{name}: {exc}")
    raise RuntimeError("price fetch failed -> " + " | ".join(errors))


# --------------------------------------------------------------------------- #
# 2. テクニカル指標
# --------------------------------------------------------------------------- #
def _atr(df: pd.DataFrame, window: int = 14) -> float:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(window).mean().iloc[-1]
    if not np.isfinite(atr):
        atr = float(tr.dropna().mean())
    return float(atr)


def compute_features(df: pd.DataFrame, decimals: int = 3) -> dict:
    """テクニカル要約を dict で返す。テスト対象にできるよう純関数にしてある。"""
    close = df["Close"]
    current = float(close.iloc[-1])
    recent = df.tail(5)
    sma20 = float(close.rolling(20).mean().iloc[-1]) if len(close) >= 20 else current
    sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else current
    atr14 = _atr(df, 14)

    if current > sma20 > sma50:
        trend = "uptrend"
    elif current < sma20 < sma50:
        trend = "downtrend"
    else:
        trend = "range"

    r = lambda x: round(float(x), decimals)  # noqa: E731
    return {
        "current": r(current),
        "week_high": r(recent["High"].max()),
        "week_low": r(recent["Low"].min()),
        "atr14": r(atr14),
        "sma20": r(sma20),
        "sma50": r(sma50),
        "trend": trend,
        "last_date": df.index[-1].strftime("%Y-%m-%d"),
        "history_60d_high": r(df["High"].tail(60).max()),
        "history_60d_low": r(df["Low"].tail(60).min()),
    }


# --------------------------------------------------------------------------- #
# 3. DeepSeek 予測
# --------------------------------------------------------------------------- #
def _build_system_prompt(instrument: dict) -> str:
    role = instrument.get("analyst_role", "経験豊富なマーケットアナリスト")
    label = instrument.get("asset_label") or instrument.get("symbol", "")
    events = instrument.get("event_hint", "重要イベント・経済指標")
    return (
        f"あなたは{role}です。与えられたテクニカルデータと、参考として渡される"
        f"ニュース記事の抜粋を踏まえて、{label} の今後1週間 (5営業日) に想定される高値と安値を"
        "推定してください。数値レンジはテクニカル (ATR・トレンド) を主軸にし、ニュースはレンジの"
        f"偏り・警戒方向・イベントリスク ({events}) の反映に使います。"
        "投機的な断定や憶測による極端な値は避けます。"
        '出力は次の JSON のみ: {"week_high": number, "week_low": number, '
        '"rationale": "日本語の短い根拠", "bias": "up"|"down"|"neutral", "key_events": ["..."]}'
    )


def _extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object in model output: {text[:200]!r}")
    return json.loads(match.group(0))


def _build_messages(instrument: dict, features: dict, web: dict | None) -> list[dict]:
    """DeepSeek へ送る messages を組み立てる。web に記事があれば「参考ニュース」節を足す。"""
    label = instrument.get("asset_label") or instrument.get("symbol", "")
    user_prompt = f"テクニカルデータ ({label}, 日足):\n" + json.dumps(
        features, ensure_ascii=False, indent=2
    )
    articles = (web or {}).get("articles") or []
    if articles:
        lines = []
        for a in articles:
            date = f"[{a['published']}] " if a.get("published") else ""
            snippet = (a.get("snippet") or "").strip()
            lines.append(f"- {date}{a.get('title', '')} … {snippet}")
        user_prompt += "\n\n参考ニュース（新しい順・抜粋）:\n" + "\n".join(lines)
    user_prompt += "\n\n今後1週間の想定高値・安値を JSON で返してください。"
    return [
        {"role": "system", "content": _build_system_prompt(instrument)},
        {"role": "user", "content": user_prompt},
    ]


def _dump_path(instrument: dict) -> Path | None:
    raw = getattr(config, "PROMPT_DUMP_DIR", "")
    if not raw:
        return None
    d = Path(raw)
    if not d.is_absolute():
        d = Path(__file__).resolve().parent / d
    return d / f"deepseek_{instrument['id']}.txt"


def _dump_deepseek_input(
    instrument: dict,
    messages: list[dict],
    meta: dict,
    response_text: str | None = None,
    parsed: dict | None = None,
) -> None:
    """DeepSeek に渡した messages（記事を埋め込んだ完成形）を毎回上書きで書き出す。

    config.DEEPSEEK_PROMPT_DUMP が真のときだけ動く。例外は握りつぶす。
    """
    if not getattr(config, "DEEPSEEK_PROMPT_DUMP", False):
        return
    try:
        path = _dump_path(instrument)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        web = meta.get("web") or {}
        arts = web.get("articles") or []
        out = [
            f"=== DeepSeek 入力ダンプ  {datetime.now(ZoneInfo(config.TIMEZONE)).isoformat()} ===",
            f"instrument       : {instrument['id']}  ({instrument.get('asset_label', '')})",
            f"model            : {meta.get('model', '')}",
            f"response_format  : {meta.get('response_format', '')}",
            f"web_used         : {bool(web.get('used'))}",
            f"articles         : {len(arts)}",
        ]
        for a in arts:
            out.append(
                f"    - {a.get('published') or '----------'}  "
                f"{a.get('title', '')}  {a.get('url', '')}"
            )
        out.append("")
        for i, m in enumerate(messages):
            out.append(f"----- messages[{i}] role={m.get('role')} -----")
            out.append(m.get("content", ""))
            out.append("")
        if response_text is not None:
            out += ["----- raw response -----", response_text, ""]
        if parsed is not None:
            out += ["----- parsed -----", json.dumps(parsed, ensure_ascii=False, indent=2), ""]
        path.write_text("\n".join(out), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _dump_degraded(instrument: dict, web: dict | None, reason: str) -> None:
    """劣化モード（DeepSeek 未呼び出し）でも、同じパスに状況を書き残す。"""
    if not getattr(config, "DEEPSEEK_PROMPT_DUMP", False):
        return
    try:
        path = _dump_path(instrument)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        arts = (web or {}).get("articles") or []
        out = [
            f"=== DeepSeek 入力ダンプ  {datetime.now(ZoneInfo(config.TIMEZONE)).isoformat()} ===",
            f"instrument       : {instrument['id']}  ({instrument.get('asset_label', '')})",
            "degraded: DeepSeek は呼ばれていません",
            f"理由             : {reason}",
            f"articles         : {len(arts)}",
        ]
        for a in arts:
            out.append(
                f"    - {a.get('published') or '----------'}  "
                f"{a.get('title', '')}  {a.get('url', '')}"
            )
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _deepseek_forecast(instrument: dict, features: dict, web: dict | None = None) -> dict:
    from openai import OpenAI

    client = OpenAI(
        api_key=config.DEEPSEEK_API_KEY,
        base_url=config.DEEPSEEK_BASE_URL,
        timeout=60,
    )
    messages = _build_messages(instrument, features, web)
    meta = {
        "model": config.DEEPSEEK_MODEL,
        "response_format": "json_object",
        "web": web or {"used": False, "articles": []},
    }
    # 入力のみ先に書き出す（API 失敗時もこの分は残る）
    _dump_deepseek_input(instrument, messages, meta)

    # まず JSON モードで試し、モデルが未対応ならプレーンで再試行
    try:
        resp = client.chat.completions.create(
            model=config.DEEPSEEK_MODEL,
            messages=messages,
            response_format={"type": "json_object"},
        )
    except Exception:  # noqa: BLE001
        meta["response_format"] = "text"
        resp = client.chat.completions.create(
            model=config.DEEPSEEK_MODEL,
            messages=messages,
        )

    raw = resp.choices[0].message.content or ""
    data = _extract_json(raw)

    # 応答・パース結果を含めて書き直す
    _dump_deepseek_input(instrument, messages, meta, response_text=raw, parsed=data)

    return {
        "week_high": float(data["week_high"]),
        "week_low": float(data["week_low"]),
        "rationale": str(data.get("rationale", "")).strip(),
        "bias": str(data.get("bias", "") or "").strip(),
        "key_events": [str(e) for e in (data.get("key_events") or [])],
        "source": "deepseek",
    }


# --------------------------------------------------------------------------- #
# 4. 整合化 + 公開 API
# --------------------------------------------------------------------------- #
def _degraded_forecast(instrument: dict, features: dict, reason: str) -> dict:
    current = features["current"]
    span = features["atr14"] * float(instrument.get("atr_multiplier", 1.5))
    return {
        "week_high": current + span,
        "week_low": current - span,
        "rationale": f"degraded mode: テクニカルのみ ({reason})",
        "source": "degraded",
    }


def _reconcile(current: float, week_high: float, week_low: float, decimals: int = 3) -> tuple[float, float]:
    """現在値がレンジ外に出ないよう最小限の補正をする。"""
    hi, lo = max(week_high, week_low), min(week_high, week_low)
    hi = max(hi, current)
    lo = min(lo, current)
    return round(hi, decimals), round(lo, decimals)


def _edge_position(current: float, week_high: float, week_low: float) -> dict:
    """現在値が「予想上値・下値の中央値」からレンジ全幅の何割離れているかを判定する。

    ratio は 0（中央）〜0.5（どちらかの端）。config.EDGE_ALERT_RATIO 以上なら alert=True。
    """
    mid = (week_high + week_low) / 2
    width = week_high - week_low
    ratio = abs(current - mid) / width if width > 0 else 0.0
    ratio = round(ratio, 3)
    if ratio == 0.0:
        side = "中央"
    elif current > mid:
        side = "上寄り"
    else:
        side = "下寄り"
    threshold = getattr(config, "EDGE_ALERT_RATIO", 0.30)
    return {
        "ratio": ratio,
        "side": side,
        "alert": ratio >= threshold,
        "threshold": threshold,
    }


def _collect_web_context(instrument: dict) -> dict:
    """Web コンテキストを取得する。無効・失敗時は {"used": False, "articles": []}。"""
    empty = {"used": False, "articles": []}
    if not (getattr(config, "WEB_CONTEXT_ENABLED", False) and getattr(config, "TAVILY_API_KEY", "")):
        return empty
    try:
        import web_context

        web = web_context.get_web_context(instrument)
        if not isinstance(web, dict):
            return empty
        web.setdefault("articles", [])
        web["used"] = bool(web.get("articles"))
        return web
    except Exception:  # noqa: BLE001
        return empty


def get_forecast_detail(instrument: dict) -> dict:
    decimals = int(instrument.get("price_decimals", 3))
    df, price_source = _load_prices(instrument)
    features = compute_features(df, decimals)
    current = features["current"]

    web = _collect_web_context(instrument)

    if config.DEEPSEEK_API_KEY:
        try:
            fc = _deepseek_forecast(instrument, features, web)
        except Exception as exc:  # noqa: BLE001
            reason = f"DeepSeek 失敗: {exc}"
            fc = _degraded_forecast(instrument, features, reason)
            _dump_degraded(instrument, web, reason)
    else:
        reason = "DEEPSEEK_API_KEY 未設定"
        fc = _degraded_forecast(instrument, features, reason)
        _dump_degraded(instrument, web, reason)

    week_high, week_low = _reconcile(current, fc["week_high"], fc["week_low"], decimals)
    edge = _edge_position(current, week_high, week_low)

    articles = web.get("articles") or []
    web_articles = [
        {"title": a.get("title", ""), "published": a.get("published"), "url": a.get("url", "")}
        for a in articles
    ]

    return {
        "instrument_id": instrument["id"],
        "display_name": instrument.get("display_name", instrument["id"]),
        "asset_class": instrument.get("asset_class", ""),
        "market": instrument.get("market", ""),
        "price_decimals": decimals,
        "unit": instrument.get("unit", ""),
        "week_high": week_high,
        "week_low": week_low,
        "current": round(current, decimals),
        "rationale": fc["rationale"],
        "source": fc["source"],
        "price_source": price_source,
        "features": features,
        "web_used": bool(articles),
        "web_sources": [a.get("url", "") for a in articles],
        "web_articles": web_articles,
        "bias": fc.get("bias", ""),
        "key_events": fc.get("key_events", []),
        "edge": edge,
    }


def get_forecast(instrument: dict) -> list:
    """[予想上値, 予想下値, 現在値] を返す。"""
    d = get_forecast_detail(instrument)
    return [d["week_high"], d["week_low"], d["current"]]


if __name__ == "__main__":
    import instruments

    for prof in instruments.load_instruments():
        print(json.dumps(get_forecast_detail(prof), ensure_ascii=False, indent=2))
