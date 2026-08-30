"""ドル円 (USD/JPY) の 1 週間見通しを算出する。

公開 API:
    get_usdjpy_forecast()        -> [予想上値, 予想下値, 現在値]
    get_usdjpy_forecast_detail() -> dict  (根拠テキストなどを含む詳細)

処理の流れ:
    1. 価格取得   yfinance("JPY=X") -> 失敗時 stooq("usdjpy") にフォールバック
    2. テクニカル 週間高安 / ATR14 / SMA20 / SMA50 / 直近の方向性
    3. DeepSeek   データを渡して来週の想定レンジ (week_high / week_low) を JSON で受領
    4. 整合化     current は必ず実測値。week_low < current < week_high を保証
劣化モード (DeepSeek のキー未設定 / API 失敗 / 応答不正):
    week_high = current + ATR14 * ATR_MULTIPLIER
    week_low  = current - ATR14 * ATR_MULTIPLIER
"""

from __future__ import annotations

import io
import json
import re
import urllib.request

import numpy as np
import pandas as pd

import config

TICKER_YF = "JPY=X"
STOOQ_URL = "https://stooq.com/q/d/l/?s=usdjpy&i=d"
_HTTP_TIMEOUT = 15


# --------------------------------------------------------------------------- #
# 1. 価格取得
# --------------------------------------------------------------------------- #
def _fetch_prices_yfinance(lookback_days: int) -> pd.DataFrame:
    import yfinance as yf

    period = f"{max(lookback_days, 30) + 10}d"
    raw = yf.download(
        TICKER_YF,
        period=period,
        interval="1d",
        auto_adjust=False,
        progress=False,
    )
    if raw is None or raw.empty:
        raise RuntimeError("yfinance returned no data")
    # MultiIndex 列 (('Close', 'JPY=X') など) を単層へ
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    return _normalize_ohlc(raw)


def _fetch_prices_stooq(lookback_days: int) -> pd.DataFrame:
    req = urllib.request.Request(STOOQ_URL, headers={"User-Agent": "Mozilla/5.0"})
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


def _load_prices(lookback_days: int) -> tuple[pd.DataFrame, str]:
    errors = []
    for name, fn in (("yfinance", _fetch_prices_yfinance), ("stooq", _fetch_prices_stooq)):
        try:
            return fn(lookback_days), name
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


def compute_features(df: pd.DataFrame) -> dict:
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

    return {
        "current": round(current, 3),
        "week_high": round(float(recent["High"].max()), 3),
        "week_low": round(float(recent["Low"].min()), 3),
        "atr14": round(atr14, 3),
        "sma20": round(sma20, 3),
        "sma50": round(sma50, 3),
        "trend": trend,
        "last_date": df.index[-1].strftime("%Y-%m-%d"),
        "history_60d_high": round(float(df["High"].tail(60).max()), 3),
        "history_60d_low": round(float(df["Low"].tail(60).min()), 3),
    }


# --------------------------------------------------------------------------- #
# 3. DeepSeek 予測
# --------------------------------------------------------------------------- #
_SYSTEM_PROMPT = (
    "あなたは経験豊富な為替アナリストです。与えられたテクニカルデータのみを根拠に、"
    "USD/JPY の今後1週間 (5営業日) に想定される高値と安値を推定してください。"
    "投機的な断定は避け、直近のボラティリティ (ATR) とトレンドを踏まえた現実的なレンジにします。"
    '出力は次の JSON のみ: {"week_high": number, "week_low": number, "rationale": "日本語の短い根拠"}'
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


def _deepseek_forecast(features: dict) -> dict:
    from openai import OpenAI

    client = OpenAI(
        api_key=config.DEEPSEEK_API_KEY,
        base_url=config.DEEPSEEK_BASE_URL,
        timeout=60,
    )
    user_prompt = (
        "テクニカルデータ (USD/JPY, 日足):\n"
        + json.dumps(features, ensure_ascii=False, indent=2)
        + "\n\n今後1週間の想定高値・安値を JSON で返してください。"
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    # まず JSON モードで試し、モデルが未対応ならプレーンで再試行
    try:
        resp = client.chat.completions.create(
            model=config.DEEPSEEK_MODEL,
            messages=messages,
            response_format={"type": "json_object"},
        )
    except Exception:  # noqa: BLE001
        resp = client.chat.completions.create(
            model=config.DEEPSEEK_MODEL,
            messages=messages,
        )

    data = _extract_json(resp.choices[0].message.content or "")
    return {
        "week_high": float(data["week_high"]),
        "week_low": float(data["week_low"]),
        "rationale": str(data.get("rationale", "")).strip(),
        "source": "deepseek",
    }


# --------------------------------------------------------------------------- #
# 4. 整合化 + 公開 API
# --------------------------------------------------------------------------- #
def _degraded_forecast(features: dict, reason: str) -> dict:
    current = features["current"]
    span = features["atr14"] * config.ATR_MULTIPLIER
    return {
        "week_high": current + span,
        "week_low": current - span,
        "rationale": f"degraded mode: テクニカルのみ ({reason})",
        "source": "degraded",
    }


def _reconcile(current: float, week_high: float, week_low: float) -> tuple[float, float]:
    """現在値がレンジ外に出ないよう最小限の補正をする。"""
    hi, lo = max(week_high, week_low), min(week_high, week_low)
    hi = max(hi, current)
    lo = min(lo, current)
    return round(hi, 3), round(lo, 3)


def get_usdjpy_forecast_detail() -> dict:
    df, price_source = _load_prices(config.PRICE_LOOKBACK_DAYS)
    features = compute_features(df)
    current = features["current"]

    if config.DEEPSEEK_API_KEY:
        try:
            fc = _deepseek_forecast(features)
        except Exception as exc:  # noqa: BLE001
            fc = _degraded_forecast(features, f"DeepSeek 失敗: {exc}")
    else:
        fc = _degraded_forecast(features, "DEEPSEEK_API_KEY 未設定")

    week_high, week_low = _reconcile(current, fc["week_high"], fc["week_low"])

    return {
        "week_high": week_high,
        "week_low": week_low,
        "current": round(current, 3),
        "rationale": fc["rationale"],
        "source": fc["source"],
        "price_source": price_source,
        "features": features,
    }


def get_usdjpy_forecast() -> list:
    """[予想上値, 予想下値, 現在値] を返す。"""
    d = get_usdjpy_forecast_detail()
    return [d["week_high"], d["week_low"], d["current"]]


if __name__ == "__main__":
    print(json.dumps(get_usdjpy_forecast_detail(), ensure_ascii=False, indent=2))
