"""設定・秘密情報の一元管理。

このファイル (config.example.py) をコピーして config.py を作り、実値を記入する。
    cp config.example.py config.py

config.py は .gitignore 済み。絶対にコミットしない。
"""

# --- Telegram ---
TELEGRAM_BOT_TOKEN = ""          # BotFather で取得した Bot Token
TELEGRAM_CHAT_ID = ""            # 送信先の chat.id (ユーザー or グループ)

# --- DeepSeek ---
DEEPSEEK_API_KEY = ""            # DeepSeek API キー (空なら劣化モードで動作)
DEEPSEEK_BASE_URL = "https://api.deepseek.com"   # OpenAI 互換エンドポイント
DEEPSEEK_MODEL = "deepseek-reasoner"             # 使用モデル

# --- 一般設定 ---
TIMEZONE = "Asia/Tokyo"         # メッセージの日付表示に使うタイムゾーン
PRICE_LOOKBACK_DAYS = 60        # 価格を取得する暦日数 (テクニカル算出用)
ATR_MULTIPLIER = 1.5            # 劣化モードで現在値からレンジを広げる係数
