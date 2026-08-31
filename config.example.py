"""設定・秘密情報の一元管理。

このファイル (config.example.py) をコピーして config.py を作り、実値を記入する。
    cp config.example.py config.py

config.py は .gitignore 済み。絶対にコミットしない。

銘柄ごとの設定（ティッカー・ATR 係数・検索クエリ等）は instruments.yaml 側。
このファイルには API キー・通知・共通の上限値など「グローバル設定」だけを置く。
"""

# --- Telegram ---
TELEGRAM_BOT_TOKEN = ""          # BotFather で取得した Bot Token
TELEGRAM_CHAT_ID = ""            # 送信先の chat.id (ユーザー or グループ)

# --- DeepSeek ---
DEEPSEEK_API_KEY = ""            # DeepSeek API キー (空なら劣化モードで動作)
DEEPSEEK_BASE_URL = "https://api.deepseek.com"   # OpenAI 互換エンドポイント
DEEPSEEK_MODEL = "deepseek-reasoner"             # 使用モデル

# --- 一般設定 ---
TIMEZONE = "Asia/Tokyo"         # メッセージの日付表示 & 取引日判定の基準タイムゾーン

# --- Web コンテキスト (Tavily 検索 API) ---
WEB_CONTEXT_ENABLED = True      # False にすると Web 参照を完全に無効化
TAVILY_API_KEY = ""             # https://tavily.com で取得 (空なら Web 参照はスキップ)
WEB_MAX_RESULTS_PER_QUERY = 4   # 1 クエリあたり Tavily から取る件数
WEB_MAX_ARTICLES = 5           # フィルタ・重複除去後に採用する最大記事数
WEB_ARTICLE_CHARS = 800        # 1 記事の本文抜粋の最大文字数
WEB_TOTAL_CHARS = 4000         # 全記事合計の最大文字数
WEB_FETCH_TIMEOUT = 15         # Tavily HTTP タイムアウト秒
WEB_SUMMARY_MAX_LINES = 5      # Telegram 本文に載せる参照ニュース行数の上限

# --- デバッグ: DeepSeek へ渡した入力のダンプ ---
DEEPSEEK_PROMPT_DUMP = True                    # True で実行のたびに銘柄別ファイルへ書き出す
PROMPT_DUMP_DIR = "logs"                       # logs/deepseek_<id>.txt (相対パスは forecast.py の位置基準)

# --- 現在値のレンジ内位置 (端寄り判定) ---
EDGE_ALERT_RATIO = 0.30        # 中央値からの距離がレンジ全幅の何割以上で「端寄り」と表示するか (0〜0.5)
EDGE_ALERT_ONLY = False        # True にすると端寄り (乖離率 >= EDGE_ALERT_RATIO) でない銘柄は Telegram 送信をスキップ

# --- 実行日の取引判定 ---
TRADING_DAY_CHECK = True       # True にすると実行日(JST)に取引がない市場の銘柄は計算・送信せず終了
                              #   fx: 土日 / 元日、jpx・nyse: 取引所カレンダー、加えて下記
                              #   --force を付けると判定を無視して実行
EXTRA_HOLIDAYS = {            # 追加で休場扱いする日 "YYYY-MM-DD" (市場別)
    "fx": [],
    "jpx": [],
    "nyse": [],
}
