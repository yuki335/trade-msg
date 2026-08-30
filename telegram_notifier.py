"""Telegram 送信処理。認証情報は config から読む。"""

from __future__ import annotations

import requests

import config

_API_TIMEOUT = 10


def send_message(text: str) -> None:
    """Telegram に指定したテキストメッセージを送信する。

    失敗時は requests の例外をそのまま呼び出し元へ伝播させる。
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        raise RuntimeError("config.py に TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID が未設定です")

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        data={"chat_id": config.TELEGRAM_CHAT_ID, "text": text},
        timeout=_API_TIMEOUT,
    )
    resp.raise_for_status()
