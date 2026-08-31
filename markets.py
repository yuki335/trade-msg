"""市場ごとの「当日の取引有無」を判定する。

is_trading_day(market, now=None) -> (bool, reason)
    market: "fx" / "jpx" / "nyse"

基準日は **常に config.TIMEZONE（JST）の暦日**。
朝の JST 実行で「今夜開く NYSE セッション（ET 日付 = JST の今日）」まで含めて
判定できるよう、market ローカル時刻には変換しない。

- fx : 月〜金のみ（土日クローズ）＋ 元日 ＋ EXTRA_HOLIDAYS["fx"]
- jpx / nyse : exchange_calendars（XTKS / XNYS）で祝日・半日・年末年始を判定
               ＋ EXTRA_HOLIDAYS[market]
ライブラリ未導入・範囲外なら平日判定へフォールバック（＝停止させない・警告のみ）。
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

import config

_XCAL_CODE = {"jpx": "XTKS", "nyse": "XNYS"}
_cal_cache: dict[str, object] = {}

MARKETS = ("fx", "jpx", "nyse")


def _warn(msg: str) -> None:
    print(f"[warn] markets: {msg}", file=sys.stderr)


def _extra_holidays(market: str) -> set[str]:
    raw = getattr(config, "EXTRA_HOLIDAYS", {}) or {}
    days = set(raw.get(market, []) or [])
    # 旧 config.FX_MARKET_HOLIDAYS（単一リスト）を fx へ自動移送
    if market == "fx":
        days |= set(getattr(config, "FX_MARKET_HOLIDAYS", []) or [])
    return days


def _calendar(code: str):
    cal = _cal_cache.get(code)
    if cal is None:
        import exchange_calendars as xcals

        cal = xcals.get_calendar(code)
        _cal_cache[code] = cal
    return cal


def is_trading_day(market: str, now: datetime | None = None) -> tuple[bool, str]:
    now = now or datetime.now(ZoneInfo(config.TIMEZONE))
    ref: date = now.date()
    iso = ref.isoformat()

    if iso in _extra_holidays(market):
        return False, f"手動休場指定（{iso}）"

    if market == "fx":
        wd = ref.weekday()
        if wd == 5:
            return False, "土曜（FX 市場クローズ）"
        if wd == 6:
            return False, "日曜（FX 市場クローズ）"
        if (ref.month, ref.day) == (1, 1):
            return False, "元日"
        return True, ""

    code = _XCAL_CODE.get(market)
    if code is None:
        wd = ref.weekday()
        return (wd < 5), ("" if wd < 5 else "週末（未知の市場）")

    try:
        import pandas as pd

        cal = _calendar(code)
        if cal.is_session(pd.Timestamp(ref)):
            return True, ""
        return False, f"{market} 休場（{iso}）"
    except Exception as exc:  # noqa: BLE001
        wd = ref.weekday()
        if wd >= 5:
            return False, f"週末（{market}・暦フォールバック）"
        _warn(f"{market}: 取引所カレンダー参照に失敗、平日として扱う ({exc})")
        return True, ""


if __name__ == "__main__":
    for m in MARKETS:
        print(m, is_trading_day(m))
