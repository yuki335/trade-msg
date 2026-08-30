"""ドル円 1週間見通しを算出し、Telegram に送信するエントリポイント。

使い方:
    python main.py            予測を算出して Telegram へ送信
    python main.py --json     送信せず [予想上値, 予想下値, 現在値] を JSON 出力
    python main.py --detail   送信せず詳細 dict を JSON 出力 (根拠テキスト付き)
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import config
from forecast import get_usdjpy_forecast, get_usdjpy_forecast_detail
from telegram_notifier import send_message


def build_message(values: list, rationale: str = "") -> str:
    week_high, week_low, current = values
    now = datetime.now(ZoneInfo(config.TIMEZONE))
    lines = [
        f"=== ドル円 1週間見通し ({now:%Y-%m-%d}) ===",
        f"現在値  : {current:.2f}",
        f"予想上値: {week_high:.2f}",
        f"予想下値: {week_low:.2f}",
    ]
    if rationale:
        lines.append(f"根拠    : {rationale}")
    return "\n".join(lines)


def main() -> None:
    args = sys.argv[1:]

    if "--detail" in args:
        print(json.dumps(get_usdjpy_forecast_detail(), ensure_ascii=False, indent=2))
        return

    if "--json" in args:
        print(json.dumps(get_usdjpy_forecast()))
        return

    detail = get_usdjpy_forecast_detail()
    values = [detail["week_high"], detail["week_low"], detail["current"]]
    message = build_message(values, detail.get("rationale", ""))

    try:
        send_message(message)
        print(f"Message sent successfully:\n{message}")
    except Exception as e:  # noqa: BLE001
        print(f"Failed to send message: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
