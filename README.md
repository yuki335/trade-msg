# market-forecast

為替・日本株・米国株について **1 週間の予想上値・予想下値・現在値** を算出し、Telegram に定期送信する。
対象銘柄は `instruments.yaml` に列挙する（1 銘柄 = 数行）。

- 価格: yfinance → 失敗時 stooq にフォールバック（株は分割・配当調整あり）
- 予測: テクニカル要約（週間高安 / ATR14 / SMA）＋ Web ニュース抜粋を DeepSeek に**1コール**で渡して想定レンジを推定
- Web ニュース: Tavily 検索 API。クエリと関連度キーワードは銘柄ごとに自動生成（`instruments.yaml` で上書き可）。`TAVILY_API_KEY` 未設定なら自動スキップ
- 取引日判定: 銘柄の市場ごとに実行日(JST)の取引有無を判定し、開いている市場の銘柄だけ送信
  - `fx` … 土日・元日クローズ
  - `jpx` / `nyse` … `exchange_calendars`（XTKS / XNYS）で祝日・半日・年末年始を判定
- `DEEPSEEK_API_KEY` 未設定・API 失敗時は「現在値 ± ATR14 × 係数」で算出する**劣化モード**
- 秘密情報・グローバル設定は `config.py`（`.gitignore` 済み・コミットしない）。銘柄設定は `instruments.yaml`（コミットする）
- 1 銘柄の失敗で全体は止めない

> 投資助言ではありません。テクニカル指標と LLM 推論による参考値です。

## ファイル

| ファイル | 役割 |
|---|---|
| `main.py` | エントリ。全銘柄をループし、市場が開いていれば予測して Telegram 送信 |
| `instruments.yaml` | 予測対象の銘柄リスト（コミットする） |
| `instruments.py` | `instruments.yaml` を読み銘柄プロファイルに解決（クラス既定値 + yfinance 自動補完） |
| `forecast.py` | `get_forecast(instrument) -> [予想上値, 予想下値, 現在値]` / `get_forecast_detail(instrument)` |
| `markets.py` | `is_trading_day(market)` — fx / jpx / nyse の当日取引判定 |
| `web_context.py` | `get_web_context(instrument)` — Tavily でニュース抜粋を収集（LLM 不使用・例外を投げない） |
| `telegram_notifier.py` | `send_message(text)` |
| `config.py` | API キー・通知・共通上限などグローバル設定（**コミットしない**、`.gitignore` 済み） |
| `config.example.py` | `config.py` のテンプレート |

## instruments.yaml

必須は `symbol` のみ（`asset_class` は推定されるが明示推奨）。省略項目は `asset_class`
ごとの既定値（`instruments.py: CLASS_DEFAULTS`）→ 実行時に yfinance の会社名などで補完される。

```yaml
- symbol: JPY=X          # 為替: {PAIR}=X（USD 始まりは省略可。JPY=X = USD/JPY）
  asset_class: fx
  display_name: ドル円

- symbol: 7203.T         # 日本株: {4桁コード}.T
  asset_class: jp_stock
  display_name: トヨタ自動車

- symbol: AAPL           # 米国株: ティッカーそのまま
  asset_class: us_stock
  atr_multiplier: 2.0    # 既定を変えたい項目だけ書く
```

指定できる主なキー: `symbol` / `asset_class`（`fx` `jp_stock` `us_stock`）/ `display_name` /
`atr_multiplier` / `lookback_days` / `price_decimals` / `search_queries` /
`relevance_keywords` / `enabled: false`（一時無効化）。

**`symbol` の確認**: finance.yahoo.com（日本株は finance.yahoo.co.jp）で検索し、
表示されるティッカーをそのまま使う。`venv/bin/python -c "import yfinance as yf; print(yf.Ticker('7203.T').fast_info['last_price'])"` で疎通確認。

## Telegram 本文の例

```
=== ドル円 1週間見通し (2026-08-30) ===
現在値  : 149.40
予想上値: 150.00
予想下値: 145.00
根拠    : ...
位置    : 現在値はレンジ端寄り（上寄り 35% / 閾値 30%）
---
bias : up  / 注目 : 9/5 米雇用統計, 日銀会合
--- 参照ニュース (2件) ---
・[08-29] 日銀が金融政策の修正を検討との報道 — https://example.com/a
・Fed高官がタカ派発言 — https://example.com/b
```

株の場合はタイトルが `=== トヨタ自動車 1週間見通し ===`、数値は `2714 円` のように
`price_decimals` と `unit` に従う。「位置」行は `edge` が閾値以上のときだけ。
「bias / 注目」行と「参照ニュース」節は劣化モードでは出さない。

`config.EDGE_ALERT_ONLY = True` にすると、乖離率が `EDGE_ALERT_RATIO`（既定 30%）を
超えない（＝端寄りでない）銘柄は Telegram 送信をスキップする（`skipped` に「端寄りでない」で記録）。

## ローカルでの実行

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp config.example.py config.py
# config.py を編集: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / DEEPSEEK_API_KEY / TAVILY_API_KEY

python main.py --check                # 送信せず、解決後プロファイルと市場の取引判定を表示
python main.py --detail               # 送信せず {id: 詳細dict} を JSON 出力
python main.py --json                 # 送信せず {id: [上値, 下値, 現在値]} を JSON 出力
python main.py                        # 全銘柄を Telegram へ送信（非取引日の市場はスキップ）
python main.py --force                # 取引日判定を無視
python main.py --market jpx           # 東証銘柄だけ
python main.py --only 7203,AAPL       # id / symbol 指定（カンマ区切り）
```

関数として使う場合:

```python
import instruments
from forecast import get_forecast

for prof in instruments.load_instruments():
    week_high, week_low, current = get_forecast(prof)
```

## テスト

ネット / API 不要:

```bash
python -m pytest -q
```

## サーバーへのデプロイ (systemd timer, 毎日 06:00 JST, 自動 git pull)

```bash
# 1. クローン（ubuntu ユーザー所有で配置）
sudo git clone <repo-url> /opt/market-forecast
sudo chown -R ubuntu:ubuntu /opt/market-forecast
cd /opt/market-forecast

# 2. config.py を作成（instruments.yaml はリポジトリに含まれる）
cp config.example.py config.py
vi config.py

# 3. 初回デプロイ（venv 構築 + 依存インストール）
chmod +x deploy.sh && ./deploy.sh

# 4. 動作確認
venv/bin/python main.py --check

# 5. ubuntu ユーザーで git pull できることを確認（自分の SSH 鍵 か HTTPS トークン）
git -C /opt/market-forecast pull --ff-only
#   所有者不一致で怒られたら: git config --global --add safe.directory /opt/market-forecast

# 6. systemd 登録
sudo cp systemd/market-forecast.service systemd/market-forecast.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now market-forecast.timer

# 7. 確認
systemctl list-timers market-forecast.timer
sudo systemctl start market-forecast.service   # 即時テスト実行
journalctl -u market-forecast.service -n 40
```

`market-forecast.service` の `ExecStartPre` が実行のたびに `git pull --ff-only` +
`pip install` を行う（先頭 `-` 付きなので失敗しても直前の正常版で forecast は継続）。

## 銘柄の追加・変更フロー

1. スマホ / PC の GitHub で `instruments.yaml` を編集して commit
2. 翌 06:00 JST、timer が `git pull` → `main.py` を実行して反映
3. すぐ反映したいときは `sudo systemctl start market-forecast.service`

サーバー上で `instruments.yaml` を直接編集しないこと（`git pull` / `git reset` で消える）。

## 実行時に必要な外向き通信

| ホスト | 用途 |
|---|---|
| `api.telegram.org:443` | Telegram Bot API |
| `api.deepseek.com:443` | DeepSeek API |
| `api.tavily.com:443` | Tavily 検索 API（`TAVILY_API_KEY` 未設定なら不要） |
| `query1.finance.yahoo.com:443` / `query2.finance.yahoo.com:443` | yfinance（価格・会社名メタ） |
| `stooq.com:443` | フォールバック価格 |
| GitHub（`github.com:443` / `:22`） | systemd の自動 `git pull` |
